"""Tests for wz.hotkey API — leader-key logic and custom key registration."""

from unittest.mock import MagicMock, patch

from wenzi.hotkey import _ALL_KEY_NAMES, _SPECIAL_VK, _VK_TO_NAME, unregister_custom_keys
from wenzi.scripting.api.hotkey import HotkeyAPI
from wenzi.scripting.registry import LeaderMapping, RemapEntry, ScriptingRegistry


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

    def test_unbind_removes_shared_tap_binding_when_token_is_zero(self):
        reg = ScriptingRegistry()
        api = HotkeyAPI(reg)
        api.bind("ctrl+cmd+v", lambda: None)
        reg.hotkeys[0].tap_key = 0
        api._shared_tap = MagicMock()

        api.unbind("ctrl+cmd+v")

        api._shared_tap.remove.assert_called_once_with(0)
        assert reg.hotkeys == []

    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_leader_press_activates(self, mock_helper):
        _, api = self._make_api()
        result = api._on_press("cmd_r")
        assert result is False  # Don't swallow FlagsChanged
        assert api._active_leader is not None
        assert api._active_leader.trigger_key == "cmd_r"

    @patch.object(HotkeyAPI, "_execute_mapping")
    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_leader_subkey_executes(self, mock_helper, mock_exec):
        _, api = self._make_api()
        api._on_press("cmd_r")
        result = api._on_press("w")
        assert result is True  # Swallowed
        assert api._leader_triggered is True

    @patch.object(HotkeyAPI, "_execute_mapping")
    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_leader_release_deactivates(self, mock_helper, mock_exec):
        _, api = self._make_api()
        api._on_press("cmd_r")
        api._on_press("w")
        api._on_release("cmd_r")
        assert api._active_leader is None

    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_leader_tap_without_subkey(self, mock_helper):
        _, api = self._make_api()
        api._on_press("cmd_r")
        api._on_release("cmd_r")
        assert api._active_leader is None
        assert api._leader_triggered is False

    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_non_leader_key_ignored(self, mock_helper):
        _, api = self._make_api()
        result = api._on_press("shift")
        assert result is False

    @patch.object(HotkeyAPI, "_execute_mapping")
    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
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

    @patch("wenzi.scripting.api.execute.subprocess")
    def test_execute_mapping_exec(self, mock_sp):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_sp.run.return_value = mock_result

        _, api = self._make_api()
        mapping = LeaderMapping(key="i", exec_cmd="echo hello")
        api._execute_mapping(mapping)
        mock_sp.run.assert_called_once()

    @patch("PyObjCTools.AppHelper.callAfter")
    def test_stop_closes_leader_alert(self, mock_call_after):
        """stop() should close the leader alert panel to prevent orphaned panels."""
        _, api = self._make_api()
        api._leader_alert = MagicMock()
        api.stop()
        mock_call_after.assert_called_with(api._close_leader_ui)


class TestToggleLeader:
    """Tests for sticky (toggle) leader mode."""

    def _make_api(self):
        reg = ScriptingRegistry()
        reg.register_leader("cmd_d", [
            LeaderMapping(key="w", app="WeChat"),
            LeaderMapping(key="t", desc="term", func=lambda: None),
        ])
        api = HotkeyAPI(reg)
        return reg, api

    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_toggle_leader_activates_sticky(self, mock_helper):
        _, api = self._make_api()
        api.toggle_leader("cmd_d")
        assert api._active_leader is not None
        assert api._active_leader.trigger_key == "cmd_d"
        assert api._sticky_leader is True

    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_toggle_leader_again_deactivates(self, mock_helper):
        _, api = self._make_api()
        api.toggle_leader("cmd_d")
        api.toggle_leader("cmd_d")
        assert api._active_leader is None
        assert api._sticky_leader is False

    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_toggle_leader_unknown_key_noop(self, mock_helper):
        _, api = self._make_api()
        api.toggle_leader("nonexistent")
        assert api._active_leader is None
        assert api._sticky_leader is False

    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_sticky_release_does_not_close(self, mock_helper):
        """Releasing the trigger key should NOT close the panel in sticky mode."""
        _, api = self._make_api()
        api.toggle_leader("cmd_d")
        api._on_release("cmd_d")
        assert api._active_leader is not None
        assert api._sticky_leader is True

    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_sticky_esc_dismisses(self, mock_helper):
        _, api = self._make_api()
        api.toggle_leader("cmd_d")
        result = api._on_press("esc")
        assert result is True
        assert api._active_leader is None
        assert api._sticky_leader is False

    @patch.object(HotkeyAPI, "_execute_mapping")
    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_sticky_subkey_executes_and_closes(self, mock_helper, mock_exec):
        _, api = self._make_api()
        api.toggle_leader("cmd_d")
        # Mock Quartz so CGEventSourceFlagsState returns 0 (no modifiers held),
        # avoiding flaky failures when real system modifier state leaks in.
        with patch("Quartz.CGEventSourceFlagsState", return_value=0):
            result = api._on_press("w")
        assert result is True
        assert api._active_leader is None
        assert api._sticky_leader is False

    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_sticky_unmatched_key_swallowed(self, mock_helper):
        _, api = self._make_api()
        api.toggle_leader("cmd_d")
        result = api._on_press("z")
        assert result is True
        # Panel stays open
        assert api._active_leader is not None
        assert api._sticky_leader is True

    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_global_click_dismisses_sticky(self, mock_helper):
        _, api = self._make_api()
        api._leader_alert = MagicMock()
        api.toggle_leader("cmd_d")
        api._on_global_click()
        assert api._active_leader is None
        assert api._sticky_leader is False

    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_global_click_noop_when_not_sticky(self, mock_helper):
        """_on_global_click should be a no-op when not in sticky mode."""
        _, api = self._make_api()
        api._leader_alert = MagicMock()
        api._on_global_click()
        api._leader_alert.close.assert_not_called()

    @patch("PyObjCTools.AppHelper.callAfter")
    def test_stop_clears_sticky_state(self, mock_call_after):
        _, api = self._make_api()
        api._leader_alert = MagicMock()
        # Manually set sticky state
        api._active_leader = api._registry.leaders["cmd_d"]
        api._sticky_leader = True
        api.stop()
        assert api._active_leader is None
        assert api._sticky_leader is False
        mock_call_after.assert_called_with(api._close_leader_ui)


class TestDefineKeys:
    """Tests for HotkeyAPI.define_key / define_keys."""

    def setup_method(self):
        unregister_custom_keys()

    def teardown_method(self):
        unregister_custom_keys()

    def test_define_key(self):
        reg = ScriptingRegistry()
        api = HotkeyAPI(reg)
        api.define_key("kp+", 69)
        assert _SPECIAL_VK["kp+"] == 69
        assert _VK_TO_NAME[69] == "kp+"
        assert "kp+" in _ALL_KEY_NAMES

    def test_define_keys_batch(self):
        reg = ScriptingRegistry()
        api = HotkeyAPI(reg)
        api.define_keys({"kp-": 78, "kp*": 67, "kp/": 75})
        assert _SPECIAL_VK["kp-"] == 78
        assert _SPECIAL_VK["kp*"] == 67
        assert _SPECIAL_VK["kp/"] == 75

    @patch.object(HotkeyAPI, "_execute_mapping")
    @patch("wenzi.scripting.api.hotkey.AppHelper", create=True)
    def test_leader_with_custom_key(self, mock_helper, mock_exec):
        """Custom keys should work in leader mappings."""
        reg = ScriptingRegistry()
        api = HotkeyAPI(reg)
        api.define_key("kp+", 69)
        reg.register_leader("cmd_r", [
            LeaderMapping(key="kp+", desc="plus", func=lambda: None),
        ])
        api._on_press("cmd_r")
        result = api._on_press("kp+")
        assert result is True
        assert api._leader_triggered is True


class TestRemap:
    """Tests for HotkeyAPI.remap / unremap."""

    def test_remap_registers_modifier_source(self):
        reg = ScriptingRegistry()
        api = HotkeyAPI(reg)
        api.remap("shift_r", "f19")
        assert 60 in reg.remaps  # shift_r vk = 60
        entry = reg.remaps[60]
        assert entry.source_name == "shift_r"
        assert entry.target_name == "f19"
        assert entry.source_vk == 60
        assert entry.target_vk == 80  # f19 vk = 80
        assert entry.is_modifier is True
        assert entry.mod_flag == 0x020000  # shift flag

    def test_remap_registers_regular_key(self):
        reg = ScriptingRegistry()
        api = HotkeyAPI(reg)
        api.remap("f13", "esc")
        assert 105 in reg.remaps  # f13 vk = 105
        entry = reg.remaps[105]
        assert entry.is_modifier is False
        assert entry.mod_flag == 0

    def test_unremap_removes(self):
        reg = ScriptingRegistry()
        api = HotkeyAPI(reg)
        api.remap("shift_r", "f19")
        assert 60 in reg.remaps
        api.unremap("shift_r")
        assert 60 not in reg.remaps

    def test_unremap_nonexistent_is_noop(self):
        reg = ScriptingRegistry()
        api = HotkeyAPI(reg)
        api.unremap("shift_r")  # should not raise

    def test_remap_unknown_key_raises(self):
        reg = ScriptingRegistry()
        api = HotkeyAPI(reg)
        import pytest
        with pytest.raises(ValueError, match="Unknown key"):
            api.remap("nonexistent", "f19")

    @patch("wenzi.hotkey.KeyRemapListener")
    def test_remap_starts_listener_when_started(self, mock_listener_cls):
        mock_listener = MagicMock()
        mock_listener.is_running.return_value = False
        mock_listener_cls.return_value = mock_listener

        reg = ScriptingRegistry()
        api = HotkeyAPI(reg)
        api._started = True
        api.remap("shift_r", "f19")

        mock_listener.add.assert_called_once_with(60, 80, True, 0x020000)
        mock_listener.start.assert_called_once()

    def test_registry_clear_clears_remaps(self):
        reg = ScriptingRegistry()
        reg.register_remap(RemapEntry(
            source_name="shift_r", target_name="f19",
            source_vk=60, target_vk=80,
            is_modifier=True, mod_flag=0x020000,
        ))
        reg.remap_listener = MagicMock()
        reg.clear()
        assert len(reg.remaps) == 0
        assert reg.remap_listener is None
