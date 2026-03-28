"""Tests for VocabController."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from wenzi.controllers.vocab_controller import VocabController, _app_display_name
from wenzi.ui.web_utils import time_range_cutoff as _time_range_cutoff
from wenzi.enhance.manual_vocabulary import ManualVocabularyStore


@pytest.fixture
def store(tmp_path):
    s = ManualVocabularyStore(path=str(tmp_path / "vocab.json"))
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

    def test_add_empty_variant_ignored(self, controller, store):
        controller.on_add_entry("", "Claude", "user")
        assert store.entry_count == 0

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
        entry = store.add(
            "Cloud", "Claude", source="asr",
            app_bundle_id="com.apple.dt.Xcode",
            asr_model="whisper",
        )
        # Simulate accumulated stats
        entry.frequency = 5
        entry.hit_count = 10
        entry.first_seen = "2025-01-01T00:00:00+00:00"
        entry.last_hit = "2025-03-01T00:00:00+00:00"
        store.save()

        controller.on_edit_entry("Cloud", "Claude", "Claud", "Claude")
        assert not store.contains("Cloud", "Claude")
        assert store.contains("Claud", "Claude")

        new_entries = store.get_all()
        assert len(new_entries) == 1
        new_entry = new_entries[0]
        assert new_entry.frequency == 5
        assert new_entry.hit_count == 10
        assert new_entry.first_seen == "2025-01-01T00:00:00+00:00"
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
        controller.on_sort("hit_count")
        assert controller._sort_column == "hit_count"
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
