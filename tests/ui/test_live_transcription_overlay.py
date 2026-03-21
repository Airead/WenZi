"""Tests for the live transcription overlay."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_appkit(mock_appkit_modules, monkeypatch):
    """Mock AppKit and Foundation modules for headless testing."""
    return mock_appkit_modules


@pytest.fixture(autouse=True)
def _mock_overlay_internals():
    mock_view = MagicMock()
    mock_cls = MagicMock()
    mock_cls.alloc.return_value.initWithFrame_.return_value = mock_view
    with patch("wenzi.ui.live_transcription_overlay._LiveBgView", mock_cls):
        yield


class TestLiveTranscriptionOverlayInit:
    def test_defaults(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        assert overlay._panel is None
        assert overlay._text_field is None
        assert overlay._screen_center_y == 0

    def test_show_creates_panel(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show()

        assert overlay._panel is not None
        assert overlay._text_field is not None
        assert overlay._content_view is not None

    def test_show_sets_panel_properties(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show()

        panel = overlay._panel
        panel.setHidesOnDeactivate_.assert_any_call(False)
        panel.setIgnoresMouseEvents_.assert_any_call(True)
        panel.setOpaque_.assert_any_call(False)
        panel.setHasShadow_.assert_any_call(True)

    def test_show_uses_clear_panel_background(self):
        from AppKit import NSColor
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show()

        overlay._panel.setBackgroundColor_.assert_any_call(
            NSColor.clearColor()
        )


class TestLiveTranscriptionOverlayText:
    @staticmethod
    def _setup_frame_mock(overlay):
        """Set up proper numeric frame mocks for text field and panel."""
        # Mock cellSizeForBounds_ for _resize_panel
        cell_mock = MagicMock()
        needed_size = MagicMock()
        needed_size.height = 30.0
        cell_mock.cellSizeForBounds_.return_value = needed_size
        overlay._text_field.cell.return_value = cell_mock

        panel_frame = MagicMock()
        panel_frame.size.height = 40.0
        panel_frame.origin.y = 400.0
        panel_frame.origin.x = 200.0
        overlay._panel.frame.return_value = panel_frame

    def test_update_text(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show()
        self._setup_frame_mock(overlay)

        overlay.update_text("hello world")

        overlay._text_field.setStringValue_.assert_called_with("hello world")

    def test_update_text_noop_without_show(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        # Should not raise
        overlay.update_text("hello")

    def test_update_text_auto_resizes(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show()
        self._setup_frame_mock(overlay)

        # Make height differ enough to trigger resize
        cell_mock = overlay._text_field.cell()
        needed = MagicMock()
        needed.height = 60.0
        cell_mock.cellSizeForBounds_.return_value = needed

        overlay.update_text("some text")

        overlay._panel.setFrame_display_.assert_called()


class TestLiveTranscriptionOverlayLifecycle:
    def test_hide(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show()
        panel = overlay._panel

        overlay.hide()

        panel.orderOut_.assert_called()
        assert overlay._panel is None
        assert overlay._text_field is None
        assert overlay._content_view is None

    def test_close_cleans_up(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show()

        overlay.close()

        assert overlay._panel is None
        assert overlay._content_view is None
        assert overlay._text_field is None

    def test_close_without_show(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        # Should not raise
        overlay.close()

    def test_show_after_close(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show()
        overlay.close()
        overlay.show()

        assert overlay._panel is not None


class TestLiveTranscriptionOverlayActiveState:
    def test_show_active_by_default(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show()

        assert overlay._active is True
        # setAlphaValue_ should NOT have been called (defaults to 1.0)
        overlay._panel.setAlphaValue_.assert_not_called()

    def test_show_inactive_sets_low_alpha(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show(active=False)

        assert overlay._active is False
        overlay._panel.setAlphaValue_.assert_called_once_with(
            LiveTranscriptionOverlay._INACTIVE_ALPHA
        )

    def test_set_active_restores_full_alpha(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show(active=False)
        overlay._panel.setAlphaValue_.reset_mock()

        overlay.set_active()

        assert overlay._active is True
        overlay._panel.setAlphaValue_.assert_called_once_with(1.0)

    def test_set_active_noop_when_already_active(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show()

        overlay.set_active()

        # Should not call setAlphaValue_ at all
        overlay._panel.setAlphaValue_.assert_not_called()

    def test_set_active_noop_without_panel(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        # Should not raise
        overlay.set_active()


class TestLiveTranscriptionOverlayDarkMode:
    def test_content_view_is_bg_view(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show()

        # Content view should be the custom bg view (drawRect_-based)
        assert overlay._content_view is not None

    def test_text_uses_dynamic_color(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show()

        # Text color should be set (dynamic, not hardcoded)
        overlay._text_field.setTextColor_.assert_called_once()

    def test_text_center_aligned(self):
        from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay

        overlay = LiveTranscriptionOverlay()
        overlay.show()

        overlay._text_field.setAlignment_.assert_called_once_with(1)
