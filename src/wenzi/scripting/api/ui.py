"""wz.ui — UI API for user scripts."""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


class UIAPI:
    """API for creating UI panels, exposed as wz.ui."""

    def webview_panel(
        self,
        title: str,
        html: str = "",
        html_file: str = "",
        width: int = 900,
        height: int = 700,
        resizable: bool = True,
        allowed_read_paths: list[str] | None = None,
        titlebar_hidden: bool = False,
        floating: bool = True,
    ):
        """Create and return a new WebView panel.

        The panel is not shown until ``panel.show()`` is called.

        Provide either *html* (a string) or *html_file* (a path to an HTML
        file on disk).  When *html_file* is used, the file is loaded directly
        via ``loadFileURL`` — no temp file is created, and the file's
        directory is automatically granted read access.

        Args:
            title: Window title.
            html: Initial HTML content string.
            html_file: Path to an HTML file to load directly.
            width: Default width in pixels.
            height: Default height in pixels.
            resizable: Whether the window can be resized.
            allowed_read_paths: Directories the WebView can read via file://.
            titlebar_hidden: Hide the native title bar and traffic light
                buttons so the web content fills the entire window.
            floating: Keep the panel above all other windows. Default True.

        Returns:
            A :class:`WebViewPanel` instance.
        """
        from wenzi.scripting.ui.webview_panel import WebViewPanel

        return WebViewPanel(
            title=title,
            html=html,
            html_file=html_file,
            width=width,
            height=height,
            resizable=resizable,
            allowed_read_paths=allowed_read_paths,
            titlebar_hidden=titlebar_hidden,
            floating=floating,
        )

    def picture_editor(
        self,
        image_path: str,
        on_done: Callable | None = None,
        on_cancel: Callable | None = None,
    ) -> None:
        """Open the picture editor for an image file.

        Supports PNG, JPG, GIF, BMP, WebP, TIFF, and other macOS-supported
        formats.  The user can annotate with drawing tools (rectangle, ellipse,
        arrow, line, pen, mosaic, text, numbered markers) and copy the result
        to the clipboard or save to a file.

        The original image file is **not** modified or deleted.

        Args:
            image_path: Path to the image file to edit.
            on_done: Called after the annotated image is copied to clipboard.
            on_cancel: Called when the user cancels or closes the editor.

        Example::

            wz.ui.picture_editor("/path/to/photo.jpg")
        """
        from wenzi.screenshot.annotation import AnnotationLayer

        layer = AnnotationLayer()
        layer.show(
            image_path=image_path,
            on_done=on_done or (lambda: None),
            on_cancel=on_cancel or (lambda: None),
            delete_on_close=False,
        )
