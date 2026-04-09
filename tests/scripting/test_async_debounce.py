"""Tests for async source debounce functionality."""

from __future__ import annotations

from unittest.mock import patch

from wenzi.scripting.api.chooser import ChooserAPI
from wenzi.scripting.sources import ChooserItem, ChooserSource

# ---------------------------------------------------------------------------
# ChooserSource debounce field
# ---------------------------------------------------------------------------


class TestChooserSourceDebounceField:
    def test_defaults(self):
        src = ChooserSource(name="test")
        assert src.debounce_delay is None

    def test_custom_debounce_delay(self):
        src = ChooserSource(name="test", debounce_delay=0.3)
        assert src.debounce_delay == 0.3

    def test_zero_debounce_delay(self):
        src = ChooserSource(name="test", debounce_delay=0)
        assert src.debounce_delay == 0


# ---------------------------------------------------------------------------
# ChooserPanel._get_debounce_delay
# ---------------------------------------------------------------------------


class TestGetDebounceDelay:
    def test_none_uses_global_default(self, chooser_panel):
        src = ChooserSource(name="test", is_async=True, debounce_delay=None)
        assert chooser_panel._get_debounce_delay(src) == chooser_panel._DEFAULT_ASYNC_DEBOUNCE

    def test_custom_value(self, chooser_panel):
        src = ChooserSource(name="test", is_async=True, debounce_delay=0.5)
        assert chooser_panel._get_debounce_delay(src) == 0.5

    def test_zero_value(self, chooser_panel):
        src = ChooserSource(name="test", is_async=True, debounce_delay=0)
        assert chooser_panel._get_debounce_delay(src) == 0


# ---------------------------------------------------------------------------
# ChooserPanel._get_timeout
# ---------------------------------------------------------------------------


class TestGetTimeout:
    def test_none_uses_global_default(self, chooser_panel):
        src = ChooserSource(name="test", is_async=True, search_timeout=None)
        assert chooser_panel._get_timeout(src) == chooser_panel._DEFAULT_ASYNC_TIMEOUT

    def test_custom_value(self, chooser_panel):
        src = ChooserSource(name="test", is_async=True, search_timeout=3.0)
        assert chooser_panel._get_timeout(src) == 3.0


# ---------------------------------------------------------------------------
# @source decorator debounce parameter
# ---------------------------------------------------------------------------


class TestSourceDecoratorDebounce:
    def test_debounce_delay_passed_to_source(self):
        api = ChooserAPI()

        @api.source("debounced", debounce_delay=0.25)
        async def search(query):
            return []

        src = api.panel._sources["debounced"]
        assert src.debounce_delay == 0.25

    def test_debounce_delay_default_is_none(self):
        api = ChooserAPI()

        @api.source("no-debounce")
        async def search(query):
            return []

        src = api.panel._sources["no-debounce"]
        assert src.debounce_delay is None

    def test_search_timeout_default_is_none(self):
        api = ChooserAPI()

        @api.source("no-timeout")
        async def search(query):
            return []

        src = api.panel._sources["no-timeout"]
        assert src.search_timeout is None


# ---------------------------------------------------------------------------
# Debounce scheduling
# ---------------------------------------------------------------------------


class TestDebounceScheduling:
    def test_schedule_sets_timer(self, chooser_panel):
        chooser_panel._search_generation = 1

        src = ChooserSource(name="test", is_async=True)
        chooser_panel._schedule_debounced_search(src, "hello", 1, 0.1)

        entry = chooser_panel._debounce_state.get("test")
        assert entry is not None
        assert entry.source is src
        assert entry.query == "hello"
        assert entry.generation == 1

        # Cleanup
        chooser_panel._cancel_all_debounce_timers()

    def test_schedule_cancels_previous_timer_for_same_source(self, chooser_panel):
        chooser_panel._search_generation = 1

        src = ChooserSource(name="test", is_async=True)

        # Schedule first
        chooser_panel._schedule_debounced_search(src, "hello", 1, 0.5)
        old_timer = chooser_panel._debounce_state["test"].timer

        # Schedule second for same source (should cancel first)
        chooser_panel._schedule_debounced_search(src, "world", 2, 0.5)

        assert chooser_panel._debounce_state["test"].timer is not old_timer
        assert chooser_panel._debounce_state["test"].query == "world"
        assert chooser_panel._debounce_state["test"].generation == 2

        # Cleanup
        chooser_panel._cancel_all_debounce_timers()

    def test_independent_timers_per_source(self, chooser_panel):
        chooser_panel._search_generation = 1

        src1 = ChooserSource(name="src1", is_async=True)
        src2 = ChooserSource(name="src2", is_async=True)

        chooser_panel._schedule_debounced_search(src1, "hello", 1, 0.1)
        chooser_panel._schedule_debounced_search(src2, "hello", 1, 0.5)

        assert "src1" in chooser_panel._debounce_state
        assert "src2" in chooser_panel._debounce_state
        assert chooser_panel._debounce_state["src1"].timer is not chooser_panel._debounce_state["src2"].timer

        # Cleanup
        chooser_panel._cancel_all_debounce_timers()

    def test_close_cancels_all_timers(self, chooser_panel):
        chooser_panel._search_generation = 1

        src1 = ChooserSource(name="src1", is_async=True)
        src2 = ChooserSource(name="src2", is_async=True)
        chooser_panel._schedule_debounced_search(src1, "hello", 1, 1.0)
        chooser_panel._schedule_debounced_search(src2, "hello", 1, 1.0)

        assert len(chooser_panel._debounce_state) == 2

        # Mock necessary cleanup
        chooser_panel._webview = None
        chooser_panel._panel = None

        chooser_panel.close()

        assert len(chooser_panel._debounce_state) == 0


# ---------------------------------------------------------------------------
# _do_search debounce behavior
# ---------------------------------------------------------------------------


class TestDoSearchDebounce:
    def test_zero_debounce_launches_immediately(self, chooser_panel):
        async def async_search(query):
            return [ChooserItem(title=f"async-{query}")]

        chooser_panel.register_source(
            ChooserSource(
                name="immediate",
                search=async_search,
                is_async=True,
                debounce_delay=0,
            )
        )

        with patch.object(chooser_panel, "_launch_async_search") as mock_launch:
            chooser_panel._do_search("test")
            mock_launch.assert_called_once()

    def test_positive_debounce_schedules(self, chooser_panel):
        async def async_search(query):
            return [ChooserItem(title=f"async-{query}")]

        chooser_panel.register_source(
            ChooserSource(
                name="debounced",
                search=async_search,
                is_async=True,
                debounce_delay=0.3,
            )
        )

        with patch.object(chooser_panel, "_schedule_debounced_search") as mock_schedule:
            chooser_panel._do_search("test")
            mock_schedule.assert_called_once()
            args = mock_schedule.call_args[0]
            assert args[0].name == "debounced"  # source
            assert args[1] == "test"  # query
            assert args[3] == 0.3  # delay

    def test_mixed_immediate_and_debounced(self, chooser_panel):
        async def async_search(query):
            return [ChooserItem(title=f"async-{query}")]

        chooser_panel.register_source(
            ChooserSource(
                name="immediate",
                search=async_search,
                is_async=True,
                debounce_delay=0,
            )
        )

        chooser_panel.register_source(
            ChooserSource(
                name="debounced",
                search=async_search,
                is_async=True,
                debounce_delay=0.2,
            )
        )

        with patch.object(chooser_panel, "_launch_async_search") as mock_launch, \
                patch.object(chooser_panel, "_schedule_debounced_search") as mock_schedule:
            chooser_panel._do_search("test")
            # Immediate source should launch
            mock_launch.assert_called_once()
            # Debounced source should be scheduled
            mock_schedule.assert_called_once()

    def test_each_debounced_source_gets_own_delay(self, chooser_panel):
        async def async_search(query):
            return [ChooserItem(title=f"async-{query}")]

        chooser_panel.register_source(
            ChooserSource(
                name="slow",
                search=async_search,
                is_async=True,
                debounce_delay=0.5,
            )
        )

        chooser_panel.register_source(
            ChooserSource(
                name="fast",
                search=async_search,
                is_async=True,
                debounce_delay=0.1,
            )
        )

        with patch.object(chooser_panel, "_schedule_debounced_search") as mock_schedule:
            chooser_panel._do_search("test")
            assert mock_schedule.call_count == 2
            calls = mock_schedule.call_args_list
            delays = {c[0][0].name: c[0][3] for c in calls}
            assert delays["slow"] == 0.5
            assert delays["fast"] == 0.1


# ---------------------------------------------------------------------------
# Integration: debounce timer fires
# ---------------------------------------------------------------------------


class TestDebounceTimerFires:
    def test_timer_fires_and_launches_search(self, chooser_panel):
        """Debounce timer fires after delay and launches async search."""
        chooser_panel._search_generation = 1

        launch_calls = []

        def capture_launch(src, query, gen):
            launch_calls.append((src.name, query, gen))
            chooser_panel._pending_async_count = max(0, chooser_panel._pending_async_count - 1)

        chooser_panel._launch_async_search = capture_launch

        src = ChooserSource(name="test", is_async=True)
        chooser_panel._schedule_debounced_search(src, "hello", 1, 0.1)

        entry = chooser_panel._debounce_state["test"]
        assert entry is not None

        # Manually fire the timer callback (simulates RunLoop firing timer)
        entry.helper.fire_(entry.timer)

        assert len(launch_calls) == 1
        assert launch_calls[0] == ("test", "hello", 1)
        assert "test" not in chooser_panel._debounce_state

    def test_stale_generation_discarded(self, chooser_panel):
        """Timer fires but search is discarded if generation changed."""
        chooser_panel._search_generation = 1

        launch_calls = []

        def capture_launch(src, query, gen):
            launch_calls.append((src.name, query, gen))

        chooser_panel._launch_async_search = capture_launch

        src = ChooserSource(name="test", is_async=True)
        chooser_panel._schedule_debounced_search(src, "hello", 1, 0.1)

        entry = chooser_panel._debounce_state["test"]

        # Simulate new search before timer fires (which resets generation)
        chooser_panel._search_generation = 2

        # Manually fire the timer callback
        entry.helper.fire_(entry.timer)

        # Should not have launched because generation is stale
        assert len(launch_calls) == 0
        assert "test" not in chooser_panel._debounce_state
