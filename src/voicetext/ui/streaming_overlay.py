"""Floating overlay panel for Direct mode streaming AI enhancement output.

Uses WKWebView for rendering with automatic dark mode support via CSS.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Panel dimensions
_PANEL_WIDTH = 400
_PANEL_HEIGHT = 200

# Layout constants
_CORNER_RADIUS = 10
_SCREEN_MARGIN = 20

# ESC key code
_ESC_KEY_CODE = 53

# Delayed close
_CLOSE_DELAY = 1.0
_HOVER_RECHECK_INTERVAL = 0.5
_FADE_OUT_DURATION = 0.3

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
:root {
    --bg: rgba(245, 245, 245, 0.92);
    --text: #1d1d1f;
    --secondary: #6e6e73;
    --border: rgba(0, 0, 0, 0.12);
    --thinking: #86868b;
}
@media (prefers-color-scheme: dark) {
    :root {
        --bg: rgba(38, 38, 38, 0.92);
        --text: #f0f0f0;
        --secondary: #a0a0a5;
        --border: rgba(255, 255, 255, 0.15);
        --thinking: #8e8e93;
        }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
    height: 100%;
    background: transparent;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
    font-size: 13px;
    color: var(--text);
    -webkit-user-select: none; user-select: none;
}
body {
    background: var(--bg);
    border-radius: __RADIUS__px;
    overflow: hidden;
    display: flex; flex-direction: column;
    padding: 10px 12px;
}
#asr-section {
    flex: 1; min-height: 0;
    display: flex; flex-direction: column;
    margin-bottom: 4px;
}
#asr-title {
    font-weight: 600; font-size: 11px; color: var(--secondary);
    margin-bottom: 2px; flex-shrink: 0;
}
#asr-text {
    flex: 1; min-height: 0;
    font-size: 12px; color: var(--secondary);
    line-height: 1.4;
    overflow-y: auto; scrollbar-width: none;
    -webkit-user-select: text; user-select: text;
    cursor: text;
}
#asr-text::-webkit-scrollbar { display: none; }
#asr-text:empty::after {
    content: "Transcribing...";
    animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse { 0%,100%{opacity:.4} 50%{opacity:1} }
#ai-section {
    flex: 1; min-height: 0;
    display: flex; flex-direction: column;
    padding-top: 4px;
}
#status {
    font-weight: 600; font-size: 11px;
    margin-bottom: 2px; flex-shrink: 0;
}
#stream-area {
    flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden;
    font-size: 13px; line-height: 1.4;
    scrollbar-width: none;
    -webkit-user-select: text; user-select: text;
    cursor: text;
}
#stream-area::-webkit-scrollbar { display: none; }
.thinking { color: var(--thinking); font-style: italic; }
</style>
</head>
<body>
<div id="asr-section">
  <div id="asr-title"></div>
  <div id="asr-text"></div>
</div>
<div id="ai-section">
  <div id="status"></div>
  <div id="stream-area"></div>
</div>
<script>
const C = __CONFIG__;
document.getElementById('asr-title').textContent = C.asrTitle;
document.getElementById('asr-text').textContent = C.asrText;
document.getElementById('status').textContent = C.statusText;

function setAsrText(t) { document.getElementById('asr-text').textContent = t; }
function setStatus(t) { document.getElementById('status').textContent = t; }
function setStatusHtml(h) { document.getElementById('status').innerHTML = h; }

function appendText(chunk, tokens) {
    const el = document.getElementById('stream-area');
    // If there was thinking content, clear it for real content
    if (el._hasThinking) { el.innerHTML = ''; el._hasThinking = false; }
    el.appendChild(document.createTextNode(chunk));
    el.scrollTop = el.scrollHeight;
    if (tokens > 0) setStatus(C.aiBase + '  Tokens: \u2193' + tokens.toLocaleString());
}

function appendThinkingText(chunk, tokens) {
    const el = document.getElementById('stream-area');
    el._hasThinking = true;
    const span = document.createElement('span');
    span.className = 'thinking';
    span.textContent = chunk;
    el.appendChild(span);
    el.scrollTop = el.scrollHeight;
    if (tokens > 0) setStatus(C.aiBase + '  \u25b6 Thinking: ' + tokens.toLocaleString());
}

function clearText() {
    const el = document.getElementById('stream-area');
    el.innerHTML = '';
    el._hasThinking = false;
}

function setComplete(usage) {
    if (usage && usage.total_tokens) {
        var prompt = usage.prompt_tokens||0, comp = usage.completion_tokens||0;
        var cached = usage.cache_read_tokens||0;
        var pp = cached
            ? '<span style="opacity:0.5">\u2191' + cached.toLocaleString() + '</span>+' + (prompt-cached).toLocaleString()
            : '\u2191' + prompt.toLocaleString();
        var safe = C.aiBase.replace(/&/g,'&amp;').replace(/</g,'&lt;');
        setStatusHtml(safe + '&ensp;Tokens: ' + usage.total_tokens.toLocaleString()
            + ' (' + pp + ' \u2193' + comp.toLocaleString() + ')');
    } else {
        setStatus(C.aiBase);
    }
}
</script>
</body>
</html>"""

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
        self._esc_monitor: object = None
        self._cancel_event: Optional[threading.Event] = None
        self._on_cancel: object = None
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
    ) -> None:
        """Create and show the overlay panel. Must be called on main thread.

        Args:
            on_cancel: Optional callback invoked immediately when ESC is
                pressed, before the overlay closes. Runs on the main thread.
        """
        try:
            from AppKit import NSColor, NSPanel, NSScreen, NSStatusWindowLevel
            from Foundation import NSMakeRect, NSURL
            from WebKit import WKWebView, WKWebViewConfiguration

            if self._panel is not None:
                self._do_close()

            self._cancel_event = cancel_event
            self._on_cancel = on_cancel
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
            panel.setCollectionBehavior_(1 << 4)  # canJoinAllSpaces

            # WKWebView
            config = WKWebViewConfiguration.alloc().init()
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
            html = _HTML_TEMPLATE.replace(
                "__CONFIG__", json.dumps(config_data, ensure_ascii=False)
            ).replace("__RADIUS__", str(_CORNER_RADIUS))
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
            self._register_esc_monitor()

            # Start loading timer
            self._start_loading_timer()

            logger.debug("Streaming overlay shown")
        except Exception:
            logger.error("Failed to show streaming overlay", exc_info=True)

    # ------------------------------------------------------------------
    # ESC key monitor
    # ------------------------------------------------------------------

    def _register_esc_monitor(self) -> None:
        """Register a global key event monitor for ESC key."""
        try:
            from AppKit import NSEvent

            NSKeyDownMask = 1 << 10

            def _handler(event):
                if event.keyCode() == _ESC_KEY_CODE:
                    if self._cancel_event is not None:
                        self._cancel_event.set()
                    if self._on_cancel is not None:
                        try:
                            self._on_cancel()
                        except Exception:
                            logger.error("on_cancel callback failed", exc_info=True)
                    self.close()
                    logger.info("Streaming cancelled via ESC key")

            self._esc_monitor = (
                NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                    NSKeyDownMask, _handler
                )
            )
        except Exception:
            logger.error("Failed to register ESC monitor", exc_info=True)

    def _remove_esc_monitor(self) -> None:
        """Remove the global ESC key monitor."""
        if self._esc_monitor is not None:
            try:
                from AppKit import NSEvent

                NSEvent.removeMonitor_(self._esc_monitor)
            except Exception:
                logger.error("Failed to remove ESC monitor", exc_info=True)
            self._esc_monitor = None

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
            if self._esc_monitor is None:
                self._register_esc_monitor()

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
        self._remove_esc_monitor()
        self._cancel_event = None
        self._on_cancel = None
        self._page_loaded = False
        self._pending_js.clear()

        if self._panel is not None:
            self._panel.orderOut_(None)
            self._panel = None

        self._webview = None
        self._nav_delegate = None
        logger.debug("Streaming overlay closed")

    def close(self) -> None:
        """Close and clean up the overlay panel immediately. Thread-safe."""
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._do_close)
