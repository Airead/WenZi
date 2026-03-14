"""Tests for app menu structure and Show Config functionality."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from voicetext.controllers.config_controller import ConfigController
from voicetext.enhance.enhancer import MODE_OFF


def _make_mock_app():
    """Create a minimal mock of VoiceTextApp for testing _build_config_info."""
    app = MagicMock(spec=[])
    app._current_remote_asr = None
    app._current_preset_id = "funasr-zh"
    app._enhance_mode = "proofread"
    app._preview_enabled = True
    app._output_method = "clipboard"
    app._config = {
        "hotkeys": {"right_cmd": True, "fn": True},
        "logging": {"level": "INFO"},
    }
    app._config_path = "/tmp/test_config.yaml"

    app._enhancer = MagicMock()
    app._enhancer.provider_name = "my-provider"
    app._enhancer.model_name = "gpt-4o"
    app._enhancer.thinking = True

    app._enhance_vocab_item = MagicMock()
    app._enhance_vocab_item.state = 1
    app._enhance_history_item = MagicMock()
    app._enhance_history_item.state = 0

    return app


def _get_info(app):
    """Call build_config_info via ConfigController with PRESET_BY_ID patched."""
    ctrl = ConfigController(app)
    preset_map = {"funasr-zh": MagicMock(display_name="FunASR 中文")}
    with patch("voicetext.controllers.config_controller.PRESET_BY_ID", preset_map):
        return ctrl.build_config_info()


class TestBuildConfigInfo:
    """Tests for _build_config_info."""

    def test_all_fields_present(self):
        app = _make_mock_app()
        info = _get_info(app)

        assert "FunASR" in info
        assert "proofread" in info
        assert "my-provider" in info
        assert "gpt-4o" in info
        assert "Thinking:       \u2705" in info
        assert "Preview:        \u2705" in info
        assert "Vocabulary:     \u2705" in info
        assert "History:        \u274C" in info
        assert "clipboard" in info
        assert "right_cmd" in info
        assert "INFO" in info
        assert "test_config.yaml" in info

    def test_default_config_path(self):
        app = _make_mock_app()
        app._config_path = None
        info = _get_info(app)

        assert "None" not in info
        assert ".config/VoiceText/config.json" in info

    def test_no_enhancer(self):
        app = _make_mock_app()
        app._enhancer = None
        info = _get_info(app)

        assert "AI Provider:    N/A" in info
        assert "AI Model:       N/A" in info
        assert "Thinking:       N/A" in info

    def test_toggle_states_off(self):
        app = _make_mock_app()
        app._preview_enabled = False
        app._enhancer.thinking = False
        app._enhance_vocab_item.state = 0
        app._enhance_history_item.state = 0
        info = _get_info(app)

        assert "Thinking:       \u274C" in info
        assert "Preview:        \u274C" in info
        assert "Vocabulary:     \u274C" in info
        assert "History:        \u274C" in info

    def test_enhance_mode_off(self):
        app = _make_mock_app()
        app._enhance_mode = MODE_OFF
        info = _get_info(app)

        assert f"AI Enhance:     {MODE_OFF}" in info

    def test_unknown_preset(self):
        app = _make_mock_app()
        app._current_remote_asr = None
        app._current_preset_id = "unknown-preset"

        ctrl = ConfigController(app)
        with patch("voicetext.controllers.config_controller.PRESET_BY_ID", {}):
            info = ctrl.build_config_info()

        assert "unknown-preset" in info

    def test_remote_asr_active(self):
        app = _make_mock_app()
        app._current_remote_asr = ("groq", "whisper-large-v3-turbo")
        info = _get_info(app)

        assert "groq / whisper-large-v3-turbo (remote)" in info
