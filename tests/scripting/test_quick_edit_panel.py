"""Tests for the quick edit panel module."""

from unittest.mock import patch

from wenzi.scripting.ui.quick_edit_panel import QuickEditPanel, open_quick_edit


class TestQuickEditPanel:
    def test_init_state(self):
        panel = QuickEditPanel()
        assert panel._panel is None
        assert panel._text_view is None
        assert panel._event_monitor is None
        assert panel._reveal_path is None

    @patch("AppKit.NSApp")
    def test_close_noop_when_not_shown(self, mock_app):
        """Closing a never-shown panel should not raise."""
        panel = QuickEditPanel()
        panel.close()  # should not raise


class TestOpenQuickEdit:
    @patch("wenzi.scripting.ui.quick_edit_panel.QuickEditPanel")
    def test_dispatches_to_main_thread(self, MockPanel):
        """open_quick_edit schedules panel creation via callAfter."""
        with patch("PyObjCTools.AppHelper.callAfter") as mock_call_after:
            open_quick_edit("test content")
            assert mock_call_after.called
            callback = mock_call_after.call_args[0][0]
            assert callable(callback)

    @patch("wenzi.scripting.ui.quick_edit_panel.QuickEditPanel")
    def test_passes_reveal_path(self, MockPanel):
        """open_quick_edit forwards reveal_path to show()."""
        with patch("PyObjCTools.AppHelper.callAfter") as mock_call_after:
            open_quick_edit("content", reveal_path="/path/to/snippet.md")
            callback = mock_call_after.call_args[0][0]

            # Execute the callback to verify show() is called with reveal_path
            mock_instance = MockPanel.return_value
            callback()
            mock_instance.show.assert_called_once_with(
                "content", reveal_path="/path/to/snippet.md",
            )

    @patch("wenzi.scripting.ui.quick_edit_panel.QuickEditPanel")
    def test_no_reveal_path_for_clipboard(self, MockPanel):
        """open_quick_edit without reveal_path passes None."""
        with patch("PyObjCTools.AppHelper.callAfter") as mock_call_after:
            open_quick_edit("clipboard text")
            callback = mock_call_after.call_args[0][0]

            mock_instance = MockPanel.return_value
            callback()
            mock_instance.show.assert_called_once_with(
                "clipboard text", reveal_path=None,
            )
