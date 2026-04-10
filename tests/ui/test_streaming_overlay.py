"""Tests for the streaming overlay panel (native AppKit)."""

from __future__ import annotations

import sys
import threading
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_appkit(mock_appkit_modules):
    """Mock AppKit and Foundation modules for headless testing."""
    return mock_appkit_modules


@pytest.fixture(autouse=True)
def _mock_cgeventtap(monkeypatch):
    """Mock wenzi._cgeventtap for headless testing."""
    mock_cg = MagicMock()
    mock_cg.kCGEventKeyDown = 10
    mock_cg.kCGEventTapDisabledByTimeout = 0xFFFFFFFE
    mock_cg.kCGKeyboardEventKeycode = 9
    mock_cg.kCGSessionEventTap = 1
    mock_cg.kCGHeadInsertEventTap = 0
    mock_cg.kCGEventTapOptionDefault = 0
    mock_cg.kCFRunLoopDefaultMode = MagicMock(value="kCFRunLoopDefaultMode")
    mock_cg.CGEventMaskBit.side_effect = lambda t: 1 << t
    mock_cg.CGEventGetFlags.return_value = 0

    _runner_ready = threading.Event()

    class _MockRunner:
        def __init__(self):
            self.tap = MagicMock()
            self._callback = None

        def start(self, mask, callback, **kwargs):
            self._callback = callback
            _runner_ready.set()

        def stop(self):
            self.tap = None
            self._callback = None

        def wait_ready(self, timeout=2.0):
            pass

    mock_cg.CGEventTapRunner = _MockRunner

    monkeypatch.setattr("wenzi._cgeventtap", mock_cg, raising=False)
    monkeypatch.setitem(sys.modules, "wenzi._cgeventtap", mock_cg)

    class _CGHelper:
        cg = mock_cg

        @staticmethod
        def wait_for_tap(timeout=1.0):
            _runner_ready.wait(timeout)

        @staticmethod
        def simulate_key(panel, keycode):
            mock_event = 0xCAFE
            mock_cg.CGEventGetIntegerValueField.return_value = keycode
            return panel._key_tap_callback(
                None, mock_cg.kCGEventKeyDown, mock_event, None,
            )

    return _CGHelper()


def _make_panel():
    from wenzi.ui.streaming_overlay import StreamingOverlayPanel

    return StreamingOverlayPanel()


class TestStreamingOverlayPanel:
    def test_initial_state(self):
        panel = _make_panel()
        assert panel._panel is None
        assert panel._vfx_view is None
        assert panel._tap_runner is None
        assert panel._stream_text_view is None

    def test_show_creates_panel(self):
        panel = _make_panel()
        panel.show(asr_text="hello")
        assert panel._panel is not None
        assert panel._vfx_view is not None
        assert panel._asr_text_view is not None
        assert panel._stream_text_view is not None

    def test_show_sets_model_info(self):
        panel = _make_panel()
        panel.show(asr_text="test", stt_info="FunASR", llm_info="gpt-4o")
        assert panel._llm_info == "gpt-4o"

    def test_close_cleans_up(self):
        panel = _make_panel()
        panel.show(asr_text="test")
        mock_ns_panel = panel._panel
        panel.close()
        mock_ns_panel.orderOut_.assert_called_with(None)
        assert panel._panel is None
        assert panel._vfx_view is None
        assert panel._stream_text_view is None

    def test_show_registers_key_tap(self, _mock_cgeventtap):
        panel = _make_panel()
        panel.show(asr_text="test", cancel_event=threading.Event())
        _mock_cgeventtap.wait_for_tap()
        assert panel._tap_runner is not None

    def test_close_removes_key_tap(self, _mock_cgeventtap):
        panel = _make_panel()
        panel.show(asr_text="test", cancel_event=threading.Event())
        _mock_cgeventtap.wait_for_tap()
        panel.close()
        assert panel._tap_runner is None

    def test_esc_sets_cancel_event(self, _mock_cgeventtap):
        panel = _make_panel()
        cancel_event = threading.Event()
        panel.show(asr_text="test", cancel_event=cancel_event)
        _mock_cgeventtap.wait_for_tap()
        result = _mock_cgeventtap.simulate_key(panel, 53)
        assert result is None
        assert cancel_event.is_set()

    def test_esc_calls_on_cancel(self, _mock_cgeventtap):
        panel = _make_panel()
        on_cancel = MagicMock()
        panel.show(asr_text="test", cancel_event=threading.Event(), on_cancel=on_cancel)
        _mock_cgeventtap.wait_for_tap()
        _mock_cgeventtap.simulate_key(panel, 53)
        on_cancel.assert_called_once()

    def test_enter_calls_on_confirm_asr(self, _mock_cgeventtap):
        panel = _make_panel()
        on_confirm = MagicMock()
        panel.show(asr_text="test", on_confirm_asr=on_confirm)
        _mock_cgeventtap.wait_for_tap()
        result = _mock_cgeventtap.simulate_key(panel, 36)
        assert result is None
        on_confirm.assert_called_once()

    def test_enter_closes_panel(self, _mock_cgeventtap):
        panel = _make_panel()
        panel.show(asr_text="test", on_confirm_asr=MagicMock())
        _mock_cgeventtap.wait_for_tap()
        _mock_cgeventtap.simulate_key(panel, 36)
        assert panel._panel is None

    def test_enter_passes_through_without_callback(self, _mock_cgeventtap):
        panel = _make_panel()
        panel.show(asr_text="test")
        _mock_cgeventtap.wait_for_tap()
        result = _mock_cgeventtap.simulate_key(panel, 36)
        assert result is not None

    def test_other_keys_not_swallowed(self, _mock_cgeventtap):
        panel = _make_panel()
        panel.show(asr_text="test")
        _mock_cgeventtap.wait_for_tap()
        result = _mock_cgeventtap.simulate_key(panel, 0)
        assert result is not None

    def test_multiple_show_close_cycles(self):
        panel = _make_panel()
        for _ in range(3):
            panel.show(asr_text="test")
            assert panel._panel is not None
            panel.close()
            assert panel._panel is None

    def test_close_after_close_no_crash(self):
        panel = _make_panel()
        panel.close()
        panel.close()

    def test_append_text_after_close_no_crash(self):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.close()
        panel.append_text("more text")

    def test_set_status_after_close_no_crash(self):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.close()
        panel.set_status("done")

    def test_set_asr_text_after_close_no_crash(self):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.close()
        panel.set_asr_text("new text")

    def test_show_without_cancel_event(self):
        panel = _make_panel()
        panel.show(asr_text="test")
        assert panel._panel is not None
        assert panel._cancel_event is None

    def test_show_with_animate_from_frame(self):
        panel = _make_panel()
        mock_frame = MagicMock()
        panel.show(asr_text="test", animate_from_frame=mock_frame)
        assert panel._panel is not None
        # Panel should be created and shown regardless of animate_from_frame
        panel._panel.setFrame_display_.assert_called()

    def test_show_positions_panel(self):
        panel = _make_panel()
        panel.show(asr_text="test")
        assert panel._panel is not None
        panel._panel.setFrame_display_.assert_called()

    def test_loading_timer_starts_on_show(self, _mock_appkit):
        mock_foundation = _mock_appkit.foundation
        panel = _make_panel()
        panel.show(asr_text="test")
        mock_foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_.assert_called()
        assert panel._loading_timer is not None

    def test_close_stops_loading_timer(self):
        panel = _make_panel()
        panel.show(asr_text="test")
        mock_timer = panel._loading_timer
        panel.close()
        mock_timer.invalidate.assert_called()

    def test_set_cancel_event_registers_tap(self, _mock_cgeventtap):
        panel = _make_panel()
        panel.show(asr_text="test")
        _mock_cgeventtap.wait_for_tap()
        panel._tap_runner = None
        cancel = threading.Event()
        panel.set_cancel_event(cancel)
        _mock_cgeventtap.wait_for_tap()
        assert panel._cancel_event is cancel
        assert panel._tap_runner is not None

    def test_close_with_delay_schedules_timer(self, _mock_appkit):
        mock_foundation = _mock_appkit.foundation
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.close_with_delay()
        mock_foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_.assert_called()
        assert panel._close_timer is not None

    def test_delayed_close_fires_when_mouse_outside(self):
        panel = _make_panel()
        panel.show(asr_text="test")
        with patch(
            "wenzi.ui.streaming_overlay.StreamingOverlayPanel._is_mouse_over_panel",
            return_value=False,
        ), patch(
            "wenzi.ui.streaming_overlay.StreamingOverlayPanel._fade_out_and_close",
        ) as mock_fade:
            panel._delayedCloseCheck_(None)
            mock_fade.assert_called_once()

    def test_delayed_close_rechecks_when_hovering(self):
        panel = _make_panel()
        panel.show(asr_text="test")
        with patch(
            "wenzi.ui.streaming_overlay.StreamingOverlayPanel._is_mouse_over_panel",
            return_value=True,
        ):
            panel._delayedCloseCheck_(None)
            assert panel._close_timer is not None

    def test_close_cancels_delayed_close(self):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.close_with_delay()
        mock_timer = panel._close_timer
        panel.close()
        mock_timer.invalidate.assert_called()
        assert panel._close_timer is None

    def test_close_with_delay_after_close_no_crash(self):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.close()
        panel.close_with_delay()

    def test_ai_label_with_llm_info(self):
        panel = _make_panel()
        panel._llm_info = "gpt-4o"
        assert panel._ai_label("") == "\u2728 AI (gpt-4o)"
        assert panel._ai_label("\u23f3 3s") == "\u2728 AI (gpt-4o)  \u23f3 3s"

    def test_ai_label_without_llm_info(self):
        panel = _make_panel()
        assert panel._ai_label("") == "\u2728 AI"

    def test_complete_flag_set_by_set_complete(self):
        panel = _make_panel()
        panel.show(asr_text="test")
        assert not panel._complete
        panel.set_complete({"total_tokens": 100, "prompt_tokens": 50, "completion_tokens": 50})
        assert panel._complete

    def test_any_key_closes_when_complete(self, _mock_cgeventtap):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel._complete = True
        _mock_cgeventtap.wait_for_tap()
        result = _mock_cgeventtap.simulate_key(panel, 0)  # random key
        assert result is not None  # key passes through (not swallowed)

    def test_hint_label_created(self):
        panel = _make_panel()
        panel.show(asr_text="test", on_confirm_asr=lambda: None)
        assert panel._hint_label is not None

    def test_hint_label_without_confirm(self):
        panel = _make_panel()
        panel.show(asr_text="test")
        assert panel._hint_label is not None

    @patch("wenzi.input.set_clipboard_text")
    def test_copy_cmd_c(self, mock_set_cb, _mock_cgeventtap):
        panel = _make_panel()
        panel.show(asr_text="test")
        _mock_cgeventtap.wait_for_tap()
        _mock_cgeventtap.cg.CGEventGetFlags.return_value = 1 << 20  # Cmd
        _mock_cgeventtap.cg.CGEventGetIntegerValueField.return_value = 8  # C key
        mock_event = 0xCAFE
        result = panel._key_tap_callback(
            None, _mock_cgeventtap.cg.kCGEventKeyDown, mock_event, None,
        )
        assert result is None  # swallowed

    def test_progress_bar_created(self):
        panel = _make_panel()
        panel.show(asr_text="test")
        assert panel._progress_view is not None

    def test_close_delay_constant(self):
        from wenzi.ui.streaming_overlay import _CLOSE_DELAY
        assert _CLOSE_DELAY == 3.0

    def test_font_size_constant(self):
        from wenzi.ui.streaming_overlay import _FONT_SIZE
        assert _FONT_SIZE == 15.6
