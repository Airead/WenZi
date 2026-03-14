"""Tests for the settings window."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import mock_panel_close_delegate


@pytest.fixture(autouse=True)
def _mock_appkit(mock_appkit_modules, monkeypatch):
    """Mock AppKit and Foundation modules for headless testing."""
    import voicetext.ui.settings_window as _sw

    mock_panel_close_delegate(monkeypatch, _sw)
    return mock_appkit_modules


def _make_state():
    """Create a minimal settings state dict for testing."""
    return {
        "hotkeys": {"fn": True, "right_command": False},
        "sound_enabled": True,
        "visual_indicator": True,
        "preview": True,
        "current_preset_id": "funasr-paraformer",
        "current_remote_asr": None,
        "stt_presets": [
            ("funasr-paraformer", "FunASR Paraformer", True),
            ("apple-speech", "Apple Speech", True),
        ],
        "stt_remote_models": [],
        "llm_models": [
            ("ollama", "qwen2.5:7b", "ollama / qwen2.5:7b"),
        ],
        "current_llm": ("ollama", "qwen2.5:7b"),
        "enhance_modes": [
            ("proofread", "纠错"),
            ("format", "格式化"),
        ],
        "current_enhance_mode": "proofread",
        "thinking": False,
        "vocab_enabled": True,
        "vocab_count": 42,
        "auto_build": True,
        "history_enabled": False,
    }


def _make_callbacks():
    """Create a dict of mock callbacks."""
    names = [
        "on_hotkey_toggle", "on_record_hotkey", "on_sound_toggle",
        "on_visual_toggle", "on_preview_toggle", "on_stt_select",
        "on_stt_remote_select", "on_stt_add_provider", "on_stt_remove_provider",
        "on_llm_select", "on_llm_add_provider", "on_llm_remove_provider",
        "on_enhance_mode_select", "on_enhance_add_mode", "on_enhance_mode_edit",
        "on_thinking_toggle", "on_vocab_toggle", "on_auto_build_toggle",
        "on_history_toggle", "on_vocab_build",
        "on_show_config", "on_edit_config", "on_reload_config",
    ]
    return {name: MagicMock(name=name) for name in names}


class TestSettingsPanelInit:
    """Tests for SettingsPanel initialization."""

    def test_init_defaults(self):
        from voicetext.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        assert panel._panel is None
        assert panel._tab_view is None
        assert not panel.is_visible

    def test_show_creates_panel(self):
        from voicetext.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        callbacks = _make_callbacks()

        panel.show(state, callbacks)

        assert panel._panel is not None
        assert panel._tab_view is not None
        assert panel._callbacks == callbacks

    def test_show_rebuilds_panel_each_time(self):
        from voicetext.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        callbacks = _make_callbacks()

        panel.show(state, callbacks)
        first_panel = panel._panel
        # Simulate orderOut_ call clearing the panel in show()
        assert first_panel is not None

        # Second show should set panel to None then rebuild
        panel.show(state, callbacks)
        # Verify _build_panel was called (panel exists)
        assert panel._panel is not None


class TestSettingsPanelClose:
    """Tests for closing the settings panel."""

    def test_close_clears_delegate(self):
        from voicetext.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        panel.show(_make_state(), _make_callbacks())

        panel.close()
        assert panel._close_delegate is None


class TestSettingsCallbacks:
    """Tests for callback invocation from action handlers."""

    def _make_panel(self):
        from voicetext.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        callbacks = _make_callbacks()
        panel.show(state, callbacks)
        return panel, callbacks

    def test_toolbar_button_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        panel._set_meta(sender, cb_name="on_show_config")
        panel.toolbarButtonClicked_(sender)

        cbs["on_show_config"].assert_called_once()

    def test_hotkey_check_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        panel._set_meta(sender, key_name="fn")
        sender.state.return_value = 0  # unchecked
        panel.hotkeyCheckChanged_(sender)

        cbs["on_hotkey_toggle"].assert_called_once_with("fn", False)

    def test_sound_check_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        sender.state.return_value = 1
        panel.soundCheckChanged_(sender)

        cbs["on_sound_toggle"].assert_called_once_with(True)

    def test_visual_check_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        sender.state.return_value = 0
        panel.visualCheckChanged_(sender)

        cbs["on_visual_toggle"].assert_called_once_with(False)

    def test_preview_check_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        sender.state.return_value = 1
        panel.previewCheckChanged_(sender)

        cbs["on_preview_toggle"].assert_called_once_with(True)

    def test_thinking_check_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        sender.state.return_value = 1
        panel.thinkingCheckChanged_(sender)

        cbs["on_thinking_toggle"].assert_called_once_with(True)

    def test_vocab_check_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        sender.state.return_value = 0
        panel.vocabCheckChanged_(sender)

        cbs["on_vocab_toggle"].assert_called_once_with(False)

    def test_auto_build_check_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        sender.state.return_value = 1
        panel.autoBuildCheckChanged_(sender)

        cbs["on_auto_build_toggle"].assert_called_once_with(True)

    def test_history_check_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        sender.state.return_value = 0
        panel.historyCheckChanged_(sender)

        cbs["on_history_toggle"].assert_called_once_with(False)

    def test_record_hotkey_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        panel.recordHotkeyClicked_(sender)

        cbs["on_record_hotkey"].assert_called_once()

    def test_build_vocab_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        panel.buildVocabClicked_(sender)

        cbs["on_vocab_build"].assert_called_once()

    def test_enhance_mode_selected_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        panel._set_meta(sender, mode_id="proofread")
        panel.enhanceModeSelected_(sender)

        cbs["on_enhance_mode_select"].assert_called_once_with("proofread")

    def test_add_mode_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        panel.addModeClicked_(sender)

        cbs["on_enhance_add_mode"].assert_called_once()

    def test_llm_model_selected_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        panel._set_meta(sender, provider="ollama", model="qwen2.5:7b")
        panel.llmModelSelected_(sender)

        cbs["on_llm_select"].assert_called_once_with("ollama", "qwen2.5:7b")

    def test_stt_add_provider_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        panel.sttAddProviderClicked_(sender)

        cbs["on_stt_add_provider"].assert_called_once()

    def test_llm_add_provider_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        panel.llmAddProviderClicked_(sender)

        cbs["on_llm_add_provider"].assert_called_once()

    def test_enhance_mode_edit_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        panel._set_meta(sender, mode_id="proofread")
        panel.enhanceModeEditClicked_(sender)

        cbs["on_enhance_mode_edit"].assert_called_once_with("proofread")


class TestSettingsStateUpdate:
    """Tests for state update methods."""

    def _make_panel(self):
        from voicetext.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        callbacks = _make_callbacks()
        panel.show(state, callbacks)
        return panel

    def test_update_enhance_mode(self):
        panel = self._make_panel()

        # Should not raise even if buttons are mocks
        panel.update_enhance_mode("format")

    def test_update_thinking(self):
        panel = self._make_panel()

        panel.update_thinking(True)
        # Verify it doesn't crash

    def test_update_vocab(self):
        panel = self._make_panel()

        panel.update_vocab(True, 99)

    def test_update_hotkey(self):
        panel = self._make_panel()

        panel.update_hotkey("fn", False)


class TestHintHelpers:
    """Tests for _make_hint and _add_hint helper methods."""

    def test_make_hint_returns_label(self):
        from voicetext.ui.settings_window import SettingsPanel

        hint = SettingsPanel._make_hint("test hint", 10, 100, 200)
        # Should call labelWithString_ and configure font/color
        assert hint is not None

    def test_make_hint_sets_font_and_color(self):
        from AppKit import NSColor
        from voicetext.ui.settings_window import SettingsPanel

        hint = SettingsPanel._make_hint("hello", 0, 0, 100)
        hint.setFont_.assert_called_once()
        hint.setTextColor_.assert_called_once_with(NSColor.secondaryLabelColor())

    def test_add_hint_returns_updated_y(self):
        from voicetext.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        parent = MagicMock()
        initial_y = 200
        new_y = panel._add_hint("some hint", 10, initial_y, 300, parent)

        expected_y = initial_y - (panel._HINT_HEIGHT + panel._HINT_GAP)
        assert new_y == expected_y

    def test_add_hint_adds_subview(self):
        from voicetext.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        parent = MagicMock()
        panel._add_hint("some hint", 10, 200, 300, parent)

        parent.addSubview_.assert_called_once()


class TestSettingsCallbackErrorHandling:
    """Tests for error handling in callbacks."""

    def test_callback_exception_logged_not_raised(self):
        from voicetext.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        callbacks = _make_callbacks()
        callbacks["on_sound_toggle"].side_effect = RuntimeError("test error")
        panel.show(state, callbacks)

        sender = MagicMock()
        sender.state.return_value = 1

        # Should not raise
        panel.soundCheckChanged_(sender)

    def test_missing_callback_logged_not_raised(self):
        from voicetext.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        # Empty callbacks
        panel.show(state, {})

        sender = MagicMock()
        sender.state.return_value = 1

        # Should not raise
        panel.soundCheckChanged_(sender)
