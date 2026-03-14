"""Tests for the hotkey module."""

import threading

import pytest
from unittest.mock import MagicMock

from voicetext.hotkey import (
    _is_fn_key,
    _name_to_vk,
    _parse_hotkey_for_quartz,
    _KEYCODE_MAP,
    _MOD_FLAGS,
    _VK_TO_NAME,
    HoldHotkeyListener,
    TapHotkeyListener,
    MultiHotkeyListener,
)


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
        assert listener._tap is None


class TestMultiHotkeyListener:
    def test_creation_with_fn(self):
        listener = MultiHotkeyListener(["fn", "f2"], MagicMock(), MagicMock())
        assert "fn" in listener._enabled_names
        assert "f2" in listener._enabled_names
        assert 63 in listener._target_vks  # fn vk
        assert 120 in listener._target_vks  # f2 vk

    def test_press_release_target_key(self):
        on_press = MagicMock()
        on_release = MagicMock()
        listener = MultiHotkeyListener(["f2"], on_press, on_release)

        listener._handle_press("f2")
        on_press.assert_called_once()
        assert "f2" in listener._held

        listener._handle_release("f2")
        on_release.assert_called_once()
        assert "f2" not in listener._held

    def test_non_target_key_ignored(self):
        on_press = MagicMock()
        listener = MultiHotkeyListener(["f2"], on_press, MagicMock())

        listener._handle_press("f3")
        on_press.assert_not_called()

    def test_repeated_press_ignored(self):
        on_press = MagicMock()
        listener = MultiHotkeyListener(["f2"], on_press, MagicMock())

        listener._handle_press("f2")
        listener._handle_press("f2")
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
        on_press = MagicMock()
        on_release = MagicMock()
        listener = MultiHotkeyListener(["fn"], on_press, on_release)

        listener._handle_press("fn")
        on_press.assert_called_once()

        listener._handle_release("fn")
        on_release.assert_called_once()


class TestMultiHotkeyRestartKey:
    def test_restart_callback_when_hotkey_held(self):
        on_press = MagicMock()
        on_release = MagicMock()
        on_restart = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], on_press, on_release, on_restart=on_restart
        )

        # Hold hotkey
        listener._handle_press("fn")
        on_press.assert_called_once()

        # Press space while hotkey is held
        result = listener._handle_press("space")
        on_restart.assert_called_once()
        assert result is True  # should signal swallow

    def test_restart_not_called_when_hotkey_not_held(self):
        on_restart = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_restart=on_restart
        )

        # Press space without holding hotkey
        result = listener._handle_press("space")
        on_restart.assert_not_called()
        assert result is False

    def test_restart_not_called_when_no_callback(self):
        on_press = MagicMock()
        listener = MultiHotkeyListener(["fn"], on_press, MagicMock())

        listener._handle_press("fn")
        # Press space - should be ignored (no on_restart callback)
        result = listener._handle_press("space")
        # on_press called only once (for fn)
        assert on_press.call_count == 1
        assert result is False

    def test_restart_with_custom_key(self):
        on_restart = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(),
            on_restart=on_restart, restart_key="f5",
        )

        listener._handle_press("fn")
        result = listener._handle_press("f5")
        on_restart.assert_called_once()
        assert result is True

    def test_restart_ignores_non_restart_keys(self):
        on_restart = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_restart=on_restart
        )

        listener._handle_press("fn")
        # Press a non-restart key
        result = listener._handle_press("a")
        on_restart.assert_not_called()
        assert result is False

    def test_restart_multiple_times(self):
        on_restart = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_restart=on_restart
        )

        listener._handle_press("fn")
        listener._handle_press("space")
        listener._handle_press("space")
        listener._handle_press("space")
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
        on_press.assert_called_once()

        # Press cmd while hotkey is held — runs cancel in background thread
        listener._handle_press("cmd")
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

        # Press cmd without holding hotkey
        result = listener._handle_press("cmd")
        on_cancel.assert_not_called()
        assert result is False

    def test_cancel_not_called_when_no_callback(self):
        on_press = MagicMock()
        listener = MultiHotkeyListener(["fn"], on_press, MagicMock())

        listener._handle_press("fn")
        result = listener._handle_press("cmd")
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

    def test_cancel_does_not_swallow_event(self):
        """Cancel key (modifier) should not be swallowed."""
        on_cancel = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), MagicMock(), on_cancel=on_cancel
        )

        listener._handle_press("fn")
        result = listener._handle_press("cmd")
        assert result is False  # modifier keys are not swallowed

    def test_cancel_skips_release(self):
        """After cancel, releasing the hotkey should not trigger on_release."""
        on_release = MagicMock()
        on_cancel = MagicMock()
        listener = MultiHotkeyListener(
            ["fn"], MagicMock(), on_release, on_cancel=on_cancel
        )

        listener._handle_press("fn")
        listener._handle_press("cmd")
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
        listener._handle_press("cmd")
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

        # Space triggers restart
        listener._handle_press("space")
        on_restart.assert_called_once()
        on_cancel.assert_not_called()

        # Cmd triggers cancel (runs in background thread)
        listener._handle_press("cmd")
        for _ in range(50):
            if on_cancel.called:
                break
            time.sleep(0.01)
        on_cancel.assert_called_once()
        assert on_restart.call_count == 1


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
        call_count = 0
        barrier = threading.Barrier(10)

        def on_press():
            nonlocal call_count
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

        assert call_count == 1
