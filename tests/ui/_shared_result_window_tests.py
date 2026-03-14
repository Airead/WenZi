"""Shared behavioral tests for ResultPreviewPanel implementations.

Both the native AppKit and WKWebView-based preview panels must satisfy
these tests.  Each implementation's test file provides a ``panel_factory``
fixture that returns a ready-to-test panel instance with mocked internals.

The fixture must return an object with:
  - panel: the ResultPreviewPanel instance
  - trigger_confirm(text, user_edited=False, enhance_text="", copy=False):
        simulate user clicking Confirm
  - trigger_cancel(): simulate user clicking Cancel
  - trigger_mode_change(index): simulate user switching mode segment
  - trigger_stt_change(index): simulate user changing STT model
  - trigger_llm_change(index): simulate user changing LLM model
  - trigger_punc_toggle(enabled): simulate user toggling Punc checkbox
  - trigger_thinking_toggle(enabled): simulate user toggling Thinking checkbox
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Show / state initialization
# ---------------------------------------------------------------------------


class SharedShowTests:
    """Test show() initializes state correctly."""

    def test_show_stores_callbacks(self, panel_factory):
        pf = panel_factory()
        on_confirm = MagicMock()
        on_cancel = MagicMock()
        pf.panel.show(
            asr_text="hello", show_enhance=False,
            on_confirm=on_confirm, on_cancel=on_cancel,
        )
        assert pf.panel._on_confirm is on_confirm
        assert pf.panel._on_cancel is on_cancel
        assert pf.panel._asr_text == "hello"

    def test_show_stores_modes(self, panel_factory):
        pf = panel_factory()
        modes = [("off", "Off"), ("proofread", "纠错")]
        pf.panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
            available_modes=modes, current_mode="proofread",
        )
        assert pf.panel._available_modes == modes
        assert pf.panel._current_mode == "proofread"

    def test_show_stores_model_lists(self, panel_factory):
        pf = panel_factory()
        pf.panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
            stt_models=["whisper", "funASR"], stt_current_index=1,
            llm_models=["gpt-4", "claude"], llm_current_index=0,
        )
        assert pf.panel._stt_models == ["whisper", "funASR"]
        assert pf.panel._stt_current_index == 1
        assert pf.panel._llm_models == ["gpt-4", "claude"]
        assert pf.panel._llm_current_index == 0

    def test_show_defaults_without_modes(self, panel_factory):
        pf = panel_factory()
        pf.panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        assert pf.panel._available_modes == []
        assert pf.panel._current_mode == "off"
        assert pf.panel._on_mode_change is None

    def test_show_resets_user_edited(self, panel_factory):
        pf = panel_factory()
        pf.panel._user_edited = True
        pf.panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        assert pf.panel._user_edited is False

    def test_show_enhance_flag_stored(self, panel_factory):
        pf = panel_factory()
        pf.panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        assert pf.panel._show_enhance is True

        pf2 = panel_factory()
        pf2.panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        assert pf2.panel._show_enhance is False


# ---------------------------------------------------------------------------
# Confirm / Cancel
# ---------------------------------------------------------------------------


class SharedConfirmCancelTests:
    """Test confirm and cancel callbacks."""

    def test_confirm_triggers_callback_with_text(self, panel_factory):
        pf = panel_factory()
        confirmed = []
        pf.panel.show(
            asr_text="raw", show_enhance=False,
            on_confirm=lambda t, info=None, clipboard=False: confirmed.append(t),
            on_cancel=MagicMock(),
        )
        pf.trigger_confirm("final text")
        assert confirmed == ["final text"]

    def test_confirm_with_copy_to_clipboard(self, panel_factory):
        pf = panel_factory()
        flags = []
        pf.panel.show(
            asr_text="raw", show_enhance=False,
            on_confirm=lambda t, info=None, clipboard=False: flags.append(clipboard),
            on_cancel=MagicMock(),
        )
        pf.trigger_confirm("text", copy=True)
        assert flags == [True]

    def test_confirm_correction_info_when_user_edited(self, panel_factory):
        pf = panel_factory()
        results = []
        pf.panel.show(
            asr_text="raw asr", show_enhance=True,
            on_confirm=lambda t, info=None, clipboard=False: results.append(info),
            on_cancel=MagicMock(),
        )
        pf.trigger_confirm(
            "user modified", user_edited=True, enhance_text="enhanced",
        )
        assert results[0] is not None
        assert results[0]["asr_text"] == "raw asr"
        assert results[0]["enhanced_text"] == "enhanced"
        assert results[0]["final_text"] == "user modified"

    def test_confirm_correction_info_none_when_not_edited(self, panel_factory):
        pf = panel_factory()
        results = []
        pf.panel.show(
            asr_text="raw", show_enhance=True,
            on_confirm=lambda t, info=None, clipboard=False: results.append(info),
            on_cancel=MagicMock(),
        )
        pf.trigger_confirm("text", user_edited=False)
        assert results[0] is None

    def test_cancel_triggers_callback(self, panel_factory):
        pf = panel_factory()
        cancelled = []
        pf.panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=lambda: cancelled.append(True),
        )
        pf.trigger_cancel()
        assert cancelled == [True]

    def test_confirm_closes_panel(self, panel_factory):
        pf = panel_factory()
        pf.panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        pf.trigger_confirm("text")
        assert pf.panel._panel is None

    def test_cancel_closes_panel(self, panel_factory):
        pf = panel_factory()
        pf.panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        pf.trigger_cancel()
        assert pf.panel._panel is None


# ---------------------------------------------------------------------------
# Mode switching
# ---------------------------------------------------------------------------


class SharedModeSwitchTests:
    """Test mode switching."""

    def test_mode_change_triggers_callback(self, panel_factory):
        pf = panel_factory()
        changes = []
        modes = [("off", "Off"), ("proofread", "纠错"), ("format", "格式")]
        pf.panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
            available_modes=modes, current_mode="off",
            on_mode_change=lambda m: changes.append(m),
        )
        pf.trigger_mode_change(1)
        assert changes == ["proofread"]
        assert pf.panel._current_mode == "proofread"

    def test_same_mode_does_not_trigger_callback(self, panel_factory):
        pf = panel_factory()
        changes = []
        modes = [("off", "Off"), ("proofread", "纠错")]
        pf.panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
            available_modes=modes, current_mode="off",
            on_mode_change=lambda m: changes.append(m),
        )
        pf.trigger_mode_change(0)
        assert changes == []


# ---------------------------------------------------------------------------
# STT / LLM model switching
# ---------------------------------------------------------------------------


class SharedModelChangeTests:
    """Test STT and LLM model switching."""

    def test_stt_model_change(self, panel_factory):
        pf = panel_factory()
        changes = []
        pf.panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
            stt_models=["a", "b"],
            on_stt_model_change=lambda i: changes.append(i),
        )
        pf.trigger_stt_change(1)
        assert changes == [1]

    def test_llm_model_change(self, panel_factory):
        pf = panel_factory()
        changes = []
        pf.panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
            llm_models=["gpt-4", "claude"],
            on_llm_model_change=lambda i: changes.append(i),
        )
        pf.trigger_llm_change(0)
        assert changes == [0]


# ---------------------------------------------------------------------------
# Toggle callbacks
# ---------------------------------------------------------------------------


class SharedToggleTests:
    """Test punc and thinking toggle callbacks."""

    def test_punc_toggle(self, panel_factory):
        pf = panel_factory()
        toggles = []
        pf.panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
            on_punc_toggle=lambda v: toggles.append(v),
        )
        pf.trigger_punc_toggle(False)
        assert toggles == [False]
        assert pf.panel._punc_enabled is False

    def test_thinking_toggle(self, panel_factory):
        pf = panel_factory()
        toggles = []
        pf.panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
            on_thinking_toggle=lambda v: toggles.append(v),
        )
        pf.trigger_thinking_toggle(True)
        assert toggles == [True]
        assert pf.panel._thinking_enabled is True


# ---------------------------------------------------------------------------
# Streaming enhancement
# ---------------------------------------------------------------------------


class SharedStreamingTests:
    """Test streaming enhancement updates."""

    def test_append_thinking_text_accumulates(self, panel_factory):
        from unittest.mock import patch
        pf = panel_factory()
        pf.panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        with patch("PyObjCTools.AppHelper") as mh:
            mh.callAfter.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
            pf.panel.append_thinking_text("think1 ")
            pf.panel.append_thinking_text("think2")
        assert pf.panel._thinking_text == "think1 think2"

    def test_stale_request_id_discarded(self, panel_factory):
        """Chunks with old request_id should not update internal asr_text."""
        from unittest.mock import patch
        pf = panel_factory()
        pf.panel.show(
            asr_text="original", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        pf.panel._asr_request_id = 5
        with patch("PyObjCTools.AppHelper") as mh:
            mh.callAfter.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
            pf.panel.set_asr_result("stale", request_id=3)
        assert pf.panel._asr_text == "original"

    def test_set_asr_result_updates_text(self, panel_factory):
        from unittest.mock import patch
        pf = panel_factory()
        pf.panel.show(
            asr_text="old", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        with patch("PyObjCTools.AppHelper") as mh:
            mh.callAfter.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
            pf.panel.set_asr_result("new text", "model info")
        assert pf.panel._asr_text == "new text"
        assert pf.panel._asr_info == "model info"

    def test_asr_loading_increments_request_id(self, panel_factory):
        from unittest.mock import patch
        pf = panel_factory()
        pf.panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        initial = pf.panel.asr_request_id
        with patch("PyObjCTools.AppHelper") as mh:
            mh.callAfter.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
            pf.panel.set_asr_loading()
        assert pf.panel.asr_request_id == initial + 1

    def test_set_enhance_off_clears_show_enhance(self, panel_factory):
        from unittest.mock import patch
        pf = panel_factory()
        pf.panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        with patch("PyObjCTools.AppHelper") as mh:
            mh.callAfter.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
            pf.panel.set_enhance_off()
        assert pf.panel._show_enhance is False

    def test_set_enhance_loading_resets_user_edited(self, panel_factory):
        from unittest.mock import patch
        pf = panel_factory()
        pf.panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        pf.panel._user_edited = True
        with patch("PyObjCTools.AppHelper") as mh:
            mh.callAfter.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
            pf.panel.set_enhance_loading()
        assert pf.panel._user_edited is False

    def test_set_enhance_loading_clears_thinking_text(self, panel_factory):
        from unittest.mock import patch
        pf = panel_factory()
        pf.panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        pf.panel._thinking_text = "old thinking"
        with patch("PyObjCTools.AppHelper") as mh:
            mh.callAfter.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
            pf.panel.set_enhance_loading()
        assert pf.panel._thinking_text == ""


# ---------------------------------------------------------------------------
# Properties and lifecycle
# ---------------------------------------------------------------------------


class SharedPropertyTests:
    """Test properties and visibility."""

    def test_is_visible_false_when_no_panel(self, panel_factory):
        pf = panel_factory()
        pf.panel._panel = None  # Ensure no panel
        assert pf.panel.is_visible is False

    def test_is_visible_true_when_visible(self, panel_factory):
        pf = panel_factory()
        pf.panel._panel = MagicMock()
        pf.panel._panel.isVisible.return_value = True
        assert pf.panel.is_visible is True

    def test_enhance_request_id_property(self, panel_factory):
        pf = panel_factory()
        pf.panel.enhance_request_id = 42
        assert pf.panel.enhance_request_id == 42

    def test_close_clears_state(self, panel_factory):
        pf = panel_factory()
        pf.panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        pf.panel.close()
        assert pf.panel._panel is None
        assert pf.panel._on_confirm is None
        assert pf.panel._on_cancel is None

    def test_close_without_show_is_noop(self, panel_factory):
        pf = panel_factory()
        pf.panel.close()  # Should not raise


# ---------------------------------------------------------------------------
# Threading
# ---------------------------------------------------------------------------


class SharedThreadingTests:
    """Test callback/event patterns work across threads."""

    def test_confirm_unblocks_waiting_thread(self, panel_factory):
        pf = panel_factory()
        event = threading.Event()
        result_holder = {"text": None}

        def on_confirm(text, correction_info=None, clipboard=False):
            result_holder["text"] = text
            event.set()

        pf.panel.show(
            asr_text="asr", show_enhance=False,
            on_confirm=on_confirm, on_cancel=lambda: event.set(),
        )
        pf.trigger_confirm("result")
        assert event.wait(timeout=1)
        assert result_holder["text"] == "result"

    def test_cancel_unblocks_waiting_thread(self, panel_factory):
        pf = panel_factory()
        event = threading.Event()
        cancelled = []

        def on_cancel():
            cancelled.append(True)
            event.set()

        pf.panel.show(
            asr_text="asr", show_enhance=False,
            on_confirm=lambda t, info=None, clipboard=False: event.set(),
            on_cancel=on_cancel,
        )
        pf.trigger_cancel()
        assert event.wait(timeout=1)
        assert cancelled == [True]


# ---------------------------------------------------------------------------
# Enhance label text helper
# ---------------------------------------------------------------------------


class SharedEnhanceLabelTests:
    """Test _enhance_label_text helper."""

    def test_with_llm_models_returns_suffix_only(self, panel_factory):
        pf = panel_factory()
        pf.panel._llm_models = ["gpt-4"]
        assert pf.panel._enhance_label_text("Tokens: 100") == "Tokens: 100"

    def test_without_llm_models_includes_ai_prefix(self, panel_factory):
        pf = panel_factory()
        pf.panel._enhance_info = "openai/gpt-4"
        assert pf.panel._enhance_label_text("ok") == "AI (openai/gpt-4)  ok"

    def test_empty_suffix(self, panel_factory):
        pf = panel_factory()
        assert pf.panel._enhance_label_text() == "AI"


# ---------------------------------------------------------------------------
# Replay cached result
# ---------------------------------------------------------------------------


class SharedReplayCachedTests:
    """Test replay_cached_result stores state."""

    def test_replay_stores_state(self, panel_factory):
        from unittest.mock import patch
        pf = panel_factory()
        pf.panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        with patch("PyObjCTools.AppHelper") as mh:
            mh.callAfter.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
            pf.panel.replay_cached_result(
                display_text="cached",
                usage={"total_tokens": 50, "prompt_tokens": 30, "completion_tokens": 20},
                system_prompt="sys prompt",
                thinking_text="thought",
                final_text="final",
            )
        assert pf.panel._system_prompt == "sys prompt"
        assert pf.panel._thinking_text == "thought"
