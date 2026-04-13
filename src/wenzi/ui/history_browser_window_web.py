"""Web-based history browser panel using WKWebView.

Drop-in replacement for the AppKit-based HistoryBrowserPanel, with the
same public API surface.  See dev/wkwebview-pitfalls.md for background.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from wenzi.ui.templates import load_template
from wenzi.ui.web_utils import cleanup_webview
from wenzi.ui.web_utils import time_range_cutoff as _time_range_cutoff

logger = logging.getLogger(__name__)


def _format_timestamp(ts: str) -> str:
    """Format ISO timestamp as 'YYYY-MM-DD HH:MM'."""
    try:
        return ts[:16].replace("T", " ")
    except Exception:
        return ts


# ---------------------------------------------------------------------------
# NSObject subclasses (lazy-created, unique class names)
# ---------------------------------------------------------------------------

def _get_panel_close_delegate_class():
    from wenzi.ui.web_utils import make_panel_close_delegate_class

    return make_panel_close_delegate_class("HistoryBrowserWebCloseDelegate")


_HistoryBrowserWebNavigationDelegate = None


def _get_navigation_delegate_class():
    global _HistoryBrowserWebNavigationDelegate
    if _HistoryBrowserWebNavigationDelegate is None:
        from Foundation import NSObject

        class HistoryBrowserWebNavigationDelegate(NSObject):
            _panel_ref = None

            def webView_didFinishNavigation_(self, webview, navigation):
                if self._panel_ref is not None:
                    self._panel_ref._on_page_loaded()

        _HistoryBrowserWebNavigationDelegate = HistoryBrowserWebNavigationDelegate
    return _HistoryBrowserWebNavigationDelegate


_HistoryBrowserWebMessageHandler = None


def _get_message_handler_class():
    global _HistoryBrowserWebMessageHandler
    if _HistoryBrowserWebMessageHandler is None:
        import json as _json

        import objc
        import WebKit  # noqa: F401
        from Foundation import NSObject

        WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")

        class HistoryBrowserWebMessageHandler(NSObject, protocols=[WKScriptMessageHandler]):
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

        _HistoryBrowserWebMessageHandler = HistoryBrowserWebMessageHandler
    return _HistoryBrowserWebMessageHandler


# ---------------------------------------------------------------------------
# Panel class
# ---------------------------------------------------------------------------


class HistoryBrowserPanel:
    """WKWebView-based floating panel for browsing conversation history.

    Drop-in replacement for the AppKit-based HistoryBrowserPanel.
    """

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

        self._all_records: list[dict[str, Any]] = []
        self._filtered_records: list[dict[str, Any]] = []
        self._selected_index: int = -1
        self._conversation_history = None
        self._on_save: Callable[[str, str], None] | None = None
        self._search_text: str = ""
        self._time_range: str = "7d"
        self._include_archived: bool = False
        self._active_tags: set[str] = set()
        self._page: int = 0
        self._page_size: int = 100

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_visible(self) -> bool:
        return self._panel is not None and self._panel.isVisible()

    def show(
        self,
        conversation_history,
        on_save: Callable[[str, str], None] | None = None,
    ) -> None:
        """Show the history browser panel."""
        from AppKit import NSApp

        self._conversation_history = conversation_history
        self._on_save = on_save

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
        self._all_records = []
        self._filtered_records = []

        if self._conversation_history is not None:
            self._conversation_history.release_full_cache()

        from AppKit import NSApp

        NSApp.setActivationPolicy_(1)  # Accessory

    # ------------------------------------------------------------------
    # Data loading and filtering
    # ------------------------------------------------------------------

    def _reload_data(self) -> None:
        """Reload all records and push to JS."""
        if self._conversation_history is None:
            return
        if self._search_text:
            self._all_records = self._conversation_history.search(
                self._search_text, include_archived=self._include_archived
            )
        else:
            self._all_records = self._conversation_history.get_all(
                include_archived=self._include_archived
            )

        self._apply_filters()
        self._page = 0
        self._selected_index = -1
        self._push_tag_options()
        self._push_records()

    def _apply_filters(self) -> None:
        """Filter _all_records by time range and active tags."""
        from wenzi.enhance.conversation_history import ConversationHistory

        records = self._all_records

        # Time range filter
        cutoff = _time_range_cutoff(self._time_range)
        if cutoff:
            records = [r for r in records if r.get("timestamp", "") >= cutoff]

        # Tag filter (OR logic): show records matching ANY active tag
        if self._active_tags:
            filtered = []
            for r in records:
                mode = r.get("enhance_mode", "off") or "off"
                stt = r.get("stt_model", "")
                llm = r.get("llm_model", "")
                is_corrected = ConversationHistory._is_corrected(r)
                if mode in self._active_tags:
                    filtered.append(r)
                elif stt and stt in self._active_tags:
                    filtered.append(r)
                elif llm and llm in self._active_tags:
                    filtered.append(r)
                elif "corrected" in self._active_tags and is_corrected:
                    filtered.append(r)
            records = filtered

        self._filtered_records = records

    def _push_records(self) -> None:
        """Send current page of filtered records to JS."""
        from wenzi.enhance.conversation_history import ConversationHistory

        filtered_count = len(self._filtered_records)
        total_pages = max(1, (filtered_count + self._page_size - 1) // self._page_size)

        # Clamp page to valid range
        if self._page >= total_pages:
            self._page = total_pages - 1
        if self._page < 0:
            self._page = 0

        start = self._page * self._page_size
        end = start + self._page_size
        page_records = self._filtered_records[start:end]

        records_json = []
        for r in page_records:
            entry = dict(r)
            entry["_corrected"] = ConversationHistory._is_corrected(r)
            records_json.append(entry)

        total = len(self._all_records)
        self._eval_js(
            f"setRecords({json.dumps(records_json, ensure_ascii=False)},"
            f"{total},{self._page},{total_pages},{filtered_count})"
        )

    def _push_tag_options(self) -> None:
        """Send available tag options with counts to JS."""
        from wenzi.enhance.conversation_history import ConversationHistory

        mode_counts: dict[str, int] = {}
        stt_counts: dict[str, int] = {}
        llm_counts: dict[str, int] = {}
        corrected_count = 0
        cutoff = _time_range_cutoff(self._time_range)
        for r in self._all_records:
            if cutoff and r.get("timestamp", "") < cutoff:
                continue
            mode = r.get("enhance_mode", "off") or "off"
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
            stt = r.get("stt_model", "")
            if stt:
                stt_counts[stt] = stt_counts.get(stt, 0) + 1
            llm = r.get("llm_model", "")
            if llm:
                llm_counts[llm] = llm_counts.get(llm, 0) + 1
            if ConversationHistory._is_corrected(r):
                corrected_count += 1

        tags: list[dict[str, Any]] = []
        # Corrected first
        if corrected_count > 0:
            tags.append({"name": "corrected", "count": corrected_count, "group": "special"})
        for m in sorted(mode_counts.keys()):
            tags.append({"name": m, "count": mode_counts[m], "group": "mode"})
        for s in sorted(stt_counts.keys()):
            tags.append({"name": s, "count": stt_counts[s], "group": "stt"})
        for lm in sorted(llm_counts.keys()):
            tags.append({"name": lm, "count": llm_counts[lm], "group": "llm"})
        self._eval_js(f"setTagOptions({json.dumps(tags)})")

    # ------------------------------------------------------------------
    # JS message handler
    # ------------------------------------------------------------------

    def _handle_js_message(self, body: dict) -> None:
        """Dispatch messages from JavaScript."""
        msg_type = body.get("type", "")

        if msg_type == "search":
            self._search_text = body.get("text", "")
            self._time_range = body.get("timeRange", "7d")
            self._include_archived = bool(body.get("includeArchived", False))
            self._reload_data()

        elif msg_type == "toggleTags":
            self._active_tags = set(body.get("tags", []))
            self._apply_filters()
            self._page = 0
            self._selected_index = -1
            self._push_records()
            self._eval_js("clearDetail()")

        elif msg_type == "changePage":
            self._page = body.get("page", 0)
            self._selected_index = -1
            self._push_records()
            self._eval_js("clearDetail()")

        elif msg_type == "clearFilters":
            self._search_text = ""
            self._time_range = "7d"
            self._include_archived = False
            self._active_tags = set()
            self._eval_js("resetFilters()")
            self._reload_data()

        elif msg_type == "selectRow":
            page_index = body.get("index", -1)
            abs_index = self._page * self._page_size + page_index
            if 0 <= abs_index < len(self._filtered_records):
                self._selected_index = abs_index
                record = self._filtered_records[abs_index]
                self._eval_js(f"showDetail({json.dumps(record, ensure_ascii=False)})")
            else:
                self._selected_index = -1
                self._eval_js("clearDetail()")

        elif msg_type == "save":
            self._on_save_clicked(body.get("timestamp", ""), body.get("text", ""))

        elif msg_type == "delete":
            self._on_delete_clicked(body.get("timestamp", ""))

        elif msg_type == "pageSize":
            new_size = body.get("size", self._page_size)
            if new_size != self._page_size or not self._all_records:
                self._page_size = new_size
                if not self._all_records:
                    # Initial load — triggered by JS after measuring layout
                    self._reload_data()
                else:
                    # Resize — re-push with new page size
                    self._page = 0
                    self._selected_index = -1
                    self._push_records()
                    self._eval_js("clearDetail()")

        elif msg_type == "close":
            self.close()

    def _on_save_clicked(self, timestamp: str, new_text: str) -> None:
        """Save edited final_text back to conversation history."""
        if not timestamp or self._conversation_history is None:
            return
        if self._selected_index < 0 or self._selected_index >= len(self._filtered_records):
            return

        ok = self._conversation_history.update_final_text(timestamp, new_text)
        if ok:
            self._filtered_records[self._selected_index]["final_text"] = new_text
            page_index = self._selected_index - self._page * self._page_size
            self._eval_js(f"markSaved({page_index})")
            if self._on_save:
                self._on_save(timestamp, new_text)

    def _on_delete_clicked(self, timestamp: str) -> None:
        """Delete a record from conversation history."""
        if not timestamp or self._conversation_history is None:
            return
        if self._selected_index < 0 or self._selected_index >= len(self._filtered_records):
            return

        ok = self._conversation_history.delete_record(timestamp)
        if ok:
            # Remove from both lists
            deleted = self._filtered_records[self._selected_index]
            self._filtered_records.pop(self._selected_index)
            if deleted in self._all_records:
                self._all_records.remove(deleted)
            self._selected_index = -1
            self._push_tag_options()
            self._push_records()
            self._eval_js("clearDetail()")

    # ------------------------------------------------------------------
    # WKWebView JS bridge
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
        """Flush pending JS calls atomically when page finishes loading."""
        # Inject i18n translations before flushing pending JS
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

        inject_i18n_into_webview(self._webview, "history_web.")

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

        from wenzi.ui.result_window_web import _ensure_edit_menu

        _ensure_edit_menu()

        NSApp.setActivationPolicy_(0)

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
            NSTitledWindowMask | NSClosableWindowMask | NSResizableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setMinSize_(NSMakeSize(800, 550))
        panel.setTitle_("Conversation History")
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

        html = load_template("history_browser_window_web.html")
        webview.loadHTMLString_baseURL_(html, NSURL.URLWithString_("file:///"))
