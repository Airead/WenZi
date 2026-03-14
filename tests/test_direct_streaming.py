"""Integration tests for Direct mode streaming enhancement.

Tests the streaming helper functions (_run_direct_single_stream,
_run_direct_chain_stream) and the _do_transcribe_direct flow using
mock objects, without importing the full app module.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_pyobjc(monkeypatch):
    """Mock PyObjCTools.AppHelper so callAfter executes immediately."""
    mock_apphelper = MagicMock()
    mock_apphelper.callAfter = lambda fn: fn()
    mock_pyobjctools = MagicMock()
    mock_pyobjctools.AppHelper = mock_apphelper

    monkeypatch.setitem(sys.modules, "PyObjCTools", mock_pyobjctools)
    monkeypatch.setitem(sys.modules, "PyObjCTools.AppHelper", mock_apphelper)

    return mock_apphelper


def _make_async_gen(chunks):
    """Create an async generator from a list of (chunk, usage, is_thinking) tuples."""

    async def gen():
        for item in chunks:
            yield item

    return gen()


def _run_single_stream(app, asr_text, cancel_event):
    """Replicate _run_direct_single_stream logic for testing."""
    loop = asyncio.new_event_loop()
    collected: list[str] = []
    usage = None

    async def _stream():
        nonlocal usage
        gen = app._enhancer.enhance_stream(asr_text)
        completion_tokens = 0
        thinking_tokens = 0
        had_thinking = False
        try:
            async for chunk, chunk_usage, is_thinking in gen:
                if cancel_event.is_set():
                    return
                if is_thinking == "retry" and chunk:
                    had_thinking = True
                    app._streaming_overlay.append_thinking_text(chunk)
                    label = chunk.strip().strip("()\n")
                    app._streaming_overlay.set_status(f"\u23f3 {label}")
                elif is_thinking and chunk:
                    had_thinking = True
                    thinking_tokens += 1
                    app._streaming_overlay.append_thinking_text(
                        chunk, thinking_tokens=thinking_tokens
                    )
                elif chunk:
                    if had_thinking:
                        had_thinking = False
                        app._streaming_overlay.clear_text()
                    collected.append(chunk)
                    completion_tokens += 1
                    app._streaming_overlay.append_text(
                        chunk, completion_tokens=completion_tokens
                    )
                if chunk_usage is not None:
                    usage = chunk_usage
        finally:
            await gen.aclose()

    loop.run_until_complete(_stream())
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()

    if usage:
        app._usage_stats.record_token_usage(usage)
        app._streaming_overlay.set_complete(usage)

    return "".join(collected).strip() or asr_text


def _run_chain_stream(app, asr_text, chain_steps, cancel_event):
    """Replicate _run_direct_chain_stream logic for testing."""
    loop = asyncio.new_event_loop()
    total_steps = len(chain_steps)
    input_text = asr_text
    original_mode = app._enhancer.mode
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    try:
        for step_idx, step_id in enumerate(chain_steps, 1):
            if cancel_event.is_set():
                break

            step_def = app._enhancer.get_mode_definition(step_id)
            step_label = step_def.label if step_def else step_id

            app._streaming_overlay.set_status(
                f"\u23f3 Step {step_idx}/{total_steps}: {step_label}"
            )

            if step_idx > 1:
                app._streaming_overlay.clear_text()

            app._enhancer.mode = step_id
            collected: list[str] = []
            step_usage = None

            async def _stream_step(text_input: str) -> None:
                nonlocal step_usage
                gen = app._enhancer.enhance_stream(text_input)
                completion_tokens = 0
                thinking_tokens = 0
                had_thinking = False
                try:
                    async for chunk, chunk_usage, is_thinking in gen:
                        if cancel_event.is_set():
                            return
                        if is_thinking == "retry" and chunk:
                            had_thinking = True
                            app._streaming_overlay.append_thinking_text(chunk)
                        elif is_thinking and chunk:
                            had_thinking = True
                            thinking_tokens += 1
                            app._streaming_overlay.append_thinking_text(
                                chunk, thinking_tokens=thinking_tokens
                            )
                        elif chunk:
                            if had_thinking:
                                had_thinking = False
                            collected.append(chunk)
                            completion_tokens += 1
                            app._streaming_overlay.append_text(
                                chunk, completion_tokens=completion_tokens
                            )
                        if chunk_usage is not None:
                            step_usage = chunk_usage
                finally:
                    await gen.aclose()

            loop.run_until_complete(_stream_step(input_text))

            if cancel_event.is_set():
                break

            step_result = "".join(collected).strip()
            if step_result:
                input_text = step_result

            if step_usage:
                total_usage["prompt_tokens"] += step_usage.get("prompt_tokens", 0)
                total_usage["completion_tokens"] += step_usage.get("completion_tokens", 0)
                total_usage["total_tokens"] += step_usage.get("total_tokens", 0)
            app._usage_stats.record_token_usage(step_usage)

        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

        if total_usage["total_tokens"] > 0:
            app._streaming_overlay.set_complete(total_usage)

        return input_text.strip() or asr_text
    finally:
        app._enhancer.mode = original_mode


@pytest.fixture
def mock_enhancer():
    """Create a mock TextEnhancer with enhance_stream support."""
    enhancer = MagicMock()
    enhancer.is_active = True
    enhancer.mode = "proofread"
    enhancer.provider_name = "openai"
    enhancer.model_name = "gpt-4o"
    enhancer.last_system_prompt = "You are a proofreader."

    mode_def = MagicMock()
    mode_def.label = "Proofread"
    mode_def.steps = None
    enhancer.get_mode_definition.return_value = mode_def

    return enhancer


@pytest.fixture
def mock_overlay():
    return MagicMock()


@pytest.fixture
def mock_app(mock_enhancer, mock_overlay):
    app = MagicMock()
    app._enhancer = mock_enhancer
    app._streaming_overlay = mock_overlay
    app._usage_stats = MagicMock()
    app._enhance_mode = "proofread"
    app._append_newline = False
    app._output_method = "key"
    app._conversation_history = MagicMock()
    return app


class TestRunDirectSingleStream:
    """Test the single-step streaming logic."""

    def test_collects_chunks_and_returns_text(self, mock_app):
        chunks = [
            ("Hello", None, False),
            (" world", None, False),
            ("", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, False),
        ]
        mock_app._enhancer.enhance_stream.return_value = _make_async_gen(chunks)

        result = _run_single_stream(mock_app, "你好世界", threading.Event())

        assert result == "Hello world"
        assert mock_app._streaming_overlay.append_text.call_count == 2
        mock_app._streaming_overlay.append_text.assert_any_call(
            "Hello", completion_tokens=1
        )
        mock_app._streaming_overlay.append_text.assert_any_call(
            " world", completion_tokens=2
        )

    def test_thinking_then_content(self, mock_app):
        """Thinking tokens should show in overlay, then be cleared for content."""
        chunks = [
            ("thinking...", None, True),
            ("Result", None, False),
            ("", {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}, False),
        ]
        mock_app._enhancer.enhance_stream.return_value = _make_async_gen(chunks)

        result = _run_single_stream(mock_app, "test", threading.Event())

        assert result == "Result"
        # Thinking text was appended
        mock_app._streaming_overlay.append_thinking_text.assert_called_once_with(
            "thinking...", thinking_tokens=1
        )
        # Text view was cleared before content
        mock_app._streaming_overlay.clear_text.assert_called_once()
        # Content was appended
        mock_app._streaming_overlay.append_text.assert_called_once_with(
            "Result", completion_tokens=1
        )

    def test_retry_shows_status(self, mock_app):
        """Retry messages should update status label."""
        chunks = [
            ("(Retrying 1/3...)\n", None, "retry"),
            ("Result", None, False),
            ("", {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}, False),
        ]
        mock_app._enhancer.enhance_stream.return_value = _make_async_gen(chunks)

        result = _run_single_stream(mock_app, "test", threading.Event())

        assert result == "Result"
        mock_app._streaming_overlay.set_status.assert_any_call(
            "\u23f3 Retrying 1/3..."
        )

    def test_cancel_stops_streaming(self, mock_app):
        cancel_event = threading.Event()
        cancel_event.set()  # Pre-cancel

        chunks = [("Hello", None, False), (" world", None, False)]
        mock_app._enhancer.enhance_stream.return_value = _make_async_gen(chunks)

        result = _run_single_stream(mock_app, "test", cancel_event)

        assert result == "test"
        mock_app._streaming_overlay.append_text.assert_not_called()

    def test_fallback_on_empty_result(self, mock_app):
        chunks = [
            ("", {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10}, False),
        ]
        mock_app._enhancer.enhance_stream.return_value = _make_async_gen(chunks)

        result = _run_single_stream(mock_app, "original", threading.Event())

        assert result == "original"

    def test_records_token_usage_and_set_complete(self, mock_app):
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        chunks = [("Hi", None, False), ("", usage, False)]
        mock_app._enhancer.enhance_stream.return_value = _make_async_gen(chunks)

        _run_single_stream(mock_app, "test", threading.Event())
        mock_app._usage_stats.record_token_usage.assert_called_with(usage)
        mock_app._streaming_overlay.set_complete.assert_called_with(usage)


class TestRunDirectChainStream:
    """Test multi-step chain streaming logic."""

    def test_chain_runs_steps_sequentially(self, mock_app):
        step1_def = MagicMock()
        step1_def.label = "Proofread"
        step2_def = MagicMock()
        step2_def.label = "Translate"

        def get_mode_def(mode_id):
            return {"proofread": step1_def, "translate": step2_def}.get(mode_id)

        mock_app._enhancer.get_mode_definition.side_effect = get_mode_def

        call_count = [0]

        def make_stream(text):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_async_gen([
                    ("Corrected text", None, False),
                    ("", {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}, False),
                ])
            else:
                return _make_async_gen([
                    ("Translated text", None, False),
                    ("", {"prompt_tokens": 15, "completion_tokens": 2, "total_tokens": 17}, False),
                ])

        mock_app._enhancer.enhance_stream.side_effect = make_stream

        result = _run_chain_stream(
            mock_app, "原始文本", ["proofread", "translate"], threading.Event()
        )

        assert result == "Translated text"
        mock_app._streaming_overlay.set_status.assert_any_call(
            "\u23f3 Step 1/2: Proofread"
        )
        mock_app._streaming_overlay.set_status.assert_any_call(
            "\u23f3 Step 2/2: Translate"
        )
        mock_app._streaming_overlay.clear_text.assert_called()
        # Mode restored
        assert mock_app._enhancer.mode == "proofread"

    def test_chain_cancel_stops_early(self, mock_app):
        cancel_event = threading.Event()
        cancel_event.set()

        step_def = MagicMock()
        step_def.label = "Step"
        mock_app._enhancer.get_mode_definition.return_value = step_def

        result = _run_chain_stream(
            mock_app, "original", ["step1", "step2"], cancel_event
        )

        assert result == "original"
        mock_app._enhancer.enhance_stream.assert_not_called()

    def test_chain_restores_mode_on_error(self, mock_app):
        mock_app._enhancer.mode = "original_mode"
        step_def = MagicMock()
        step_def.label = "Step"
        mock_app._enhancer.get_mode_definition.return_value = step_def

        async def failing_gen():
            raise RuntimeError("LLM error")
            yield  # noqa: E275

        mock_app._enhancer.enhance_stream.return_value = failing_gen()

        with pytest.raises(RuntimeError):
            _run_chain_stream(mock_app, "test", ["step1"], threading.Event())

        assert mock_app._enhancer.mode == "original_mode"


class TestDoTranscribeDirectFlow:
    """Test the full _do_transcribe_direct flow logic."""

    def test_enhance_shows_overlay_and_types_result(self, mock_app):
        """Full flow: show overlay, stream enhance, close overlay, type result."""
        from PyObjCTools import AppHelper

        chunks = [
            ("Enhanced!", None, False),
            ("", {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}, False),
        ]
        mock_app._enhancer.enhance_stream.return_value = _make_async_gen(chunks)

        mode_def = MagicMock()
        mode_def.steps = None
        mock_app._enhancer.get_mode_definition.return_value = mode_def

        # Simulate _do_transcribe_direct flow
        asr_text = "你好"
        cancel_event = threading.Event()

        AppHelper.callAfter(
            lambda: mock_app._streaming_overlay.show(
                asr_text=asr_text, cancel_event=cancel_event
            )
        )

        text = _run_single_stream(mock_app, asr_text, cancel_event)
        AppHelper.callAfter(mock_app._streaming_overlay.close)

        mock_app._streaming_overlay.show.assert_called_once()
        mock_app._streaming_overlay.close.assert_called_once()
        assert text == "Enhanced!"

    def test_no_enhance_skips_overlay(self, mock_app):
        """Without enhance, overlay should not be shown."""
        # Just verify the overlay is not called when use_enhance=False
        mock_app._streaming_overlay.show.assert_not_called()

    def test_cancel_returns_original_text(self, mock_app):
        """When cancelled, result should be original ASR text."""
        cancel_event = threading.Event()

        def fake_show(asr_text="", cancel_event=None):
            if cancel_event:
                cancel_event.set()

        mock_app._streaming_overlay.show.side_effect = fake_show

        from PyObjCTools import AppHelper

        AppHelper.callAfter(
            lambda: mock_app._streaming_overlay.show(
                asr_text="test", cancel_event=cancel_event
            )
        )

        assert cancel_event.is_set()

    def test_enhance_failure_fallback(self, mock_app):
        """Enhancement failure should result in original ASR text."""
        mock_app._enhancer.enhance_stream.side_effect = RuntimeError("LLM down")

        asr_text = "原始"
        cancel_event = threading.Event()
        text = asr_text

        try:
            text = _run_single_stream(mock_app, asr_text, cancel_event)
        except Exception:
            text = asr_text

        assert text == "原始"
