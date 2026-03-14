"""Tests for RecordingIndicatorPanel."""

from unittest.mock import MagicMock, patch

from voicetext.audio.recording_indicator import RecordingIndicatorPanel, RecordingIndicatorView


class TestRecordingIndicatorView:
    def test_set_level(self):
        view = RecordingIndicatorView()
        assert view._level == 0.0
        view.set_level(0.5)
        assert view._level == 0.5
        view.set_level(1.0)
        assert view._level == 1.0


class TestRecordingIndicatorPanel:
    def test_initial_state(self):
        panel = RecordingIndicatorPanel()
        assert panel.enabled is True
        assert panel._panel is None
        assert panel._timer is None

    def test_enabled_toggle(self):
        panel = RecordingIndicatorPanel()
        panel.enabled = False
        assert panel.enabled is False
        panel.enabled = True
        assert panel.enabled is True

    def test_update_level_ema_smoothing(self):
        panel = RecordingIndicatorPanel()
        panel._indicator_view = RecordingIndicatorView()

        # First update: smoothed = 0.3 * 1.0 + 0.7 * 0.0 = 0.3
        panel.update_level(1.0)
        assert abs(panel._smoothed_level - 0.3) < 0.01

        # Second update: smoothed = 0.3 * 1.0 + 0.7 * 0.3 = 0.51
        panel.update_level(1.0)
        assert abs(panel._smoothed_level - 0.51) < 0.01

        # Drop to zero: smoothed = 0.3 * 0.0 + 0.7 * 0.51 = 0.357
        panel.update_level(0.0)
        assert abs(panel._smoothed_level - 0.357) < 0.01

    def test_update_level_without_view(self):
        panel = RecordingIndicatorPanel()
        panel._indicator_view = None
        # Should not raise
        panel.update_level(0.5)
        assert abs(panel._smoothed_level - 0.15) < 0.01

    def test_hide_cleans_up(self):
        panel = RecordingIndicatorPanel()
        mock_timer = MagicMock()
        mock_panel = MagicMock()
        panel._timer = mock_timer
        panel._panel = mock_panel
        panel._indicator_view = RecordingIndicatorView()
        panel._smoothed_level = 0.5

        panel.hide()

        mock_timer.invalidate.assert_called_once()
        mock_panel.orderOut_.assert_called_once_with(None)
        assert panel._timer is None
        assert panel._panel is None
        assert panel._indicator_view is None
        assert panel._smoothed_level == 0.0

    def test_hide_noop_when_not_shown(self):
        panel = RecordingIndicatorPanel()
        # Should not raise
        panel.hide()

    def test_show_disabled_does_nothing(self):
        panel = RecordingIndicatorPanel()
        panel.enabled = False
        panel.show()
        assert panel._panel is None

    def test_disable_hides_panel(self):
        panel = RecordingIndicatorPanel()
        mock_timer = MagicMock()
        mock_panel_obj = MagicMock()
        panel._timer = mock_timer
        panel._panel = mock_panel_obj

        panel.enabled = False

        mock_timer.invalidate.assert_called_once()
        mock_panel_obj.orderOut_.assert_called_once()
        assert panel._panel is None

    def test_current_frame_returns_none_when_no_panel(self):
        panel = RecordingIndicatorPanel()
        assert panel.current_frame is None

    def test_current_frame_returns_panel_frame(self):
        panel = RecordingIndicatorPanel()
        mock_panel = MagicMock()
        mock_frame = MagicMock()
        mock_panel.frame.return_value = mock_frame
        panel._panel = mock_panel

        assert panel.current_frame is mock_frame
        mock_panel.frame.assert_called_once()

    def test_animate_out_calls_completion_when_no_panel(self):
        panel = RecordingIndicatorPanel()
        callback = MagicMock()
        panel.animate_out(completion=callback)
        callback.assert_called_once()

    def test_animate_out_no_completion_when_no_panel(self):
        panel = RecordingIndicatorPanel()
        # Should not raise
        panel.animate_out()

    def test_animate_out_stops_timer(self):
        panel = RecordingIndicatorPanel()
        mock_timer = MagicMock()
        mock_panel = MagicMock()
        panel._timer = mock_timer
        panel._panel = mock_panel

        # animate_out will try to import NSAnimationContext; mock it
        mock_ctx = MagicMock()
        mock_animation_context = MagicMock()
        mock_animation_context.currentContext.return_value = mock_ctx

        with patch(
            "voicetext.audio.recording_indicator.RecordingIndicatorPanel.animate_out",
            wraps=panel.animate_out,
        ):
            # Just verify the timer gets invalidated
            # We can't easily test the full animation, so test the fallback path
            with patch.dict(
                "sys.modules",
                {"AppKit": MagicMock(NSAnimationContext=mock_animation_context)},
            ):
                try:
                    panel.animate_out()
                except Exception:
                    pass
                mock_timer.invalidate.assert_called_once()
                assert panel._timer is None
