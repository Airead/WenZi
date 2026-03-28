"""Tests for wenzi.enhance.vocabulary — hotword building from manual vocabulary."""

from __future__ import annotations

from unittest.mock import MagicMock

from wenzi.enhance.manual_vocabulary import ManualVocabEntry
from wenzi.enhance.vocabulary import (
    HotwordDetail,
    build_hotword_list_detailed,
)


class TestHotwordDetail:
    def test_defaults(self):
        d = HotwordDetail(term="API")
        assert d.term == "API"
        assert d.variant == ""
        assert d.source == ""
        assert d.hit_count == 0
        assert d.last_hit == ""
        assert d.first_seen == ""

    def test_full_fields(self):
        d = HotwordDetail(
            term="Kubernetes",
            variant="库伯尼特斯",
            source="asr",
            hit_count=5,
            last_hit="2024-01-01T00:00:00",
            first_seen="2023-06-15T00:00:00",
        )
        assert d.term == "Kubernetes"
        assert d.variant == "库伯尼特斯"
        assert d.source == "asr"
        assert d.hit_count == 5


class TestBuildHotwordListDetailed:
    def _make_mock_store(self, terms, entries=None):
        store = MagicMock()
        store.get_asr_hotwords.return_value = terms
        if entries is None:
            entries = [ManualVocabEntry(term=t, variant=t) for t in terms]
        store.get_all.return_value = entries
        return store

    def test_empty_without_store(self):
        result = build_hotword_list_detailed()
        assert result == []

    def test_with_none_store(self):
        result = build_hotword_list_detailed(manual_vocab_store=None)
        assert result == []

    def test_returns_manual_hotwords(self):
        entries = [
            ManualVocabEntry(term="Claude", variant="克劳德", source="asr", hit_count=3),
            ManualVocabEntry(term="Kubernetes", variant="库伯尼特斯", source="llm"),
        ]
        store = self._make_mock_store(
            ["Claude", "Kubernetes"], entries=entries,
        )
        result = build_hotword_list_detailed(manual_vocab_store=store)
        assert len(result) == 2
        assert result[0].term == "Claude"
        assert result[0].variant == "克劳德"
        assert result[0].source == "asr"
        assert result[0].hit_count == 3
        assert result[1].term == "Kubernetes"

    def test_respects_max_count(self):
        store = self._make_mock_store(["a", "b", "c", "d", "e"])
        result = build_hotword_list_detailed(max_count=3, manual_vocab_store=store)
        assert len(result) == 3

    def test_deduplicates_case_insensitive(self):
        store = self._make_mock_store(["Claude", "claude", "CLAUDE"])
        result = build_hotword_list_detailed(manual_vocab_store=store)
        assert len(result) == 1
        assert result[0].term == "Claude"

    def test_passes_asr_model_and_app_bundle_id(self):
        store = self._make_mock_store(["test"])
        build_hotword_list_detailed(
            asr_model="whisper",
            app_bundle_id="com.test",
            manual_vocab_store=store,
        )
        store.get_asr_hotwords.assert_called_once_with(
            asr_model="whisper", app_bundle_id="com.test",
        )

    def test_graceful_on_store_error(self):
        store = MagicMock()
        store.get_asr_hotwords.side_effect = RuntimeError("fail")
        result = build_hotword_list_detailed(manual_vocab_store=store)
        assert result == []

    def test_populates_fields_from_entry(self):
        entries = [
            ManualVocabEntry(
                term="API", variant="a p i", source="llm",
                hit_count=7, last_hit="2024-06-01T00:00:00",
                first_seen="2024-01-01T00:00:00",
            ),
        ]
        store = self._make_mock_store(["API"], entries=entries)
        result = build_hotword_list_detailed(manual_vocab_store=store)
        assert result[0].variant == "a p i"
        assert result[0].source == "llm"
        assert result[0].hit_count == 7
        assert result[0].last_hit == "2024-06-01T00:00:00"
        assert result[0].first_seen == "2024-01-01T00:00:00"
