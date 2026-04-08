"""Floating overlay panel for Direct mode streaming AI enhancement output.

Uses WKWebView for rendering with automatic dark mode support via CSS.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Optional

from wenzi.ui.templates import load_template

logger = logging.getLogger(__name__)

# Panel dimensions
_PANEL_WIDTH = 400
_PANEL_HEIGHT = 200

# Layout constants
_CORNER_RADIUS = 10
_SCREEN_MARGIN = 20

# Key codes
_ESC_KEY_CODE = 53
_RETURN_KEY_CODE = 36

# Delayed close
_CLOSE_DELAY = 1.0
_HOVER_RECHECK_INTERVAL = 0.5
_FADE_OUT_DURATION = 0.3

# ---------------------------------------------------------------------------
# WKNavigationDelegate (lazy-created)
# ---------------------------------------------------------------------------
_OverlayNavDelegate = None


def _get_nav_delegate_class():
    global _OverlayNavDelegate
    if _OverlayNavDelegate is None:
        import objc
        from Foundation import NSObject

        import WebKit  # noqa: F401

        WKNavigationDelegate = objc.protocolNamed("WKNavigationDelegate")

        class StreamingOverlayNavDelegate(
            NSObject, protocols=[WKNavigationDelegate]
        ):
            _panel_ref = None

            def webView_didFinishNavigation_(self, webview, navigation):
                if self._panel_ref is not None:
                    self._panel_ref._on_page_loaded()

        _OverlayNavDelegate = StreamingOverlayNavDelegate
    return _OverlayNavDelegate


class StreamingOverlayPanel:
    """Non-interactive floating overlay that displays streaming AI enhancement.

    Shows ASR original text at top, streaming enhanced text below.
    Uses WKWebView for rendering with automatic dark mode support.
    Does not steal focus or accept mouse events.
    """

    def __init__(self) -> None:
        self._panel: object = None
        self._webview: object = None
        self._nav_delegate: object = None
        self._tap_runner: object = None
        self._cancel_event: Optional[threading.Event] = None
        self._on_cancel: object = None
        self._on_confirm_asr: object = None
        self._loading_timer: object = None
        self._loading_seconds: int = 0
        self._llm_info: str = ""
        self._close_timer: object = None
        self._page_loaded: bool = False
        self._pending_js: list[str] = []

    # ------------------------------------------------------------------
    # JavaScript bridge
    # ------------------------------------------------------------------

    def _eval_js(self, js_code: str) -> None:
        """Evaluate JS in the webview. Queues if page not loaded yet."""
        if not self._page_loaded:
            self._pending_js.append(js_code)
            return
        if self._webview is not None:
            self._webview.evaluateJavaScript_completionHandler_(js_code, None)

    def _on_page_loaded(self) -> None:
        """Called by navigation delegate when HTML finishes loading."""
        pending = self._pending_js[:]
        self._pending_js.clear()
        self._page_loaded = True
        if pending and self._webview is not None:
            combined = ";".join(pending)
            self._webview.evaluateJavaScript_completionHandler_(combined, None)

    # ------------------------------------------------------------------
    # Show / position
    # ------------------------------------------------------------------

    def _ai_label(self, suffix: str) -> str:
        """Build the AI status label with optional LLM info prefix."""
        base = "\u2728 AI"
        if self._llm_info:
            base += f" ({self._llm_info})"
        if suffix:
            return f"{base}  {suffix}"
        return base

    def show(
        self,
        asr_text: str = "",
        cancel_event: Optional[threading.Event] = None,
        animate_from_frame: object = None,
        stt_info: str = "",
        llm_info: str = "",
        on_cancel: object = None,
        on_confirm_asr: object = None,
    ) -> None:
        """Create and show the overlay panel. Must be called on main thread.

        Args:
            on_cancel: Optional callback invoked when ESC is pressed.
            on_confirm_asr: Optional callback invoked when Enter is pressed
                (skip enhancement, output ASR text directly).
        """
        try:
            from AppKit import NSColor, NSPanel, NSScreen, NSStatusWindowLevel
            from Foundation import NSMakeRect, NSURL
            from WebKit import WKWebView

            if self._panel is not None:
                self._do_close()

            self._cancel_event = cancel_event
            self._on_cancel = on_cancel
            self._on_confirm_asr = on_confirm_asr
            self._loading_seconds = 0
            self._llm_info = llm_info
            self._page_loaded = False
            self._pending_js.clear()

            # Create borderless panel
            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, _PANEL_WIDTH, _PANEL_HEIGHT),
                0,  # NSBorderlessWindowMask
                2,  # NSBackingStoreBuffered
                False,
            )
            panel.setLevel_(NSStatusWindowLevel + 1)
            panel.setOpaque_(False)
            panel.setBackgroundColor_(NSColor.clearColor())
            panel.setHasShadow_(True)
            panel.setHidesOnDeactivate_(False)
            panel.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 8))  # canJoinAllSpaces | stationary | fullScreenAuxiliary

            # WKWebView
            from wenzi.ui.web_utils import lightweight_webview_config

            config = lightweight_webview_config()
            webview = WKWebView.alloc().initWithFrame_configuration_(
                NSMakeRect(0, 0, _PANEL_WIDTH, _PANEL_HEIGHT),
                config,
            )
            webview.setAutoresizingMask_(0x12)
            webview.setValue_forKey_(False, "drawsBackground")
            panel.contentView().addSubview_(webview)

            # Navigation delegate for page-load callback
            nav_cls = _get_nav_delegate_class()
            nav_delegate = nav_cls.alloc().init()
            nav_delegate._panel_ref = self
            webview.setNavigationDelegate_(nav_delegate)

            self._panel = panel
            self._webview = webview
            self._nav_delegate = nav_delegate

            # Build config and load HTML
            ai_base = "\u2728 AI"
            if llm_info:
                ai_base += f" ({llm_info})"

            asr_title = "\U0001f3a4 ASR"
            if stt_info:
                asr_title += f"  ({stt_info})"

            config_data = {
                "asrTitle": asr_title,
                "asrText": asr_text,
                "statusText": ai_base,
                "aiBase": ai_base,
            }
            html = load_template(
                "streaming_overlay.html",
                CONFIG=json.dumps(config_data, ensure_ascii=False),
                RADIUS=str(_CORNER_RADIUS),
            )
            webview.loadHTMLString_baseURL_(
                html, NSURL.fileURLWithPath_("/")
            )

            # Position at bottom-right
            screen = NSScreen.mainScreen()
            target_x, target_y = 0, 0
            if screen:
                sf = screen.visibleFrame()
                target_x = sf.origin.x + sf.size.width - _PANEL_WIDTH - _SCREEN_MARGIN
                target_y = sf.origin.y + _SCREEN_MARGIN

            if animate_from_frame is not None:
                from AppKit import NSAnimationContext

                panel.setFrame_display_(animate_from_frame, False)
                panel.setAlphaValue_(0.0)
                panel.orderFront_(None)

                target_frame = NSMakeRect(
                    target_x, target_y, _PANEL_WIDTH, _PANEL_HEIGHT
                )
                NSAnimationContext.beginGrouping()
                ctx = NSAnimationContext.currentContext()
                ctx.setDuration_(0.3)
                panel.animator().setFrame_display_(target_frame, True)
                panel.animator().setAlphaValue_(1.0)
                NSAnimationContext.endGrouping()
            else:
                panel.setFrameOrigin_((target_x, target_y))
                panel.orderFront_(None)

            # Register global ESC key monitor
            self._register_key_tap()

            # Start loading timer
            self._start_loading_timer()

            logger.debug("Streaming overlay shown")
        except Exception:
            logger.error("Failed to show streaming overlay", exc_info=True)

    # ------------------------------------------------------------------
    # Key tap (ESC / Enter) — CGEventTap swallows the keys
    # ------------------------------------------------------------------

    def _register_key_tap(self) -> None:
        """Create a CGEventTap that intercepts and swallows ESC / Enter."""
        if self._tap_runner is not None:
            return
        from wenzi import _cgeventtap as cg

        self._tap_runner = cg.CGEventTapRunner()
        mask = cg.CGEventMaskBit(cg.kCGEventKeyDown)
        self._tap_runner.start(mask, self._key_tap_callback)

    def _key_tap_callback(self, proxy, event_type, event, refcon):
        """CGEventTap callback — runs on the tap's background thread."""
        from wenzi import _cgeventtap as cg

        try:
            if event_type == cg.kCGEventTapDisabledByTimeout:
                if self._tap_runner is not None and self._tap_runner.tap is not None:
                    cg.CGEventTapEnable(self._tap_runner.tap, True)
                return event

            if event_type != cg.kCGEventKeyDown:
                return event

            keycode = cg.CGEventGetIntegerValueField(
                event, cg.kCGKeyboardEventKeycode,
            )

            if keycode == _ESC_KEY_CODE:
                # Disable tap to prevent auto-repeat queueing
                if self._tap_runner is not None and self._tap_runner.tap is not None:
                    cg.CGEventTapEnable(self._tap_runner.tap, False)
                if self._cancel_event is not None:
                    self._cancel_event.set()
                from PyObjCTools import AppHelper

                # Marshal callbacks to the main thread — this callback
                # runs on the CGEventTap background thread.
                on_cancel = self._on_cancel
                if on_cancel is not None:
                    AppHelper.callAfter(on_cancel)
                AppHelper.callAfter(self._do_close)
                logger.info("Streaming cancelled via ESC key")
                return None  # swallow

            if keycode == _RETURN_KEY_CODE and self._on_confirm_asr is not None:
                # Disable tap to prevent auto-repeat queueing
                if self._tap_runner is not None and self._tap_runner.tap is not None:
                    cg.CGEventTapEnable(self._tap_runner.tap, False)
                from PyObjCTools import AppHelper

                on_confirm = self._on_confirm_asr
                AppHelper.callAfter(on_confirm)
                logger.info("ASR confirmed via Enter key")
                return None  # swallow

        except Exception:
            logger.warning("Key tap callback error", exc_info=True)
        return event

    def _remove_key_tap(self) -> None:
        """Disable and remove the CGEventTap."""
        if self._tap_runner is not None:
            self._tap_runner.stop()
            self._tap_runner = None

    # ------------------------------------------------------------------
    # Loading timer (elapsed seconds while waiting for first chunk)
    # ------------------------------------------------------------------

    def _start_loading_timer(self) -> None:
        """Start a 1-second repeating timer that updates the status label."""
        self._stop_loading_timer()
        self._loading_seconds = 0
        try:
            from Foundation import NSTimer

            self._loading_timer = (
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    1.0, self, b"tickLoadingTimer:", None, True,
                )
            )
        except Exception:
            logger.error("Failed to start loading timer", exc_info=True)

    def _stop_loading_timer(self) -> None:
        """Stop the loading timer if running."""
        if self._loading_timer is not None:
            try:
                self._loading_timer.invalidate()
            except Exception:
                pass
            self._loading_timer = None

    def tickLoadingTimer_(self, timer) -> None:
        """NSTimer callback: update status label with elapsed seconds."""
        self._loading_seconds += 1
        self._eval_js(
            f"setStatus({json.dumps(self._ai_label(f'⏳ {self._loading_seconds}s'))})"
        )

    # ------------------------------------------------------------------
    # Streaming text updates (all thread-safe via callAfter)
    # ------------------------------------------------------------------

    def append_text(self, chunk: str, completion_tokens: int = 0) -> None:
        """Append content text to the streaming text view. Thread-safe."""
        from PyObjCTools import AppHelper

        def _append():
            self._stop_loading_timer()
            if self._webview is None:
                return
            self._eval_js(
                f"appendText({json.dumps(chunk)},{completion_tokens})"
            )

        AppHelper.callAfter(_append)

    def append_thinking_text(self, chunk: str, thinking_tokens: int = 0) -> None:
        """Append thinking/reasoning text in italic secondary color. Thread-safe."""
        from PyObjCTools import AppHelper

        def _append():
            self._stop_loading_timer()
            if self._webview is None:
                return
            self._eval_js(
                f"appendThinkingText({json.dumps(chunk)},{thinking_tokens})"
            )

        AppHelper.callAfter(_append)

    def set_status(self, text: str) -> None:
        """Update the status label. Thread-safe."""
        from PyObjCTools import AppHelper

        def _update():
            if self._webview is None:
                return
            self._eval_js(f"setStatus({json.dumps(text)})")

        AppHelper.callAfter(_update)

    def set_asr_text(self, text: str) -> None:
        """Update the ASR label text after transcription completes. Thread-safe."""
        from PyObjCTools import AppHelper

        def _update():
            if self._webview is None:
                return
            self._eval_js(f"setAsrText({json.dumps(text)})")

        AppHelper.callAfter(_update)

    def set_cancel_event(self, cancel_event: threading.Event) -> None:
        """Attach a cancel event and register ESC monitor. Thread-safe."""
        from PyObjCTools import AppHelper

        def _update():
            self._cancel_event = cancel_event
            if self._tap_runner is None:
                self._register_key_tap()

        AppHelper.callAfter(_update)

    def set_complete(self, usage: dict | None = None) -> None:
        """Mark enhancement complete, show final token usage. Thread-safe."""
        from PyObjCTools import AppHelper

        def _update():
            self._stop_loading_timer()
            if self._webview is None:
                return
            self._eval_js(
                f"setComplete({json.dumps(usage) if usage else 'null'})"
            )

        AppHelper.callAfter(_update)

    def clear_text(self) -> None:
        """Clear the streaming text view. Thread-safe."""
        from PyObjCTools import AppHelper

        def _clear():
            if self._webview is None:
                return
            self._eval_js("clearText()")

        AppHelper.callAfter(_clear)

    # ------------------------------------------------------------------
    # Delayed close with hover detection
    # ------------------------------------------------------------------

    def close_with_delay(self, delay: float = _CLOSE_DELAY) -> None:
        """Close the overlay after *delay* seconds, with fade-out animation.

        If the mouse cursor is hovering over the panel when the timer fires,
        the close is postponed until the cursor leaves. Thread-safe.
        """
        from PyObjCTools import AppHelper

        def _schedule():
            self._stop_close_timer()
            try:
                from Foundation import NSTimer

                self._close_timer = (
                    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                        delay, self, b"_delayedCloseCheck:", None, False,
                    )
                )
            except Exception:
                logger.error("Failed to schedule delayed close", exc_info=True)

        AppHelper.callAfter(_schedule)

    def _delayedCloseCheck_(self, timer) -> None:
        """NSTimer callback: fade out if mouse is not hovering, else recheck."""
        self._close_timer = None
        if self._panel is None:
            return

        if self._is_mouse_over_panel():
            try:
                from Foundation import NSTimer

                self._close_timer = (
                    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                        _HOVER_RECHECK_INTERVAL, self, b"_delayedCloseCheck:", None, False,
                    )
                )
            except Exception:
                self._do_close()
        else:
            self._fade_out_and_close()

    def _is_mouse_over_panel(self) -> bool:
        """Return True if the mouse cursor is inside the panel frame."""
        try:
            from AppKit import NSEvent
            from Foundation import NSPointInRect

            mouse_loc = NSEvent.mouseLocation()
            return bool(NSPointInRect(mouse_loc, self._panel.frame()))
        except Exception:
            return False

    def _fade_out_and_close(self) -> None:
        """Animate the panel to transparent, then clean up."""
        if self._panel is None:
            return
        try:
            from AppKit import NSAnimationContext

            NSAnimationContext.beginGrouping()
            ctx = NSAnimationContext.currentContext()
            ctx.setDuration_(_FADE_OUT_DURATION)
            ctx.setCompletionHandler_(self._do_close)
            self._panel.animator().setAlphaValue_(0.0)
            NSAnimationContext.endGrouping()
        except Exception:
            self._do_close()

    def _stop_close_timer(self) -> None:
        """Cancel any pending delayed-close timer."""
        if self._close_timer is not None:
            try:
                self._close_timer.invalidate()
            except Exception:
                pass
            self._close_timer = None

    def _do_close(self) -> None:
        """Immediate cleanup — shared by close() and fade-out completion."""
        self._stop_loading_timer()
        self._stop_close_timer()
        self._remove_key_tap()
        self._cancel_event = None
        self._on_cancel = None
        self._on_confirm_asr = None
        self._page_loaded = False
        self._pending_js.clear()

        if self._panel is not None:
            self._panel.orderOut_(None)
            self._panel = None

        try:
            if self._webview is not None:
                self._webview.setNavigationDelegate_(None)
                self._webview.stopLoading_(None)
                self._webview.loadHTMLString_baseURL_("", None)
            if self._nav_delegate is not None:
                self._nav_delegate._panel_ref = None
        except Exception:
            logger.debug("Error clearing delegate refs", exc_info=True)
        self._webview = None
        self._nav_delegate = None
        logger.debug("Streaming overlay closed")

    def close_now(self) -> None:
        """Close and clean up the overlay panel. Must be on main thread."""
        self._do_close()

    def close(self) -> None:
        """Close and clean up the overlay panel immediately. Thread-safe."""
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._do_close)
