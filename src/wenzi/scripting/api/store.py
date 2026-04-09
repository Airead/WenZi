"""vt.store — key-value persistent storage API for scripts."""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from wenzi.config import DEFAULT_SCRIPT_DATA_PATH

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.expanduser(DEFAULT_SCRIPT_DATA_PATH)
_FLUSH_DELAY = 2.0


class StoreAPI:
    """Simple key-value store backed by a JSON file.

    Thread-safe with deferred disk writes (coalesced via a timer).
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _DEFAULT_PATH
        self._data: dict[str, Any] = {}
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
            logger.debug("Loaded script store: %d keys", len(self._data))
        except Exception:
            logger.debug("Failed to load script store", exc_info=True)

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value for *key*, or *default* if not found."""
        with self._lock:
            self._ensure_loaded()
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set *key* to *value* (must be JSON-serializable)."""
        with self._lock:
            self._ensure_loaded()
            self._data[key] = value
            self._dirty = True
        self._schedule_flush()

    def delete(self, key: str) -> None:
        """Remove *key* from the store."""
        removed = False
        with self._lock:
            self._ensure_loaded()
            if key in self._data:
                del self._data[key]
                self._dirty = True
                removed = True
        if removed:
            self._schedule_flush()

    def keys(self) -> list[str]:
        """Return all keys in the store."""
        with self._lock:
            self._ensure_loaded()
            return list(self._data.keys())

    def clear(self) -> None:
        """Remove all data from the store."""
        with self._lock:
            self._data = {}
            self._loaded = True
            self._dirty = True
        self._schedule_flush()

    def flush_sync(self) -> None:
        """Immediately flush pending data to disk."""
        with self._lock:
            timer = self._flush_timer
            self._flush_timer = None
        if timer is not None:
            timer.cancel()
        self._flush()

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
            logger.debug("Failed to save script store", exc_info=True)
