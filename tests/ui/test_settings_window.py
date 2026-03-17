"""Tests for the settings window."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import mock_panel_close_delegate


@pytest.fixture(autouse=True)
def _mock_appkit(mock_appkit_modules, monkeypatch):
    """Mock AppKit and Foundation modules for headless testing."""
    import wenzi.ui.settings_window as _sw

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
            ("proofread", "纠错", 10),
            ("format", "格式化", 30),
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
        "on_hotkey_toggle", "on_record_hotkey",
        "on_restart_key_select", "on_cancel_key_select",
        "on_sound_toggle",
        "on_visual_toggle", "on_preview_toggle",
        "on_stt_select",
        "on_stt_remote_select", "on_stt_add_provider", "on_stt_remove_provider",
        "on_llm_select", "on_llm_add_provider", "on_llm_remove_provider",
        "on_enhance_mode_select", "on_enhance_add_mode", "on_enhance_mode_edit",
        "on_thinking_toggle", "on_vocab_toggle", "on_auto_build_toggle",
        "on_history_toggle", "on_vocab_build",
        "on_tab_change",
        "on_reveal_config_folder",
    ]
    return {name: MagicMock(name=name) for name in names}


class TestSettingsPanelInit:
    """Tests for SettingsPanel initialization."""

    def test_init_defaults(self):
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        assert panel._panel is None
        assert panel._tab_view is None
        assert not panel.is_visible

    def test_show_creates_panel(self):
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        callbacks = _make_callbacks()

        panel.show(state, callbacks)

        assert panel._panel is not None
        assert panel._tab_view is not None
        assert panel._callbacks == callbacks

    def test_show_rebuilds_panel_each_time(self):
        from wenzi.ui.settings_window import SettingsPanel

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
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        panel.show(_make_state(), _make_callbacks())

        panel.close()
        assert panel._close_delegate is None


class TestSettingsCallbacks:
    """Tests for callback invocation from action handlers."""

    def _make_panel(self):
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        callbacks = _make_callbacks()
        panel.show(state, callbacks)
        return panel, callbacks

    def test_reveal_config_folder_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        panel.revealConfigFolderClicked_(sender)

        cbs["on_reveal_config_folder"].assert_called_once()

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

    def test_restart_key_changed_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        item = MagicMock()
        item.representedObject.return_value = "ctrl"
        sender.selectedItem.return_value = item
        panel.restartKeyChanged_(sender)

        cbs["on_restart_key_select"].assert_called_once_with("ctrl")

    def test_cancel_key_changed_calls_callback(self):
        panel, cbs = self._make_panel()

        sender = MagicMock()
        item = MagicMock()
        item.representedObject.return_value = "esc"
        sender.selectedItem.return_value = item
        panel.cancelKeyChanged_(sender)

        cbs["on_cancel_key_select"].assert_called_once_with("esc")

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


class TestEnhanceModeOrder:
    """Tests that enhance modes preserve insertion order from state."""

    def test_modes_follow_state_order(self):
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        # Provide modes whose alphabetical label order differs from list order
        state["enhance_modes"] = [
            ("proofread", "纠错", 10),
            ("translate", "翻译为英文", 20),
            ("format", "格式化", 30),
        ]
        callbacks = _make_callbacks()
        panel.show(state, callbacks)

        # _enhance_mode_buttons is an ordered dict: "off" first, then modes
        keys = list(panel._enhance_mode_buttons.keys())
        assert keys == ["off", "proofread", "translate", "format"]


class TestSettingsStateUpdate:
    """Tests for state update methods."""

    def _make_panel(self):
        from wenzi.ui.settings_window import SettingsPanel

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
        from wenzi.ui.settings_window import SettingsPanel

        hint = SettingsPanel._make_hint("test hint", 10, 100, 200)
        # Should call labelWithString_ and configure font/color
        assert hint is not None

    def test_make_hint_sets_font_and_color(self):
        from AppKit import NSColor
        from wenzi.ui.settings_window import SettingsPanel

        hint = SettingsPanel._make_hint("hello", 0, 0, 100)
        hint.setFont_.assert_called_once()
        hint.setTextColor_.assert_called_once_with(NSColor.secondaryLabelColor())

    def test_add_hint_returns_updated_y(self):
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        parent = MagicMock()
        initial_y = 200
        new_y = panel._add_hint("some hint", 10, initial_y, 300, parent)

        expected_y = initial_y - (panel._HINT_HEIGHT + panel._HINT_GAP)
        assert new_y == expected_y

    def test_add_hint_adds_subview(self):
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        parent = MagicMock()
        panel._add_hint("some hint", 10, 200, 300, parent)

        parent.addSubview_.assert_called_once()


class TestModelSizeDisplay:
    """Tests for model size display in STT tab."""

    def test_format_size_mb(self):
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        assert panel._format_size(50 * 1024 * 1024) == "50 MB"
        assert panel._format_size(512 * 1024 * 1024) == "512 MB"

    def test_format_size_gb(self):
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        assert panel._format_size(2 * 1024 * 1024 * 1024) == "2.0 GB"
        assert panel._format_size(int(1.5 * 1024 * 1024 * 1024)) == "1.5 GB"

    def test_stt_tab_shows_sizes(self):
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        state["stt_model_sizes"] = {
            "funasr-paraformer": 200 * 1024 * 1024,  # 200 MB
        }
        callbacks = _make_callbacks()
        panel.show(state, callbacks)

        # Panel should build without errors
        assert panel._panel is not None


class TestTabScrollReset:
    """Tests for tab change scroll-to-top behavior."""

    def test_tab_delegate_method_exists(self):
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        assert hasattr(panel, "tabView_didSelectTabViewItem_")

    def test_tab_delegate_does_not_raise(self):
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        callbacks = _make_callbacks()
        panel.show(state, callbacks)

        # Simulate tab switch - should not raise
        mock_tab_item = MagicMock()
        panel.tabView_didSelectTabViewItem_(panel._tab_view, mock_tab_item)

    def test_tab_change_fires_callback(self):
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        callbacks = _make_callbacks()
        panel.show(state, callbacks)

        mock_tab_item = MagicMock()
        mock_tab_item.identifier.return_value = "stt"
        panel.tabView_didSelectTabViewItem_(panel._tab_view, mock_tab_item)

        callbacks["on_tab_change"].assert_called_once_with("stt")

    def test_last_tab_restored_on_show(self):
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        state["last_tab"] = "llm"
        callbacks = _make_callbacks()
        panel.show(state, callbacks)

        # Verify selectTabViewItemWithIdentifier_ was called with "llm"
        panel._tab_view.selectTabViewItemWithIdentifier_.assert_called_with("llm")

    def test_default_tab_no_extra_select(self):
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        # No last_tab or last_tab == "general" should not call selectTabViewItemWithIdentifier_
        callbacks = _make_callbacks()
        panel.show(state, callbacks)

        panel._tab_view.selectTabViewItemWithIdentifier_.assert_not_called()


class TestSettingsCallbackErrorHandling:
    """Tests for error handling in callbacks."""

    def test_callback_exception_logged_not_raised(self):
        from wenzi.ui.settings_window import SettingsPanel

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
        from wenzi.ui.settings_window import SettingsPanel

        panel = SettingsPanel()
        state = _make_state()
        # Empty callbacks
        panel.show(state, {})

        sender = MagicMock()
        sender.state.return_value = 1

        # Should not raise
        panel.soundCheckChanged_(sender)
