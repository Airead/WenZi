"""WKWebView annotation layer for screenshot markup.

Creates an NSPanel + WKWebView that loads a Fabric.js annotation canvas.
The image comes from macOS ``screencapture -i`` (a PNG file on disk).

All PyObjC / WebKit imports are deferred so the module can be imported
(and its pure-logic helpers tested) without a running AppKit environment.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Height reserved for the toolbar area below the canvas.
_TOOLBAR_HEIGHT = 80

# Path to the annotation HTML template
_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_ANNOTATION_HTML = os.path.join(_TEMPLATES_DIR, "annotation.html")

# ---------------------------------------------------------------------------
# Bridge JavaScript (same as webview_panel.py but standalone to avoid
# coupling; ObjC class names must be unique)
# ---------------------------------------------------------------------------

_BRIDGE_JS = r"""
(function() {
    const _handlers = {};
    const _pending = {};
    let _callId = 0;

    const wz = {
        send(event, data) {
            window.webkit.messageHandlers.wz.postMessage(
                {type: 'event', name: event, data: data || null}
            );
        },

        call(method, data, opts) {
            return new Promise(function(resolve, reject) {
                const id = 'c' + (++_callId);
                const timeout = (opts && opts.timeout) || 30000;
                _pending[id] = {resolve: resolve, reject: reject};
                setTimeout(function() {
                    if (_pending[id]) {
                        delete _pending[id];
                        reject(new Error("wz.call timeout: " + method));
                    }
                }, timeout);
                window.webkit.messageHandlers.wz.postMessage(
                    {type: 'call', name: method, data: data || null, callId: id}
                );
            });
        },

        on(event, callback) {
            if (!_handlers[event]) _handlers[event] = [];
            _handlers[event].push(callback);
        },

        _resolve(callId, result) {
            const p = _pending[callId];
            if (p) { delete _pending[callId]; p.resolve(result); }
        },

        _reject(callId, error) {
            const p = _pending[callId];
            if (p) { delete _pending[callId]; p.reject(new Error(error)); }
        },

        _emit(event, data) {
            const cbs = _handlers[event] || [];
            for (const cb of cbs) {
                try { cb(data); } catch(e) { console.error('wz.on handler error:', e); }
            }
        },

        _rejectAll(reason) {
            for (const id of Object.keys(_pending)) {
                const p = _pending[id];
                delete _pending[id];
                p.reject(new Error(reason));
            }
        }
    };

    window.wz = wz;

    // Forward console output to Python logger
    const _origConsole = {
        log: console.log.bind(console),
        warn: console.warn.bind(console),
        error: console.error.bind(console),
    };
    function _forward(level, args) {
        try {
            const msg = Array.from(args).map(a =>
                typeof a === "object" ? JSON.stringify(a) : String(a)
            ).join(" ");
            window.webkit.messageHandlers.wz.postMessage(
                {type: "console", level: level, message: msg}
            );
        } catch {}
    }
    console.log   = function() { _origConsole.log(...arguments);   _forward("info",  arguments); };
    console.warn  = function() { _origConsole.warn(...arguments);  _forward("warning", arguments); };
    console.error = function() { _origConsole.error(...arguments); _forward("error", arguments); };
})();
"""

# ---------------------------------------------------------------------------
# Pure-logic helpers (testable without PyObjC)
# ---------------------------------------------------------------------------


def decode_data_url(data_url: str) -> Optional[bytes]:
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
    """Read width and height of a PNG file without heavy dependencies.

    Falls back to (800, 600) if the image cannot be read.
    """
    try:
        # PNG header: first 8 bytes signature, then IHDR chunk
        # IHDR starts at offset 16: 4 bytes width, 4 bytes height (big-endian)
        import struct

        with open(image_path, "rb") as f:
            sig = f.read(8)
            if sig[:4] != b"\x89PNG":
                return (800, 600)
            f.read(4)  # chunk length
            f.read(4)  # chunk type "IHDR"
            w_bytes = f.read(4)
            h_bytes = f.read(4)
            width = struct.unpack(">I", w_bytes)[0]
            height = struct.unpack(">I", h_bytes)[0]
            return (width, height)
    except Exception:
        return (800, 600)


# ---------------------------------------------------------------------------
# Lazy ObjC classes (avoid PyObjC import at module level)
# ---------------------------------------------------------------------------

_MessageHandler: Any = None
_FileSchemeHandler: Any = None


def _get_message_handler_class() -> Any:
    """Return the ScreenshotAnnotationMessageHandler class."""
    global _MessageHandler
    if _MessageHandler is not None:
        return _MessageHandler

    import objc
    from Foundation import NSObject

    import WebKit  # noqa: F401

    WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")

    class ScreenshotAnnotationMessageHandler(
        NSObject, protocols=[WKScriptMessageHandler]
    ):
        _layer_ref = None

        def userContentController_didReceiveScriptMessage_(
            self, controller, message
        ):
            if self._layer_ref is None:
                return
            raw = message.body()
            try:
                body = dict(raw) if not isinstance(raw, dict) else raw
            except (TypeError, ValueError):
                logger.warning("Cannot convert annotation message: %r", raw)
                return
            self._layer_ref._handle_js_message(body)

    _MessageHandler = ScreenshotAnnotationMessageHandler
    return _MessageHandler


def _get_file_scheme_handler_class() -> Any:
    """Return the ScreenshotAnnotationFileSchemeHandler class."""
    global _FileSchemeHandler
    if _FileSchemeHandler is not None:
        return _FileSchemeHandler

    import mimetypes

    import objc
    from Foundation import NSData, NSObject

    import WebKit  # noqa: F401

    WKURLSchemeHandler = objc.protocolNamed("WKURLSchemeHandler")

    class ScreenshotAnnotationFileSchemeHandler(
        NSObject, protocols=[WKURLSchemeHandler]
    ):
        _allowed_prefixes: list = []

        def webView_startURLSchemeTask_(self, webView, task):
            url = task.request().URL()
            file_path = url.path()

            if not self._is_path_allowed(file_path):
                logger.warning("wz-file:// blocked: %s", file_path)
                self._fail_task(task, 403, "Forbidden")
                return

            try:
                with open(file_path, "rb") as f:
                    data = f.read()
            except FileNotFoundError:
                self._fail_task(task, 404, "Not Found")
                return
            except OSError as exc:
                self._fail_task(task, 500, str(exc))
                return

            mime, _ = mimetypes.guess_type(file_path)
            mime = mime or "application/octet-stream"

            try:
                from Foundation import NSHTTPURLResponse

                response = (
                    NSHTTPURLResponse.alloc()
                    .initWithURL_statusCode_HTTPVersion_headerFields_(
                        url,
                        200,
                        "HTTP/1.1",
                        {
                            "Content-Type": mime,
                            "Content-Length": str(len(data)),
                            "Access-Control-Allow-Origin": "*",
                        },
                    )
                )
                task.didReceiveResponse_(response)
                task.didReceiveData_(
                    NSData.dataWithBytes_length_(data, len(data))
                )
                task.didFinish()
            except Exception:
                pass  # Task may have been stopped

        def webView_stopURLSchemeTask_(self, webView, task):
            pass

        def _is_path_allowed(self, path: str) -> bool:
            real = os.path.realpath(path)
            for prefix in self._allowed_prefixes or []:
                if real.startswith(prefix) or real == prefix.rstrip(os.sep):
                    return True
            return False

        def _fail_task(self, task: Any, code: int, message: str) -> None:
            try:
                from Foundation import NSError

                error = NSError.errorWithDomain_code_userInfo_(
                    "ScreenshotAnnotationFileSchemeHandler",
                    code,
                    {"NSLocalizedDescription": message},
                )
                task.didFailWithError_(error)
            except Exception:
                pass

    _FileSchemeHandler = ScreenshotAnnotationFileSchemeHandler
    return _FileSchemeHandler


# ---------------------------------------------------------------------------
# AnnotationLayer
# ---------------------------------------------------------------------------


class AnnotationLayer:
    """WKWebView-based annotation layer for a screenshot image.

    Opens an NSPanel with a WKWebView that loads the Fabric.js annotation
    canvas, sized to the image from ``screencapture``.
    """

    def __init__(self) -> None:
        self._panel: Any = None
        self._webview: Any = None
        self._message_handler_obj: Any = None
        self._file_handler: Any = None

        self._on_done: Optional[Callable] = None
        self._on_cancel: Optional[Callable] = None

        self._image_path: Optional[str] = None

        # Pending action: "clipboard" or "save" — set when waiting for
        # the JS "exported" event to arrive with canvas data.
        self._pending_action: Optional[str] = None

        self._open = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(
        self,
        image_path: str,
        on_done: Callable,
        on_cancel: Callable,
    ) -> None:
        """Show annotation layer for the given screenshot image.

        Args:
            image_path: Path to the PNG file from screencapture.
            on_done: Called after the annotated image is copied to clipboard.
            on_cancel: Called when the user cancels.
        """
        if not os.path.isfile(image_path):
            logger.error("Screenshot image not found: %s", image_path)
            on_cancel()
            return

        self._on_done = on_done
        self._on_cancel = on_cancel
        self._image_path = image_path

        # Read image dimensions to size the window
        img_w, img_h = get_image_dimensions(image_path)

        # Cap window size to 80% of screen, scale down if needed
        screen_w, screen_h = self._get_screen_size()
        max_w = int(screen_w * 0.8)
        max_h = int(screen_h * 0.8) - _TOOLBAR_HEIGHT
        scale = min(1.0, max_w / max(img_w, 1), max_h / max(img_h, 1))
        canvas_w = int(img_w * scale)
        canvas_h = int(img_h * scale)
        panel_w = canvas_w
        panel_h = canvas_h + _TOOLBAR_HEIGHT

        self._build_panel(panel_w, panel_h)
        self._open = True
        self._load_annotation_html()

        # Send init event after a short delay for page load
        from PyObjCTools import AppHelper

        init_data = {
            "imageUrl": f"wz-file://{image_path}",
            "width": canvas_w,
            "height": canvas_h,
            "toolbarPosition": "bottom",
        }

        AppHelper.callLater(0.3, self._send_event, "init", init_data)

        logger.debug(
            "Annotation layer shown: %dx%d (image %dx%d, scale %.2f)",
            panel_w, panel_h, img_w, img_h, scale,
        )

    def close(self) -> None:
        """Tear down the WKWebView window and clean up."""
        if not self._open:
            return
        self._open = False

        # Clean up WKWebView message handler
        if self._webview is not None:
            try:
                cfg = self._webview.configuration()
                cfg.userContentController().removeScriptMessageHandlerForName_(
                    "wz"
                )
            except Exception:
                pass

        if self._panel is not None:
            from AppKit import NSApp

            self._panel.orderOut_(None)
            NSApp.setActivationPolicy_(1)  # Back to accessory

        self._panel = None
        self._webview = None
        self._message_handler_obj = None
        self._file_handler = None

        # Remove temp image
        if self._image_path is not None:
            try:
                os.unlink(self._image_path)
            except OSError:
                pass
            self._image_path = None

        self._pending_action = None
        logger.debug("Annotation layer closed")

    # ------------------------------------------------------------------
    # Panel construction
    # ------------------------------------------------------------------

    @staticmethod
    def _get_screen_size() -> tuple[float, float]:
        """Return (width, height) of the main screen in points."""
        try:
            from AppKit import NSScreen

            frame = NSScreen.mainScreen().frame()
            return (frame.size.width, frame.size.height)
        except Exception:
            return (1440.0, 900.0)

    def _build_panel(self, width: int, height: int) -> None:
        """Build NSPanel + WKWebView centered on screen."""
        from AppKit import (
            NSApp,
            NSBackingStoreBuffered,
            NSPanel,
            NSWindowStyleMaskClosable,
            NSWindowStyleMaskTitled,
        )
        from Foundation import NSMakeRect
        from WebKit import (
            WKUserContentController,
            WKUserScript,
            WKUserScriptInjectionTimeAtDocumentStart,
            WKWebView,
            WKWebViewConfiguration,
        )

        # Center on screen
        screen_w, screen_h = self._get_screen_size()
        x = (screen_w - width) / 2
        y = (screen_h - height) / 2

        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, width, height),
            style,
            NSBackingStoreBuffered,
            False,
        )

        panel.setTitle_("Screenshot")
        panel.setLevel_(3)  # NSFloatingWindowLevel
        panel.setOpaque_(False)
        panel.setHasShadow_(True)
        panel.setHidesOnDeactivate_(False)

        # WKWebView configuration
        content_controller = WKUserContentController.alloc().init()

        # Inject bridge JS at document start
        bridge_script = (
            WKUserScript.alloc()
            .initWithSource_injectionTime_forMainFrameOnly_(
                _BRIDGE_JS,
                WKUserScriptInjectionTimeAtDocumentStart,
                True,
            )
        )
        content_controller.addUserScript_(bridge_script)

        # Message handler
        handler_cls = _get_message_handler_class()
        handler = handler_cls.alloc().init()
        handler._layer_ref = self
        content_controller.addScriptMessageHandler_name_(handler, "wz")
        self._message_handler_obj = handler

        config = WKWebViewConfiguration.alloc().init()
        config.setUserContentController_(content_controller)

        # Register wz-file:// scheme handler
        file_handler_cls = _get_file_scheme_handler_class()
        file_handler = file_handler_cls.alloc().init()
        image_dir = os.path.dirname(os.path.realpath(self._image_path)) if self._image_path else ""
        file_handler._allowed_prefixes = [
            image_dir + os.sep,
            os.path.realpath(_TEMPLATES_DIR) + os.sep,
        ]
        config.setURLSchemeHandler_forURLScheme_(file_handler, "wz-file")
        self._file_handler = file_handler

        # Create WKWebView filling the panel
        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, width, height),
            config,
        )
        webview.setAutoresizingMask_(0x12)  # Width + Height sizable
        webview.setValue_forKey_(False, "drawsBackground")

        panel.contentView().addSubview_(webview)

        self._panel = panel
        self._webview = webview

        # Show the panel
        NSApp.setActivationPolicy_(0)  # Regular (foreground)
        panel.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def _load_annotation_html(self) -> None:
        """Load the annotation HTML template into the WKWebView."""
        if self._webview is None:
            return

        from Foundation import NSURL

        if not os.path.isfile(_ANNOTATION_HTML):
            logger.error("Annotation HTML not found: %s", _ANNOTATION_HTML)
            return

        file_url = NSURL.fileURLWithPath_(_ANNOTATION_HTML)
        access_url = NSURL.fileURLWithPath_(_TEMPLATES_DIR)
        self._webview.loadFileURL_allowingReadAccessToURL_(
            file_url, access_url
        )

    # ------------------------------------------------------------------
    # JS bridge communication
    # ------------------------------------------------------------------

    def _send_event(self, event: str, data: Any = None) -> None:
        """Send an event from Python to JavaScript."""
        if not self._open or self._webview is None:
            return
        payload = json.dumps(data, ensure_ascii=False)
        js = f"wz._emit({json.dumps(event)}, {payload})"
        self._webview.evaluateJavaScript_completionHandler_(js, None)

    def _handle_js_message(self, body: Dict[str, Any]) -> None:
        """Route an incoming message from the JS bridge."""
        msg_type = body.get("type")
        name = body.get("name", "")
        data = body.get("data")

        if msg_type == "event":
            self._handle_event(name, data)

        elif msg_type == "console":
            level = body.get("level", "info")
            message = body.get("message", "")
            log_fn = getattr(logger, level, logger.info)
            log_fn("[Annotation] %s", message)

        else:
            logger.warning("Unknown annotation message type: %r", msg_type)

    def _handle_event(self, name: str, data: Any) -> None:
        """Handle a named event from JS."""
        if name == "confirm":
            self._pending_action = "clipboard"
            self._send_event("export")

        elif name == "cancel":
            callback = self._on_cancel
            self.close()
            if callback:
                callback()

        elif name == "save":
            self._pending_action = "save"
            self._send_event("export")

        elif name == "exported":
            self._handle_exported(data)

        else:
            logger.debug("Unhandled annotation event: %s", name)

    def _handle_exported(self, data: Any) -> None:
        """Process the exported canvas data from JS."""
        if data is None:
            logger.warning("Exported event with no data")
            return

        data_url = data.get("dataUrl") if hasattr(data, "get") else None
        if not data_url:
            logger.warning("Exported event missing dataUrl")
            return

        png_bytes = decode_data_url(data_url)
        if png_bytes is None:
            logger.warning("Failed to decode exported image")
            return

        action = self._pending_action
        self._pending_action = None

        if action == "clipboard":
            self._copy_to_clipboard(png_bytes)
            self._play_sound()
            callback = self._on_done
            self.close()
            if callback:
                callback()

        elif action == "save":
            self._save_to_file(png_bytes)

        else:
            logger.warning("Exported event with no pending action")

    # ------------------------------------------------------------------
    # Clipboard
    # ------------------------------------------------------------------

    def _copy_to_clipboard(self, png_bytes: bytes) -> None:
        """Write the annotated image to the system clipboard."""
        from AppKit import (
            NSData,
            NSImage,
            NSPasteboard,
            NSPasteboardTypePNG,
            NSPasteboardTypeTIFF,
        )

        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()

        png_data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
        pb.setData_forType_(png_data, NSPasteboardTypePNG)

        ns_image = NSImage.alloc().initWithData_(png_data)
        if ns_image is not None:
            tiff_data = ns_image.TIFFRepresentation()
            if tiff_data is not None:
                pb.setData_forType_(tiff_data, NSPasteboardTypeTIFF)

        logger.debug("Annotated image copied to clipboard (%d bytes PNG)", len(png_bytes))

    # ------------------------------------------------------------------
    # File save
    # ------------------------------------------------------------------

    def _save_to_file(self, png_bytes: bytes) -> None:
        """Show an NSSavePanel and write the image to the chosen path."""
        from AppKit import NSSavePanel

        panel = NSSavePanel.savePanel()
        panel.setTitle_("Save Annotated Screenshot")
        panel.setNameFieldStringValue_("screenshot.png")
        panel.setAllowedContentTypes_(self._png_content_types())
        panel.setCanCreateDirectories_(True)

        if self._panel:
            panel.setLevel_(self._panel.level())

        result = panel.runModal()
        if result == 1:  # NSModalResponseOK
            url = panel.URL()
            if url is not None:
                path = url.path()
                try:
                    with open(path, "wb") as f:
                        f.write(png_bytes)
                    logger.info("Screenshot saved to %s", path)
                except OSError:
                    logger.exception("Failed to save screenshot to %s", path)

    @staticmethod
    def _png_content_types() -> list:
        """Return a list with the PNG UTType for NSSavePanel."""
        try:
            from UniformTypeIdentifiers import UTType

            return [UTType.typeWithIdentifier_("public.png")]
        except ImportError:
            return []

    # ------------------------------------------------------------------
    # Sound feedback
    # ------------------------------------------------------------------

    @staticmethod
    def _play_sound() -> None:
        """Play a subtle feedback sound after clipboard copy."""
        try:
            from AppKit import NSSound

            sound = NSSound.soundNamed_("Glass")
            if sound is not None:
                sound.setVolume_(0.3)
                sound.play()
        except Exception:
            pass
