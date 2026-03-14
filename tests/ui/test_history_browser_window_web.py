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
        assert panel._time_range == "7d"
        assert panel._active_tags == set()

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
# Time range cutoff
# ---------------------------------------------------------------------------


class TestTimeRangeCutoff:
    def test_all_returns_none(self):
        from voicetext.ui.history_browser_window_web import _time_range_cutoff

        assert _time_range_cutoff("all") is None

    def test_7d_returns_iso_string(self):
        from voicetext.ui.history_browser_window_web import _time_range_cutoff

        result = _time_range_cutoff("7d")
        assert result is not None
        assert "T" in result  # ISO format

    def test_30d_returns_iso_string(self):
        from voicetext.ui.history_browser_window_web import _time_range_cutoff

        result = _time_range_cutoff("30d")
        assert result is not None

    def test_today_returns_iso_string(self):
        from voicetext.ui.history_browser_window_web import _time_range_cutoff

        result = _time_range_cutoff("today")
        assert result is not None
        # Should be midnight today
        assert "T00:00:00" in result


# ---------------------------------------------------------------------------
# Filtering logic
# ---------------------------------------------------------------------------


class TestApplyFilters:
    def test_no_filters(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._time_range = "all"
        panel._all_records = [
            {"timestamp": "2026-03-13T14:30:00+00:00", "enhance_mode": "proofread", "final_text": "a"},
            {"timestamp": "2026-03-13T14:25:00+00:00", "enhance_mode": "translate_en", "final_text": "b"},
        ]

        panel._apply_filters()
        assert len(panel._filtered_records) == 2

    def test_time_range_filter(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._time_range = "7d"
        panel._all_records = [
            {"timestamp": "2099-01-01T00:00:00+00:00", "enhance_mode": "off", "final_text": "future"},
            {"timestamp": "2020-01-01T00:00:00+00:00", "enhance_mode": "off", "final_text": "old"},
        ]

        panel._apply_filters()
        # Only the future record passes the 7d cutoff
        assert len(panel._filtered_records) == 1
        assert panel._filtered_records[0]["final_text"] == "future"

    def test_tag_filter_mode(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._time_range = "all"
        panel._active_tags = {"proofread"}
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a"},
            {"timestamp": "t2", "enhance_mode": "translate_en", "final_text": "b"},
            {"timestamp": "t3", "enhance_mode": "proofread", "final_text": "c"},
        ]

        panel._apply_filters()
        assert len(panel._filtered_records) == 2
        assert all(r["enhance_mode"] == "proofread" for r in panel._filtered_records)

    def test_tag_filter_corrected(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._time_range = "all"
        panel._active_tags = {"corrected"}
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a", "user_corrected": True},
            {"timestamp": "t2", "enhance_mode": "proofread", "final_text": "b", "user_corrected": False},
        ]

        panel._apply_filters()
        assert len(panel._filtered_records) == 1
        assert panel._filtered_records[0]["timestamp"] == "t1"

    def test_tag_filter_or_logic(self):
        """Multiple active tags use OR: match any."""
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._time_range = "all"
        panel._active_tags = {"proofread", "translate_en"}
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a"},
            {"timestamp": "t2", "enhance_mode": "translate_en", "final_text": "b"},
            {"timestamp": "t3", "enhance_mode": "format", "final_text": "c"},
        ]

        panel._apply_filters()
        assert len(panel._filtered_records) == 2

    def test_tag_filter_model(self):
        """Model names can be used as tags to filter."""
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._time_range = "all"
        panel._active_tags = {"whisper"}
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a", "stt_model": "whisper", "llm_model": "gpt-4"},
            {"timestamp": "t2", "enhance_mode": "proofread", "final_text": "b", "stt_model": "funASR", "llm_model": "gpt-4"},
            {"timestamp": "t3", "enhance_mode": "off", "final_text": "c", "stt_model": "funASR", "llm_model": ""},
        ]

        panel._apply_filters()
        assert len(panel._filtered_records) == 1
        assert panel._filtered_records[0]["stt_model"] == "whisper"

    def test_tag_filter_llm_model(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._time_range = "all"
        panel._active_tags = {"gpt-4"}
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a", "stt_model": "w", "llm_model": "gpt-4"},
            {"timestamp": "t2", "enhance_mode": "proofread", "final_text": "b", "stt_model": "w", "llm_model": "claude"},
        ]

        panel._apply_filters()
        assert len(panel._filtered_records) == 1
        assert panel._filtered_records[0]["llm_model"] == "gpt-4"

    def test_combined_time_and_tag(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._time_range = "7d"
        panel._active_tags = {"proofread"}
        panel._all_records = [
            {"timestamp": "2099-01-01T00:00:00+00:00", "enhance_mode": "proofread", "final_text": "a"},
            {"timestamp": "2099-01-01T00:00:00+00:00", "enhance_mode": "translate_en", "final_text": "b"},
            {"timestamp": "2020-01-01T00:00:00+00:00", "enhance_mode": "proofread", "final_text": "c"},
        ]

        panel._apply_filters()
        # Only future proofread passes both filters
        assert len(panel._filtered_records) == 1
        assert panel._filtered_records[0]["final_text"] == "a"


# ---------------------------------------------------------------------------
# JS message handling
# ---------------------------------------------------------------------------


class TestJsMessages:
    def test_search_with_time_range(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        history = MagicMock()
        history.get_all.return_value = []
        history.search.return_value = []
        panel._conversation_history = history

        panel._handle_js_message({"type": "search", "text": "hello", "timeRange": "30d"})

        assert panel._search_text == "hello"
        assert panel._time_range == "30d"
        history.search.assert_called_once_with("hello", limit=500)

    def test_toggle_tags(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        panel._time_range = "all"
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a"},
            {"timestamp": "t2", "enhance_mode": "translate_en", "final_text": "b"},
        ]

        panel._handle_js_message({"type": "toggleTags", "tags": ["proofread"]})

        assert panel._active_tags == {"proofread"}
        assert len(panel._filtered_records) == 1
        assert panel._selected_index == -1

    def test_clear_filters(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        history = MagicMock()
        history.get_all.return_value = []
        panel._conversation_history = history
        panel._search_text = "old"
        panel._time_range = "30d"
        panel._active_tags = {"proofread"}

        panel._handle_js_message({"type": "clearFilters"})

        assert panel._search_text == ""
        assert panel._time_range == "7d"
        assert panel._active_tags == set()
        calls = _get_js_calls(panel)
        assert any("resetFilters" in c for c in calls)

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

    def test_delete(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        history = MagicMock()
        history.delete_record.return_value = True
        panel._conversation_history = history
        panel._time_range = "all"
        rec = {"timestamp": "t1", "enhance_mode": "off", "final_text": "old"}
        panel._all_records = [rec]
        panel._filtered_records = [rec]
        panel._selected_index = 0

        panel._handle_js_message({"type": "delete", "timestamp": "t1"})

        history.delete_record.assert_called_once_with("t1")
        assert len(panel._filtered_records) == 0
        assert len(panel._all_records) == 0
        assert panel._selected_index == -1

    def test_delete_no_selection(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        history = MagicMock()
        panel._conversation_history = history
        panel._selected_index = -1

        panel._handle_js_message({"type": "delete", "timestamp": "t1"})

        history.delete_record.assert_not_called()

    def test_delete_failed(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        history = MagicMock()
        history.delete_record.return_value = False
        panel._conversation_history = history
        panel._time_range = "all"
        rec = {"timestamp": "t1", "enhance_mode": "off", "final_text": "old"}
        panel._all_records = [rec]
        panel._filtered_records = [rec]
        panel._selected_index = 0

        panel._handle_js_message({"type": "delete", "timestamp": "t1"})

        assert len(panel._filtered_records) == 1

    def test_close_message(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        panel._handle_js_message({"type": "close"})

        assert panel._panel is None


# ---------------------------------------------------------------------------
# JS call queue
# ---------------------------------------------------------------------------


class TestJsCallQueue:
    def test_eval_js_queued_before_page_load(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._webview = MagicMock()
        panel._page_loaded = False

        panel._eval_js("setRecords([],0)")

        panel._webview.evaluateJavaScript_completionHandler_.assert_not_called()
        assert len(panel._pending_js) == 1

    def test_pending_js_flushed_on_page_load(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._webview = MagicMock()
        panel._page_loaded = False

        panel._eval_js("setRecords([],0)")
        panel._eval_js("setTagOptions([])")

        panel._on_page_loaded()

        assert panel._page_loaded is True
        assert len(panel._pending_js) == 0
        panel._webview.evaluateJavaScript_completionHandler_.assert_called_once()
        combined = panel._webview.evaluateJavaScript_completionHandler_.call_args[0][0]
        assert "setRecords" in combined
        assert "setTagOptions" in combined

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
    def test_push_records_includes_corrected_flag_and_total(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a", "user_corrected": True},
            {"timestamp": "t2", "enhance_mode": "proofread", "final_text": "b", "user_corrected": False},
        ]
        panel._filtered_records = panel._all_records[:1]

        panel._push_records()

        calls = _get_js_calls(panel)
        set_records_calls = [c for c in calls if c.startswith("setRecords(")]
        assert len(set_records_calls) == 1
        # Should contain total count as second arg
        assert ",2)" in set_records_calls[0]

    def test_push_tag_options(self):
        from voicetext.ui.history_browser_window_web import HistoryBrowserPanel

        panel = _build_panel(HistoryBrowserPanel())
        panel._time_range = "all"
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "user_corrected": True,
             "final_text": "a", "stt_model": "whisper", "llm_model": "gpt-4"},
            {"timestamp": "t2", "enhance_mode": "translate_en", "user_corrected": False,
             "final_text": "b", "stt_model": "whisper", "llm_model": "claude"},
            {"timestamp": "t3", "enhance_mode": "proofread", "user_corrected": False,
             "final_text": "c", "stt_model": "funASR", "llm_model": ""},
        ]

        panel._push_tag_options()

        calls = _get_js_calls(panel)
        tag_calls = [c for c in calls if c.startswith("setTagOptions(")]
        assert len(tag_calls) == 1
        data = json.loads(tag_calls[0][len("setTagOptions("):-1])
        names = [t["name"] for t in data]
        # Mode tags
        assert "proofread" in names
        assert "translate_en" in names
        # Model tags
        assert "whisper" in names
        assert "funASR" in names
        assert "gpt-4" in names
        assert "claude" in names
        # Special
        assert "corrected" in names
        # Check counts and groups
        by_name = {t["name"]: t for t in data}
        assert by_name["proofread"]["count"] == 2
        assert by_name["proofread"]["group"] == "mode"
        assert by_name["whisper"]["count"] == 2
        assert by_name["whisper"]["group"] == "stt"
        assert by_name["gpt-4"]["count"] == 1
        assert by_name["gpt-4"]["group"] == "llm"
        assert by_name["corrected"]["group"] == "special"


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------


class TestHtmlTemplate:
    def test_has_dark_mode_support(self):
        from voicetext.ui.history_browser_window_web import _HTML_TEMPLATE

        assert "prefers-color-scheme: dark" in _HTML_TEMPLATE

    def test_has_key_ui_elements(self):
        from voicetext.ui.history_browser_window_web import _HTML_TEMPLATE

        for elem_id in ("search", "time-range", "clear-btn",
                         "tag-row", "stats-line", "table-body",
                         "save-btn", "close-btn"):
            assert elem_id in _HTML_TEMPLATE

    def test_has_tag_filter_row(self):
        from voicetext.ui.history_browser_window_web import _HTML_TEMPLATE

        assert "tag-pill" in _HTML_TEMPLATE
        assert "tag-row" in _HTML_TEMPLATE

    def test_has_keyboard_shortcuts(self):
        from voicetext.ui.history_browser_window_web import _HTML_TEMPLATE

        assert "Escape" in _HTML_TEMPLATE
        assert "metaKey" in _HTML_TEMPLATE

    def test_has_table_columns(self):
        from voicetext.ui.history_browser_window_web import _HTML_TEMPLATE

        for col in ("col-time", "col-mode", "col-content", "col-tags"):
            assert col in _HTML_TEMPLATE


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
