"""Tests for VocabController."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from wenzi.controllers.vocab_controller import VocabController, _app_display_name
from wenzi.enhance.manual_vocabulary import ManualVocabularyStore
from wenzi.ui.web_utils import time_range_cutoff as _time_range_cutoff


@pytest.fixture
def store(tmp_path):
    s = ManualVocabularyStore(path=str(tmp_path / "vocab.db"))
    return s


@pytest.fixture
def app(store):
    mock_app = MagicMock()
    mock_app._manual_vocab_store = store
    return mock_app


@pytest.fixture
def controller(app):
    ctrl = VocabController(app)
    ctrl._panel = MagicMock()
    ctrl._panel.is_visible = True
    return ctrl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_time_range_cutoff_all(self):
        assert _time_range_cutoff("all") is None

    def test_time_range_cutoff_7d(self):
        result = _time_range_cutoff("7d")
        assert result is not None
        assert "T" in result

    def test_time_range_cutoff_30d(self):
        result = _time_range_cutoff("30d")
        assert result is not None

    def test_time_range_cutoff_today(self):
        result = _time_range_cutoff("today")
        assert result is not None
        assert "T00:00:00" in result

    def test_app_display_name(self):
        assert _app_display_name("com.apple.dt.Xcode") == "Xcode"
        assert _app_display_name("com.google.Chrome") == "Chrome"
        assert _app_display_name("") == ""
        assert _app_display_name("Terminal") == "Terminal"


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


class TestCollectEntries:
    def test_empty_store(self, controller):
        entries = controller._collect_entries()
        assert entries == []

    def test_returns_entries(self, controller, store):
        store.add("Cloud", "Claude", source="asr")
        entries = controller._collect_entries()
        assert len(entries) == 1
        assert entries[0].variant == "Cloud"
        assert entries[0].term == "Claude"
        assert entries[0].source == "asr"


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


class TestApplyFilters:
    def test_no_filters(self, controller, store):
        store.add("Cloud", "Claude", source="asr")
        store.add("派森", "Python", source="user")
        controller._reload_data()
        assert len(controller._filtered_entries) == 2

    def test_search_filter(self, controller, store):
        store.add("Cloud", "Claude", source="asr")
        store.add("派森", "Python", source="user")
        controller._all_entries = controller._collect_entries()
        controller._search_text = "cloud"
        controller._apply_filters()
        assert len(controller._filtered_entries) == 1
        assert controller._filtered_entries[0].variant == "Cloud"

    def test_search_by_term(self, controller, store):
        store.add("Cloud", "Claude", source="asr")
        store.add("派森", "Python", source="user")
        controller._all_entries = controller._collect_entries()
        controller._search_text = "python"
        controller._apply_filters()
        assert len(controller._filtered_entries) == 1
        assert controller._filtered_entries[0].term == "Python"

    def test_time_filter(self, controller, store):
        store.add("old", "Old", source="asr")
        store.add("new", "New", source="asr")
        controller._all_entries = controller._collect_entries()
        for e in controller._all_entries:
            if e.variant == "old":
                e.last_updated = "2020-01-01T00:00:00+00:00"
            else:
                e.last_updated = "2099-01-01T00:00:00+00:00"
        controller._time_range = "7d"
        controller._apply_filters()
        assert len(controller._filtered_entries) == 1
        assert controller._filtered_entries[0].variant == "new"

    def test_tag_filter_source(self, controller, store):
        store.add("Cloud", "Claude", source="asr")
        store.add("派森", "Python", source="user")
        controller._all_entries = controller._collect_entries()
        controller._active_tags = {"asr"}
        controller._apply_filters()
        assert len(controller._filtered_entries) == 1
        assert controller._filtered_entries[0].source == "asr"

    def test_tag_filter_app(self, controller, store):
        store.add("a", "A", source="asr", app_bundle_id="com.apple.dt.Xcode")
        store.add("b", "B", source="asr", app_bundle_id="com.apple.Terminal")
        controller._all_entries = controller._collect_entries()
        controller._active_tags = {"Xcode"}
        controller._apply_filters()
        assert len(controller._filtered_entries) == 1
        assert controller._filtered_entries[0].variant == "a"

    def test_tag_filter_model(self, controller, store):
        store.add("a", "A", source="asr", asr_model="whisper-large")
        store.add("b", "B", source="asr", asr_model="funasr")
        controller._all_entries = controller._collect_entries()
        controller._active_tags = {"whisper-large"}
        controller._apply_filters()
        assert len(controller._filtered_entries) == 1
        assert controller._filtered_entries[0].variant == "a"

    def test_tag_filter_or_logic(self, controller, store):
        store.add("a", "A", source="asr")
        store.add("b", "B", source="llm")
        store.add("c", "C", source="user")
        controller._all_entries = controller._collect_entries()
        controller._active_tags = {"asr", "user"}
        controller._apply_filters()
        assert len(controller._filtered_entries) == 2

    def test_sort_default(self, controller, store):
        store.add("a", "A", source="asr")
        store.add("b", "B", source="asr")
        controller._all_entries = controller._collect_entries()
        # Default sort: last_updated descending — both have same timestamp from add()
        controller._apply_filters()
        assert len(controller._filtered_entries) == 2


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    def test_push_records_single_page(self, controller, store):
        store.add("Cloud", "Claude", source="asr")
        controller._reload_data()
        js_calls = [c[0][0] for c in controller._panel._eval_js.call_args_list]
        set_records_call = [c for c in js_calls if c.startswith("setRecords(")]
        assert len(set_records_call) >= 1

    def test_push_records_multi_page(self, controller, store):
        controller._page_size = 2
        for i in range(5):
            store.add(f"v{i}", f"t{i}", source="asr")
        controller._reload_data()
        js_calls = [c[0][0] for c in controller._panel._eval_js.call_args_list]
        set_records_call = [c for c in js_calls if c.startswith("setRecords(")]
        assert len(set_records_call) >= 1
        # Should show 2 records on first page
        assert "5,0,3," in set_records_call[-1] or "5,0," in set_records_call[-1]


# ---------------------------------------------------------------------------
# Tag options
# ---------------------------------------------------------------------------


class TestTagOptions:
    def test_push_tag_options(self, controller, store):
        store.add("a", "A", source="asr", app_bundle_id="com.apple.dt.Xcode")
        store.add("b", "B", source="user", asr_model="whisper")
        controller._all_entries = controller._collect_entries()
        controller._push_tag_options()
        js_calls = [c[0][0] for c in controller._panel._eval_js.call_args_list]
        tag_calls = [c for c in js_calls if c.startswith("setTagOptions(")]
        assert len(tag_calls) == 1


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCRUD:
    def test_add_entry(self, controller, store):
        controller.on_add_entry("Cloud", "Claude", "user")
        assert store.entry_count == 1
        assert store.contains("Cloud", "Claude")

    def test_add_entry_with_app_and_models(self, controller, store):
        controller.on_add_entry(
            "Cloud", "Claude", "asr",
            app_bundle_id="com.apple.dt.Xcode",
            asr_model="whisper-large-v3",
            llm_model="zai / glm-5",
        )
        assert store.entry_count == 1
        entry = store.get_all()[0]
        assert entry.app_bundle_id == "com.apple.dt.Xcode"
        assert entry.asr_model == "whisper-large-v3"
        assert entry.llm_model == "zai / glm-5"

    def test_add_empty_variant_uses_term(self, controller, store):
        controller.on_add_entry("", "Claude", "user")
        assert store.entry_count == 1
        entry = store.get("Claude", "Claude")
        assert entry is not None
        assert entry.variant == "Claude"
        assert entry.term == "Claude"

    def test_add_empty_term_ignored(self, controller, store):
        controller.on_add_entry("Cloud", "", "user")
        assert store.entry_count == 0

    def test_remove_entry(self, controller, store):
        store.add("Cloud", "Claude", source="asr")
        controller.on_remove_entry("Cloud", "Claude")
        assert store.entry_count == 0

    def test_batch_remove(self, controller, store):
        store.add("a", "A", source="asr")
        store.add("b", "B", source="asr")
        store.add("c", "C", source="asr")
        controller.on_batch_remove([
            {"variant": "a", "term": "A"},
            {"variant": "b", "term": "B"},
        ])
        assert store.entry_count == 1
        assert store.contains("c", "C")

    def test_edit_entry_preserves_metadata(self, controller, store):
        store.add(
            "Cloud", "Claude", source="asr",
            app_bundle_id="com.apple.dt.Xcode",
            asr_model="whisper",
        )

        controller.on_edit_entry("Cloud", "Claude", "Claud", "Claude")
        assert not store.contains("Cloud", "Claude")
        assert store.contains("Claud", "Claude")

        new_entries = store.get_all()
        assert len(new_entries) == 1
        new_entry = new_entries[0]
        assert new_entry.source == "asr"
        assert new_entry.app_bundle_id == "com.apple.dt.Xcode"
        assert new_entry.asr_model == "whisper"

    def test_edit_entry_empty_new_variant_ignored(self, controller, store):
        store.add("Cloud", "Claude", source="asr")
        controller.on_edit_entry("Cloud", "Claude", "", "Claude")
        assert store.contains("Cloud", "Claude")  # unchanged

    def test_edit_missing_old_entry(self, controller, store):
        """Editing a non-existent entry still creates the new entry."""
        controller.on_edit_entry("nonexist", "nonexist", "new", "New")
        assert store.contains("new", "New")

    def test_edit_field_source(self, controller, store):
        store.add("Cloud", "Claude", source="asr")
        controller.on_edit_field("Cloud", "Claude", {"source": "llm"})
        entry = store.get("Cloud", "Claude")
        assert entry.source == "llm"

    def test_edit_field_app_bundle_id(self, controller, store):
        store.add("Cloud", "Claude", source="user")
        controller.on_edit_field("Cloud", "Claude", {"app_bundle_id": "com.apple.dt.Xcode"})
        entry = store.get("Cloud", "Claude")
        assert entry.app_bundle_id == "com.apple.dt.Xcode"

    def test_edit_field_multiple(self, controller, store):
        store.add("Cloud", "Claude", source="asr")
        controller.on_edit_field("Cloud", "Claude", {
            "asr_model": "whisper-large-v3",
            "llm_model": "zai / glm-5",
        })
        entry = store.get("Cloud", "Claude")
        assert entry.asr_model == "whisper-large-v3"
        assert entry.llm_model == "zai / glm-5"

    def test_edit_field_disallowed_field_ignored(self, controller, store):
        store.add("Cloud", "Claude", source="asr")
        controller.on_edit_field("Cloud", "Claude", {"term": "hacked"})
        entry = store.get("Cloud", "Claude")
        assert entry.term == "Claude"  # unchanged

    def test_edit_field_nonexistent_entry_ignored(self, controller, store):
        controller.on_edit_field("nonexist", "nonexist", {"source": "llm"})
        assert store.entry_count == 0


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------


class TestSort:
    def test_sort_toggles_direction(self, controller):
        controller._sort_column = "variant"
        controller._sort_asc = False
        controller.on_sort("variant")
        assert controller._sort_asc is True
        controller.on_sort("variant")
        assert controller._sort_asc is False

    def test_sort_changes_column(self, controller):
        controller._sort_column = "variant"
        controller._sort_asc = True
        controller.on_sort("asr_miss")
        assert controller._sort_column == "asr_miss"
        assert controller._sort_asc is False  # new column defaults to descending


# ---------------------------------------------------------------------------
# Search and filter handlers
# ---------------------------------------------------------------------------


class TestSearchAndFilter:
    def test_on_search(self, controller, store):
        store.add("Cloud", "Claude", source="asr")
        controller._reload_data()
        controller._panel._eval_js.reset_mock()
        controller.on_search("cloud", "all")
        assert controller._search_text == "cloud"
        assert controller._time_range == "all"

    def test_on_toggle_tags(self, controller, store):
        store.add("a", "A", source="asr")
        controller._reload_data()
        controller._panel._eval_js.reset_mock()
        controller.on_toggle_tags(["asr"])
        assert controller._active_tags == {"asr"}

    def test_on_clear_filters(self, controller, store):
        controller._search_text = "test"
        controller._time_range = "7d"
        controller._active_tags = {"asr"}
        controller._sort_column = "variant"
        controller._sort_asc = True
        store.add("a", "A", source="asr")
        controller.on_clear_filters()
        assert controller._search_text == ""
        assert controller._time_range == "all"
        assert controller._active_tags == set()
        assert controller._sort_column == "last_updated"
        assert controller._sort_asc is False
        # Verify setSortState was pushed to JS
        js_calls = [c[0][0] for c in controller._panel._eval_js.call_args_list]
        assert any("setSortState(" in c for c in js_calls)

    def test_on_change_page(self, controller, store):
        controller._page_size = 1
        store.add("a", "A", source="asr")
        store.add("b", "B", source="asr")
        controller._reload_data()
        controller._panel._eval_js.reset_mock()
        controller.on_change_page(1)
        assert controller._page == 1


# ---------------------------------------------------------------------------
# Stats serialization
# ---------------------------------------------------------------------------


class TestStatsSerialization:
    def test_serialize_page_includes_stats(self, controller, store):
        store.add("Cloud", "Claude", source="asr")
        entries = store.get_all()
        entry = entries[0]
        # Record some stats
        store._db.record_stats([
            (entry.id, "asr_miss", "asr:whisper"),
            (entry.id, "asr_hit", "asr:whisper"),
            (entry.id, "llm_hit", "llm:gpt-4o"),
        ])
        store._db.record_stats([
            (entry.id, "asr_miss", "asr:whisper"),
        ])
        result = controller._serialize_page(entries)
        assert len(result) == 1
        d = result[0]
        assert d["asr_miss"] == 2
        assert d["asr_hit"] == 1
        assert d["llm_hit"] == 1
        assert d["llm_miss"] == 0

    def test_serialize_page_context_filtered(self, controller, store):
        store.add("Cloud", "Claude", source="asr", asr_model="whisper")
        entries = store.get_all()
        entry = entries[0]
        store._db.record_stats([
            (entry.id, "asr_miss", "asr:whisper"),
            (entry.id, "asr_miss", "asr:funasr"),
            (entry.id, "asr_miss", "asr:funasr"),
        ])
        # Without filter → global sum = 3
        result = controller._serialize_page(entries)
        assert result[0]["asr_miss"] == 3

        # With asr_model tag filter → only whisper bucket = 1
        controller._all_entries = entries
        controller._active_tags = {"whisper"}
        controller._apply_filters()
        result = controller._serialize_page(entries)
        assert result[0]["asr_miss"] == 1


# ---------------------------------------------------------------------------
# Stats-based sorting
# ---------------------------------------------------------------------------


class TestStatsSorting:
    def test_sort_by_asr_miss(self, controller, store):
        store.add("a", "A", source="asr")
        store.add("b", "B", source="asr")
        all_entries = store.get_all()
        e_a = next(e for e in all_entries if e.variant == "a")
        e_b = next(e for e in all_entries if e.variant == "b")
        # b has more asr_miss
        store._db.record_stats([(e_a.id, "asr_miss", "asr:w")])
        store._db.record_stats([
            (e_b.id, "asr_miss", "asr:w"),
            (e_b.id, "asr_miss", "asr:w"),
            (e_b.id, "asr_miss", "asr:w"),
        ])
        controller._sort_column = "asr_miss"
        controller._sort_asc = False
        controller._all_entries = store.get_all()
        controller._apply_filters()
        assert controller._filtered_entries[0].variant == "b"
        assert controller._filtered_entries[1].variant == "a"

    def test_sort_by_llm_hit(self, controller, store):
        store.add("a", "A", source="asr")
        store.add("b", "B", source="asr")
        all_entries = store.get_all()
        e_a = next(e for e in all_entries if e.variant == "a")
        e_b = next(e for e in all_entries if e.variant == "b")
        store._db.record_stats([
            (e_a.id, "llm_hit", "llm:gpt"),
            (e_a.id, "llm_hit", "llm:gpt"),
        ])
        store._db.record_stats([(e_b.id, "llm_hit", "llm:gpt")])
        controller._sort_column = "llm_hit"
        controller._sort_asc = False
        controller._all_entries = store.get_all()
        controller._apply_filters()
        assert controller._filtered_entries[0].variant == "a"


# ---------------------------------------------------------------------------
# Detail stats query
# ---------------------------------------------------------------------------


class TestDetailStats:
    def test_get_entry_stats(self, controller, store):
        store.add("Cloud", "Claude", source="asr")
        entry = store.get_all()[0]
        store._db.record_stats([
            (entry.id, "asr_miss", "asr:whisper"),
            (entry.id, "asr_hit", "asr:whisper"),
            (entry.id, "llm_hit", "llm:gpt-4o"),
        ])
        controller.on_get_entry_stats("Cloud", "Claude")
        js_calls = [c[0][0] for c in controller._panel._eval_js.call_args_list]
        stats_calls = [c for c in js_calls if c.startswith("setEntryStats(")]
        assert len(stats_calls) == 1

    def test_get_entry_stats_nonexistent(self, controller, store):
        controller.on_get_entry_stats("nonexist", "nonexist")
        js_calls = [c[0][0] for c in controller._panel._eval_js.call_args_list]
        stats_calls = [c for c in js_calls if c.startswith("setEntryStats(")]
        assert len(stats_calls) == 1
        # Should still return valid empty structure
        assert '"asr": []' in stats_calls[0]


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


def _import_entries_with_stats(store, raw_entries):
    """Simulate the controller's import logic (bypassing NSOpenPanel)."""
    for raw in raw_entries:
        entry = store.add(
            variant=raw["variant"], term=raw["term"],
            source=raw.get("source", "user"),
            app_bundle_id=raw.get("app_bundle_id", ""),
            asr_model=raw.get("asr_model", ""),
            llm_model=raw.get("llm_model", ""),
            enhance_mode=raw.get("enhance_mode", ""),
        )
        raw_stats = raw.get("stats", [])
        if raw_stats:
            store.import_stats_by_id(entry.id, raw_stats)


class TestExportImport:
    def test_export_includes_stats(self, controller, store, tmp_path):
        """Export should produce version 2 JSON with stats."""
        store.add("派森", "Python", "asr")
        entry = store.get_all()[0]
        store._db.record_stats([
            (entry.id, "asr_miss", "asr:whisper"),
            (entry.id, "llm_hit", "llm:gpt-4o"),
        ])
        out_path = str(tmp_path / "export.json")
        exported = store.export_all_with_stats()
        data = {"version": 2, "entries": exported}
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        with open(out_path, encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["version"] == 2
        assert len(loaded["entries"]) == 1
        entry_data = loaded["entries"][0]
        assert entry_data["term"] == "Python"
        assert len(entry_data["stats"]) == 2
        metrics = {s["metric"] for s in entry_data["stats"]}
        assert "asr_miss" in metrics
        assert "llm_hit" in metrics

    def test_import_v2_restores_stats(self, controller, store, tmp_path):
        """Import version 2 JSON should restore both entries and stats."""
        v2_data = {
            "version": 2,
            "entries": [
                {
                    "term": "Kubernetes",
                    "variant": "库伯尼特斯",
                    "source": "asr",
                    "frequency": 3,
                    "first_seen": "2026-03-20T10:00:00",
                    "last_updated": "2026-03-28T12:00:00",
                    "app_bundle_id": "com.apple.dt.Xcode",
                    "asr_model": "paraformer",
                    "llm_model": "gpt-4",
                    "enhance_mode": "translate",
                    "stats": [
                        {"metric": "asr_miss", "context_key": "asr:paraformer", "count": 5, "last_time": "2026-03-28T11:00:00"},
                        {"metric": "llm_hit", "context_key": "llm:gpt-4", "count": 3, "last_time": "2026-03-28T12:00:00"},
                    ],
                },
            ],
        }
        _import_entries_with_stats(store, v2_data["entries"])

        assert store.entry_count == 1
        entry = store.get("库伯尼特斯", "Kubernetes")
        assert entry is not None
        stats = store.get_entry_stats("库伯尼特斯", "Kubernetes")
        assert len(stats["asr"]) > 0
        assert len(stats["llm"]) > 0
        asr_bucket = stats["asr"][0]
        assert asr_bucket["miss"] == 5
        llm_bucket = stats["llm"][0]
        assert llm_bucket["hit"] == 3

    def test_import_v1_backward_compatible(self, controller, store, tmp_path):
        """Version 1 JSON (no stats) should import entries without errors."""
        v1_data = {
            "version": 1,
            "entries": [
                {
                    "term": "Python",
                    "variant": "派森",
                    "source": "asr",
                },
            ],
        }
        _import_entries_with_stats(store, v1_data["entries"])

        assert store.entry_count == 1
        stats = store.get_entry_stats("派森", "Python")
        assert stats == {"asr": [], "llm": []}

    def test_import_merges_stats_with_existing(self, controller, store, tmp_path):
        """Import stats for an existing entry should upsert (keep max)."""
        store.add("派森", "Python", "asr")
        entry = store.get_all()[0]
        for _ in range(10):
            store._db.record_stats([(entry.id, "asr_miss", "asr:whisper")])

        store.import_stats_by_id(entry.id, [
            {"metric": "asr_miss", "context_key": "asr:whisper", "count": 3, "last_time": "2026-01-01T00:00:00"},
        ])
        all_stats = store._db.get_stats(entry.id)
        asr_miss = next(s for s in all_stats if s["metric"] == "asr_miss")
        assert asr_miss["count"] == 10

    def test_roundtrip_export_import(self, controller, store, tmp_path):
        """Full roundtrip: add entries with stats, export, import into fresh store."""
        store.add("派森", "Python", "asr")
        store.add("库伯尼特斯", "Kubernetes", "asr")
        entry = store.get("派森", "Python")
        store._db.record_stats([
            (entry.id, "asr_miss", "asr:whisper"),
            (entry.id, "asr_miss", "asr:whisper"),
            (entry.id, "llm_hit", "llm:gpt-4o"),
        ])

        exported = store.export_all_with_stats()
        store2 = ManualVocabularyStore(path=str(tmp_path / "store2.db"))
        _import_entries_with_stats(store2, exported)

        assert store2.entry_count == 2
        stats = store2.get_entry_stats("派森", "Python")
        asr_buckets = stats["asr"]
        assert any(b["miss"] == 2 for b in asr_buckets)
        llm_buckets = stats["llm"]
        assert any(b["hit"] == 1 for b in llm_buckets)
        stats_k = store2.get_entry_stats("库伯尼特斯", "Kubernetes")
        assert stats_k == {"asr": [], "llm": []}
