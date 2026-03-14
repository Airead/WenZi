"""Tests for the history browser window module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.usefixtures("mock_appkit_modules")


class TestHistoryBrowserPanel:
    def test_init(self):
        from voicetext.ui.history_browser_window import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        assert panel._panel is None
        assert panel._all_records == []
        assert panel._filtered_records == []
        assert panel._selected_index == -1

    def test_number_of_rows_empty(self):
        from voicetext.ui.history_browser_window import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        assert panel.numberOfRowsInTableView_(None) == 0

    def test_number_of_rows_with_data(self):
        from voicetext.ui.history_browser_window import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._filtered_records = [
            {"timestamp": "2026-03-13T14:30:00", "asr_text": "hello", "enhance_mode": "off", "final_text": "hello"},
            {"timestamp": "2026-03-13T14:25:00", "asr_text": "world", "enhance_mode": "off", "final_text": "world"},
        ]
        assert panel.numberOfRowsInTableView_(None) == 2

    def test_table_view_object_value_time_full_format(self):
        from voicetext.ui.history_browser_window import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._filtered_records = [
            {"timestamp": "2026-03-13T14:30:00+00:00", "asr_text": "hello",
             "enhance_mode": "proofread", "final_text": "hello world"},
        ]
        col = MagicMock()
        col.identifier.return_value = "time"
        result = panel.tableView_objectValueForTableColumn_row_(None, col, 0)
        assert result == "2026-03-13 14:30"

    def test_table_view_object_value_mode(self):
        from voicetext.ui.history_browser_window import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._filtered_records = [
            {"timestamp": "2026-03-13T14:30:00", "asr_text": "hello",
             "enhance_mode": "proofread", "final_text": "hello"},
        ]
        col = MagicMock()
        col.identifier.return_value = "mode"
        result = panel.tableView_objectValueForTableColumn_row_(None, col, 0)
        assert result == "proofread"

    def test_table_view_object_value_preview_truncated(self):
        from voicetext.ui.history_browser_window import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        long_text = "a" * 100
        panel._filtered_records = [
            {"timestamp": "2026-03-13T14:30:00", "asr_text": "hello", "enhance_mode": "off", "final_text": long_text},
        ]
        col = MagicMock()
        col.identifier.return_value = "preview"
        result = panel.tableView_objectValueForTableColumn_row_(None, col, 0)
        assert len(result) == 80

    def test_table_view_object_value_out_of_range(self):
        from voicetext.ui.history_browser_window import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._filtered_records = []
        col = MagicMock()
        col.identifier.return_value = "time"
        result = panel.tableView_objectValueForTableColumn_row_(None, col, 0)
        assert result == ""

    def test_close_without_panel(self):
        from voicetext.ui.history_browser_window import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        # Should not raise
        panel.close()

    def test_save_clicked_no_selection(self):
        from voicetext.ui.history_browser_window import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._selected_index = -1
        # Should not raise
        panel.saveClicked_(None)

    def test_mode_filter(self):
        from voicetext.ui.history_browser_window import HistoryBrowserPanel, _MODE_ALL

        panel = HistoryBrowserPanel()
        panel._all_records = [
            {"timestamp": "2026-03-13T14:30:00", "enhance_mode": "proofread", "final_text": "a"},
            {"timestamp": "2026-03-13T14:25:00", "enhance_mode": "translate_en", "final_text": "b"},
            {"timestamp": "2026-03-13T14:20:00", "enhance_mode": "proofread", "final_text": "c"},
        ]

        # All mode
        panel._filter_mode = _MODE_ALL
        panel._apply_filters()
        assert len(panel._filtered_records) == 3

        # Filter by proofread
        panel._filter_mode = "proofread"
        panel._apply_filters()
        assert len(panel._filtered_records) == 2
        assert all(r["enhance_mode"] == "proofread" for r in panel._filtered_records)

        # Filter by translate_en
        panel._filter_mode = "translate_en"
        panel._apply_filters()
        assert len(panel._filtered_records) == 1

    def test_model_filter(self):
        from voicetext.ui.history_browser_window import HistoryBrowserPanel, _MODEL_ALL

        panel = HistoryBrowserPanel()
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a",
             "stt_model": "whisper-large", "llm_model": "gpt-4"},
            {"timestamp": "t2", "enhance_mode": "proofread", "final_text": "b",
             "stt_model": "whisper-large", "llm_model": "qwen2.5"},
            {"timestamp": "t3", "enhance_mode": "off", "final_text": "c",
             "stt_model": "whisper-small", "llm_model": ""},
        ]

        # All models
        panel._filter_model = _MODEL_ALL
        panel._apply_filters()
        assert len(panel._filtered_records) == 3

        # Filter by stt_model
        panel._filter_model = "whisper-large"
        panel._apply_filters()
        assert len(panel._filtered_records) == 2

        # Filter by llm_model
        panel._filter_model = "gpt-4"
        panel._apply_filters()
        assert len(panel._filtered_records) == 1
        assert panel._filtered_records[0]["timestamp"] == "t1"

        # Filter by model not present
        panel._filter_model = "nonexistent"
        panel._apply_filters()
        assert len(panel._filtered_records) == 0

    def test_combined_mode_and_model_filter(self):
        from voicetext.ui.history_browser_window import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a",
             "stt_model": "whisper", "llm_model": "gpt-4"},
            {"timestamp": "t2", "enhance_mode": "translate_en", "final_text": "b",
             "stt_model": "whisper", "llm_model": "gpt-4"},
            {"timestamp": "t3", "enhance_mode": "proofread", "final_text": "c",
             "stt_model": "whisper", "llm_model": "qwen"},
        ]

        panel._filter_mode = "proofread"
        panel._filter_model = "gpt-4"
        panel._apply_filters()
        assert len(panel._filtered_records) == 1
        assert panel._filtered_records[0]["timestamp"] == "t1"

    def test_corrected_only_filter(self):
        from voicetext.ui.history_browser_window import HistoryBrowserPanel

        panel = HistoryBrowserPanel()
        panel._all_records = [
            {"timestamp": "t1", "enhance_mode": "proofread", "final_text": "a",
             "user_corrected": True},
            {"timestamp": "t2", "enhance_mode": "proofread", "final_text": "b",
             "user_corrected": False},
            {"timestamp": "t3", "enhance_mode": "proofread",
             "enhanced_text": "x", "final_text": "y"},  # legacy inferred
        ]

        panel._filter_corrected_only = False
        panel._apply_filters()
        assert len(panel._filtered_records) == 3

        panel._filter_corrected_only = True
        panel._apply_filters()
        assert len(panel._filtered_records) == 2
        assert panel._filtered_records[0]["timestamp"] == "t1"
        assert panel._filtered_records[1]["timestamp"] == "t3"

    def test_format_timestamp(self):
        from voicetext.ui.history_browser_window import _format_timestamp

        assert _format_timestamp("2026-03-13T14:30:00+00:00") == "2026-03-13 14:30"
        assert _format_timestamp("2026-01-01T09:05:00") == "2026-01-01 09:05"
        assert _format_timestamp("") == ""
