"""vt.pasteboard — clipboard read/write and history API."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class PasteboardAPI:
    """Read and write the system clipboard, with optional history access."""

    def __init__(self) -> None:
        self._clipboard_monitor = None

    def _set_monitor(self, monitor) -> None:
        """Inject the ClipboardMonitor instance (called by ScriptEngine)."""
        self._clipboard_monitor = monitor

    def get(self) -> str | None:
        """Get the current clipboard text."""
        from voicetext.input import get_clipboard_text

        return get_clipboard_text()

    def set(self, text: str) -> None:
        """Set the clipboard text."""
        from voicetext.input import set_clipboard_text

        set_clipboard_text(text)
        logger.debug("Pasteboard set: %s", text[:50] if text else "")

    def history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent clipboard history entries as plain dicts.

        Each entry has keys: ``text``, ``timestamp``, ``source_app``,
        and optionally ``image_path`` (for image entries).

        Returns an empty list when the clipboard monitor is not running.
        """
        if self._clipboard_monitor is None:
            return []
        entries = self._clipboard_monitor.entries  # already newest-first
        result: List[Dict[str, Any]] = []
        for entry in entries[:limit]:
            d: Dict[str, Any] = {
                "text": entry.text,
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%S", time.localtime(entry.timestamp),
                ),
                "source_app": entry.source_app,
            }
            if entry.image_path:
                d["image_path"] = entry.image_path
            result.append(d)
        return result

    def clear_history(self) -> None:
        """Clear all clipboard history entries."""
        if self._clipboard_monitor is not None:
            self._clipboard_monitor.clear()
