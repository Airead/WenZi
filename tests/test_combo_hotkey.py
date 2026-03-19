"""Tests for combo hotkey recording helpers.

White-box tests: _MOD_CANONICAL, _MOD_DISPLAY_ORDER, _SPECIAL_VK, and
_VK_TO_NAME are private mapping tables imported intentionally to verify
internal consistency (e.g. every modifier key has a canonical form, every
keycode has a reverse lookup). These invariants cannot be tested through
public APIs alone.
"""

from wenzi.app import (
    _MOD_CANONICAL,
    _MOD_DISPLAY_ORDER,
    build_combo_string,
    format_combo_display,
)
from wenzi.hotkey import MODIFIER_KEY_NAMES, _SPECIAL_VK, _VK_TO_NAME


class TestModifierKeyNames:
    """Verify MODIFIER_KEY_NAMES constant."""

    def test_contains_known_modifiers(self):
        for name in ("cmd", "cmd_r", "ctrl", "ctrl_r", "alt", "alt_r", "shift", "shift_r"):
            assert name in MODIFIER_KEY_NAMES

    def test_no_non_modifiers(self):
        assert "fn" not in MODIFIER_KEY_NAMES
        assert "space" not in MODIFIER_KEY_NAMES


class TestFormatComboDisplay:
    """Verify human-readable combo display formatting."""

    def test_empty(self):
        assert format_combo_display(set(), None) == "..."

    def test_single_modifier(self):
        assert format_combo_display({"cmd"}, None) == "Cmd + ..."

    def test_multiple_modifiers_ordered(self):
        result = format_combo_display({"cmd", "alt"}, None)
        assert result == "Alt + Cmd + ..."

    def test_full_combo(self):
        result = format_combo_display({"alt", "cmd"}, "v")
        assert result == "Alt + Cmd + V"

    def test_all_modifiers_with_trigger(self):
        result = format_combo_display({"ctrl", "alt", "shift", "cmd"}, "a")
        assert result == "Ctrl + Alt + Shift + Cmd + A"

    def test_trigger_only(self):
        result = format_combo_display(set(), "v")
        assert result == "V"


class TestBuildComboString:
    """Verify config-format combo string building."""

    def test_simple_combo(self):
        assert build_combo_string({"cmd", "alt"}, "v") == "alt+cmd+v"

    def test_single_modifier(self):
        assert build_combo_string({"ctrl"}, "c") == "ctrl+c"

    def test_all_modifiers(self):
        result = build_combo_string({"ctrl", "alt", "shift", "cmd"}, "space")
        assert result == "ctrl+alt+shift+cmd+space"

    def test_modifier_order_consistent(self):
        """Order should always follow _MOD_DISPLAY_ORDER regardless of set order."""
        result1 = build_combo_string({"cmd", "alt"}, "v")
        result2 = build_combo_string({"alt", "cmd"}, "v")
        assert result1 == result2 == "alt+cmd+v"


class TestModCanonical:
    """Verify _MOD_CANONICAL mapping covers all MODIFIER_KEY_NAMES."""

    def test_all_modifier_keys_mapped(self):
        for name in MODIFIER_KEY_NAMES:
            assert name in _MOD_CANONICAL, f"{name} missing from _MOD_CANONICAL"

    def test_canonical_values_in_display_order(self):
        for canonical in _MOD_CANONICAL.values():
            assert canonical in _MOD_DISPLAY_ORDER


class TestSpecialKeysForComboRecording:
    """Verify return and delete keys are in the keycode map."""

    def test_return_key_in_special_vk(self):
        assert "return" in _SPECIAL_VK
        assert _SPECIAL_VK["return"] == 36

    def test_delete_key_in_special_vk(self):
        assert "delete" in _SPECIAL_VK
        assert _SPECIAL_VK["delete"] == 51

    def test_tab_key_in_special_vk(self):
        assert "tab" in _SPECIAL_VK
        assert _SPECIAL_VK["tab"] == 48

    def test_return_in_reverse_lookup(self):
        assert _VK_TO_NAME[36] == "return"

    def test_delete_in_reverse_lookup(self):
        assert _VK_TO_NAME[51] == "delete"
