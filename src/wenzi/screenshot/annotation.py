"""WKWebView annotation layer for screenshot markup.

Composes :class:`~wenzi.scripting.ui.webview_panel.WebViewPanel` to display
a Fabric.js annotation canvas over a screenshot image captured by macOS
``screencapture -i``.
"""

from __future__ import annotations

import base64
import logging
import os
import struct
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Height reserved for the toolbar area below the canvas.
_TOOLBAR_HEIGHT = 80

# Minimum window width so the toolbar is never clipped.
_MIN_WINDOW_WIDTH = 480

# Path to the annotation HTML template
_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_ANNOTATION_HTML = os.path.join(_TEMPLATES_DIR, "annotation.html")


# ---------------------------------------------------------------------------
# Pure-logic helpers (testable without PyObjC)
# ---------------------------------------------------------------------------


def decode_data_url(data_url: str) -> bytes | None:
    """Decode a ``data:image/png;base64,...`` URL to raw bytes."""
    prefix = "data:image/png;base64,"
    if not data_url.startswith(prefix):
        logger.warning("Unexpected data URL prefix")
        return None
    try:
        return base64.b64decode(data_url[len(prefix):], validate=True)
    except Exception:
        logger.exception("Failed to decode data URL")
        return None


def get_image_dimensions(image_path: str) -> tuple[int, int]:
    """Read pixel width and height of an image file.

    Uses NSImage (supports PNG/JPG/GIF/BMP/WebP/TIFF/etc.).
    Falls back to manual PNG header parsing in environments without AppKit.
    Returns (800, 600) if the image cannot be read.
    """
    # Try NSImage first — handles all macOS-supported formats
    try:
        from AppKit import NSImage

        img = NSImage.alloc().initWithContentsOfFile_(image_path)
        if img is not None:
            reps = img.representations()
            if reps and len(reps) > 0:
                rep = reps[0]
                w, h = int(rep.pixelsWide()), int(rep.pixelsHigh())
                if w > 0 and h > 0:
                    return (w, h)
    except Exception:
        pass

    # Fallback: manual PNG header parsing (for test environments)
    try:
        with open(image_path, "rb") as f:
            if f.read(4) != b"\x89PNG":
                return (800, 600)
            f.read(12)  # rest of 8-byte sig + 4-byte chunk length + "IHDR"
            return struct.unpack(">II", f.read(8))
    except Exception:
        return (800, 600)


# ---------------------------------------------------------------------------
# AnnotationLayer
# ---------------------------------------------------------------------------


class AnnotationLayer:
    """WKWebView-based annotation layer for a screenshot image.

    Wraps :class:`WebViewPanel` to display a Fabric.js canvas sized to
    the image from ``screencapture``.
    """

    def __init__(self) -> None:
        self._panel = None  # WebViewPanel instance
        self._on_done: Callable | None = None
        self._on_cancel: Callable | None = None
        self._image_path: str | None = None
        self._delete_on_close: bool = True
        self._pending_action: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(
        self,
        image_path: str,
        on_done: Callable,
        on_cancel: Callable,
        delete_on_close: bool = True,
    ) -> None:
        """Show annotation layer for the given image.

        Args:
            image_path: Path to an image file (PNG, JPG, GIF, BMP, WebP, TIFF, etc.).
            on_done: Called after the annotated image is copied to clipboard.
            on_cancel: Called when the user cancels.
            delete_on_close: If True, delete the image file when the editor closes.
                Set to True for temporary screenshot files, False for user files.
        """
        if not os.path.isfile(image_path):
            logger.error("Image not found: %s", image_path)
            on_cancel()
            return

        self._on_done = on_done
        self._on_cancel = on_cancel
        self._image_path = image_path
        self._delete_on_close = delete_on_close

        img_w, img_h = get_image_dimensions(image_path)
        screen_w, screen_h = self._get_screen_size()
        max_w = int(screen_w * 0.8)
        max_h = int(screen_h * 0.8) - _TOOLBAR_HEIGHT
        scale = min(1.0, max_w / max(img_w, 1), max_h / max(img_h, 1))
        canvas_w = int(img_w * scale)
        canvas_h = int(img_h * scale)
        window_w = max(canvas_w, _MIN_WINDOW_WIDTH)

        from wenzi.scripting.ui.webview_panel import WebViewPanel

        image_dir = os.path.dirname(os.path.realpath(image_path))
        self._panel = WebViewPanel(
            title="WenZi Picture Editor",
            html_file=_ANNOTATION_HTML,
            width=window_w,
            height=canvas_h + _TOOLBAR_HEIGHT,
            resizable=False,
            allowed_read_paths=[image_dir, _TEMPLATES_DIR],
            floating=False,
        )

        self._panel.on("ready", lambda _: self._send_init(canvas_w, canvas_h, window_w))
        self._panel.on("confirm", lambda _: self._request_export("clipboard"))
        self._panel.on("cancel", lambda _: self._do_cancel())
        self._panel.on("save", lambda _: self._request_export("save"))
        self._panel.on("exported", self._handle_exported)
        self._panel.on_close(self._on_panel_closed)
        self._panel.show()

        logger.debug(
            "Annotation layer shown: %dx%d window=%d (image %dx%d, scale %.2f)",
            canvas_w, canvas_h + _TOOLBAR_HEIGHT, window_w, img_w, img_h, scale,
        )

    def close(self) -> None:
        """Tear down the panel and clean up."""
        if self._panel is not None:
            # Release Fabric.js canvas, undo stack, and mosaic buffers
            # before the WKWebView is torn down.
            self._panel.eval_js("cleanupCanvas()")
            self._panel.close()
            self._panel = None

        if self._delete_on_close and self._image_path is not None:
            try:
                os.unlink(self._image_path)
            except OSError:
                pass
        self._image_path = None
        self._pending_action = None
        self._on_done = None
        self._on_cancel = None

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _send_init(self, canvas_w: int, canvas_h: int, window_w: int) -> None:
        """Send init data to JS once the page signals ready.

        The image is sent as a base64 data URL to avoid canvas taint
        issues with both ``file://`` and ``wz-file://`` schemes in WKWebView.
        """
        if self._panel is None or self._image_path is None:
            return
        import base64
        import mimetypes

        mime, _ = mimetypes.guess_type(self._image_path)
        mime = mime or "image/png"
        with open(self._image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")

        self._panel.send("init", {
            "imageUrl": f"data:{mime};base64,{b64}",
            "width": canvas_w,
            "height": canvas_h,
            "windowWidth": window_w,
        })

    def _request_export(self, action: str) -> None:
        self._pending_action = action
        if self._panel:
            self._panel.send("export")

    def _do_cancel(self) -> None:
        callback = self._on_cancel
        self.close()
        if callback:
            callback()

    def _on_panel_closed(self) -> None:
        """Called when user clicks the window close button."""
        self._panel = None  # already closed by WebViewPanel
        callback = self._on_cancel
        self._on_cancel = None
        if self._delete_on_close and self._image_path:
            try:
                os.unlink(self._image_path)
            except OSError:
                pass
        self._image_path = None
        if callback:
            callback()

    def _handle_exported(self, data: Any) -> None:
        """Process the exported canvas data from JS."""
        if data is None:
            return

        data_url = data.get("dataUrl") if hasattr(data, "get") else None
        if not data_url:
            return

        png_bytes = decode_data_url(data_url)
        if png_bytes is None:
            return

        action = self._pending_action
        self._pending_action = None

        if action == "clipboard":
            self._copy_to_clipboard(png_bytes)
            self._notify_copied()
            callback = self._on_done
            self.close()
            if callback:
                callback()
        elif action == "save":
            self._save_to_file(png_bytes)

    # ------------------------------------------------------------------
    # Clipboard / file save / sound
    # ------------------------------------------------------------------

    @staticmethod
    def _copy_to_clipboard(png_bytes: bytes) -> None:
        from AppKit import NSData, NSImage, NSPasteboard, NSPasteboardTypePNG, NSPasteboardTypeTIFF

        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        png_data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
        pb.setData_forType_(png_data, NSPasteboardTypePNG)
        ns_image = NSImage.alloc().initWithData_(png_data)
        if ns_image is not None:
            tiff_data = ns_image.TIFFRepresentation()
            if tiff_data is not None:
                pb.setData_forType_(tiff_data, NSPasteboardTypeTIFF)

    def _save_to_file(self, png_bytes: bytes) -> None:
        from AppKit import NSSavePanel

        panel = NSSavePanel.savePanel()
        panel.setTitle_("Save Annotated Screenshot")
        panel.setNameFieldStringValue_("screenshot.png")
        panel.setAllowedContentTypes_(self._png_content_types())
        panel.setCanCreateDirectories_(True)
        result = panel.runModal()
        if result == 1:
            url = panel.URL()
            if url is not None:
                path = url.path()
                try:
                    with open(path, "wb") as f:
                        f.write(png_bytes)
                    logger.info("Screenshot saved to %s", path)
                    callback = self._on_done
                    self.close()
                    if callback:
                        callback()
                except OSError:
                    logger.exception("Failed to save screenshot to %s", path)

    @staticmethod
    def _png_content_types() -> list:
        try:
            from UniformTypeIdentifiers import UTType
            return [UTType.typeWithIdentifier_("public.png")]
        except ImportError:
            return []

    @staticmethod
    def _notify_copied() -> None:
        from wenzi.i18n import t
        from wenzi.scripting.api.alert import alert

        alert(t("screenshot.copied_to_clipboard"), duration=1.5)

    @staticmethod
    def _get_screen_size() -> tuple[float, float]:
        try:
            from AppKit import NSScreen
            frame = NSScreen.mainScreen().frame()
            return (frame.size.width, frame.size.height)
        except Exception:
            return (1440.0, 900.0)
