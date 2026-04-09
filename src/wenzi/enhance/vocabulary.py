"""Vocabulary hotword building for ASR injection."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wenzi.enhance.vocab_db import METRIC_ASR_HIT, METRIC_ASR_MISS

if TYPE_CHECKING:
    from wenzi.enhance.manual_vocabulary import ManualVocabEntry, ManualVocabularyStore

logger = logging.getLogger(__name__)


@dataclass
class HotwordDetail:
    """A hotword entry with full metadata for display in the preview panel."""

    term: str
    variant: str = ""
    source: str = ""
    asr_miss_count: int = 0
    asr_hit_count: int = 0
    first_seen: str = ""


def build_hotword_list_detailed(
    *,
    max_count: int = 10,
    asr_model: str | None = None,
    app_bundle_id: str | None = None,
    manual_vocab_store: ManualVocabularyStore | None = None,
) -> list[HotwordDetail]:
    """Build a hotword list from manual vocabulary for ASR injection.

    Returns up to *max_count* :class:`HotwordDetail` entries sourced from
    the user-curated manual vocabulary store.
    """
    result: list[HotwordDetail] = []
    if manual_vocab_store is not None:
        try:
            manual_terms = manual_vocab_store.get_asr_hotwords(
                asr_model=asr_model, app_bundle_id=app_bundle_id,
            )
            seen: set[str] = set()
            all_entries = manual_vocab_store.get_all()
            entry_by_term: dict[str, ManualVocabEntry] = {}
            for e in all_entries:
                entry_by_term.setdefault(e.term.lower(), e)

            # Batch-fetch stats for all entries in one query
            entry_ids = [e.id for e in all_entries if e.id]
            stats_map = manual_vocab_store.get_stats_summary_batch(
                entry_ids, [METRIC_ASR_MISS, METRIC_ASR_HIT],
            )

            for term in manual_terms:
                if len(result) >= max_count:
                    break
                lower = term.lower()
                if lower in seen:
                    continue
                seen.add(lower)
                entry = entry_by_term.get(lower)
                if entry:
                    result.append(HotwordDetail(
                        term=term,
                        variant=entry.variant,
                        source=entry.source,
                        asr_miss_count=stats_map.get((entry.id, METRIC_ASR_MISS), 0),
                        asr_hit_count=stats_map.get((entry.id, METRIC_ASR_HIT), 0),
                        first_seen=entry.first_seen,
                    ))
                else:
                    result.append(HotwordDetail(term=term))
        except Exception as e:
            logger.warning("Failed to get manual vocab hotwords: %s", e)

    return result
