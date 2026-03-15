"""vt.pasteboard — clipboard read/write API."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PasteboardAPI:
    """Read and write the system clipboard."""

    def get(self) -> str | None:
        """Get the current clipboard text."""
        from voicetext.input import get_clipboard_text

        return get_clipboard_text()

    def set(self, text: str) -> None:
        """Set the clipboard text."""
        from voicetext.input import set_clipboard_text

        set_clipboard_text(text)
        logger.debug("Pasteboard set: %s", text[:50] if text else "")
