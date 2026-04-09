"""Query history for Chooser search input.

Records queries that the user executed so they can be recalled with
arrow keys when the search input is empty — similar to Alfred's
query history.

Disk writes are batched and deferred to a background thread.
"""

from __future__ import annotations

import json
import logging
import os
import threading

from wenzi.config import DEFAULT_CHOOSER_HISTORY_PATH

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.expanduser(DEFAULT_CHOOSER_HISTORY_PATH)
_MAX_ENTRIES = 100
_FLUSH_DELAY = 2.0


class QueryHistory:
    """Persist and recall recent chooser queries.

    Data format: JSON array ``["oldest", ..., "newest"]``.
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _DEFAULT_PATH
        self._entries: list[str] = []  # oldest first
        self._loaded = False
        self._lock = threading.Lock()
        self._dirty = False
        self._flush_timer: threading.Timer | None = None

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not os.path.isfile(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._entries = [e for e in data if isinstance(e, str) and e.strip()]
            logger.debug("Loaded query history: %d entries", len(self._entries))
        except Exception:
            logger.debug("Failed to load query history", exc_info=True)

    def record(self, query: str) -> None:
        """Record a query. Deduplicates by moving to the end."""
        if not query or not query.strip():
            return

        with self._lock:
            self._ensure_loaded()
            # Remove existing occurrence (dedup)
            try:
                self._entries.remove(query)
            except ValueError:
                pass
            self._entries.append(query)
            # Trim oldest entries
            if len(self._entries) > _MAX_ENTRIES:
                self._entries = self._entries[-_MAX_ENTRIES:]
            self._dirty = True

        self._schedule_flush()

    def entries(self) -> list[str]:
        """Return entries in newest-first order."""
        with self._lock:
            self._ensure_loaded()
            return list(reversed(self._entries))

    def clear(self) -> None:
        """Clear all history."""
        with self._lock:
            self._entries = []
            self._loaded = True
            self._dirty = True
        self._schedule_flush()

    def _schedule_flush(self) -> None:
        with self._lock:
            if self._flush_timer is not None:
                self._flush_timer.cancel()
            self._flush_timer = threading.Timer(_FLUSH_DELAY, self._flush)
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _flush(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            snapshot = json.dumps(self._entries, ensure_ascii=False)

        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                f.write(snapshot)
        except Exception:
            logger.debug("Failed to save query history", exc_info=True)

    def flush_sync(self) -> None:
        """Immediately flush pending data to disk."""
        with self._lock:
            timer = self._flush_timer
            self._flush_timer = None
        if timer is not None:
            timer.cancel()
        self._flush()
