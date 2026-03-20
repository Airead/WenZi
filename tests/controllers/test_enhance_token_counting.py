"""Test character counting in EnhanceController streaming.

Verifies that thinking and completion counters accumulate by character count
(len(chunk)) rather than by chunk count (+1), so the UI displays accurate
character counts for all models including MiniMax (inline <think> tags).
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from wenzi.controllers.enhance_controller import EnhanceController


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_controller(stream_chunks):
    """Build an EnhanceController with a mock enhancer that yields *stream_chunks*.

    Each entry in *stream_chunks* is ``(text, usage, is_thinking)``.
    """
    enhancer = MagicMock()
    enhancer.provider_name = "minimax"
    enhancer.model_name = "MiniMax-M1"
    enhancer.thinking = True
    enhancer.is_active = True
    enhancer.get_mode_definition.return_value = None
    enhancer.last_system_prompt = "system"

    async def fake_stream(text, **kwargs):
        for chunk in stream_chunks:
            yield chunk

    enhancer.enhance_stream = fake_stream

    panel = MagicMock()
    panel._thinking_text = ""
    panel.enhance_request_id = 0
    stats = MagicMock()

    ctrl = EnhanceController(
        enhancer=enhancer, preview_panel=panel, usage_stats=stats,
    )
    ctrl.enhance_mode = "proofread"
    return ctrl, panel


def _run_single(ctrl, text="hello", request_id=1):
    """Synchronously run _run_single (which normally runs in a thread)."""
    ctrl._run_single(text, request_id, result_holder={}, cancel_event=threading.Event())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestThinkingCharCounting:
    """Verify character-based counting for thinking and completion streams."""

    def test_single_char_thinking_chunks(self):
        """Single-char chunks: char count equals chunk count."""
        chunks = [
            ("a", None, True),
            ("b", None, True),
            ("c", None, True),
            ("d", None, True),
            ("e", None, True),
            ("result", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cache_read_tokens": 0}, False),
        ]
        ctrl, panel = _make_controller(chunks)
        _run_single(ctrl)

        thinking_calls = panel.append_thinking_text.call_args_list
        thinking_values = [c.kwargs["thinking_tokens"] for c in thinking_calls]
        assert thinking_values == [1, 2, 3, 4, 5]

    def test_multi_char_thinking_chunks_minimax(self):
        """MiniMax-style large thinking chunks: counter accumulates by len(chunk)."""
        chunks = [
            ("Let me analyze this.", None, True),   # 20 chars
            (" Checking grammar.", None, True),      # 19 chars
            ("Hello world", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cache_read_tokens": 0}, False),
        ]
        ctrl, panel = _make_controller(chunks)
        _run_single(ctrl)

        thinking_calls = panel.append_thinking_text.call_args_list
        thinking_values = [c.kwargs["thinking_tokens"] for c in thinking_calls]
        assert thinking_values[0] == len("Let me analyze this.")  # 20
        assert thinking_values[1] == len("Let me analyze this.") + len(" Checking grammar.")  # 39

    def test_multi_char_completion_chunks(self):
        """Completion counter also accumulates by character count."""
        chunks = [
            ("Hello ", None, False),    # 6 chars
            ("world!", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cache_read_tokens": 0}, False),  # 6 chars
        ]
        ctrl, panel = _make_controller(chunks)
        _run_single(ctrl)

        completion_calls = panel.append_enhance_text.call_args_list
        token_values = [c.kwargs["completion_tokens"] for c in completion_calls]
        assert token_values[0] == 6   # len("Hello ")
        assert token_values[1] == 12  # len("Hello ") + len("world!")

    def test_mixed_thinking_and_completion(self):
        """Thinking and completion counters are independent."""
        chunks = [
            ("think think think", None, True),   # 17 chars thinking
            ("AB", None, False),                  # 2 chars completion
            ("CD", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cache_read_tokens": 0}, False),  # 2 chars completion
        ]
        ctrl, panel = _make_controller(chunks)
        _run_single(ctrl)

        thinking_calls = panel.append_thinking_text.call_args_list
        assert len(thinking_calls) == 1
        assert thinking_calls[0].kwargs["thinking_tokens"] == 17

        completion_calls = panel.append_enhance_text.call_args_list
        assert len(completion_calls) == 2
        assert completion_calls[0].kwargs["completion_tokens"] == 2
        assert completion_calls[1].kwargs["completion_tokens"] == 4
