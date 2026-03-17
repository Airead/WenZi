"""Calculator data source for the Chooser.

Provides inline math evaluation and unit conversion directly in the
search bar.  Math is powered by *simpleeval* (sandboxed) and unit
conversion by *pint* (loaded lazily in a background thread).
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
from typing import List

from simpleeval import SimpleEval

from wenzi.scripting.sources import ChooserItem, ChooserSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FUNC_NAMES = frozenset({
    "sqrt", "sin", "cos", "tan", "asin", "acos", "atan",
    "log", "log2", "log10", "abs", "round", "ceil", "floor",
    "min", "max", "pow",
})

_OPERATORS_RE = re.compile(r"[+\-*/^%]")
_FUNC_CALL_RE = re.compile(r"\b(" + "|".join(_FUNC_NAMES) + r")\s*\(")
_INCOMPLETE_RE = re.compile(r"[+\-*/^%(]\s*$")

_CONVERSION_RE = re.compile(
    r"^(-?\d+\.?\d*)\s*(.+?)\s+(?:to|in)\s+(.+?)\s*$",
    re.IGNORECASE,
)

_UNIT_ALIASES = {
    "°c": "degC", "°f": "degF", "°k": "kelvin",
    "c": "degC", "f": "degF",
}

_CALC_APP = "/System/Applications/Calculator.app"
_CALC_ICON = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_calculator_icon() -> str:
    global _CALC_ICON
    if not _CALC_ICON:
        icns = os.path.join(_CALC_APP, "Contents", "Resources", "AppIcon.icns")
        if os.path.isfile(icns):
            _CALC_ICON = "file://" + icns
    return _CALC_ICON


def _normalize_unit(unit_str: str) -> str:
    return _UNIT_ALIASES.get(unit_str.strip().lower(), unit_str.strip())


def _looks_like_math(expr: str) -> bool:
    # A bare negative number like "-5" should not count as a math expression.
    # Require at least one binary operator (an operator that is not a leading
    # unary minus) OR a known function call.
    if _FUNC_CALL_RE.search(expr):
        return True
    # Strip leading unary minus before checking for operators
    check = expr.lstrip("-").lstrip()
    return bool(_OPERATORS_RE.search(check))


def _is_complete(expr: str) -> bool:
    return not _INCOMPLETE_RE.search(expr)


def _format_number(value: object) -> tuple[str, str]:
    """Return ``(display, raw)`` strings for *value*.

    *display* uses thousand separators for readability (shown in title).
    *raw* is a plain number string safe for pasting into code or another
    calculator.
    """
    if isinstance(value, bool):
        s = str(value)
        return s, s
    if isinstance(value, int):
        return f"{value:,}", str(value)
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            iv = int(value)
            return f"{iv:,}", str(iv)
        g = f"{value:.10g}"
        return g, g
    s = str(value)
    return s, s


def _paste_text(text: str) -> None:
    """Write text to clipboard and simulate Cmd+V to paste at cursor."""
    try:
        from wenzi.input import _set_pasteboard_concealed

        import subprocess
        import time

        _set_pasteboard_concealed(text)
        time.sleep(0.05)
        subprocess.run(
            [
                "osascript", "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ],
            capture_output=True, timeout=5,
        )
    except Exception:
        logger.exception("Failed to paste calculator result")


def _copy_to_clipboard(text: str) -> None:
    """Write text to the system clipboard without pasting."""
    try:
        from wenzi.input import _set_pasteboard_concealed

        _set_pasteboard_concealed(text)
    except Exception:
        logger.exception("Failed to copy calculator result to clipboard")


# ---------------------------------------------------------------------------
# CalculatorSource
# ---------------------------------------------------------------------------


class CalculatorSource:
    """Inline calculator and unit converter for the Chooser."""

    def __init__(self) -> None:
        # simpleeval — lightweight, synchronous
        self._eval = SimpleEval()
        self._eval.names = {"pi": math.pi, "e": math.e}
        self._eval.functions = {
            "sqrt": math.sqrt,
            "sin": math.sin,
            "cos": math.cos,
            "tan": math.tan,
            "asin": math.asin,
            "acos": math.acos,
            "atan": math.atan,
            "log": math.log,
            "log2": math.log2,
            "log10": math.log10,
            "abs": abs,
            "round": round,
            "ceil": math.ceil,
            "floor": math.floor,
            "min": min,
            "max": max,
            "pow": pow,
        }

        # pint — heavy, loaded in background thread
        self._ureg = None

        def _init_pint() -> None:
            try:
                import pint

                self._ureg = pint.UnitRegistry()
                logger.info("Pint UnitRegistry initialized")
            except Exception:
                logger.exception("Failed to initialize pint")

        threading.Thread(target=_init_pint, daemon=True).start()

    # -- public API ----------------------------------------------------------

    def search(self, query: str) -> List[ChooserItem]:
        """Return calculator results for *query*, or an empty list."""
        q = query.strip()
        if not q:
            return []

        # Fast pre-check: must contain at least one digit
        if not any(ch.isdigit() for ch in q):
            return []

        # Strip trailing '='
        expr = q.rstrip("= ")

        # 1. Try unit conversion (if Pint is ready)
        item = self._try_conversion_item(expr)
        if item is not None:
            return [item]

        # 2. Try math expression
        item = self._try_math_item(expr)
        if item is not None:
            return [item]

        return []

    def as_chooser_source(self) -> ChooserSource:
        return ChooserSource(
            name="calculator",
            prefix=None,
            search=self.search,
            priority=12,
            description="Calculator & unit conversion",
            action_hints={
                "enter": "Paste",
                "cmd_enter": "Copy",
            },
        )

    # -- unit conversion -----------------------------------------------------

    def _try_conversion_item(self, expr: str) -> ChooserItem | None:
        if self._ureg is None:
            return None

        m = _CONVERSION_RE.match(expr)
        if not m:
            return None

        try:
            number = float(m.group(1))
            from_unit = _normalize_unit(m.group(2))
            to_unit = _normalize_unit(m.group(3))

            quantity = self._ureg.Quantity(number, from_unit)
            result = quantity.to(to_unit)
            magnitude = result.magnitude
            unit_str = f"{result.units:~P}"
        except Exception:
            return None

        display, raw = _format_number(magnitude)
        display_text = f"{display} {unit_str}"
        raw_text = f"{raw} {unit_str}"
        title = f"{expr} = {display_text}"
        icon = _get_calculator_icon()

        return ChooserItem(
            title=title,
            subtitle="Unit Conversion",
            icon=icon,
            item_id=f"calc:{expr}",
            action=lambda t=raw_text: _paste_text(t),
            secondary_action=lambda t=raw_text: _copy_to_clipboard(t),
        )

    # -- math expression -----------------------------------------------------

    def _try_math_item(self, expr: str) -> ChooserItem | None:
        if not _looks_like_math(expr):
            return None
        if not _is_complete(expr):
            return None

        # Preprocess: ^ → **
        eval_expr = expr.replace("^", "**")

        try:
            value = self._eval.eval(eval_expr)
        except Exception:
            return None

        # Reject non-numeric results
        if not isinstance(value, (int, float)):
            return None
        # Reject inf / nan
        if isinstance(value, float) and (math.isinf(value) or math.isnan(value)):
            return None

        display, raw = _format_number(value)
        title = f"{expr} = {display}"
        icon = _get_calculator_icon()

        return ChooserItem(
            title=title,
            subtitle="Calculator",
            icon=icon,
            item_id=f"calc:{expr}",
            action=lambda t=raw: _paste_text(t),
            secondary_action=lambda t=raw: _copy_to_clipboard(t),
        )
