"""Tests for vt.hotkey API — leader-key logic."""

from unittest.mock import patch, MagicMock

from voicetext.scripting.registry import LeaderMapping, ScriptingRegistry
from voicetext.scripting.api.hotkey import HotkeyAPI


class TestHotkeyAPI:
    def _make_api(self):
        reg = ScriptingRegistry()
        reg.register_leader("cmd_r", [
            LeaderMapping(key="w", app="WeChat"),
            LeaderMapping(key="d", desc="date", func=lambda: None),
        ])
        api = HotkeyAPI(reg)
        return reg, api

    def test_bind_registers(self):
        reg = ScriptingRegistry()
        api = HotkeyAPI(reg)
        api.bind("ctrl+cmd+v", lambda: None)
        assert len(reg.hotkeys) == 1
        assert reg.hotkeys[0].hotkey_str == "ctrl+cmd+v"

    @patch("voicetext.scripting.api.hotkey.AppHelper", create=True)
    def test_leader_press_activates(self, mock_helper):
        _, api = self._make_api()
        result = api._on_press("cmd_r")
        assert result is False  # Don't swallow FlagsChanged
        assert api._active_leader is not None
        assert api._active_leader.trigger_key == "cmd_r"

    @patch.object(HotkeyAPI, "_execute_mapping")
    @patch("voicetext.scripting.api.hotkey.AppHelper", create=True)
    def test_leader_subkey_executes(self, mock_helper, mock_exec):
        _, api = self._make_api()
        api._on_press("cmd_r")
        result = api._on_press("w")
        assert result is True  # Swallowed
        assert api._leader_triggered is True

    @patch.object(HotkeyAPI, "_execute_mapping")
    @patch("voicetext.scripting.api.hotkey.AppHelper", create=True)
    def test_leader_release_deactivates(self, mock_helper, mock_exec):
        _, api = self._make_api()
        api._on_press("cmd_r")
        api._on_press("w")
        api._on_release("cmd_r")
        assert api._active_leader is None

    @patch("voicetext.scripting.api.hotkey.AppHelper", create=True)
    def test_leader_tap_without_subkey(self, mock_helper):
        _, api = self._make_api()
        api._on_press("cmd_r")
        api._on_release("cmd_r")
        assert api._active_leader is None
        assert api._leader_triggered is False

    @patch("voicetext.scripting.api.hotkey.AppHelper", create=True)
    def test_non_leader_key_ignored(self, mock_helper):
        _, api = self._make_api()
        result = api._on_press("shift")
        assert result is False

    @patch.object(HotkeyAPI, "_execute_mapping")
    @patch("voicetext.scripting.api.hotkey.AppHelper", create=True)
    def test_unmatched_subkey_still_swallowed(self, mock_helper, mock_exec):
        _, api = self._make_api()
        api._on_press("cmd_r")
        result = api._on_press("z")
        assert result is True  # Still swallowed during leader mode
        mock_exec.assert_not_called()  # No mapping for "z"

    def test_execute_mapping_app(self):
        _, api = self._make_api()
        with patch("AppKit.NSWorkspace") as mock_ws_cls:
            ws = MagicMock()
            ws.launchApplication_.return_value = True
            mock_ws_cls.sharedWorkspace.return_value = ws
            api._execute_mapping(LeaderMapping(key="w", app="WeChat"))
            ws.launchApplication_.assert_called_once_with("WeChat")

    def test_execute_mapping_func(self):
        result = []
        mapping = LeaderMapping(key="d", func=lambda: result.append(1))
        _, api = self._make_api()
        api._execute_mapping(mapping)
        assert result == [1]

    @patch("voicetext.scripting.api.execute.subprocess")
    def test_execute_mapping_exec(self, mock_sp):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_sp.run.return_value = mock_result

        _, api = self._make_api()
        mapping = LeaderMapping(key="i", exec_cmd="echo hello")
        api._execute_mapping(mapping)
        mock_sp.run.assert_called_once()
