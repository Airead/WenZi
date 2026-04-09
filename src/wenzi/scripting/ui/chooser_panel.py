"""Chooser panel — Alfred/Raycast-style quick launcher.

Uses NSPanel + WKWebView for a search-and-filter UI.
Keyboard-driven: type to filter, ↑↓ to navigate, Enter to execute.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from typing import NamedTuple

from wenzi.i18n import t
from wenzi.scripting.sources import ChooserItem, ChooserSource, fuzzy_match
from wenzi.ui_helpers import get_frontmost_app, reactivate_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WKScriptMessageHandler (lazy-created to avoid PyObjC import at module level)
# ---------------------------------------------------------------------------
_MessageHandler = None


def _get_message_handler_class():
    global _MessageHandler
    if _MessageHandler is not None:
        return _MessageHandler

    import objc
    import WebKit  # noqa: F401
    from Foundation import NSObject

    WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")

    class ChooserMessageHandler(NSObject, protocols=[WKScriptMessageHandler]):
        _panel_ref = None

        def userContentController_didReceiveScriptMessage_(self, controller, message):
            if self._panel_ref is None:
                return
            raw = message.body()
            try:
                from Foundation import NSJSONSerialization

                json_data, _ = NSJSONSerialization.dataWithJSONObject_options_error_(raw, 0, None)
                body = json.loads(bytes(json_data))
            except Exception:
                logger.warning("Cannot convert chooser message: %r", raw)
                return
            self._panel_ref._handle_js_message(body)

    _MessageHandler = ChooserMessageHandler
    return _MessageHandler


# ---------------------------------------------------------------------------
# WKNavigationDelegate (lazy-created)
# ---------------------------------------------------------------------------
_NavigationDelegate = None


def _get_navigation_delegate_class():
    global _NavigationDelegate
    if _NavigationDelegate is not None:
        return _NavigationDelegate

    import objc
    import WebKit  # noqa: F401
    from Foundation import NSObject

    WKNavigationDelegate = objc.protocolNamed("WKNavigationDelegate")

    class ChooserNavigationDelegate(NSObject, protocols=[WKNavigationDelegate]):
        _panel_ref = None

        def webView_didFinishNavigation_(self, webview, navigation):
            if self._panel_ref is not None:
                self._panel_ref._on_page_loaded()

    _NavigationDelegate = ChooserNavigationDelegate
    return _NavigationDelegate


# ---------------------------------------------------------------------------
# Borderless key-capable NSPanel subclass (lazy-created)
# ---------------------------------------------------------------------------
_KeyablePanel = None


def _get_keyable_panel_class():
    """Return an NSPanel subclass that can become key window when borderless."""
    global _KeyablePanel
    if _KeyablePanel is not None:
        return _KeyablePanel

    from AppKit import NSPanel

    class ChooserKeyablePanel(NSPanel):
        def canBecomeKeyWindow(self):
            return True

    _KeyablePanel = ChooserKeyablePanel
    return _KeyablePanel


# ---------------------------------------------------------------------------
# Panel delegate for resign-key (lazy-created)
# ---------------------------------------------------------------------------
_PanelDelegate = None


def _get_panel_delegate_class():
    """Return an NSObject subclass that closes the panel on focus loss.

    Uses a deferred check so the chooser stays open when the user clicks
    on the Quick Look preview panel (which becomes key window).
    """
    global _PanelDelegate
    if _PanelDelegate is not None:
        return _PanelDelegate

    from Foundation import NSObject

    class ChooserPanelDelegate(NSObject):
        _panel_ref = None

        def windowDidResignKey_(self, notification):
            if self._panel_ref is not None:
                self._panel_ref._maybe_close()

        def windowDidBecomeKey_(self, notification):
            if self._panel_ref is not None:
                self._panel_ref._exit_calc_mode()

    _PanelDelegate = ChooserPanelDelegate
    return _PanelDelegate


# ---------------------------------------------------------------------------
# Debounce timer helper (lazy-created)
# ---------------------------------------------------------------------------
_DebounceTimerHelper = None


def _get_debounce_timer_helper_class():
    """Return an NSObject subclass for NSTimer callbacks."""
    global _DebounceTimerHelper
    if _DebounceTimerHelper is not None:
        return _DebounceTimerHelper

    from Foundation import NSObject

    class ChooserDebounceTimerHelper(NSObject):
        _callback = None

        def fire_(self, _timer):
            if self._callback is not None:
                self._callback()

    _DebounceTimerHelper = ChooserDebounceTimerHelper
    return _DebounceTimerHelper


class _DebounceEntry(NamedTuple):
    """Per-source debounce state: timer, helper ref, and search args."""
    timer: object  # NSTimer
    helper: object  # NSObject helper (prevents GC)
    source: ChooserSource
    query: str
    generation: int


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

_ESC_KEYCODE = 53


class ChooserPanel:
    """Alfred/Raycast-style search launcher panel.

    Manages an NSPanel with WKWebView, dispatches search queries to
    registered ChooserSource instances, and executes item actions.
    """

    _INITIAL_WIDTH = 680
    _INITIAL_HEIGHT = 49  # bootstrap; JS updates after page load
    _MAX_TOTAL_RESULTS = 50
    _DEFERRED_ACTION_DELAY = 0.15  # seconds to let previous app regain focus
    _DEFAULT_ASYNC_DEBOUNCE = 0.15  # seconds
    _DEFAULT_ASYNC_TIMEOUT = 5.0  # seconds
    _UA_USAGE_PREFIX = "_ua"  # Synthetic query prefix for UA mode usage tracking
    _RECYCLE_DELAY = 60.0  # seconds before recycling idle webview

    def __init__(self, usage_tracker=None) -> None:
        self._panel = None
        self._webview = None
        self._message_handler = None
        self._navigation_delegate = None
        self._panel_delegate = None
        self._page_loaded: bool = False
        self._pending_js: list[str] = []

        self._sources: dict[str, ChooserSource] = {}
        self._current_items: list[ChooserItem] = []
        self._items_version: int = 0  # incremented on every setResults push
        self._closing: bool = False
        self._last_query: str = ""  # Track query for usage recording

        self._usage_tracker = usage_tracker
        self._query_history = None
        self._history_index: int = -1
        self._on_close: Callable | None = None
        self._pending_initial_query: str | None = None
        self._pending_placeholder: str | None = None
        self._event_callback: Callable | None = None  # (event, *args)
        self._snippet_expander = None  # SnippetExpander to suppress on show
        self._previous_app = None  # NSRunningApplication saved on show()
        self._ql_panel = None  # Quick Look preview panel
        self._calc_mode: bool = False  # Calculator pin mode
        self._calc_sticky: bool = False  # Sticky: keep pinned for incomplete expressions
        self._esc_runner = None  # CGEventTapRunner for global ESC
        self._show_preview: bool = False
        self._compact_results: bool = False
        self._switch_english: bool = True
        self._saved_input_source: str | None = None
        self._active_source: ChooserSource | None = None  # currently prefix-activated source
        self._context_text: str | None = None  # Universal Action context
        self._exclusive_source: str | None = None  # Source name to search exclusively (UA mode)
        self._search_generation: int = 0
        self._pending_async_count: int = 0
        self._loading_visible: bool = False
        self._debounce_state: dict[str, _DebounceEntry] = {}  # source_name -> pending debounce
        self._recycle_timer = None  # deferred webview recycle timer
        self._last_screen = None  # last screen the panel was positioned on

    # ------------------------------------------------------------------
    # Panel resize (driven by JS)
    # ------------------------------------------------------------------

    def _apply_frame(self, width: int, height: int) -> None:
        """Resize the panel to the given dimensions (from JS)."""
        if self._panel is None:
            return
        from AppKit import NSScreen
        from Foundation import NSMakeRect

        # Clamp to screen bounds to prevent unbounded growth.
        screen = NSScreen.mainScreen()
        if screen:
            sf = screen.frame()
            width = min(width, int(sf.size.width))
            height = min(height, int(sf.size.height))

        old = self._panel.frame()
        if round(old.size.width) == width and round(old.size.height) == height:
            return
        # Keep the top edge fixed (macOS coords: origin is bottom-left)
        new_y = old.origin.y + old.size.height - height
        # Keep horizontally centered
        new_x = old.origin.x + (old.size.width - width) / 2
        new_frame = NSMakeRect(new_x, new_y, width, height)
        self._panel.setFrame_display_(new_frame, True)

    def _drag_panel(self, dx: int, dy: int) -> None:
        """Move the panel by (dx, dy) screen points (from JS drag)."""
        if self._panel is None:
            return
        from Foundation import NSMakeRect

        old = self._panel.frame()
        # macOS y-axis is flipped vs screen coords: up is positive
        new_frame = NSMakeRect(
            old.origin.x + dx,
            old.origin.y - dy,
            old.size.width,
            old.size.height,
        )
        self._panel.setFrame_display_(new_frame, True)
        # Clear cached screen so next show() always resets position
        self._last_screen = None

    def _position_on_mouse_screen(self) -> None:
        """Position panel centered-top on the screen containing the mouse.

        Skips repositioning if the mouse is still on the same screen as
        the last call, avoiding unnecessary frame changes.
        """
        if self._panel is None:
            return
        from AppKit import NSEvent, NSScreen

        mouse = NSEvent.mouseLocation()
        target = None
        for screen in NSScreen.screens():
            sf = screen.frame()
            if (sf.origin.x <= mouse.x < sf.origin.x + sf.size.width
                    and sf.origin.y <= mouse.y < sf.origin.y + sf.size.height):
                target = screen
                break
        if target is None:
            target = NSScreen.mainScreen()
        if target is None:
            self._panel.center()
            self._last_screen = None
            return

        if target == self._last_screen:
            return

        self._last_screen = target
        sf = target.frame()
        pw = self._panel.frame().size.width
        ph = self._panel.frame().size.height
        x = sf.origin.x + (sf.size.width - pw) / 2
        y = sf.origin.y + sf.size.height - ph - 200
        self._panel.setFrameOrigin_((x, y))

    # ------------------------------------------------------------------
    # Panel reuse helpers
    # ------------------------------------------------------------------

    def _reconnect_panel_refs(self) -> None:
        """Restore ``_panel_ref`` back-references broken by :meth:`close`."""
        if self._message_handler is not None:
            self._message_handler._panel_ref = self
        if self._navigation_delegate is not None:
            self._navigation_delegate._panel_ref = self
        if self._panel_delegate is not None:
            self._panel_delegate._panel_ref = self

    def _reset_panel_ui(
        self,
        initial_query: str | None = None,
        placeholder: str | None = None,
    ) -> None:
        """Reset the webview UI state for a reused panel.

        Clears the previous search input, results, context block, and
        preview/compact modes so the panel appears fresh.

        The panel must be invisible (alpha=0) when this method is called.
        After the JS reset completes, the panel is resized to the measured
        collapsed height and revealed (alpha=1) so the user never sees
        stale content or the wrong dimensions.
        """
        parts = [
            "setResults([])",
            "setPreviewVisible(false)",
            "setCompact(false)",
            "setModifierHints({},null)",
            "setCreateButton(false)",
        ]
        # Clear or set input value
        if initial_query:
            parts.append(f"setInputValue({json.dumps(initial_query)})")
            # pending_initial_query is consumed by _on_page_loaded only on
            # first load; for reuse we apply it directly here.
            self._pending_initial_query = None
        else:
            parts.append("setInputValue('')")
        # Handle context block (Universal Action)
        if self._context_text is not None:
            escaped = json.dumps(self._context_text)
            label = json.dumps(t("chooser.ua.context_label"))
            parts.append(f"setContextText({escaped}, {label})")
        else:
            parts.append("clearContext()")
        # Apply placeholder
        if placeholder:
            parts.append(f"setPlaceholder({json.dumps(placeholder)})")
            self._pending_placeholder = None

        # Return the measured collapsed height so the completion handler
        # can resize the panel to exactly match a freshly created one.
        js = ";".join(parts) + ";document.querySelector('.search-bar').offsetHeight"

        def _on_reset_done(result: object, error: object) -> None:
            if self._panel is None:
                return
            h = int(result) if result else self._INITIAL_HEIGHT
            self._apply_frame(self._INITIAL_WIDTH, h)
            self._panel.setAlphaValue_(1.0)

        self._webview.evaluateJavaScript_completionHandler_(
            js, _on_reset_done
        )

    # ------------------------------------------------------------------
    # Source management
    # ------------------------------------------------------------------

    def register_source(self, source: ChooserSource) -> None:
        """Register a data source."""
        self._sources[source.name] = source
        logger.info("Chooser source registered: %s", source.name)

    def unregister_source(self, name: str) -> None:
        """Remove a data source by name."""
        self._sources.pop(name, None)

    def reset(self) -> None:
        """Clear all sources and reset trackers."""
        self._sources.clear()
        self._usage_tracker = None
        self._query_history = None

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------

    def _fire_event(self, event: str, *args) -> None:
        """Notify the API layer about a panel event."""
        if self._event_callback is not None:
            try:
                self._event_callback(event, *args)
            except Exception:
                logger.exception("Panel event callback error (%s)", event)

    def _maybe_close(self) -> None:
        """Close unless one of our panels (chooser or QL) is still key.

        Called on a deferred schedule after either the chooser or the QL
        panel loses key-window status.  Gives macOS time to assign the
        new key window before we check.
        """
        if self._closing:
            return

        def _check():
            if self._closing or self._panel is None:
                return
            try:
                from AppKit import NSApp

                key = NSApp.keyWindow()
                # Chooser panel regained key — do nothing
                if key is not None and key == self._panel:
                    return
                # QL panel is now key — user is interacting with preview
                if self._ql_panel is not None and self._ql_panel.is_key_window:
                    return
            except Exception:
                pass

            # Calculator mode: keep panel visible, listen for ESC
            if self._should_pin_for_calc():
                self._enter_calc_mode()
                return

            self.close()

        from PyObjCTools import AppHelper

        AppHelper.callLater(0.1, _check)

    # ------------------------------------------------------------------
    # Calculator pin mode
    # ------------------------------------------------------------------

    def _has_calc_results(self) -> bool:
        """Check if current results include calculator items."""
        return any(item.item_id.startswith("calc:") for item in self._current_items)

    def _should_pin_for_calc(self) -> bool:
        """Whether the panel should stay visible for calculator use."""
        return self._has_calc_results() or self._calc_sticky

    def _enter_calc_mode(self) -> None:
        """Keep the panel open despite losing focus, and listen for ESC.

        Called from ``_maybe_close`` when the panel loses key-window
        status while calculator results are displayed.
        """
        if self._calc_mode:
            return
        self._calc_mode = True
        self._previous_app = None  # Don't reactivate a stale app on close
        self._start_esc_tap()
        logger.debug("Entered calculator pin mode")

    def _exit_calc_mode(self) -> None:
        """Stop the ESC listener and reset the calc-mode flag."""
        if not self._calc_mode:
            return
        self._calc_mode = False
        self._stop_esc_tap()
        logger.debug("Exited calculator pin mode")

    def _start_esc_tap(self) -> None:
        """Create a CGEventTap on a background thread that swallows ESC."""
        if self._esc_runner is not None:
            return
        from PyObjCTools import AppHelper

        from wenzi import _cgeventtap as cg

        self._esc_runner = cg.CGEventTapRunner()
        mask = cg.CGEventMaskBit(cg.kCGEventKeyDown)
        self._esc_runner.start(
            mask, self._esc_tap_callback,
            on_create_failed=lambda: AppHelper.callAfter(self.close),
        )

    def _esc_tap_callback(self, proxy, event_type, event, refcon):
        """CGEventTap callback for ESC — runs on the tap's background thread."""
        from wenzi import _cgeventtap as cg

        try:
            if event_type == cg.kCGEventTapDisabledByTimeout:
                if self._esc_runner is not None and self._esc_runner.tap is not None:
                    cg.CGEventTapEnable(self._esc_runner.tap, True)
                return event
            if event_type == cg.kCGEventKeyDown:
                keycode = cg.CGEventGetIntegerValueField(
                    event,
                    cg.kCGKeyboardEventKeycode,
                )
                if keycode == _ESC_KEYCODE:
                    # Disable tap immediately to prevent auto-repeat
                    # from queuing multiple close() calls
                    if self._esc_runner is not None and self._esc_runner.tap is not None:
                        cg.CGEventTapEnable(self._esc_runner.tap, False)
                    from PyObjCTools import AppHelper

                    AppHelper.callAfter(self.close)
                    return None  # Swallow ESC
        except Exception:
            logger.warning("ESC tap callback error", exc_info=True)
        return event

    def _stop_esc_tap(self) -> None:
        """Disable and remove the ESC event tap."""
        if self._esc_runner is not None:
            self._esc_runner.stop()
            self._esc_runner = None
        logger.debug("ESC event tap stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_visible(self) -> bool:
        return self._panel is not None and self._panel.isVisible()

    def show(
        self,
        on_close: Callable | None = None,
        initial_query: str | None = None,
        placeholder: str | None = None,
    ) -> None:
        """Show the chooser panel. Must run on main thread.

        Args:
            on_close: Callback invoked when the panel closes.
            initial_query: If set, pre-fill the search input with this value
                and trigger a search immediately after the page loads.
            placeholder: If set, override the search input placeholder text.
        """
        self._cancel_recycle_timer()
        self._on_close = on_close
        self._pending_initial_query = initial_query
        self._pending_placeholder = placeholder

        if self._panel is not None and self._panel.isVisible():
            # Already visible — apply initial query if provided, else focus
            if initial_query:
                self._eval_js(f"setInputValue({json.dumps(initial_query)})")
            else:
                self._eval_js("focusInput()")
            self._position_on_mouse_screen()
            self._panel.makeKeyAndOrderFront_(None)
            from AppKit import NSApp

            NSApp.activateIgnoringOtherApps_(True)
            return

        self._previous_app = get_frontmost_app()

        if self._panel is not None and self._page_loaded:
            # Reuse hidden panel — reconnect refs and reset UI.
            # Hide the panel visually while JS resets content and layout;
            # _reset_panel_ui reveals it (alpha=1) once the measured
            # collapsed height is applied so no stale frame flashes.
            self._reconnect_panel_refs()
            self._position_on_mouse_screen()
            self._panel.setAlphaValue_(0.0)
            self._reset_panel_ui(initial_query, placeholder)
        elif self._panel is not None and self._webview is not None:
            # Warm path: panel + webview alive but page was unloaded
            # (empty HTML) to reclaim IOSurface memory.  Reload the
            # cached HTML — _on_page_loaded will handle pending query,
            # placeholder, and context.  Hide the panel (alpha=0) until
            # the page finishes loading to avoid a blank flash.
            self._reconnect_panel_refs()
            self._position_on_mouse_screen()
            self._panel.setAlphaValue_(0.0)
            self._reload_chooser_html()
        else:
            # First show — build from scratch
            self._build_panel()

        self._panel.makeKeyAndOrderFront_(None)

        from AppKit import NSApp

        NSApp.activateIgnoringOtherApps_(True)

        if self._snippet_expander is not None:
            self._snippet_expander.suppress()

        if self._switch_english:
            from wenzi.input_source import (
                get_current_input_source,
                is_english_input_source,
                select_english_input_source,
            )

            current = get_current_input_source()
            if current and not is_english_input_source(current):
                self._saved_input_source = current
                select_english_input_source()
            else:
                self._saved_input_source = None

        self._fire_event("open")

    def show_universal_action(
        self,
        context_text: str,
        exclusive_source: str | None = None,
        on_close: Callable | None = None,
        initial_query: str | None = None,
        placeholder: str | None = None,
    ) -> None:
        """Show the chooser in Universal Action mode with a context block.

        Must run on the main thread.

        Args:
            context_text: The selected text to display as read-only context.
            exclusive_source: If set, only search this source (bypass prefix logic).
            on_close: Callback invoked when the panel closes.
            initial_query: Pre-fill the search input (for filtering actions).
            placeholder: Override the search input placeholder text.
        """
        self._context_text = context_text
        self._exclusive_source = exclusive_source
        self.show(on_close=on_close, initial_query=initial_query, placeholder=placeholder)

    def close(self, *, _schedule_recycle: bool = True) -> None:
        """Hide the chooser panel, preserving WKWebView for fast re-show.

        Breaks ``_panel_ref`` back-references to prevent retain cycles
        while the panel is hidden.  The panel and webview remain alive
        so that the next :meth:`show` can skip the expensive
        WKWebView + HTML-load cold start.

        Use :meth:`destroy` for full teardown (e.g. during reload).
        """
        if self._closing:
            return
        self._closing = True
        self._context_text = None
        self._exclusive_source = None

        # Cancel all pending debounce timers
        self._cancel_all_debounce_timers()

        if self._snippet_expander is not None:
            self._snippet_expander.resume()
        self._calc_sticky = False
        self._exit_calc_mode()

        if self._ql_panel is not None:
            self._ql_panel.close()
            self._ql_panel = None

        # Break back-references to prevent retain cycles while hidden.
        # The objects themselves are kept alive for reuse.
        if self._message_handler is not None:
            self._message_handler._panel_ref = None
        if self._navigation_delegate is not None:
            self._navigation_delegate._panel_ref = None
        if self._panel_delegate is not None:
            self._panel_delegate._panel_ref = None

        # Clear visible content while the webview is still on screen so
        # that next show() won't flash stale results (evaluateJavaScript
        # is async but executes before the next display
        # cycle once the panel is already hidden).
        if self._webview is not None and self._page_loaded:
            self._webview.evaluateJavaScript_completionHandler_(
                "_releasePreviewImages();"
                "setResults([]);setInputValue('');"
                "setPreviewVisible(false);setCompact(false);"
                "setModifierHints({},null);setCreateButton(false);"
                "clearContext()",
                None,
            )

        if self._panel is not None:
            self._panel.orderOut_(None)

            # Shrink to 1×1 to release the CA Whippet Drawable backing
            # store.  macOS retains the full-size RGBA half-float
            # IOSurface (~63 MB at retina) even after orderOut_;
            # resizing forces CoreAnimation to reallocate a trivial
            # buffer.  show() repositions and JS resizes back.
            from Foundation import NSMakeRect

            f = self._panel.frame()
            self._panel.setFrame_display_(
                NSMakeRect(f.origin.x, f.origin.y, 1, 1), False,
            )
        self._last_screen = None

        # Load empty HTML to release IOSurface compositing layer buffers.
        # The WKWebView stays alive for fast re-show; only the rendered
        # content is discarded.  _page_loaded is set to False so the next
        # show() will reload the HTML from the cached temp file.
        if self._webview is not None:
            self._webview.loadHTMLString_baseURL_("", None)
            self._page_loaded = False

        self._current_items = []
        self._history_index = -1
        self._show_preview = False
        self._compact_results = False
        self._closing = False

        if self._saved_input_source is not None:
            from wenzi.input_source import select_input_source

            select_input_source(self._saved_input_source)
            self._saved_input_source = None

        # Reactivate the previous app's focused window.
        # No need to restore accessory mode — we never left it.
        from PyObjCTools import AppHelper

        previous_app = self._previous_app
        self._previous_app = None

        AppHelper.callAfter(reactivate_app, previous_app)

        self._fire_event("close")

        callback = self._on_close
        self._on_close = None
        if callback is not None:
            callback()

        if _schedule_recycle:
            self._schedule_recycle()

    # ------------------------------------------------------------------
    # Deferred webview recycle
    # ------------------------------------------------------------------

    def _schedule_recycle(self) -> None:
        """Schedule a webview recycle to free WebKit decoded image cache."""
        self._cancel_recycle_timer()
        if self._webview is None:
            return  # nothing to recycle
        from PyObjCTools import AppHelper

        self._recycle_timer = AppHelper.callLater(
            self._RECYCLE_DELAY, self._do_recycle,
        )

    def _cancel_recycle_timer(self) -> None:
        if self._recycle_timer is not None:
            self._recycle_timer.cancel()
            self._recycle_timer = None

    def _teardown_webview(self) -> None:
        """Release the webview, panel, and associated delegates."""
        if self._webview is not None:
            self._webview.setNavigationDelegate_(None)
            try:
                config = self._webview.configuration()
                if config:
                    config.userContentController().removeScriptMessageHandlerForName_("chooser")
            except Exception:
                pass
        if self._panel is not None:
            self._panel.setDelegate_(None)
            self._panel.orderOut_(None)
        self._panel = None
        self._webview = None
        self._effect_view = None
        self._message_handler = None
        self._navigation_delegate = None
        self._panel_delegate = None
        self._page_loaded = False
        self._pending_js = []

    def _do_recycle(self) -> None:
        """Replace old webview with a fresh one to free WebKit image cache.

        The old Web Content process (with accumulated decoded image bitmaps)
        exits when the WKWebView is deallocated.  A new panel + webview is
        pre-built so the next :meth:`show` gets a near-instant warm start.

        This method is called by the recycle timer — *not* by user code.
        """
        self._recycle_timer = None
        if self._panel is not None and self._panel.isVisible():
            return  # user re-opened before timer fired

        self._teardown_webview()

        # Build fresh panel + webview (new Web Content process) but skip
        # HTML loading.  Loading HTML triggers IOSurface compositing layer
        # allocation (~72 MB at Retina) that persists even while hidden.
        # The next show() will hit the warm path and call
        # _reload_chooser_html() on demand.
        self._build_panel(load_html=False)
        self._last_screen = None
        logger.debug("ChooserPanel recycled: old webview destroyed, fresh one built")

    def destroy(self) -> None:
        """Fully destroy the panel and webview, releasing all resources.

        Called during script reload when the HTML/i18n may have changed
        and the WKWebView must be recreated from scratch.
        """
        # Close first to handle hide + state cleanup (skip recycle —
        # we're tearing down fully).
        self.close(_schedule_recycle=False)

        self._teardown_webview()
        self._last_screen = None

    def toggle(self, on_close: Callable | None = None) -> None:
        """Toggle the chooser panel visibility."""
        if self.is_visible:
            self.close()
        else:
            self.show(on_close=on_close)

    # ------------------------------------------------------------------
    # Search logic
    # ------------------------------------------------------------------

    def _do_search(self, query: str) -> None:
        """Run a search against sources and push results to JS.

        Prefix activation: if the query starts with ``<prefix> `` (e.g.
        ``cb hello``), the matching source is activated and the prefix is
        stripped.  Otherwise all non-prefix sources are searched.

        Sync sources return results immediately.  Async sources are
        dispatched to the shared event loop; their results are merged
        incrementally via :meth:`_merge_async_results`.
        """
        self._last_query = query
        self._search_generation += 1
        generation = self._search_generation
        source = None

        # Exclusive source mode (Universal Action): bypass prefix logic,
        # always search only the designated source with the raw query.
        if self._exclusive_source and self._exclusive_source in self._sources:
            source = self._sources[self._exclusive_source]
        else:
            # Check for prefix activation (Alfred-style: "prefix query")
            for src in self._sources.values():
                if src.prefix:
                    trigger = src.prefix + " "
                    if query.startswith(trigger):
                        source = src
                        query = query[len(trigger) :]
                        break

        # Track active source and toggle create button in JS
        prev_source = self._active_source
        self._active_source = source
        if source != prev_source:
            has_create = source is not None and source.create_action is not None
            self._eval_js(f"setCreateButton({'true' if has_create else 'false'})")

        # When searching across all non-prefix sources (no specific source),
        # empty query returns nothing. When a specific source is active
        # (e.g. clipboard via prefix), let the source decide.
        if source is None:
            if not query.strip():
                self._current_items = []
                self._pending_async_count = 0
                self._calc_sticky = False
                self._compact_results = False
                self._show_preview = False
                self._eval_js("setResults([]);setPreviewVisible(false);setCompact(false);setModifierHints({},null)")
                self._set_loading(False)
                return

        # Partition sources into sync and async
        if source is not None:
            # Single source activated by prefix
            if source.is_async:
                sync_sources = []
                async_sources = [source]
            else:
                sync_sources = [source]
                async_sources = []
        else:
            sorted_sources = sorted(
                self._sources.values(),
                key=lambda s: s.priority,
                reverse=True,
            )
            sync_sources: list = []
            async_sources: list = []
            for s in sorted_sources:
                if s.prefix is None and s.search is not None:
                    (async_sources if s.is_async else sync_sources).append(s)

        # Phase 1: Run sync sources immediately
        all_items: list = []
        for src in sync_sources:
            try:
                all_items.extend(src.search(query))
            except Exception:
                logger.exception("Chooser source %s search error", src.name)

        # Inject prefix-source hints when no source is activated
        if source is None and query.strip():
            all_items.extend(self._match_prefix_sources(query.strip()))

        self._current_items = all_items[: self._MAX_TOTAL_RESULTS]

        # Apply usage-based boosting
        if self._usage_tracker and self._current_items:
            self._boost_by_usage(self._usage_query(query))

        # Update calculator sticky mode
        if self._has_calc_results():
            self._calc_sticky = True
        elif not any(ch.isdigit() for ch in query):
            self._calc_sticky = False

        # Determine preview mode and compact mode
        # Once in compact mode, stay until input is cleared (handled by
        # the empty-query early return above).
        show_preview = source.show_preview if source is not None else False
        if not self._compact_results:
            compact = bool(self._current_items) and all(item.item_id.startswith("calc:") for item in self._current_items)
        else:
            compact = True
        self._compact_results = compact
        self._show_preview = show_preview

        # Push sync results immediately
        self._push_items_to_js(source=source)

        # Phase 2: Launch async sources (with debounce support)
        self._cancel_all_debounce_timers()
        if async_sources:
            immediate = []
            debounced = []
            for asrc in async_sources:
                delay = self._get_debounce_delay(asrc)
                if delay > 0:
                    debounced.append((asrc, delay))
                else:
                    immediate.append(asrc)

            # Launch immediate sources right away
            self._pending_async_count = len(immediate)
            if immediate:
                self._set_loading(True)
                for asrc in immediate:
                    self._launch_async_search(asrc, query, generation)

            # Schedule debounced sources (each with its own timer)
            if debounced:
                self._pending_async_count += len(debounced)
                self._set_loading(True)
                for asrc, delay in debounced:
                    self._schedule_debounced_search(asrc, query, generation, delay)
        else:
            self._set_loading(False)

    def _match_prefix_sources(self, query: str) -> list[ChooserItem]:
        """Return ChooserItems for registered prefixed sources matching *query*.

        Each item's ``complete_text`` is set to ``"<prefix> "`` so that
        pressing Enter activates the source instead of closing the panel.
        """
        hits: list[tuple[int, ChooserItem]] = []
        for src in self._sources.values():
            if not src.prefix:
                continue
            fields = [src.prefix]
            if src.display_name:
                fields.append(src.display_name)
            fields.append(src.name)
            if src.description:
                fields.append(src.description)
            matched, score = False, 0
            for f in fields:
                m, s = fuzzy_match(query, f)
                if m and s > score:
                    matched, score = m, s
            if matched:
                label = src.display_name or src.name
                hits.append((
                    score,
                    ChooserItem(
                        title=label,
                        subtitle=f"{src.prefix}  —  {src.description}" if src.description else src.prefix,
                        item_id=f"source-hint:{src.name}",
                        complete_text=src.prefix + " ",
                    ),
                ))
        hits.sort(key=lambda x: -x[0])
        return [item for _, item in hits]

    def _boost_by_usage(self, query: str) -> None:
        """Re-sort items by usage frequency while preserving source order."""
        tracker = self._usage_tracker
        scored = []
        for i, item in enumerate(self._current_items):
            usage = tracker.score(query, item.item_id) if item.item_id else 0
            # Stable sort: usage descending, then original order
            scored.append((-usage, i, item))
        scored.sort(key=lambda x: (x[0], x[1]))
        self._current_items = [item for _, _, item in scored]

    def _usage_query(self, query: str) -> str:
        """Return the query key for usage tracking.

        In Universal Action mode, empty queries use a synthetic prefix so
        that usage learning still works when the user hasn't typed anything.
        """
        if not query and self._context_text is not None:
            return self._UA_USAGE_PREFIX
        return query

    @staticmethod
    def _default_action_hints():
        return {
            "enter": t("chooser.action.open"),
            "cmd_enter": t("chooser.action.reveal"),
        }

    _HINT_KEY_TO_MODIFIER = {
        "cmd_enter": "cmd",
        "alt_enter": "alt",
        "shift": "shift",
        "ctrl_enter": "ctrl",
    }

    @classmethod
    def _action_hints_to_modifier_map(cls, hints: dict) -> dict:
        """Convert action_hints keys to modifier→label map for JS."""
        return {
            mod: hints[key]
            for key, mod in cls._HINT_KEY_TO_MODIFIER.items()
            if hints.get(key)
        }

    def _push_items_to_js(
        self,
        selected_index: int | None = None,
        source=None,
        preserve_selection: bool = False,
    ) -> None:
        """Serialize current items and send to the web view.

        Builds a single JS snippet combining icon cache updates, result
        items, and action hints to minimise evaluateJavaScript round-trips.
        """
        self._items_version += 1

        js_items = []
        for item in self._current_items:
            js_item: dict = {
                "title": item.title,
                "subtitle": item.subtitle,
                "icon": item.icon,
                "icon_badge": item.icon_badge,
                "icon_accessory": item.icon_accessory,
                "badge": "",
                "hasReveal": (item.reveal_path is not None or item.secondary_action is not None),
                "hasModifiers": bool(item.modifiers),
                "deletable": item.delete_action is not None,
                "confirmDelete": item.confirm_delete,
            }
            # Include preview only for the selected item to keep payload
            # small while avoiding an extra bridge round-trip.
            sel = selected_index if selected_index is not None else 0
            if len(js_items) == sel and item.preview is not None:
                preview = item.preview
                if callable(preview):
                    try:
                        preview = preview()
                    except Exception:
                        preview = None
                    if preview is not None:
                        item.preview = preview  # cache resolved value
                if preview is not None:
                    js_item["preview"] = preview
            js_items.append(js_item)

        # Build a single JS snippet
        parts: list[str] = []

        if preserve_selection:
            idx_arg = ",-2"  # sentinel: JS keeps current selection
        elif selected_index is None:
            idx_arg = ""
        else:
            idx_arg = f",{selected_index}"
        parts.append(f"setResults({json.dumps(js_items, ensure_ascii=False)},{self._items_version}{idx_arg})")

        if source is not None and source.action_hints:
            hints = source.action_hints
        elif self._compact_results and "calculator" in self._sources:
            hints = self._sources["calculator"].action_hints or self._default_action_hints()
        else:
            hints = self._default_action_hints()
        modifier_map = self._action_hints_to_modifier_map(hints)

        item_overrides: dict = {}
        for i, item in enumerate(self._current_items):
            if item.modifiers:
                item_overrides[str(i)] = {
                    mod_key: mod_action.subtitle
                    for mod_key, mod_action in item.modifiers.items()
                }
        ov_json = json.dumps(item_overrides, ensure_ascii=False) if item_overrides else "null"
        parts.append(f"setModifierHints({json.dumps(modifier_map, ensure_ascii=False)},{ov_json})")

        show = "true" if self._show_preview else "false"
        parts.append(f"setPreviewVisible({show})")
        compact = "true" if self._compact_results else "false"
        parts.append(f"setCompact({compact})")

        self._eval_js(";".join(parts))

    # ------------------------------------------------------------------
    # Async source search
    # ------------------------------------------------------------------

    def _set_loading(self, visible: bool) -> None:
        """Update the loading spinner, skipping no-op calls."""
        if visible == self._loading_visible:
            return
        self._loading_visible = visible
        self._eval_js(f"setLoading({'true' if visible else 'false'})")

    def _get_timeout(self, source: ChooserSource) -> float:
        """Get the actual timeout for an async source."""
        if source.search_timeout is not None:
            return source.search_timeout
        return self._DEFAULT_ASYNC_TIMEOUT

    def _get_debounce_delay(self, source: ChooserSource) -> float:
        """Get the actual debounce delay for an async source."""
        if source.debounce_delay is not None:
            return source.debounce_delay
        return self._DEFAULT_ASYNC_DEBOUNCE

    def _launch_async_search(
        self,
        source: ChooserSource,
        query: str,
        generation: int,
    ) -> None:
        """Submit an async source search to the shared event loop."""
        import wenzi.async_loop as _aloop

        timeout = self._get_timeout(source)

        async def _run():
            try:
                return await asyncio.wait_for(
                    source.search(query),
                    timeout=timeout,
                )
            except TimeoutError:
                logger.warning(
                    "Async source %s timed out after %.1fs",
                    source.name,
                    timeout,
                )
                return []
            except asyncio.CancelledError:
                return []
            except Exception:
                logger.exception("Async source %s search error", source.name)
                return []

        def _on_future_done(future):
            """Called on asyncio thread — bridge results to main thread."""
            try:
                items = future.result() or []
            except Exception:
                items = []
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self._merge_async_results, source, items, generation)

        try:
            loop = _aloop.get_loop()
            future = asyncio.run_coroutine_threadsafe(_run(), loop)
            future.add_done_callback(_on_future_done)
        except RuntimeError:
            logger.error("Async loop unavailable for source %s", source.name)
            self._pending_async_count = max(0, self._pending_async_count - 1)
            if self._pending_async_count == 0:
                self._set_loading(False)

    def _merge_async_results(
        self,
        source: ChooserSource,
        items: list,
        generation: int,
    ) -> None:
        """Merge async source results on the main thread."""
        if generation != self._search_generation:
            return  # Stale search — discard

        self._pending_async_count = max(0, self._pending_async_count - 1)

        pushed = False
        if items:
            remaining = self._MAX_TOTAL_RESULTS - len(self._current_items)
            if remaining > 0:
                self._current_items.extend(items[:remaining])

            if self._usage_tracker and self._current_items:
                self._boost_by_usage(self._usage_query(self._last_query))

            self._push_items_to_js(
                source=source if self._active_source is source else None,
                preserve_selection=True,
            )
            pushed = True

        if self._pending_async_count == 0:
            if generation == self._search_generation:
                self._set_loading(False)
            # Force a state sync if no results were pushed
            if not pushed:
                self._push_items_to_js(
                    source=self._active_source,
                    preserve_selection=True,
                )

    # ------------------------------------------------------------------
    # Debounced async search
    # ------------------------------------------------------------------

    def _cancel_all_debounce_timers(self) -> None:
        """Invalidate and remove all pending debounce timers."""
        for entry in self._debounce_state.values():
            entry.timer.invalidate()
        self._debounce_state.clear()

    def _schedule_debounced_search(
        self,
        source: ChooserSource,
        query: str,
        generation: int,
        delay: float,
    ) -> None:
        """Schedule a debounced async search for a single source using NSTimer."""
        name = source.name

        # Cancel previous timer for this source
        old = self._debounce_state.pop(name, None)
        if old is not None:
            old.timer.invalidate()

        # Create helper (NSObject target for NSTimer)
        HelperClass = _get_debounce_timer_helper_class()
        helper = HelperClass.alloc().init()
        helper._callback = lambda n=name: self._execute_debounced_search(n)

        from Foundation import NSTimer

        timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            delay,
            helper,
            b"fire:",
            None,
            False,
        )

        self._debounce_state[name] = _DebounceEntry(
            timer=timer,
            helper=helper,
            source=source,
            query=query,
            generation=generation,
        )

    def _execute_debounced_search(self, source_name: str) -> None:
        """Execute debounced search for a single source (called by NSTimer)."""
        entry = self._debounce_state.pop(source_name, None)
        if entry is None:
            return

        # Discard if stale. The count was already reset by the new _do_search
        # call that incremented the generation.
        if entry.generation != self._search_generation:
            return

        self._launch_async_search(entry.source, entry.query, entry.generation)

    # ------------------------------------------------------------------
    # JS message handler
    # ------------------------------------------------------------------

    def _handle_js_message(self, body: dict) -> None:
        """Dispatch messages from JavaScript."""
        msg_type = body.get("type", "")

        if msg_type == "search":
            query = body.get("query", "")
            self._do_search(query)

        elif msg_type == "execute":
            index = body.get("index", 0)
            version = body.get("version", self._items_version)
            modifier = body.get("modifier")  # "alt", "ctrl", "shift" or None
            self._execute_item(index, version, modifier=modifier)

        elif msg_type == "reveal":
            index = body.get("index", 0)
            version = body.get("version", self._items_version)
            self._reveal_item(index, version)

        elif msg_type == "close":
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self.close)

        elif msg_type == "requestPreview":
            index = body.get("index", -1)
            self._send_preview(index)

        elif msg_type == "deleteItem":
            index = body.get("index", -1)
            version = body.get("version", self._items_version)
            self._delete_item(index, version)

        elif msg_type == "createItem":
            query = body.get("query", "")
            self._handle_create_item(query)

        elif msg_type == "historyUp":
            self._history_navigate(1)

        elif msg_type == "historyDown":
            self._history_navigate(-1)

        elif msg_type == "exitHistory":
            self._history_index = -1

        elif msg_type == "resize":
            w = body.get("width", self._INITIAL_WIDTH)
            h = body.get("height", self._INITIAL_HEIGHT)
            self._apply_frame(w, h)

        elif msg_type == "tab":
            index = body.get("index", -1)
            self._handle_tab_complete(index)

        elif msg_type == "openSettings":
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self.close)
            self._fire_event("openSettings")

        elif msg_type == "shiftPreview":
            is_open = body.get("open", False)
            index = body.get("index", -1)
            self._toggle_quicklook(is_open, index)

        elif msg_type == "drag":
            dx = body.get("dx", 0)
            dy = body.get("dy", 0)
            self._drag_panel(dx, dy)

        elif msg_type == "qlNavigate":
            index = body.get("index", -1)
            self._update_quicklook(index)

    def _history_navigate(self, direction: int) -> None:
        """Navigate query history. direction=1 means older, -1 means newer."""
        if self._query_history is None:
            return
        history = self._query_history.entries()  # newest-first
        if not history:
            return

        new_index = self._history_index + direction
        if new_index < 0:
            # Already at newest or before history — exit history mode
            self._history_index = -1
            self._eval_js("clearInput();exitHistoryMode()")
            return
        if new_index >= len(history):
            # At the oldest entry — do nothing
            return

        self._history_index = new_index
        query = history[new_index]
        self._eval_js(f"setHistoryQuery({json.dumps(query)})")

    def _handle_tab_complete(self, index: int) -> None:
        """Handle Tab key: call active source's complete callback."""
        query = self._last_query or ""

        # Resolve the active prefix source from the current query
        source = None
        prefix_str = ""
        for src in self._sources.values():
            if src.prefix:
                trigger = src.prefix + " "
                if query.startswith(trigger):
                    source = src
                    prefix_str = trigger
                    break

        if source is None or source.complete is None:
            return

        stripped_query = query[len(prefix_str) :]
        if not (0 <= index < len(self._current_items)):
            return

        item = self._current_items[index]
        try:
            completed = source.complete(stripped_query, item)
        except Exception:
            logger.exception("Tab complete error for source %s", source.name)
            return

        if completed is None:
            return

        new_query = prefix_str + completed
        self._eval_js(f"setInputValue({json.dumps(new_query, ensure_ascii=False)})")

    def _handle_create_item(self, query: str) -> None:
        """Dispatch the create action for the active source."""
        source = self._active_source
        if source is None or source.create_action is None:
            return

        self.close()

        from PyObjCTools import AppHelper

        def _run_create():
            try:
                source.create_action(query)
            except Exception:
                logger.exception(
                    "Chooser create action failed for source %r",
                    source.name,
                )

        AppHelper.callAfter(_run_create)

    def _delete_item(self, index: int, version: int = 0) -> None:
        """Delete an item and refresh the list, preserving selection position."""
        if version and version != self._items_version:
            return
        if 0 <= index < len(self._current_items):
            item = self._current_items[index]
            if item.delete_action is not None:
                try:
                    item.delete_action()
                except Exception:
                    logger.exception(
                        "Chooser delete action failed for %r",
                        item.title,
                    )
                self._fire_event(
                    "delete",
                    {
                        "title": item.title,
                        "subtitle": item.subtitle,
                        "item_id": item.item_id,
                    },
                )
                self._current_items.pop(index)
                # Keep selection at the same position (clamped by JS)
                self._push_items_to_js(selected_index=index)

    def _execute_item(
        self,
        index: int,
        version: int = 0,
        modifier: str | None = None,
    ) -> None:
        """Execute item action. Uses modifier action if available."""
        if version and version != self._items_version:
            logger.debug("Stale execute (v%d != v%d), ignored", version, self._items_version)
            return
        if 0 <= index < len(self._current_items):
            item = self._current_items[index]

            # Choose action based on modifier key
            action = item.action
            if modifier and item.modifiers and modifier in item.modifiers:
                mod_action = item.modifiers[modifier]
                if mod_action.action is not None:
                    action = mod_action.action

            # Record usage for learning
            if self._usage_tracker and item.item_id:
                self._usage_tracker.record(
                    self._usage_query(self._last_query), item.item_id
                )

            # Record query history
            if self._query_history and self._last_query and self._last_query.strip():
                self._query_history.record(self._last_query)

            self._fire_event(
                "select",
                {
                    "title": item.title,
                    "subtitle": item.subtitle,
                    "item_id": item.item_id,
                },
            )

            # If the item has complete_text, fill search box instead of closing
            if item.complete_text is not None:
                self._eval_js(f"setInputValue({json.dumps(item.complete_text, ensure_ascii=False)})")
                return

            from PyObjCTools import AppHelper

            AppHelper.callAfter(self.close)
            if action is not None:
                import threading

                def _deferred():
                    import time

                    import objc

                    time.sleep(self._DEFERRED_ACTION_DELAY)
                    with objc.autorelease_pool():
                        try:
                            action()
                        except Exception:
                            logger.exception("Chooser action failed for %r", item.title)

                threading.Thread(target=_deferred, daemon=True).start()

    def _toggle_quicklook(self, is_open: bool, index: int) -> None:
        """Toggle Quick Look preview for the selected item."""
        if is_open:
            if 0 <= index < len(self._current_items):
                item = self._current_items[index]
                path = item.reveal_path
                if path and os.path.exists(path):
                    if self._ql_panel is None:
                        from wenzi.scripting.ui.quicklook_panel import QuickLookPanel

                        self._ql_panel = QuickLookPanel(
                            on_resign_key=self._maybe_close,
                            on_shift_toggle=self._on_ql_shift_toggle,
                        )
                    self._ql_panel.show(path, anchor_panel=self._panel)
                    return
        # Close
        if self._ql_panel is not None:
            self._ql_panel.close()

    def _on_ql_shift_toggle(self) -> None:
        """Called when Shift is tapped while the QL panel has focus."""
        if self._ql_panel is not None:
            self._ql_panel.close()
        # Reset JS-side qlPreviewOpen state
        self._eval_js("qlPreviewOpen=false")

    def _update_quicklook(self, index: int) -> None:
        """Update Quick Look preview when navigating with ↑↓."""
        if self._ql_panel is None or not self._ql_panel.is_visible:
            return
        if 0 <= index < len(self._current_items):
            item = self._current_items[index]
            path = item.reveal_path
            if path and os.path.exists(path):
                self._ql_panel.update(path)

    def _reveal_item(self, index: int, version: int = 0) -> None:
        """Execute the secondary action (Cmd+Enter).

        For apps: reveal in Finder. For other items: call secondary_action.
        """
        if version and version != self._items_version:
            return
        if 0 <= index < len(self._current_items):
            item = self._current_items[index]
            from PyObjCTools import AppHelper

            if item.reveal_path:
                import subprocess

                subprocess.Popen(  # noqa: S603
                    ["open", "-R", item.reveal_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                AppHelper.callAfter(self.close)
            elif item.secondary_action is not None:
                AppHelper.callAfter(self.close)
                try:
                    item.secondary_action()
                except Exception:
                    logger.exception("Chooser secondary action failed for %r", item.title)

    def _send_preview(self, index: int) -> None:
        """Send preview data for the item at *index* to JS."""
        if 0 <= index < len(self._current_items):
            item = self._current_items[index]
            preview = item.preview
            if preview is not None:
                # Resolve lazy preview (callable → dict)
                if callable(preview):
                    try:
                        preview = preview()
                    except Exception:
                        logger.debug("Preview provider error", exc_info=True)
                        preview = None
                    # Only cache successful resolutions; keep the
                    # callable around so the user can retry.
                    if preview is not None:
                        item.preview = preview
                if preview is not None:
                    self._eval_js(f"setPreview({json.dumps(preview, ensure_ascii=False)})")
                    return
        self._eval_js("setPreview(null)")

    # ------------------------------------------------------------------
    # Internal: panel construction
    # ------------------------------------------------------------------

    def _eval_js(self, js_code: str) -> None:
        """Evaluate JavaScript, queuing if page not yet loaded."""
        if self._webview is None:
            return
        if not self._page_loaded:
            self._pending_js.append(js_code)
            return
        self._webview.evaluateJavaScript_completionHandler_(js_code, None)

    def _inject_i18n(self) -> None:
        """Inject i18n translations into the webview JS context."""
        from wenzi.i18n import inject_i18n_into_webview

        inject_i18n_into_webview(self._webview, "chooser.")

    def _on_page_loaded(self) -> None:
        """Called when WKWebView finishes loading the HTML."""
        # Guard against the blank-page navigation fired by close().
        # If show() is called before the blank load completes,
        # _reconnect_panel_refs restores the delegate ref and this
        # callback would fire for the empty page.  Ignore it.
        if self._webview is not None:
            url = self._webview.URL()
            url_str = str(url.absoluteString()) if url is not None else ""
            if not url_str or url_str == "about:blank":
                return

        # Inject i18n translations before flushing pending JS
        self._inject_i18n()

        pending = self._pending_js[:]
        self._pending_js.clear()
        self._page_loaded = True
        if pending and self._webview is not None:
            combined = ";".join(pending)
            self._webview.evaluateJavaScript_completionHandler_(combined, None)

        # Apply custom placeholder
        if self._pending_placeholder is not None:
            self._eval_js(f"setPlaceholder({json.dumps(self._pending_placeholder)})")
            self._pending_placeholder = None

        # Apply pending initial query (e.g. from source hotkey)
        if self._pending_initial_query is not None:
            query = self._pending_initial_query
            self._pending_initial_query = None
            self._eval_js(f"setInputValue({json.dumps(query)})")

        # Universal Action context block
        if self._context_text is not None:
            escaped = json.dumps(self._context_text)
            label = json.dumps(t("chooser.ua.context_label"))
            self._eval_js(f"setContextText({escaped}, {label})")

        # Reveal the panel if it was hidden (alpha=0) during the warm-start
        # path.  This is a no-op on the cold path where alpha is already 1.
        if self._panel is not None:
            self._panel.setAlphaValue_(1.0)

    @staticmethod
    def _ensure_edit_menu() -> None:
        """Create a minimal Edit menu if the app doesn't have one.

        macOS routes Cmd+C/X/V/A through the main menu's key equivalents
        into the responder chain.  Without an Edit menu, these shortcuts
        are never dispatched, producing a beep in borderless panels.
        The menu is invisible in accessory (statusbar) mode.
        """
        from AppKit import NSApp, NSMenu, NSMenuItem

        main_menu = NSApp.mainMenu()
        if main_menu is None:
            main_menu = NSMenu.alloc().initWithTitle_("")
            NSApp.setMainMenu_(main_menu)

        # Check if an Edit submenu already exists
        for i in range(main_menu.numberOfItems()):
            item = main_menu.itemAtIndex_(i)
            if item.submenu() and item.submenu().title() == "Edit":
                return

        edit_menu = NSMenu.alloc().initWithTitle_("Edit")
        for title, action, key in (
            ("Undo", "undo:", "z"),
            ("Redo", "redo:", "Z"),
            ("Cut", "cut:", "x"),
            ("Copy", "copy:", "c"),
            ("Paste", "paste:", "v"),
            ("Select All", "selectAll:", "a"),
        ):
            edit_menu.addItemWithTitle_action_keyEquivalent_(title, action, key)

        edit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Edit",
            None,
            "",
        )
        edit_item.setSubmenu_(edit_menu)
        main_menu.addItem_(edit_item)

    def _reload_chooser_html(self) -> None:
        """Reload the chooser HTML into an existing (but blanked) WKWebView.

        Used by the warm-start path in :meth:`show` after :meth:`close`
        loaded ``about:blank`` to release IOSurface memory.  The cached
        HTML file on disk is reused — no need to regenerate it.
        """
        from Foundation import NSURL

        from wenzi.config import DEFAULT_CACHE_DIR

        cache_dir = os.path.expanduser(DEFAULT_CACHE_DIR)
        html_path = os.path.join(cache_dir, "_chooser.html")

        if not os.path.isfile(html_path):
            # HTML file missing (shouldn't happen) — regenerate it in-place.
            # Do NOT call destroy() here: we are inside show(), and destroy()
            # would fire the _on_close callback out of order.
            logger.warning("Chooser HTML cache missing, regenerating")
            from wenzi.ui.templates import load_template

            os.makedirs(cache_dir, exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(load_template("chooser.html"))

        home_dir = os.path.expanduser("~")
        self._webview.loadFileURL_allowingReadAccessToURL_(
            NSURL.fileURLWithPath_(html_path),
            NSURL.fileURLWithPath_(home_dir),
        )

    def _build_panel(self, *, load_html: bool = True) -> None:
        """Create NSPanel + WKWebView."""
        from AppKit import (
            NSBackingStoreBuffered,
            NSStatusWindowLevel,
        )
        from Foundation import NSURL, NSMakeRect
        from WebKit import WKUserContentController, WKWebView

        PanelClass = _get_keyable_panel_class()
        # Bootstrap size; JS will send the correct size after page load
        initial_width = self._INITIAL_WIDTH
        initial_height = self._INITIAL_HEIGHT
        panel = PanelClass.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, initial_width, initial_height),
            0,  # Borderless
            NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(NSStatusWindowLevel + 1)
        panel.setOpaque_(False)
        panel.setHasShadow_(True)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setMovableByWindowBackground_(False)
        panel.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 8))  # canJoinAllSpaces | stationary | fullScreenAuxiliary

        # Transparent background — the HTML provides its own
        from AppKit import NSColor

        panel.setBackgroundColor_(NSColor.clearColor())

        # Ensure the app has an Edit menu so Cmd+C/X/V/A key equivalents
        # are routed through the responder chain to the WKWebView.
        # Without this, borderless panels in accessory apps silently
        # drop these shortcuts (producing a beep).
        self._ensure_edit_menu()

        # Close on focus loss
        delegate_cls = _get_panel_delegate_class()
        delegate = delegate_cls.alloc().init()
        delegate._panel_ref = self
        panel.setDelegate_(delegate)
        self._panel_delegate = delegate

        # Round corners
        panel.contentView().setWantsLayer_(True)
        panel.contentView().layer().setCornerRadius_(16.0)
        panel.contentView().layer().setMasksToBounds_(True)

        # Glass blur background (NSVisualEffectView behind WKWebView)
        from AppKit import NSVisualEffectView

        effect_view = NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, initial_width, initial_height)
        )
        effect_view.setAutoresizingMask_(0x12)  # Width + Height sizable
        effect_view.setBlendingMode_(1)  # NSVisualEffectBlendingModeBehindWindow
        effect_view.setMaterial_(0)  # NSVisualEffectMaterialAppearanceBased — bright glass
        effect_view.setState_(1)  # NSVisualEffectStateActive (always active)
        effect_view.setWantsLayer_(True)
        effect_view.layer().setCornerRadius_(16.0)
        effect_view.layer().setMasksToBounds_(True)
        panel.contentView().addSubview_(effect_view)
        self._effect_view = effect_view

        # Position: center-top of mouse screen (like Spotlight)
        self._panel = panel
        self._position_on_mouse_screen()

        # WKWebView with message handler
        from wenzi.ui.web_utils import lightweight_webview_config

        wk_config = lightweight_webview_config(shared=False)
        content_controller = WKUserContentController.alloc().init()

        handler_cls = _get_message_handler_class()
        handler = handler_cls.alloc().init()
        handler._panel_ref = self
        content_controller.addScriptMessageHandler_name_(handler, "chooser")
        wk_config.setUserContentController_(content_controller)

        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, initial_width, initial_height),
            wk_config,
        )
        webview.setAutoresizingMask_(0x12)  # Width + Height sizable
        webview.setValue_forKey_(False, "drawsBackground")
        panel.contentView().addSubview_(webview)

        # Navigation delegate
        nav_cls = _get_navigation_delegate_class()
        nav_delegate = nav_cls.alloc().init()
        nav_delegate._panel_ref = self
        webview.setNavigationDelegate_(nav_delegate)

        self._webview = webview
        self._message_handler = handler
        self._navigation_delegate = nav_delegate
        self._page_loaded = False
        self._pending_js = []
        self._current_items = []

        if not load_html:
            return

        # Load HTML from a temp file so WKWebView grants file:// access.
        # Icons live in ~/.cache/WenZi and clipboard images in
        # ~/.local/share/WenZi.  WKWebView only accepts a single
        # allowingReadAccessToURL_ directory, and the lowest common ancestor
        # of these two XDG paths is ~/, so we must grant home-wide read
        # access.  This is safe because the web view only loads our own
        # local HTML — no user-controlled URLs are loaded.
        from wenzi.config import DEFAULT_CACHE_DIR
        from wenzi.ui.templates import load_template

        cache_dir = os.path.expanduser(DEFAULT_CACHE_DIR)
        os.makedirs(cache_dir, exist_ok=True)
        html_path = os.path.join(cache_dir, "_chooser.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(load_template("chooser.html"))
        home_dir = os.path.expanduser("~")
        webview.loadFileURL_allowingReadAccessToURL_(
            NSURL.fileURLWithPath_(html_path),
            NSURL.fileURLWithPath_(home_dir),
        )
