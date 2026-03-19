"""Detect repetitive patterns in LLM streaming output."""

from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Minimum total repeated characters before triggering detection.
_MIN_REPEATED_CHARS = 20

# Minimum number of repeats regardless of pattern length.
_MIN_REPEATS = 4


def _find_repeating_pattern(
    tail: str,
    min_repeated_chars: int = _MIN_REPEATED_CHARS,
    min_repeats: int = _MIN_REPEATS,
) -> Optional[Tuple[str, int]]:
    """Find a repeating pattern at the end of *tail*.

    Returns ``(pattern, needed_repeats)`` if found, or ``None``.
    """
    if not tail:
        return None

    max_pat_len = len(tail) // min_repeats
    for pat_len in range(1, max_pat_len + 1):
        needed = max(min_repeats, min_repeated_chars // pat_len)
        if pat_len * needed > len(tail):
            continue

        pattern = tail[-pat_len:]
        if not pattern.strip():
            continue

        matched = True
        for i in range(1, needed):
            start = len(tail) - pat_len * (i + 1)
            if start < 0 or tail[start : start + pat_len] != pattern:
                matched = False
                break
        if matched:
            return pattern, needed

    return None


def detect_repetition(
    text: str,
    *,
    min_repeated_chars: int = _MIN_REPEATED_CHARS,
    min_repeats: int = _MIN_REPEATS,
    check_window: int = 200,
) -> bool:
    """Check whether *text* ends with a repeating pattern.

    Scans the tail of *text* for any pattern that repeats consecutively.
    Shorter patterns require more repeats to trigger (scaled so that the
    total repeated length reaches *min_repeated_chars*).

    Args:
        text: The accumulated output text to check.
        min_repeated_chars: Require ``pattern_len * repeats >= this`` before
            flagging.  Default 20 — e.g. a single char must repeat 20×,
            a 4-char pattern 5×.
        min_repeats: Absolute minimum repeats regardless of pattern length.
            Default 4.
        check_window: Only inspect the last *check_window* characters of
            *text*.  Keeps cost constant.

    Returns:
        ``True`` if a repetition loop is detected.
    """
    tail = text[-check_window:] if len(text) > check_window else text
    result = _find_repeating_pattern(tail, min_repeated_chars, min_repeats)
    if result is not None:
        pattern, needed = result
        preview = pattern if len(pattern) <= 30 else pattern[:30] + "..."
        logger.warning(
            "Repetition detected: pattern=%r repeated %d× (pat_len=%d)",
            preview, needed, len(pattern),
        )
        return True
    return False


def truncate_repeated(
    text: str,
    *,
    min_repeated_chars: int = _MIN_REPEATED_CHARS,
    min_repeats: int = _MIN_REPEATS,
    check_window: int = 200,
) -> str:
    """If *text* ends with a repeating pattern, truncate to keep one copy.

    Returns the original text unchanged if no repetition is detected.
    """
    tail = text[-check_window:] if len(text) > check_window else text
    result = _find_repeating_pattern(tail, min_repeated_chars, min_repeats)
    if result is None:
        return text

    pattern, needed = result
    # The repeated block sits at the very end of text.  Compute how far
    # back the consecutive repetitions extend and keep one copy.
    pat_len = len(pattern)
    end = len(text)
    # Walk backwards past all consecutive copies (may exceed `needed`)
    while end - pat_len >= 0 and text[end - pat_len : end] == pattern:
        end -= pat_len
    # Keep one copy of the pattern after the non-repeating prefix
    return text[: end + pat_len]
