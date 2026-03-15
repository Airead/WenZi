"""Usage frequency tracker for Chooser search results.

Records how often each item is selected for a given query prefix,
allowing search results to be ranked by learned user preference.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.expanduser("~/.config/VoiceText/chooser_usage.json")
_MAX_PREFIX_LEN = 3  # Group by first N chars of query


class UsageTracker:
    """Track item selection frequency per query prefix.

    Data format: ``{query_prefix: {item_id: count}}``.
    Persisted to a JSON file on disk.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path or _DEFAULT_PATH
        self._data: Dict[str, Dict[str, int]] = {}
        self._loaded = False
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not os.path.isfile(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._data = data
            logger.debug(
                "Loaded usage data: %d prefixes", len(self._data)
            )
        except Exception:
            logger.debug("Failed to load usage data", exc_info=True)

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
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
            self._save()

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
            self._save()
