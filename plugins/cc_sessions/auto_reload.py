"""Auto-reload watcher using macOS kqueue for session JSONL files."""

from __future__ import annotations

import json
import logging
import os
import select
import threading
from typing import Callable, List

logger = logging.getLogger(__name__)

_WATCH_FLAGS = (
    select.KQ_NOTE_WRITE
    | select.KQ_NOTE_EXTEND
    | select.KQ_NOTE_DELETE
    | select.KQ_NOTE_RENAME
)


class AutoReloadWatcher:
    """Watch a single JSONL file and push incremental lines via callback.

    Uses macOS kqueue for near-instant change detection.  Handles partial
    lines (buffered until a full newline arrives) and file deletion/rename
    (re-opens the fd automatically).

    Parameters
    ----------
    filepath : str
        Absolute path to the JSONL session file.
    on_new_lines : callback(lines: List[dict])
        Called with a list of parsed JSON objects whenever new complete
        lines are appended to the file.
    """

    def __init__(
        self,
        filepath: str,
        on_new_lines: Callable[[List[dict]], None],
    ) -> None:
        self._filepath = filepath
        self._on_new_lines = on_new_lines

        self._offset: int = 0
        self._buffer: str = ""
        self._fd: int | None = None
        self._kq: select.kqueue | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ── public API ──────────────────────────────────────────────

    def start(self, *, skip_existing: bool = True) -> None:
        """Start watching in a background daemon thread.

        If *skip_existing* is True (default), the current file content is
        skipped so only **new** appends trigger the callback.
        """
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._ready_event.clear()

        # Seek to end so we only report new content
        if skip_existing:
            try:
                self._offset = os.path.getsize(self._filepath)
            except OSError:
                self._offset = 0

        self._thread = threading.Thread(
            target=self._run, name="auto-reload-watcher", daemon=True,
        )
        self._thread.start()
        self._ready_event.wait(timeout=5)
        logger.info("auto-reload: watching %s", self._filepath)

    def request_stop(self) -> None:
        """Signal the watcher to stop without waiting (non-blocking).

        The daemon thread will exit on its own within ~1s and clean up
        its own fd/kqueue in its finally block.
        """
        if self._stop_event.is_set():
            return
        logger.info("auto-reload: unwatching %s", self._filepath)
        self._stop_event.set()

    def stop(self) -> None:
        """Signal the watcher thread to stop and wait for it."""
        self.request_stop()
        t = self._thread
        if t is not None:
            t.join(timeout=3)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── kqueue loop ─────────────────────────────────────────────

    def _run(self) -> None:
        """Background thread entry point."""
        try:
            self._kq = select.kqueue()
            if not self._open_fd():
                logger.warning("auto-reload: cannot open %s", self._filepath)
                return
            self._register_events()
            self._ready_event.set()
            self._loop()
        except Exception:
            logger.error("auto-reload: watcher crashed", exc_info=True)
        finally:
            self._ready_event.set()  # unblock start() on failure
            self._close_fd()
            if self._kq is not None:
                self._kq.close()
                self._kq = None

    def _loop(self) -> None:
        assert self._kq is not None
        while not self._stop_event.is_set():
            try:
                events = self._kq.control(None, 4, 1.0)  # 1s timeout
            except OSError:
                if self._stop_event.is_set():
                    break
                raise

            for ev in events:
                fflags = ev.fflags
                if fflags & (select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME):
                    self._handle_reopen()
                if fflags & (select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND):
                    self._read_increment()

    # ── fd management ───────────────────────────────────────────

    def _open_fd(self) -> bool:
        self._close_fd()
        try:
            self._fd = os.open(self._filepath, os.O_RDONLY)
            return True
        except OSError:
            self._fd = None
            return False

    def _close_fd(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def _register_events(self) -> None:
        assert self._kq is not None and self._fd is not None
        ev = select.kevent(
            self._fd,
            filter=select.KQ_FILTER_VNODE,
            flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
            fflags=_WATCH_FLAGS,
        )
        self._kq.control([ev], 0)

    def _handle_reopen(self) -> None:
        """Re-open the file after delete/rename (e.g. atomic rewrite)."""
        self._close_fd()
        # Brief retry — the new file may not exist yet
        for _ in range(10):
            if self._stop_event.wait(0.1):
                return
            if self._open_fd():
                self._offset = 0
                self._buffer = ""
                self._register_events()
                self._read_increment()
                return
        logger.warning("auto-reload: file disappeared: %s", self._filepath)

    # ── incremental read ────────────────────────────────────────

    def _read_increment(self) -> None:
        if self._fd is None:
            return
        try:
            size = os.fstat(self._fd).st_size
        except OSError:
            return
        if size <= self._offset:
            return

        try:
            raw = os.pread(self._fd, size - self._offset, self._offset)
        except OSError:
            return
        self._offset = self._offset + len(raw)

        text = self._buffer + raw.decode("utf-8", errors="replace")

        parts = text.split("\n")
        # Last element is incomplete if file didn't end with newline
        if not text.endswith("\n"):
            self._buffer = parts[-1]
            parts = parts[:-1]
        else:
            self._buffer = ""

        lines: List[dict] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            try:
                lines.append(json.loads(part))
            except json.JSONDecodeError:
                logger.debug("auto-reload: skipping malformed line: %s",
                             part[:80])

        if lines:
            try:
                self._on_new_lines(lines)
            except Exception:
                logger.error("auto-reload: callback error", exc_info=True)
