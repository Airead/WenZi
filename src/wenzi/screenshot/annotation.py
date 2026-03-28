"""WKWebView annotation layer for screenshot markup.

Creates an NSPanel + WKWebView positioned over the selected region,
loads the Fabric.js annotation canvas, and bridges JS events (confirm,
cancel, save, exported) back to Python for clipboard writing or file
export.

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

# Height reserved for the toolbar area below (or above) the canvas.
# This accounts for the toolbar (40px) + secondary panel (32px) + padding.
_TOOLBAR_HEIGHT = 80

# Directory for temporary screenshot images
_TMP_DIR = os.path.expanduser("~/.cache/WenZi/screenshot_tmp")

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


def compute_toolbar_position(
    region_rect: Dict[str, float],
    screen_height: float,
    toolbar_height: float = _TOOLBAR_HEIGHT,
) -> str:
    """Decide whether the toolbar goes below or above the selection.

    Returns ``"bottom"`` (toolbar below canvas) or ``"top"`` (toolbar above).
    The toolbar is placed below by default.  If it would extend beyond the
    screen bottom, it is placed above instead.
    """
    # In screen coordinates, Y increases downward (origin at top-left).
    selection_bottom = region_rect["y"] + region_rect["height"]
    space_below = screen_height - selection_bottom
    if space_below >= toolbar_height:
        return "bottom"
    return "top"


def compute_panel_frame(
    region_rect: Dict[str, float],
    toolbar_position: str,
    toolbar_height: float = _TOOLBAR_HEIGHT,
) -> Dict[str, float]:
    """Compute the NSPanel frame in screen coordinates (Y-down).

    The panel must be large enough for the canvas *plus* the toolbar.

    Returns ``{"x", "y", "width", "height"}`` in screen coordinates.
    """
    x = region_rect["x"]
    w = region_rect["width"]
    h = region_rect["height"] + toolbar_height

    if toolbar_position == "bottom":
        # Canvas at top, toolbar extends below the selection
        y = region_rect["y"]
    else:
        # Toolbar above — panel starts above the selection
        y = region_rect["y"] - toolbar_height

    return {"x": x, "y": y, "width": w, "height": h}


def build_init_event_data(
    image_url: str,
    region_rect: Dict[str, float],
    toolbar_position: str,
) -> Dict[str, Any]:
    """Build the data dict for the ``init`` event sent to JS."""
    return {
        "imageUrl": image_url,
        "width": region_rect["width"],
        "height": region_rect["height"],
        "toolbarPosition": toolbar_position,
    }


# ---------------------------------------------------------------------------
# Temp image helpers
# ---------------------------------------------------------------------------


def save_cgimage_to_png(cg_image: Any, path: str) -> bool:
    """Write a CGImage to a PNG file. Returns True on success."""
    import Quartz
    from Foundation import NSURL

    os.makedirs(os.path.dirname(path), exist_ok=True)
    url = NSURL.fileURLWithPath_(path)
    dest = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
    if dest is None:
        logger.error("Failed to create image destination: %s", path)
        return False
    Quartz.CGImageDestinationAddImage(dest, cg_image, None)
    ok = Quartz.CGImageDestinationFinalize(dest)
    if not ok:
        logger.error("Failed to finalize image: %s", path)
    return bool(ok)


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
    """WKWebView-based annotation layer over a screenshot selection.

    Creates an NSPanel with a WKWebView that loads the Fabric.js
    annotation canvas, positioned over the selected region.
    """

    def __init__(self) -> None:
        self._panel: Any = None
        self._webview: Any = None
        self._message_handler_obj: Any = None
        self._file_handler: Any = None

        self._on_done: Optional[Callable] = None
        self._on_cancel: Optional[Callable] = None

        self._tmp_image_path: Optional[str] = None
        self._toolbar_position: str = "bottom"

        # Pending action: "clipboard" or "save" — set when waiting for
        # the JS "exported" event to arrive with canvas data.
        self._pending_action: Optional[str] = None

        self._open = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(
        self,
        region_rect: Dict[str, float],
        cropped_image: Any,
        on_done: Callable,
        on_cancel: Callable,
    ) -> None:
        """Show annotation layer over the given region.

        Args:
            region_rect: ``{"x", "y", "width", "height"}`` in screen coords
                (Y-down, matching CGWindowList coordinate space).
            cropped_image: CGImage of the selected region.
            on_done: Called after the annotated image is copied to clipboard.
            on_cancel: Called when the user cancels.
        """
        self._on_done = on_done
        self._on_cancel = on_cancel

        # 1. Save cropped image to temp PNG
        os.makedirs(_TMP_DIR, exist_ok=True)
        import tempfile

        fd, tmp_path = tempfile.mkstemp(suffix=".png", dir=_TMP_DIR)
        os.close(fd)
        self._tmp_image_path = tmp_path

        if not save_cgimage_to_png(cropped_image, tmp_path):
            logger.error("Failed to save temp screenshot image")
            if self._on_cancel:
                self._on_cancel()
            return

        # 2. Compute toolbar position and panel frame
        screen_height = self._get_screen_height()
        self._toolbar_position = compute_toolbar_position(
            region_rect, screen_height
        )
        panel_frame = compute_panel_frame(
            region_rect, self._toolbar_position
        )

        # 3. Build panel + WKWebView
        self._build_panel(panel_frame, region_rect)
        self._open = True

        # 4. Load annotation HTML
        self._load_annotation_html()

        # 5. Send init event after a short delay to let the page load
        from PyObjCTools import AppHelper

        init_data = build_init_event_data(
            f"wz-file://{tmp_path}",
            region_rect,
            self._toolbar_position,
        )

        def _send_init():
            self._send_event("init", init_data)

        # Delay to give WKWebView time to load the page
        AppHelper.callLater(0.3, _send_init)

        logger.debug(
            "Annotation layer shown: %.0fx%.0f at (%.0f, %.0f), toolbar=%s",
            region_rect["width"],
            region_rect["height"],
            region_rect["x"],
            region_rect["y"],
            self._toolbar_position,
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
            self._panel.orderOut_(None)

        self._panel = None
        self._webview = None
        self._message_handler_obj = None
        self._file_handler = None

        # Remove temp image
        if self._tmp_image_path is not None:
            try:
                os.unlink(self._tmp_image_path)
            except OSError:
                pass
            self._tmp_image_path = None

        self._pending_action = None
        logger.debug("Annotation layer closed")

    # ------------------------------------------------------------------
    # Panel construction
    # ------------------------------------------------------------------

    def _get_screen_height(self) -> float:
        """Return the main screen height in points."""
        try:
            from AppKit import NSScreen

            screen = NSScreen.mainScreen()
            return screen.frame().size.height
        except Exception:
            return 1080.0  # reasonable fallback

    def _build_panel(
        self,
        panel_frame: Dict[str, float],
        region_rect: Dict[str, float],
    ) -> None:
        """Build NSPanel + WKWebView with bridge injection."""
        from AppKit import (
            NSBackingStoreBuffered,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
        )
        from Foundation import NSMakeRect
        from WebKit import (
            WKUserContentController,
            WKUserScript,
            WKUserScriptInjectionTimeAtDocumentStart,
            WKWebView,
            WKWebViewConfiguration,
        )
        import Quartz

        # Convert screen coords (Y-down) to AppKit coords (Y-up from bottom)
        screen_height = self._get_screen_height()
        appkit_y = screen_height - panel_frame["y"] - panel_frame["height"]

        from AppKit import NSPanel

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(
                panel_frame["x"],
                appkit_y,
                panel_frame["width"],
                panel_frame["height"],
            ),
            0,  # NSBorderlessWindowMask
            NSBackingStoreBuffered,
            False,
        )

        # Window level: CGShieldingWindowLevel + 1 (above the overlay)
        shielding_level = Quartz.CGShieldingWindowLevel()
        panel.setLevel_(shielding_level + 1)
        panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
        panel.setOpaque_(False)
        panel.setHasShadow_(False)
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
        # Allow access to temp dir and templates dir
        file_handler._allowed_prefixes = [
            os.path.realpath(_TMP_DIR) + os.sep,
            os.path.realpath(_TEMPLATES_DIR) + os.sep,
        ]
        config.setURLSchemeHandler_forURLScheme_(file_handler, "wz-file")
        self._file_handler = file_handler

        # Create WKWebView filling the panel
        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(
                0, 0, panel_frame["width"], panel_frame["height"]
            ),
            config,
        )
        webview.setAutoresizingMask_(0x12)  # Width + Height sizable
        # Transparent background so the panel bg shows through
        webview.setValue_forKey_(False, "drawsBackground")

        panel.contentView().addSubview_(webview)

        self._panel = panel
        self._webview = webview

        # Show the panel
        panel.makeKeyAndOrderFront_(None)

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

        data_url = data.get("dataUrl") if isinstance(data, dict) else None
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
            # Do NOT close — user may continue annotating

        else:
            logger.warning("Exported event with no pending action")

    # ------------------------------------------------------------------
    # Clipboard
    # ------------------------------------------------------------------

    def _copy_to_clipboard(self, png_bytes: bytes) -> None:
        """Write the annotated image to the system clipboard.

        Writes both PNG and TIFF for maximum compatibility.
        """
        from AppKit import (
            NSData,
            NSImage,
            NSPasteboard,
            NSPasteboardTypePNG,
            NSPasteboardTypeTIFF,
        )

        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()

        # Write PNG
        png_data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
        pb.setData_forType_(png_data, NSPasteboardTypePNG)

        # Write TIFF for apps that prefer it
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

        # Bring save panel to the front
        panel.setLevel_(self._panel.level() if self._panel else 0)

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

            # Use the macOS system glass sound
            sound = NSSound.soundNamed_("Glass")
            if sound is not None:
                sound.setVolume_(0.3)
                sound.play()
        except Exception:
            pass  # Sound is non-critical
