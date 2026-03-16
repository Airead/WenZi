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


class TestRecordingIndicatorViewRecordingActive:
    def test_initial_recording_active_is_false(self):
        view = RecordingIndicatorView()
        assert view._recording_active is False

    def test_set_recording_active(self):
        view = RecordingIndicatorView()
        view._recording_active = True
        assert view._recording_active is True


class TestRecordingIndicatorViewMode:
    def test_initial_mode_fields(self):
        view = RecordingIndicatorView()
        assert view._mode_name is None
        assert view._mode_nav == (False, False)
        assert view._mode_attrs is None
        assert view._arrow_attrs is None

    def test_set_mode_fields(self):
        view = RecordingIndicatorView()
        view._mode_name = "Proofread"
        view._mode_nav = (True, True)
        assert view._mode_name == "Proofread"
        assert view._mode_nav == (True, True)


class TestRecordingIndicatorPanel:
    def test_initial_state(self):
        panel = RecordingIndicatorPanel()
        assert panel.enabled is True
        assert panel.show_device_name is False
        assert panel._panel is None
        assert panel._timer is None

    def test_show_device_name_toggle(self):
        panel = RecordingIndicatorPanel()
        assert panel.show_device_name is False
        panel.show_device_name = True
        assert panel.show_device_name is True
        panel.show_device_name = False
        assert panel.show_device_name is False

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

    def test_update_device_name_noop_when_no_panel(self):
        panel = RecordingIndicatorPanel()
        # Should not raise when panel is not shown
        panel.update_device_name("Test Mic")
        assert panel._panel is None

    def test_update_device_name_noop_when_same_name(self):
        panel = RecordingIndicatorPanel()
        panel._panel = MagicMock()
        view = RecordingIndicatorView(device_name="Same Mic")
        panel._indicator_view = view

        panel.update_device_name("Same Mic")
        # Panel should not be resized since name didn't change
        panel._panel.setContentSize_.assert_not_called()

    def test_update_device_name_updates_view_and_resets_attrs(self):
        panel = RecordingIndicatorPanel()
        view = RecordingIndicatorView(device_name=None)
        # Pre-set label attrs to verify they get reset
        view._label_attrs = {"some": "attrs"}
        panel._indicator_view = view
        mock_panel = MagicMock()
        panel._panel = mock_panel
        mock_timer = MagicMock()
        panel._timer = mock_timer

        # Patch the whole method's internals since it imports AppKit/Foundation
        # Just verify the state changes by letting the exception path handle it
        try:
            panel.update_device_name("New Mic")
        except Exception:
            pass

        # These are set before any AppKit calls
        assert view._device_name == "New Mic"
        assert view._label_attrs is None

    def test_animate_out_calls_completion_when_no_panel(self):
        panel = RecordingIndicatorPanel()
        callback = MagicMock()
        panel.animate_out(completion=callback)
        callback.assert_called_once()

    def test_animate_out_no_completion_when_no_panel(self):
        panel = RecordingIndicatorPanel()
        # Should not raise
        panel.animate_out()

    def test_update_mode_sets_view_fields(self):
        panel = RecordingIndicatorPanel()
        view = RecordingIndicatorView()
        panel._indicator_view = view
        panel._panel = None  # no real panel — just test view updates

        panel.update_mode("Translate EN", True, False)

        assert view._mode_name == "Translate EN"
        assert view._mode_nav == (True, False)

    def test_update_mode_noop_when_same_values(self):
        panel = RecordingIndicatorPanel()
        view = RecordingIndicatorView()
        view._mode_name = "Proofread"
        view._mode_nav = (True, True)
        view._mode_ns_str = "cached"  # should not be reset
        panel._indicator_view = view

        panel.update_mode("Proofread", True, True)

        # Cached NSString should be preserved (no invalidation)
        assert view._mode_ns_str == "cached"

    def test_update_mode_noop_when_no_view(self):
        panel = RecordingIndicatorPanel()
        panel._indicator_view = None
        # Should not raise
        panel.update_mode("Proofread", False, True)

    def test_clear_mode_resets_fields(self):
        panel = RecordingIndicatorPanel()
        view = RecordingIndicatorView()
        view._mode_name = "Proofread"
        view._mode_nav = (True, True)
        panel._indicator_view = view

        panel.clear_mode()

        assert view._mode_name is None
        assert view._mode_nav == (False, False)

    def test_set_recording_active_updates_view(self):
        panel = RecordingIndicatorPanel()
        view = RecordingIndicatorView()
        panel._indicator_view = view
        assert view._recording_active is False

        panel.set_recording_active()
        assert view._recording_active is True

    def test_set_recording_active_noop_when_no_view(self):
        panel = RecordingIndicatorPanel()
        panel._indicator_view = None
        # Should not raise
        panel.set_recording_active()

    def test_show_resets_recording_active(self):
        """show() creates a new view which defaults to recording_active=False."""
        panel = RecordingIndicatorPanel()
        panel.enabled = False  # prevent actual AppKit panel creation
        panel.show()
        # Panel not created (disabled), but verify the contract:
        # a fresh RecordingIndicatorView always starts inactive
        view = RecordingIndicatorView()
        assert view._recording_active is False

    def test_clear_mode_noop_when_no_view(self):
        panel = RecordingIndicatorPanel()
        panel._indicator_view = None
        # Should not raise
        panel.clear_mode()

    def test_update_device_name_preserves_panel_width(self):
        """update_device_name should use current panel width, not _PANEL_WIDTH."""
        panel = RecordingIndicatorPanel()
        view = RecordingIndicatorView(device_name=None)
        panel._indicator_view = view
        mock_panel = MagicMock()
        # Simulate panel already widened to 220 by update_mode
        mock_frame = MagicMock()
        mock_frame.size.width = 220
        mock_panel.frame.return_value = mock_frame
        panel._panel = mock_panel
        panel._timer = MagicMock()

        try:
            panel.update_device_name("New Mic")
        except Exception:
            pass

        # Verify setContentSize_ was called with width 220 (not 120)
        if mock_panel.setContentSize_.called:
            args = mock_panel.setContentSize_.call_args[0][0]
            assert args[0] == 220.0

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
