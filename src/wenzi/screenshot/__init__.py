"""Screenshot subpackage — screen capture and window metadata."""

from __future__ import annotations

from .annotation import AnnotationLayer
from .capture import capture_screen
from .overlay import ScreenshotOverlay

__all__ = [
    "AnnotationLayer",
    "capture_screen",
    "ScreenshotOverlay",
]
