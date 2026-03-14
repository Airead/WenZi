"""Tests for the streaming overlay panel."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

@pytest.fixture(autouse=True)
def _mock_appkit(mock_appkit_modules):
    """Mock AppKit and Foundation modules for headless testing."""
    return mock_appkit_modules


def _make_panel():
    from voicetext.ui.streaming_overlay import StreamingOverlayPanel

    return StreamingOverlayPanel()


class TestStreamingOverlayPanel:
    def test_initial_state(self):
        panel = _make_panel()
        assert panel._panel is None
        assert panel._text_view is None
        assert panel._status_label is None
        assert panel._esc_monitor is None

    def test_show_creates_panel(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="你好")
        assert panel._panel is not None
        assert panel._text_view is not None
        assert panel._status_label is not None

    def test_show_sets_asr_text(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="你好世界")
        assert panel._asr_label is not None

    def test_append_text_calls_text_storage(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.append_text("hello ")
        panel._text_view.textStorage().appendAttributedString_.assert_called()

    def test_set_status_updates_label(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.set_status("Step 1/2: Proofread")
        panel._status_label.setStringValue_.assert_called_with("Step 1/2: Proofread")

    def test_close_orders_out_panel(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        mock_ns_panel = panel._panel
        panel.close()
        mock_ns_panel.orderOut_.assert_called_with(None)
        assert panel._panel is None
        assert panel._text_view is None
        assert panel._status_label is None

    def test_show_registers_esc_monitor(self, _mock_appkit):
        mock_appkit_mod = _mock_appkit.appkit
        panel = _make_panel()
        cancel_event = threading.Event()
        panel.show(asr_text="test", cancel_event=cancel_event)
        mock_appkit_mod.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_.assert_called()
        assert panel._esc_monitor is not None

    def test_close_removes_esc_monitor(self, _mock_appkit):
        mock_appkit_mod = _mock_appkit.appkit
        panel = _make_panel()
        panel.show(asr_text="test", cancel_event=threading.Event())
        panel.close()
        mock_appkit_mod.NSEvent.removeMonitor_.assert_called()
        assert panel._esc_monitor is None

    def test_esc_handler_sets_cancel_event(self, _mock_appkit):
        mock_appkit_mod = _mock_appkit.appkit
        panel = _make_panel()
        cancel_event = threading.Event()

        # Capture the handler passed to addGlobalMonitor
        handler = None

        def capture_handler(mask, h):
            nonlocal handler
            handler = h
            return MagicMock()  # monitor object

        mock_appkit_mod.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_ = (
            capture_handler
        )

        panel.show(asr_text="test", cancel_event=cancel_event)
        assert handler is not None

        # Simulate ESC key event
        mock_event = MagicMock()
        mock_event.keyCode.return_value = 53  # ESC
        handler(mock_event)
        assert cancel_event.is_set()

    def test_multiple_show_close_cycles(self, _mock_appkit):
        panel = _make_panel()
        for _ in range(3):
            panel.show(asr_text="test")
            assert panel._panel is not None
            panel.close()
            assert panel._panel is None

    def test_close_after_close_no_crash(self, _mock_appkit):
        panel = _make_panel()
        panel.close()  # close without show
        panel.close()  # double close

    def test_append_text_after_close_no_crash(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.close()
        # Should be a no-op, not raise
        panel.append_text("more text")

    def test_set_status_after_close_no_crash(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.close()
        # Should be a no-op, not raise
        panel.set_status("done")

    def test_clear_text(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.append_text("hello")
        panel.clear_text()
        panel._text_view.setString_.assert_called_with("")

    def test_show_without_cancel_event(self, _mock_appkit):
        """show() without cancel_event should still work."""
        panel = _make_panel()
        panel.show(asr_text="test")
        assert panel._panel is not None
        assert panel._cancel_event is None

    def test_show_with_model_info(self, _mock_appkit):
        """show() with stt_info and llm_info should include them in labels."""
        mock_appkit_mod = _mock_appkit.appkit
        panel = _make_panel()
        panel.show(asr_text="test", stt_info="FunASR", llm_info="openai / gpt-4o")
        assert panel._panel is not None
        assert panel._llm_info == "openai / gpt-4o"
        # ASR title should include STT model
        mock_appkit_mod.NSTextField.labelWithString_.assert_any_call(
            "\U0001f3a4 ASR  (FunASR)"
        )

    def test_show_with_animate_from_frame(self, _mock_appkit):
        """show() with animate_from_frame should use animation."""
        mock_appkit_mod = _mock_appkit.appkit
        panel = _make_panel()
        mock_frame = MagicMock()
        panel.show(asr_text="test", animate_from_frame=mock_frame)
        assert panel._panel is not None
        # Panel should start from the indicator frame
        panel._panel.setFrame_display_.assert_called_with(mock_frame, False)
        panel._panel.setAlphaValue_.assert_called_with(0.0)
        # Animation context should be used
        mock_appkit_mod.NSAnimationContext.beginGrouping.assert_called()

    def test_show_without_animate_from_frame(self, _mock_appkit):
        """show() without animate_from_frame should set position directly."""
        panel = _make_panel()
        panel.show(asr_text="test")
        assert panel._panel is not None
        panel._panel.setFrameOrigin_.assert_called()
        panel._panel.setFrame_display_.assert_not_called()

    def test_append_thinking_text(self, _mock_appkit):
        """append_thinking_text should append to text storage."""
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.append_thinking_text("reasoning...", thinking_tokens=5)
        panel._text_view.textStorage().appendAttributedString_.assert_called()

    def test_set_complete_with_usage(self, _mock_appkit):
        """set_complete should show final token usage."""
        panel = _make_panel()
        panel.show(asr_text="test", llm_info="openai / gpt-4o")
        usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        panel.set_complete(usage)
        panel._status_label.setStringValue_.assert_called_with(
            "\u2728 AI (openai / gpt-4o)  Tokens: 150 (\u2191100 \u219350)"
        )

    def test_set_complete_without_usage(self, _mock_appkit):
        """set_complete without usage should show AI label."""
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.set_complete(None)
        panel._status_label.setStringValue_.assert_called_with("\u2728 AI")

    def test_loading_timer_starts_on_show(self, _mock_appkit):
        """show() should start the loading timer."""
        mock_foundation = _mock_appkit.foundation
        panel = _make_panel()
        panel.show(asr_text="test")
        mock_foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_.assert_called()
        assert panel._loading_timer is not None

    def test_append_text_stops_loading_timer(self, _mock_appkit):
        """First append_text should stop the loading timer."""
        panel = _make_panel()
        panel.show(asr_text="test")
        mock_timer = panel._loading_timer
        panel.append_text("chunk")
        mock_timer.invalidate.assert_called()
        assert panel._loading_timer is None

    def test_close_stops_loading_timer(self, _mock_appkit):
        """close() should stop the loading timer."""
        panel = _make_panel()
        panel.show(asr_text="test")
        mock_timer = panel._loading_timer
        panel.close()
        mock_timer.invalidate.assert_called()

    def test_append_text_with_completion_tokens(self, _mock_appkit):
        """append_text with completion_tokens should update status."""
        panel = _make_panel()
        panel.show(asr_text="test", llm_info="openai / gpt-4o")
        panel.append_text("chunk", completion_tokens=10)
        panel._status_label.setStringValue_.assert_called_with(
            "\u2728 AI (openai / gpt-4o)  Tokens: \u219310"
        )

    def test_thinking_then_content_clears_text(self, _mock_appkit):
        """Content after thinking should clear the thinking text."""
        panel = _make_panel()
        panel.show(asr_text="test")
        # This tests the app-level logic, but we verify clear_text works
        panel.append_thinking_text("thinking...")
        panel.clear_text()
        panel._text_view.setString_.assert_called_with("")
        panel.append_text("result", completion_tokens=1)
        assert panel._text_view.textStorage().appendAttributedString_.call_count == 2
