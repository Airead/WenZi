"""Tests for ConfigController."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from voicetext.controllers.config_controller import ConfigController


@pytest.fixture
def mock_app():
    """Create a minimal mock app for ConfigController."""
    app = MagicMock(spec=[])
    app._config = {
        "logging": {"level": "INFO"},
        "output": {"method": "type", "append_newline": False, "preview": True},
        "ai_enhance": {"enabled": True, "mode": "proofread"},
        "feedback": {"sound_enabled": True, "visual_indicator": True},
        "clipboard_enhance": {"hotkey": "ctrl+cmd+v"},
    }
    app._config_path = "/tmp/test_config.json"
    app._enhancer = MagicMock()
    app._enhancer.debug_print_prompt = False
    app._enhancer.debug_print_request_body = False
    app._enhancer.provider_name = "openai"
    app._enhancer.model_name = "gpt-4o"
    app._enhancer.thinking = True
    app._enhancer.vocab_enabled = False
    app._enhancer.history_enabled = False
    app._current_remote_asr = None
    app._current_preset_id = "funasr-zh"
    app._enhance_mode = "proofread"
    app._preview_enabled = True
    app._output_method = "type"
    app._enhance_vocab_item = MagicMock()
    app._enhance_vocab_item.state = 0
    app._enhance_history_item = MagicMock()
    app._enhance_history_item.state = 0
    app._usage_stats = MagicMock()
    app._conversation_history = MagicMock()
    app._history_browser = None
    app._log_viewer = None
    app._enhance_controller = MagicMock()
    app._enhance_menu_items = {}
    app._enhance_thinking_item = MagicMock()
    app._llm_model_menu_items = {}
    app._preview_item = MagicMock()
    app._sound_feedback_item = MagicMock()
    app._visual_indicator_item = MagicMock()
    app._sound_manager = MagicMock()
    app._recording_indicator = MagicMock()
    app._clipboard_hotkey_listener = None
    app._menu_builder = MagicMock()
    app._preview_controller = MagicMock()
    return app


@pytest.fixture
def ctrl(mock_app):
    return ConfigController(mock_app)


class TestOnEnhanceEditConfig:
    @patch("voicetext.controllers.config_controller.subprocess.Popen")
    def test_opens_config_file(self, mock_popen, ctrl, mock_app):
        ctrl.on_enhance_edit_config(None)
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert args[0] == "open"

    @patch("voicetext.controllers.config_controller.subprocess.Popen", side_effect=Exception("fail"))
    def test_handles_error(self, mock_popen, ctrl):
        # Should not raise
        ctrl.on_enhance_edit_config(None)


class TestLogLevelChange:
    @patch("voicetext.controllers.config_controller.save_config")
    def test_changes_level(self, mock_save, ctrl, mock_app):
        ctrl.on_log_level_change("DEBUG")
        assert mock_app._config["logging"]["level"] == "DEBUG"
        mock_save.assert_called_once()

    @patch("voicetext.controllers.config_controller.save_config")
    def test_invalid_level_defaults_to_info(self, mock_save, ctrl, mock_app):
        ctrl.on_log_level_change("NONEXISTENT")
        # getattr fallback is logging.INFO
        mock_save.assert_called_once()


class TestDebugToggles:
    def test_print_prompt_on(self, ctrl, mock_app):
        ctrl.on_print_prompt_change(True)
        assert mock_app._enhancer.debug_print_prompt is True

    def test_print_prompt_off(self, ctrl, mock_app):
        ctrl.on_print_prompt_change(False)
        assert mock_app._enhancer.debug_print_prompt is False

    def test_print_request_body_on(self, ctrl, mock_app):
        ctrl.on_print_request_body_change(True)
        assert mock_app._enhancer.debug_print_request_body is True

    def test_print_prompt_no_enhancer(self, ctrl, mock_app):
        mock_app._enhancer = None
        # Should not raise
        ctrl.on_print_prompt_change(True)

    def test_print_request_body_no_enhancer(self, ctrl, mock_app):
        mock_app._enhancer = None
        ctrl.on_print_request_body_change(True)


class TestBuildConfigInfo:
    @patch("voicetext.controllers.config_controller.PRESET_BY_ID", {"funasr-zh": MagicMock(display_name="FunASR 中文")})
    def test_basic_fields(self, ctrl, mock_app):
        info = ctrl.build_config_info()
        assert "FunASR" in info
        assert "proofread" in info
        assert "openai" in info
        assert "gpt-4o" in info

    @patch("voicetext.controllers.config_controller.PRESET_BY_ID", {})
    def test_remote_asr(self, ctrl, mock_app):
        mock_app._current_remote_asr = ("groq", "whisper-v3")
        info = ctrl.build_config_info()
        assert "groq / whisper-v3 (remote)" in info

    @patch("voicetext.controllers.config_controller.PRESET_BY_ID", {})
    def test_no_enhancer(self, ctrl, mock_app):
        mock_app._enhancer = None
        info = ctrl.build_config_info()
        assert "AI Provider:    N/A" in info


class TestOnReloadConfig:
    @patch("voicetext.controllers.config_controller.send_notification")
    @patch("voicetext.controllers.config_controller.load_config")
    def test_reload_success(self, mock_load, mock_notify, ctrl, mock_app):
        mock_load.return_value = {
            "output": {"method": "clipboard", "append_newline": True, "preview": False},
            "logging": {"level": "DEBUG"},
            "ai_enhance": {"enabled": True, "mode": "translate", "thinking": True,
                           "vocabulary": {"enabled": True},
                           "conversation_history": {"enabled": True},
                           "default_provider": "openai", "default_model": "gpt-4o"},
            "feedback": {"sound_enabled": False, "sound_volume": 0.5, "visual_indicator": False},
            "clipboard_enhance": {"hotkey": "ctrl+cmd+v"},
        }
        mock_app._enhance_menu_items = {}
        mock_app._enhance_thinking_item = MagicMock()
        mock_app._enhance_vocab_item = MagicMock()
        mock_app._enhance_history_item = MagicMock()
        mock_app._llm_model_menu_items = {}
        mock_app._preview_item = MagicMock()
        mock_app._sound_feedback_item = MagicMock()
        mock_app._visual_indicator_item = MagicMock()
        mock_app._clipboard_hotkey_listener = None

        ctrl.on_reload_config(None)

        assert mock_app._output_method == "clipboard"
        assert mock_app._append_newline is True
        mock_notify.assert_called()

    @patch("voicetext.controllers.config_controller.send_notification")
    @patch("voicetext.controllers.config_controller.load_config", side_effect=Exception("bad config"))
    def test_reload_failure(self, mock_load, mock_notify, ctrl):
        ctrl.on_reload_config(None)
        mock_notify.assert_called_once()
        assert "Reload Failed" in mock_notify.call_args[0][1]


class TestOnBrowseHistory:
    def test_creates_browser_and_shows(self, ctrl, mock_app):
        with patch("voicetext.ui.history_browser_window_web.HistoryBrowserPanel") as mock_cls:
            mock_panel = MagicMock()
            mock_cls.return_value = mock_panel

            ctrl.on_browse_history(None)

            mock_panel.show.assert_called_once()
            mock_app._usage_stats.record_history_browse_open.assert_called_once()

    def test_reuses_existing_browser(self, ctrl, mock_app):
        mock_app._history_browser = MagicMock()
        ctrl.on_browse_history(None)
        mock_app._history_browser.show.assert_called_once()

