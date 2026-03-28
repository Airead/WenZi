"""Vocabulary management controller — filtering, sorting, pagination, CRUD."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from wenzi.enhance.vocab_db import (
    CTX_APP,
    CTX_ASR,
    CTX_LLM,
    METRIC_ASR_HIT,
    METRIC_ASR_MISS,
    METRIC_LLM_HIT,
    METRIC_LLM_MISS,
)
from wenzi.ui.web_utils import time_range_cutoff as _time_range_cutoff

if TYPE_CHECKING:
    from wenzi.app import WenZiApp
    from wenzi.enhance.manual_vocabulary import ManualVocabEntry

logger = logging.getLogger(__name__)

_ALL_METRICS = [METRIC_ASR_MISS, METRIC_ASR_HIT, METRIC_LLM_HIT, METRIC_LLM_MISS]
_STATS_SORT_COLUMNS = frozenset({METRIC_ASR_MISS, METRIC_LLM_HIT})


_TAG_FIELDS = ("source", "app_bundle_id", "asr_model", "llm_model")
_TAG_GROUPS = (
    ("source", "source"),
    ("app_bundle_id", "app"),
    ("asr_model", "asr_model"),
    ("llm_model", "llm_model"),
)


def _app_display_name(bundle_id: str) -> str:
    """Extract short display name from a bundle ID."""
    if not bundle_id:
        return ""
    # "com.apple.dt.Xcode" → "Xcode"
    return bundle_id.rsplit(".", 1)[-1]


def _tag_display(field: str, value: str) -> str:
    """Return the display value for a tag field."""
    if field == "app_bundle_id":
        return _app_display_name(value)
    return value


class VocabController:
    """Business logic for the vocabulary management panel."""

    def __init__(self, app: WenZiApp) -> None:
        self._app = app
        self._panel: Any = None
        self._all_entries: list[ManualVocabEntry] = []
        self._base_filtered: list[ManualVocabEntry] = []
        self._filtered_entries: list[ManualVocabEntry] = []
        self._search_text: str = ""
        self._time_range: str = "all"
        self._active_tags: set[str] = set()
        self._sort_column: str = "last_updated"
        self._sort_asc: bool = False
        self._context_key: str = ""
        self._page: int = 0
        self._page_size: int = 18

    # ------------------------------------------------------------------
    # Public: open the panel
    # ------------------------------------------------------------------

    def on_open_vocab_manager(self, _=None) -> None:
        """Lazy-create panel, wire callbacks, show."""
        if self._panel is None:
            from wenzi.ui.vocab_manager_window import VocabManagerPanel

            self._panel = VocabManagerPanel()

        callbacks = {
            "on_page_ready": self._on_page_ready,
            "on_search": self.on_search,
            "on_toggle_tags": self.on_toggle_tags,
            "on_change_page": self.on_change_page,
            "on_sort": self.on_sort,
            "on_clear_filters": self.on_clear_filters,
            "on_add": self.on_add_entry,
            "on_remove": self.on_remove_entry,
            "on_batch_remove": self.on_batch_remove,
            "on_edit": self.on_edit_entry,
            "on_edit_field": self.on_edit_field,
            "on_get_entry_stats": self.on_get_entry_stats,
            "on_export": self.on_export,
            "on_import": self.on_import,
            "on_close": self._on_panel_closed,
        }
        self._panel.show(callbacks)

    def close_panel(self) -> None:
        """Close the panel if visible (called on app quit)."""
        if self._panel is not None and self._panel.is_visible:
            self._panel.close()
        self._clear_cached_entries()

    def _on_panel_closed(self) -> None:
        """Called when the panel is closed (via close button or programmatically)."""
        self._clear_cached_entries()

    def _clear_cached_entries(self) -> None:
        """Release cached entry lists to free memory."""
        self._all_entries = []
        self._base_filtered = []
        self._filtered_entries = []

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _on_page_ready(self) -> None:
        """Called when the JS page is ready for data."""
        self._reload_data()

    def _reload_data(self) -> None:
        """Reload all entries from store and push to panel."""
        self._all_entries = self._collect_entries()
        self._apply_filters()
        self._push_tag_options()
        self._push_records()

    def _collect_entries(self) -> list[ManualVocabEntry]:
        """Return a snapshot of all store entries."""
        return list(self._app._manual_vocab_store.get_all())

    # ------------------------------------------------------------------
    # Filtering, sorting, pagination
    # ------------------------------------------------------------------

    def _apply_filters(self) -> None:
        """Filter and sort _all_entries into _filtered_entries."""
        entries = self._all_entries

        if self._search_text:
            q = self._search_text.lower()
            entries = [
                e for e in entries
                if q in e.variant.lower() or q in e.term.lower()
            ]

        cutoff = _time_range_cutoff(self._time_range)
        if cutoff:
            entries = [e for e in entries if e.last_updated >= cutoff]

        # Snapshot before tag filtering — used by _push_tag_options()
        self._base_filtered = entries

        if self._active_tags:
            tags = self._active_tags
            entries = [
                e for e in entries
                if any(
                    _tag_display(f, getattr(e, f, "")) in tags
                    for f in _TAG_FIELDS
                )
            ]

        self._context_key = self._resolve_context_key()

        col = self._sort_column
        reverse = not self._sort_asc

        if col in _STATS_SORT_COLUMNS:
            # Stats-based sort: batch-query the metric counts
            stats_map = self._build_stats_sort_map(entries, col)
            def _sort_key(e: ManualVocabEntry) -> tuple:
                return (stats_map.get(e.id, 0), e.variant)
        else:
            def _sort_key(e: ManualVocabEntry) -> tuple:
                v = getattr(e, col, "")
                if v is None:
                    v = ""
                return (v, e.variant)

        entries.sort(key=_sort_key, reverse=reverse)

        self._filtered_entries = entries

    def _push_records(self) -> None:
        """Send current page of filtered entries to JS."""
        if self._panel is None:
            return
        filtered_count = len(self._filtered_entries)
        total_pages = max(1, (filtered_count + self._page_size - 1) // self._page_size)

        if self._page >= total_pages:
            self._page = total_pages - 1
        if self._page < 0:
            self._page = 0

        start = self._page * self._page_size
        end = start + self._page_size
        page_records = self._serialize_page(self._filtered_entries[start:end])

        total = len(self._all_entries)
        self._panel._eval_js(
            f"setRecords({json.dumps(page_records, ensure_ascii=False)},"
            f"{total},{self._page},{total_pages},{filtered_count})"
        )

    def _serialize_page(self, entries: list[ManualVocabEntry]) -> list[dict]:
        """Convert a page of entries to dicts for JS serialization."""
        entry_ids = [e.id for e in entries if e.id]
        stats = self._fetch_stats(entry_ids, _ALL_METRICS)

        result = []
        for e in entries:
            d = asdict(e)
            d["app_name"] = _app_display_name(d.get("app_bundle_id", ""))
            d["asr_miss"] = stats.get((e.id, METRIC_ASR_MISS), 0)
            d["asr_hit"] = stats.get((e.id, METRIC_ASR_HIT), 0)
            d["llm_hit"] = stats.get((e.id, METRIC_LLM_HIT), 0)
            d["llm_miss"] = stats.get((e.id, METRIC_LLM_MISS), 0)
            result.append(d)
        return result

    def _fetch_stats(
        self,
        entry_ids: list[int],
        metrics: list[str],
    ) -> dict[tuple[int, str], int]:
        """Batch-fetch stats, scoped to the active tag filter context."""
        if not entry_ids:
            return {}
        return self._app._manual_vocab_store.get_stats_summary_batch(
            entry_ids, metrics, self._context_key,
        )

    def _resolve_context_key(self) -> str:
        """Derive a context_key from the currently active tag filters.

        Returns a single context_key string (e.g. ``"asr:whisper-large-v3"``)
        when a model/app tag is active, or ``""`` for global summary.
        """
        if not self._active_tags:
            return ""
        for e in self._all_entries:
            if e.asr_model and e.asr_model in self._active_tags:
                return f"{CTX_ASR}:{e.asr_model}"
            if e.llm_model and e.llm_model in self._active_tags:
                return f"{CTX_LLM}:{e.llm_model}"
            if e.app_bundle_id and _app_display_name(e.app_bundle_id) in self._active_tags:
                return f"{CTX_APP}:{e.app_bundle_id}"
        return ""

    def _push_tag_options(self) -> None:
        """Send available tag options with counts to JS."""
        if self._panel is None:
            return

        group_counts: dict[str, dict[str, int]] = {g: {} for _, g in _TAG_GROUPS}
        for e in self._base_filtered:
            for field, group in _TAG_GROUPS:
                val = _tag_display(field, getattr(e, field, ""))
                if val:
                    counts = group_counts[group]
                    counts[val] = counts.get(val, 0) + 1

        tags: list[dict[str, Any]] = []
        for _, group in _TAG_GROUPS:
            for name in sorted(group_counts[group]):
                tags.append({"name": name, "count": group_counts[group][name], "group": group})

        self._panel._eval_js(f"setTagOptions({json.dumps(tags)})")

        # Push unique raw values for the add-row selects
        apps: dict[str, str] = {}  # bundle_id → display_name
        asr_models: set[str] = set()
        llm_models: set[str] = set()
        for e in self._all_entries:
            if e.app_bundle_id:
                apps[e.app_bundle_id] = _app_display_name(e.app_bundle_id)
            if e.asr_model:
                asr_models.add(e.asr_model)
            if e.llm_model:
                llm_models.add(e.llm_model)

        add_opts = {
            "apps": [{"id": k, "name": v} for k, v in sorted(apps.items(), key=lambda x: x[1].lower())],
            "asr_models": sorted(asr_models),
            "llm_models": sorted(llm_models),
        }
        self._panel._eval_js(f"setAddOptions({json.dumps(add_opts)})")

    def _build_stats_sort_map(
        self, entries: list[ManualVocabEntry], metric: str,
    ) -> dict[int, int]:
        """Build entry_id → count map for stats-based sorting."""
        entry_ids = [e.id for e in entries if e.id]
        stats = self._fetch_stats(entry_ids, [metric])
        return {eid: count for (eid, _), count in stats.items()}

    # ------------------------------------------------------------------
    # JS message handlers
    # ------------------------------------------------------------------

    def on_get_entry_stats(self, variant: str, term: str) -> None:
        """Query detailed bucketed stats for an entry and push to JS."""
        store = self._app._manual_vocab_store
        stats = store.get_entry_stats(variant, term)
        if self._panel is not None:
            self._panel._eval_js(
                f"setEntryStats({json.dumps(variant)},"
                f"{json.dumps(term)},"
                f"{json.dumps(stats, ensure_ascii=False)})"
            )

    def on_search(self, text: str, time_range: str) -> None:
        self._search_text = text
        self._time_range = time_range
        self._active_tags = set()
        self._page = 0
        self._apply_filters()
        self._push_tag_options()
        self._push_records()

    def on_toggle_tags(self, tags: list[str]) -> None:
        self._active_tags = set(tags)
        self._page = 0
        self._apply_filters()
        self._push_records()

    def on_change_page(self, page: int) -> None:
        self._page = page
        self._push_records()

    def on_sort(self, column: str) -> None:
        if column == self._sort_column:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_column = column
            self._sort_asc = False
        self._page = 0
        self._apply_filters()
        self._push_records()
        # Push sort state to JS for indicator update
        if self._panel is not None:
            self._panel._eval_js(
                f"setSortState({json.dumps(self._sort_column)},{json.dumps(self._sort_asc)})"
            )

    def on_clear_filters(self) -> None:
        self._search_text = ""
        self._time_range = "all"
        self._active_tags = set()
        self._sort_column = "last_updated"
        self._sort_asc = False
        self._page = 0
        if self._panel is not None:
            self._panel._eval_js("resetFilters()")
            self._panel._eval_js(
                f"setSortState({json.dumps(self._sort_column)},{json.dumps(self._sort_asc)})"
            )
        self._reload_data()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def on_add_entry(
        self,
        variant: str,
        term: str,
        source: str,
        *,
        app_bundle_id: str = "",
        asr_model: str = "",
        llm_model: str = "",
    ) -> None:
        """Add a new vocabulary entry."""
        variant = variant.strip()
        term = term.strip()
        if not variant or not term:
            return
        from wenzi.enhance.manual_vocabulary import SOURCE_USER

        self._app._manual_vocab_store.add(
            variant=variant,
            term=term,
            source=source or SOURCE_USER,
            app_bundle_id=app_bundle_id,
            asr_model=asr_model,
            llm_model=llm_model,
        )
        self._reload_data()

    def on_remove_entry(self, variant: str, term: str) -> None:
        """Remove a single vocabulary entry."""
        self._app._manual_vocab_store.remove(variant, term)
        self._reload_data()

    def on_batch_remove(self, entries: list[dict]) -> None:
        """Remove multiple vocabulary entries."""
        pairs = [(e.get("variant", ""), e.get("term", "")) for e in entries]
        self._app._manual_vocab_store.remove_batch(pairs)
        self._reload_data()

    def on_edit_entry(
        self,
        old_variant: str,
        old_term: str,
        new_variant: str,
        new_term: str,
    ) -> None:
        """Edit a vocabulary entry (change variant or term)."""
        new_variant = new_variant.strip()
        new_term = new_term.strip()
        if not new_variant or not new_term:
            return

        store = self._app._manual_vocab_store
        old_entry = store.get(old_variant, old_term)

        if old_entry is not None and old_entry.id:
            store.rename_entry(old_entry.id, new_variant, new_term)
        else:
            store.remove_batch([(old_variant, old_term)])
            from wenzi.enhance.manual_vocabulary import SOURCE_USER

            store.add(variant=new_variant, term=new_term, source=SOURCE_USER)

        self._reload_data()

    _EDITABLE_FIELDS = frozenset({"source", "app_bundle_id", "asr_model", "llm_model"})

    def on_edit_field(
        self, variant: str, term: str, fields: dict,
    ) -> None:
        """Update one or more fields on an existing entry."""
        store = self._app._manual_vocab_store
        entry = store.get(variant, term)
        if entry is None:
            return
        updates = {f: v for f, v in fields.items() if f in self._EDITABLE_FIELDS}
        if updates:
            store.update_fields(entry.id, updates)
            self._reload_data()

    # ------------------------------------------------------------------
    # Import / Export
    # ------------------------------------------------------------------

    def on_export(self) -> None:
        """Export vocabulary to JSON via NSSavePanel."""
        try:
            from AppKit import NSSavePanel

            panel = NSSavePanel.savePanel()
            panel.setTitle_("Export Vocabulary")
            panel.setNameFieldStringValue_("vocabulary.json")
            panel.setAllowedContentTypes_([])
            result = panel.runModal()
            if result != 1:  # NSModalResponseOK
                return
            path = str(panel.URL().path())

            entries = self._app._manual_vocab_store.get_all()
            data = {
                "version": 1,
                "entries": [asdict(e) for e in entries],
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info("Exported %d vocabulary entries to %s", len(entries), path)
        except Exception as exc:
            logger.error("Failed to export vocabulary: %s", exc, exc_info=True)

    def on_import(self) -> None:
        """Import vocabulary from JSON via NSOpenPanel."""
        try:
            from AppKit import NSOpenPanel

            panel = NSOpenPanel.openPanel()
            panel.setTitle_("Import Vocabulary")
            panel.setCanChooseFiles_(True)
            panel.setCanChooseDirectories_(False)
            panel.setAllowsMultipleSelection_(False)
            result = panel.runModal()
            if result != 1:  # NSModalResponseOK
                return
            path = str(panel.URLs()[0].path())

            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            raw_entries = data.get("entries", [])
            store = self._app._manual_vocab_store
            new_count = 0
            update_count = 0
            for raw in raw_entries:
                variant = raw.get("variant", "").strip()
                term = raw.get("term", "").strip()
                if not variant or not term:
                    continue
                existed = store.get(variant, term) is not None
                store.add(
                    variant=variant,
                    term=term,
                    source=raw.get("source", "user"),
                    app_bundle_id=raw.get("app_bundle_id", ""),
                    asr_model=raw.get("asr_model", ""),
                    llm_model=raw.get("llm_model", ""),
                    enhance_mode=raw.get("enhance_mode", ""),
                )
                if existed:
                    update_count += 1
                else:
                    new_count += 1

            logger.info(
                "Imported vocabulary: %d new, %d updated from %s",
                new_count, update_count, os.path.basename(path),
            )
            self._reload_data()
        except Exception as exc:
            logger.error("Failed to import vocabulary: %s", exc, exc_info=True)
