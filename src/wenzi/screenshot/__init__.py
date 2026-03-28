"""Screenshot subpackage — screen capture and window metadata."""

from __future__ import annotations

from .capture import capture_screen
from .overlay import ScreenshotOverlay

__all__ = [
    "capture_screen",
    "ScreenshotOverlay",
]
