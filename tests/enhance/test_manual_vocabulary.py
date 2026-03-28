"""Tests for ManualVocabularyStore."""

from __future__ import annotations

import threading

import pytest

from wenzi.enhance.manual_vocabulary import ManualVocabularyStore


@pytest.fixture
def store(tmp_path):
    """Create a ManualVocabularyStore backed by a temporary SQLite file."""
    path = str(tmp_path / "manual_vocabulary.db")
    return ManualVocabularyStore(path=path)


class TestAddRemove:
    def test_add_new_entry(self, store):
        entry = store.add("库伯尼特斯", "Kubernetes", "asr", app_bundle_id="com.test")
        assert entry.term == "Kubernetes"
        assert entry.variant == "库伯尼特斯"
        assert entry.source == "asr"
        assert entry.frequency == 1
        assert entry.app_bundle_id == "com.test"
        assert entry.first_seen != ""
        assert entry.last_updated != ""
        assert store.entry_count == 1

    def test_add_existing_increments_frequency(self, store):
        store.add("派森", "Python", "asr")
        entry = store.add("派森", "Python", "asr")
        assert entry.frequency == 2
        assert store.entry_count == 1

    def test_add_case_insensitive_key(self, store):
        store.add("foo", "Bar", "asr")
        entry = store.add("FOO", "BAR", "asr")
        # Should match and increment, not create a new entry
        assert entry.frequency == 2
        assert store.entry_count == 1

    def test_add_updates_metadata_on_existing(self, store):
        store.add("test", "Test", "asr", app_bundle_id="old.app", asr_model="old-model")
        entry = store.add("test", "Test", "asr", app_bundle_id="new.app", asr_model="new-model")
        assert entry.app_bundle_id == "new.app"
        assert entry.asr_model == "new-model"

    def test_remove_entry(self, store):
        store.add("派森", "Python", "asr")
        assert store.remove("派森", "Python") is True
        assert store.entry_count == 0

    def test_remove_nonexistent(self, store):
        assert store.remove("not", "exist") is False

    def test_remove_case_insensitive(self, store):
        store.add("foo", "Bar", "asr")
        assert store.remove("FOO", "BAR") is True
        assert store.entry_count == 0

    def test_contains(self, store):
        store.add("派森", "Python", "asr")
        assert store.contains("派森", "Python") is True
        assert store.contains("派森", "PYTHON") is True
        assert store.contains("nope", "Python") is False


class TestTwoPhaseHitTracking:
    def test_record_asr_phase_miss(self, store):
        store.add("派森", "Python", "asr")
        misses = store.record_asr_phase(
            "我在使用派森编程", asr_model="whisper",
        )
        assert len(misses) == 1
        assert misses[0].term == "Python"

    def test_record_asr_phase_hit(self, store):
        store.add("派森", "Python", "asr")
        misses = store.record_asr_phase(
            "I love Python", asr_model="whisper",
        )
        assert len(misses) == 0

    def test_record_asr_phase_no_match(self, store):
        store.add("派森", "Python", "asr")
        misses = store.record_asr_phase(
            "没有匹配", asr_model="whisper",
        )
        assert len(misses) == 0

    def test_record_llm_phase_hit(self, store):
        store.add("派森", "Python", "asr")
        misses = store.record_asr_phase("派森很好", asr_model="whisper")
        store.record_llm_phase(misses, "Python很好", llm_model="gpt-4o")
        # Verify stats
        stats = store.get_entry_stats("派森", "Python")
        llm_hits = [s for s in stats["llm"] if s["hit"] > 0]
        assert len(llm_hits) > 0

    def test_record_llm_phase_miss(self, store):
        store.add("派森", "Python", "asr")
        misses = store.record_asr_phase("派森很好", asr_model="whisper")
        store.record_llm_phase(misses, "派森仍然没改", llm_model="gpt-4o")
        stats = store.get_entry_stats("派森", "Python")
        llm_misses = [s for s in stats["llm"] if s["miss"] > 0]
        assert len(llm_misses) > 0

    def test_record_phases_context_keys(self, store):
        store.add("派森", "Python", "asr")
        misses = store.record_asr_phase(
            "派森编程", asr_model="whisper", app_bundle_id="com.apple.dt.Xcode",
        )
        store.record_llm_phase(
            misses, "Python编程", llm_model="gpt-4o", app_bundle_id="com.apple.dt.Xcode",
        )
        entry = store.get("派森", "Python")
        all_stats = store.db.get_stats(entry.id)
        context_keys = {s["context_key"] for s in all_stats}
        assert "asr:whisper" in context_keys
        assert "app:com.apple.dt.Xcode" in context_keys
        assert "llm:gpt-4o" in context_keys

    def test_asr_miss_ordering(self, store):
        """Entries with higher asr_miss should rank higher in hotwords."""
        store.add("a", "A", "asr")
        store.add("b", "B", "asr")
        # Give B more asr_miss
        for _ in range(3):
            store.record_asr_phase("b出错了", asr_model="whisper")
        store.record_asr_phase("a出错了", asr_model="whisper")
        terms = store.get_asr_hotwords(asr_model="whisper")
        assert terms[0].lower() == "b"

    def test_cold_start_fallback(self, store):
        """New model with no bucket data should fall back to global then recency."""
        store.add("a", "A", "asr")
        store.add("b", "B", "asr")
        # Record stats only for old_model
        for _ in range(3):
            store.record_asr_phase("a出错了", asr_model="old_model")
        # Query for new_model — should fall back
        terms = store.get_asr_hotwords(asr_model="new_model")
        assert len(terms) == 2
        # A should be first (has global stats)
        assert terms[0].lower() == "a"


class TestLegacyHitTracking:
    def test_record_hit(self, store):
        store.add("派森", "Python", "asr")
        store.record_hit("派森", "Python")
        entry = store.get("派森", "Python")
        # Hit is recorded in stats, not on the entry itself
        stats = store.db.get_stats(entry.id)
        assert any(s["metric"] == "llm_hit" and s["count"] == 1 for s in stats)

    def test_record_hits_batch(self, store):
        store.add("派森", "Python", "asr")
        store.add("库伯尼特斯", "Kubernetes", "asr")
        store.record_hits([
            ("派森", "Python"),
            ("库伯尼特斯", "Kubernetes"),
            ("nonexistent", "nope"),
        ])
        e1 = store.get("派森", "Python")
        e2 = store.get("库伯尼特斯", "Kubernetes")
        assert store.db.get_stats_summary(e1.id, "llm_hit") == 1
        assert store.db.get_stats_summary(e2.id, "llm_hit") == 1

    def test_find_hits_in_text(self, store):
        store.add("库伯尼特斯", "Kubernetes", "asr")
        store.add("派森", "Python", "asr")
        hits = store.find_hits_in_text("我在使用库伯尼特斯部署服务")
        assert len(hits) == 1
        assert hits[0].term == "Kubernetes"

    def test_find_hits_case_insensitive(self, store):
        store.add("python", "Python", "asr")
        hits = store.find_hits_in_text("I love PYTHON")
        assert len(hits) == 1

    def test_find_hits_no_match(self, store):
        store.add("派森", "Python", "asr")
        hits = store.find_hits_in_text("没有匹配的文本")
        assert len(hits) == 0


class TestQueryHelpers:
    def test_get_all_for_state(self, store):
        store.add("派森", "Python", "asr")
        store.add("foo", "Bar", "llm")
        state = store.get_all_for_state()
        assert len(state) == 2
        assert all("variant" in s and "term" in s for s in state)

    def test_get_asr_hotwords_all(self, store):
        store.add("派森", "Python", "asr")
        store.add("库伯尼特斯", "Kubernetes", "asr")
        terms = store.get_asr_hotwords()
        assert {t.lower() for t in terms} == {"python", "kubernetes"}

    def test_get_asr_hotwords_deduplicates(self, store):
        store.add("派森", "Python", "asr")
        store.add("python_err", "Python", "llm")  # Same term, different variant
        terms = store.get_asr_hotwords()
        assert len([t for t in terms if t.lower() == "python"]) == 1

    def test_get_llm_vocab(self, store):
        store.add("派森", "Python", "asr")
        entries = store.get_llm_vocab()
        assert len(entries) == 1

    def test_get_llm_vocab_max_entries_default(self, store):
        for i in range(8):
            store.add(f"var{i}", f"Term{i}", "llm")
        entries = store.get_llm_vocab()
        assert len(entries) == ManualVocabularyStore.MAX_LLM_ENTRIES

    def test_get_llm_vocab_max_entries_custom(self, store):
        for i in range(5):
            store.add(f"var{i}", f"Term{i}", "llm")
        entries = store.get_llm_vocab(max_entries=3)
        assert len(entries) == 3

    def test_get_llm_vocab_ranked_by_llm_miss(self, store):
        """Entries with more llm_miss should rank higher (need more help)."""
        store.add("派森", "Python", "asr")
        store.add("库伯尼特斯", "Kubernetes", "asr")
        # Simulate: "派森" gets 1 llm_miss, "库伯尼特斯" gets 3 llm_miss
        for _ in range(1):
            misses = store.record_asr_phase("派森编程", asr_model="test")
            store.record_llm_phase(misses, "still 派森", llm_model="test")
        for _ in range(3):
            misses = store.record_asr_phase("库伯尼特斯部署", asr_model="test")
            store.record_llm_phase(misses, "still 库伯尼特斯", llm_model="test")
        entries = store.get_llm_vocab(llm_model="test")
        assert len(entries) == 2
        assert entries[0].term == "Kubernetes"


class TestGetEntryStats:
    def test_empty_stats(self, store):
        store.add("派森", "Python", "asr")
        stats = store.get_entry_stats("派森", "Python")
        assert stats == {"asr": [], "llm": []}

    def test_stats_after_phases(self, store):
        store.add("派森", "Python", "asr")
        misses = store.record_asr_phase(
            "派森编程", asr_model="whisper", app_bundle_id="com.test",
        )
        store.record_llm_phase(
            misses, "Python编程", llm_model="gpt-4o", app_bundle_id="com.test",
        )
        stats = store.get_entry_stats("派森", "Python")
        assert len(stats["asr"]) > 0
        assert len(stats["llm"]) > 0

    def test_stats_nonexistent_entry(self, store):
        stats = store.get_entry_stats("nope", "nope")
        assert stats == {"asr": [], "llm": []}


class TestExportImportWithStats:
    def test_export_all_with_stats(self, store):
        store.add("派森", "Python", "asr")
        store.add("库伯尼特斯", "Kubernetes", "asr")
        misses = store.record_asr_phase("派森编程", asr_model="whisper")
        store.record_llm_phase(misses, "Python编程", llm_model="gpt-4o")
        exported = store.export_all_with_stats()
        assert len(exported) == 2
        python_entry = next(e for e in exported if e["term"] == "Python")
        assert "stats" in python_entry
        assert len(python_entry["stats"]) > 0
        for s in python_entry["stats"]:
            assert "metric" in s
            assert "context_key" in s
            assert "count" in s
            assert "last_time" in s
        # Entry without stats has empty stats list
        k8s_entry = next(e for e in exported if e["term"] == "Kubernetes")
        assert k8s_entry["stats"] == []

    def test_import_stats_by_id(self, store):
        entry = store.add("派森", "Python", "asr")
        store.import_stats_by_id(entry.id, [
            {"metric": "asr_miss", "context_key": "asr:whisper", "count": 5, "last_time": "2026-03-28T10:00:00"},
            {"metric": "llm_hit", "context_key": "llm:gpt-4o", "count": 3, "last_time": "2026-03-28T11:00:00"},
        ])
        stats = store.get_entry_stats("派森", "Python")
        assert len(stats["asr"]) > 0
        assert len(stats["llm"]) > 0

    def test_import_stats_by_id_invalid(self, store):
        store.import_stats_by_id(0, [{"metric": "asr_miss", "context_key": "x", "count": 1, "last_time": ""}])
        store.add("派森", "Python", "asr")
        store.import_stats_by_id(1, [])
        # Neither should raise or insert anything unexpected

    def test_roundtrip_export_import(self, tmp_path):
        """Export from one store, import into another, verify stats preserved."""
        path1 = str(tmp_path / "store1.db")
        store1 = ManualVocabularyStore(path=path1)
        store1.add("派森", "Python", "asr")
        misses = store1.record_asr_phase("派森很好", asr_model="whisper")
        store1.record_llm_phase(misses, "Python很好", llm_model="gpt-4o")
        exported = store1.export_all_with_stats()

        path2 = str(tmp_path / "store2.db")
        store2 = ManualVocabularyStore(path=path2)
        for entry_data in exported:
            entry = store2.add(
                variant=entry_data["variant"],
                term=entry_data["term"],
                source=entry_data.get("source", "user"),
                app_bundle_id=entry_data.get("app_bundle_id", ""),
                asr_model=entry_data.get("asr_model", ""),
                llm_model=entry_data.get("llm_model", ""),
                enhance_mode=entry_data.get("enhance_mode", ""),
            )
            store2.import_stats_by_id(entry.id, entry_data.get("stats", []))

        assert store2.entry_count == 1
        stats2 = store2.get_entry_stats("派森", "Python")
        stats1 = store1.get_entry_stats("派森", "Python")
        assert len(stats2["asr"]) == len(stats1["asr"])
        assert len(stats2["llm"]) == len(stats1["llm"])


class TestStatsIncludeApp:
    """Test stats_include_app configuration flag."""

    @pytest.fixture
    def store_no_app(self, tmp_path):
        return ManualVocabularyStore(
            path=str(tmp_path / "no_app.db"), stats_include_app=False,
        )

    @pytest.fixture
    def store_with_app(self, tmp_path):
        return ManualVocabularyStore(
            path=str(tmp_path / "with_app.db"), stats_include_app=True,
        )

    def test_get_entry_stats_excludes_app_buckets(self, store_no_app):
        store = store_no_app
        entry = store.add("派森", "Python", "asr")
        store.db.record_stats([
            (entry.id, "asr_miss", "asr:whisper"),
            (entry.id, "asr_miss", "app:com.example"),
        ])
        stats = store.get_entry_stats("派森", "Python")
        contexts = [b["context"] for b in stats["asr"]]
        assert "asr:whisper" in contexts
        assert "app:com.example" not in contexts

    def test_get_entry_stats_includes_app_buckets(self, store_with_app):
        store = store_with_app
        entry = store.add("派森", "Python", "asr")
        store.db.record_stats([
            (entry.id, "asr_miss", "asr:whisper"),
            (entry.id, "asr_miss", "app:com.example"),
        ])
        stats = store.get_entry_stats("派森", "Python")
        contexts = [b["context"] for b in stats["asr"]]
        assert "asr:whisper" in contexts
        assert "app:com.example" in contexts

    def test_summary_batch_excludes_app(self, store_no_app):
        store = store_no_app
        entry = store.add("派森", "Python", "asr")
        store.db.record_stats([
            (entry.id, "asr_miss", "asr:whisper"),
            (entry.id, "asr_miss", "app:com.example"),
        ])
        result = store.get_stats_summary_batch([entry.id], ["asr_miss"])
        assert result[(entry.id, "asr_miss")] == 1

    def test_summary_batch_includes_app(self, store_with_app):
        store = store_with_app
        entry = store.add("派森", "Python", "asr")
        store.db.record_stats([
            (entry.id, "asr_miss", "asr:whisper"),
            (entry.id, "asr_miss", "app:com.example"),
        ])
        result = store.get_stats_summary_batch([entry.id], ["asr_miss"])
        assert result[(entry.id, "asr_miss")] == 2

    def test_query_context_key_ignores_app(self, store_no_app):
        key = store_no_app._query_context_key("asr", None, "com.example")
        assert key == ""

    def test_query_context_key_uses_app(self, store_with_app):
        key = store_with_app._query_context_key("asr", None, "com.example")
        assert key == "app:com.example"


class TestNormalization:
    """Entries should be stripped of leading/trailing whitespace and punctuation."""

    def test_add_strips_whitespace(self, store):
        entry = store.add(" Claude ", "Cloud", "asr")
        assert entry.term == "Cloud"
        assert entry.variant == "Claude"

    def test_add_strips_punctuation(self, store):
        entry = store.add('"Claude"', "Cloud!", "asr")
        assert entry.term == "Cloud"
        assert entry.variant == "Claude"

    def test_add_strips_chinese_punctuation(self, store):
        entry = store.add("\u3001\u5e93\u4f2f\u5c3c\u7279\u65af\u3002", "\uff01Kubernetes\uff0c", "asr")
        assert entry.variant == "\u5e93\u4f2f\u5c3c\u7279\u65af"
        assert entry.term == "Kubernetes"

    def test_whitespace_variant_deduplicates(self, store):
        store.add("Cloud", "Claude", "user")
        entry = store.add(" Cloud ", " Claude ", "asr")
        assert entry.frequency == 2
        assert store.entry_count == 1

    def test_contains_normalizes(self, store):
        store.add("Cloud", "Claude", "asr")
        assert store.contains(" Cloud ", " Claude ") is True

    def test_remove_normalizes(self, store):
        store.add("Cloud", "Claude", "asr")
        assert store.remove(" Cloud ", " Claude ") is True
        assert store.entry_count == 0


class TestRemoveBatch:
    def test_remove_batch_basic(self, store):
        store.add("a", "A", "asr")
        store.add("b", "B", "asr")
        store.add("c", "C", "asr")
        assert store.entry_count == 3
        removed = store.remove_batch([("a", "A"), ("b", "B")])
        assert removed == 2
        assert store.entry_count == 1

    def test_remove_batch_nonexistent(self, store):
        removed = store.remove_batch([("x", "X")])
        assert removed == 0


class TestGet:
    def test_get_existing(self, store):
        store.add("Cloud", "Claude", "asr")
        entry = store.get("Cloud", "Claude")
        assert entry is not None
        assert entry.term == "Claude"

    def test_get_nonexistent(self, store):
        assert store.get("x", "X") is None

    def test_get_case_insensitive(self, store):
        store.add("Cloud", "Claude", "asr")
        entry = store.get("CLOUD", "CLAUDE")
        assert entry is not None


class TestThreadSafety:
    def test_concurrent_add_remove(self, store):
        """Concurrent add/remove should not raise."""
        errors = []

        def adder():
            try:
                for i in range(50):
                    store.add(f"v{i}", f"t{i}", "asr")
            except Exception as e:
                errors.append(e)

        def remover():
            try:
                for i in range(50):
                    store.remove(f"v{i}", f"t{i}")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=adder),
            threading.Thread(target=remover),
            threading.Thread(target=adder),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


class TestPersistence:
    def test_reopen_db(self, tmp_path):
        path = str(tmp_path / "manual_vocabulary.db")
        store1 = ManualVocabularyStore(path=path)
        store1.add("派森", "Python", "asr", app_bundle_id="com.test")
        store1.add("库伯尼特斯", "Kubernetes", "llm", asr_model="whisper")

        store2 = ManualVocabularyStore(path=path)
        assert store2.entry_count == 2
        assert store2.contains("派森", "Python")
        assert store2.contains("库伯尼特斯", "Kubernetes")
