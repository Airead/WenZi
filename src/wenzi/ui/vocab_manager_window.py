"""WKWebView-based vocabulary management panel.

Provides a full CRUD interface for browsing, searching, filtering,
editing, and managing manual vocabulary entries.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from wenzi.ui.templates import load_template
from wenzi.ui.web_utils import cleanup_webview

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NSObject subclasses (lazy-created, unique class names)
# ---------------------------------------------------------------------------

def _get_panel_close_delegate_class():
    from wenzi.ui.web_utils import make_panel_close_delegate_class

    return make_panel_close_delegate_class("VocabManagerCloseDelegate")


_VocabManagerNavigationDelegate = None


def _get_navigation_delegate_class():
    global _VocabManagerNavigationDelegate
    if _VocabManagerNavigationDelegate is None:
        from Foundation import NSObject

        class VocabManagerNavigationDelegate(NSObject):
            _panel_ref = None

            def webView_didFinishNavigation_(self, webview, navigation):
                if self._panel_ref is not None:
                    self._panel_ref._on_page_loaded()

        _VocabManagerNavigationDelegate = VocabManagerNavigationDelegate
    return _VocabManagerNavigationDelegate


_VocabManagerMessageHandler = None


def _get_message_handler_class():
    global _VocabManagerMessageHandler
    if _VocabManagerMessageHandler is None:
        import json as _json

        import objc
        import WebKit  # noqa: F401
        from Foundation import NSObject

        WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")

        class VocabManagerMessageHandler(NSObject, protocols=[WKScriptMessageHandler]):
            _panel_ref = None

            def userContentController_didReceiveScriptMessage_(self, controller, message):
                if self._panel_ref is None:
                    return
                raw = message.body()
                try:
                    from Foundation import NSJSONSerialization

                    json_data, _ = NSJSONSerialization.dataWithJSONObject_options_error_(raw, 0, None)
                    body = _json.loads(bytes(json_data))
                except Exception:
                    logger.warning("Cannot convert message body: %r", raw)
                    return
                self._panel_ref._handle_js_message(body)

        _VocabManagerMessageHandler = VocabManagerMessageHandler
    return _VocabManagerMessageHandler


# ---------------------------------------------------------------------------
# Panel class
# ---------------------------------------------------------------------------


class VocabManagerPanel:
    """WKWebView-based floating panel for vocabulary management."""

    _PANEL_WIDTH = 1000
    _PANEL_HEIGHT = 720

    def __init__(self) -> None:
        self._panel = None
        self._webview = None
        self._close_delegate = None
        self._message_handler = None
        self._navigation_delegate = None
        self._page_loaded: bool = False
        self._pending_js: list[str] = []
        self._callbacks: dict[str, Callable] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_visible(self) -> bool:
        return self._panel is not None and self._panel.isVisible()

    def show(self, callbacks: dict[str, Callable]) -> None:
        """Show the vocabulary manager panel."""
        from AppKit import NSApp

        self._callbacks = callbacks
        NSApp.setActivationPolicy_(0)  # Regular

        if self.is_visible:
            self._panel.makeKeyAndOrderFront_(None)
            NSApp.activateIgnoringOtherApps_(True)
            return

        self._build_panel()
        self._panel.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def close(self) -> None:
        """Close the panel and clean up."""
        # Invoke on_close callback before clearing callbacks
        on_close = self._callbacks.get("on_close")
        if on_close is not None:
            try:
                on_close()
            except Exception:
                pass
        if self._panel is not None:
            self._panel.setDelegate_(None)
            self._close_delegate = None
            from wenzi.ui_helpers import release_panel_surfaces

            release_panel_surfaces(self._panel)
            self._panel.orderOut_(None)
            self._panel = None
        cleanup_webview(self._webview)
        self._webview = None
        if self._message_handler is not None:
            self._message_handler._panel_ref = None
        if self._navigation_delegate is not None:
            self._navigation_delegate._panel_ref = None
        self._message_handler = None
        self._navigation_delegate = None
        self._page_loaded = False
        self._pending_js = []
        self._callbacks = {}

        from AppKit import NSApp

        NSApp.setActivationPolicy_(1)  # Accessory

    # ------------------------------------------------------------------
    # JS bridge
    # ------------------------------------------------------------------

    def _eval_js(self, js_code: str) -> None:
        """Evaluate JS in WKWebView, with queue for pre-load calls."""
        if self._webview is None:
            return
        if not self._page_loaded:
            self._pending_js.append(js_code)
            return
        self._webview.evaluateJavaScript_completionHandler_(js_code, None)

    def _on_page_loaded(self) -> None:
        """Flush pending JS calls when page finishes loading."""
        self._inject_i18n()

        pending = self._pending_js[:]
        self._pending_js.clear()
        self._page_loaded = True
        if pending and self._webview is not None:
            combined = ";".join(pending)
            self._webview.evaluateJavaScript_completionHandler_(combined, None)

    def _inject_i18n(self) -> None:
        """Inject i18n translations into the webview JS context."""
        from wenzi.i18n import inject_i18n_into_webview

        inject_i18n_into_webview(self._webview, "vocab.")

    def _handle_js_message(self, body: dict) -> None:
        """Dispatch messages from JavaScript."""
        msg_type = body.get("type", "")

        if msg_type == "console":
            level = body.get("level", "info")
            msg = body.get("message", "")
            getattr(logger, level, logger.info)("JS: %s", msg)

        elif msg_type == "pageReady":
            cb = self._callbacks.get("on_page_ready")
            if cb:
                cb()

        elif msg_type == "search":
            cb = self._callbacks.get("on_search")
            if cb:
                cb(body.get("text", ""), body.get("timeRange", "all"))

        elif msg_type == "toggleTags":
            cb = self._callbacks.get("on_toggle_tags")
            if cb:
                cb(body.get("tags", []))

        elif msg_type == "changePage":
            cb = self._callbacks.get("on_change_page")
            if cb:
                cb(body.get("page", 0))

        elif msg_type == "sort":
            cb = self._callbacks.get("on_sort")
            if cb:
                cb(body.get("column", ""))

        elif msg_type == "clearFilters":
            cb = self._callbacks.get("on_clear_filters")
            if cb:
                cb()

        elif msg_type == "addEntry":
            cb = self._callbacks.get("on_add")
            if cb:
                cb(
                    body.get("variant", ""),
                    body.get("term", ""),
                    body.get("source", "user"),
                    app_bundle_id=body.get("app_bundle_id", ""),
                    asr_model=body.get("asr_model", ""),
                    llm_model=body.get("llm_model", ""),
                )

        elif msg_type == "removeEntry":
            cb = self._callbacks.get("on_remove")
            if cb:
                cb(body.get("variant", ""), body.get("term", ""))

        elif msg_type == "batchRemove":
            cb = self._callbacks.get("on_batch_remove")
            if cb:
                cb(body.get("entries", []))

        elif msg_type == "editEntry":
            cb = self._callbacks.get("on_edit")
            if cb:
                cb(
                    body.get("oldVariant", ""),
                    body.get("oldTerm", ""),
                    body.get("newVariant", ""),
                    body.get("newTerm", ""),
                )

        elif msg_type == "editField":
            cb = self._callbacks.get("on_edit_field")
            if cb:
                cb(
                    body.get("variant", ""),
                    body.get("term", ""),
                    body.get("fields", {}),
                )

        elif msg_type == "getEntryStats":
            cb = self._callbacks.get("on_get_entry_stats")
            if cb:
                cb(body.get("variant", ""), body.get("term", ""))

        elif msg_type == "exportVocab":
            cb = self._callbacks.get("on_export")
            if cb:
                cb()

        elif msg_type == "importVocab":
            cb = self._callbacks.get("on_import")
            if cb:
                cb()

        else:
            logger.warning("Unknown vocab manager message type: %s", msg_type)

    # ------------------------------------------------------------------
    # Panel construction
    # ------------------------------------------------------------------

    def _build_panel(self) -> None:
        """Build NSPanel + WKWebView."""
        from AppKit import (
            NSApp,
            NSBackingStoreBuffered,
            NSClosableWindowMask,
            NSPanel,
            NSResizableWindowMask,
            NSScreen,
            NSStatusWindowLevel,
            NSTitledWindowMask,
        )
        from Foundation import NSURL, NSMakeRect, NSMakeSize
        from WebKit import WKUserContentController, WKWebView

        from wenzi.i18n import t
        from wenzi.ui.result_window_web import _ensure_edit_menu

        _ensure_edit_menu()

        NSApp.setActivationPolicy_(0)

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
            NSTitledWindowMask | NSClosableWindowMask | NSResizableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setMinSize_(NSMakeSize(600, 400))
        panel.setTitle_(t("vocab.title"))
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)

        screen = NSScreen.mainScreen()
        if screen:
            sf = screen.visibleFrame()
            pf = panel.frame()
            x = sf.origin.x + (sf.size.width - pf.size.width) / 2
            y = sf.origin.y + (sf.size.height - pf.size.height) / 2
            panel.setFrameOrigin_((x, y))
        else:
            panel.center()

        delegate_cls = _get_panel_close_delegate_class()
        delegate = delegate_cls.alloc().init()
        delegate._panel_ref = self
        panel.setDelegate_(delegate)
        self._close_delegate = delegate

        from wenzi.ui.web_utils import lightweight_webview_config

        config = lightweight_webview_config(shared=False)
        content_controller = WKUserContentController.alloc().init()

        handler_cls = _get_message_handler_class()
        handler = handler_cls.alloc().init()
        handler._panel_ref = self
        content_controller.addScriptMessageHandler_name_(handler, "action")
        config.setUserContentController_(content_controller)

        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
            config,
        )
        webview.setAutoresizingMask_(0x12)  # Width + Height sizable
        webview.setValue_forKey_(False, "drawsBackground")
        panel.contentView().addSubview_(webview)

        nav_delegate_cls = _get_navigation_delegate_class()
        nav_delegate = nav_delegate_cls.alloc().init()
        nav_delegate._panel_ref = self
        webview.setNavigationDelegate_(nav_delegate)

        self._panel = panel
        self._webview = webview
        self._message_handler = handler
        self._navigation_delegate = nav_delegate
        self._page_loaded = False
        self._pending_js = []

        html = load_template("vocab_manager_web.html")
        webview.loadHTMLString_baseURL_(html, NSURL.URLWithString_("file:///"))
