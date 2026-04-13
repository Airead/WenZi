"""Tests for the hotkey module."""

import threading
from unittest.mock import MagicMock

import pytest

from wenzi.hotkey import (
    _ALL_KEY_NAMES,
    _KEYCODE_MAP,
    _MOD_FLAGS,
    _SPECIAL_VK,
    _VK_TO_NAME,
    HoldHotkeyListener,
    KeyRemapListener,
    MultiHotkeyListener,
    SharedHotkeyTap,
    TapHotkeyListener,
    _is_fn_key,
    _name_to_vk,
    _parse_hotkey_for_quartz,
    register_custom_key,
    unregister_custom_keys,
)


def _flush():
    """Wait for all pending hotkey executor tasks to complete."""
    from wenzi.hotkey import _hotkey_executor
    _hotkey_executor.submit(lambda: None).result(timeout=1.0)


class TestNameToVk:
    def test_regular_key(self):
        assert _name_to_vk("a") == 0
        assert _name_to_vk("v") == 9

    def test_special_key(self):
        assert _name_to_vk("f2") == 120
        assert _name_to_vk("esc") == 53
        assert _name_to_vk("fn") == 63
        assert _name_to_vk("space") == 49

    def test_modifier_key(self):
        assert _name_to_vk("cmd") == 55
        assert _name_to_vk("ctrl") == 59
        assert _name_to_vk("alt") == 58
        assert _name_to_vk("shift") == 56

    def test_aliases(self):
        assert _name_to_vk("option") == _name_to_vk("alt")
        assert _name_to_vk("command") == _name_to_vk("cmd")

    def test_case_insensitive(self):
        assert _name_to_vk("F2") == 120

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown key"):
            _name_to_vk("nonexistent")


class TestIsFnKey:
    def test_fn_variants(self):
        assert _is_fn_key("fn") is True
        assert _is_fn_key("FN") is True
        assert _is_fn_key(" fn ") is True

    def test_non_fn(self):
        assert _is_fn_key("f2") is False
        assert _is_fn_key("cmd") is False


class TestCustomKeyRegistration:
    """Tests for register_custom_key / unregister_custom_keys."""

    def setup_method(self):
        # Ensure clean state before each test
        unregister_custom_keys()

    def teardown_method(self):
        # Restore built-in maps after each test
        unregister_custom_keys()

    def test_register_new_key(self):
        register_custom_key("kp+", 69)
        assert _name_to_vk("kp+") == 69
        assert _VK_TO_NAME[69] == "kp+"
        assert "kp+" in _ALL_KEY_NAMES

    def test_register_multiple_keys(self):
        register_custom_key("kp-", 78)
        register_custom_key("kp*", 67)
        assert _name_to_vk("kp-") == 78
        assert _name_to_vk("kp*") == 67

    def test_unregister_removes_custom_keys(self):
        register_custom_key("mykey", 200)
        assert "mykey" in _ALL_KEY_NAMES
        unregister_custom_keys()
        assert "mykey" not in _ALL_KEY_NAMES
        assert 200 not in _VK_TO_NAME
        assert "mykey" not in _SPECIAL_VK

    def test_unregister_restores_overwritten_builtin(self):
        original_vk = _SPECIAL_VK["space"]
        register_custom_key("space", 999)
        assert _SPECIAL_VK["space"] == 999
        assert _VK_TO_NAME[999] == "space"
        unregister_custom_keys()
        assert _SPECIAL_VK["space"] == original_vk
        assert _VK_TO_NAME[original_vk] == "space"
        assert 999 not in _VK_TO_NAME

    def test_unregister_restores_overwritten_reverse_mapping(self):
        # keycode 49 is "space" — register a custom key with the same keycode
        assert _VK_TO_NAME[49] == "space"
        register_custom_key("my_key", 49)
        assert _VK_TO_NAME[49] == "my_key"
        unregister_custom_keys()
        assert _VK_TO_NAME[49] == "space"
        assert "my_key" not in _ALL_KEY_NAMES

    def test_name_normalized_lowercase(self):
        register_custom_key("  KP_Enter  ", 76)
        assert _name_to_vk("kp_enter") == 76

    def test_idempotent_unregister(self):
        """Calling unregister twice should be safe."""
        register_custom_key("mykey", 200)
        unregister_custom_keys()
        unregister_custom_keys()
        assert "mykey" not in _ALL_KEY_NAMES


class TestVkToName:
    def test_reverse_lookup(self):
        assert _VK_TO_NAME[0] == "a"
        assert _VK_TO_NAME[120] == "f2"
        assert _VK_TO_NAME[63] == "fn"
        assert _VK_TO_NAME[55] == "cmd"


class TestHoldHotkeyListener:
    def test_fn_key_creates_listener(self):
        listener = HoldHotkeyListener("fn", MagicMock(), MagicMock())
        assert listener._target_vk == 63

    def test_regular_key_creates_listener(self):
        listener = HoldHotkeyListener("f2", MagicMock(), MagicMock())
        assert listener._target_vk == 120

    def test_press_and_release(self):
        on_press = MagicMock()
        on_release = MagicMock()

        listener = HoldHotkeyListener("f2", on_press, on_release)

        listener._handle_press("f2")
        on_press.assert_called_once()
        assert listener._held is True

        listener._handle_release("f2")
        on_release.assert_called_once()
        assert listener._held is False

    def test_repeated_press_ignored(self):
        on_press = MagicMock()
        listener = HoldHotkeyListener("f2", on_press, MagicMock())

        listener._handle_press("f2")
        listener._handle_press("f2")
        assert on_press.call_count == 1

    def test_wrong_key_ignored(self):
        on_press = MagicMock()
        listener = HoldHotkeyListener("f2", on_press, MagicMock())

        listener._handle_press("f3")
        on_press.assert_not_called()


class TestParseHotkeyForQuartz:
    def test_ctrl_cmd_v(self):
        mod_flags, keycode = _parse_hotkey_for_quartz("ctrl+cmd+v")
        assert mod_flags == (_MOD_FLAGS["ctrl"] | _MOD_FLAGS["cmd"])
        assert keycode == _KEYCODE_MAP["v"]

    def test_shift_a(self):
        mod_flags, keycode = _parse_hotkey_for_quartz("shift+a")
        assert mod_flags == _MOD_FLAGS["shift"]
        assert keycode == _KEYCODE_MAP["a"]

    def test_option_alias(self):
        mod_flags, keycode = _parse_hotkey_for_quartz("option+c")
        assert mod_flags == _MOD_FLAGS["alt"]
        assert keycode == _KEYCODE_MAP["c"]

    def test_command_alias(self):
        mod_flags, keycode = _parse_hotkey_for_quartz("command+v")
        assert mod_flags == _MOD_FLAGS["cmd"]
        assert keycode == _KEYCODE_MAP["v"]

    def test_case_insensitive(self):
        mod_flags, keycode = _parse_hotkey_for_quartz("Ctrl+Cmd+V")
        assert mod_flags == (_MOD_FLAGS["ctrl"] | _MOD_FLAGS["cmd"])
        assert keycode == _KEYCODE_MAP["v"]

    def test_strips_spaces(self):
        mod_flags, keycode = _parse_hotkey_for_quartz(" ctrl + cmd + v ")
        assert mod_flags == (_MOD_FLAGS["ctrl"] | _MOD_FLAGS["cmd"])
        assert keycode == _KEYCODE_MAP["v"]

    def test_no_modifier_raises(self):
        with pytest.raises(ValueError, match="at least one modifier"):
            _parse_hotkey_for_quartz("v")

    def test_no_trigger_key_raises(self):
        with pytest.raises(ValueError, match="exactly one trigger key"):
            _parse_hotkey_for_quartz("ctrl+cmd")

    def test_multiple_trigger_keys_raises(self):
        with pytest.raises(ValueError, match="exactly one trigger key"):
            _parse_hotkey_for_quartz("ctrl+a+b")

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError, match="Unknown key"):
            _parse_hotkey_for_quartz("ctrl+nonsense")


class TestQuartzAllKeysListener:
    def test_init_snapshots_current_modifier_flags(self):
        """_mod_flags_prev should reflect currently-held modifiers to avoid
        false press events after listener restart (e.g. during script reload)."""
        from wenzi.hotkey import _QuartzAllKeysListener

        listener = _QuartzAllKeysListener(
            on_press=MagicMock(), on_release=MagicMock()
        )
        # Should be an int (snapshot of current flags), not necessarily 0
        assert isinstance(listener._mod_flags_prev, int)

    def test_stop_delegates_to_runner(self):
        """stop() must delegate to _runner.stop() and clear the reference."""
        from wenzi.hotkey import _QuartzAllKeysListener

        listener = _QuartzAllKeysListener(
            on_press=MagicMock(), on_release=MagicMock()
        )
        mock_runner = MagicMock()
        listener._runner = mock_runner

        listener.stop()

        mock_runner.stop.assert_called_once()
        assert listener._runner is None

    def test_tap_timeout_resyncs_modifier_flags(self):
        """When CGEventTap is disabled by timeout, missed modifier releases
        should be detected by polling CGEventSourceFlagsState."""
        import wenzi._cgeventtap as cg
        from wenzi.hotkey import _FN_FLAG, _QuartzAllKeysListener

        on_release = MagicMock()
        listener = _QuartzAllKeysListener(
            on_press=MagicMock(), on_release=on_release
        )
        fake_tap = MagicMock()
        listener._runner = MagicMock()
        listener._runner.tap = fake_tap
        # Simulate: fn was held (flag was set)
        listener._mod_flags_prev = _FN_FLAG

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cg, "CGEventTapEnable", lambda tap, en: None)
            # Current flags show fn is no longer held
            mp.setattr(cg, "CGEventSourceFlagsState", lambda src: 0)

            listener._callback(
                None, 0xFFFFFFFE, None, None
            )

        on_release.assert_called_once_with("fn")
        assert listener._mod_flags_prev == 0

    def test_tap_timeout_no_false_release(self):
        """When modifier is still held during tap timeout, no release fires."""
        import wenzi._cgeventtap as cg
        from wenzi.hotkey import _FN_FLAG, _QuartzAllKeysListener

        on_release = MagicMock()
        listener = _QuartzAllKeysListener(
            on_press=MagicMock(), on_release=on_release
        )
        fake_tap = MagicMock()
        listener._runner = MagicMock()
        listener._runner.tap = fake_tap
        listener._mod_flags_prev = _FN_FLAG

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cg, "CGEventTapEnable", lambda tap, en: None)
            # fn is still held
            mp.setattr(cg, "CGEventSourceFlagsState", lambda src: _FN_FLAG)

            listener._callback(
                None, 0xFFFFFFFE, None, None
            )

        on_release.assert_not_called()
        assert listener._mod_flags_prev == _FN_FLAG


class TestTapHotkeyListener:
    def test_creation(self):
        cb = MagicMock()
        listener = TapHotkeyListener("ctrl+cmd+v", cb)
        assert listener._mod_flags == (_MOD_FLAGS["ctrl"] | _MOD_FLAGS["cmd"])
        assert listener._keycode == _KEYCODE_MAP["v"]
        assert listener._on_activate is cb

    def test_stop_when_not_started(self):
        listener = TapHotkeyListener("ctrl+v", MagicMock())
        listener.stop()

    def test_stop_delegates_to_runner(self):
        """stop() must delegate to _runner.stop() and clear the reference."""
        listener = TapHotkeyListener("ctrl+v", MagicMock())
        mock_runner = MagicMock()
        listener._runner = mock_runner

        listener.stop()

        mock_runner.stop.assert_called_once()
        assert listener._runner is None


class TestSharedHotkeyTap:
    def test_add_parses_and_stores(self):
        tap = SharedHotkeyTap()
        cb = MagicMock()
        token = tap.add("ctrl+cmd+v", cb)
        key = (_MOD_FLAGS["ctrl"] | _MOD_FLAGS["cmd"], _KEYCODE_MAP["v"])
        assert token in tap._bindings[key]
        assert tap._bindings[key][token] is cb
        assert tap._tokens[token] == key
        # Cleanup — stop the auto-started runner
        tap.stop()

    def test_remove(self):
        tap = SharedHotkeyTap()
        cb = MagicMock()
        token = tap.add("cmd+space", cb)
        key = (_MOD_FLAGS["cmd"], _SPECIAL_VK["space"])
        assert token in tap._bindings[key]
        tap.remove(token)
        assert key not in tap._bindings
        tap.stop()

    def test_remove_nonexistent_key_is_noop(self):
        tap = SharedHotkeyTap()
        tap.remove(999)  # no error

    def test_stop_clears_bindings_and_runner(self):
        tap = SharedHotkeyTap()
        tap.add("cmd+a", MagicMock())
        assert tap._runner is not None
        tap.stop()
        assert tap._bindings == {}
        assert tap._tokens == {}
        assert tap._runner is None

    def test_callback_matches_and_dispatches(self):
        """Simulate a kCGEventKeyDown event and verify callback dispatch."""
        import time

        tap = SharedHotkeyTap()
        cb = MagicMock()
        tap.add("cmd+v", cb)

        from wenzi import _cgeventtap as cg

        # Build a fake event: cmd (0x100000) + v (keycode 9)
        mock_event = 0xDEAD  # non-zero sentinel
        get_field_orig = cg.CGEventGetIntegerValueField
        get_flags_orig = cg.CGEventGetFlags

        def fake_get_field(event, field):
            if field == cg.kCGKeyboardEventKeycode:
                return 9  # 'v'
            return 0

        def fake_get_flags(event):
            return 0x100000  # cmd

        cg.CGEventGetIntegerValueField = fake_get_field
        cg.CGEventGetFlags = fake_get_flags
        try:
            result = tap._callback(None, cg.kCGEventKeyDown, mock_event, None)
            assert result is None  # swallowed
            # Wait for _submit_callback dispatch
            for _ in range(50):
                if cb.called:
                    break
                time.sleep(0.01)
            cb.assert_called_once()
        finally:
            cg.CGEventGetIntegerValueField = get_field_orig
            cg.CGEventGetFlags = get_flags_orig
            tap.stop()

    def test_callback_passes_through_non_matching(self):
        tap = SharedHotkeyTap()
        tap.add("cmd+v", MagicMock())

        from wenzi import _cgeventtap as cg

        mock_event = 0xBEEF
        get_field_orig = cg.CGEventGetIntegerValueField
        get_flags_orig = cg.CGEventGetFlags

        def fake_get_field(event, field):
            if field == cg.kCGKeyboardEventKeycode:
                return 0  # 'a', not 'v'
            return 0

        def fake_get_flags(event):
            return 0x100000  # cmd

        cg.CGEventGetIntegerValueField = fake_get_field
        cg.CGEventGetFlags = fake_get_flags
        try:
            result = tap._callback(None, cg.kCGEventKeyDown, mock_event, None)
            assert result == mock_event  # passed through
        finally:
            cg.CGEventGetIntegerValueField = get_field_orig
            cg.CGEventGetFlags = get_flags_orig
            tap.stop()

    def test_multiple_hotkeys_single_tap(self):
        """Multiple add() calls should share one runner."""
        tap = SharedHotkeyTap()
        tap.add("cmd+a", MagicMock())
        runner1 = tap._runner
        tap.add("cmd+b", MagicMock())
        assert tap._runner is runner1  # same runner, not recreated
        assert len(tap._bindings) == 2
        tap.stop()

    def test_duplicate_hotkeys_get_distinct_tokens(self):
        tap = SharedHotkeyTap()
        cb1 = MagicMock()
        cb2 = MagicMock()

        token1 = tap.add("cmd+a", cb1)
        token2 = tap.add("cmd+a", cb2)

        key = (_MOD_FLAGS["cmd"], _KEYCODE_MAP["a"])
        assert token1 != token2
        assert set(tap._bindings[key]) == {token1, token2}

        tap.remove(token1)
        assert set(tap._bindings[key]) == {token2}
        assert token1 not in tap._tokens
        assert tap._runner is not None

        tap.remove(token2)
        assert key not in tap._bindings
        assert token2 not in tap._tokens
        assert tap._runner is None

    def test_duplicate_hotkeys_dispatch_all_callbacks(self):
        import time

        tap = SharedHotkeyTap()
        cb1 = MagicMock()
        cb2 = MagicMock()
        tap.add("cmd+v", cb1)
        tap.add("cmd+v", cb2)

        from wenzi import _cgeventtap as cg

        mock_event = 0xDEAD
        get_field_orig = cg.CGEventGetIntegerValueField
        get_flags_orig = cg.CGEventGetFlags

        def fake_get_field(event, field):
            if field == cg.kCGKeyboardEventKeycode:
                return 9
            return 0

        def fake_get_flags(event):
            return 0x100000

        cg.CGEventGetIntegerValueField = fake_get_field
        cg.CGEventGetFlags = fake_get_flags
        try:
            result = tap._callback(None, cg.kCGEventKeyDown, mock_event, None)
            assert result is None
            for _ in range(50):
                if cb1.called and cb2.called:
                    break
                time.sleep(0.01)
            cb1.assert_called_once()
            cb2.assert_called_once()
        finally:
            cg.CGEventGetIntegerValueField = get_field_orig
            cg.CGEventGetFlags = get_flags_orig
            tap.stop()

    def test_remove_last_binding_stops_runner(self):
        tap = SharedHotkeyTap()
        runner = MagicMock()
        tap._runner = runner
        token = tap.add("cmd+a", MagicMock())

        tap.remove(token)

        runner.stop.assert_called_once()
        assert tap._runner is None


class TestMultiHotkeyListener:
    def test_creation_with_fn(self):
        listener = MultiHotkeyListener(["fn", "f2"], MagicMock(), MagicMock())
        assert "fn" in listener._enabled_names
        assert "f2" in listener._enabled_names
        assert 63 in listener._target_vks  # fn vk
        assert 120 in listener._target_vks  # f2 vk

    def test_press_dispatched_to_background_thread(self):
        """on_press callback should run in a background thread, not blocking."""
        import time
        call_thread_ids = []

        def on_press(name):
            call_thread_ids.append(threading.current_thread().ident)

        listener = MultiHotkeyListener(["f2"], on_press, MagicMock())
        listener._handle_press("f2")

        # Wait for background thread to finish
        for _ in range(50):
            if call_thread_ids:
                break
            time.sleep(0.01)

        assert len(call_thread_ids) == 1
        assert call_thread_ids[0] != threading.current_thread().ident

    def test_press_release_target_key(self):
        import time

        on_press = MagicMock()
        on_release = MagicMock()
        listener = MultiHotkeyListener(["f2"], on_press, on_release)

        listener._handle_press("f2")
        # on_press is now dispatched to a background thread
        for _ in range(50):
            if on_press.called:
                break
            time.sleep(0.01)
        on_press.assert_called_once()
        assert "f2" in listener._held

        listener._handle_release("f2")
        on_release.assert_called_once()
        assert "f2" not in listener._held

    def test_non_target_key_ignored(self):
        on_press = MagicMock()
        listener = MultiHotkeyListener(["f2"], on_press, MagicMock())

        listener._handle_press("f3")
        import time
        time.sleep(0.05)
        on_press.assert_not_called()

    def test_repeated_press_ignored(self):
        import time

        on_press = MagicMock()
        listener = MultiHotkeyListener(["f2"], on_press, MagicMock())

        listener._handle_press("f2")
        listener._handle_press("f2")
        for _ in range(50):
            if on_press.called:
                break
            time.sleep(0.01)
        assert on_press.call_count == 1

    def test_enable_disable_key(self):
        listener = MultiHotkeyListener(["f2"], MagicMock(), MagicMock())
        assert "f2" in listener._enabled_names

        listener.disable_key("f2")
        assert "f2" not in listener._enabled_names

        listener.enable_key("f3")
        assert "f3" in listener._enabled_names

    def test_recording_mode(self):
        on_recorded = MagicMock()
        on_timeout = MagicMock()
        listener = MultiHotkeyListener(["f2"], MagicMock(), MagicMock())

        listener.record_next_key(on_recorded, on_timeout, timeout=10.0)

        # Pressing any recognized key should trigger recording
        listener._handle_press("f5")
        on_recorded.assert_called_once_with("f5")

    def test_recording_mode_cancel(self):
        on_recorded = MagicMock()
        on_timeout = MagicMock()
        listener = MultiHotkeyListener(["f2"], MagicMock(), MagicMock())

        listener.record_next_key(on_recorded, on_timeout, timeout=10.0)
        listener.cancel_record()

        # After cancel, pressing key should not trigger recording
        listener._handle_press("f5")
        on_recorded.assert_not_called()

    def test_fn_key_handling(self):
        import time

        on_press = MagicMock()
        on_release = MagicMock()
        listener = MultiHotkeyListener(["fn"], on_press, on_release)

        listener._handle_press("fn")
        for _ in range(50):
            if on_press.called:
                break
            time.sleep(0.01)
        on_press.assert_called_once()

        listener._handle_release("fn")
        on_release.assert_called_once()


class TestMultiHotkeyRestartKey:
    def test_restart_callback_when_hotkey_held(self):
        import time

        on_press = MagicMock()
        on_release = MagicMock()
        on_restart = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], on_press, on_release, on_restart=on_restart
        )

        # Hold hotkey
        listener._handle_press("fn")
        for _ in range(50):
            if on_press.called:
                break
            time.sleep(0.01)
        on_press.assert_called_once()

        # Press cmd while hotkey is held
        result = listener._handle_press("cmd")
        for _ in range(50):
            if on_restart.called:
                break
            time.sleep(0.01)
        on_restart.assert_called_once()
        assert result is True  # should signal swallow

    def test_restart_not_called_when_hotkey_not_held(self):
        import time

        on_restart = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_restart=on_restart
        )

        # Press cmd without holding hotkey
        result = listener._handle_press("cmd")
        time.sleep(0.05)
        on_restart.assert_not_called()
        assert result is False

    def test_restart_not_called_when_no_callback(self):
        import time

        on_press = MagicMock()
        listener = MultiHotkeyListener(["fn"], on_press, MagicMock())

        listener._handle_press("fn")
        # Press cmd - should be ignored (no on_restart callback)
        result = listener._handle_press("cmd")
        time.sleep(0.05)
        # on_press called only once (for fn)
        for _ in range(50):
            if on_press.called:
                break
            time.sleep(0.01)
        assert on_press.call_count == 1
        assert result is False

    def test_restart_with_custom_key(self):
        import time

        on_restart = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(),
            on_restart=on_restart, restart_key="f5",
        )

        listener._handle_press("fn")
        result = listener._handle_press("f5")
        for _ in range(50):
            if on_restart.called:
                break
            time.sleep(0.01)
        on_restart.assert_called_once()
        assert result is True

    def test_restart_ignores_non_restart_keys(self):
        import time

        on_restart = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_restart=on_restart
        )

        listener._handle_press("fn")
        # Press a non-restart key
        result = listener._handle_press("a")
        time.sleep(0.05)
        on_restart.assert_not_called()
        assert result is False

    def test_restart_multiple_times(self):
        import time

        on_restart = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_restart=on_restart
        )

        listener._handle_press("fn")
        listener._handle_press("cmd")
        listener._handle_press("cmd")
        listener._handle_press("cmd")
        for _ in range(50):
            if on_restart.call_count >= 3:
                break
            time.sleep(0.01)
        assert on_restart.call_count == 3

    def test_press_returns_false_for_normal_key(self):
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_restart=MagicMock()
        )
        result = listener._handle_press("fn")
        assert result is False  # normal hotkey press should not swallow


class TestMultiHotkeyCancelKey:
    def test_cancel_callback_when_hotkey_held(self):
        import time

        on_press = MagicMock()
        on_release = MagicMock()
        on_cancel = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], on_press, on_release, on_cancel=on_cancel
        )

        # Hold hotkey
        listener._handle_press("fn")
        for _ in range(50):
            if on_press.called:
                break
            time.sleep(0.01)
        on_press.assert_called_once()

        # Press space while hotkey is held — runs cancel in background thread
        listener._handle_press("space")
        # Wait for background thread to complete
        for _ in range(50):
            if on_cancel.called:
                break
            time.sleep(0.01)
        on_cancel.assert_called_once()

    def test_cancel_not_called_when_hotkey_not_held(self):
        on_cancel = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_cancel=on_cancel
        )

        # Press space without holding hotkey
        result = listener._handle_press("space")
        on_cancel.assert_not_called()
        assert result is False

    def test_cancel_not_called_when_no_callback(self):
        on_press = MagicMock()
        listener = MultiHotkeyListener(["fn"], on_press, MagicMock())

        listener._handle_press("fn")
        _flush()
        result = listener._handle_press("space")
        assert on_press.call_count == 1
        assert result is False

    def test_cancel_with_custom_key(self):
        import time

        on_cancel = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(),
            on_cancel=on_cancel, cancel_key="ctrl",
        )

        listener._handle_press("fn")
        listener._handle_press("ctrl")
        for _ in range(50):
            if on_cancel.called:
                break
            time.sleep(0.01)
        on_cancel.assert_called_once()

    def test_cancel_swallows_event(self):
        """Cancel key (space) should be swallowed to prevent typing."""
        on_cancel = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_cancel=on_cancel
        )

        listener._handle_press("fn")
        result = listener._handle_press("space")
        assert result is True  # space must be swallowed

    def test_cancel_skips_release(self):
        """After cancel, releasing the hotkey should not trigger on_release."""
        on_release = MagicMock()
        on_cancel = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), on_release, on_cancel=on_cancel
        )

        listener._handle_press("fn")
        listener._handle_press("space")
        # Release Fn — should be skipped because cancel was requested
        listener._handle_release("fn")
        on_release.assert_not_called()

    def test_cancel_flag_cleared_after_release(self):
        """Cancel flag should be cleared after release, so next cycle works."""
        import time

        on_press = MagicMock()
        on_release = MagicMock()
        on_cancel = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], on_press, on_release, on_cancel=on_cancel
        )

        # First cycle: press, cancel, release
        listener._handle_press("fn")
        listener._handle_press("space")
        listener._handle_release("fn")
        on_release.assert_not_called()

        # Wait for cancel thread
        for _ in range(50):
            if on_cancel.called:
                break
            time.sleep(0.01)

        # Second cycle: normal press and release should work
        listener._handle_press("fn")
        listener._handle_release("fn")
        on_release.assert_called_once()

    def test_restart_and_cancel_coexist(self):
        import time

        on_restart = MagicMock()
        on_cancel = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(),
            on_restart=on_restart, on_cancel=on_cancel,
        )

        listener._handle_press("fn")

        # Cmd triggers restart
        listener._handle_press("cmd")
        for _ in range(50):
            if on_restart.called:
                break
            time.sleep(0.01)
        on_restart.assert_called_once()
        on_cancel.assert_not_called()

        # Space triggers cancel (runs in background thread)
        listener._handle_press("space")
        for _ in range(50):
            if on_cancel.called:
                break
            time.sleep(0.01)
        on_cancel.assert_called_once()
        assert on_restart.call_count == 1


class TestMultiHotkeyModeNav:
    def test_left_arrow_calls_mode_prev_when_held(self):
        on_mode_prev = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_mode_prev=on_mode_prev
        )

        listener._handle_press("fn")
        result = listener._handle_press("left")
        _flush()
        on_mode_prev.assert_called_once()
        assert result is True  # swallowed

    def test_up_arrow_calls_mode_prev_when_held(self):
        on_mode_prev = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_mode_prev=on_mode_prev
        )

        listener._handle_press("fn")
        result = listener._handle_press("up")
        _flush()
        on_mode_prev.assert_called_once()
        assert result is True

    def test_right_arrow_calls_mode_next_when_held(self):
        on_mode_next = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_mode_next=on_mode_next
        )

        listener._handle_press("fn")
        result = listener._handle_press("right")
        _flush()
        on_mode_next.assert_called_once()
        assert result is True

    def test_down_arrow_calls_mode_next_when_held(self):
        on_mode_next = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_mode_next=on_mode_next
        )

        listener._handle_press("fn")
        result = listener._handle_press("down")
        _flush()
        on_mode_next.assert_called_once()
        assert result is True

    def test_arrows_ignored_when_hotkey_not_held(self):
        on_mode_prev = MagicMock()
        on_mode_next = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(),
            on_mode_prev=on_mode_prev, on_mode_next=on_mode_next,
        )

        result = listener._handle_press("left")
        assert result is False
        on_mode_prev.assert_not_called()

        result = listener._handle_press("right")
        assert result is False
        on_mode_next.assert_not_called()

    def test_arrows_ignored_when_no_callbacks(self):
        on_press = MagicMock()
        listener = MultiHotkeyListener(["fn"], on_press, MagicMock())

        listener._handle_press("fn")
        result = listener._handle_press("left")
        assert result is False
        result = listener._handle_press("right")
        assert result is False


class TestMultiHotkeySetKeys:
    def test_set_restart_key(self):
        import time

        on_restart = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_restart=on_restart
        )

        # Default restart key is cmd
        listener._handle_press("fn")
        listener._handle_press("cmd")
        for _ in range(50):
            if on_restart.called:
                break
            time.sleep(0.01)
        on_restart.assert_called_once()

        # Change to f5
        listener.set_restart_key("f5")
        listener._handle_press("f5")
        for _ in range(50):
            if on_restart.call_count >= 2:
                break
            time.sleep(0.01)
        assert on_restart.call_count == 2

        # Old key no longer triggers restart
        on_restart.reset_mock()
        listener._handle_press("cmd")
        time.sleep(0.05)
        on_restart.assert_not_called()

    def test_set_cancel_key(self):
        import time

        on_cancel = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_cancel=on_cancel
        )

        # Default cancel key is space
        listener._handle_press("fn")
        listener._handle_press("space")
        for _ in range(50):
            if on_cancel.called:
                break
            time.sleep(0.01)
        on_cancel.assert_called_once()

        # Release to reset state, then change cancel key
        listener._handle_release("fn")
        on_cancel.reset_mock()
        listener.set_cancel_key("esc")

        listener._handle_press("fn")
        listener._handle_press("esc")
        for _ in range(50):
            if on_cancel.called:
                break
            time.sleep(0.01)
        on_cancel.assert_called_once()

    def test_set_restart_key_normalizes_aliases(self):
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_restart=MagicMock()
        )
        listener.set_restart_key("command")
        assert listener._restart_key == "cmd"

        listener.set_restart_key("option")
        assert listener._restart_key == "alt"

    def test_set_cancel_key_normalizes_aliases(self):
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_cancel=MagicMock()
        )
        listener.set_cancel_key("command")
        assert listener._cancel_key == "cmd"

        listener.set_cancel_key("option")
        assert listener._cancel_key == "alt"


class TestHoldHotkeyThreadSafety:
    """Test that _held state is protected by a lock."""

    def test_hold_listener_has_lock(self):
        listener = HoldHotkeyListener("fn", MagicMock(), MagicMock())
        assert hasattr(listener, "_held_lock")
        assert isinstance(listener._held_lock, type(threading.Lock()))

    def test_concurrent_press_fires_once(self):
        """Rapid concurrent presses should only fire on_press once."""
        call_count = 0
        barrier = threading.Barrier(10)

        def on_press():
            nonlocal call_count
            call_count += 1

        listener = HoldHotkeyListener("fn", on_press, MagicMock())

        def worker():
            barrier.wait()
            listener._handle_press("fn")

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert call_count == 1


class TestMultiHotkeyThreadSafety:
    """Test that MultiHotkeyListener._held set is protected by a lock."""

    def test_multi_listener_has_lock(self):
        listener = MultiHotkeyListener(["fn"], MagicMock(), MagicMock())
        assert hasattr(listener, "_held_lock")
        assert isinstance(listener._held_lock, type(threading.Lock()))

    def test_concurrent_press_fires_once(self):
        """Rapid concurrent presses should only fire on_press once."""
        import time

        call_count = 0
        count_lock = threading.Lock()
        barrier = threading.Barrier(10)

        def on_press(key_name):
            nonlocal call_count
            with count_lock:
                call_count += 1

        listener = MultiHotkeyListener(["fn"], on_press, MagicMock())

        def worker():
            barrier.wait()
            listener._handle_press("fn")

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Wait for the background dispatch thread to complete
        for _ in range(50):
            with count_lock:
                if call_count >= 1:
                    break
            time.sleep(0.01)

        with count_lock:
            assert call_count == 1


class TestKeyRemapListener:
    """Tests for KeyRemapListener callback logic (no real CGEventTap)."""

    def test_add_and_remove(self):
        listener = KeyRemapListener()
        listener.add(60, 80, True, 0x020000)
        assert 60 in listener._remaps
        listener.remove(60)
        assert 60 not in listener._remaps

    def test_modifier_remap_keydown(self):
        """FlagsChanged event for modifier press → synthesize target keydown."""
        import wenzi._cgeventtap as cg

        listener = KeyRemapListener()
        listener._runner = MagicMock()
        listener._runner.tap = MagicMock()
        listener.add(60, 80, True, 0x020000)  # shift_r → f19
        listener._prev_flags = 0

        posted_events = []
        created_events = []

        def mock_create(source, vk, key_down):
            token = f"evt_{vk}_{key_down}"
            created_events.append(token)
            return token

        def mock_post(tap, evt):
            posted_events.append(evt)

        def mock_get_int(event, field):
            # Return keycode 60 for the input event
            if field == cg.kCGKeyboardEventKeycode:
                return 60
            return 0

        def mock_get_flags(event):
            return 0x020000  # shift flag set (key down)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cg, "CGEventGetIntegerValueField", mock_get_int)
            mp.setattr(cg, "CGEventGetFlags", mock_get_flags)
            mp.setattr(cg, "CGEventCreateKeyboardEvent", mock_create)
            mp.setattr(cg, "CGEventPost", mock_post)
            mp.setattr(cg, "CFRelease", lambda evt: None)
            result = listener._callback(None, cg.kCGEventFlagsChanged, 0xDEAD, None)

        assert result is None  # Swallowed
        assert len(posted_events) == 1
        assert posted_events[0] == "evt_80_True"  # F19 keydown

    def test_modifier_remap_keyup(self):
        """FlagsChanged event for modifier release → synthesize target keyup."""
        import wenzi._cgeventtap as cg

        listener = KeyRemapListener()
        listener._runner = MagicMock()
        listener._runner.tap = MagicMock()
        listener.add(60, 80, True, 0x020000)
        listener._prev_flags = 0x020000  # Was previously down

        posted_events = []

        def mock_create(source, vk, key_down):
            return f"evt_{vk}_{key_down}"

        def mock_get_int(event, field):
            if field == cg.kCGKeyboardEventKeycode:
                return 60
            return 0

        def mock_get_flags(event):
            return 0  # Flags cleared → key up

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cg, "CGEventGetIntegerValueField", mock_get_int)
            mp.setattr(cg, "CGEventGetFlags", mock_get_flags)
            mp.setattr(cg, "CGEventCreateKeyboardEvent", mock_create)
            mp.setattr(cg, "CGEventPost", lambda tap, evt: posted_events.append(evt))
            mp.setattr(cg, "CFRelease", lambda evt: None)
            result = listener._callback(None, cg.kCGEventFlagsChanged, 0xDEAD, None)

        assert result is None
        assert len(posted_events) == 1
        assert posted_events[0] == "evt_80_False"  # F19 keyup

    def test_unremapped_key_passes_through(self):
        """Keys not in the remap table should pass through unchanged."""
        import wenzi._cgeventtap as cg

        listener = KeyRemapListener()
        listener._runner = MagicMock()
        listener._runner.tap = MagicMock()
        listener.add(60, 80, True, 0x020000)

        event = 0xBEEF  # raw pointer placeholder

        def mock_get_int(ev, field):
            if field == cg.kCGKeyboardEventKeycode:
                return 0  # 'a' key
            return 0

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cg, "CGEventGetIntegerValueField", mock_get_int)
            result = listener._callback(None, cg.kCGEventKeyDown, event, None)

        assert result == event  # Passed through

    def test_regular_key_remap(self):
        """KeyDown for regular key remap → synthesize target."""
        import wenzi._cgeventtap as cg

        listener = KeyRemapListener()
        listener._runner = MagicMock()
        listener._runner.tap = MagicMock()
        listener.add(105, 53, False, 0)  # f13 → esc

        posted_events = []

        def mock_create(source, vk, key_down):
            return f"evt_{vk}_{key_down}"

        def mock_get_int(event, field):
            if field == cg.kCGKeyboardEventKeycode:
                return 105
            return 0

        def mock_get_flags(event):
            return 0

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cg, "CGEventGetIntegerValueField", mock_get_int)
            mp.setattr(cg, "CGEventGetFlags", mock_get_flags)
            mp.setattr(cg, "CGEventCreateKeyboardEvent", mock_create)
            mp.setattr(cg, "CGEventSetFlags", lambda evt, flags: None)
            mp.setattr(cg, "CGEventPost", lambda tap, evt: posted_events.append(evt))
            mp.setattr(cg, "CFRelease", lambda evt: None)
            result = listener._callback(None, cg.kCGEventKeyDown, 0xDEAD, None)

        assert result is None
        assert posted_events == ["evt_53_True"]
