"""Tests for async source search support."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

from wenzi.scripting.api.chooser import ChooserAPI
from wenzi.scripting.sources import ChooserItem, ChooserSource


def _wait_for(predicate, timeout=5.0, interval=0.05):
    """Poll until predicate returns True or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# ChooserSource dataclass — new fields
# ---------------------------------------------------------------------------


class TestChooserSourceAsyncFields:
    def test_defaults(self):
        src = ChooserSource(name="test")
        assert src.is_async is False
        assert src.search_timeout is None
        assert src.debounce_delay is None

    def test_custom_values(self):
        src = ChooserSource(name="test", is_async=True, search_timeout=3.0, debounce_delay=0.2)
        assert src.is_async is True
        assert src.search_timeout == 3.0
        assert src.debounce_delay == 0.2


# ---------------------------------------------------------------------------
# Source decorator — async detection
# ---------------------------------------------------------------------------


class TestSourceDecoratorAsyncDetection:
    def test_sync_source_not_async(self):
        api = ChooserAPI()

        @api.source("sync-test", prefix="st")
        def search_sync(query):
            return [{"title": f"sync-{query}"}]

        src = api.panel._sources["sync-test"]
        assert src.is_async is False

    def test_async_source_detected(self):
        api = ChooserAPI()

        @api.source("async-test", prefix="at", search_timeout=2.0)
        async def search_async(query):
            return [{"title": f"async-{query}"}]

        src = api.panel._sources["async-test"]
        assert src.is_async is True
        assert src.search_timeout == 2.0
        assert asyncio.iscoroutinefunction(src.search)

    def test_async_source_default_timeout(self):
        api = ChooserAPI()

        @api.source("async-default")
        async def search_async(query):
            return []

        src = api.panel._sources["async-default"]
        assert src.search_timeout is None
        assert src.debounce_delay is None


# ---------------------------------------------------------------------------
# ChooserPanel — _do_search sync/async split
# ---------------------------------------------------------------------------


class TestDoSearchSyncAsyncSplit:
    """Test that _do_search partitions sources correctly."""

    def test_sync_source_works_unchanged(self, chooser_panel):
        chooser_panel.register_source(
            ChooserSource(
                name="apps",
                search=lambda q: [ChooserItem(title=f"App-{q}")],
            )
        )
        chooser_panel._do_search("hello")
        assert len(chooser_panel._current_items) == 1
        assert chooser_panel._current_items[0].title == "App-hello"

    def test_generation_counter_increments(self, chooser_panel):
        chooser_panel.register_source(
            ChooserSource(
                name="apps",
                search=lambda q: [ChooserItem(title="a")],
            )
        )
        chooser_panel._do_search("a")
        gen1 = chooser_panel._search_generation
        chooser_panel._do_search("b")
        gen2 = chooser_panel._search_generation
        assert gen2 == gen1 + 1

    def test_async_source_triggers_launch(self, chooser_panel):
        async def async_search(query):
            return [ChooserItem(title=f"async-{query}")]

        chooser_panel.register_source(
            ChooserSource(
                name="async-src",
                search=async_search,
                is_async=True,
                debounce_delay=0,  # Disable debounce for this test
            )
        )

        with patch.object(chooser_panel, "_launch_async_search") as mock_launch:
            chooser_panel._do_search("test")
            mock_launch.assert_called_once()
            args = mock_launch.call_args
            assert args[0][0].name == "async-src"
            assert args[0][1] == "test"

    def test_sync_results_immediate_async_deferred(self, chooser_panel):
        """Sync sources produce immediate items; async sources are deferred."""
        chooser_panel.register_source(
            ChooserSource(
                name="sync-src",
                search=lambda q: [ChooserItem(title="sync")],
            )
        )

        async def async_search(query):
            return [ChooserItem(title="async")]

        chooser_panel.register_source(
            ChooserSource(
                name="async-src",
                search=async_search,
                is_async=True,
            )
        )

        with patch.object(chooser_panel, "_launch_async_search"):
            chooser_panel._do_search("test")

        # Sync items are immediately available
        assert len(chooser_panel._current_items) == 1
        assert chooser_panel._current_items[0].title == "sync"

    def test_loading_indicator_set_for_async(self, chooser_panel):
        async def async_search(query):
            return []

        chooser_panel.register_source(
            ChooserSource(
                name="async-src",
                search=async_search,
                is_async=True,
            )
        )

        with patch.object(chooser_panel, "_launch_async_search"):
            chooser_panel._do_search("test")

        js_calls = [str(c) for c in chooser_panel._eval_js.call_args_list]
        assert any("setLoading(true)" in c for c in js_calls)

    def test_no_loading_for_sync_only(self, chooser_panel):
        chooser_panel.register_source(
            ChooserSource(
                name="sync-src",
                search=lambda q: [ChooserItem(title="sync")],
            )
        )
        chooser_panel._do_search("test")

        # Loading was never shown — _set_loading(False) is a no-op
        assert chooser_panel._loading_visible is False
        js_calls = [str(c) for c in chooser_panel._eval_js.call_args_list]
        assert not any("setLoading(true)" in c for c in js_calls)

    def test_empty_query_clears_loading(self, chooser_panel):
        chooser_panel._pending_async_count = 2  # simulate in-flight
        chooser_panel._loading_visible = True  # simulate loading was shown
        chooser_panel._do_search("")

        assert chooser_panel._pending_async_count == 0
        assert chooser_panel._loading_visible is False
        js_calls = [str(c) for c in chooser_panel._eval_js.call_args_list]
        assert any("setLoading(false)" in c for c in js_calls)

    def test_prefix_async_source(self, chooser_panel):
        """Prefix-activated async source should launch async search."""
        async def async_search(query):
            return [ChooserItem(title=f"prefix-{query}")]

        chooser_panel.register_source(
            ChooserSource(
                name="api-src",
                prefix="api",
                search=async_search,
                is_async=True,
                debounce_delay=0,  # Disable debounce for this test
            )
        )

        with patch.object(chooser_panel, "_launch_async_search") as mock_launch:
            chooser_panel._do_search("api hello")
            mock_launch.assert_called_once()
            # Verify prefix was stripped
            assert mock_launch.call_args[0][1] == "hello"

        # No sync items
        assert len(chooser_panel._current_items) == 0

    def test_prefix_sync_source_unchanged(self, chooser_panel):
        chooser_panel.register_source(
            ChooserSource(
                name="cb",
                prefix="cb",
                search=lambda q: [ChooserItem(title=f"clip-{q}")],
            )
        )
        chooser_panel._do_search("cb test")
        assert len(chooser_panel._current_items) == 1
        assert chooser_panel._current_items[0].title == "clip-test"


# ---------------------------------------------------------------------------
# _merge_async_results
# ---------------------------------------------------------------------------


class TestMergeAsyncResults:
    def test_merge_appends_items(self, chooser_panel):
        chooser_panel._search_generation = 1
        chooser_panel._pending_async_count = 1
        chooser_panel._current_items = [ChooserItem(title="sync")]

        src = ChooserSource(name="async-src", is_async=True)
        chooser_panel._merge_async_results(
            src,
            [ChooserItem(title="async")],
            generation=1,
        )
        assert len(chooser_panel._current_items) == 2
        assert chooser_panel._current_items[1].title == "async"

    def test_stale_generation_discarded(self, chooser_panel):
        chooser_panel._search_generation = 5
        chooser_panel._pending_async_count = 1
        chooser_panel._current_items = [ChooserItem(title="sync")]

        src = ChooserSource(name="async-src", is_async=True)
        chooser_panel._merge_async_results(
            src,
            [ChooserItem(title="stale")],
            generation=3,  # old generation
        )
        # Items NOT merged
        assert len(chooser_panel._current_items) == 1
        assert chooser_panel._current_items[0].title == "sync"

    def test_loading_cleared_when_last_async_completes(self, chooser_panel):
        chooser_panel._search_generation = 1
        chooser_panel._pending_async_count = 1
        chooser_panel._loading_visible = True  # loading was turned on

        src = ChooserSource(name="async-src", is_async=True)
        chooser_panel._merge_async_results(src, [], generation=1)

        assert chooser_panel._pending_async_count == 0
        assert chooser_panel._loading_visible is False
        js_calls = [str(c) for c in chooser_panel._eval_js.call_args_list]
        assert any("setLoading(false)" in c for c in js_calls)

    def test_loading_not_cleared_while_others_pending(self, chooser_panel):
        chooser_panel._search_generation = 1
        chooser_panel._pending_async_count = 2

        src = ChooserSource(name="async-src", is_async=True)
        chooser_panel._merge_async_results(
            src,
            [ChooserItem(title="first")],
            generation=1,
        )

        assert chooser_panel._pending_async_count == 1
        js_calls = [str(c) for c in chooser_panel._eval_js.call_args_list]
        assert not any("setLoading(false)" in c for c in js_calls)

    def test_respects_max_total_results(self, chooser_panel):
        chooser_panel._search_generation = 1
        chooser_panel._pending_async_count = 1
        # Fill up to near the limit
        chooser_panel._current_items = [ChooserItem(title=f"item-{i}") for i in range(chooser_panel._MAX_TOTAL_RESULTS - 1)]

        src = ChooserSource(name="async-src", is_async=True)
        chooser_panel._merge_async_results(
            src,
            [ChooserItem(title="a1"), ChooserItem(title="a2")],
            generation=1,
        )
        # Only 1 slot remaining, so only 1 async item added
        assert len(chooser_panel._current_items) == chooser_panel._MAX_TOTAL_RESULTS
        assert chooser_panel._current_items[-1].title == "a1"

    def test_preserve_selection_on_merge(self, chooser_panel):
        """Merged results should use preserve_selection=True."""
        chooser_panel._search_generation = 1
        chooser_panel._pending_async_count = 1
        chooser_panel._current_items = [ChooserItem(title="sync")]

        src = ChooserSource(name="async-src", is_async=True)
        chooser_panel._merge_async_results(
            src,
            [ChooserItem(title="async")],
            generation=1,
        )

        # Check that _push_items_to_js was called with preserve_selection
        # by inspecting the JS output for the -2 sentinel
        js_calls = " ".join(str(c) for c in chooser_panel._eval_js.call_args_list)
        assert ",-2" in js_calls


# ---------------------------------------------------------------------------
# Integration: _launch_async_search with real event loop
# ---------------------------------------------------------------------------


class TestLaunchAsyncSearchIntegration:
    """Integration tests using the real asyncio event loop."""

    @patch("wenzi.scripting.ui.chooser_panel.AppHelper", create=True)
    def test_async_source_results_merged(self, _mock_apphelper, chooser_panel):
        """Async source results are delivered via _merge_async_results."""
        chooser_panel._search_generation = 1
        chooser_panel._pending_async_count = 1

        merge_calls = []
        original_merge = chooser_panel._merge_async_results

        def capture_merge(*args, **kwargs):
            merge_calls.append(args)
            original_merge(*args, **kwargs)

        chooser_panel._merge_async_results = capture_merge

        async def fast_search(query):
            await asyncio.sleep(0.05)
            return [ChooserItem(title=f"fast-{query}")]

        src = ChooserSource(
            name="fast",
            search=fast_search,
            is_async=True,
            search_timeout=2.0,
        )

        # Mock AppHelper.callAfter to call the function directly
        def call_directly(fn, *args, **kwargs):
            fn(*args, **kwargs)

        with patch(
            "PyObjCTools.AppHelper.callAfter",
            side_effect=call_directly,
        ):
            chooser_panel._launch_async_search(src, "hello", generation=1)
            assert _wait_for(lambda: len(merge_calls) > 0, timeout=5.0)

        assert len(chooser_panel._current_items) == 1
        assert chooser_panel._current_items[0].title == "fast-hello"

    @patch("wenzi.scripting.ui.chooser_panel.AppHelper", create=True)
    def test_async_source_timeout(self, _mock_apphelper, chooser_panel):
        """Async source that exceeds timeout returns empty results."""
        chooser_panel._search_generation = 1
        chooser_panel._pending_async_count = 1

        merge_calls = []
        original_merge = chooser_panel._merge_async_results

        def capture_merge(*args, **kwargs):
            merge_calls.append(args)
            original_merge(*args, **kwargs)

        chooser_panel._merge_async_results = capture_merge

        async def slow_search(query):
            await asyncio.sleep(10.0)  # Much longer than timeout
            return [ChooserItem(title="should-not-appear")]

        src = ChooserSource(
            name="slow",
            search=slow_search,
            is_async=True,
            search_timeout=0.1,  # Very short timeout
        )

        with patch(
            "PyObjCTools.AppHelper.callAfter",
            side_effect=lambda fn, *a, **kw: fn(*a, **kw),
        ):
            chooser_panel._launch_async_search(src, "hello", generation=1)
            assert _wait_for(lambda: len(merge_calls) > 0, timeout=5.0)

        # Timed out — no items merged
        assert len(chooser_panel._current_items) == 0

    @patch("wenzi.scripting.ui.chooser_panel.AppHelper", create=True)
    def test_stale_generation_ignored(self, _mock_apphelper, chooser_panel):
        """Async results for an old generation are silently discarded."""
        chooser_panel._search_generation = 1
        chooser_panel._pending_async_count = 1

        merge_calls = []
        original_merge = chooser_panel._merge_async_results

        def capture_merge(*args, **kwargs):
            merge_calls.append(args)
            original_merge(*args, **kwargs)

        chooser_panel._merge_async_results = capture_merge

        async def search(query):
            await asyncio.sleep(0.05)
            return [ChooserItem(title="old-result")]

        src = ChooserSource(
            name="async-src",
            search=search,
            is_async=True,
        )

        with patch(
            "PyObjCTools.AppHelper.callAfter",
            side_effect=lambda fn, *a, **kw: fn(*a, **kw),
        ):
            chooser_panel._launch_async_search(src, "hello", generation=1)
            # Simulate user typing again before result arrives
            chooser_panel._search_generation = 2
            assert _wait_for(lambda: len(merge_calls) > 0, timeout=5.0)

        # Results discarded because generation changed
        assert len(chooser_panel._current_items) == 0


# ---------------------------------------------------------------------------
# Async demo plugin — source registration
# ---------------------------------------------------------------------------


class TestAsyncDemoSourceRegistration:
    def test_async_source_registered(self):
        from wenzi.scripting.api import _WZNamespace
        from wenzi.scripting.registry import ScriptingRegistry

        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        _ = wz.chooser
        wz.chooser._ensure_command_source()

        from async_demo import setup

        setup(wz)

        assert "async-search" in wz.chooser.panel._sources
        src = wz.chooser.panel._sources["async-search"]
        assert src.is_async is True
        assert src.prefix == "as"
        assert src.search_timeout == 3.0
