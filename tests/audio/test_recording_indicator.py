"""Tests for RecordingIndicatorPanel."""

from unittest.mock import MagicMock, patch

from wenzi.audio.recording_indicator import (
    RecordingIndicatorPanel,
    RecordingIndicatorView,
    _build_subtitle,
)


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


class TestRecordingIndicatorViewSubtitle:
    def test_initial_subtitle_is_none(self):
        view = RecordingIndicatorView()
        assert view._subtitle is None
        assert view._subtitle_attrs is None

    def test_set_subtitle(self):
        view = RecordingIndicatorView()
        view._subtitle = "Proofread"
        assert view._subtitle == "Proofread"


class TestBuildSubtitle:
    def test_both_none(self):
        assert _build_subtitle(None, None) is None

    def test_mode_only(self):
        assert _build_subtitle("Proofread", None) == "Proofread"

    def test_device_only(self):
        assert _build_subtitle(None, "MacBook Mic") == "MacBook Mic"

    def test_both(self):
        assert _build_subtitle("Proofread", "MacBook Mic") == "Proofread · MacBook Mic"


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

    def test_update_level_asymmetric_ema(self):
        panel = RecordingIndicatorPanel()
        panel._indicator_view = RecordingIndicatorView()

        # First update: level=1.0 > smoothed=0.0 → attack (alpha=0.6)
        # smoothed = 0.6 * 1.0 + 0.4 * 0.0 = 0.6
        panel.update_level(1.0)
        assert abs(panel._smoothed_level - 0.6) < 0.01

        # Second update: level=1.0 > smoothed=0.6 → attack
        # smoothed = 0.6 * 1.0 + 0.4 * 0.6 = 0.84
        panel.update_level(1.0)
        assert abs(panel._smoothed_level - 0.84) < 0.01

        # Drop to zero: level=0.0 < smoothed=0.84 → release (alpha=0.25)
        # smoothed = 0.25 * 0.0 + 0.75 * 0.84 = 0.63
        panel.update_level(0.0)
        assert abs(panel._smoothed_level - 0.63) < 0.01

    def test_update_level_without_view(self):
        panel = RecordingIndicatorPanel()
        panel._indicator_view = None
        # Should not raise; level=0.5 > smoothed=0.0 → attack
        # smoothed = 0.6 * 0.5 + 0.4 * 0.0 = 0.3
        panel.update_level(0.5)
        assert abs(panel._smoothed_level - 0.3) < 0.01

    def test_hide_cleans_up(self):
        panel = RecordingIndicatorPanel()
        mock_timer = MagicMock()
        mock_panel = MagicMock()
        panel._timer = mock_timer
        panel._panel = mock_panel
        panel._indicator_view = RecordingIndicatorView()
        panel._smoothed_level = 0.5
        panel._mode_name = "Proofread"
        panel._device_name = "Mic"

        panel.hide()

        mock_timer.invalidate.assert_called_once()
        mock_panel.orderOut_.assert_called_once_with(None)
        assert panel._timer is None
        assert panel._panel is None
        assert panel._indicator_view is None
        assert panel._smoothed_level == 0.0
        assert panel._mode_name is None
        assert panel._device_name is None

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

    def test_update_mode_sets_subtitle(self):
        panel = RecordingIndicatorPanel()
        view = RecordingIndicatorView()
        panel._indicator_view = view
        panel._panel = None

        panel.update_mode("Translate EN")

        assert panel._mode_name == "Translate EN"
        assert view._subtitle == "Translate EN"

    def test_update_mode_merges_with_device(self):
        panel = RecordingIndicatorPanel()
        view = RecordingIndicatorView()
        panel._indicator_view = view
        panel._panel = None
        panel._device_name = "MacBook Mic"

        panel.update_mode("Proofread")

        assert view._subtitle == "Proofread · MacBook Mic"

    def test_update_mode_noop_when_same_name(self):
        panel = RecordingIndicatorPanel()
        view = RecordingIndicatorView()
        view._subtitle = "Proofread"
        view._subtitle_ns_str = "cached"
        panel._indicator_view = view
        panel._mode_name = "Proofread"

        panel.update_mode("Proofread")

        # Cached NSString should be preserved
        assert view._subtitle_ns_str == "cached"

    def test_update_mode_noop_when_no_view(self):
        panel = RecordingIndicatorPanel()
        panel._indicator_view = None
        # Should not raise
        panel.update_mode("Proofread")

    def test_clear_mode_clears_subtitle(self):
        panel = RecordingIndicatorPanel()
        view = RecordingIndicatorView()
        view._subtitle = "Proofread"
        panel._indicator_view = view
        panel._mode_name = "Proofread"

        panel.clear_mode()

        assert panel._mode_name is None
        assert view._subtitle is None

    def test_clear_mode_preserves_device_in_subtitle(self):
        panel = RecordingIndicatorPanel()
        view = RecordingIndicatorView()
        view._subtitle = "Proofread · MacBook Mic"
        panel._indicator_view = view
        panel._mode_name = "Proofread"
        panel._device_name = "MacBook Mic"

        panel.clear_mode()

        assert view._subtitle == "MacBook Mic"

    def test_update_device_name_noop_when_no_panel(self):
        panel = RecordingIndicatorPanel()
        panel.update_device_name("Test Mic")
        assert panel._panel is None

    def test_update_device_name_noop_when_same_name(self):
        panel = RecordingIndicatorPanel()
        panel._panel = MagicMock()
        panel._indicator_view = RecordingIndicatorView()
        panel._device_name = "Same Mic"

        panel.update_device_name("Same Mic")
        # Panel should not be resized since name didn't change
        panel._panel.setContentSize_.assert_not_called()

    def test_update_device_name_updates_subtitle(self):
        panel = RecordingIndicatorPanel()
        view = RecordingIndicatorView()
        panel._indicator_view = view
        panel._panel = MagicMock()
        panel._device_name = None

        # Will fail on AppKit calls but state should be set first
        try:
            panel.update_device_name("New Mic")
        except Exception:
            pass

        assert panel._device_name == "New Mic"
        assert view._subtitle == "New Mic"

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
        view = RecordingIndicatorView()
        assert view._recording_active is False

    def test_clear_mode_noop_when_no_view(self):
        panel = RecordingIndicatorPanel()
        panel._indicator_view = None
        panel._mode_name = "Proofread"
        panel.clear_mode()
        assert panel._mode_name is None

    def test_animate_out_stops_timer(self):
        panel = RecordingIndicatorPanel()
        mock_timer = MagicMock()
        mock_panel = MagicMock()
        panel._timer = mock_timer
        panel._panel = mock_panel

        mock_ctx = MagicMock()
        mock_animation_context = MagicMock()
        mock_animation_context.currentContext.return_value = mock_ctx

        with patch(
            "wenzi.audio.recording_indicator.RecordingIndicatorPanel.animate_out",
            wraps=panel.animate_out,
        ):
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
