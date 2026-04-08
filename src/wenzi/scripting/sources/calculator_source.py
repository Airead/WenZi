"""Calculator data source for the Chooser.

Provides inline math evaluation directly in the search bar.
Math is powered by a safe AST-based evaluator (no ``eval``).
"""

from __future__ import annotations

import ast
import logging
import math
import operator
import re
from typing import List

from wenzi.scripting.sources import (
    ChooserItem, ChooserSource, copy_to_clipboard, paste_text,
)

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

_CALC_ICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
    "%3Crect x='3' y='2' width='26' height='28' rx='4' fill='%23fd9426'/%3E"
    "%3Crect x='7' y='5' width='18' height='7' rx='1.5' fill='%23fff' opacity='.95'/%3E"
    "%3Ccircle cx='10' cy='17' r='2' fill='%23fff' opacity='.9'/%3E"
    "%3Ccircle cx='16' cy='17' r='2' fill='%23fff' opacity='.9'/%3E"
    "%3Ccircle cx='22' cy='17' r='2' fill='%23fff' opacity='.9'/%3E"
    "%3Ccircle cx='10' cy='23' r='2' fill='%23fff' opacity='.9'/%3E"
    "%3Ccircle cx='16' cy='23' r='2' fill='%23fff' opacity='.9'/%3E"
    "%3Ccircle cx='22' cy='23' r='2' fill='%2347d16c'/%3E"
    "%3C/svg%3E"
)

# ---------------------------------------------------------------------------
# Safe AST evaluator
# ---------------------------------------------------------------------------

_SAFE_NAMES: dict[str, object] = {"pi": math.pi, "e": math.e}

_SAFE_FUNCS: dict[str, object] = {
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

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _safe_eval(expr: str) -> object:
    """Evaluate a math expression safely via the AST.

    Only allows numeric literals, basic arithmetic operators, and
    whitelisted function calls.  Raises ``ValueError`` for anything
    unexpected.
    """
    tree = ast.parse(expr, mode="eval")
    return _eval_node(tree.body)


def _eval_node(node: ast.expr) -> object:
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise ValueError(f"unsupported constant: {node.value!r}")
        return node.value

    if isinstance(node, ast.Name):
        if node.id in _SAFE_NAMES:
            return _SAFE_NAMES[node.id]
        raise ValueError(f"unknown name: {node.id!r}")

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"unsupported unary op: {node.op!r}")
        return op(_eval_node(node.operand))

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"unsupported binary op: {node.op!r}")
        return op(_eval_node(node.left), _eval_node(node.right))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("only simple function calls allowed")
        func = _SAFE_FUNCS.get(node.func.id)
        if func is None:
            raise ValueError(f"unknown function: {node.func.id!r}")
        args = [_eval_node(a) for a in node.args]
        return func(*args)

    raise ValueError(f"unsupported node: {type(node).__name__}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# CalculatorSource
# ---------------------------------------------------------------------------


class CalculatorSource:
    """Inline calculator for the Chooser."""

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

        item = self._try_math_item(expr)
        if item is not None:
            return [item]

        return []

    def as_chooser_source(self) -> ChooserSource:
        from wenzi.i18n import t

        return ChooserSource(
            name="calculator",
            prefix=None,
            search=self.search,
            priority=12,
            description="Calculator",
            action_hints={
                "enter": t("chooser.action.paste"),
                "cmd_enter": t("chooser.action.copy"),
            },
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
            value = _safe_eval(eval_expr)
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
        icon = _CALC_ICON

        return ChooserItem(
            title=title,
            subtitle="Calculator",
            icon=icon,
            item_id=f"calc:{expr}",
            action=lambda t=raw: paste_text(t),
            secondary_action=lambda t=raw: copy_to_clipboard(t),
        )
