"""Chooser data sources, core data structures, and fuzzy matching."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared clipboard/paste helpers used by multiple sources
# ---------------------------------------------------------------------------


def paste_text(text: str) -> None:
    """Write *text* to clipboard and simulate Cmd+V to paste at cursor."""
    try:
        from wenzi.input import _set_pasteboard_concealed

        import subprocess
        import time

        _set_pasteboard_concealed(text)
        time.sleep(0.05)
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        _logger.exception("Failed to paste text")


def copy_to_clipboard(text: str) -> None:
    """Write *text* to the system clipboard without pasting."""
    try:
        from wenzi.input import _set_pasteboard_concealed

        _set_pasteboard_concealed(text)
    except Exception:
        _logger.exception("Failed to copy to clipboard")


@dataclass
class ModifierAction:
    """An alternative action triggered by holding a modifier key."""

    subtitle: str  # Shown when modifier is held
    action: Optional[Callable] = field(default=None, repr=False)


@dataclass
class ChooserItem:
    """A single item in the chooser result list."""

    title: str
    subtitle: str = ""
    icon: str = ""  # file:// URL, data: URI, or empty
    item_id: str = ""  # Stable identifier for usage tracking
    # {"type": "text"|"image", ...} or a callable returning such a dict
    preview: object = field(default=None, repr=False)
    action: Optional[Callable] = field(default=None, repr=False)
    secondary_action: Optional[Callable] = field(default=None, repr=False)  # Cmd+Enter
    reveal_path: Optional[str] = None  # For Cmd+Enter (reveal in Finder)
    modifiers: Optional[Dict[str, ModifierAction]] = field(
        default=None,
        repr=False,
    )  # key: "cmd", "alt", "ctrl", "shift"
    delete_action: Optional[Callable] = field(default=None, repr=False)
    confirm_delete: bool = False  # Two-step delete confirmation
    icon_badge: str = ""  # Short text rendered as badge on icon corner
    icon_accessory: str = ""  # Raw HTML injected into icon container
    complete_text: Optional[str] = None  # If set, Enter fills search box with this text instead of closing


@dataclass
class ChooserSource:
    """A data source that provides items to the chooser.

    Sources with a prefix (e.g. "cb") are activated when the query starts
    with "<prefix> " (prefix followed by a space), Alfred-style.
    Sources without a prefix participate in every search.
    """

    name: str
    display_name: Optional[str] = None  # Localized label for UI (prefix hints)
    prefix: Optional[str] = None
    search: Callable[[str], List[ChooserItem]] = field(default=None, repr=False)
    priority: int = 0  # Higher values appear first
    action_hints: Optional[Dict[str, str]] = field(default=None, repr=False)
    # action_hints keys: "enter", "cmd_enter", "delete", "tab"
    # e.g. {"enter": "Paste", "cmd_enter": "Copy", "delete": "Delete"}
    description: str = ""  # Human-readable description shown in help
    show_preview: bool = False  # Show the preview panel when this source is active
    complete: Optional[Callable[[str, "ChooserItem"], Optional[str]]] = field(
        default=None,
        repr=False,
    )  # Tab completion: (query, selected_item) -> completed query (without prefix) or None
    create_action: Optional[Callable[[str], None]] = field(
        default=None,
        repr=False,
    )  # Optional "create new" action; receives stripped query
    is_async: bool = False
    search_timeout: Optional[float] = None  # Async sources only; None = use global default
    debounce_delay: Optional[float] = None  # Async sources only; None = use global default, 0 = no debounce
    universal_action: bool = False  # Opt-in to Universal Action mode


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------


def fuzzy_match(query: str, text: str) -> Tuple[bool, int]:
    """Match *query* against *text* using multiple strategies.

    Returns ``(matched, score)`` where higher score means better match.
    Strategies (highest score first):
      - Exact prefix match (score 100)
      - Word-initials / CamelCase match (score 80)
      - Substring match (score 60)
      - Scattered character match (score 40)
    """
    if not query:
        return False, 0

    q = query.lower()
    t = text.lower()

    # 1. Exact prefix
    if t.startswith(q):
        return True, 100

    # 2. Substring
    sub_match = q in t
    if sub_match and len(q) == len(t):
        return True, 100  # exact match

    # 3. Word-initials / CamelCase match
    initials = _word_initials(text)
    if len(q) >= 1 and initials.startswith(q):
        return True, 80

    if sub_match:
        return True, 60

    # 4. Scattered character match — query chars appear in order in text
    if _chars_in_order(q, t):
        return True, 40

    return False, 0


def _word_initials(text: str) -> str:
    """Extract lowercase initials from words and CamelCase boundaries.

    "System Configuration" -> "sc"
    "DragonDrop" -> "dd"
    "Visual Studio Code" -> "vsc"
    """
    initials: list[str] = []
    prev_lower = False
    for i, ch in enumerate(text):
        if ch in (" ", "-", "_"):
            prev_lower = False
            continue
        if i == 0 or text[i - 1] in (" ", "-", "_"):
            # Start of a word
            initials.append(ch.lower())
            prev_lower = ch.islower()
        elif ch.isupper() and prev_lower:
            # CamelCase boundary
            initials.append(ch.lower())
            prev_lower = False
        else:
            prev_lower = ch.islower()
    return "".join(initials)


def _chars_in_order(query: str, text: str) -> bool:
    """Check if all chars of *query* appear in *text* in order."""
    it = iter(text)
    return all(ch in it for ch in query)


def fuzzy_match_fields(query: str, fields: Sequence[str]) -> Tuple[bool, int]:
    """Multi-term AND fuzzy match across multiple fields.

    Splits *query* on whitespace.  Each term must fuzzy-match at least one
    field.  Returns ``(matched, avg_score)``.  A single-term query degrades
    to the same behaviour as calling :func:`fuzzy_match` on each field and
    taking the best score.
    """
    terms = query.split()
    if not terms:
        return False, 0

    total_score = 0
    for term in terms:
        best = 0
        for fval in fields:
            matched, score = fuzzy_match(term, fval)
            if matched and score > best:
                best = score
        if best == 0:
            return False, 0
        total_score += best
    return True, total_score // len(terms)
