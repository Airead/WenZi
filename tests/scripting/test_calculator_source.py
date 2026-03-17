"""Tests for the calculator chooser source."""

from __future__ import annotations

import pytest

from wenzi.scripting.sources.calculator_source import (
    CalculatorSource,
    _format_number,
    _is_complete,
    _looks_like_math,
)


@pytest.fixture()
def calc():
    return CalculatorSource()


@pytest.fixture()
def calc_with_pint(calc):
    """Ensure Pint is fully initialized before running tests."""
    import pint

    calc._ureg = pint.UnitRegistry()
    calc._ureg_ready = True
    return calc


# ---------------------------------------------------------------------------
# Math operations
# ---------------------------------------------------------------------------


class TestMathOperations:
    def test_addition(self, calc):
        items = calc.search("2 + 3")
        assert len(items) == 1
        assert "= 5" in items[0].title

    def test_multiply_divide(self, calc):
        items = calc.search("10 * 3 / 2")
        assert len(items) == 1
        assert "= 15" in items[0].title

    def test_parentheses(self, calc):
        items = calc.search("(2 + 3) * 4")
        assert len(items) == 1
        assert "= 20" in items[0].title

    def test_power_caret(self, calc):
        items = calc.search("2^10")
        assert len(items) == 1
        assert "= 1,024" in items[0].title

    def test_power_double_star(self, calc):
        items = calc.search("2**10")
        assert len(items) == 1
        assert "= 1,024" in items[0].title

    def test_modulo(self, calc):
        items = calc.search("10 % 3")
        assert len(items) == 1
        assert "= 1" in items[0].title

    def test_sqrt(self, calc):
        items = calc.search("sqrt(16)")
        assert len(items) == 1
        assert "= 4" in items[0].title

    def test_sin_zero(self, calc):
        items = calc.search("sin(0)")
        assert len(items) == 1
        assert "= 0" in items[0].title

    def test_pi_constant(self, calc):
        items = calc.search("pi * 2")
        assert len(items) == 1
        assert "6.28318" in items[0].title

    def test_max_function(self, calc):
        items = calc.search("max(1, 2, 3)")
        assert len(items) == 1
        assert "= 3" in items[0].title

    def test_float_precision(self, calc):
        items = calc.search("0.1 + 0.2")
        assert len(items) == 1
        assert "= 0.3" in items[0].title
        # Must NOT show 0.30000000000000004
        assert "0.30000" not in items[0].title

    def test_trailing_equals(self, calc):
        items = calc.search("2 + 3=")
        assert len(items) == 1
        assert "= 5" in items[0].title

    def test_negative_numbers(self, calc):
        items = calc.search("-3 + 5")
        assert len(items) == 1
        assert "= 2" in items[0].title

    def test_large_integer_formatting(self, calc):
        items = calc.search("1000 * 1000")
        assert len(items) == 1
        assert "1,000,000" in items[0].title


# ---------------------------------------------------------------------------
# Detection logic — should return empty
# ---------------------------------------------------------------------------


class TestDetectionLogic:
    def test_plain_text(self, calc):
        assert calc.search("Safari") == []

    def test_plain_number_no_operator(self, calc):
        assert calc.search("42") == []

    def test_incomplete_expression_plus(self, calc):
        assert calc.search("2+") == []

    def test_incomplete_expression_star(self, calc):
        assert calc.search("3*") == []

    def test_app_name_with_digit(self, calc):
        assert calc.search("1password") == []

    def test_division_by_zero(self, calc):
        assert calc.search("1/0") == []

    def test_empty_query(self, calc):
        assert calc.search("") == []

    def test_only_spaces(self, calc):
        assert calc.search("   ") == []

    def test_bare_negative_number(self, calc):
        assert calc.search("-5") == []

    def test_bare_negative_decimal(self, calc):
        assert calc.search("-3.14") == []


# ---------------------------------------------------------------------------
# Unit conversion
# ---------------------------------------------------------------------------


class TestUnitConversion:
    def test_km_to_mi(self, calc_with_pint):
        items = calc_with_pint.search("10 km to mi")
        assert len(items) == 1
        assert "6.21371" in items[0].title

    def test_inches_to_cm(self, calc_with_pint):
        items = calc_with_pint.search("10 in to cm")
        assert len(items) == 1
        assert "25.4" in items[0].title

    def test_celsius_to_fahrenheit_symbol(self, calc_with_pint):
        items = calc_with_pint.search("100 °C to °F")
        assert len(items) == 1
        assert "212" in items[0].title

    def test_celsius_to_fahrenheit_letter(self, calc_with_pint):
        items = calc_with_pint.search("0 C to F")
        assert len(items) == 1
        assert "32" in items[0].title

    def test_fahrenheit_to_celsius(self, calc_with_pint):
        items = calc_with_pint.search("72 F to C")
        assert len(items) == 1
        assert "22.2" in items[0].title

    def test_kg_to_lb(self, calc_with_pint):
        items = calc_with_pint.search("5 kg to lb")
        assert len(items) == 1

    def test_gb_to_mb(self, calc_with_pint):
        items = calc_with_pint.search("1 GB to MB")
        assert len(items) == 1
        title = items[0].title
        assert "1000" in title.replace(",", "") or "1,000" in title

    def test_in_keyword(self, calc_with_pint):
        items_to = calc_with_pint.search("10 km to mi")
        items_in = calc_with_pint.search("10 km in mi")
        assert len(items_to) == 1
        assert len(items_in) == 1
        # Both should produce the same magnitude in the result
        assert "6.21371" in items_in[0].title

    def test_incompatible_units(self, calc_with_pint):
        assert calc_with_pint.search("10 kg to km") == []

    def test_unknown_units(self, calc_with_pint):
        assert calc_with_pint.search("10 foo to bar") == []

    def test_pint_not_ready(self, calc):
        # Ensure _ureg is None to simulate pint not yet initialized
        calc._ureg = None
        # Should fall through to math or return empty — not crash
        result = calc.search("10 km to mi")
        # No unit conversion available, no math match either
        assert result == []


# ---------------------------------------------------------------------------
# ChooserSource metadata
# ---------------------------------------------------------------------------


class TestChooserSource:
    def test_as_chooser_source(self, calc):
        cs = calc.as_chooser_source()
        assert cs.name == "calculator"
        assert cs.prefix is None
        assert cs.priority == 12
        assert cs.action_hints == {"enter": "Paste", "cmd_enter": "Copy"}

    def test_search_callable(self, calc):
        cs = calc.as_chooser_source()
        items = cs.search("1 + 1")
        assert len(items) == 1
        assert "= 2" in items[0].title


# ---------------------------------------------------------------------------
# ChooserItem fields
# ---------------------------------------------------------------------------


class TestChooserItem:
    def test_math_item_fields(self, calc):
        items = calc.search("2 + 3")
        item = items[0]
        assert item.action is not None
        assert item.secondary_action is not None
        assert item.item_id.startswith("calc:")
        assert item.subtitle == "Calculator"

    def test_clipboard_value_has_no_commas(self, calc):
        """The value copied to clipboard must be a plain number (no thousand separators)."""
        items = calc.search("1000 * 1000")
        assert len(items) == 1
        # title shows formatted display
        assert "1,000,000" in items[0].title
        # Inspect what the copy closure would copy — extract from closure defaults
        # secondary_action is the copy action (Cmd+Enter)
        copy_closure = items[0].secondary_action
        # The default arg 't' captured in 'lambda t=raw: ...'
        raw_value = copy_closure.__defaults__[0] if hasattr(copy_closure, "__defaults__") else None
        if raw_value is None:
            # Fallback: check via __code__.co_freevars / __closure__
            for cell in (copy_closure.__closure__ or []):
                val = cell.cell_contents
                if isinstance(val, str) and val.isdigit():
                    raw_value = val
                    break
        assert raw_value == "1000000"

    def test_conversion_item_fields(self, calc_with_pint):
        items = calc_with_pint.search("10 km to mi")
        item = items[0]
        assert item.action is not None
        assert item.secondary_action is not None
        assert item.item_id.startswith("calc:")
        assert item.subtitle == "Unit Conversion"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_format_number_int(self):
        display, raw = _format_number(1000000)
        assert display == "1,000,000"
        assert raw == "1000000"

    def test_format_number_float_whole(self):
        display, raw = _format_number(4.0)
        assert display == "4"
        assert raw == "4"

    def test_format_number_float(self):
        display, raw = _format_number(3.14159)
        assert display == "3.14159"
        assert raw == "3.14159"

    def test_format_number_bool(self):
        display, raw = _format_number(True)
        assert display == "True"
        assert raw == "True"

    def test_looks_like_math_with_operator(self):
        assert _looks_like_math("2 + 3") is True

    def test_looks_like_math_function(self):
        assert _looks_like_math("sqrt(16)") is True

    def test_looks_like_math_plain(self):
        assert _looks_like_math("hello") is False

    def test_is_complete_valid(self):
        assert _is_complete("2 + 3") is True

    def test_is_complete_trailing_operator(self):
        assert _is_complete("2+") is False

    def test_is_complete_trailing_paren(self):
        assert _is_complete("sqrt(") is False
