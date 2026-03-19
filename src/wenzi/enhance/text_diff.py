"""Shared inline-diff utilities for comparing ASR and corrected text."""

from __future__ import annotations

import difflib
import re
from typing import List

# ASCII words as whole units, each non-ASCII char individually,
# whitespace runs, or any other single character.
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+|[^\x00-\x7f]|\s+|.")


def tokenize_for_diff(text: str) -> List[str]:
    """Split text into diff-friendly tokens.

    English/number sequences stay as whole tokens; each CJK character
    becomes its own token.  This gives a good granularity for diffing
    mixed Chinese-English ASR text.
    """
    return _TOKEN_RE.findall(text)


def inline_diff(asr: str, final: str) -> str:
    """Produce an inline diff between ASR text and corrected text.

    Only replacements are bracketed as ``[old→new]``.  Insertions
    and deletions are applied silently (new text included / old text
    omitted) since they carry no ASR-misrecognition information
    useful for vocabulary extraction.
    """
    if asr == final:
        return asr

    asr_tokens = tokenize_for_diff(asr)
    final_tokens = tokenize_for_diff(final)
    matcher = difflib.SequenceMatcher(None, asr_tokens, final_tokens)

    parts: List[str] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            parts.append("".join(asr_tokens[i1:i2]))
        elif op == "replace":
            old = "".join(asr_tokens[i1:i2])
            new = "".join(final_tokens[j1:j2])
            parts.append(f"[{old}→{new}]")
        elif op == "insert":
            parts.append("".join(final_tokens[j1:j2]))
        # delete: omit old text silently
    return "".join(parts)
