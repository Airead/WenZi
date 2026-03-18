"""Chooser panel — Alfred/Raycast-style quick launcher.

Uses NSPanel + WKWebView for a search-and-filter UI.
Keyboard-driven: type to filter, ↑↓ to navigate, Enter to execute.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Callable, Dict, List, Optional

from wenzi.scripting.sources import ChooserItem, ChooserSource
from wenzi.ui_helpers import get_frontmost_app, reactivate_app, restore_accessory

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
    from Foundation import NSObject

    import WebKit  # noqa: F401

    WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")

    class ChooserMessageHandler(NSObject, protocols=[WKScriptMessageHandler]):
        _panel_ref = None

        def userContentController_didReceiveScriptMessage_(
            self, controller, message
        ):
            if self._panel_ref is None:
                return
            raw = message.body()
            try:
                from Foundation import NSJSONSerialization

                json_data, _ = (
                    NSJSONSerialization.dataWithJSONObject_options_error_(
                        raw, 0, None
                    )
                )
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
    from Foundation import NSObject

    import WebKit  # noqa: F401

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
# Panel
# ---------------------------------------------------------------------------


class ChooserPanel:
    """Alfred/Raycast-style search launcher panel.

    Manages an NSPanel with WKWebView, dispatches search queries to
    registered ChooserSource instances, and executes item actions.
    """

    _PANEL_WIDTH_WIDE = 960  # With preview panel
    _PANEL_WIDTH_NARROW = 600  # Without preview panel
    _PANEL_HEIGHT_EXPANDED = 400
    _PANEL_HEIGHT_COMPACT = 120  # Search bar + one result row + footer
    _PANEL_HEIGHT_COLLAPSED = 48
    _MAX_TOTAL_RESULTS = 50

    def __init__(self, usage_tracker=None) -> None:
        self._panel = None
        self._webview = None
        self._message_handler = None
        self._navigation_delegate = None
        self._panel_delegate = None
        self._page_loaded: bool = False
        self._pending_js: list[str] = []

        self._sources: Dict[str, ChooserSource] = {}
        self._current_items: List[ChooserItem] = []
        self._items_version: int = 0  # incremented on every setResults push
        self._closing: bool = False
        self._last_query: str = ""  # Track query for usage recording

        self._usage_tracker = usage_tracker
        self._query_history = None
        self._history_index: int = -1
        self._on_close: Optional[Callable] = None
        self._pending_initial_query: Optional[str] = None
        self._pending_placeholder: Optional[str] = None
        self._event_callback: Optional[Callable] = None  # (event, *args)
        self._snippet_expander = None  # SnippetExpander to suppress on show
        self._previous_app = None  # NSRunningApplication saved on show()
        self._ql_panel = None  # Quick Look preview panel
        self._calc_mode: bool = False  # Calculator pin mode
        self._calc_sticky: bool = False  # Sticky: keep pinned for incomplete expressions
        self._esc_tap = None  # CGEventTap for global ESC
        self._esc_source = None  # CFRunLoopSource for ESC tap
        self._is_expanded: bool = False  # Panel height state
        self._show_preview: bool = False  # Preview panel visibility
        self._compact_results: bool = False  # Compact height for calc-only results
        self._active_source: Optional[ChooserSource] = None  # currently prefix-activated source

    # ------------------------------------------------------------------
    # Panel resize (collapsed ↔ expanded, narrow ↔ wide)
    # ------------------------------------------------------------------

    def _resize_panel(
        self,
        expanded: bool,
        show_preview: Optional[bool] = None,
        compact: Optional[bool] = None,
    ) -> None:
        """Resize the panel in one step (height and/or width)."""
        if self._panel is None:
            return
        if show_preview is None:
            show_preview = self._show_preview
        if compact is None:
            compact = self._compact_results

        if (
            self._is_expanded == expanded
            and self._show_preview == show_preview
            and self._compact_results == compact
        ):
            return

        self._is_expanded = expanded
        self._show_preview = show_preview
        self._compact_results = compact

        from Foundation import NSMakeRect

        old = self._panel.frame()
        if not expanded:
            new_height = self._PANEL_HEIGHT_COLLAPSED
        elif compact:
            new_height = self._PANEL_HEIGHT_COMPACT
        else:
            new_height = self._PANEL_HEIGHT_EXPANDED
        new_width = (
            self._PANEL_WIDTH_WIDE if show_preview
            else self._PANEL_WIDTH_NARROW
        )
        # Keep the top edge fixed (macOS coords: origin is bottom-left)
        new_y = old.origin.y + old.size.height - new_height
        # Keep horizontally centered
        new_x = old.origin.x + (old.size.width - new_width) / 2
        new_frame = NSMakeRect(new_x, new_y, new_width, new_height)
        self._panel.setFrame_display_(new_frame, True)

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
                if (
                    self._ql_panel is not None
                    and self._ql_panel.is_key_window
                ):
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
        return any(
            item.item_id.startswith("calc:") for item in self._current_items
        )

    def _should_pin_for_calc(self) -> bool:
        """Whether the panel should stay visible for calculator use."""
        return self._has_calc_results() or self._calc_sticky

    def _update_hides_on_deactivate(self) -> None:
        """Set hidesOnDeactivate based on whether calc results are present.

        Must be called preemptively (before the panel loses focus) so the
        panel stays visible when the app deactivates.
        """
        if self._panel is not None:
            try:
                self._panel.setHidesOnDeactivate_(not self._should_pin_for_calc())
            except Exception:
                pass

    def _enter_calc_mode(self) -> None:
        """Pin the panel and listen for a global ESC to dismiss.

        Called from ``_maybe_close`` when the panel loses key-window
        status while calculator results are displayed.
        """
        if self._calc_mode:
            return
        self._calc_mode = True
        self._previous_app = None  # Don't reactivate a stale app on close
        restore_accessory()
        self._start_esc_tap()
        logger.debug("Entered calculator pin mode")

    def _exit_calc_mode(self) -> None:
        """Stop the ESC listener and reset the calc-mode flag.

        Does NOT change ``hidesOnDeactivate`` — that is managed solely
        by ``_update_hides_on_deactivate`` (driven by search results).
        """
        if not self._calc_mode:
            return
        self._calc_mode = False
        self._stop_esc_tap()
        logger.debug("Exited calculator pin mode")

    def _start_esc_tap(self) -> None:
        """Create a CGEventTap on the main run loop that swallows ESC."""
        try:
            import Quartz
        except ImportError:
            logger.warning("Quartz not available, cannot create ESC tap")
            self.close()
            return

        _kCGEventKeyDown = Quartz.kCGEventKeyDown
        _kCGKeyboardEventKeycode = Quartz.kCGKeyboardEventKeycode
        _ESC_KEYCODE = 53

        def _esc_callback(proxy, event_type, event, refcon):
            try:
                if event_type == Quartz.kCGEventTapDisabledByTimeout:
                    if self._esc_tap is not None:
                        Quartz.CGEventTapEnable(self._esc_tap, True)
                    return event
                if event_type == _kCGEventKeyDown:
                    keycode = Quartz.CGEventGetIntegerValueField(
                        event, _kCGKeyboardEventKeycode,
                    )
                    if keycode == _ESC_KEYCODE:
                        # Disable tap immediately to prevent auto-repeat
                        # from queuing multiple close() calls
                        if self._esc_tap is not None:
                            Quartz.CGEventTapEnable(self._esc_tap, False)
                        from PyObjCTools import AppHelper

                        AppHelper.callAfter(self.close)
                        return None  # Swallow ESC
            except Exception:
                logger.warning("ESC tap callback error", exc_info=True)
            return event

        mask = Quartz.CGEventMaskBit(_kCGEventKeyDown)
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            mask,
            _esc_callback,
            None,
        )
        if tap is None:
            logger.warning(
                "Failed to create ESC event tap — closing panel instead"
            )
            self.close()
            return

        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        loop = Quartz.CFRunLoopGetMain()
        Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopDefaultMode)
        Quartz.CGEventTapEnable(tap, True)

        self._esc_tap = tap
        self._esc_source = source
        logger.debug("ESC event tap started on main run loop")

    def _stop_esc_tap(self) -> None:
        """Disable and remove the ESC event tap."""
        if self._esc_tap is None:
            return
        try:
            import Quartz

            Quartz.CGEventTapEnable(self._esc_tap, False)
            if self._esc_source is not None:
                loop = Quartz.CFRunLoopGetMain()
                Quartz.CFRunLoopRemoveSource(
                    loop, self._esc_source, Quartz.kCFRunLoopDefaultMode,
                )
        except Exception:
            logger.warning("Failed to stop ESC tap", exc_info=True)
        self._esc_tap = None
        self._esc_source = None
        logger.debug("ESC event tap stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_visible(self) -> bool:
        return self._panel is not None and self._panel.isVisible()

    def show(
        self,
        on_close: Optional[Callable] = None,
        initial_query: Optional[str] = None,
        placeholder: Optional[str] = None,
    ) -> None:
        """Show the chooser panel. Must run on main thread.

        Args:
            on_close: Callback invoked when the panel closes.
            initial_query: If set, pre-fill the search input with this value
                and trigger a search immediately after the page loads.
            placeholder: If set, override the search input placeholder text.
        """
        self._on_close = on_close
        self._pending_initial_query = initial_query
        self._pending_placeholder = placeholder

        if self._panel is not None and self._panel.isVisible():
            # Already visible — apply initial query if provided, else focus
            if initial_query:
                self._eval_js(
                    f"setInputValue({json.dumps(initial_query)})"
                )
            else:
                self._eval_js("focusInput()")
            self._panel.makeKeyAndOrderFront_(None)
            from AppKit import NSApp
            NSApp.activateIgnoringOtherApps_(True)
            return

        self._previous_app = get_frontmost_app()

        self._build_panel()
        self._panel.makeKeyAndOrderFront_(None)

        from AppKit import NSApp
        NSApp.setActivationPolicy_(0)  # Regular (foreground)
        NSApp.activateIgnoringOtherApps_(True)

        if self._snippet_expander is not None:
            self._snippet_expander.suppress()

        self._fire_event("open")

    def close(self) -> None:
        """Close the chooser panel."""
        if self._closing:
            return
        self._closing = True

        if self._snippet_expander is not None:
            self._snippet_expander.resume()
        self._calc_sticky = False
        self._exit_calc_mode()

        if self._ql_panel is not None:
            self._ql_panel.close()
            self._ql_panel = None

        if self._webview is not None:
            self._webview.setNavigationDelegate_(None)
            # Remove the script message handler to break the reference cycle
            try:
                config = self._webview.configuration()
                if config:
                    config.userContentController().removeScriptMessageHandlerForName_(
                        "chooser"
                    )
            except Exception:
                pass
        if self._message_handler is not None:
            self._message_handler._panel_ref = None
        if self._navigation_delegate is not None:
            self._navigation_delegate._panel_ref = None
        if self._panel is not None:
            self._panel.setDelegate_(None)
            self._panel_delegate = None
            self._panel.orderOut_(None)
            self._panel = None
        self._webview = None
        self._message_handler = None
        self._navigation_delegate = None
        self._page_loaded = False
        self._pending_js = []
        self._current_items = []
        self._history_index = -1
        self._is_expanded = False
        self._show_preview = False
        self._compact_results = False
        self._closing = False

        # Reactivate the previous app's focused window, then restore accessory mode.
        # Order matters: activate first (without AllWindows) so macOS doesn't
        # trigger its own all-windows activation when we drop to accessory.
        from PyObjCTools import AppHelper

        previous_app = self._previous_app
        self._previous_app = None

        def _activate_prev():
            reactivate_app(previous_app)

        def _go_accessory():
            restore_accessory()

        AppHelper.callAfter(_activate_prev)
        AppHelper.callAfter(_go_accessory)

        self._fire_event("close")

        callback = self._on_close
        self._on_close = None
        if callback is not None:
            callback()

    def toggle(self, on_close: Optional[Callable] = None) -> None:
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
        """
        self._last_query = query
        source = None

        # Check for prefix activation (Alfred-style: "prefix query")
        for src in self._sources.values():
            if src.prefix:
                trigger = src.prefix + " "
                if query.startswith(trigger):
                    source = src
                    query = query[len(trigger):]
                    break

        # Track active source and toggle create button in JS
        prev_source = self._active_source
        self._active_source = source
        if source != prev_source:
            has_create = (
                source is not None
                and source.create_action is not None
            )
            self._eval_js(
                f"setCreateButton({'true' if has_create else 'false'})"
            )

        # When searching across all non-prefix sources (no specific source),
        # empty query returns nothing. When a specific source is active
        # (e.g. clipboard via prefix), let the source decide.
        if source is None:
            if not query.strip():
                self._current_items = []
                self._calc_sticky = False
                self._compact_results = False
                if self._show_preview:
                    self._resize_panel(self._is_expanded, show_preview=False)
                self._eval_js("setResults([]);setPreviewVisible(false)")
                self._update_hides_on_deactivate()
                return
            all_items = []
            sorted_sources = sorted(
                self._sources.values(),
                key=lambda s: s.priority,
                reverse=True,
            )
            for src in sorted_sources:
                if src.prefix is not None:
                    continue  # Skip prefix-only sources
                if src.search is not None:
                    try:
                        all_items.extend(src.search(query))
                    except Exception:
                        logger.exception("Chooser source %s search error", src.name)
            self._current_items = all_items[:self._MAX_TOTAL_RESULTS]
        else:
            try:
                items = source.search(query) if source.search else []
                self._current_items = items[:self._MAX_TOTAL_RESULTS]
            except Exception:
                logger.exception("Chooser source %s search error", source.name)
                self._current_items = []

        # Apply usage-based boosting
        if self._usage_tracker and self._current_items:
            self._boost_by_usage(query)

        # Update calculator sticky mode
        if self._has_calc_results():
            self._calc_sticky = True
        elif not any(ch.isdigit() for ch in query):
            self._calc_sticky = False

        self._update_hides_on_deactivate()

        # Determine preview mode and compact mode
        # Once in compact mode, stay until input is cleared (handled by
        # the empty-query early return above).
        show_preview = source.show_preview if source is not None else False
        if not self._compact_results:
            compact = (
                bool(self._current_items)
                and all(
                    item.item_id.startswith("calc:")
                    for item in self._current_items
                )
            )
        else:
            compact = True
        if (
            self._show_preview != show_preview
            or self._compact_results != compact
        ):
            self._resize_panel(
                self._is_expanded,
                show_preview=show_preview,
                compact=compact,
            )

        self._push_items_to_js(source=source)

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

    _DEFAULT_ACTION_HINTS = {
        "enter": "Open",
        "cmd_enter": "Reveal",
    }

    def _push_items_to_js(
        self,
        selected_index: Optional[int] = None,
        source=None,
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
                "badge": "",
                "hasReveal": (
                    item.reveal_path is not None
                    or item.secondary_action is not None
                ),
                "hasModifiers": bool(item.modifiers),
                "deletable": item.delete_action is not None,
                "confirmDelete": item.confirm_delete,
            }
            # Include preview only for the selected item to keep payload
            # small while avoiding an extra bridge round-trip.
            sel = (
                selected_index if selected_index is not None else 0
            )
            if len(js_items) == sel and item.preview is not None:
                preview = item.preview
                if callable(preview):
                    try:
                        preview = preview()
                    except Exception:
                        preview = None
                    item.preview = preview  # cache resolved value
                if preview is not None:
                    js_item["preview"] = preview
            js_items.append(js_item)

        # Build a single JS snippet
        parts: list[str] = []

        idx_arg = "" if selected_index is None else f",{selected_index}"
        parts.append(
            f"setResults({json.dumps(js_items, ensure_ascii=False)},"
            f"{self._items_version}{idx_arg})"
        )

        # Use the active source's hints, or fall back to the calculator
        # source's hints when all results are calc items (general search
        # has source=None, so calc hints would otherwise be lost).
        if source is not None and source.action_hints:
            hints = source.action_hints
        elif self._compact_results and "calculator" in self._sources:
            hints = (
                self._sources["calculator"].action_hints
                or self._DEFAULT_ACTION_HINTS
            )
        else:
            hints = self._DEFAULT_ACTION_HINTS
        parts.append(
            f"setActionHints({json.dumps(hints, ensure_ascii=False)})"
        )

        show = "true" if self._show_preview else "false"
        parts.append(f"setPreviewVisible({show})")

        self._eval_js(";".join(parts))

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

        elif msg_type == "modifierChange":
            index = body.get("index", -1)
            modifier = body.get("modifier")
            self._send_modifier_subtitle(index, modifier)

        elif msg_type == "historyUp":
            self._history_navigate(1)

        elif msg_type == "historyDown":
            self._history_navigate(-1)

        elif msg_type == "exitHistory":
            self._history_index = -1

        elif msg_type == "panelResize":
            expanded = body.get("expanded", False)
            self._resize_panel(expanded)

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

        stripped_query = query[len(prefix_str):]
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
                    "Chooser create action failed for source %r", source.name,
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
                        "Chooser delete action failed for %r", item.title,
                    )
                self._fire_event("delete", {
                    "title": item.title,
                    "subtitle": item.subtitle,
                    "item_id": item.item_id,
                })
                self._current_items.pop(index)
                # Keep selection at the same position (clamped by JS)
                self._push_items_to_js(selected_index=index)

    def _execute_item(
        self, index: int, version: int = 0, modifier: Optional[str] = None,
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
                self._usage_tracker.record(self._last_query, item.item_id)

            # Record query history
            if self._query_history and self._last_query and self._last_query.strip():
                self._query_history.record(self._last_query)

            self._fire_event("select", {
                "title": item.title,
                "subtitle": item.subtitle,
                "item_id": item.item_id,
            })

            from PyObjCTools import AppHelper
            AppHelper.callAfter(self.close)
            if action is not None:
                import threading

                def _deferred():
                    import time
                    time.sleep(0.15)  # Let previous app regain focus
                    try:
                        action()
                    except Exception:
                        logger.exception(
                            "Chooser action failed for %r", item.title
                        )

                threading.Thread(target=_deferred, daemon=True).start()

    def _send_modifier_subtitle(
        self, index: int, modifier: Optional[str],
    ) -> None:
        """Send the modifier-specific subtitle to JS for live display."""
        if 0 <= index < len(self._current_items):
            item = self._current_items[index]
            subtitle = item.subtitle
            if modifier and item.modifiers and modifier in item.modifiers:
                subtitle = item.modifiers[modifier].subtitle
            self._eval_js(
                f"setModifierSubtitle({index},"
                f"{json.dumps(subtitle, ensure_ascii=False)})"
            )
        else:
            self._eval_js(f"setModifierSubtitle({index},null)")

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
                    logger.exception(
                        "Chooser secondary action failed for %r", item.title
                    )

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
                    # Cache the resolved result
                    item.preview = preview
                if preview is not None:
                    self._eval_js(
                        f"setPreview("
                        f"{json.dumps(preview, ensure_ascii=False)})"
                    )
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

    def _on_page_loaded(self) -> None:
        """Called when WKWebView finishes loading the HTML."""
        pending = self._pending_js[:]
        self._pending_js.clear()
        self._page_loaded = True
        if pending and self._webview is not None:
            combined = ";".join(pending)
            self._webview.evaluateJavaScript_completionHandler_(combined, None)

        # Push available prefix hints to JS for placeholder display
        self._push_prefix_hints_to_js()

        # Apply custom placeholder (overrides prefix hints default)
        if self._pending_placeholder is not None:
            self._eval_js(
                f"setPlaceholder({json.dumps(self._pending_placeholder)})"
            )
            self._pending_placeholder = None

        # Apply pending initial query (e.g. from source hotkey)
        if self._pending_initial_query is not None:
            query = self._pending_initial_query
            self._pending_initial_query = None
            self._eval_js(f"setInputValue({json.dumps(query)})")

    def _push_prefix_hints_to_js(self) -> None:
        """Send prefix hints to JS so the search placeholder shows them."""
        hints = []
        for src in self._sources.values():
            if src.prefix:
                hints.append(f"{src.prefix} {src.name}")
        self._eval_js(
            f"setPrefixHints({json.dumps(hints, ensure_ascii=False)})"
        )

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
            "Edit", None, "",
        )
        edit_item.setSubmenu_(edit_menu)
        main_menu.addItem_(edit_item)

    def _build_panel(self) -> None:
        """Create NSPanel + WKWebView."""
        from AppKit import (
            NSBackingStoreBuffered,
            NSScreen,
            NSStatusWindowLevel,
        )
        from Foundation import NSMakeRect, NSURL
        from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

        PanelClass = _get_keyable_panel_class()
        initial_expanded = self._pending_initial_query is not None
        self._is_expanded = initial_expanded
        self._show_preview = False  # Always start without preview
        initial_height = (
            self._PANEL_HEIGHT_EXPANDED if initial_expanded
            else self._PANEL_HEIGHT_COLLAPSED
        )
        initial_width = self._PANEL_WIDTH_NARROW
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
        panel.setHidesOnDeactivate_(True)
        panel.setMovableByWindowBackground_(False)
        panel.setCollectionBehavior_(1 << 4)  # canJoinAllSpaces

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
        panel.contentView().layer().setCornerRadius_(12.0)
        panel.contentView().layer().setMasksToBounds_(True)

        # Position: center-top of main screen (like Spotlight)
        # Top edge is always 200px below the screen top, regardless of height
        screen = NSScreen.mainScreen()
        if screen:
            sf = screen.frame()
            x = sf.origin.x + (sf.size.width - initial_width) / 2
            y = sf.origin.y + sf.size.height - initial_height - 200
            panel.setFrameOrigin_((x, y))
        else:
            panel.center()

        # WKWebView with message handler
        wk_config = WKWebViewConfiguration.alloc().init()
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

        self._panel = panel
        self._webview = webview
        self._message_handler = handler
        self._navigation_delegate = nav_delegate
        self._page_loaded = False
        self._pending_js = []
        self._current_items = []

        # Load HTML from a temp file so WKWebView grants file:// access.
        # Icons live in ~/.cache/WenZi and clipboard images in
        # ~/.local/share/WenZi.  WKWebView only accepts a single
        # allowingReadAccessToURL_ directory, and the lowest common ancestor
        # of these two XDG paths is ~/, so we must grant home-wide read
        # access.  This is safe because the web view only loads our own
        # local HTML — no user-controlled URLs are loaded.
        from wenzi.scripting.ui.chooser_html import CHOOSER_HTML
        from wenzi.config import DEFAULT_CACHE_DIR

        cache_dir = os.path.expanduser(DEFAULT_CACHE_DIR)
        os.makedirs(cache_dir, exist_ok=True)
        html_path = os.path.join(cache_dir, "_chooser.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(CHOOSER_HTML)
        home_dir = os.path.expanduser("~")
        webview.loadFileURL_allowingReadAccessToURL_(
            NSURL.fileURLWithPath_(html_path),
            NSURL.fileURLWithPath_(home_dir),
        )
