"""Chooser panel — Alfred/Raycast-style quick launcher.

Uses NSPanel + WKWebView for a search-and-filter UI.
Keyboard-driven: type to filter, ↑↓ to navigate, Enter to execute.
"""

from __future__ import annotations

import json
import logging
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
    """Return an NSObject subclass that closes the panel on focus loss."""
    global _PanelDelegate
    if _PanelDelegate is not None:
        return _PanelDelegate

    from Foundation import NSObject

    class ChooserPanelDelegate(NSObject):
        _panel_ref = None

        def windowDidResignKey_(self, notification):
            if self._panel_ref is not None:
                self._panel_ref.close()

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

    _PANEL_WIDTH = 960
    _PANEL_HEIGHT = 400
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
        self._on_close: Optional[Callable] = None
        self._pending_initial_query: Optional[str] = None
        self._pending_placeholder: Optional[str] = None
        self._event_callback: Optional[Callable] = None  # (event, *args)
        self._previous_app = None  # NSRunningApplication saved on show()

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

        self._fire_event("open")

    def close(self) -> None:
        """Close the chooser panel."""
        if self._closing:
            return
        self._closing = True

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

        # When searching across all non-prefix sources (no specific source),
        # empty query returns nothing. When a specific source is active
        # (e.g. clipboard via prefix), let the source decide.
        if source is None:
            if not query.strip():
                self._current_items = []
                self._eval_js("setResults([])")
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

        self._push_items_to_js()

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

    def _push_items_to_js(self, selected_index: Optional[int] = None) -> None:
        """Serialize current items and send to the web view."""
        self._items_version += 1
        js_items = []
        for item in self._current_items:
            js_item = {
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
            }
            if item.preview is not None:
                js_item["preview"] = item.preview
            js_items.append(js_item)
        idx_arg = "" if selected_index is None else f",{selected_index}"
        self._eval_js(
            f"setResults({json.dumps(js_items, ensure_ascii=False)},"
            f"{self._items_version}{idx_arg})"
        )

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

        elif msg_type == "modifierChange":
            index = body.get("index", -1)
            modifier = body.get("modifier")
            self._send_modifier_subtitle(index, modifier)

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
            if item.preview is not None:
                self._eval_js(
                    f"setPreview({json.dumps(item.preview, ensure_ascii=False)})"
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
        panel = PanelClass.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
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
        screen = NSScreen.mainScreen()
        if screen:
            sf = screen.frame()
            x = sf.origin.x + (sf.size.width - self._PANEL_WIDTH) / 2
            y = sf.origin.y + sf.size.height - self._PANEL_HEIGHT - 200
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
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
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

        # Load HTML
        from wenzi.scripting.ui.chooser_html import CHOOSER_HTML

        webview.loadHTMLString_baseURL_(
            CHOOSER_HTML, NSURL.fileURLWithPath_("/")
        )
