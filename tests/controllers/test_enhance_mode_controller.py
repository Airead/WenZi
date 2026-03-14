"""Tests for EnhanceModeController."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from voicetext.controllers.enhance_mode_controller import EnhanceModeController


@pytest.fixture
def mock_app():
    """Create a minimal mock app for EnhanceModeController."""
    app = MagicMock(spec=[])
    app._config = {
        "ai_enhance": {"enabled": True, "mode": "proofread"},
        "output": {"preview": True},
    }
    app._config_path = "/tmp/test_config.json"
    app._enhancer = MagicMock()
    app._enhancer.thinking = False
    app._enhancer.vocab_enabled = False
    app._enhancer.vocab_index = None
    app._enhancer.history_enabled = False
    app._enhancer.provider_name = "openai"
    app._enhancer.model_name = "gpt-4o"
    app._enhance_mode = "proofread"
    app._enhance_menu_items = {}
    app._enhance_controller = MagicMock()
    app._enhance_vocab_item = MagicMock()
    app._enhance_vocab_item.state = 0
    app._enhance_vocab_item.title = "Vocabulary"
    app._enhance_history_item = MagicMock()
    app._enhance_thinking_item = MagicMock()
    app._menu_builder = MagicMock()
    app._auto_vocab_builder = MagicMock()
    app._auto_vocab_builder._enabled = True
    app._auto_vocab_builder.is_building.return_value = False
    app._preview_enabled = True
    app._current_status = "VT"
    return app


@pytest.fixture
def ctrl(mock_app):
    return EnhanceModeController(mock_app)


class TestOnEnhanceModeSelect:
    @patch("voicetext.controllers.enhance_mode_controller.save_config")
    def test_selects_mode(self, mock_save, ctrl, mock_app):
        sender = MagicMock()
        sender._enhance_mode = "translate"
        mock_app._enhance_menu_items = {"proofread": MagicMock(), "translate": MagicMock()}

        ctrl.on_enhance_mode_select(sender)

        assert mock_app._enhance_mode == "translate"
        assert mock_app._enhance_menu_items["translate"].state == 1
        assert mock_app._enhance_menu_items["proofread"].state == 0
        mock_save.assert_called_once()

    @patch("voicetext.controllers.enhance_mode_controller.save_config")
    def test_selects_off(self, mock_save, ctrl, mock_app):
        from voicetext.enhance.enhancer import MODE_OFF

        sender = MagicMock()
        sender._enhance_mode = MODE_OFF
        mock_app._enhance_menu_items = {MODE_OFF: MagicMock()}

        ctrl.on_enhance_mode_select(sender)

        assert mock_app._enhancer._enabled is False


class TestOnEnhanceThinkingToggle:
    @patch("voicetext.controllers.enhance_mode_controller.save_config")
    def test_toggle_on(self, mock_save, ctrl, mock_app):
        sender = MagicMock()
        mock_app._enhancer.thinking = False

        ctrl.on_enhance_thinking_toggle(sender)

        assert mock_app._enhancer.thinking is True
        assert sender.state == 1
        mock_save.assert_called_once()

    @patch("voicetext.controllers.enhance_mode_controller.save_config")
    def test_toggle_off(self, mock_save, ctrl, mock_app):
        sender = MagicMock()
        mock_app._enhancer.thinking = True

        ctrl.on_enhance_thinking_toggle(sender)

        assert mock_app._enhancer.thinking is False
        assert sender.state == 0

    def test_no_enhancer(self, ctrl, mock_app):
        mock_app._enhancer = None
        sender = MagicMock()
        ctrl.on_enhance_thinking_toggle(sender)
        # Should not raise


class TestVocabToggle:
    @patch("voicetext.controllers.enhance_mode_controller.save_config")
    def test_toggle_on(self, mock_save, ctrl, mock_app):
        sender = MagicMock()
        mock_app._enhancer.vocab_enabled = False

        ctrl.on_vocab_toggle(sender)

        assert mock_app._enhancer.vocab_enabled is True
        assert sender.state == 1
        mock_save.assert_called_once()

    def test_no_enhancer(self, ctrl, mock_app):
        mock_app._enhancer = None
        sender = MagicMock()
        ctrl.on_vocab_toggle(sender)


class TestAutoBuildToggle:
    @patch("voicetext.controllers.enhance_mode_controller.save_config")
    def test_toggle_off(self, mock_save, ctrl, mock_app):
        sender = MagicMock()
        mock_app._auto_vocab_builder._enabled = True

        ctrl.on_auto_build_toggle(sender)

        assert mock_app._auto_vocab_builder._enabled is False
        assert sender.state == 0
        mock_save.assert_called_once()


class TestHistoryToggle:
    @patch("voicetext.controllers.enhance_mode_controller.save_config")
    def test_toggle_on(self, mock_save, ctrl, mock_app):
        sender = MagicMock()
        mock_app._enhancer.history_enabled = False

        ctrl.on_history_toggle(sender)

        assert mock_app._enhancer.history_enabled is True
        assert sender.state == 1
        mock_save.assert_called_once()

    def test_no_enhancer(self, ctrl, mock_app):
        mock_app._enhancer = None
        sender = MagicMock()
        ctrl.on_history_toggle(sender)


class TestUpdateVocabTitle:
    @patch("voicetext.controllers.enhance_mode_controller.get_vocab_entry_count", return_value=42)
    def test_with_entries(self, mock_count, ctrl, mock_app):
        ctrl.update_vocab_title()
        assert mock_app._enhance_vocab_item.title == "Vocabulary (42)"

    @patch("voicetext.controllers.enhance_mode_controller.get_vocab_entry_count", return_value=0)
    def test_no_entries(self, mock_count, ctrl, mock_app):
        ctrl.update_vocab_title()
        assert mock_app._enhance_vocab_item.title == "Vocabulary"

    @patch("voicetext.controllers.enhance_mode_controller.get_vocab_entry_count", return_value=0)
    def test_uses_vocab_index_count(self, mock_count, ctrl, mock_app):
        mock_app._enhancer.vocab_index = MagicMock()
        mock_app._enhancer.vocab_index.entry_count = 15

        ctrl.update_vocab_title()
        assert mock_app._enhance_vocab_item.title == "Vocabulary (15)"


class TestPreviewToggle:
    @patch("voicetext.controllers.enhance_mode_controller.save_config")
    def test_toggle_off(self, mock_save, ctrl, mock_app):
        sender = MagicMock()
        mock_app._preview_enabled = True

        ctrl.on_preview_toggle(sender)

        assert mock_app._preview_enabled is False
        assert sender.state == 0
        mock_save.assert_called_once()

    @patch("voicetext.controllers.enhance_mode_controller.save_config")
    def test_toggle_on(self, mock_save, ctrl, mock_app):
        sender = MagicMock()
        mock_app._preview_enabled = False

        ctrl.on_preview_toggle(sender)

        assert mock_app._preview_enabled is True
        assert sender.state == 1


class TestOnVocabBuild:
    def test_no_enhancer_shows_alert(self, ctrl, mock_app):
        mock_app._enhancer = None
        with patch("voicetext.controllers.enhance_mode_controller.topmost_alert") as mock_alert:
            ctrl.on_vocab_build(None)
            mock_alert.assert_called_once()

    def test_building_shows_alert(self, ctrl, mock_app):
        mock_app._auto_vocab_builder.is_building.return_value = True
        with patch("voicetext.controllers.enhance_mode_controller.topmost_alert") as mock_alert:
            ctrl.on_vocab_build(None)
            mock_alert.assert_called_once()


class TestAddModeTemplate:
    def test_template_exists(self):
        assert EnhanceModeController._ADD_MODE_TEMPLATE is not None
        assert "My New Mode" in EnhanceModeController._ADD_MODE_TEMPLATE
