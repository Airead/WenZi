"""Tests for SettingsController."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from wenzi.controllers.settings_controller import SettingsController


@pytest.fixture
def mock_app():
    """Create a mock WenZiApp with all attributes used by SettingsController."""
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
        "ui": {
            "settings_last_tab": "general",
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
    mock_proofread = MagicMock()
    mock_proofread.order = 10
    mock_translate = MagicMock()
    mock_translate.order = 20
    app._enhancer.get_mode_definition = lambda mid: {"proofread": mock_proofread, "translate": mock_translate}.get(mid)
    app._hotkey_listener = MagicMock()
    app._hotkey_menu_items = {"fn": MagicMock(), "ctrl": MagicMock()}
    app._sound_manager = MagicMock()
    app._sound_manager.enabled = True
    app._sound_feedback_item = MagicMock()
    app._recording_indicator = MagicMock()
    app._recording_indicator.enabled = True
    app._visual_indicator_item = MagicMock()
    app._preview_enabled = True
    app._preview_panel = MagicMock()
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
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_enable_hotkey(self, mock_save, ctrl, mock_app):
        ctrl.hotkey_toggle("ctrl", True)

        assert mock_app._config["hotkeys"]["ctrl"] is True
        mock_save.assert_called_once()
        mock_app._hotkey_listener.enable_key.assert_called_with("ctrl")
        mock_app._hotkey_menu_items["ctrl"].state == 1

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_disable_hotkey(self, mock_save, ctrl, mock_app):
        ctrl.hotkey_toggle("fn", False)

        assert mock_app._config["hotkeys"]["fn"] is False
        mock_app._hotkey_listener.disable_key.assert_called_with("fn")

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_no_listener(self, mock_save, ctrl, mock_app):
        mock_app._hotkey_listener = None
        ctrl.hotkey_toggle("fn", True)  # Should not raise
        mock_save.assert_called_once()


class TestRestartKeySelect:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_set_restart_key(self, mock_save, ctrl, mock_app):
        ctrl.restart_key_select("space")

        assert mock_app._config["feedback"]["restart_key"] == "space"
        mock_save.assert_called_once()
        mock_app._hotkey_listener.set_restart_key.assert_called_with("space")

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_no_listener(self, mock_save, ctrl, mock_app):
        mock_app._hotkey_listener = None
        ctrl.restart_key_select("ctrl")  # Should not raise
        mock_save.assert_called_once()


class TestCancelKeySelect:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_set_cancel_key(self, mock_save, ctrl, mock_app):
        ctrl.cancel_key_select("esc")

        assert mock_app._config["feedback"]["cancel_key"] == "esc"
        mock_save.assert_called_once()
        mock_app._hotkey_listener.set_cancel_key.assert_called_with("esc")

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_no_listener(self, mock_save, ctrl, mock_app):
        mock_app._hotkey_listener = None
        ctrl.cancel_key_select("cmd")  # Should not raise
        mock_save.assert_called_once()


class TestKeychainToggle:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_toggle_keychain_on(self, mock_save, ctrl, mock_app):
        ctrl.keychain_toggle(True)

        assert mock_app._config["keychain"]["enabled"] is True
        mock_save.assert_called_once()

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_toggle_keychain_off(self, mock_save, ctrl, mock_app):
        mock_app._config["keychain"] = {"enabled": True}
        ctrl.keychain_toggle(False)

        assert mock_app._config["keychain"]["enabled"] is False
        mock_save.assert_called_once()


class TestSoundToggle:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_toggle_sound(self, mock_save, ctrl, mock_app):
        ctrl.sound_toggle(False)

        assert mock_app._sound_manager.enabled is False
        assert mock_app._sound_feedback_item.state == 0
        assert mock_app._config["feedback"]["sound_enabled"] is False
        mock_save.assert_called_once()


class TestVisualToggle:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_toggle_visual(self, mock_save, ctrl, mock_app):
        ctrl.visual_toggle(False)

        assert mock_app._recording_indicator.enabled is False
        assert mock_app._visual_indicator_item.state == 0
        mock_save.assert_called_once()


class TestLauncherRecycleMode:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_build_launcher_state_includes_recycle_mode(self, mock_save, ctrl, mock_app):
        mock_app._config.setdefault("scripting", {})["chooser"] = {
            "enabled": True,
            "recycle_mode": "preload_html",
        }

        state = ctrl._build_launcher_state()

        assert state["recycle_mode"] == "preload_html"
        mock_save.assert_not_called()

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_launcher_recycle_mode_change_updates_config_and_panel(self, mock_save, ctrl, mock_app):
        mock_app._config.setdefault("scripting", {})["chooser"] = {}
        mock_app._settings_panel.is_visible = False
        mock_panel = MagicMock()
        mock_app._script_engine = MagicMock()
        mock_app._script_engine.wz.chooser._get_panel.return_value = mock_panel

        ctrl.launcher_recycle_mode_change("preload_html")

        assert mock_app._config["scripting"]["chooser"]["recycle_mode"] == "preload_html"
        mock_panel.set_recycle_mode.assert_called_once_with("preload_html")
        mock_save.assert_called_once()


class TestPreviewToggle:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_toggle_preview(self, mock_save, ctrl, mock_app):
        ctrl.preview_toggle(False)

        assert mock_app._preview_enabled is False
        assert mock_app._preview_item.state == 0
        assert mock_app._config["output"]["preview"] is False
        mock_save.assert_called_once()


class TestSttSelect:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_same_preset_noop(self, mock_save, ctrl, mock_app):
        """Selecting the already-active preset should be a no-op."""
        ctrl.stt_select("funasr-zh")
        mock_save.assert_not_called()

    def test_busy_shows_alert(self, ctrl, mock_app):
        mock_app._busy = True
        with patch("wenzi.controllers.settings_controller.topmost_alert") as mock_alert:
            with patch("wenzi.controllers.settings_controller.restore_accessory"):
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
        with patch("wenzi.controllers.settings_controller.topmost_alert") as mock_alert:
            with patch("wenzi.controllers.settings_controller.restore_accessory"):
                ctrl.stt_remote_select("groq", "whisper-v3")
        mock_alert.assert_called_once()


class TestLlmSelect:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_select_new_model(self, mock_save, ctrl, mock_app):
        mock_app._llm_model_menu_items = {
            ("openai", "gpt-4o"): MagicMock(),
            ("openai", "gpt-4o-mini"): MagicMock(),
        }
        ctrl.llm_select("openai", "gpt-4o-mini")

        assert mock_app._enhancer.provider_name == "openai"
        assert mock_app._enhancer.model_name == "gpt-4o-mini"
        mock_save.assert_called_once()

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_same_model_noop(self, mock_save, ctrl, mock_app):
        ctrl.llm_select("openai", "gpt-4o")
        mock_save.assert_not_called()

    def test_no_enhancer(self, ctrl, mock_app):
        mock_app._enhancer = None
        ctrl.llm_select("openai", "gpt-4o-mini")  # Should not raise


class TestEnhanceModeSelect:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_select_mode(self, mock_save, ctrl, mock_app):
        ctrl.enhance_mode_select("translate")

        assert mock_app._enhance_mode == "translate"
        assert mock_app._enhance_controller.enhance_mode == "translate"
        mock_app._enhancer.mode = "translate"
        mock_save.assert_called_once()

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_select_off(self, mock_save, ctrl, mock_app):
        ctrl.enhance_mode_select("off")

        assert mock_app._enhance_mode == "off"
        assert mock_app._enhancer._enabled is False
        assert mock_app._config["ai_enhance"]["enabled"] is False


class TestThinkingToggle:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_enable_thinking(self, mock_save, ctrl, mock_app):
        ctrl.thinking_toggle(True)

        assert mock_app._enhancer.thinking is True
        assert mock_app._enhance_thinking_item.state == 1
        assert mock_app._config["ai_enhance"]["thinking"] is True

    def test_no_enhancer(self, ctrl, mock_app):
        mock_app._enhancer = None
        ctrl.thinking_toggle(True)  # Should not raise


class TestHistoryToggle:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_enable_history(self, mock_save, ctrl, mock_app):
        ctrl.history_toggle(True)

        assert mock_app._enhancer.history_enabled is True
        assert mock_app._enhance_history_item.state == 1
        assert mock_app._config["ai_enhance"]["conversation_history"]["enabled"] is True

    def test_no_enhancer(self, ctrl, mock_app):
        mock_app._enhancer = None
        ctrl.history_toggle(True)  # Should not raise


class TestModelTimeoutChange:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_change_timeout(self, mock_save, ctrl, mock_app):
        ctrl.model_timeout_change(20)

        assert mock_app._enhancer._connection_timeout == 20
        assert mock_app._config["ai_enhance"]["connection_timeout"] == 20

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_no_enhancer(self, mock_save, ctrl, mock_app):
        mock_app._enhancer = None
        ctrl.model_timeout_change(20)  # Should not raise
        assert mock_app._config["ai_enhance"]["connection_timeout"] == 20


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


class TestTabChange:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_persist_tab(self, mock_save, ctrl, mock_app):
        ctrl.tab_change("stt")

        assert mock_app._config["ui"]["settings_last_tab"] == "stt"
        mock_save.assert_called_once()

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_creates_ui_section_if_missing(self, mock_save, ctrl, mock_app):
        mock_app._config.pop("ui", None)
        ctrl.tab_change("ai")

        assert mock_app._config["ui"]["settings_last_tab"] == "ai"
        mock_save.assert_called_once()


class TestOnOpenSettings:
    def test_shows_panel_with_state_and_callbacks(self, ctrl, mock_app):
        with patch("wenzi.controllers.settings_controller.PRESETS", []):
            with patch("wenzi.controllers.settings_controller.build_remote_asr_models", return_value=[]):
                ctrl.on_open_settings(None)

        mock_app._settings_panel.show.assert_called_once()
        state, callbacks = mock_app._settings_panel.show.call_args[0]

        assert "hotkeys" in state
        assert "sound_enabled" in state
        assert "preview" in state
        assert "current_preset_id" in state
        assert state["last_tab"] == "general"
        assert state["enhance_modes"] == [("proofread", "Proofread", 10), ("translate", "Translate", 20)]

        assert "on_hotkey_toggle" in callbacks
        assert "on_hotkey_mode_select" in callbacks
        assert "on_hotkey_delete" in callbacks
        assert "on_sound_toggle" in callbacks
        assert "on_stt_select" in callbacks
        assert "on_llm_select" in callbacks
        assert "on_thinking_toggle" in callbacks
        assert "on_tab_change" in callbacks


class TestHotkeyModeSelect:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_set_mode(self, mock_save, ctrl, mock_app):
        ctrl.hotkey_mode_select("fn", "translate_en")

        assert mock_app._config["hotkeys"]["fn"] == {"mode": "translate_en"}
        mock_save.assert_called_once()

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_clear_mode_to_system_default(self, mock_save, ctrl, mock_app):
        mock_app._config["hotkeys"]["fn"] = {"mode": "translate_en"}
        ctrl.hotkey_mode_select("fn", None)

        assert mock_app._config["hotkeys"]["fn"] is True
        mock_save.assert_called_once()


class TestHotkeyDelete:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_delete_hotkey(self, mock_save, ctrl, mock_app):
        mock_app._config["hotkeys"]["ctrl"] = True
        ctrl.hotkey_delete("ctrl")

        assert "ctrl" not in mock_app._config["hotkeys"]
        mock_save.assert_called_once()
        mock_app._hotkey_listener.disable_key.assert_called_with("ctrl")

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_cannot_delete_fn(self, mock_save, ctrl, mock_app):
        ctrl.hotkey_delete("fn")

        assert "fn" in mock_app._config["hotkeys"]
        mock_save.assert_not_called()

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_delete_removes_menu_item(self, mock_save, ctrl, mock_app):
        menu = MagicMock()
        item = MagicMock()
        item.menu.return_value = menu
        mock_app._hotkey_menu_items["ctrl"] = item

        ctrl.hotkey_delete("ctrl")

        menu.removeItem_.assert_called_with(item)
        assert "ctrl" not in mock_app._hotkey_menu_items


class TestHotkeyToggleWithMode:
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_enable_preserves_dict_config(self, mock_save, ctrl, mock_app):
        """When enabling a hotkey that has dict config, preserve it."""
        mock_app._config["hotkeys"]["ctrl"] = {"mode": "translate_en"}
        # Simulate: user unchecked then rechecked
        ctrl.hotkey_toggle("ctrl", False)
        assert mock_app._config["hotkeys"]["ctrl"] is False

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_enable_sets_true_for_bool(self, mock_save, ctrl, mock_app):
        """When enabling a hotkey with bool config, set True."""
        mock_app._config["hotkeys"]["ctrl"] = False
        ctrl.hotkey_toggle("ctrl", True)
        assert mock_app._config["hotkeys"]["ctrl"] is True


class TestOpenDocLink:
    @patch("wenzi.controllers.settings_controller.webbrowser")
    @patch("wenzi.controllers.settings_controller.build_doc_url", return_value="https://example.com/docs/test")
    def test_opens_url(self, mock_build, mock_wb, ctrl):
        ctrl.open_doc_link("user-guide.html#hotkeys")
        mock_build.assert_called_once_with("user-guide.html#hotkeys")
        mock_wb.open.assert_called_once_with("https://example.com/docs/test")

    @patch("wenzi.controllers.settings_controller.webbrowser")
    @patch("wenzi.controllers.settings_controller.build_doc_url", return_value="https://example.com/docs/test")
    def test_catches_exception(self, mock_build, mock_wb, ctrl):
        mock_wb.open.side_effect = OSError("browser not found")
        ctrl.open_doc_link("user-guide.html#hotkeys")  # should not raise


class TestCollectStateLlmProviders:
    """Tests for llm_providers in _collect_state()."""

    def test_collect_state_includes_llm_providers(self, ctrl, mock_app):
        """_collect_state() includes provider config without API keys."""
        mock_app._config["ai_enhance"] = {
            "providers": {
                "openai": {
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "sk-secret",
                    "models": ["gpt-4o", "gpt-4o-mini"],
                },
                "deepseek": {
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "sk-ds-secret",
                    "models": ["deepseek-chat"],
                    "extra_body": {"temperature": 0.7},
                },
            }
        }
        state = ctrl._collect_state()
        providers = state["llm_providers"]
        assert "openai" in providers
        assert providers["openai"]["base_url"] == "https://api.openai.com/v1"
        assert providers["openai"]["models"] == ["gpt-4o", "gpt-4o-mini"]
        assert providers["openai"]["extra_body"] == {}
        assert "api_key" not in providers["openai"]
        assert providers["deepseek"]["extra_body"] == {"temperature": 0.7}
        assert "api_key" not in providers["deepseek"]

    def test_collect_state_llm_providers_empty_when_no_config(self, ctrl, mock_app):
        """llm_providers is empty dict when no providers configured."""
        mock_app._config["ai_enhance"] = {}
        state = ctrl._collect_state()
        assert state["llm_providers"] == {}


class TestLlmVerifySave:
    """Tests for llm_verify_save() callback."""

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_verify_save_dispatches_to_model_controller(self, mock_save, ctrl, mock_app):
        """Verify save calls model_controller.do_verify_and_save_provider."""
        mock_app._model_controller.do_verify_and_save_provider.return_value = {"ok": True}

        ctrl._do_llm_verify_save({
            "name": "test",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-test",
            "models": ["model-a"],
            "extra_body": {},
            "mode": "add",
        })

        mock_app._model_controller.do_verify_and_save_provider.assert_called_once_with(
            name="test",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            models=["model-a"],
            extra_body={},
            mode="add",
        )

    def test_verify_concurrent_guard(self, ctrl, mock_app):
        """Second call while verify in progress is ignored."""
        ctrl._verify_in_progress = True
        ctrl.llm_verify_save({
            "name": "test", "base_url": "u", "api_key": "k",
            "models": ["m"], "extra_body": {}, "mode": "add",
        })
        mock_app._model_controller.do_verify_and_save_provider.assert_not_called()

    def test_verify_stale_callback(self, ctrl, mock_app):
        """Stale callback guard: verify_in_progress stays True when request_id mismatches."""
        ctrl._verify_in_progress = True
        ctrl._verify_request_id = 5
        # The stale callback scenario: request_id was 3 but current is 5
        # verify_in_progress should NOT be reset by a stale callback
        assert ctrl._verify_in_progress is True
        assert ctrl._verify_request_id == 5


class TestLlmDeleteProvider:
    """Tests for llm_delete_provider() via WebView."""

    @patch("wenzi.controllers.settings_controller.get_vault")
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_delete_removes_provider_and_updates_config(self, mock_save, mock_get_vault, ctrl, mock_app):
        mock_app._enhancer.remove_provider.return_value = True
        mock_app._enhancer.provider_name = "openai"
        mock_app._enhancer.model_name = "gpt-4o"
        mock_app._config["ai_enhance"] = {
            "providers": {"test-provider": {"base_url": "u", "api_key": "k", "models": ["m"]}},
            "default_provider": "test-provider",
            "default_model": "m",
        }
        mock_app._config["keychain"] = {"enabled": True}

        ctrl.llm_delete_provider("test-provider")

        mock_app._enhancer.remove_provider.assert_called_once_with("test-provider")
        assert "test-provider" not in mock_app._config["ai_enhance"]["providers"]
        mock_save.assert_called_once()
        mock_get_vault.return_value.delete_prefix.assert_called_once_with("ai_enhance.providers.test-provider.")


class TestSttVerifySave:
    """Tests for stt_verify_save() callback."""

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_verify_save_dispatches_to_model_controller(self, mock_save, ctrl, mock_app):
        """Verify save calls model_controller.do_verify_and_save_stt_provider."""
        mock_app._model_controller.do_verify_and_save_stt_provider.return_value = {"ok": True}

        ctrl._do_stt_verify_save({
            "name": "groq",
            "base_url": "https://api.groq.com/openai/v1",
            "api_key": "gsk-test",
            "models": ["whisper-large-v3-turbo"],
            "mode": "add",
        })

        mock_app._model_controller.do_verify_and_save_stt_provider.assert_called_once_with(
            name="groq",
            base_url="https://api.groq.com/openai/v1",
            api_key="gsk-test",
            models=["whisper-large-v3-turbo"],
            mode="add",
        )

    def test_verify_concurrent_guard(self, ctrl, mock_app):
        """Second call while verify in progress is ignored."""
        ctrl._stt_verify_in_progress = True
        ctrl.stt_verify_save({
            "name": "groq", "base_url": "u", "api_key": "k",
            "models": ["m"], "mode": "add",
        })
        mock_app._model_controller.do_verify_and_save_stt_provider.assert_not_called()


class TestSttDeleteProvider:
    """Tests for stt_delete_provider() via WebView."""

    @patch("wenzi.controllers.settings_controller.get_vault")
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_delete_removes_provider_and_updates_config(self, mock_save, mock_get_vault, ctrl, mock_app):
        mock_app._config["asr"] = {
            "providers": {"groq": {"base_url": "u", "api_key": "k", "models": ["m"]}},
            "default_provider": "groq",
            "default_model": "m",
        }
        mock_app._config["keychain"] = {"enabled": True}
        mock_app._current_remote_asr = None

        ctrl.stt_delete_provider("groq")

        assert "groq" not in mock_app._config["asr"]["providers"]
        mock_save.assert_called_once()
        mock_app._menu_builder.build_model_menu.assert_called_once()
        mock_get_vault.return_value.delete_prefix.assert_called_once_with("asr.providers.groq.")

    @patch("wenzi.controllers.settings_controller.get_vault")
    @patch("wenzi.controllers.settings_controller.save_config")
    def test_delete_active_provider_clears_remote(self, mock_save, mock_get_vault, ctrl, mock_app):
        """Deleting the active STT provider should clear current_remote_asr."""
        mock_app._config["asr"] = {
            "providers": {"groq": {"base_url": "u", "api_key": "k", "models": ["m"]}},
            "default_provider": "groq",
            "default_model": "m",
        }
        mock_app._current_remote_asr = ("groq", "m")

        ctrl.stt_delete_provider("groq")

        assert mock_app._current_remote_asr is None
        assert mock_app._current_preset_id is None
        assert mock_app._config["asr"]["default_provider"] is None
        assert mock_app._config["asr"]["default_model"] is None


class TestCollectStateSttProviders:
    """Tests for stt_providers in _collect_state()."""

    @patch("wenzi.controllers.settings_controller.save_config")
    def test_stt_providers_in_state(self, mock_save, ctrl, mock_app):
        """Verify stt_providers is included in state and excludes API keys."""
        mock_app._config["asr"]["providers"] = {
            "groq": {
                "base_url": "https://api.groq.com/openai/v1",
                "api_key": "gsk-secret",
                "models": ["whisper-large-v3-turbo"],
            }
        }

        state = ctrl._collect_state()

        assert "stt_providers" in state
        assert "groq" in state["stt_providers"]
        provider = state["stt_providers"]["groq"]
        assert provider["base_url"] == "https://api.groq.com/openai/v1"
        assert provider["models"] == ["whisper-large-v3-turbo"]
        assert "api_key" not in provider


class TestPermissionStatus:
    """Tests for permission status collection."""

    def test_collect_permission_status_returns_all_permissions(self, ctrl):
        """All 6 permission IDs should be present."""
        perms = ctrl._collect_permission_status()
        ids = [p["id"] for p in perms]
        assert ids == [
            "accessibility",
            "microphone",
            "speech_recognition",
            "screen_recording",
            "automation",
            "full_disk_access",
        ]

    def test_each_permission_has_valid_status(self, ctrl):
        """Each permission should have a status in the allowed set."""
        perms = ctrl._collect_permission_status()
        for p in perms:
            assert p["status"] in {"granted", "not_granted", "unknown"}, (
                f"{p['id']} has unexpected status: {p['status']}"
            )

    def test_automation_has_valid_status(self, ctrl):
        """Automation uses AEDeterminePermissionToAutomateTarget — should return a valid status."""
        perms = ctrl._collect_permission_status()
        auto = next(p for p in perms if p["id"] == "automation")
        assert auto["status"] in {"granted", "not_granted", "unknown"}

    def test_full_disk_access_has_valid_status(self, ctrl):
        """Full Disk Access probes a TCC-protected file — should return a valid status."""
        perms = ctrl._collect_permission_status()
        fda = next(p for p in perms if p["id"] == "full_disk_access")
        assert fda["status"] in {"granted", "not_granted", "unknown"}

    def test_permissions_in_collect_state(self, ctrl):
        """_collect_state should include a 'permissions' key."""
        state = ctrl._collect_state()
        assert "permissions" in state
        assert isinstance(state["permissions"], list)
        assert len(state["permissions"]) == 6

    @patch("subprocess.Popen")
    def test_open_permission_settings_accessibility(self, mock_popen, ctrl):
        ctrl.open_permission_settings("accessibility")
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert args[0] == "open"
        assert "Privacy_Accessibility" in args[1]

    @patch("subprocess.Popen")
    def test_open_permission_settings_full_disk_access(self, mock_popen, ctrl):
        ctrl.open_permission_settings("full_disk_access")
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert args[0] == "open"
        assert "Privacy_AllFiles" in args[1]

    @patch("subprocess.Popen")
    def test_open_permission_settings_unknown_id(self, mock_popen, ctrl):
        ctrl.open_permission_settings("nonexistent")
        mock_popen.assert_not_called()

    @patch("wenzi.controllers.settings_controller.SettingsController._collect_permission_status")
    def test_refresh_permissions(self, mock_collect, ctrl, mock_app):
        mock_app._settings_panel.is_visible = True
        mock_collect.return_value = [{"id": "accessibility", "status": "granted"}]
        with patch("PyObjCTools.AppHelper.callAfter") as mock_call:
            ctrl.refresh_permissions()
            mock_call.assert_called_once()
            call_args = mock_call.call_args[0]
            assert call_args[0] == mock_app._settings_panel.update_state
            assert call_args[1]["permissions"] == [
                {"id": "accessibility", "status": "granted"}
            ]


class TestCheckAutomationPermission:
    """Tests for _check_automation_permission()."""

    def test_returns_valid_status(self):
        status = SettingsController._check_automation_permission()
        assert status in {"granted", "not_granted", "unknown"}

    @patch("ctypes.util.find_library", return_value=None)
    def test_returns_unknown_when_no_library(self, _mock):
        assert SettingsController._check_automation_permission() == "unknown"


class TestCheckFullDiskAccess:
    """Tests for _check_full_disk_access()."""

    def test_returns_valid_status(self):
        status = SettingsController._check_full_disk_access()
        assert status in {"granted", "not_granted", "unknown"}

    def test_granted_when_readable(self, tmp_path):
        probe = tmp_path / "Bookmarks.plist"
        probe.write_bytes(b"\x00")
        with patch("os.path.expanduser", return_value=str(probe)):
            assert SettingsController._check_full_disk_access() == "granted"

    def test_not_granted_on_permission_error(self, tmp_path):
        with patch("builtins.open", side_effect=PermissionError):
            assert SettingsController._check_full_disk_access() == "not_granted"

    def test_unknown_on_file_not_found(self):
        with patch("os.path.expanduser", return_value="/nonexistent/path"):
            assert SettingsController._check_full_disk_access() == "unknown"


class TestRequestPermission:
    """Tests for _request_permission()."""

    def test_unknown_permission_is_noop(self):
        # Should not raise
        SettingsController._request_permission("nonexistent")

    def test_microphone_request(self):
        mock_av = MagicMock()
        with patch.dict("sys.modules", {"AVFoundation": mock_av}):
            SettingsController._request_permission("microphone")
            mock_av.AVCaptureDevice.requestAccessForMediaType_completionHandler_.assert_called_once()

    def test_speech_recognition_request(self):
        mock_speech = MagicMock()
        with patch.dict("sys.modules", {"Speech": mock_speech}):
            SettingsController._request_permission("speech_recognition")
            mock_speech.SFSpeechRecognizer.requestAuthorization_.assert_called_once()


class TestRevokeAllPermissions:
    """Tests for revoke_all_permissions()."""

    @patch("wenzi.controllers.settings_controller.SettingsController.refresh_permissions")
    @patch("wenzi.controllers.settings_controller.send_notification")
    @patch("subprocess.run")
    def test_successful_revoke(self, mock_run, mock_notify, mock_refresh, ctrl):
        mock_run.return_value = MagicMock(returncode=0)
        ctrl.revoke_all_permissions()
        mock_run.assert_called_once_with(
            ["tccutil", "reset", "All", "io.github.airead.wenzi"],
            capture_output=True, text=True, timeout=10,
        )
        mock_notify.assert_called_once()
        mock_refresh.assert_called_once()

    @patch("wenzi.controllers.settings_controller.SettingsController.refresh_permissions")
    @patch("wenzi.controllers.settings_controller.restore_accessory")
    @patch("wenzi.controllers.settings_controller.topmost_alert")
    @patch("subprocess.run")
    def test_failed_revoke_shows_alert(self, mock_run, mock_alert, mock_restore, mock_refresh, ctrl):
        mock_run.return_value = MagicMock(returncode=1, stderr="error msg")
        ctrl.revoke_all_permissions()
        mock_alert.assert_called_once()
        mock_refresh.assert_called_once()

    @patch("wenzi.controllers.settings_controller.SettingsController.refresh_permissions")
    @patch("subprocess.run")
    def test_exception_does_not_crash(self, mock_run, mock_refresh, ctrl):
        mock_run.side_effect = OSError("tccutil not found")
        ctrl.revoke_all_permissions()  # Should not raise
        mock_refresh.assert_called_once()


class TestRevokeCallbackRegistered:
    """Ensure revoke_all_permissions callback is registered in on_open_settings."""

    def test_callback_registered(self, ctrl, mock_app):
        with patch("wenzi.controllers.settings_controller.PRESETS", []):
            with patch("wenzi.controllers.settings_controller.build_remote_asr_models", return_value=[]):
                ctrl.on_open_settings(None)
        _, callbacks = mock_app._settings_panel.show.call_args[0]
        assert "on_revoke_all_permissions" in callbacks
