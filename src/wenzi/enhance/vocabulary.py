"""Vocabulary index and retrieval using inverted index + pinyin matching."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Set

if TYPE_CHECKING:
    from wenzi.enhance.conversation_history import ConversationHistory
    from wenzi.enhance.correction_tracker import CorrectionTracker

from wenzi.config import DEFAULT_DATA_DIR

logger = logging.getLogger(__name__)

# Minimum variant/term length to index (avoid noisy single-char matches)
_MIN_INDEX_LENGTH = 2

# Pure-Latin variants shorter than this are too noisy for substring matching
# (e.g. "set" matches "Settings", "doc" matches "Docker").
# CJK characters carry more information per char, so _MIN_INDEX_LENGTH suffices.
_MIN_LATIN_VARIANT_LENGTH = 4


@dataclass
class VocabularyEntry:
    """A single vocabulary entry extracted from correction logs."""

    term: str
    category: str = "other"
    variants: List[str] = field(default_factory=list)
    context: str = ""
    frequency: int = 1
    last_seen: str = ""  # ISO 8601 timestamp (persisted to vocabulary.json)
    last_seen_ts: float = 0.0  # Epoch seconds (derived at load time, not persisted)


LAYER_CONTEXT = "context"
LAYER_BASE = "base"


@dataclass
class HotwordDetail:
    """A hotword entry with full metadata for display in the preview panel."""

    term: str
    layer: str  # LAYER_CONTEXT | LAYER_BASE
    category: str = "other"
    variants: List[str] = field(default_factory=list)
    context: str = ""
    frequency: int = 1
    last_seen: str = ""
    score: float = 0.0
    recency_bonus: int = 0


# Recency tier thresholds: (max_age_seconds, bonus_points)
_RECENCY_TIERS = (
    (86400.0, 3),  # < 24h → +3
    (7 * 86400.0, 2),  # < 7d  → +2
    (30 * 86400.0, 1),  # < 30d → +1
)


def _recency_bonus(last_seen_ts: float, now: float) -> int:
    """Return a recency bonus based on how recently the term was seen."""
    if last_seen_ts <= 0:
        return 0
    age = now - last_seen_ts
    for threshold, bonus in _RECENCY_TIERS:
        if age < threshold:
            return bonus
    return 0


def hotword_score(frequency: int, last_seen_ts: float, now: float) -> float:
    """Compute score = frequency + recency_bonus for hotword ranking."""
    return frequency + _recency_bonus(last_seen_ts, now)


def _has_cjk(text: str) -> bool:
    """Return True if *text* contains at least one CJK Unified Ideograph."""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _min_variant_length(variant: str) -> int:
    """Return the minimum length threshold for a variant to be matchable."""
    return _MIN_INDEX_LENGTH if _has_cjk(variant) else _MIN_LATIN_VARIANT_LENGTH


class VocabularyIndex:
    """Inverted-index vocabulary lookup for retrieval during text enhancement.

    Two retrieval layers:
    1. **Exact** — sliding-window substring match on lowercased text.
    2. **Pinyin** (fallback) — space-joined ``lazy_pinyin`` substring match,
       only invoked when exact matches < *top_k*.
    """

    def __init__(
        self,
        config: dict,
        data_dir: str = DEFAULT_DATA_DIR,
    ) -> None:
        self._data_dir = os.path.expanduser(data_dir)
        self._vocab_path = os.path.join(self._data_dir, "vocabulary.json")

        self._entries: List[VocabularyEntry] = []

        # Exact-match index: length → {lowercased_string → [entry_indices]}
        self._variants_by_length: Dict[int, Dict[str, List[int]]] = {}

        # Pinyin index: pinyin_string → [entry_indices]
        self._pinyin_index: Dict[str, List[int]] = {}

        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def load(self) -> bool:
        """Load vocabulary.json and build the inverted index.

        Returns True if loaded successfully.
        """
        try:
            entries = self._read_vocabulary()
            if not entries:
                logger.info("No vocabulary entries found")
                return False

            self._entries = entries
            self._build_index()
            self._loaded = True
            logger.info(
                "Vocabulary loaded: %d entries, %d exact keys, %d pinyin keys",
                len(self._entries),
                sum(len(b) for b in self._variants_by_length.values()),
                len(self._pinyin_index),
            )
            return True
        except Exception as e:
            logger.warning("Failed to load vocabulary: %s", e)
            return False

    def reload(self) -> bool:
        """Reload vocabulary and rebuild index."""
        self._loaded = False
        self._entries = []
        self._variants_by_length = {}
        self._pinyin_index = {}
        return self.load()

    def retrieve(self, text: str, top_k: int = 5) -> List[VocabularyEntry]:
        """Retrieve relevant vocabulary entries for *text*.

        Only entries matched via a **variant** (ASR misrecognition) are
        returned.  Term-only matches are filtered out because they indicate
        the ASR already produced the correct form — no correction needed.

        Layer 1 (exact substring match) runs first.  If it already yields
        *top_k* or more results the pinyin layer is skipped entirely.
        """
        text = text.strip()
        if not self._loaded or not text:
            return []

        try:
            text_lower = text.lower()

            exact_indices = self._exact_search(text, text_lower=text_lower)
            exact_indices = self._filter_variant_matched(
                exact_indices,
                lambda v: len(v) >= _min_variant_length(v) and v.lower() in text_lower,
            )

            if len(exact_indices) >= top_k:
                return self._rank(exact_indices, set(), top_k)

            text_py = self._to_pinyin(text)
            pinyin_indices = self._pinyin_search(
                text, exact_indices, text_py=text_py
            )
            if pinyin_indices:

                def _variant_pinyin_match(v: str) -> bool:
                    if not _has_cjk(v):
                        return False
                    py = self._to_pinyin(v)
                    return bool(py) and py in text_py

                pinyin_indices = self._filter_variant_matched(
                    pinyin_indices, _variant_pinyin_match
                )
            return self._rank(exact_indices, pinyin_indices, top_k)
        except Exception as e:
            logger.warning("Vocabulary retrieval failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Term extraction (for hotword building)
    # ------------------------------------------------------------------

    def find_terms_in_text(self, text: str) -> List[VocabularyEntry]:
        """Find vocabulary entries whose term/variant appears in *text*.

        Uses exact substring matching only (no pinyin layer) to avoid noisy
        matches.  Returns entries sorted by frequency descending.
        """
        text = text.strip()
        if not self._loaded or not text:
            return []

        try:
            indices = self._exact_search(text)
            now = datetime.now(timezone.utc).timestamp()
            sorted_indices = sorted(
                indices,
                key=lambda i: -hotword_score(
                    self._entries[i].frequency, self._entries[i].last_seen_ts, now
                ),
            )
            return [self._entries[i] for i in sorted_indices]
        except Exception as e:
            logger.warning("find_terms_in_text failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Formatting (unchanged public API)
    # ------------------------------------------------------------------

    @staticmethod
    def format_entry_lines(entries: List["VocabularyEntry"]) -> str:
        """Format vocabulary entries as plain lines (no header/footer).

        Returns a newline-joined string like::

            - term1（context1）
            - term2

        Used by :class:`TextEnhancer` inside the combined context section.
        """
        if not entries:
            return ""
        lines: list[str] = []
        for entry in entries:
            if entry.context:
                lines.append(f"- {entry.term}（{entry.context}）")
            else:
                lines.append(f"- {entry.term}")
        return "\n".join(lines)

    def format_for_prompt(self, entries: List[VocabularyEntry]) -> str:
        """Format vocabulary entries for injection into LLM prompt.

        Returns a self-contained section with header and footer.
        """
        if not entries:
            return ""

        header = (
            "---\n"
            "以下是用户词库中与本次输入相关的专有名词，ASR 常将其误写为同音近音词。\n"
            "仅当输入中确实存在对应误写时才替换，不要强行套用：\n"
            "\n"
        )
        return header + self.format_entry_lines(entries) + "\n---"

    # ------------------------------------------------------------------
    # Index construction
    # ------------------------------------------------------------------

    def _build_index(self) -> None:
        """Build inverted indices from *self._entries*."""
        variants_by_length: Dict[int, Dict[str, List[int]]] = {}
        pinyin_index: Dict[str, List[int]] = {}
        seen_exact: set[tuple[str, int]] = set()
        seen_pinyin: set[tuple[str, int]] = set()

        for i, entry in enumerate(self._entries):
            strings = [entry.term] + entry.variants
            for s in strings:
                if len(s) < _MIN_INDEX_LENGTH:
                    continue

                # Exact-match index (deduplicated)
                key = s.lower()
                exact_pair = (key, i)
                if exact_pair not in seen_exact:
                    seen_exact.add(exact_pair)
                    bucket = variants_by_length.setdefault(len(key), {})
                    bucket.setdefault(key, []).append(i)

                # Pinyin index (CJK only, deduplicated)
                if _has_cjk(s):
                    py = self._to_pinyin(s)
                    if py:
                        pinyin_pair = (py, i)
                        if pinyin_pair not in seen_pinyin:
                            seen_pinyin.add(pinyin_pair)
                            pinyin_index.setdefault(py, []).append(i)

        self._variants_by_length = variants_by_length
        self._pinyin_index = pinyin_index

    # ------------------------------------------------------------------
    # Search layers
    # ------------------------------------------------------------------

    def _exact_search(
        self, text: str, *, text_lower: str | None = None
    ) -> Set[int]:
        """Sliding-window exact substring match, returns matched entry indices."""
        if text_lower is None:
            text_lower = text.lower()
        matched: Set[int] = set()

        for length, bucket in self._variants_by_length.items():
            if length > len(text_lower):
                continue
            for i in range(len(text_lower) - length + 1):
                substr = text_lower[i : i + length]
                indices = bucket.get(substr)
                if indices is not None:
                    matched.update(indices)

        return matched

    def _pinyin_search(
        self, text: str, exclude: Set[int], *, text_py: str | None = None
    ) -> Set[int]:
        """Pinyin substring match on *text*, excluding already-found indices."""
        if text_py is None:
            text_py = self._to_pinyin(text)
        if not text_py:
            return set()

        matched: Set[int] = set()
        for variant_py, entry_indices in self._pinyin_index.items():
            if variant_py in text_py:
                for idx in entry_indices:
                    if idx not in exclude:
                        matched.add(idx)
        return matched

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    def _rank(
        self,
        exact_indices: Set[int],
        pinyin_indices: Set[int],
        top_k: int,
    ) -> List[VocabularyEntry]:
        """Merge and rank results: exact first, then pinyin, each by score desc."""
        now = datetime.now(timezone.utc).timestamp()

        def _score(i: int) -> float:
            return -hotword_score(
                self._entries[i].frequency, self._entries[i].last_seen_ts, now
            )

        exact_sorted = sorted(exact_indices, key=_score)
        pinyin_sorted = sorted(pinyin_indices, key=_score)
        combined = exact_sorted + pinyin_sorted
        return [self._entries[i] for i in combined[:top_k]]

    # ------------------------------------------------------------------
    # Variant-only filtering
    # ------------------------------------------------------------------

    def _filter_variant_matched(
        self,
        indices: Set[int],
        predicate: Callable[[str], bool],
    ) -> Set[int]:
        """Keep only entries where at least one variant satisfies *predicate*."""
        result: Set[int] = set()
        for i in indices:
            for v in self._entries[i].variants:
                if predicate(v):
                    result.add(i)
                    break
        return result

    # ------------------------------------------------------------------
    # Pinyin helpers
    # ------------------------------------------------------------------

    # Cached reference to pypinyin.lazy_pinyin (lazily resolved on first use)
    _lazy_pinyin_func = None

    @staticmethod
    def _to_pinyin(text: str) -> str:
        """Convert *text* to space-joined toneless pinyin.

        Returns empty string on failure.  ``pypinyin`` is lazily imported
        so the dependency is only incurred when the pinyin layer is actually
        used.
        """
        try:
            if VocabularyIndex._lazy_pinyin_func is None:
                from pypinyin import lazy_pinyin

                VocabularyIndex._lazy_pinyin_func = lazy_pinyin
            return " ".join(VocabularyIndex._lazy_pinyin_func(text))
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Vocabulary I/O
    # ------------------------------------------------------------------

    def _read_vocabulary(self) -> List[VocabularyEntry]:
        """Read vocabulary.json and parse entries."""
        try:
            with open(self._vocab_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            raw_entries = data.get("entries", [])
            entries = []
            for raw in raw_entries:
                last_seen_str = raw.get("last_seen", "")
                ts = _parse_timestamp(last_seen_str) if last_seen_str else None
                entry = VocabularyEntry(
                    term=raw["term"],
                    category=raw.get("category", "other"),
                    variants=raw.get("variants", []),
                    context=raw.get("context", ""),
                    frequency=raw.get("frequency", 1),
                    last_seen=last_seen_str,
                    last_seen_ts=ts.timestamp() if ts else 0.0,
                )
                entries.append(entry)
            return entries
        except Exception as e:
            logger.warning("Failed to read vocabulary.json: %s", e)
            return []


def _read_raw_entries(data_dir: str = DEFAULT_DATA_DIR) -> List[dict]:
    """Read raw entry dicts from vocabulary.json.

    Shared I/O helper for :func:`load_hotwords` and :func:`get_vocab_entry_count`.
    Returns an empty list on any error.
    """
    vocab_path = os.path.join(os.path.expanduser(data_dir), "vocabulary.json")
    try:
        with open(vocab_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("entries", [])
    except Exception:
        return []


def load_hotwords(
    data_dir: str = DEFAULT_DATA_DIR,
    min_frequency: int = 1,
    max_count: Optional[int] = None,
) -> List[str]:
    """Load high-frequency vocabulary terms for ASR hotword injection.

    Thin wrapper around :func:`load_hotwords_detailed` that returns
    term strings only.

    Args:
        max_count: Maximum number of hotwords to return.  ``None`` means
            unlimited (return all entries that pass the frequency filter).
    """
    return [
        d.term for d in load_hotwords_detailed(data_dir, min_frequency, max_count)
    ]


def load_hotwords_detailed(
    data_dir: str = DEFAULT_DATA_DIR,
    min_frequency: int = 1,
    max_count: Optional[int] = None,
) -> List[HotwordDetail]:
    """Load high-frequency vocabulary terms with full metadata.

    Reads vocabulary.json, filters by *min_frequency*, scores by
    ``frequency + recency_bonus``, and returns the top *max_count*
    entries as :class:`HotwordDetail` with ``layer=LAYER_BASE``.

    Args:
        max_count: Maximum number of entries to return.  ``None`` means
            unlimited (return all entries that pass the frequency filter).
    """
    entries = _read_raw_entries(data_dir)
    filtered = [e for e in entries if e.get("frequency", 1) >= min_frequency]
    now = datetime.now(timezone.utc).timestamp()

    details: List[HotwordDetail] = []
    for e in filtered:
        if "term" not in e:
            continue
        ls = e.get("last_seen", "")
        ts = _parse_timestamp(ls) if ls else None
        ls_ts = ts.timestamp() if ts else 0.0
        bonus = _recency_bonus(ls_ts, now)
        freq = e.get("frequency", 1)
        details.append(HotwordDetail(
            term=e["term"],
            layer=LAYER_BASE,
            category=e.get("category", "other"),
            variants=e.get("variants", []),
            context=e.get("context", ""),
            frequency=freq,
            last_seen=ls,
            score=freq + bonus,
            recency_bonus=bonus,
        ))

    details.sort(key=lambda d: -d.score)
    return details[:max_count] if max_count is not None else details


def get_vocab_entry_count(data_dir: str = DEFAULT_DATA_DIR) -> int:
    """Read the number of entries in vocabulary.json without loading the index."""
    return len(_read_raw_entries(data_dir))


def _parse_timestamp(ts: str) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp string into a timezone-aware datetime.

    Returns None on failure.
    """
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _extract_context_entries(
    vocab_index: VocabularyIndex,
    conversation_history: "ConversationHistory",
    max_recent: int,
    max_age_hours: float,
) -> List[VocabularyEntry]:
    """Extract vocabulary entries matching recent conversation text.

    Shared helper for :func:`build_hotword_list_detailed`.
    """
    records = conversation_history.get_recent(n=max_recent)
    now_dt = datetime.now(timezone.utc)
    texts: List[str] = []
    for record in records:
        ts_str = record.get("timestamp", "")
        ts = _parse_timestamp(ts_str)
        if ts is not None:
            age_hours = (now_dt - ts).total_seconds() / 3600.0
            if age_hours > max_age_hours:
                continue
        final = record.get("final_text", "")
        if final:
            texts.append(final)

    if not texts:
        return []
    return vocab_index.find_terms_in_text(" ".join(texts))


def build_hotword_list(
    vocab_index: Optional[VocabularyIndex],
    conversation_history: Optional["ConversationHistory"],
    base_hotwords: Optional[List[str]],
    *,
    max_count: int = 10,
    max_recent: int = 15,
    max_age_hours: float = 2.0,
    correction_tracker: Optional[CorrectionTracker] = None,
    asr_model: Optional[str] = None,
    app_bundle_id: Optional[str] = None,
) -> Optional[List[str]]:
    """Build a two-layer hotword list for ASR injection.

    Thin wrapper around :func:`build_hotword_list_detailed` that returns
    term strings only.  Returns None when no hotwords are available.
    """
    # Convert base strings to HotwordDetail for the detailed builder
    base_detail: Optional[List[HotwordDetail]] = None
    if base_hotwords:
        base_detail = [HotwordDetail(term=t, layer=LAYER_BASE) for t in base_hotwords]

    details = build_hotword_list_detailed(
        vocab_index, conversation_history, base_detail,
        max_count=max_count, max_recent=max_recent, max_age_hours=max_age_hours,
        correction_tracker=correction_tracker,
        asr_model=asr_model,
        app_bundle_id=app_bundle_id,
    )
    return [d.term for d in details] if details else None


def build_hotword_list_detailed(
    vocab_index: Optional[VocabularyIndex],
    conversation_history: Optional["ConversationHistory"],
    base_hotwords_detail: Optional[List[HotwordDetail]],
    *,
    max_count: int = 10,
    max_recent: int = 15,
    max_age_hours: float = 2.0,
    correction_tracker: Optional[CorrectionTracker] = None,
    asr_model: Optional[str] = None,
    app_bundle_id: Optional[str] = None,
) -> List[HotwordDetail]:
    """Build a two-layer hotword list with full metadata for display.

    Layer 1 (context): terms from recent conversation history.
    Layer 2 (base): static high-frequency hotwords.
    Layer 3 (correction tracker): high-frequency corrected words from the
        correction tracker, merged in after base-layer terms (deduplicated).

    Context-layer terms are placed first; base-layer terms fill remaining
    slots (deduplicated). Correction tracker hotwords supplement remaining
    slots if provided.
    """
    context_details: List[HotwordDetail] = []
    now = datetime.now(timezone.utc).timestamp()

    if vocab_index is not None and vocab_index.is_loaded and conversation_history is not None:
        try:
            entries = _extract_context_entries(
                vocab_index, conversation_history, max_recent, max_age_hours,
            )
            for entry in entries:
                bonus = _recency_bonus(entry.last_seen_ts, now)
                context_details.append(HotwordDetail(
                    term=entry.term,
                    layer=LAYER_CONTEXT,
                    category=entry.category,
                    variants=entry.variants,
                    context=entry.context,
                    frequency=entry.frequency,
                    last_seen=entry.last_seen,
                    score=entry.frequency + bonus,
                    recency_bonus=bonus,
                ))
        except Exception as e:
            logger.warning("Failed to build context hotword details: %s", e)

    # Collect correction tracker hotwords if provided
    tracker_details: List[HotwordDetail] = []
    if correction_tracker is not None and asr_model:
        try:
            tracker_words = correction_tracker.get_asr_hotwords(
                asr_model=asr_model,
                app_bundle_id=app_bundle_id,
            )
            for word in tracker_words:
                tracker_details.append(HotwordDetail(term=word, layer=LAYER_BASE))
        except Exception as e:
            logger.warning("Failed to get correction tracker hotwords: %s", e)

    # Merge: context first, base fills remaining slots, tracker supplements (all deduplicated)
    seen: set[str] = set()
    result: List[HotwordDetail] = []
    for detail in [*context_details, *(base_hotwords_detail or ()), *tracker_details]:
        lower = detail.term.lower()
        if lower not in seen and len(result) < max_count:
            seen.add(lower)
            result.append(detail)

    return result
