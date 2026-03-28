"""Tests for ManualVocabularyStore."""

from __future__ import annotations

import json
import threading

import pytest

from wenzi.enhance.manual_vocabulary import ManualVocabularyStore


@pytest.fixture
def store(tmp_path):
    """Create a ManualVocabularyStore backed by a temporary file."""
    path = str(tmp_path / "manual_vocabulary.json")
    return ManualVocabularyStore(path=path)


class TestAddRemove:
    def test_add_new_entry(self, store):
        entry = store.add("库伯尼特斯", "Kubernetes", "asr", app_bundle_id="com.test")
        assert entry.term == "Kubernetes"
        assert entry.variant == "库伯尼特斯"
        assert entry.source == "asr"
        assert entry.frequency == 1
        assert entry.hit_count == 0
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


class TestHitTracking:
    def test_record_hit(self, store):
        store.add("派森", "Python", "asr")
        store.record_hit("派森", "Python")
        entries = store.get_all()
        assert entries[0].hit_count == 1
        assert entries[0].last_hit != ""

    def test_record_hit_increments(self, store):
        store.add("派森", "Python", "asr")
        store.record_hit("派森", "Python")
        store.record_hit("派森", "Python")
        entries = store.get_all()
        assert entries[0].hit_count == 2

    def test_record_hit_nonexistent_is_noop(self, store):
        store.record_hit("nope", "nope")  # Should not raise

    def test_record_hits_batch(self, store):
        store.add("派森", "Python", "asr")
        store.add("库伯尼特斯", "Kubernetes", "asr")
        store.add("no_hit", "NoHit", "asr")
        store.record_hits([
            ("派森", "Python"),
            ("库伯尼特斯", "Kubernetes"),
            ("nonexistent", "nope"),  # mixed: should be ignored
        ])
        entries = {e.term: e for e in store.get_all()}
        assert entries["Python"].hit_count == 1
        assert entries["Kubernetes"].hit_count == 1
        assert entries["NoHit"].hit_count == 0  # not in batch

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


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "manual_vocabulary.json")
        store1 = ManualVocabularyStore(path=path)
        store1.add("派森", "Python", "asr", app_bundle_id="com.test")
        store1.add("库伯尼特斯", "Kubernetes", "llm", asr_model="whisper")
        store1.record_hit("派森", "Python")

        store2 = ManualVocabularyStore(path=path)
        store2.load()
        assert store2.entry_count == 2
        assert store2.contains("派森", "Python")
        assert store2.contains("库伯尼特斯", "Kubernetes")
        entries = {e.term: e for e in store2.get_all()}
        assert entries["Python"].hit_count == 1
        assert entries["Kubernetes"].asr_model == "whisper"

    def test_load_missing_file(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        store = ManualVocabularyStore(path=path)
        store.load()
        assert store.entry_count == 0

    def test_load_corrupt_file(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w") as f:
            f.write("not json")
        store = ManualVocabularyStore(path=path)
        store.load()
        assert store.entry_count == 0

    def test_save_creates_directory(self, tmp_path):
        path = str(tmp_path / "sub" / "dir" / "manual_vocabulary.json")
        store = ManualVocabularyStore(path=path)
        store.add("test", "Test", "asr")
        assert json.loads(open(path).read())["version"] == 1


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
        assert set(terms) == {"Python", "Kubernetes"}

    def test_get_asr_hotwords_filtered_by_app(self, store):
        store.add("派森", "Python", "asr", app_bundle_id="com.xcode")
        store.add("库伯尼特斯", "Kubernetes", "asr", app_bundle_id="com.chrome")
        terms = store.get_asr_hotwords(app_bundle_id="com.xcode")
        # Matching entries come first
        assert terms[0] == "Python"
        assert len(terms) == 2  # All still included

    def test_get_asr_hotwords_filtered_by_model(self, store):
        store.add("派森", "Python", "asr", asr_model="whisper")
        store.add("库伯尼特斯", "Kubernetes", "asr", asr_model="funasr")
        terms = store.get_asr_hotwords(asr_model="whisper")
        assert terms[0] == "Python"

    def test_get_asr_hotwords_deduplicates(self, store):
        store.add("派森", "Python", "asr")
        store.add("python", "Python", "llm")  # Same term, different variant
        terms = store.get_asr_hotwords()
        assert terms.count("Python") == 1

    def test_get_llm_vocab(self, store):
        store.add("派森", "Python", "asr", app_bundle_id="com.xcode")
        store.add("库伯尼特斯", "Kubernetes", "asr", app_bundle_id="com.chrome")
        entries = store.get_llm_vocab(app_bundle_id="com.xcode")
        assert len(entries) == 2
        # Matching app first
        assert entries[0].term == "Python"

    def test_get_llm_vocab_no_filter(self, store):
        store.add("派森", "Python", "asr")
        entries = store.get_llm_vocab()
        assert len(entries) == 1

    def test_get_llm_vocab_llm_model_filter(self, store):
        """Entries matching llm_model are prioritized."""
        store.add("派森", "Python", "llm", llm_model="gpt-4o")
        store.add("库伯尼特斯", "Kubernetes", "llm", llm_model="claude-3")
        store.add("通用词", "General", "llm")  # no llm_model
        entries = store.get_llm_vocab(llm_model="gpt-4o")
        assert len(entries) == 3
        # gpt-4o match and no-model entry first, mismatched last
        assert entries[0].term == "Python"
        assert entries[1].term == "General"
        assert entries[2].term == "Kubernetes"

    def test_get_llm_vocab_llm_model_and_app(self, store):
        """Both llm_model and app_bundle_id filter together."""
        store.add("a", "A", "llm", llm_model="gpt-4o", app_bundle_id="com.app")
        store.add("b", "B", "llm", llm_model="claude-3", app_bundle_id="com.app")
        store.add("c", "C", "llm", llm_model="gpt-4o", app_bundle_id="com.other")
        store.add("d", "D", "llm")  # no model, no app
        entries = store.get_llm_vocab(
            llm_model="gpt-4o", app_bundle_id="com.app",
        )
        # A matches both; D matches (no constraints); B mismatches model; C mismatches app
        terms = [e.term for e in entries]
        assert terms[0] == "A"
        assert "D" in terms[:2]  # D is also a match (no stored model/app)
        assert set(terms[2:]) == {"B", "C"}

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

    def test_get_llm_vocab_max_entries_app_priority(self, store):
        """App-matching entries should be prioritized before truncation."""
        for i in range(4):
            store.add(f"other{i}", f"Other{i}", "llm", app_bundle_id="com.other")
        store.add("target", "Target", "llm", app_bundle_id="com.app")
        entries = store.get_llm_vocab(
            app_bundle_id="com.app", max_entries=3,
        )
        # The app-matching entry should be in the result
        terms = [e.term for e in entries]
        assert "Target" in terms
        assert len(entries) == 3


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

    def test_load_merges_pre_normalization_duplicates(self, tmp_path):
        """Entries that differ only by whitespace should merge on load."""
        path = str(tmp_path / "manual_vocabulary.json")
        data = {
            "version": 1,
            "entries": [
                {"term": "Claude", "variant": "Cloud", "frequency": 2, "hit_count": 3,
                 "first_seen": "2026-01-01T00:00:00+00:00", "last_updated": "2026-01-02T00:00:00+00:00",
                 "last_hit": "2026-01-02T00:00:00+00:00"},
                {"term": " Claude", "variant": "Cloud", "frequency": 1, "hit_count": 1,
                 "first_seen": "2026-01-03T00:00:00+00:00", "last_updated": "2026-01-03T00:00:00+00:00",
                 "last_hit": "2026-01-01T00:00:00+00:00"},
            ],
        }
        with open(path, "w") as f:
            json.dump(data, f)
        store = ManualVocabularyStore(path=path)
        store.load()
        assert store.entry_count == 1
        entry = store.get_all()[0]
        assert entry.term == "Claude"  # normalized
        assert entry.frequency == 3  # 2 + 1
        assert entry.hit_count == 4  # 3 + 1
        assert entry.last_updated == "2026-01-03T00:00:00+00:00"  # latest


class TestRemoveBatch:
    def test_remove_batch_single_save(self, store):
        store.add("a", "A", "asr")
        store.add("b", "B", "asr")
        store.add("c", "C", "asr")
        assert store.entry_count == 3
        removed = store.remove_batch([("a", "A"), ("b", "B")])
        assert removed == 2
        assert store.entry_count == 1

    def test_remove_batch_no_persist(self, store):
        store.add("a", "A", "asr")
        removed = store.remove_batch([("a", "A")], persist=False)
        assert removed == 1
        assert store.entry_count == 0

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


class TestAddPersist:
    def test_add_no_persist(self, store, tmp_path):
        store.add("a", "A", "asr", persist=False)
        assert store.entry_count == 1
        # File should not exist since persist=False
        path = tmp_path / "manual_vocabulary.json"
        assert not path.exists()

    def test_add_with_persist(self, store, tmp_path):
        store.add("a", "A", "asr", persist=True)
        assert store.entry_count == 1


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
