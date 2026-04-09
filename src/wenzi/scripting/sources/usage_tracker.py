"""Usage frequency tracker for Chooser search results.

Records how often each item is selected for a given query prefix,
allowing search results to be ranked by learned user preference.
Disk writes are batched and deferred to a background thread.
"""

from __future__ import annotations

import json
import logging
import os
import threading

from wenzi.config import DEFAULT_CHOOSER_USAGE_PATH

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.expanduser(DEFAULT_CHOOSER_USAGE_PATH)
_MAX_PREFIX_LEN = 3  # Group by first N chars of query
_FLUSH_DELAY = 2.0  # Seconds to wait before flushing to disk


class UsageTracker:
    """Track item selection frequency per query prefix.

    Data format: ``{query_prefix: {item_id: count}}``.
    Persisted to a JSON file on disk.  Writes are batched: after a
    ``record()`` call the data is flushed to disk after a short delay,
    coalescing multiple rapid selections into a single write.
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _DEFAULT_PATH
        self._data: dict[str, dict[str, int]] = {}
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
            if isinstance(data, dict):
                self._data = data
            logger.debug(
                "Loaded usage data: %d prefixes", len(self._data)
            )
        except Exception:
            logger.debug("Failed to load usage data", exc_info=True)

    def _schedule_flush(self) -> None:
        """Schedule a deferred disk write, coalescing rapid updates."""
        with self._lock:
            if self._flush_timer is not None:
                self._flush_timer.cancel()
            self._flush_timer = threading.Timer(_FLUSH_DELAY, self._flush)
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _flush(self) -> None:
        """Write data to disk (runs on background timer thread)."""
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            snapshot = json.dumps(self._data, ensure_ascii=False)

        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                f.write(snapshot)
        except Exception:
            logger.debug("Failed to save usage data", exc_info=True)

    def record(self, query: str, item_id: str) -> None:
        """Record a selection of *item_id* for *query*."""
        if not query or not item_id:
            return
        prefix = query.strip().lower()[:_MAX_PREFIX_LEN]
        if not prefix:
            return

        with self._lock:
            self._ensure_loaded()
            bucket = self._data.setdefault(prefix, {})
            bucket[item_id] = bucket.get(item_id, 0) + 1
            self._dirty = True

        self._schedule_flush()

    def score(self, query: str, item_id: str) -> int:
        """Return the usage count for *item_id* given *query*.

        Higher count means the user selects this item more often
        for queries starting with this prefix.
        """
        if not query or not item_id:
            return 0
        prefix = query.strip().lower()[:_MAX_PREFIX_LEN]
        if not prefix:
            return 0

        with self._lock:
            self._ensure_loaded()
            return self._data.get(prefix, {}).get(item_id, 0)

    def clear(self) -> None:
        """Clear all usage data."""
        with self._lock:
            self._data = {}
            self._loaded = True
            self._dirty = True
        self._schedule_flush()

    def flush_sync(self) -> None:
        """Immediately flush pending data to disk (for testing)."""
        with self._lock:
            timer = self._flush_timer
            self._flush_timer = None
        if timer is not None:
            timer.cancel()
        self._flush()
