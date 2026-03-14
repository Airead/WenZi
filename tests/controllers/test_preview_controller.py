"""Tests for PreviewController enhance mode debounce."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from voicetext.controllers.preview_controller import PreviewController


@pytest.fixture
def mock_app():
    """Create a mock VoiceTextApp for PreviewController."""
    app = MagicMock()
    app._enhance_mode = "proofread"
    app._enhance_controller = MagicMock()
    app._enhance_controller.get_cached.return_value = None
    app._enhancer = MagicMock()
    app._enhancer._enabled = True
    app._enhancer.mode = "proofread"
    app._enhance_menu_items = {"off": MagicMock(), "proofread": MagicMock(), "translate": MagicMock()}
    app._preview_panel = MagicMock()
    app._preview_panel.enhance_request_id = 0
    app._current_preview_asr_text = "hello"
    app._config = {"ai_enhance": {"enabled": True, "mode": "proofread"}}
    app._config_path = "/tmp/test.json"
    return app


@pytest.fixture
def ctrl(mock_app):
    return PreviewController(mock_app)


class TestEnhanceModeDebounce:
    """Tests for debounced enhancement on mode switch."""

    @patch("voicetext.controllers.preview_controller.save_config")
    def test_single_switch_fires_after_debounce(self, mock_save, ctrl, mock_app):
        """A single mode switch should fire enhancement after debounce delay."""
        # Use a very short debounce for testing
        ctrl._ENHANCE_DEBOUNCE_SECONDS = 0.05

        ctrl.on_preview_mode_change("translate")

        # Enhancement should NOT have started yet
        mock_app._enhance_controller.run.assert_not_called()
        # A debounce timer should be active
        assert ctrl._enhance_debounce_timer is not None

        # Wait for debounce to fire
        time.sleep(0.1)

        mock_app._enhance_controller.run.assert_called_once()

    @patch("voicetext.controllers.preview_controller.save_config")
    def test_rapid_switches_fire_only_once(self, mock_save, ctrl, mock_app):
        """Rapid mode switches should only fire one enhancement request."""
        ctrl._ENHANCE_DEBOUNCE_SECONDS = 0.05

        ctrl.on_preview_mode_change("translate")
        ctrl.on_preview_mode_change("proofread")
        ctrl.on_preview_mode_change("translate")

        # Nothing fired yet
        mock_app._enhance_controller.run.assert_not_called()
        # Cancel should have been called for each switch
        assert mock_app._enhance_controller.cancel.call_count == 3

        # Wait for debounce
        time.sleep(0.1)

        # Only one call should have fired (for the last mode)
        mock_app._enhance_controller.run.assert_called_once()

    @patch("voicetext.controllers.preview_controller.save_config")
    def test_switch_to_off_cancels_immediately(self, mock_save, ctrl, mock_app):
        """Switching to Off should cancel immediately without debounce."""
        ctrl._ENHANCE_DEBOUNCE_SECONDS = 0.05

        ctrl.on_preview_mode_change("off")

        mock_app._enhance_controller.cancel.assert_called_once()
        # No debounce timer should be active
        assert ctrl._enhance_debounce_timer is None

    @patch("voicetext.controllers.preview_controller.save_config")
    def test_switch_off_cancels_pending_debounce(self, mock_save, ctrl, mock_app):
        """Switching to Off should cancel any pending debounce timer."""
        ctrl._ENHANCE_DEBOUNCE_SECONDS = 0.05

        ctrl.on_preview_mode_change("translate")
        assert ctrl._enhance_debounce_timer is not None

        ctrl.on_preview_mode_change("off")
        assert ctrl._enhance_debounce_timer is None

        # Wait to ensure cancelled timer doesn't fire
        time.sleep(0.1)
        mock_app._enhance_controller.run.assert_not_called()

    @patch("voicetext.controllers.preview_controller.save_config")
    def test_cached_result_replays_immediately(self, mock_save, ctrl, mock_app):
        """If cache hit, replay immediately without debounce."""
        cached = MagicMock()
        mock_app._enhance_controller.get_cached.return_value = cached

        ctrl.on_preview_mode_change("translate")

        mock_app._preview_panel.replay_cached_result.assert_called_once()
        # No debounce timer needed
        assert ctrl._enhance_debounce_timer is None
        # No enhancement run needed
        mock_app._enhance_controller.run.assert_not_called()

    @patch("voicetext.controllers.preview_controller.save_config")
    def test_config_saved_immediately(self, mock_save, ctrl, mock_app):
        """Config should be saved immediately, not debounced."""
        ctrl._ENHANCE_DEBOUNCE_SECONDS = 0.05

        ctrl.on_preview_mode_change("translate")

        mock_save.assert_called_once()
        assert mock_app._config["ai_enhance"]["mode"] == "translate"

    @patch("voicetext.controllers.preview_controller.save_config")
    def test_stale_timer_does_not_fire(self, mock_save, ctrl, mock_app):
        """A stale debounce timer should not fire if request_id changed."""
        ctrl._ENHANCE_DEBOUNCE_SECONDS = 0.05

        ctrl.on_preview_mode_change("translate")
        # Simulate another component bumping the request_id
        mock_app._preview_panel.enhance_request_id = 999

        time.sleep(0.1)

        # Timer fired but guard prevented run()
        mock_app._enhance_controller.run.assert_not_called()
