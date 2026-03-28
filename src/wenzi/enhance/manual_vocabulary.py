"""Manual vocabulary store — user-curated correction pairs.

Each entry represents a single variant->term pair that the user has explicitly
confirmed.  Entries carry rich metadata (app, model, timestamps) and are
persisted in a SQLite database via :class:`VocabDB`.
"""

from __future__ import annotations

import logging
import string
from dataclasses import dataclass
from typing import Optional

from wenzi.enhance.vocab_db import (
    CTX_APP,
    CTX_ASR,
    CTX_LLM,
    METRIC_ASR_HIT,
    METRIC_ASR_MISS,
    METRIC_LLM_HIT,
    METRIC_LLM_MISS,
    VocabDB,
    build_context_keys,
)

logger = logging.getLogger(__name__)

# Allowed values for ManualVocabEntry.source
SOURCE_ASR = "asr"
SOURCE_LLM = "llm"
SOURCE_USER = "user"


@dataclass
class ManualVocabEntry:
    """A single user-confirmed correction pair."""

    term: str  # correct form ("Kubernetes")
    variant: str  # ASR / LLM erroneous form ("库伯尼特斯")
    source: str = SOURCE_ASR
    frequency: int = 1  # times the user added/confirmed this pair
    first_seen: str = ""  # ISO 8601
    last_updated: str = ""  # ISO 8601
    app_bundle_id: str = ""  # e.g. "com.apple.dt.Xcode"
    asr_model: str = ""
    llm_model: str = ""
    enhance_mode: str = ""
    id: int = 0  # database primary key


_STRIP_CHARS = string.whitespace + string.punctuation + "\u3000\u3001\u3002\uff0c\uff01\uff1f"


def _normalize(s: str) -> str:
    """Strip leading/trailing whitespace and punctuation."""
    return s.strip(_STRIP_CHARS)


def _entry_from_row(row: dict) -> ManualVocabEntry:
    """Convert a VocabDB row dict to a ManualVocabEntry."""
    return ManualVocabEntry(
        term=row["term"],
        variant=row["variant"],
        source=row.get("source", SOURCE_ASR),
        frequency=row.get("frequency", 1),
        first_seen=row.get("first_seen", ""),
        last_updated=row.get("last_updated", ""),
        app_bundle_id=row.get("app_bundle_id", ""),
        asr_model=row.get("asr_model", ""),
        llm_model=row.get("llm_model", ""),
        enhance_mode=row.get("enhance_mode", ""),
        id=row.get("id", 0),
    )


class ManualVocabularyStore:
    """SQLite-backed store for user-curated correction pairs.

    Thread safety is provided by the underlying :class:`VocabDB`.
    """

    MAX_LLM_ENTRIES: int = 5

    @staticmethod
    def _query_context_key(
        prefix: str, model: Optional[str], app_bundle_id: Optional[str],
    ) -> str:
        """Build a single context key for ranked queries (model takes priority)."""
        if model:
            return f"{prefix}:{model}"
        if app_bundle_id:
            return f"{CTX_APP}:{app_bundle_id}"
        return ""

    _DIMENSION_MAP = {
        METRIC_ASR_MISS: ("asr", "miss"),
        METRIC_ASR_HIT: ("asr", "hit"),
        METRIC_LLM_HIT: ("llm", "hit"),
        METRIC_LLM_MISS: ("llm", "miss"),
    }
    _SORT_KEY = {"asr": "miss", "llm": "hit"}

    def __init__(self, path: str) -> None:
        self._path = path
        self._db = VocabDB(path)

    @property
    def db(self) -> VocabDB:
        """Expose the underlying VocabDB for direct access."""
        return self._db

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(
        self,
        variant: str,
        term: str,
        source: str = SOURCE_ASR,
        *,
        app_bundle_id: str = "",
        asr_model: str = "",
        llm_model: str = "",
        enhance_mode: str = "",
        persist: bool = True,  # accepted for API compat, no effect
    ) -> ManualVocabEntry:
        """Add or update a correction pair.

        If the (variant, term) pair already exists, increment *frequency*
        and update *last_updated*.  Returns the (possibly updated) entry.
        """
        variant = _normalize(variant)
        term = _normalize(term)
        row = self._db.add(
            variant, term, source,
            app_bundle_id=app_bundle_id,
            asr_model=asr_model,
            llm_model=llm_model,
            enhance_mode=enhance_mode,
        )
        if row is None:
            return ManualVocabEntry(term=term, variant=variant, source=source)
        return _entry_from_row(row)

    def remove(self, variant: str, term: str) -> bool:
        """Remove a correction pair.  Returns True if it existed."""
        return self._db.remove(_normalize(variant), _normalize(term))

    def remove_batch(
        self, pairs: list[tuple[str, str]],
        *, persist: bool = True,  # accepted for API compat, no effect
    ) -> int:
        """Remove multiple correction pairs.  Returns count removed."""
        normalized = [(_normalize(v), _normalize(t)) for v, t in pairs]
        return self._db.remove_batch(normalized)

    def get(self, variant: str, term: str) -> Optional[ManualVocabEntry]:
        """Return the entry for a (variant, term) pair, or None."""
        row = self._db.get(_normalize(variant), _normalize(term))
        return _entry_from_row(row) if row else None

    def contains(self, variant: str, term: str) -> bool:
        """Check whether a (variant, term) pair exists."""
        return self._db.contains(_normalize(variant), _normalize(term))

    # ------------------------------------------------------------------
    # Two-phase hit tracking
    # ------------------------------------------------------------------

    def record_asr_phase(
        self,
        asr_text: str,
        *,
        asr_model: str = "",
        app_bundle_id: str = "",
    ) -> list[ManualVocabEntry]:
        """Phase 1: detect ASR hits/misses before LLM enhancement.

        Scans *asr_text* against all entries.  Records ``asr_miss`` when the
        variant appears (ASR got it wrong) and ``asr_hit`` when the term
        appears (ASR got it right).

        Returns the list of entries that were ``asr_miss``.
        """
        entries = self.get_all()
        asr_text_lower = asr_text.lower()
        context_keys = build_context_keys(
            model_prefix=CTX_ASR, model_name=asr_model,
            app_bundle_id=app_bundle_id,
        )

        asr_miss_entries: list[ManualVocabEntry] = []
        stats_batch: list[tuple[int, str, str]] = []

        for entry in entries:
            if entry.variant.lower() in asr_text_lower:
                for key in context_keys:
                    stats_batch.append((entry.id, METRIC_ASR_MISS, key))
                asr_miss_entries.append(entry)
            elif entry.term.lower() in asr_text_lower:
                for key in context_keys:
                    stats_batch.append((entry.id, METRIC_ASR_HIT, key))

        if stats_batch:
            self._db.record_stats(stats_batch)

        return asr_miss_entries

    def record_llm_phase(
        self,
        asr_miss_entries: list[ManualVocabEntry],
        enhanced_text: str,
        *,
        llm_model: str = "",
        app_bundle_id: str = "",
    ) -> None:
        """Phase 2: detect LLM correction results after enhancement.

        For each entry that was ``asr_miss`` in phase 1, checks whether the
        LLM successfully corrected it (``llm_hit``) or not (``llm_miss``).
        """
        enhanced_lower = enhanced_text.lower()
        context_keys = build_context_keys(
            model_prefix=CTX_LLM, model_name=llm_model,
            app_bundle_id=app_bundle_id,
        )

        stats_batch: list[tuple[int, str, str]] = []
        for entry in asr_miss_entries:
            metric = METRIC_LLM_HIT if entry.term.lower() in enhanced_lower else METRIC_LLM_MISS
            for key in context_keys:
                stats_batch.append((entry.id, metric, key))

        if stats_batch:
            self._db.record_stats(stats_batch)

    # ------------------------------------------------------------------
    # Legacy hit tracking (compatibility shim)
    # ------------------------------------------------------------------

    def record_hit(self, variant: str, term: str) -> None:
        """Record that this pair was used in a correction (legacy)."""
        self.record_hits([(variant, term)])

    def record_hits(self, pairs: list[tuple[str, str]]) -> None:
        """Record multiple hits in a single operation (legacy)."""
        stats_batch: list[tuple[int, str, str]] = []
        for variant, term in pairs:
            entry = self.get(variant, term)
            if entry is not None:
                stats_batch.append((entry.id, METRIC_LLM_HIT, "legacy"))
        if stats_batch:
            self._db.record_stats(stats_batch)

    def find_hits_in_text(self, text: str) -> list[ManualVocabEntry]:
        """Return entries whose *variant* appears in *text* (case-insensitive)."""
        text_lower = text.lower()
        return [e for e in self.get_all() if e.variant.lower() in text_lower]

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_all(self) -> list[ManualVocabEntry]:
        """Return a snapshot of all entries."""
        return [_entry_from_row(r) for r in self._db.get_all()]

    def get_all_for_state(self) -> list[dict]:
        """Return ``[{variant, term}]`` for JS-side state synchronization."""
        return [
            {"variant": r["variant"], "term": r["term"]}
            for r in self._db.get_all()
        ]

    def get_asr_hotwords(
        self,
        *,
        asr_model: Optional[str] = None,
        app_bundle_id: Optional[str] = None,
        max_count: int = 0,
    ) -> list[str]:
        """Return *term* strings for ASR hotword injection.

        Entries are ranked by ``asr_miss`` count in the corresponding bucket,
        with cold-start fallback to global stats then recency.
        When *max_count* is 0, all entries are returned.
        """
        context_key = self._query_context_key(CTX_ASR, asr_model, app_bundle_id)
        limit = max_count if max_count > 0 else max(self._db.entry_count, 1)
        ranked = self._db.top_with_fallback(METRIC_ASR_MISS, context_key, limit)

        seen: set[str] = set()
        result: list[str] = []
        for d in ranked:
            term_lower = d["term"].lower()
            if term_lower not in seen:
                seen.add(term_lower)
                result.append(d["term"])

        return result

    def get_llm_vocab(
        self,
        *,
        llm_model: Optional[str] = None,
        app_bundle_id: Optional[str] = None,
        max_entries: int = MAX_LLM_ENTRIES,
    ) -> list[ManualVocabEntry]:
        """Return entries for LLM prompt injection.

        Entries are ranked by ``llm_hit`` count in the corresponding bucket,
        with cold-start fallback.  At most *max_entries* are returned.
        """
        context_key = self._query_context_key(CTX_LLM, llm_model, app_bundle_id)
        ranked = self._db.top_with_fallback(METRIC_LLM_HIT, context_key, max_entries)
        return [_entry_from_row(d) for d in ranked]

    def get_entry_stats(self, variant: str, term: str) -> dict:
        """Return full bucketed statistics for an entry.

        Returns ``{"asr": [...], "llm": [...]}`` where each list item has
        ``context``, ``miss``, ``hit``, ``last`` keys.
        """
        entry = self.get(variant, term)
        if entry is None:
            return {"asr": [], "llm": []}

        stats = self._db.get_stats(entry.id)

        buckets: dict[str, dict[str, dict]] = {"asr": {}, "llm": {}}
        for s in stats:
            mapping = self._DIMENSION_MAP.get(s["metric"])
            if not mapping:
                continue
            dim, field = mapping
            bucket = buckets[dim].setdefault(
                s["context_key"],
                {"context": s["context_key"], "miss": 0, "hit": 0, "last": ""},
            )
            bucket[field] = s["count"]
            if s["last_time"] > bucket["last"]:
                bucket["last"] = s["last_time"]

        return {
            dim: sorted(dim_buckets.values(), key=lambda b: b[self._SORT_KEY[dim]], reverse=True)
            for dim, dim_buckets in buckets.items()
        }

    def rename_entry(self, entry_id: int, new_variant: str, new_term: str) -> Optional[ManualVocabEntry]:
        """Rename an entry's variant/term in place, preserving stats and frequency."""
        row = self._db.rename_entry(entry_id, _normalize(new_variant), _normalize(new_term))
        return _entry_from_row(row) if row else None

    def get_stats_summary_batch(
        self,
        entry_ids: list[int],
        metrics: list[str],
        context_key: str = "",
    ) -> dict[tuple[int, str], int]:
        """Batch fetch stats summaries — delegates to VocabDB."""
        return self._db.get_stats_summary_batch(entry_ids, metrics, context_key)

    def update_fields(self, entry_id: int, fields: dict) -> None:
        """Update specific fields on an entry by id (delegates to VocabDB)."""
        self._db.update_fields(entry_id, fields)

    @property
    def entry_count(self) -> int:
        return self._db.entry_count
