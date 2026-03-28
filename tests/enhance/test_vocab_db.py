"""Tests for VocabDB SQLite storage layer."""

from __future__ import annotations

import threading

import pytest

from wenzi.enhance.vocab_db import VocabDB


@pytest.fixture
def db(tmp_path):
    """Create a VocabDB backed by a temporary SQLite file."""
    path = str(tmp_path / "vocab.db")
    d = VocabDB(path)
    yield d
    d.close()


class TestSchema:
    def test_tables_created(self, db):
        tables = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {r[0] for r in tables}
        assert "vocab_entry" in names
        assert "vocab_stats" in names

    def test_idempotent_ensure_tables(self, db):
        db._ensure_tables()  # should not raise


class TestCRUD:
    def test_add_new(self, db):
        entry = db.add("库伯尼特斯", "Kubernetes", "asr", app_bundle_id="com.test")
        assert entry["term"] == "Kubernetes"
        assert entry["variant"] == "库伯尼特斯"
        assert entry["source"] == "asr"
        assert entry["frequency"] == 1
        assert entry["app_bundle_id"] == "com.test"
        assert entry["first_seen"] != ""
        assert db.entry_count == 1

    def test_add_existing_increments_frequency(self, db):
        db.add("派森", "Python", "asr")
        entry = db.add("派森", "Python", "asr")
        assert entry["frequency"] == 2
        assert db.entry_count == 1

    def test_add_updates_metadata(self, db):
        db.add("test", "Test", "asr", app_bundle_id="old.app", asr_model="old")
        entry = db.add("test", "Test", "asr", app_bundle_id="new.app", asr_model="new")
        assert entry["app_bundle_id"] == "new.app"
        assert entry["asr_model"] == "new"

    def test_add_empty_metadata_does_not_overwrite(self, db):
        db.add("test", "Test", "asr", app_bundle_id="my.app")
        entry = db.add("test", "Test", "asr", app_bundle_id="")
        assert entry["app_bundle_id"] == "my.app"

    def test_remove(self, db):
        db.add("派森", "Python", "asr")
        assert db.remove("派森", "Python") is True
        assert db.entry_count == 0

    def test_remove_nonexistent(self, db):
        assert db.remove("nope", "nope") is False

    def test_remove_batch(self, db):
        db.add("a", "A", "asr")
        db.add("b", "B", "asr")
        db.add("c", "C", "asr")
        count = db.remove_batch([("a", "A"), ("b", "B"), ("x", "X")])
        assert count == 2
        assert db.entry_count == 1

    def test_get(self, db):
        db.add("派森", "Python", "asr")
        entry = db.get("派森", "Python")
        assert entry is not None
        assert entry["term"] == "Python"

    def test_get_nonexistent(self, db):
        assert db.get("x", "X") is None

    def test_get_by_id(self, db):
        db.add("派森", "Python", "asr")
        entry = db.get("派森", "Python")
        by_id = db.get_by_id(entry["id"])
        assert by_id["term"] == "Python"

    def test_contains(self, db):
        db.add("派森", "Python", "asr")
        assert db.contains("派森", "Python") is True
        assert db.contains("nope", "nope") is False

    def test_get_all(self, db):
        db.add("a", "A", "asr")
        db.add("b", "B", "asr")
        entries = db.get_all()
        assert len(entries) == 2

    def test_entry_count(self, db):
        assert db.entry_count == 0
        db.add("a", "A", "asr")
        assert db.entry_count == 1


class TestStats:
    def test_record_and_get_stats(self, db):
        entry = db.add("派森", "Python", "asr")
        eid = entry["id"]
        db.record_stats([
            (eid, "asr_miss", "asr:whisper-large-v3"),
            (eid, "asr_miss", "app:com.apple.dt.Xcode"),
        ])
        stats = db.get_stats(eid)
        assert len(stats) == 2
        assert all(s["count"] == 1 for s in stats)

    def test_record_stats_upsert(self, db):
        entry = db.add("派森", "Python", "asr")
        eid = entry["id"]
        db.record_stats([(eid, "asr_miss", "asr:whisper")])
        db.record_stats([(eid, "asr_miss", "asr:whisper")])
        stats = db.get_stats(eid)
        assert stats[0]["count"] == 2

    def test_get_stats_summary(self, db):
        entry = db.add("派森", "Python", "asr")
        eid = entry["id"]
        db.record_stats([
            (eid, "asr_miss", "asr:whisper"),
            (eid, "asr_miss", "asr:funasr"),
        ])
        db.record_stats([(eid, "asr_miss", "asr:whisper")])
        total = db.get_stats_summary(eid, "asr_miss")
        assert total == 3  # 2 + 1

    def test_get_stats_summary_no_data(self, db):
        entry = db.add("派森", "Python", "asr")
        assert db.get_stats_summary(entry["id"], "asr_miss") == 0

    def test_get_stats_summary_batch_with_context(self, db):
        e1 = db.add("a", "A", "asr")
        e2 = db.add("b", "B", "asr")
        db.record_stats([
            (e1["id"], "asr_miss", "asr:whisper"),
            (e1["id"], "asr_miss", "asr:funasr"),
            (e2["id"], "asr_miss", "asr:whisper"),
        ])
        result = db.get_stats_summary_batch(
            [e1["id"], e2["id"]], ["asr_miss"], context_key="asr:whisper",
        )
        assert result[(e1["id"], "asr_miss")] == 1
        assert result[(e2["id"], "asr_miss")] == 1

    def test_get_stats_summary_batch_context_empty_ids(self, db):
        result = db.get_stats_summary_batch([], ["asr_miss"], context_key="asr:x")
        assert result == {}

    def test_get_stats_summary_batch_context_no_match(self, db):
        e1 = db.add("a", "A", "asr")
        db.record_stats([(e1["id"], "asr_miss", "asr:whisper")])
        result = db.get_stats_summary_batch(
            [e1["id"]], ["asr_miss"], context_key="asr:nonexistent",
        )
        assert result == {}

    def test_cascade_delete(self, db):
        entry = db.add("派森", "Python", "asr")
        eid = entry["id"]
        db.record_stats([(eid, "asr_miss", "asr:whisper")])
        db.remove("派森", "Python")
        # Stats should be gone
        stats = db.get_stats(eid)
        assert stats == []


class TestRankedQueries:
    def _populate(self, db):
        """Create 3 entries with varying stats."""
        e1 = db.add("a", "A", "asr")
        e2 = db.add("b", "B", "asr")
        e3 = db.add("c", "C", "asr")
        # A: 5 asr_miss in whisper, 2 in funasr
        for _ in range(5):
            db.record_stats([(e1["id"], "asr_miss", "asr:whisper")])
        for _ in range(2):
            db.record_stats([(e1["id"], "asr_miss", "asr:funasr")])
        # B: 3 asr_miss in whisper
        for _ in range(3):
            db.record_stats([(e2["id"], "asr_miss", "asr:whisper")])
        # C: 1 asr_miss in funasr only
        db.record_stats([(e3["id"], "asr_miss", "asr:funasr")])
        return e1, e2, e3

    def test_top_by_metric(self, db):
        e1, e2, e3 = self._populate(db)
        top = db.top_by_metric("asr_miss", "asr:whisper", 10)
        assert len(top) == 2  # Only A and B have whisper stats
        assert top[0]["id"] == e1["id"]
        assert top[0]["stat_count"] == 5
        assert top[1]["id"] == e2["id"]

    def test_top_by_metric_limit(self, db):
        self._populate(db)
        top = db.top_by_metric("asr_miss", "asr:whisper", 1)
        assert len(top) == 1

    def test_top_by_metric_exclude(self, db):
        e1, e2, _ = self._populate(db)
        top = db.top_by_metric("asr_miss", "asr:whisper", 10, exclude_ids={e1["id"]})
        assert len(top) == 1
        assert top[0]["id"] == e2["id"]

    def test_top_by_metric_global(self, db):
        e1, e2, e3 = self._populate(db)
        top = db.top_by_metric_global("asr_miss", 10)
        assert top[0]["id"] == e1["id"]
        assert top[0]["stat_count"] == 7  # 5 + 2
        assert top[1]["id"] == e2["id"]
        assert top[1]["stat_count"] == 3

    def test_top_by_recency(self, db):
        db.add("a", "A", "asr")
        db.add("b", "B", "asr")
        top = db.top_by_recency(10)
        assert len(top) == 2
        assert all(d["stat_count"] == 0 for d in top)

    def test_top_with_fallback_all_tiers(self, db):
        """Test cold-start fallback: bucket → global → recency."""
        e1 = db.add("a", "A", "asr")
        e2 = db.add("b", "B", "asr")
        e3 = db.add("c", "C", "asr")
        e4 = db.add("d", "D", "asr")

        # e1 has stats in new_model bucket
        db.record_stats([(e1["id"], "asr_miss", "asr:new_model")])
        # e2 has stats only in old_model bucket (global only)
        for _ in range(3):
            db.record_stats([(e2["id"], "asr_miss", "asr:old_model")])
        # e3 and e4 have no stats at all (recency fallback)

        top = db.top_with_fallback("asr_miss", "asr:new_model", 4)
        assert len(top) == 4
        # Tier 1: e1 (has new_model stats)
        assert top[0]["id"] == e1["id"]
        # Tier 2: e2 (global stats)
        assert top[1]["id"] == e2["id"]
        # Tier 3: e3 and e4 (recency)
        tier3_ids = {top[2]["id"], top[3]["id"]}
        assert tier3_ids == {e3["id"], e4["id"]}

    def test_top_with_fallback_empty_context_key(self, db):
        """Empty context_key skips tier 1 and goes straight to global."""
        e1 = db.add("a", "A", "asr")
        db.record_stats([(e1["id"], "asr_miss", "asr:whisper")])
        top = db.top_with_fallback("asr_miss", "", 5)
        assert len(top) == 1
        assert top[0]["id"] == e1["id"]

    def test_top_with_fallback_respects_limit(self, db):
        for i in range(10):
            db.add(f"v{i}", f"T{i}", "asr")
        top = db.top_with_fallback("asr_miss", "", 3)
        assert len(top) == 3


class TestRenameEntry:
    def test_rename_preserves_stats(self, db):
        entry = db.add("a", "A", "asr")
        db.record_stats([(entry["id"], "asr_miss", "asr:whisper")])
        result = db.rename_entry(entry["id"], "b", "A")
        assert result is not None
        assert result["variant"] == "b"
        stats = db.get_stats(entry["id"])
        assert len(stats) == 1
        assert stats[0]["count"] == 1

    def test_rename_conflict_returns_none(self, db):
        db.add("a", "A", "asr")
        e2 = db.add("b", "B", "asr")
        # Try to rename B to match A — should fail gracefully
        result = db.rename_entry(e2["id"], "a", "A")
        assert result is None
        # Original entry unchanged
        assert db.contains("b", "B")


class TestConcurrency:
    def test_concurrent_writes(self, tmp_path):
        path = str(tmp_path / "vocab.db")
        db = VocabDB(path)
        errors = []

        def writer(prefix):
            try:
                for i in range(20):
                    db.add(f"{prefix}{i}", f"T{prefix}{i}", "asr")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=("a",)),
            threading.Thread(target=writer, args=("b",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        db.close()

        assert not errors
        db2 = VocabDB(path)
        assert db2.entry_count == 40
        db2.close()


class TestPersistence:
    def test_reopen_db(self, tmp_path):
        path = str(tmp_path / "vocab.db")
        db1 = VocabDB(path)
        entry = db1.add("派森", "Python", "asr")
        db1.record_stats([(entry["id"], "asr_miss", "asr:whisper")])
        db1.close()

        db2 = VocabDB(path)
        assert db2.entry_count == 1
        assert db2.contains("派森", "Python")
        stats = db2.get_stats(entry["id"])
        assert len(stats) == 1
        assert stats[0]["count"] == 1
        db2.close()
