"""Tests for SettingsController."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from voicetext.controllers.settings_controller import SettingsController


@pytest.fixture
def mock_app():
    """Create a mock VoiceTextApp with all attributes used by SettingsController."""
    app = MagicMock()
    app._config = {
        "hotkeys": {"fn": True, "ctrl": False},
        "feedback": {"sound_enabled": True, "visual_indicator": True},
        "output": {"method": "type", "append_newline": False, "preview": True},
        "asr": {
            "backend": "funasr",
            "preset": "funasr-zh",
            "providers": {},
        },
        "ai_enhance": {
            "enabled": True,
            "mode": "proofread",
            "thinking": False,
            "vocabulary": {"enabled": False, "auto_build": True},
            "conversation_history": {"enabled": False},
        },
    }
    app._config_path = "/tmp/test_config.json"
    app._enhancer = MagicMock()
    app._enhancer.provider_name = "openai"
    app._enhancer.model_name = "gpt-4o"
    app._enhancer.thinking = False
    app._enhancer.vocab_enabled = False
    app._enhancer.history_enabled = False
    app._enhancer.vocab_index = None
    app._enhancer.providers_with_models = {"openai": ["gpt-4o", "gpt-4o-mini"]}
    app._enhancer.available_modes = [("proofread", "Proofread"), ("translate", "Translate")]
    app._hotkey_listener = MagicMock()
    app._hotkey_menu_items = {"fn": MagicMock(), "ctrl": MagicMock()}
    app._sound_manager = MagicMock()
    app._sound_manager.enabled = True
    app._sound_feedback_item = MagicMock()
    app._recording_indicator = MagicMock()
    app._recording_indicator.enabled = True
    app._visual_indicator_item = MagicMock()
    app._preview_enabled = True
    app._preview_item = MagicMock()
    app._current_preset_id = "funasr-zh"
    app._current_remote_asr = None
    app._busy = False
    app._transcriber = MagicMock()
    app._menu_builder = MagicMock()
    app._model_controller = MagicMock()
    app._enhance_menu_items = {"off": MagicMock(), "proofread": MagicMock()}
    app._enhance_mode = "proofread"
    app._enhance_controller = MagicMock()
    app._enhance_thinking_item = MagicMock()
    app._enhance_vocab_item = MagicMock()
    app._enhance_auto_build_item = MagicMock()
    app._enhance_history_item = MagicMock()
    app._auto_vocab_builder = MagicMock()
    app._auto_vocab_builder._enabled = True
    app._llm_model_menu_items = {}
    app._asr_remove_provider_items = {}
    app._llm_remove_provider_items = {}
    app._settings_panel = MagicMock()
    return app


@pytest.fixture
def ctrl(mock_app):
    return SettingsController(mock_app)


class TestHotkeyToggle:
    @patch("voicetext.controllers.settings_controller.save_config")
    def test_enable_hotkey(self, mock_save, ctrl, mock_app):
        ctrl.hotkey_toggle("ctrl", True)

        assert mock_app._config["hotkeys"]["ctrl"] is True
        mock_save.assert_called_once()
        mock_app._hotkey_listener.enable_key.assert_called_with("ctrl")
        mock_app._hotkey_menu_items["ctrl"].state == 1

    @patch("voicetext.controllers.settings_controller.save_config")
    def test_disable_hotkey(self, mock_save, ctrl, mock_app):
        ctrl.hotkey_toggle("fn", False)

        assert mock_app._config["hotkeys"]["fn"] is False
        mock_app._hotkey_listener.disable_key.assert_called_with("fn")

    @patch("voicetext.controllers.settings_controller.save_config")
    def test_no_listener(self, mock_save, ctrl, mock_app):
        mock_app._hotkey_listener = None
        ctrl.hotkey_toggle("fn", True)  # Should not raise
        mock_save.assert_called_once()


class TestSoundToggle:
    @patch("voicetext.controllers.settings_controller.save_config")
    def test_toggle_sound(self, mock_save, ctrl, mock_app):
        ctrl.sound_toggle(False)

        assert mock_app._sound_manager.enabled is False
        assert mock_app._sound_feedback_item.state == 0
        assert mock_app._config["feedback"]["sound_enabled"] is False
        mock_save.assert_called_once()


class TestVisualToggle:
    @patch("voicetext.controllers.settings_controller.save_config")
    def test_toggle_visual(self, mock_save, ctrl, mock_app):
        ctrl.visual_toggle(False)

        assert mock_app._recording_indicator.enabled is False
        assert mock_app._visual_indicator_item.state == 0
        mock_save.assert_called_once()


class TestPreviewToggle:
    @patch("voicetext.controllers.settings_controller.save_config")
    def test_toggle_preview(self, mock_save, ctrl, mock_app):
        ctrl.preview_toggle(False)

        assert mock_app._preview_enabled is False
        assert mock_app._preview_item.state == 0
        assert mock_app._config["output"]["preview"] is False
        mock_save.assert_called_once()


class TestSttSelect:
    @patch("voicetext.controllers.settings_controller.save_config")
    def test_same_preset_noop(self, mock_save, ctrl, mock_app):
        """Selecting the already-active preset should be a no-op."""
        ctrl.stt_select("funasr-zh")
        mock_save.assert_not_called()

    def test_busy_shows_alert(self, ctrl, mock_app):
        mock_app._busy = True
        with patch("voicetext.controllers.settings_controller.topmost_alert") as mock_alert:
            with patch("voicetext.controllers.settings_controller.restore_accessory"):
                ctrl.stt_select("mlx-whisper-large-v3-turbo")
        mock_alert.assert_called_once()

    def test_unknown_preset_warns(self, ctrl, mock_app):
        mock_app._current_preset_id = "something-else"
        ctrl.stt_select("nonexistent-preset-id")
        # Should not crash, just log warning


class TestSttRemoteSelect:
    def test_same_remote_noop(self, ctrl, mock_app):
        mock_app._current_remote_asr = ("groq", "whisper-v3")
        ctrl.stt_remote_select("groq", "whisper-v3")
        # Should be a no-op

    def test_busy_shows_alert(self, ctrl, mock_app):
        mock_app._busy = True
        with patch("voicetext.controllers.settings_controller.topmost_alert") as mock_alert:
            with patch("voicetext.controllers.settings_controller.restore_accessory"):
                ctrl.stt_remote_select("groq", "whisper-v3")
        mock_alert.assert_called_once()


class TestLlmSelect:
    @patch("voicetext.controllers.settings_controller.save_config")
    def test_select_new_model(self, mock_save, ctrl, mock_app):
        mock_app._llm_model_menu_items = {
            ("openai", "gpt-4o"): MagicMock(),
            ("openai", "gpt-4o-mini"): MagicMock(),
        }
        ctrl.llm_select("openai", "gpt-4o-mini")

        assert mock_app._enhancer.provider_name == "openai"
        assert mock_app._enhancer.model_name == "gpt-4o-mini"
        mock_save.assert_called_once()

    @patch("voicetext.controllers.settings_controller.save_config")
    def test_same_model_noop(self, mock_save, ctrl, mock_app):
        ctrl.llm_select("openai", "gpt-4o")
        mock_save.assert_not_called()

    def test_no_enhancer(self, ctrl, mock_app):
        mock_app._enhancer = None
        ctrl.llm_select("openai", "gpt-4o-mini")  # Should not raise


class TestEnhanceModeSelect:
    @patch("voicetext.controllers.settings_controller.save_config")
    def test_select_mode(self, mock_save, ctrl, mock_app):
        ctrl.enhance_mode_select("translate")

        assert mock_app._enhance_mode == "translate"
        assert mock_app._enhance_controller.enhance_mode == "translate"
        mock_app._enhancer.mode = "translate"
        mock_save.assert_called_once()

    @patch("voicetext.controllers.settings_controller.save_config")
    def test_select_off(self, mock_save, ctrl, mock_app):
        ctrl.enhance_mode_select("off")

        assert mock_app._enhance_mode == "off"
        assert mock_app._enhancer._enabled is False
        assert mock_app._config["ai_enhance"]["enabled"] is False


class TestThinkingToggle:
    @patch("voicetext.controllers.settings_controller.save_config")
    def test_enable_thinking(self, mock_save, ctrl, mock_app):
        ctrl.thinking_toggle(True)

        assert mock_app._enhancer.thinking is True
        assert mock_app._enhance_thinking_item.state == 1
        assert mock_app._config["ai_enhance"]["thinking"] is True

    def test_no_enhancer(self, ctrl, mock_app):
        mock_app._enhancer = None
        ctrl.thinking_toggle(True)  # Should not raise


class TestVocabToggle:
    @patch("voicetext.controllers.settings_controller.save_config")
    def test_enable_vocab(self, mock_save, ctrl, mock_app):
        ctrl.vocab_toggle(True)

        assert mock_app._enhancer.vocab_enabled is True
        assert mock_app._enhance_vocab_item.state == 1
        assert mock_app._config["ai_enhance"]["vocabulary"]["enabled"] is True

    def test_no_enhancer(self, ctrl, mock_app):
        mock_app._enhancer = None
        ctrl.vocab_toggle(True)  # Should not raise


class TestAutoBuildToggle:
    @patch("voicetext.controllers.settings_controller.save_config")
    def test_toggle_auto_build(self, mock_save, ctrl, mock_app):
        ctrl.auto_build_toggle(False)

        assert mock_app._auto_vocab_builder._enabled is False
        assert mock_app._enhance_auto_build_item.state == 0
        assert mock_app._config["ai_enhance"]["vocabulary"]["auto_build"] is False


class TestHistoryToggle:
    @patch("voicetext.controllers.settings_controller.save_config")
    def test_enable_history(self, mock_save, ctrl, mock_app):
        ctrl.history_toggle(True)

        assert mock_app._enhancer.history_enabled is True
        assert mock_app._enhance_history_item.state == 1
        assert mock_app._config["ai_enhance"]["conversation_history"]["enabled"] is True

    def test_no_enhancer(self, ctrl, mock_app):
        mock_app._enhancer = None
        ctrl.history_toggle(True)  # Should not raise


class TestEnhanceModeEdit:
    def test_opens_textedit(self, ctrl):
        with patch("subprocess.Popen") as mock_popen:
            ctrl.enhance_mode_edit("proofread")
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            assert args[0] == "open"
            assert "proofread.md" in args[-1]


class TestSttRemoveProvider:
    def test_empty_providers(self, ctrl, mock_app):
        ctrl.stt_remove_provider()  # Should not raise

    def test_delegates_to_model_controller(self, ctrl, mock_app):
        mock_app._config["asr"]["providers"] = {"groq": {"base_url": "x", "api_key": "y"}}
        mock_item = MagicMock()
        mock_app._asr_remove_provider_items = {"groq": mock_item}
        ctrl.stt_remove_provider()
        mock_app._model_controller.on_asr_remove_provider.assert_called_with(mock_item)


class TestLlmRemoveProvider:
    def test_no_enhancer(self, ctrl, mock_app):
        mock_app._enhancer = None
        ctrl.llm_remove_provider()  # Should not raise

    def test_delegates_to_model_controller(self, ctrl, mock_app):
        mock_item = MagicMock()
        mock_app._llm_remove_provider_items = {"openai": mock_item}
        ctrl.llm_remove_provider()
        mock_app._model_controller.on_enhance_remove_provider.assert_called_with(mock_item)


class TestOnOpenSettings:
    def test_shows_panel_with_state_and_callbacks(self, ctrl, mock_app):
        with patch("voicetext.enhance.vocabulary.get_vocab_entry_count", return_value=5):
            with patch("voicetext.controllers.settings_controller.PRESETS", []):
                with patch("voicetext.controllers.settings_controller.build_remote_asr_models", return_value=[]):
                    ctrl.on_open_settings(None)

        mock_app._settings_panel.show.assert_called_once()
        state, callbacks = mock_app._settings_panel.show.call_args[0]

        assert "hotkeys" in state
        assert "sound_enabled" in state
        assert "preview" in state
        assert "current_preset_id" in state

        assert "on_hotkey_toggle" in callbacks
        assert "on_sound_toggle" in callbacks
        assert "on_stt_select" in callbacks
        assert "on_llm_select" in callbacks
        assert "on_thinking_toggle" in callbacks
