"""Tests for the web-based history browser panel."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest

from tests.conftest import mock_panel_close_delegate


@pytest.fixture(autouse=True)
def mock_appkit(mock_appkit_modules, monkeypatch):
    """Provide mock AppKit, Foundation, WebKit modules for headless testing."""
    mock_webkit = MagicMock()
    monkeypatch.setitem(sys.modules, "WebKit", mock_webkit)

    import voicetext.ui.history_browser_window_web as _hbw

    _hbw._HistoryBrowserWebCloseDelegate = None
    _hbw._HistoryBrowserWebNavigationDelegate = None
    _hbw._HistoryBrowserWebMessageHandler = None
    mock_panel_close_delegate(monkeypatch, _hbw, "_HistoryBrowserWebCloseDelegate")

    mock_nav_cls = MagicMock()
    mock_nav_instance = MagicMock()
    mock_nav_cls.alloc.return_value.init.return_value = mock_nav_instance
    monkeypatch.setattr(_hbw, "_get_navigation_delegate_class", lambda: mock_nav_cls)

    mock_handler_cls = MagicMock()
    mock_handler_instance = MagicMock()
    mock_handler_cls.alloc.return_value.init.return_value = mock_handler_instance
    monkeypatch.setattr(_hbw, "_get_message_handler_class", lambda: mock_handler_cls)

    return mock_appkit_modules


def _build_panel(panel):
    """Set up a panel with mocked internals for testing."""
    panel._build_panel = MagicMock()
    panel._panel = MagicMock()
    panel._webview = MagicMock()
    panel._page_loaded = True
    return panel


def _get_js_calls(panel):
    """Extract all JS code strings sent to evaluateJavaScript."""
    return [c[0][0] for c in panel._webview.evaluateJavaScript_completionHandler_.call_args_list]


# ---------------------------------------------------------------------------
# Init and lifecycle
# ---------------------------------------------------------------------------


class TestInit:
    def test_defaults(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        assert panel._panel is None
        assert panel._all_records == []
        assert panel._filtered_records == []
        assert panel._selected_index == -1
        assert panel._page_loaded is False
        assert panel._pending_js == []

    def test_close_without_show_is_noop(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel.close()  # Should not raise


class TestShow:
    def test_show_stores_callback(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        history = MagicMock()
        history.get_all.return_value = []
        on_save = MagicMock()

        panel.show(conversation_history=history, on_save=on_save)

        assert panel._conversation_history is history
        assert panel._on_save is on_save


class TestClose:
    def test_close_clears_state(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        history = MagicMock()
        history.get_all.return_value = []
        panel.show(conversation_history=history)

        panel.close()

        assert panel._panel is None
        assert panel._webview is None
        assert panel._page_loaded is False
        assert panel._pending_js == []


# ---------------------------------------------------------------------------
# Filtering logic
# ---------------------------------------------------------------------------


class TestApplyFilters:
    def test_mode_filter(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel, _MODE_ALL

        panel = HistoryBrowserPanel()
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a"},
            {"timestamp": "t2", "enhance_mode": "translate_en", "final_text": "b"},
            {"timestamp": "t3", "enhance_mode": "proofread", "final_text": "c"},
        ]

        panel._filter_mode = _MODE_ALL
        panel._apply_filters()
        assert len(panel._filtered_records) == 3

        panel._filter_mode = "proofread"
        panel._apply_filters()
        assert len(panel._filtered_records) == 2
        assert all(r["enhance_mode"] == "proofread" for r in panel._filtered_records)

    def test_model_filter(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel, _MODEL_ALL

        panel = HistoryBrowserPanel()
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a", "stt_model": "whisper", "llm_model": "gpt-4"},
            {"timestamp": "t2", "enhance_mode": "proofread", "final_text": "b", "stt_model": "whisper", "llm_model": "qwen"},
            {"timestamp": "t3", "enhance_mode": "off", "final_text": "c", "stt_model": "funASR", "llm_model": ""},
        ]

        panel._filter_model = _MODEL_ALL
        panel._apply_filters()
        assert len(panel._filtered_records) == 3

        panel._filter_model = "whisper"
        panel._apply_filters()
        assert len(panel._filtered_records) == 2

        panel._filter_model = "gpt-4"
        panel._apply_filters()
        assert len(panel._filtered_records) == 1
        assert panel._filtered_records[0]["timestamp"] == "t1"

    def test_corrected_filter(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a", "user_corrected": True},
            {"timestamp": "t2", "enhance_mode": "proofread", "final_text": "b", "user_corrected": False},
            {"timestamp": "t3", "enhance_mode": "proofread", "enhanced_text": "x", "final_text": "y"},
        ]

        panel._filter_corrected_only = False
        panel._apply_filters()
        assert len(panel._filtered_records) == 3

        panel._filter_corrected_only = True
        panel._apply_filters()
        assert len(panel._filtered_records) == 2
        assert panel._filtered_records[0]["timestamp"] == "t1"
        assert panel._filtered_records[1]["timestamp"] == "t3"

    def test_combined_filters(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a", "stt_model": "w", "llm_model": "gpt-4"},
            {"timestamp": "t2", "enhance_mode": "translate_en", "final_text": "b", "stt_model": "w", "llm_model": "gpt-4"},
            {"timestamp": "t3", "enhance_mode": "proofread", "final_text": "c", "stt_model": "w", "llm_model": "qwen"},
        ]

        panel._filter_mode = "proofread"
        panel._filter_model = "gpt-4"
        panel._apply_filters()
        assert len(panel._filtered_records) == 1
        assert panel._filtered_records[0]["timestamp"] == "t1"


# ---------------------------------------------------------------------------
# JS message handling
# ---------------------------------------------------------------------------


class TestJsMessages:
    def test_search(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        history = MagicMock()
        history.get_all.return_value = []
        history.search.return_value = [{"timestamp": "t1", "enhance_mode": "off", "final_text": "found"}]
        panel._conversation_history = history

        panel._handle_js_message({"type": "search", "text": "found"})

        assert panel._search_text == "found"
        history.search.assert_called_once_with("found", limit=500)

    def test_filter_mode(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a"},
            {"timestamp": "t2", "enhance_mode": "translate_en", "final_text": "b"},
        ]

        panel._handle_js_message({"type": "filterMode", "mode": "proofread"})

        assert panel._filter_mode == "proofread"
        assert len(panel._filtered_records) == 1
        assert panel._selected_index == -1

    def test_filter_model(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "off", "final_text": "a", "stt_model": "w", "llm_model": ""},
            {"timestamp": "t2", "enhance_mode": "off", "final_text": "b", "stt_model": "f", "llm_model": ""},
        ]

        panel._handle_js_message({"type": "filterModel", "model": "w"})

        assert panel._filter_model == "w"
        assert len(panel._filtered_records) == 1

    def test_filter_corrected(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "off", "final_text": "a", "user_corrected": True},
            {"timestamp": "t2", "enhance_mode": "off", "final_text": "b", "user_corrected": False},
        ]

        panel._handle_js_message({"type": "filterCorrected", "enabled": True})

        assert panel._filter_corrected_only is True
        assert len(panel._filtered_records) == 1

    def test_select_row(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        record = {"timestamp": "t1", "enhance_mode": "proofread", "asr_text": "hi", "final_text": "hello"}
        panel._filtered_records = [record]

        panel._handle_js_message({"type": "selectRow", "index": 0})

        assert panel._selected_index == 0
        calls = _get_js_calls(panel)
        assert any("showDetail" in c for c in calls)

    def test_select_row_out_of_range(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        panel._filtered_records = []

        panel._handle_js_message({"type": "selectRow", "index": 5})

        assert panel._selected_index == -1
        calls = _get_js_calls(panel)
        assert any("clearDetail" in c for c in calls)

    def test_save(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        history = MagicMock()
        history.update_final_text.return_value = True
        panel._conversation_history = history
        on_save = MagicMock()
        panel._on_save = on_save
        panel._filtered_records = [{"timestamp": "t1", "enhance_mode": "off", "final_text": "old"}]
        panel._selected_index = 0

        panel._handle_js_message({"type": "save", "timestamp": "t1", "text": "new"})

        history.update_final_text.assert_called_once_with("t1", "new")
        assert panel._filtered_records[0]["final_text"] == "new"
        on_save.assert_called_once_with("t1", "new")
        calls = _get_js_calls(panel)
        assert any("markSaved" in c for c in calls)

    def test_save_no_selection(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        history = MagicMock()
        panel._conversation_history = history
        panel._selected_index = -1

        panel._handle_js_message({"type": "save", "timestamp": "t1", "text": "new"})

        history.update_final_text.assert_not_called()

    def test_save_failed(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        history = MagicMock()
        history.update_final_text.return_value = False
        panel._conversation_history = history
        on_save = MagicMock()
        panel._on_save = on_save
        panel._filtered_records = [{"timestamp": "t1", "enhance_mode": "off", "final_text": "old"}]
        panel._selected_index = 0

        panel._handle_js_message({"type": "save", "timestamp": "t1", "text": "new"})

        assert panel._filtered_records[0]["final_text"] == "old"
        on_save.assert_not_called()

    def test_close_message(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        panel._handle_js_message({"type": "close"})

        assert panel._panel is None


# ---------------------------------------------------------------------------
# JS call queue (page load race condition)
# ---------------------------------------------------------------------------


class TestJsCallQueue:
    def test_eval_js_queued_before_page_load(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._webview = MagicMock()
        panel._page_loaded = False

        panel._eval_js("setRecords([])")

        panel._webview.evaluateJavaScript_completionHandler_.assert_not_called()
        assert len(panel._pending_js) == 1

    def test_pending_js_flushed_on_page_load(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._webview = MagicMock()
        panel._page_loaded = False

        panel._eval_js("setRecords([])")
        panel._eval_js("setFilterOptions([],[])")

        panel._on_page_loaded()

        assert panel._page_loaded is True
        assert len(panel._pending_js) == 0
        panel._webview.evaluateJavaScript_completionHandler_.assert_called_once()
        combined = panel._webview.evaluateJavaScript_completionHandler_.call_args[0][0]
        assert "setRecords" in combined
        assert "setFilterOptions" in combined

    def test_eval_js_direct_after_page_load(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._webview = MagicMock()
        panel._page_loaded = True

        panel._eval_js("someCall()")

        panel._webview.evaluateJavaScript_completionHandler_.assert_called_once()
        assert len(panel._pending_js) == 0

    def test_close_clears_pending_js(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        panel._page_loaded = False

        panel._eval_js("someCall()")
        assert len(panel._pending_js) == 1

        panel.close()

        assert panel._page_loaded is False
        assert len(panel._pending_js) == 0

    def test_flush_order_preserved(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._webview = MagicMock()
        panel._page_loaded = False

        panel._eval_js("first()")
        panel._eval_js("second()")
        panel._eval_js("third()")

        panel._on_page_loaded()

        combined = panel._webview.evaluateJavaScript_completionHandler_.call_args[0][0]
        assert combined == "first();second();third()"


# ---------------------------------------------------------------------------
# Push data to JS
# ---------------------------------------------------------------------------


class TestPushData:
    def test_push_records_includes_corrected_flag(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        panel._filtered_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a", "user_corrected": True},
        ]

        panel._push_records()

        calls = _get_js_calls(panel)
        set_records_calls = [c for c in calls if c.startswith("setRecords(")]
        assert len(set_records_calls) == 1
        data = json.loads(set_records_calls[0][len("setRecords(") : -1])
        assert data[0]["_corrected"] is True

    def test_push_filter_options(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "stt_model": "whisper", "llm_model": "gpt-4"},
            {"timestamp": "t2", "enhance_mode": "translate_en", "stt_model": "whisper", "llm_model": ""},
        ]

        panel._push_filter_options()

        calls = _get_js_calls(panel)
        filter_calls = [c for c in calls if c.startswith("setFilterOptions(")]
        assert len(filter_calls) == 1
        assert "proofread" in filter_calls[0]
        assert "translate_en" in filter_calls[0]
        assert "whisper" in filter_calls[0]
        assert "gpt-4" in filter_calls[0]


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------


class TestHtmlTemplate:
    def test_has_dark_mode_support(self):
        from voicetext.ui.history_browser_window_web import _HTML_TEMPLATE

        assert "prefers-color-scheme: dark" in _HTML_TEMPLATE

    def test_has_key_ui_elements(self):
        from voicetext.ui.history_browser_window_web import _HTML_TEMPLATE

        for elem_id in ("search", "mode-filter", "model-filter", "corrected-cb", "table-body", "save-btn", "close-btn"):
            assert elem_id in _HTML_TEMPLATE

    def test_has_keyboard_shortcuts(self):
        from voicetext.ui.history_browser_window_web import _HTML_TEMPLATE

        assert "Escape" in _HTML_TEMPLATE


class TestFormatTimestamp:
    def test_full_iso(self):
        from voicetext.ui.history_browser_window_web import _format_timestamp

        assert _format_timestamp("2026-03-13T14:30:00+00:00") == "2026-03-13 14:30"

    def test_short(self):
        from voicetext.ui.history_browser_window_web import _format_timestamp

        assert _format_timestamp("2026-01-01T09:05:00") == "2026-01-01 09:05"

    def test_empty(self):
        from voicetext.ui.history_browser_window_web import _format_timestamp

        assert _format_timestamp("") == ""
