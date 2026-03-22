"""CorrectionTracker: records ASR/LLM correction sessions and word-level diff pairs."""

from __future__ import annotations

import sqlite3
from difflib import SequenceMatcher
from typing import Optional

from .text_diff import tokenize_for_diff, _is_punctuation_only
from .vocabulary import _MIN_LATIN_VARIANT_LENGTH

_DEFAULT_MAX_REPLACE_TOKENS = 8

_SCHEMA_VERSION = 1

SOURCE_ASR = "asr"
SOURCE_LLM = "llm"

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
        self._common_words: Optional[set[str]] = None
        self._init_db()

    def _get_common_words(self) -> set[str]:
        """Lazy-load and cache the common words set."""
        if self._common_words is None:
            from wenzi.enhance.vocabulary_builder import _load_common_words
            self._common_words = _load_common_words()
        return self._common_words

    def _should_exclude(self, original: str, corrected: str) -> bool:
        """Return True if a correction pair should be auto-excluded.

        Exclusion rules (applied in order):
        1. If ``corrected.lower()`` is in the common words set, the corrected
           word is a generic common word — exclude it.
        2. If the original word is a short (< _MIN_LATIN_VARIANT_LENGTH chars)
           pure ASCII alphanumeric token, it is likely a trivial Latin variant —
           exclude it.
        """
        if corrected.lower() in self._get_common_words():
            return True
        if _is_latin(original) and len(original) < _MIN_LATIN_VARIANT_LENGTH:
            return True
        return False

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
                self._upsert_pair(conn, session_id, SOURCE_ASR, original, corrected, asr_model, _llm, _app, now)

            # LLM diffs: enhanced_text → final_text (only if user corrected)
            if user_corrected and _enhanced and _enhanced != final_text:
                llm_pairs = extract_word_pairs(_enhanced, final_text)
                for original, corrected in llm_pairs:
                    self._upsert_pair(conn, session_id, SOURCE_LLM, original, corrected, asr_model, _llm, _app, now)

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
        """Insert or increment count for a correction pair.

        Auto-exclusion is applied only on first insert; subsequent upserts do not
        change the excluded flag.
        """
        excluded_flag = int(self._should_exclude(original, corrected))
        conn.execute(
            """INSERT INTO correction_pairs
               (session_id, source, original_word, corrected_word, asr_model, llm_model,
                app_bundle_id, count, first_seen, last_seen, excluded)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(source, original_word, corrected_word, asr_model, llm_model, app_bundle_id)
               DO UPDATE SET count = count + 1, last_seen = excluded.last_seen""",
            (session_id, source, original, corrected, asr_model, llm_model, app_bundle_id,
             timestamp, timestamp, excluded_flag),
        )

    def backfill_from_history(self, conversation_history) -> int:
        """Import correction sessions from conversation history that are not yet tracked.

        Iterates all records in conversation_history, skips non-proofread entries,
        entries where asr_text equals final_text, and entries already present by
        timestamp. Returns the count of sessions imported.
        """
        all_records = conversation_history.get_all()

        conn = self._get_conn()
        try:
            existing_ts = {row[0] for row in conn.execute("SELECT timestamp FROM correction_sessions").fetchall()}
        finally:
            conn.close()

        imported = 0
        for record in all_records:
            ts = record.get("timestamp", "")
            if ts in existing_ts:
                continue
            if record.get("enhance_mode") != "proofread":
                continue
            asr_text = record.get("asr_text", "")
            final_text = record.get("final_text", "")
            if not asr_text or not final_text or asr_text == final_text:
                continue
            input_ctx = record.get("input_context") or {}
            app_bundle_id = input_ctx.get("bundle_id", "") if isinstance(input_ctx, dict) else ""

            self.record(
                asr_text=asr_text,
                enhanced_text=record.get("enhanced_text", ""),
                final_text=final_text,
                asr_model=record.get("stt_model", ""),
                llm_model=record.get("llm_model", ""),
                app_bundle_id=app_bundle_id,
                enhance_mode="proofread",
                audio_duration=record.get("audio_duration"),
                user_corrected=record.get("user_corrected", False),
                timestamp=ts,
            )
            imported += 1
        return imported

    def mark_excluded(self, pair_id: int, excluded: bool = True) -> None:
        """Manually set or clear the excluded flag for a correction pair."""
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE correction_pairs SET excluded = ? WHERE id = ?",
                (int(excluded), pair_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_asr_hotwords(
        self,
        asr_model: str,
        app_bundle_id: Optional[str] = None,
        min_count: int = 5,
        top_k: int = 20,
    ) -> list[str]:
        """Return the top-k corrected words for a given ASR model and app.

        Only non-excluded pairs with cumulative count >= min_count are included,
        ordered by frequency descending.
        """
        _app = app_bundle_id or ""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT corrected_word, SUM(count) as freq
                   FROM correction_pairs
                   WHERE source = 'asr' AND asr_model = ? AND app_bundle_id = ? AND excluded = 0
                   GROUP BY corrected_word HAVING freq >= ?
                   ORDER BY freq DESC LIMIT ?""",
                (asr_model, _app, min_count, top_k),
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    def get_llm_vocab(
        self,
        llm_model: str,
        app_bundle_id: Optional[str] = None,
        min_count: int = 5,
        top_k: int = 10,
    ) -> list[dict]:
        """Return vocabulary entries for a given LLM model and app.

        Each entry contains the corrected word, its known variants (original forms),
        and cumulative frequency. Only non-excluded pairs with count >= min_count
        are included, ordered by frequency descending.
        """
        _app = app_bundle_id or ""
        conn = self._get_conn()
        try:
            # Single query: aggregate frequency and collect variants via GROUP_CONCAT
            rows = conn.execute(
                """SELECT corrected_word, SUM(count) as freq,
                          GROUP_CONCAT(DISTINCT original_word) as variants
                   FROM correction_pairs
                   WHERE source = 'llm' AND llm_model = ? AND app_bundle_id = ? AND excluded = 0
                   GROUP BY corrected_word HAVING freq >= ?
                   ORDER BY freq DESC LIMIT ?""",
                (llm_model, _app, min_count, top_k),
            ).fetchall()
            return [
                {
                    "corrected_word": corrected,
                    "variants": variants.split(",") if variants else [],
                    "frequency": freq,
                }
                for corrected, freq, variants in rows
            ]
        finally:
            conn.close()
