"""WebView-based settings panel.

Uses WKWebView + WKScriptMessageHandler for a modern HTML/CSS/JS settings UI.
Drop-in replacement for the native PyObjC SettingsPanel.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from wenzi.ui.templates import load_template

logger = logging.getLogger(__name__)

_LOG_LEVELS = ("debug", "info", "warning", "error")

# ---------------------------------------------------------------------------
# Bridge JavaScript injected at document start
# ---------------------------------------------------------------------------
_BRIDGE_JS = """\
(function() {
    window._postMessage = function(msg) {
        window.webkit.messageHandlers.wz.postMessage(msg);
    };
    // Forward console to Python logger
    var _origConsole = {log: console.log.bind(console), warn: console.warn.bind(console), error: console.error.bind(console)};
    function _forward(level, args) {
        try {
            var msg = Array.from(args).map(function(a) { return typeof a === 'object' ? JSON.stringify(a) : String(a); }).join(' ');
            window.webkit.messageHandlers.wz.postMessage({type: 'console', level: level, message: msg});
        } catch(e) {}
    }
    console.log = function() { _origConsole.log.apply(null, arguments); _forward('info', arguments); };
    console.warn = function() { _origConsole.warn.apply(null, arguments); _forward('warning', arguments); };
    console.error = function() { _origConsole.error.apply(null, arguments); _forward('error', arguments); };
})();
"""

# ---------------------------------------------------------------------------
# Close delegate (shared factory from web_utils)
# ---------------------------------------------------------------------------


def _get_panel_close_delegate_class():
    from wenzi.ui.web_utils import make_panel_close_delegate_class

    return make_panel_close_delegate_class("SettingsWebPanelCloseDelegate")


# ---------------------------------------------------------------------------
# WKScriptMessageHandler (lazy-created)
# ---------------------------------------------------------------------------
_MessageHandler = None


def _get_message_handler_class():
    global _MessageHandler
    if _MessageHandler is None:
        import objc

        # Load WebKit framework first so the protocol is available
        import WebKit  # noqa: F401
        from Foundation import NSObject

        WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")
        logger.debug("WKScriptMessageHandler protocol: %s", WKScriptMessageHandler)

        class SettingsWebMessageHandler(
            NSObject, protocols=[WKScriptMessageHandler]
        ):
            _panel_ref = None

            def userContentController_didReceiveScriptMessage_(
                self, controller, message
            ):
                if self._panel_ref is None:
                    return
                raw = message.body()
                # WKWebView returns NSDictionary with ObjC value types;
                # JSON roundtrip converts everything to native Python types
                try:
                    from Foundation import NSJSONSerialization
                    json_data, _ = (
                        NSJSONSerialization
                        .dataWithJSONObject_options_error_(raw, 0, None)
                    )
                    body = json.loads(bytes(json_data))
                except Exception:
                    logger.warning("Cannot convert message body: %r", raw)
                    return
                self._panel_ref._handle_js_message(body)

        _MessageHandler = SettingsWebMessageHandler
    return _MessageHandler


# ---------------------------------------------------------------------------
# Panel class
# ---------------------------------------------------------------------------


class SettingsWebPanel:
    """WKWebView-based settings panel.

    Drop-in replacement for the native PyObjC SettingsPanel, with the same
    public API surface.
    """

    _PANEL_WIDTH = 750
    _PANEL_HEIGHT = 560

    def __init__(self) -> None:
        self._panel = None
        self._webview = None
        self._close_delegate = None
        self._message_handler = None
        self._callbacks: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_visible(self) -> bool:
        """Return True if the panel is currently visible."""
        if self._panel is None:
            return False
        return bool(self._panel.isVisible())

    def show(self, state: dict, callbacks: dict) -> None:
        """Show the settings panel with the given state and callbacks."""
        self._callbacks = callbacks
        self._build_panel(state)

        self._panel.makeKeyAndOrderFront_(None)

        from AppKit import NSApp

        NSApp.activateIgnoringOtherApps_(True)

    def close(self) -> None:
        """Close the panel and release resources."""
        if self._panel is not None:
            self._panel.setDelegate_(None)
            self._close_delegate = None
            from wenzi.ui_helpers import release_panel_surfaces

            release_panel_surfaces(self._panel)
            self._panel.orderOut_(None)
            self._panel = None
        from wenzi.ui.web_utils import cleanup_webview

        cleanup_webview(self._webview, handler_name="wz")
        self._webview = None
        if self._message_handler is not None:
            self._message_handler._panel_ref = None
        self._message_handler = None
        self._callbacks = None

        from AppKit import NSApp

        NSApp.setActivationPolicy_(1)  # Accessory (statusbar-only)

    def update_state(self, state: dict) -> None:
        """Push new state to JS for incremental DOM update."""
        if self._webview is None or not self.is_visible:
            return
        prepared = self._prepare_state(state, include_i18n=False)
        payload = json.dumps(prepared, ensure_ascii=False)
        self._webview.evaluateJavaScript_completionHandler_(
            f"_updateState({payload})", None
        )

    def update_stt_model(
        self, preset_id, remote_asr
    ) -> None:
        """Update STT model selection in the webview."""
        if self._webview is None or not self.is_visible:
            return
        payload = json.dumps(
            {"current_preset_id": preset_id, "current_remote_asr": remote_asr},
            ensure_ascii=False,
        )
        self._webview.evaluateJavaScript_completionHandler_(
            f"_updateSttSelection({payload})", None
        )

    def update_enhance_mode(self, mode_id: str) -> None:
        """Update enhance mode selection in the webview."""
        self.update_state({"current_enhance_mode": mode_id})

    def _set_element_text(self, element_id: str, value: str) -> None:
        """Set textContent of a DOM element by ID."""
        if self._webview is None or not self.is_visible:
            return
        escaped = json.dumps(value or "", ensure_ascii=False)
        self._webview.evaluateJavaScript_completionHandler_(
            f"document.getElementById({json.dumps(element_id)}).textContent = {escaped};",
            None,
        )

    def update_config_dir(self, path: str) -> None:
        """Update the config directory display."""
        self._set_element_text("config-dir-display", path)

    def update_launcher_hotkey(self, hotkey: str) -> None:
        """Update the launcher hotkey display."""
        self._set_element_text("ctl-launcher-hotkey", hotkey)

    def update_screenshot_hotkey(self, hotkey: str) -> None:
        """Update the screenshot hotkey display."""
        self._set_element_text("ctl-screenshot-hotkey", hotkey or "None")

    def update_source_hotkey(self, source_key: str, hotkey: str) -> None:
        """Update a launcher source hotkey display."""
        if self._webview is None or not self.is_visible:
            return
        escaped = json.dumps(hotkey or "", ensure_ascii=False)
        key_escaped = json.dumps(source_key, ensure_ascii=False)
        js = (
            f'var el = document.querySelector(\'[data-source-hotkey="\' + {key_escaped} + \'"]\');'
            f"if (el) el.textContent = {escaped};"
        )
        self._webview.evaluateJavaScript_completionHandler_(js, None)

    def update_new_snippet_hotkey(self, hotkey: str) -> None:
        """Update the new snippet hotkey display."""
        self._set_element_text("ctl-new-snippet-hotkey", hotkey)

    def update_universal_action_hotkey(self, hotkey: str) -> None:
        """Update the UA hotkey badge in the settings panel."""
        self._set_element_text("ctl-ua-hotkey", hotkey)

    # ------------------------------------------------------------------
    # Callbacks from JavaScript
    # ------------------------------------------------------------------

    def _handle_js_message(self, body: dict) -> None:
        """Dispatch messages from JavaScript."""
        if not self.is_visible:
            return

        msg_type = body.get("type", "")
        logger.debug("Handling JS message: type=%s body=%s", msg_type, body)

        if msg_type == "console":
            level = body.get("level", "info")
            level = level if level in _LOG_LEVELS else "info"
            message = body.get("message", "")
            getattr(logger, level)("[WebView] %s", message)
            return

        if msg_type == "callback":
            name = body.get("name", "")
            args = body.get("args", [])
            logger.debug("JS callback: %s args=%s", name, args)
            if self._callbacks and name in self._callbacks:
                cb = self._callbacks[name]
                try:
                    cb(*args)
                except Exception:
                    logger.exception("Callback %s raised", name)
            else:
                logger.warning("Unknown callback: %s", name)
            return

        logger.warning("Unknown JS message type: %s", msg_type)

    # ------------------------------------------------------------------
    # State preparation (stub for Task 3)
    # ------------------------------------------------------------------

    @staticmethod
    def _prepare_state(state: dict, *, include_i18n: bool = True) -> dict:
        """Convert tuple-based state values to JSON-friendly dicts."""
        s = dict(state)
        if "stt_presets" in s and s["stt_presets"] and isinstance(s["stt_presets"][0], (tuple, list)):
            s["stt_presets"] = [
                {"id": row[0], "name": row[1], "available": row[2]}
                for row in s["stt_presets"]
            ]
        if "stt_remote_models" in s and s["stt_remote_models"] and isinstance(s["stt_remote_models"][0], (tuple, list)):
            s["stt_remote_models"] = [
                {"provider": row[0], "model": row[1], "display": row[2]}
                for row in s["stt_remote_models"]
            ]
        if "llm_models" in s and s["llm_models"] and isinstance(s["llm_models"][0], (tuple, list)):
            s["llm_models"] = [
                {
                    "provider": row[0],
                    "model": row[1],
                    "display": row[2],
                    "has_api_key": row[3] if len(row) > 3 else False,
                }
                for row in s["llm_models"]
            ]
        if "current_llm" in s and isinstance(s["current_llm"], (tuple, list)):
            s["current_llm"] = {
                "provider": s["current_llm"][0],
                "model": s["current_llm"][1],
            }
        if "enhance_modes" in s and s["enhance_modes"] and isinstance(s["enhance_modes"][0], (tuple, list)):
            s["enhance_modes"] = [
                {"id": row[0], "name": row[1], "order": row[2]}
                for row in s["enhance_modes"]
            ]
        if s.get("last_tab") == "models":
            s["last_tab"] = "speech"
        # Convert current_remote_asr tuple to list for JSON
        if "current_remote_asr" in s and isinstance(s["current_remote_asr"], tuple):
            s["current_remote_asr"] = list(s["current_remote_asr"])
        # Convert hotkeys from raw config to structured dicts for JS
        if "hotkeys" in s:
            raw = s["hotkeys"]
            structured = {}
            for key_name, value in raw.items():
                enabled = bool(value)
                mode = value.get("mode") if isinstance(value, dict) else None
                structured[key_name] = {
                    "enabled": enabled,
                    "mode": mode,
                    "label": key_name,
                }
            s["hotkeys"] = structured
        # Convert vocab_build_model tuple to "provider/model" string
        if "vocab_build_model" in s:
            vbm = s["vocab_build_model"]
            if isinstance(vbm, (tuple, list)) and len(vbm) == 2:
                s["vocab_build_model"] = f"{vbm[0]}/{vbm[1]}"
            elif vbm is None:
                s["vocab_build_model"] = ""
        # Inject i18n translations
        if include_i18n and "i18n" not in s:
            try:
                from wenzi.i18n import get_translations_for_prefix
                s["i18n"] = get_translations_for_prefix("settings.")
            except Exception:
                s["i18n"] = {}
        return s

    # ------------------------------------------------------------------
    # Panel construction
    # ------------------------------------------------------------------

    def _build_panel(self, state: dict) -> None:
        """Build NSPanel + WKWebView and load the HTML template."""
        from AppKit import (
            NSApp,
            NSBackingStoreBuffered,
            NSClosableWindowMask,
            NSPanel,
            NSResizableWindowMask,
            NSScreen,
            NSTitledWindowMask,
        )
        from Foundation import NSMakeRect
        from WebKit import (
            WKUserContentController,
            WKUserScript,
            WKWebView,
        )

        from wenzi.ui.result_window_web import _ensure_edit_menu

        # Enable Cmd+C/V/A via Edit menu in the responder chain
        _ensure_edit_menu()

        NSApp.setActivationPolicy_(0)  # Regular (foreground)

        if self._panel is not None:
            self.update_state(state)
            return

        # Create NSPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
            NSTitledWindowMask | NSClosableWindowMask | NSResizableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(0)  # NSNormalWindowLevel
        panel.setFloatingPanel_(False)
        panel.setHidesOnDeactivate_(False)
        panel.setTitle_("Settings")

        # Close delegate
        delegate_cls = _get_panel_close_delegate_class()
        delegate = delegate_cls.alloc().init()
        delegate._panel_ref = self
        panel.setDelegate_(delegate)
        self._close_delegate = delegate

        # WKWebView with message handler and bridge script
        from wenzi.ui.web_utils import lightweight_webview_config

        config = lightweight_webview_config(shared=False)
        content_controller = WKUserContentController.alloc().init()

        handler_cls = _get_message_handler_class()
        handler = handler_cls.alloc().init()
        handler._panel_ref = self
        content_controller.addScriptMessageHandler_name_(handler, "wz")

        # Inject bridge JS at document start
        bridge_script = WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
            _BRIDGE_JS,
            0,  # WKUserScriptInjectionTimeAtDocumentStart
            True,
        )
        content_controller.addUserScript_(bridge_script)

        config.setUserContentController_(content_controller)

        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
            config,
        )
        webview.setAutoresizingMask_(0x12)  # width + height sizable
        webview.setValue_forKey_(False, "drawsBackground")
        panel.contentView().addSubview_(webview)

        self._panel = panel
        self._webview = webview
        self._message_handler = handler

        # Center on screen
        screen = NSScreen.mainScreen()
        if screen:
            sf = screen.visibleFrame()
            pf = panel.frame()
            x = sf.origin.x + (sf.size.width - pf.size.width) / 2
            y = sf.origin.y + (sf.size.height - pf.size.height) / 2
            panel.setFrameOrigin_((x, y))
        else:
            panel.center()

        self._load_html(state)

    def _load_html(self, state: dict) -> None:
        """Load the HTML template with the given state into the webview."""
        from Foundation import NSURL

        config_data = self._prepare_state(state)
        html_content = load_template(
            "settings_window_web.html",
            CONFIG=json.dumps(config_data, ensure_ascii=False),
            MARKED_JS=load_template("vendor/marked.min.js"),
        )
        self._webview.loadHTMLString_baseURL_(
            html_content, NSURL.fileURLWithPath_("/")
        )
