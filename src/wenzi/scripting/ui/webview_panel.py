"""WebViewPanel — NSPanel + WKWebView wrapper with JS<->Python bridge.

Provides a reusable panel for displaying HTML content with bidirectional
communication between JavaScript and Python via a message handler bridge.

Bridge protocol (JS side):
  wz.send(event, data)     — fire-and-forget event to Python
  wz.call(method, data)    — call Python handler, returns Promise
  wz.on(event, callback)   — listen for events from Python

Bridge protocol (Python side):
  panel.send(event, data)  — emit event to JS
  panel.on(event, cb)      — listen for events from JS
  panel.handle(name)(fn)   — register a call handler
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bridge JavaScript injected at document start
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

        playAudio(url) {
            return wz.call('playAudio', {url: url});
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

    // Forward console output to Python logger via bridge
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
# Lazy ObjC classes (avoid PyObjC import at module level)
# ---------------------------------------------------------------------------

_PanelCloseDelegate = None
_MessageHandler = None
_FileSchemeHandler = None
_DragView = None


def _get_close_delegate_class():
    global _PanelCloseDelegate
    if _PanelCloseDelegate is not None:
        return _PanelCloseDelegate

    from Foundation import NSObject

    class WebViewPanelCloseDelegate(NSObject):
        _panel_ref = None

        def windowWillClose_(self, notification):
            if self._panel_ref is not None:
                self._panel_ref.close()

    _PanelCloseDelegate = WebViewPanelCloseDelegate
    return _PanelCloseDelegate


def _get_message_handler_class():
    global _MessageHandler
    if _MessageHandler is not None:
        return _MessageHandler

    import objc
    import WebKit  # noqa: F401
    from Foundation import NSObject

    WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")

    class WebViewPanelMessageHandler(NSObject, protocols=[WKScriptMessageHandler]):
        _panel_ref = None

        def userContentController_didReceiveScriptMessage_(self, controller, message):
            if self._panel_ref is None:
                return
            raw = message.body()
            # WKWebView bridges JS objects to NSDictionary via PyObjC;
            # convert to a plain Python dict without JSON roundtrip.
            try:
                body = dict(raw) if not isinstance(raw, dict) else raw
            except (TypeError, ValueError):
                logger.warning("Cannot convert webview message: %r", raw)
                return
            self._panel_ref._handle_js_message(body)

    _MessageHandler = WebViewPanelMessageHandler
    return _MessageHandler


def _get_file_scheme_handler_class():
    global _FileSchemeHandler
    if _FileSchemeHandler is not None:
        return _FileSchemeHandler

    import mimetypes

    import objc
    import WebKit  # noqa: F401
    from Foundation import NSData, NSObject

    WKURLSchemeHandler = objc.protocolNamed("WKURLSchemeHandler")

    class WZFileSchemeHandler(NSObject, protocols=[WKURLSchemeHandler]):
        """Serve local files via the ``wz-file://`` custom URL scheme.

        Only files under paths listed in ``_allowed_prefixes`` are served.
        """

        # Pre-resolved allowed path prefixes (set once, each ending with os.sep)
        _allowed_prefixes: list = []

        def webView_startURLSchemeTask_(self, webView, task):
            url = task.request().URL()
            file_path = url.path()

            # Security: check path against allowed prefixes
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

                response = NSHTTPURLResponse.alloc().initWithURL_statusCode_HTTPVersion_headerFields_(
                    url,
                    200,
                    "HTTP/1.1",
                    {
                        "Content-Type": mime,
                        "Content-Length": str(len(data)),
                        "Access-Control-Allow-Origin": "*",
                    },
                )
                task.didReceiveResponse_(response)
                task.didReceiveData_(NSData.dataWithBytes_length_(data, len(data)))
                task.didFinish()
            except Exception:
                # Task may have been stopped — ignore
                pass

        def webView_stopURLSchemeTask_(self, webView, task):
            pass

        def _is_path_allowed(self, path):
            real = os.path.realpath(path)
            for prefix in self._allowed_prefixes or []:
                # prefix is pre-resolved and ends with os.sep
                if real.startswith(prefix) or real == prefix.rstrip(os.sep):
                    return True
            return False

        def _fail_task(self, task, code, message):
            try:
                from Foundation import NSError

                error = NSError.errorWithDomain_code_userInfo_(
                    "WZFileSchemeHandler",
                    code,
                    {"NSLocalizedDescription": message},
                )
                task.didFailWithError_(error)
            except Exception:
                pass

    _FileSchemeHandler = WZFileSchemeHandler
    return _FileSchemeHandler


def _get_drag_view_class():
    """Transparent NSView that enables window dragging over WKWebView."""
    global _DragView
    if _DragView is not None:
        return _DragView

    import objc
    from AppKit import NSView

    class WebViewPanelDragView(NSView):
        """Transparent overlay that forwards drag to the window title bar."""

        def initWithFrame_(self, frame):
            self = objc.super(WebViewPanelDragView, self).initWithFrame_(frame)
            return self

        def mouseDown_(self, event):
            self.window().performWindowDragWithEvent_(event)

        def acceptsFirstMouse_(self, event):
            return True

    _DragView = WebViewPanelDragView
    return _DragView


# ---------------------------------------------------------------------------
# WebViewPanel
# ---------------------------------------------------------------------------


class WebViewPanel:
    """NSPanel + WKWebView wrapper with bidirectional JS<->Python bridge.

    Args:
        title: Window title.
        html: Initial HTML content to display.
        width: Panel width in points. Default 900.
        height: Panel height in points. Default 700.
        resizable: Whether the panel is resizable. Default True.
        allowed_read_paths: Paths the WKWebView may read from via file:// URLs.
    """

    def __init__(
        self,
        *,
        title: str,
        html: str = "",
        html_file: str = "",
        width: int = 900,
        height: int = 700,
        resizable: bool = True,
        allowed_read_paths: list[str] | None = None,
        titlebar_hidden: bool = False,
        floating: bool = True,
    ) -> None:
        self._title = title
        self._html = html
        self._html_file = html_file
        self._width = width
        self._height = height
        self._resizable = resizable
        self._allowed_read_paths = allowed_read_paths or []
        self._titlebar_hidden = titlebar_hidden
        self._floating = floating

        self._panel = None
        self._webview = None
        self._close_delegate = None
        self._message_handler_obj = None
        self._open = False

        # Bridge state
        self._event_handlers: dict[str, list[Callable]] = defaultdict(list)
        self._call_handlers: dict[str, Callable] = {
            "playAudio": self._builtin_play_audio,
        }

        # Close callbacks
        self._on_close_callbacks: list = []

        # Temp file for HTML loading with allowed_read_paths
        self._tmp_html_path: str | None = None

        if self._titlebar_hidden:
            import weakref

            ref = weakref.ref(self)
            self.on("close", lambda _data: (r := ref()) and r.close())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(self) -> None:
        """Show the panel, creating it if needed. Safe to call from any thread."""
        if threading.current_thread() is not threading.main_thread():
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self.show)
            return

        from AppKit import NSApp

        NSApp.setActivationPolicy_(0)  # Regular (foreground)

        if self._open and self._panel is not None:
            self._panel.makeKeyAndOrderFront_(None)
            NSApp.activateIgnoringOtherApps_(True)
            return

        if self._panel is None:
            self._build_panel()

        if self._html_file:
            self._load_file(self._html_file)
        else:
            self._load_html(self._html)
        self._open = True

        self._panel.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def close(self) -> None:
        """Close panel and restore accessory mode."""
        if not self._open:
            return

        # Fire on_close callbacks while the panel is still nominally open,
        # so callbacks that inspect self._open see True.
        for cb in self._on_close_callbacks:
            try:
                cb()
            except Exception:
                logger.exception("Error in on_close callback")

        # Mark closed so no new JS messages are processed during teardown
        self._open = False

        # Reject all pending JS calls
        self._reject_all_pending("Panel closed")

        # Clean up temp HTML file
        if self._tmp_html_path is not None:
            try:
                os.unlink(self._tmp_html_path)
            except OSError:
                pass
            self._tmp_html_path = None

        # Break WKWebView retain cycles before removing the panel.
        from wenzi.ui.web_utils import cleanup_webview

        cleanup_webview(self._webview, handler_name="wz")
        if self._message_handler_obj is not None:
            self._message_handler_obj._panel_ref = None
        if self._close_delegate is not None:
            self._close_delegate._panel_ref = None
        if self._panel is not None:
            self._panel.setDelegate_(None)

        if self._panel is not None:
            from AppKit import NSApp

            self._panel.orderOut_(None)
            other_visible = any(w.isVisible() for w in NSApp.windows() if w != self._panel)
            if other_visible:
                NSApp.activateIgnoringOtherApps_(True)
            else:
                NSApp.setActivationPolicy_(1)  # Accessory (statusbar-only)

        self._webview = None
        self._panel = None
        self._file_handler = None
        self._message_handler_obj = None
        self._close_delegate = None
        self._on_close_callbacks = []
        self._event_handlers = {}
        self._call_handlers = {}

    def set_html(self, html: str) -> None:
        """Update the HTML content."""
        self._html = html
        if self._webview is not None and self._open:
            self._load_html(html)

    def eval_js(self, js_code: str) -> None:
        """Evaluate JavaScript in the web view."""
        if not self._open or self._webview is None:
            return
        self._webview.evaluateJavaScript_completionHandler_(js_code, None)

    def send(self, event: str, data: Any = None) -> None:
        """Send an event from Python to JavaScript."""
        if not self._open:
            return
        payload = json.dumps(data, ensure_ascii=False)
        js = f"wz._emit({json.dumps(event)}, {payload})"
        self.eval_js(js)

    def on(self, event: str, callback: Callable) -> None:
        """Register a handler for events sent from JavaScript."""
        self._event_handlers[event].append(callback)

    def handle(self, name: str) -> Callable:
        """Decorator to register a call handler for JS wz.call() requests."""

        def decorator(fn: Callable) -> Callable:
            self._call_handlers[name] = fn
            return fn

        return decorator

    def on_close(self, callback: Callable) -> None:
        """Register a callback to be called when the panel is closed."""
        self._on_close_callbacks.append(callback)

    # ------------------------------------------------------------------
    # Built-in call handlers
    # ------------------------------------------------------------------

    def _builtin_play_audio(self, data: Any) -> None:
        """Play an audio URL via NSSound (avoids Now Playing icon)."""
        url = (data or {}).get("url", "")
        if not url:
            return
        try:
            from AppKit import NSSound
            from Foundation import NSURL

            sound = NSSound.alloc().initWithContentsOfURL_byReference_(NSURL.URLWithString_(url), False)
            if sound:
                sound.play()
        except Exception:
            logger.debug("Failed to play audio: %s", url, exc_info=True)

    # ------------------------------------------------------------------
    # JS message routing
    # ------------------------------------------------------------------

    def _handle_js_message(self, body: dict[str, Any]) -> None:
        """Route an incoming message from the JS bridge."""
        if not self._open:
            return
        msg_type = body.get("type")
        name = body.get("name", "")
        data = body.get("data")

        if msg_type == "event":
            handlers = self._event_handlers.get(name, [])
            for h in handlers:
                try:
                    h(data)
                except Exception:
                    logger.exception("Error in event handler for %r", name)

        elif msg_type == "call":
            call_id = body.get("callId", "")
            if name in self._call_handlers:
                threading.Thread(
                    target=self._run_call_handler,
                    args=(name, data, call_id),
                    daemon=True,
                ).start()
            else:
                self._reject_call(call_id, f"No handler registered for '{name}'")

        elif msg_type == "console":
            level = body.get("level", "info")
            message = body.get("message", "")
            log_fn = getattr(logger, level, logger.info)
            log_fn("[WebView] %s", message)

        else:
            logger.warning("Unknown bridge message type: %r", msg_type)

    def _run_call_handler(self, name: str, data: Any, call_id: str) -> None:
        """Run the call handler (designed for background thread dispatch)."""
        handler = self._call_handlers[name]
        try:
            result = handler(data)
            self._resolve_call(call_id, result)
        except Exception as exc:
            self._reject_call(call_id, str(exc))

    def _resolve_call(self, call_id: str, result: Any) -> None:
        """Send a success response back to JS (dispatched to main thread)."""
        if not self._open:
            return

        def _do():
            payload = json.dumps(result if result is not None else None, ensure_ascii=False)
            self.eval_js(f"wz._resolve({json.dumps(call_id)}, {payload})")

        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(_do)
        except Exception:
            logger.exception("Failed to dispatch resolve to main thread")

    def _reject_call(self, call_id: str, error: str) -> None:
        """Send an error response back to JS (dispatched to main thread)."""
        if not self._open:
            return

        def _do():
            self.eval_js(f"wz._reject({json.dumps(call_id)}, {json.dumps(error)})")

        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(_do)
        except Exception:
            logger.exception("Failed to dispatch reject to main thread")

    def _reject_all_pending(self, reason: str) -> None:
        """Reject all pending JS calls via the bridge."""
        if self._webview is not None:
            js = f"wz._rejectAll({json.dumps(reason)})"
            self._webview.evaluateJavaScript_completionHandler_(js, None)

    # ------------------------------------------------------------------
    # Panel positioning
    # ------------------------------------------------------------------

    @staticmethod
    def _center_on_mouse_screen(panel) -> None:
        """Center *panel* on the screen containing the mouse pointer.

        Uses ``NSEvent.mouseLocation()`` to find the active screen so
        the panel appears on the correct display / fullscreen Space.
        Falls back to ``panel.center()`` if detection fails.
        """
        try:
            from AppKit import NSEvent, NSScreen
            from Foundation import NSMakeRect

            mouse = NSEvent.mouseLocation()
            target = None
            for screen in NSScreen.screens():
                if (
                    screen.frame().origin.x <= mouse.x < screen.frame().origin.x + screen.frame().size.width
                    and screen.frame().origin.y <= mouse.y < screen.frame().origin.y + screen.frame().size.height
                ):
                    target = screen
                    break
            if target is None:
                target = NSScreen.mainScreen()
            if target is None:
                panel.center()
                return
            sf = target.visibleFrame()
            pw = panel.frame().size.width
            ph = panel.frame().size.height
            x = sf.origin.x + (sf.size.width - pw) / 2
            y = sf.origin.y + (sf.size.height - ph) / 2
            panel.setFrame_display_(NSMakeRect(x, y, pw, ph), False)
        except Exception:
            panel.center()

    # ------------------------------------------------------------------
    # Panel construction
    # ------------------------------------------------------------------

    def _build_panel(self) -> None:
        """Build NSPanel + WKWebView with bridge injection."""
        from AppKit import (
            NSBackingStoreBuffered,
            NSClosableWindowMask,
            NSFullSizeContentViewWindowMask,
            NSMiniaturizableWindowMask,
            NSPanel,
            NSResizableWindowMask,
            NSStatusWindowLevel,
            NSTitledWindowMask,
            NSViewMinYMargin,
            NSViewWidthSizable,
            NSWindowCloseButton,
            NSWindowMiniaturizeButton,
            NSWindowTitleHidden,
            NSWindowZoomButton,
        )
        from Foundation import NSMakeRect
        from WebKit import (
            WKUserContentController,
            WKUserScript,
            WKUserScriptInjectionTimeAtDocumentStart,
            WKWebView,
        )

        style = NSTitledWindowMask | NSClosableWindowMask | NSMiniaturizableWindowMask
        if self._resizable:
            style |= NSResizableWindowMask
        if self._titlebar_hidden:
            style |= NSFullSizeContentViewWindowMask

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._width, self._height),
            style,
            NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_(self._title)
        if self._floating:
            panel.setLevel_(NSStatusWindowLevel)
            panel.setFloatingPanel_(True)
            panel.setCollectionBehavior_(1 << 1)  # moveToActiveSpace
        panel.setHidesOnDeactivate_(False)
        self._center_on_mouse_screen(panel)

        if self._titlebar_hidden:
            panel.setTitlebarAppearsTransparent_(True)
            panel.setTitleVisibility_(NSWindowTitleHidden)
            for button_type in (
                NSWindowCloseButton,
                NSWindowMiniaturizeButton,
                NSWindowZoomButton,
            ):
                btn = panel.standardWindowButton_(button_type)
                if btn:
                    btn.setHidden_(True)

        # Ensure Edit menu for Cmd+C/V/A support in WKWebView
        from wenzi.ui.result_window_web import _ensure_edit_menu

        _ensure_edit_menu()

        # Close delegate
        delegate_cls = _get_close_delegate_class()
        delegate = delegate_cls.alloc().init()
        delegate._panel_ref = self
        panel.setDelegate_(delegate)
        self._close_delegate = delegate

        # WKWebView with bridge script + message handler
        content_controller = WKUserContentController.alloc().init()

        # Inject bridge JS at document start
        bridge_script = WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
            _BRIDGE_JS,
            WKUserScriptInjectionTimeAtDocumentStart,
            True,
        )
        content_controller.addUserScript_(bridge_script)

        # Message handler
        handler_cls = _get_message_handler_class()
        handler = handler_cls.alloc().init()
        handler._panel_ref = self
        content_controller.addScriptMessageHandler_name_(handler, "wz")
        self._message_handler_obj = handler

        from wenzi.ui.web_utils import lightweight_webview_config

        config = lightweight_webview_config(shared=False)
        config.setUserContentController_(content_controller)

        # Register wz-file:// scheme handler for local file access from JS
        file_handler_cls = _get_file_scheme_handler_class()
        file_handler = file_handler_cls.alloc().init()
        # Build allowed path prefixes: allowed_read_paths + HTML file's dir
        allowed = [os.path.expanduser(p) for p in self._allowed_read_paths]
        if self._html_file:
            allowed.append(os.path.dirname(os.path.abspath(os.path.expanduser(self._html_file))))
        # Pre-resolve and normalize prefixes (each ends with os.sep)
        file_handler._allowed_prefixes = [os.path.realpath(p) + os.sep for p in allowed]
        config.setURLSchemeHandler_forURLScheme_(file_handler, "wz-file")
        self._file_handler = file_handler

        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, self._width, self._height),
            config,
        )
        webview.setAutoresizingMask_(0x12)  # Width + Height sizable
        panel.contentView().addSubview_(webview)

        # Drag overlay — offset from left to keep the HTML close button clickable
        if self._titlebar_hidden:
            drag_cls = _get_drag_view_class()
            drag_height = 28
            drag_left = 30
            drag_view = drag_cls.alloc().initWithFrame_(
                NSMakeRect(
                    drag_left,
                    self._height - drag_height,
                    self._width - drag_left,
                    drag_height,
                )
            )
            drag_view.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
            panel.contentView().addSubview_(drag_view)

        self._panel = panel
        self._webview = webview

    def _access_url(self, extra_paths: list[str]) -> Any:
        """Compute a common-ancestor access URL for loadFileURL.

        Combines *extra_paths* with ``allowed_read_paths`` and returns an
        ``NSURL`` pointing to their common ancestor directory.
        """
        from Foundation import NSURL

        all_paths = list(extra_paths)
        for p in self._allowed_read_paths:
            all_paths.append(os.path.expanduser(p))
        if not all_paths:
            return NSURL.URLWithString_("about:blank")
        ancestor = os.path.commonpath(all_paths) if len(all_paths) > 1 else all_paths[0]
        return NSURL.fileURLWithPath_(ancestor)

    def _load_html(self, html: str) -> None:
        """Load HTML into the webview.

        If allowed_read_paths is set, writes to a temp file and uses
        loadFileURL:allowingReadAccessToURL: so local file:// resources
        are accessible. Otherwise uses loadHTMLString:baseURL:.
        """
        if self._webview is None:
            return

        from Foundation import NSURL

        if self._allowed_read_paths:
            tmp = tempfile.NamedTemporaryFile(
                suffix=".html",
                delete=False,
                mode="w",
                encoding="utf-8",
            )
            tmp.write(html)
            tmp.close()

            file_url = NSURL.fileURLWithPath_(tmp.name)
            access_url = self._access_url([tmp.name])

            # Clean up previous temp file
            if self._tmp_html_path is not None:
                try:
                    os.unlink(self._tmp_html_path)
                except OSError:
                    pass
            self._tmp_html_path = tmp.name

            self._webview.loadFileURL_allowingReadAccessToURL_(file_url, access_url)
        else:
            self._webview.loadHTMLString_baseURL_(html, NSURL.URLWithString_("about:blank"))

    def _load_file(self, file_path: str) -> None:
        """Load an HTML file directly via loadFileURL.

        Grants read access to the file's directory and all allowed_read_paths.
        """
        if self._webview is None:
            return

        file_path = os.path.expanduser(file_path)
        if not os.path.isfile(file_path):
            logger.error("html_file not found: %s", file_path)
            return

        from Foundation import NSURL

        file_url = NSURL.fileURLWithPath_(file_path)
        access_url = self._access_url([os.path.dirname(file_path)])
        self._webview.loadFileURL_allowingReadAccessToURL_(file_url, access_url)
