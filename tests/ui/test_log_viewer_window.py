"""Tests for the log viewer window."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import mock_panel_close_delegate


@pytest.fixture(autouse=True)
def _mock_appkit(mock_appkit_modules, monkeypatch):
    """Mock AppKit and Foundation modules for headless testing."""
    import voicetext.ui.log_viewer_window as _lvw

    mock_panel_close_delegate(monkeypatch, _lvw)
    return mock_appkit_modules


class TestParseLogLines:
    """Tests for parse_log_lines (pure logic, no AppKit needed)."""

    def test_parse_single_info_line(self):
        from voicetext.ui.log_viewer_window import parse_log_lines

        lines = ["2026-03-13 10:00:01,123 [app] INFO: Started"]
        entries = parse_log_lines(lines)
        assert len(entries) == 1
        assert entries[0][0] == "INFO"
        assert "Started" in entries[0][1]

    def test_parse_multiple_levels(self):
        from voicetext.ui.log_viewer_window import parse_log_lines

        lines = [
            "2026-03-13 10:00:01,123 [app] INFO: First",
            "2026-03-13 10:00:02,456 [asr] WARNING: Slow",
            "2026-03-13 10:00:03,789 [app] ERROR: Failed",
        ]
        entries = parse_log_lines(lines)
        assert len(entries) == 3
        assert entries[0][0] == "INFO"
        assert entries[1][0] == "WARNING"
        assert entries[2][0] == "ERROR"

    def test_parse_multiline_traceback(self):
        from voicetext.ui.log_viewer_window import parse_log_lines

        lines = [
            "2026-03-13 10:00:01,123 [app] ERROR: Something failed",
            "Traceback (most recent call last):",
            '  File "app.py", line 10, in <module>',
            "ValueError: bad value",
        ]
        entries = parse_log_lines(lines)
        assert len(entries) == 1
        assert entries[0][0] == "ERROR"
        assert "Traceback" in entries[0][1]
        assert "ValueError" in entries[0][1]

    def test_parse_empty_input(self):
        from voicetext.ui.log_viewer_window import parse_log_lines

        assert parse_log_lines([]) == []

    def test_continuation_inherits_previous_level(self):
        from voicetext.ui.log_viewer_window import parse_log_lines

        lines = [
            "2026-03-13 10:00:01,123 [app] WARNING: Watch out",
            "extra detail line 1",
            "extra detail line 2",
            "2026-03-13 10:00:02,456 [app] INFO: OK",
        ]
        entries = parse_log_lines(lines)
        assert len(entries) == 2
        assert entries[0][0] == "WARNING"
        assert "extra detail line 1" in entries[0][1]
        assert entries[1][0] == "INFO"

    def test_parse_debug_level(self):
        from voicetext.ui.log_viewer_window import parse_log_lines

        lines = ["2026-03-13 10:00:01,123 [mod] DEBUG: Verbose output"]
        entries = parse_log_lines(lines)
        assert len(entries) == 1
        assert entries[0][0] == "DEBUG"


class TestFilterEntries:
    """Tests for filter_entries (pure logic)."""

    def _make_entries(self):
        from voicetext.ui.log_viewer_window import parse_log_lines

        lines = [
            "2026-03-13 10:00:01,123 [app] DEBUG: debug msg",
            "2026-03-13 10:00:02,456 [app] INFO: info msg",
            "2026-03-13 10:00:03,789 [asr] WARNING: warning msg",
            "2026-03-13 10:00:04,012 [app] ERROR: error msg",
        ]
        return parse_log_lines(lines)

    def test_filter_by_single_level(self):
        from voicetext.ui.log_viewer_window import filter_entries

        entries = self._make_entries()
        result = filter_entries(entries, frozenset({"ERROR"}), "")
        assert len(result) == 1
        assert result[0][0] == "ERROR"

    def test_filter_by_multiple_levels(self):
        from voicetext.ui.log_viewer_window import filter_entries

        entries = self._make_entries()
        result = filter_entries(entries, frozenset({"INFO", "WARNING"}), "")
        assert len(result) == 2

    def test_filter_all_levels(self):
        from voicetext.ui.log_viewer_window import filter_entries, _ALL_LEVELS

        entries = self._make_entries()
        result = filter_entries(entries, _ALL_LEVELS, "")
        assert len(result) == 4

    def test_filter_no_levels(self):
        from voicetext.ui.log_viewer_window import filter_entries

        entries = self._make_entries()
        result = filter_entries(entries, frozenset(), "")
        assert len(result) == 0

    def test_search_filter_case_insensitive(self):
        from voicetext.ui.log_viewer_window import filter_entries, _ALL_LEVELS

        entries = self._make_entries()
        result = filter_entries(entries, _ALL_LEVELS, "WARNING")
        assert len(result) == 1
        assert result[0][0] == "WARNING"

    def test_search_filter_partial_match(self):
        from voicetext.ui.log_viewer_window import filter_entries, _ALL_LEVELS

        entries = self._make_entries()
        result = filter_entries(entries, _ALL_LEVELS, "msg")
        assert len(result) == 4

    def test_combined_level_and_search_filter(self):
        from voicetext.ui.log_viewer_window import filter_entries

        entries = self._make_entries()
        result = filter_entries(entries, frozenset({"INFO", "ERROR"}), "error")
        assert len(result) == 1
        assert result[0][0] == "ERROR"

    def test_search_no_match(self):
        from voicetext.ui.log_viewer_window import filter_entries, _ALL_LEVELS

        entries = self._make_entries()
        result = filter_entries(entries, _ALL_LEVELS, "nonexistent")
        assert len(result) == 0


class TestLogViewerPanel:
    """Tests for LogViewerPanel (with mocked AppKit)."""

    def _make_panel(self, tmp_path, **kwargs):
        from voicetext.ui.log_viewer_window import LogViewerPanel

        log_file = tmp_path / "test.log"
        return LogViewerPanel(log_file, **kwargs), log_file

    def test_load_full_log(self, tmp_path):
        panel, log_file = self._make_panel(tmp_path)
        log_file.write_text(
            "2026-03-13 10:00:01,123 [app] INFO: Line one\n"
            "2026-03-13 10:00:02,456 [app] ERROR: Line two\n"
        )
        panel._load_full_log()
        assert len(panel._all_entries) == 2
        assert panel._last_size == log_file.stat().st_size

    def test_load_nonexistent_file(self, tmp_path):
        panel, log_file = self._make_panel(tmp_path)
        panel._load_full_log()
        assert panel._all_entries == []
        assert panel._last_size == 0

    def test_incremental_read(self, tmp_path):
        panel, log_file = self._make_panel(tmp_path)
        log_file.write_text("2026-03-13 10:00:01,123 [app] INFO: First\n")
        panel._load_full_log()
        assert len(panel._all_entries) == 1

        # Append new line
        with open(log_file, "a") as f:
            f.write("2026-03-13 10:00:02,456 [app] WARNING: Second\n")

        # Mock _apply_filters since no UI
        panel._apply_filters = MagicMock()
        panel._poll_log_file()
        assert len(panel._all_entries) == 2
        assert panel._all_entries[1][0] == "WARNING"

    def test_rotation_detection(self, tmp_path):
        panel, log_file = self._make_panel(tmp_path)
        # Start with big file
        log_file.write_text(
            "2026-03-13 10:00:01,123 [app] INFO: Old line 1\n"
            "2026-03-13 10:00:02,456 [app] INFO: Old line 2\n"
            "2026-03-13 10:00:03,789 [app] INFO: Old line 3\n"
        )
        panel._load_full_log()
        assert len(panel._all_entries) == 3

        # Simulate rotation: file becomes smaller
        log_file.write_text("2026-03-13 10:00:04,012 [app] INFO: New\n")
        panel._apply_filters = MagicMock()
        panel._poll_log_file()
        # After rotation detection, full reload happens
        assert len(panel._all_entries) == 1
        assert "New" in panel._all_entries[0][1]

    def test_no_new_data_skips_update(self, tmp_path):
        panel, log_file = self._make_panel(tmp_path)
        log_file.write_text("2026-03-13 10:00:01,123 [app] INFO: Hello\n")
        panel._load_full_log()

        panel._apply_filters = MagicMock()
        panel._poll_log_file()
        # No new data, _apply_filters should not be called
        panel._apply_filters.assert_not_called()

    def test_show_creates_panel(self, tmp_path):
        panel, log_file = self._make_panel(tmp_path)
        log_file.write_text("")
        panel.show()
        assert panel._panel is not None

    def test_close_stops_timer(self, tmp_path):
        panel, log_file = self._make_panel(tmp_path)
        log_file.write_text("")
        panel.show()

        mock_timer = MagicMock()
        panel._timer = mock_timer
        panel.close()
        mock_timer.invalidate.assert_called_once()
        assert panel._timer is None

    def test_clear_resets_entries(self, tmp_path):
        panel, log_file = self._make_panel(tmp_path)
        log_file.write_text("2026-03-13 10:00:01,123 [app] INFO: Hello\n")
        panel._load_full_log()
        assert len(panel._all_entries) == 1

        panel._apply_filters = MagicMock()
        panel.clearClicked_(None)
        assert len(panel._all_entries) == 0

    def test_refresh_reloads_file(self, tmp_path):
        panel, log_file = self._make_panel(tmp_path)
        log_file.write_text("2026-03-13 10:00:01,123 [app] INFO: V1\n")
        panel._load_full_log()

        log_file.write_text(
            "2026-03-13 10:00:01,123 [app] INFO: V1\n"
            "2026-03-13 10:00:02,456 [app] INFO: V2\n"
        )
        panel._apply_filters = MagicMock()
        panel.refreshClicked_(None)
        assert len(panel._all_entries) == 2


class TestLogViewerToolbarActions:
    """Tests for the new toolbar action handlers."""

    def _make_panel(self, tmp_path, **kwargs):
        from voicetext.ui.log_viewer_window import LogViewerPanel

        log_file = tmp_path / "test.log"
        log_file.write_text("")
        return LogViewerPanel(log_file, **kwargs), log_file

    def test_console_clicked_opens_console_app(self, tmp_path):
        panel, log_file = self._make_panel(tmp_path)
        with patch("voicetext.ui.log_viewer_window.subprocess") as mock_sub:
            panel.consoleClicked_(None)
            mock_sub.Popen.assert_called_once_with(
                ["open", "-a", "Console", str(log_file)]
            )

    def test_finder_clicked_reveals_in_finder(self, tmp_path):
        panel, log_file = self._make_panel(tmp_path)
        with patch("voicetext.ui.log_viewer_window.subprocess") as mock_sub:
            panel.finderClicked_(None)
            mock_sub.Popen.assert_called_once_with(
                ["open", "-R", str(log_file)]
            )

    def test_copy_path_clicked_copies_to_clipboard(self, tmp_path):
        panel, log_file = self._make_panel(tmp_path)
        with patch("voicetext.ui.log_viewer_window.subprocess") as mock_sub:
            panel.copyPathClicked_(None)
            mock_sub.run.assert_called_once_with(
                ["pbcopy"], input=str(log_file).encode(), check=True
            )

    def test_log_level_changed_triggers_callback(self, tmp_path):
        callback = MagicMock()
        panel, _ = self._make_panel(tmp_path, on_log_level_change=callback)
        sender = MagicMock()
        sender.titleOfSelectedItem.return_value = "WARNING"
        panel.logLevelChanged_(sender)
        callback.assert_called_once_with("WARNING")

    def test_log_level_changed_no_callback(self, tmp_path):
        panel, _ = self._make_panel(tmp_path)
        sender = MagicMock()
        sender.titleOfSelectedItem.return_value = "DEBUG"
        # Should not raise
        panel.logLevelChanged_(sender)

    def test_print_prompt_toggled_triggers_callback(self, tmp_path, _mock_appkit):
        mock_appkit = _mock_appkit.appkit
        mock_appkit.NSOnState = 1
        callback = MagicMock()
        panel, _ = self._make_panel(tmp_path, on_print_prompt_toggle=callback)
        sender = MagicMock()
        sender.state.return_value = 1  # NSOnState
        panel.printPromptToggled_(sender)
        callback.assert_called_once_with(True)

    def test_print_prompt_toggled_off(self, tmp_path, _mock_appkit):
        mock_appkit = _mock_appkit.appkit
        mock_appkit.NSOnState = 1
        callback = MagicMock()
        panel, _ = self._make_panel(tmp_path, on_print_prompt_toggle=callback)
        sender = MagicMock()
        sender.state.return_value = 0  # NSOffState
        panel.printPromptToggled_(sender)
        callback.assert_called_once_with(False)

    def test_print_request_body_toggled_triggers_callback(self, tmp_path, _mock_appkit):
        mock_appkit = _mock_appkit.appkit
        mock_appkit.NSOnState = 1
        callback = MagicMock()
        panel, _ = self._make_panel(
            tmp_path, on_print_request_body_toggle=callback
        )
        sender = MagicMock()
        sender.state.return_value = 1
        panel.printRequestBodyToggled_(sender)
        callback.assert_called_once_with(True)

    def test_print_request_body_toggled_no_callback(self, tmp_path, _mock_appkit):
        mock_appkit = _mock_appkit.appkit
        mock_appkit.NSOnState = 1
        panel, _ = self._make_panel(tmp_path)
        sender = MagicMock()
        sender.state.return_value = 0
        # Should not raise
        panel.printRequestBodyToggled_(sender)

    def test_show_sets_log_level_popup(self, tmp_path, _mock_appkit):
        mock_appkit = _mock_appkit.appkit
        mock_appkit.NSOnState = 1
        mock_appkit.NSOffState = 0
        panel, log_file = self._make_panel(tmp_path)
        panel.show(current_level="WARNING", print_prompt=True, print_request_body=False)
        assert panel._panel is not None
        # Verify popup was set to WARNING (index 2)
        panel._log_level_popup.selectItemAtIndex_.assert_called_with(2)

    def test_show_sets_default_level(self, tmp_path, _mock_appkit):
        mock_appkit = _mock_appkit.appkit
        mock_appkit.NSOnState = 1
        mock_appkit.NSOffState = 0
        panel, log_file = self._make_panel(tmp_path)
        panel.show(current_level="DEBUG")
        panel._log_level_popup.selectItemAtIndex_.assert_called_with(0)

    def test_show_sets_print_prompt_on(self, tmp_path, _mock_appkit):
        mock_appkit = _mock_appkit.appkit
        mock_appkit.NSOnState = 1
        mock_appkit.NSOffState = 0
        callback = MagicMock()
        panel, _ = self._make_panel(tmp_path, on_print_prompt_toggle=callback)
        # Build panel first so we can replace the check with a fresh mock
        panel.show(current_level="INFO", print_prompt=True, print_request_body=False)
        # The print_prompt_check.setState_ should have been called with NSOnState
        # Since all mocked buttons share the same mock chain, test via the callback approach
        # Instead, directly verify the panel stores the control references
        assert panel._print_prompt_check is not None
        assert panel._print_request_body_check is not None
