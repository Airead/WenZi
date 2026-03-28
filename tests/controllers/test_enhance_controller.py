"""Tests for EnhanceController."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from wenzi.controllers.enhance_controller import EnhanceController, EnhanceCacheEntry
from wenzi.lru_cache import LRUCache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_enhancer():
    enhancer = MagicMock()
    enhancer.provider_name = "ollama"
    enhancer.model_name = "qwen2.5:7b"
    enhancer.thinking = False
    enhancer.is_active = True
    enhancer.get_mode_definition.return_value = None
    enhancer.last_system_prompt = "system prompt"
    return enhancer


@pytest.fixture
def mock_panel():
    panel = MagicMock()
    panel._thinking_text = ""
    panel._enhance_text_view = MagicMock()
    panel.enhance_request_id = 0
    return panel


@pytest.fixture
def mock_stats():
    return MagicMock()


@pytest.fixture
def controller(mock_enhancer, mock_panel, mock_stats):
    ctrl = EnhanceController(
        enhancer=mock_enhancer,
        preview_panel=mock_panel,
        usage_stats=mock_stats,
        cache_maxsize=10,
    )
    ctrl.enhance_mode = "proofread"
    return ctrl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_async_gen(chunks):
    """Create an async generator yielding (chunk, usage, is_thinking) tuples."""
    for item in chunks:
        yield item


# ---------------------------------------------------------------------------
# Tests: initialization
# ---------------------------------------------------------------------------


class TestEnhanceControllerInit:
    def test_creates_with_defaults(self, controller):
        assert controller.enhance_mode == "proofread"
        assert isinstance(controller._cache, LRUCache)
        assert controller._cache.maxsize == 10

    def test_enhancer_property(self, controller, mock_enhancer):
        assert controller.enhancer is mock_enhancer

    def test_enhancer_setter(self, controller):
        new_enhancer = MagicMock()
        controller.enhancer = new_enhancer
        assert controller.enhancer is new_enhancer

    def test_none_enhancer(self, mock_panel, mock_stats):
        ctrl = EnhanceController(
            enhancer=None,
            preview_panel=mock_panel,
            usage_stats=mock_stats,
        )
        assert ctrl.enhancer is None

    def test_no_current_task_initially(self, controller):
        assert controller._current_task is None


# ---------------------------------------------------------------------------
# Tests: enhance_mode setter clears diffs
# ---------------------------------------------------------------------------


class TestEnhanceModeSetterDiffRefresh:
    def test_clears_diffs_for_non_tracked_mode(self, controller, mock_enhancer, mock_panel):
        mode_def = MagicMock()
        mode_def.track_corrections = False
        mock_enhancer.get_mode_definition.return_value = mode_def

        controller.enhance_mode = "translate_en"
        mock_panel.clear_diffs.assert_called()

    def test_restores_diffs_for_tracked_mode(self, controller, mock_enhancer, mock_panel, monkeypatch):
        """Switching to a tracked mode should recompute diffs from cached data."""
        mode_def = MagicMock()
        mode_def.track_corrections = True
        mock_enhancer.get_mode_definition.return_value = mode_def

        controller._last_pushed_asr_text = "ASR input"
        mock_panel.enhanced_text = "Enhanced output"
        mock_panel.reset_mock()

        monkeypatch.setattr(
            "wenzi.enhance.text_diff.extract_word_pairs",
            lambda a, b: [("ASR", "Enhanced")],
        )
        controller.enhance_mode = "proofread"
        mock_panel.set_asr_diffs.assert_called()

    def test_no_restore_without_cached_data(self, controller, mock_enhancer, mock_panel):
        """Switching to tracked mode with no cached data should not push diffs."""
        mode_def = MagicMock()
        mode_def.track_corrections = True
        mock_enhancer.get_mode_definition.return_value = mode_def

        controller._last_pushed_asr_text = ""
        mock_panel.enhanced_text = ""
        mock_panel.reset_mock()

        controller.enhance_mode = "proofread"
        mock_panel.set_asr_diffs.assert_not_called()

    def test_clears_diffs_when_mode_def_is_none(self, controller, mock_enhancer, mock_panel):
        mock_enhancer.get_mode_definition.return_value = None

        controller.enhance_mode = "unknown"
        mock_panel.clear_diffs.assert_called()

    def test_no_record_hits_on_mode_switch(self, controller, mock_enhancer, mock_panel, monkeypatch):
        """Mode switch should display diffs without recording vocab hits."""
        mode_def = MagicMock()
        mode_def.track_corrections = True
        mock_enhancer.get_mode_definition.return_value = mode_def

        mock_store = MagicMock()
        mock_store.find_hits_in_text.return_value = []
        controller._manual_vocab_store = mock_store
        controller._last_pushed_asr_text = "text"
        mock_panel.enhanced_text = "enhanced"
        mock_panel.reset_mock()

        monkeypatch.setattr(
            "wenzi.enhance.text_diff.extract_word_pairs",
            lambda a, b: [],
        )
        controller.enhance_mode = "proofread"
        mock_store.record_hits.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: cache operations
# ---------------------------------------------------------------------------


class TestCacheOperations:
    def test_cache_key(self, controller):
        key = controller.cache_key()
        assert key == ("proofread", "ollama", "qwen2.5:7b", False)

    def test_cache_key_changes_with_mode(self, controller):
        key1 = controller.cache_key()
        controller.enhance_mode = "translate"
        key2 = controller.cache_key()
        assert key1 != key2

    def test_cache_key_changes_with_model(self, controller, mock_enhancer):
        key1 = controller.cache_key()
        mock_enhancer.model_name = "llama3:8b"
        key2 = controller.cache_key()
        assert key1 != key2

    def test_cache_key_changes_with_thinking(self, controller, mock_enhancer):
        key1 = controller.cache_key()
        mock_enhancer.thinking = True
        key2 = controller.cache_key()
        assert key1 != key2

    def test_cache_key_no_enhancer(self, mock_panel, mock_stats):
        ctrl = EnhanceController(
            enhancer=None, preview_panel=mock_panel, usage_stats=mock_stats,
        )
        ctrl.enhance_mode = "proofread"
        key = ctrl.cache_key()
        assert key == ("proofread", "", "", False)

    def test_get_cached_miss(self, controller):
        assert controller.get_cached() is None

    def test_get_cached_hit(self, controller):
        entry = EnhanceCacheEntry("text", None, "prompt", "", None)
        controller._cache[controller.cache_key()] = entry
        assert controller.get_cached() is entry

    def test_clear_cache(self, controller):
        entry = EnhanceCacheEntry("text", None, "prompt", "", None)
        controller._cache[controller.cache_key()] = entry
        assert len(controller._cache) == 1
        controller.clear_cache()
        assert len(controller._cache) == 0


# ---------------------------------------------------------------------------
# Tests: cancel
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancel_no_task(self, controller):
        """Cancel when no enhancement is running should not raise."""
        controller.cancel()  # Should not raise

    def test_cancel_cancels_task(self, controller, event_loop):
        """Cancel should cancel the running asyncio task."""
        async def _long_running():
            await asyncio.sleep(100)

        task = event_loop.create_task(_long_running())
        controller._current_task = task
        controller.cancel()
        # task.cancel() marks the task as cancelling; drive the loop
        # so that CancelledError is raised and the task finishes.
        with pytest.raises(asyncio.CancelledError):
            event_loop.run_until_complete(task)

    def test_cancel_ignores_done_task(self, controller, event_loop):
        """Cancel should not raise for an already-done task."""
        async def _instant():
            return 42

        task = event_loop.create_task(_instant())
        event_loop.run_until_complete(task)
        controller._current_task = task
        controller.cancel()  # Should not raise


# ---------------------------------------------------------------------------
# Tests: run
# ---------------------------------------------------------------------------


class TestRun:
    def test_run_with_none_enhancer(self, mock_panel, mock_stats):
        """Run with no enhancer should be a no-op."""
        ctrl = EnhanceController(
            enhancer=None, preview_panel=mock_panel, usage_stats=mock_stats,
        )
        ctrl.run("text", 1)
        # Should not crash or start any task

    @patch("wenzi.controllers.enhance_controller.async_loop")
    def test_run_submits_coroutine(self, mock_async_loop, controller):
        """Run should submit a coroutine to the shared event loop."""
        mock_future = MagicMock()
        # submit() receives a coroutine; close it to avoid RuntimeWarning
        def _capture_and_close(coro):
            coro.close()
            return mock_future
        mock_async_loop.submit.side_effect = _capture_and_close

        # Mock async methods to prevent creating real coroutines
        controller._run_single_async = MagicMock()
        controller._run_wrapper = MagicMock(return_value=asyncio.sleep(0))

        controller.run("hello", 1)
        mock_async_loop.submit.assert_called_once()

    @patch("wenzi.controllers.enhance_controller.async_loop")
    def test_run_cancels_previous_task(self, mock_async_loop, controller, event_loop):
        """Running a new enhance should cancel the previous task."""
        # submit() receives a coroutine; close it to avoid RuntimeWarning
        def _capture_and_close(coro):
            coro.close()
            return MagicMock()
        mock_async_loop.submit.side_effect = _capture_and_close

        # Mock async methods to prevent creating real coroutines
        controller._run_single_async = MagicMock()
        controller._run_wrapper = MagicMock(return_value=asyncio.sleep(0))

        async def _long_running():
            await asyncio.sleep(100)

        old_task = event_loop.create_task(_long_running())
        controller._current_task = old_task

        controller.run("text", 1)
        # Drive the loop so the cancellation takes effect
        with pytest.raises(asyncio.CancelledError):
            event_loop.run_until_complete(old_task)


# ---------------------------------------------------------------------------
# Tests: _run_single_async
# ---------------------------------------------------------------------------


class TestRunSingleAsync:
    def test_single_stream_collects_text(self, controller, mock_enhancer,
                                         mock_panel, event_loop):
        """Single-step streaming should collect chunks and cache result."""
        chunks = [
            ("Hello", None, False),
            (" world", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, False),
        ]
        mock_enhancer.enhance_stream = MagicMock(
            return_value=_make_async_gen(chunks)
        )

        result_holder = {}
        event_loop.run_until_complete(
            controller._run_single_async("test", 1, result_holder)
        )

        assert result_holder["enhanced_text"] == "Hello world"
        assert result_holder["system_prompt"] == "system prompt"
        controller._usage_stats.record_token_usage.assert_called_once()
        mock_panel.set_enhance_complete.assert_called_once()
        # Should be cached
        assert controller.get_cached() is not None
        assert controller.get_cached().final_text == "Hello world"

    def test_single_stream_empty_result(self, controller, mock_enhancer,
                                         mock_panel, event_loop):
        """Empty stream result should show 'Connection failed'."""
        mock_enhancer.enhance_stream = MagicMock(
            return_value=_make_async_gen([])
        )

        event_loop.run_until_complete(
            controller._run_single_async("test", 1, None)
        )

        mock_panel.set_enhance_label.assert_called_with(
            "Connection failed", request_id=1,
        )

    def test_single_stream_thinking_text(self, controller, mock_enhancer,
                                          mock_panel, event_loop):
        """Thinking chunks should be sent to thinking panel."""
        chunks = [
            ("thinking...", None, True),
            ("result", {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}, False),
        ]
        mock_enhancer.enhance_stream = MagicMock(
            return_value=_make_async_gen(chunks)
        )

        event_loop.run_until_complete(
            controller._run_single_async("test", 1, None)
        )

        mock_panel.append_thinking_text.assert_called()
        mock_panel.clear_enhance_text.assert_called()

    def test_single_stream_retry_label(self, controller, mock_enhancer,
                                        mock_panel, event_loop):
        """Retry chunks should update the enhance label."""
        chunks = [
            ("(retrying...)", None, "retry"),
            ("result", {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}, False),
        ]
        mock_enhancer.enhance_stream = MagicMock(
            return_value=_make_async_gen(chunks)
        )

        event_loop.run_until_complete(
            controller._run_single_async("test", 1, None)
        )

        mock_panel.set_enhance_label.assert_called()

    def test_single_stream_cancellation(self, controller, mock_enhancer,
                                         event_loop):
        """CancelledError should propagate from async generator."""
        async def _cancelling_gen(*args, **kwargs):
            yield ("chunk1", None, False)
            raise asyncio.CancelledError()

        mock_enhancer.enhance_stream = MagicMock(
            return_value=_cancelling_gen()
        )

        with pytest.raises(asyncio.CancelledError):
            event_loop.run_until_complete(
                controller._run_single_async("test", 1, None)
            )


# ---------------------------------------------------------------------------
# Tests: diff skipping based on track_corrections
# ---------------------------------------------------------------------------


class TestDiffSkipping:
    """Diffs should only be computed/pushed when mode has track_corrections=True."""

    def _make_mode_def(self, track_corrections):
        mode_def = MagicMock()
        mode_def.track_corrections = track_corrections
        mode_def.steps = []
        return mode_def

    def _setup_stream(self, mock_enhancer):
        chunks = [
            ("result", {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}, False),
        ]
        mock_enhancer.enhance_stream = MagicMock(
            return_value=_make_async_gen(chunks)
        )

    def test_single_calls_push_diffs_when_tracked(
        self, controller, mock_enhancer, mock_panel, event_loop,
    ):
        """_push_diffs_and_hits should be called for track_corrections=True mode."""
        mode_def = self._make_mode_def(track_corrections=True)
        mock_enhancer.get_mode_definition.return_value = mode_def
        self._setup_stream(mock_enhancer)
        controller._push_diffs_and_hits = MagicMock()

        event_loop.run_until_complete(
            controller._run_single_async("asr text", 1, {}, track_corrections=True)
        )
        controller._push_diffs_and_hits.assert_called_once()

    def test_single_skips_push_diffs_when_not_tracked(
        self, controller, mock_enhancer, mock_panel, event_loop,
    ):
        """_push_diffs_and_hits should NOT be called for track_corrections=False mode."""
        mode_def = self._make_mode_def(track_corrections=False)
        mock_enhancer.get_mode_definition.return_value = mode_def
        self._setup_stream(mock_enhancer)
        controller._push_diffs_and_hits = MagicMock()

        event_loop.run_until_complete(
            controller._run_single_async("asr text", 1, {}, track_corrections=False)
        )
        controller._push_diffs_and_hits.assert_not_called()

    def test_chain_calls_push_diffs_when_tracked(
        self, controller, mock_enhancer, mock_panel, event_loop,
    ):
        """Chain mode with track_corrections=True should push diffs."""
        call_count = 0

        def _make_step_gen(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _make_async_gen([
                ("out", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, False),
            ])

        mock_enhancer.enhance_stream = MagicMock(side_effect=_make_step_gen)

        step_def = MagicMock()
        step_def.label = "Step"
        chain_mode_def = self._make_mode_def(track_corrections=True)

        def _get_mode_def(mode_id):
            if mode_id == "chain_mode":
                return chain_mode_def
            return step_def

        mock_enhancer.get_mode_definition = MagicMock(side_effect=_get_mode_def)
        controller._push_diffs_and_hits = MagicMock()

        event_loop.run_until_complete(
            controller._run_chain_async(
                "input", 1, {}, ["s1"], "chain_mode", track_corrections=True,
            )
        )
        controller._push_diffs_and_hits.assert_called_once()

    def test_chain_skips_push_diffs_when_not_tracked(
        self, controller, mock_enhancer, mock_panel, event_loop,
    ):
        """Chain mode with track_corrections=False should skip diffs."""
        mock_enhancer.enhance_stream = MagicMock(
            return_value=_make_async_gen([
                ("out", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, False),
            ])
        )

        step_def = MagicMock()
        step_def.label = "Step"
        chain_mode_def = self._make_mode_def(track_corrections=False)

        def _get_mode_def(mode_id):
            if mode_id == "chain_mode":
                return chain_mode_def
            return step_def

        mock_enhancer.get_mode_definition = MagicMock(side_effect=_get_mode_def)
        controller._push_diffs_and_hits = MagicMock()

        event_loop.run_until_complete(
            controller._run_chain_async(
                "input", 1, {}, ["s1"], "chain_mode", track_corrections=False,
            )
        )
        controller._push_diffs_and_hits.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _run_chain_async
# ---------------------------------------------------------------------------


class TestRunChainAsync:
    def test_chain_two_steps(self, controller, mock_enhancer,
                              mock_panel, mock_stats, event_loop):
        """Chain enhancement should run multiple steps sequentially."""
        call_count = 0

        def _make_step_gen(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_async_gen([
                    ("step1 out", {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}, False),
                ])
            else:
                return _make_async_gen([
                    ("step2 out", {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10}, False),
                ])

        mock_enhancer.enhance_stream = MagicMock(side_effect=_make_step_gen)

        step1_def = MagicMock()
        step1_def.label = "Step 1"
        step2_def = MagicMock()
        step2_def.label = "Step 2"

        def _get_mode_def(mode_id):
            return {"step1": step1_def, "step2": step2_def}.get(mode_id)

        mock_enhancer.get_mode_definition = MagicMock(side_effect=_get_mode_def)

        result_holder = {}
        event_loop.run_until_complete(
            controller._run_chain_async(
                "input", 1, result_holder,
                ["step1", "step2"], "chain_mode",
            )
        )

        assert result_holder["enhanced_text"] == "step2 out"
        assert result_holder["is_chain"] is True
        assert result_holder["token_usage"]["total_tokens"] == 18
        mock_panel.set_enhance_complete.assert_called_once()
        assert mock_enhancer.mode == "chain_mode"

    def test_chain_restores_mode_on_cancel(self, controller, mock_enhancer,
                                            event_loop):
        """Chain should restore enhancer mode even if cancelled."""
        async def _cancelling_gen(*args, **kwargs):
            raise asyncio.CancelledError()
            yield  # make it a generator  # noqa: E501

        mock_enhancer.enhance_stream = MagicMock(
            return_value=_cancelling_gen()
        )

        step_def = MagicMock()
        step_def.label = "Step"
        mock_enhancer.get_mode_definition = MagicMock(return_value=step_def)

        with pytest.raises(asyncio.CancelledError):
            event_loop.run_until_complete(
                controller._run_chain_async(
                    "input", 1, None, ["step1"], "original_mode",
                )
            )

        # Mode must be restored even after cancel
        assert mock_enhancer.mode == "original_mode"

    def test_chain_step_separator(self, controller, mock_enhancer,
                                   mock_panel, event_loop):
        """Non-first steps should have a separator prepended."""
        call_count = 0

        def _make_step_gen(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _make_async_gen([
                ("out", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, False),
            ])

        mock_enhancer.enhance_stream = MagicMock(side_effect=_make_step_gen)

        step_def = MagicMock()
        step_def.label = "StepX"
        mock_enhancer.get_mode_definition = MagicMock(return_value=step_def)

        event_loop.run_until_complete(
            controller._run_chain_async(
                "input", 1, None, ["s1", "s2"], "chain",
            )
        )

        # Check separator was appended for step 2
        separator_calls = [
            c for c in mock_panel.append_enhance_text.call_args_list
            if "---" in str(c)
        ]
        assert len(separator_calls) == 1


# ---------------------------------------------------------------------------
# Tests: _run_wrapper
# ---------------------------------------------------------------------------


class TestRunWrapper:
    def test_wrapper_captures_task(self, controller, event_loop):
        """_run_wrapper should set _current_task to the running task."""
        captured_task = None

        async def _capture():
            nonlocal captured_task
            captured_task = controller._current_task

        event_loop.run_until_complete(
            controller._run_wrapper(_capture(), request_id=1)
        )

        assert captured_task is not None

    def test_wrapper_handles_exception(self, controller, mock_panel, event_loop):
        """_run_wrapper should catch exceptions and report error."""
        async def _failing():
            raise ValueError("boom")

        event_loop.run_until_complete(
            controller._run_wrapper(_failing(), request_id=42)
        )

        mock_panel.set_enhance_result.assert_called_once()
        call_args = mock_panel.set_enhance_result.call_args
        assert "boom" in call_args[0][0]

    def test_wrapper_handles_cancellation(self, controller, event_loop):
        """_run_wrapper should catch CancelledError silently."""
        async def _cancelled():
            raise asyncio.CancelledError()

        # Should not raise
        event_loop.run_until_complete(
            controller._run_wrapper(_cancelled(), request_id=1)
        )
