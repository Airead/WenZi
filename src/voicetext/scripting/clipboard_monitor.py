"""Clipboard change monitor.

Polls NSPasteboard.changeCount() in a background thread and records
text entries, excluding concealed/transient clipboard content from
password managers and VoiceText itself.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# Pasteboard types that indicate concealed/transient content
_CONCEALED_TYPE = "org.nspasteboard.ConcealedType"
_TRANSIENT_TYPE = "com.nspasteboard.TransientType"


@dataclass
class ClipboardEntry:
    """A single clipboard history entry."""

    text: str
    timestamp: float = field(default_factory=time.time)
    source_app: str = ""


class ClipboardMonitor:
    """Background monitor that records clipboard text changes.

    Polls NSPasteboard.changeCount() at a configurable interval and
    stores entries in memory, with optional JSON persistence.
    """

    def __init__(
        self,
        max_items: int = 50,
        poll_interval: float = 0.5,
        persist_path: Optional[str] = None,
    ) -> None:
        self._max_items = max_items
        self._poll_interval = poll_interval
        self._persist_path = persist_path
        self._entries: List[ClipboardEntry] = []
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_change_count: int = -1

        if persist_path:
            self._load_from_disk()

    @property
    def entries(self) -> List[ClipboardEntry]:
        """Return a copy of the history (newest first)."""
        with self._lock:
            return list(self._entries)

    def start(self) -> None:
        """Start the background polling thread."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()

        # Capture current changeCount so we don't record existing content
        try:
            from AppKit import NSPasteboard

            pb = NSPasteboard.generalPasteboard()
            self._last_change_count = pb.changeCount()
        except Exception:
            self._last_change_count = -1

        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Clipboard monitor started (interval=%.1fs)", self._poll_interval)

    def stop(self) -> None:
        """Stop the background polling thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("Clipboard monitor stopped")

    def clear(self) -> None:
        """Clear all history entries."""
        with self._lock:
            self._entries.clear()
        self._save_to_disk()

    def _poll_loop(self) -> None:
        """Main polling loop running in a background thread."""
        while not self._stop_event.is_set():
            try:
                self._check_clipboard()
            except Exception:
                logger.debug("Clipboard poll error", exc_info=True)
            self._stop_event.wait(self._poll_interval)

    def _check_clipboard(self) -> None:
        """Check if the clipboard has changed and record new text content."""
        from AppKit import NSPasteboard, NSPasteboardTypeString

        pb = NSPasteboard.generalPasteboard()
        current_count = pb.changeCount()

        if current_count == self._last_change_count:
            return

        self._last_change_count = current_count

        # Skip concealed/transient content (password managers, VoiceText paste)
        if self._is_concealed(pb):
            logger.debug("Skipping concealed/transient clipboard entry")
            return

        text = pb.stringForType_(NSPasteboardTypeString)
        if not text or not str(text).strip():
            return

        text_str = str(text).strip()

        # Get source app name (best-effort)
        source_app = self._get_frontmost_app()

        self._add_entry(text_str, source_app)

    @staticmethod
    def _is_concealed(pb) -> bool:
        """Check if the pasteboard contains concealed/transient markers."""
        types = pb.types()
        if types is None:
            return False
        type_list = list(types)
        return _CONCEALED_TYPE in type_list or _TRANSIENT_TYPE in type_list

    @staticmethod
    def _get_frontmost_app() -> str:
        """Return the name of the frontmost application."""
        try:
            from AppKit import NSWorkspace

            workspace = NSWorkspace.sharedWorkspace()
            app = workspace.frontmostApplication()
            if app and app.localizedName():
                return str(app.localizedName())
        except Exception:
            pass
        return ""

    def _add_entry(self, text: str, source_app: str = "") -> None:
        """Add a new entry, deduplicating consecutive identical texts."""
        with self._lock:
            # Skip if same as the most recent entry
            if self._entries and self._entries[0].text == text:
                return

            entry = ClipboardEntry(
                text=text,
                timestamp=time.time(),
                source_app=source_app,
            )
            self._entries.insert(0, entry)

            # Trim to max size
            if len(self._entries) > self._max_items:
                self._entries = self._entries[: self._max_items]

        self._save_to_disk()
        logger.debug("Clipboard entry added: %s...", text[:40])

    def _save_to_disk(self) -> None:
        """Persist entries to JSON file."""
        if not self._persist_path:
            return
        try:
            path = os.path.expanduser(self._persist_path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with self._lock:
                data = [asdict(e) for e in self._entries]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.debug("Failed to save clipboard history", exc_info=True)

    def _load_from_disk(self) -> None:
        """Load entries from JSON file."""
        if not self._persist_path:
            return
        path = os.path.expanduser(self._persist_path)
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self._entries = [
                    ClipboardEntry(
                        text=d["text"],
                        timestamp=d.get("timestamp", 0),
                        source_app=d.get("source_app", ""),
                    )
                    for d in data
                    if isinstance(d, dict) and "text" in d
                ]
            logger.info("Loaded %d clipboard history entries", len(self._entries))
        except Exception:
            logger.debug("Failed to load clipboard history", exc_info=True)
