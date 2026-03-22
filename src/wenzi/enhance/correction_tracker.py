"""CorrectionTracker: records ASR/LLM correction sessions and word-level diff pairs."""

from __future__ import annotations

import sqlite3
from difflib import SequenceMatcher
from typing import Optional

from .text_diff import tokenize_for_diff, _is_punctuation_only

_DEFAULT_MAX_REPLACE_TOKENS = 8

_SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS correction_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    asr_text TEXT NOT NULL,
    enhanced_text TEXT NOT NULL DEFAULT '',
    final_text TEXT NOT NULL,
    asr_model TEXT NOT NULL,
    llm_model TEXT NOT NULL DEFAULT '',
    app_bundle_id TEXT NOT NULL DEFAULT '',
    enhance_mode TEXT NOT NULL DEFAULT '',
    audio_duration REAL,
    user_corrected INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_timestamp ON correction_sessions(timestamp);

CREATE TABLE IF NOT EXISTS correction_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES correction_sessions(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    original_word TEXT NOT NULL,
    corrected_word TEXT NOT NULL,
    asr_model TEXT NOT NULL DEFAULT '',
    llm_model TEXT NOT NULL DEFAULT '',
    app_bundle_id TEXT NOT NULL DEFAULT '',
    count INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    excluded INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source, original_word, corrected_word, asr_model, llm_model, app_bundle_id)
);

CREATE INDEX IF NOT EXISTS idx_pairs_asr_query ON correction_pairs(source, asr_model, app_bundle_id, excluded);
CREATE INDEX IF NOT EXISTS idx_pairs_llm_query ON correction_pairs(source, llm_model, app_bundle_id, excluded);
"""


def _is_latin(token: str) -> bool:
    """Return True if token consists entirely of ASCII alphanumeric characters."""
    return all(ch.isascii() and ch.isalnum() for ch in token) and len(token) > 0


def _join_tokens(tokens: list[str]) -> str:
    """Join tokens, restoring spaces between consecutive Latin tokens."""
    if not tokens:
        return ""
    parts = [tokens[0]]
    for i in range(1, len(tokens)):
        if _is_latin(tokens[i - 1]) and _is_latin(tokens[i]):
            parts.append(" ")
        parts.append(tokens[i])
    return "".join(parts)


def extract_word_pairs(
    text_a: str,
    text_b: str,
    max_replace_tokens: int = _DEFAULT_MAX_REPLACE_TOKENS,
) -> list[tuple[str, str]]:
    """Extract word-level correction pairs from two texts using diff.

    Returns a list of (original, corrected) tuples derived from replace opcodes.
    Replace blocks larger than max_replace_tokens on either side are skipped.
    Punctuation-only replacements are also skipped.
    """
    if text_a == text_b:
        return []
    tokens_a = tokenize_for_diff(text_a)
    tokens_b = tokenize_for_diff(text_b)
    matcher = SequenceMatcher(None, tokens_a, tokens_b)
    pairs: list[tuple[str, str]] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op != "replace":
            continue
        if (i2 - i1) > max_replace_tokens or (j2 - j1) > max_replace_tokens:
            continue
        original = _join_tokens(tokens_a[i1:i2])
        corrected = _join_tokens(tokens_b[j1:j2])
        if _is_punctuation_only(original) or _is_punctuation_only(corrected):
            continue
        if original.strip() and corrected.strip():
            pairs.append((original, corrected))
    return pairs


class CorrectionTracker:
    """Tracks correction sessions and word-level diff pairs in a SQLite database."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Open a new connection with foreign keys enabled."""
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        """Create schema and set user_version if not already done."""
        conn = self._get_conn()
        try:
            conn.executescript(_DDL)
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            conn.commit()
        finally:
            conn.close()

    def record(
        self,
        asr_text: str,
        enhanced_text: Optional[str],
        final_text: str,
        asr_model: str,
        llm_model: Optional[str],
        app_bundle_id: Optional[str],
        enhance_mode: Optional[str],
        audio_duration: Optional[float],
        user_corrected: bool,
        timestamp: Optional[str] = None,
    ) -> None:
        """Record a correction session and extract word-level diff pairs.

        ASR pairs are always extracted (asr_text → final_text).
        LLM pairs are only extracted when user_corrected is True and
        enhanced_text differs from final_text.
        """
        from datetime import datetime, timezone

        now = timestamp or datetime.now(timezone.utc).isoformat()
        _llm = llm_model or ""
        _app = app_bundle_id or ""
        _enhanced = enhanced_text or ""
        _mode = enhance_mode or ""

        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """INSERT INTO correction_sessions
                   (timestamp, asr_text, enhanced_text, final_text, asr_model, llm_model,
                    app_bundle_id, enhance_mode, audio_duration, user_corrected)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, asr_text, _enhanced, final_text, asr_model, _llm, _app, _mode,
                 audio_duration, int(user_corrected)),
            )
            session_id = cursor.lastrowid

            # ASR diffs: asr_text → final_text
            asr_pairs = extract_word_pairs(asr_text, final_text)
            for original, corrected in asr_pairs:
                self._upsert_pair(conn, session_id, "asr", original, corrected, asr_model, _llm, _app, now)

            # LLM diffs: enhanced_text → final_text (only if user corrected)
            if user_corrected and _enhanced and _enhanced != final_text:
                llm_pairs = extract_word_pairs(_enhanced, final_text)
                for original, corrected in llm_pairs:
                    self._upsert_pair(conn, session_id, "llm", original, corrected, asr_model, _llm, _app, now)

            conn.commit()
        finally:
            conn.close()

    def _upsert_pair(
        self,
        conn: sqlite3.Connection,
        session_id: int,
        source: str,
        original: str,
        corrected: str,
        asr_model: str,
        llm_model: str,
        app_bundle_id: str,
        timestamp: str,
    ) -> None:
        """Insert or increment count for a correction pair."""
        conn.execute(
            """INSERT INTO correction_pairs
               (session_id, source, original_word, corrected_word, asr_model, llm_model,
                app_bundle_id, count, first_seen, last_seen, excluded)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 0)
               ON CONFLICT(source, original_word, corrected_word, asr_model, llm_model, app_bundle_id)
               DO UPDATE SET count = count + 1, last_seen = excluded.last_seen""",
            (session_id, source, original, corrected, asr_model, llm_model, app_bundle_id,
             timestamp, timestamp),
        )
