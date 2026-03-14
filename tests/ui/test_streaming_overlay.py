"""Tests for the streaming overlay panel (WebView-based)."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_appkit(mock_appkit_modules):
    """Mock AppKit and Foundation modules for headless testing."""
    return mock_appkit_modules


@pytest.fixture(autouse=True)
def _mock_webkit(monkeypatch):
    """Mock WebKit and navigation delegate for headless testing."""
    import sys

    mock_webkit_mod = MagicMock()
    monkeypatch.setitem(sys.modules, "WebKit", mock_webkit_mod)

    mock_nav_cls = MagicMock()
    mock_nav_delegate = MagicMock()
    mock_nav_cls.alloc.return_value.init.return_value = mock_nav_delegate

    with patch(
        "voicetext.ui.streaming_overlay._get_nav_delegate_class",
        return_value=mock_nav_cls,
    ):
        yield


def _make_panel():
    from voicetext.ui.streaming_overlay import StreamingOverlayPanel

    return StreamingOverlayPanel()


def _show_and_load(panel, **kwargs):
    """Show a panel and simulate page load so JS calls work."""
    panel.show(**kwargs)
    # Simulate WKWebView finishing page load
    panel._on_page_loaded()
    return panel


class TestStreamingOverlayPanel:
    def test_initial_state(self):
        panel = _make_panel()
        assert panel._panel is None
        assert panel._webview is None
        assert panel._esc_monitor is None

    def test_show_creates_panel(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="hello")
        assert panel._panel is not None
        assert panel._webview is not None

    def test_show_loads_html_with_config(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="hello", stt_info="FunASR", llm_info="gpt-4o")
        assert panel._webview is not None
        # Verify loadHTMLString was called
        panel._webview.loadHTMLString_baseURL_.assert_called_once()
        html_arg = panel._webview.loadHTMLString_baseURL_.call_args[0][0]
        assert "FunASR" in html_arg
        assert "gpt-4o" in html_arg
        assert "hello" in html_arg

    def test_close_orders_out_panel(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        mock_ns_panel = panel._panel
        panel.close()
        mock_ns_panel.orderOut_.assert_called_with(None)
        assert panel._panel is None
        assert panel._webview is None

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

        handler = None

        def capture_handler(mask, h):
            nonlocal handler
            handler = h
            return MagicMock()

        mock_appkit_mod.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_ = (
            capture_handler
        )

        panel.show(asr_text="test", cancel_event=cancel_event)
        assert handler is not None

        mock_event = MagicMock()
        mock_event.keyCode.return_value = 53  # ESC
        handler(mock_event)
        assert cancel_event.is_set()

    def test_esc_handler_calls_on_cancel(self, _mock_appkit):
        """ESC should invoke on_cancel callback before closing."""
        mock_appkit_mod = _mock_appkit.appkit
        panel = _make_panel()
        on_cancel = MagicMock()

        handler = None

        def capture_handler(mask, h):
            nonlocal handler
            handler = h
            return MagicMock()

        mock_appkit_mod.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_ = (
            capture_handler
        )

        panel.show(asr_text="test", cancel_event=threading.Event(), on_cancel=on_cancel)
        mock_event = MagicMock()
        mock_event.keyCode.return_value = 53
        handler(mock_event)
        on_cancel.assert_called_once()

    def test_multiple_show_close_cycles(self, _mock_appkit):
        panel = _make_panel()
        for _ in range(3):
            panel.show(asr_text="test")
            assert panel._panel is not None
            panel.close()
            assert panel._panel is None

    def test_close_after_close_no_crash(self, _mock_appkit):
        panel = _make_panel()
        panel.close()
        panel.close()

    def test_append_text_after_close_no_crash(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.close()
        panel.append_text("more text")

    def test_set_status_after_close_no_crash(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.close()
        panel.set_status("done")

    def test_show_without_cancel_event(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        assert panel._panel is not None
        assert panel._cancel_event is None

    def test_show_with_animate_from_frame(self, _mock_appkit):
        mock_appkit_mod = _mock_appkit.appkit
        panel = _make_panel()
        mock_frame = MagicMock()
        panel.show(asr_text="test", animate_from_frame=mock_frame)
        assert panel._panel is not None
        panel._panel.setFrame_display_.assert_called_with(mock_frame, False)
        panel._panel.setAlphaValue_.assert_called_with(0.0)
        mock_appkit_mod.NSAnimationContext.beginGrouping.assert_called()

    def test_show_without_animate_from_frame(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        assert panel._panel is not None
        panel._panel.setFrameOrigin_.assert_called()
        panel._panel.setFrame_display_.assert_not_called()

    def test_show_positions_bottom_right(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel._panel.setFrameOrigin_.assert_called_once()

    def test_loading_timer_starts_on_show(self, _mock_appkit):
        mock_foundation = _mock_appkit.foundation
        panel = _make_panel()
        panel.show(asr_text="test")
        mock_foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_.assert_called()
        assert panel._loading_timer is not None

    def test_append_text_stops_loading_timer(self, _mock_appkit):
        panel = _make_panel()
        _show_and_load(panel, asr_text="test")
        mock_timer = panel._loading_timer
        panel.append_text("chunk")
        mock_timer.invalidate.assert_called()
        assert panel._loading_timer is None

    def test_close_stops_loading_timer(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        mock_timer = panel._loading_timer
        panel.close()
        mock_timer.invalidate.assert_called()

    def test_append_text_calls_eval_js(self, _mock_appkit):
        """append_text should evaluate JS via webview."""
        panel = _make_panel()
        _show_and_load(panel, asr_text="test")
        panel.append_text("hello ", completion_tokens=5)
        panel._webview.evaluateJavaScript_completionHandler_.assert_called()
        js_call = panel._webview.evaluateJavaScript_completionHandler_.call_args[0][0]
        assert "appendText" in js_call
        assert "hello " in js_call

    def test_append_thinking_text_calls_eval_js(self, _mock_appkit):
        """append_thinking_text should evaluate JS via webview."""
        panel = _make_panel()
        _show_and_load(panel, asr_text="test")
        panel.append_thinking_text("reasoning...", thinking_tokens=5)
        panel._webview.evaluateJavaScript_completionHandler_.assert_called()
        js_call = panel._webview.evaluateJavaScript_completionHandler_.call_args[0][0]
        assert "appendThinkingText" in js_call

    def test_set_status_calls_eval_js(self, _mock_appkit):
        """set_status should evaluate JS via webview."""
        panel = _make_panel()
        _show_and_load(panel, asr_text="test")
        panel.set_status("Step 1/2: Proofread")
        js_call = panel._webview.evaluateJavaScript_completionHandler_.call_args[0][0]
        assert "setStatus" in js_call
        assert "Step 1/2: Proofread" in js_call

    def test_set_asr_text_calls_eval_js(self, _mock_appkit):
        """set_asr_text should evaluate JS via webview."""
        panel = _make_panel()
        _show_and_load(panel, asr_text="")
        panel.set_asr_text("transcribed text")
        js_call = panel._webview.evaluateJavaScript_completionHandler_.call_args[0][0]
        assert "setAsrText" in js_call
        assert "transcribed text" in js_call

    def test_clear_text_calls_eval_js(self, _mock_appkit):
        """clear_text should evaluate JS via webview."""
        panel = _make_panel()
        _show_and_load(panel, asr_text="test")
        panel.clear_text()
        js_call = panel._webview.evaluateJavaScript_completionHandler_.call_args[0][0]
        assert "clearText" in js_call

    def test_set_complete_calls_eval_js(self, _mock_appkit):
        """set_complete should evaluate JS via webview."""
        panel = _make_panel()
        _show_and_load(panel, asr_text="test", llm_info="gpt-4o")
        usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        panel.set_complete(usage)
        js_call = panel._webview.evaluateJavaScript_completionHandler_.call_args[0][0]
        assert "setComplete" in js_call
        assert "150" in js_call

    def test_set_complete_without_usage(self, _mock_appkit):
        """set_complete without usage should pass null."""
        panel = _make_panel()
        _show_and_load(panel, asr_text="test")
        panel.set_complete(None)
        js_call = panel._webview.evaluateJavaScript_completionHandler_.call_args[0][0]
        assert "setComplete(null)" in js_call

    def test_pending_js_flushed_on_page_load(self, _mock_appkit):
        """JS calls before page load should be queued and flushed."""
        panel = _make_panel()
        panel.show(asr_text="test")
        assert not panel._page_loaded
        # Queue some calls before page load
        panel._eval_js("setStatus('hello')")
        panel._eval_js("setAsrText('world')")
        assert len(panel._pending_js) == 2
        # Simulate page load
        panel._on_page_loaded()
        assert panel._page_loaded
        assert len(panel._pending_js) == 0
        # Combined JS should have been executed
        panel._webview.evaluateJavaScript_completionHandler_.assert_called()

    def test_set_cancel_event_registers_esc(self, _mock_appkit):
        mock_appkit_mod = _mock_appkit.appkit
        panel = _make_panel()
        panel.show(asr_text="test")
        panel._esc_monitor = None
        cancel = threading.Event()
        panel.set_cancel_event(cancel)
        assert panel._cancel_event is cancel
        mock_appkit_mod.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_.assert_called()

    def test_set_asr_text_after_close_no_crash(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.close()
        panel.set_asr_text("new text")

    def test_close_with_delay_schedules_timer(self, _mock_appkit):
        mock_foundation = _mock_appkit.foundation
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.close_with_delay()
        mock_foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_.assert_called()
        assert panel._close_timer is not None

    def test_delayed_close_fires_when_mouse_outside(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        with patch(
            "voicetext.ui.streaming_overlay.StreamingOverlayPanel._is_mouse_over_panel",
            return_value=False,
        ), patch(
            "voicetext.ui.streaming_overlay.StreamingOverlayPanel._fade_out_and_close",
        ) as mock_fade:
            panel._delayedCloseCheck_(None)
            mock_fade.assert_called_once()

    def test_delayed_close_rechecks_when_mouse_hovering(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        with patch(
            "voicetext.ui.streaming_overlay.StreamingOverlayPanel._is_mouse_over_panel",
            return_value=True,
        ):
            panel._delayedCloseCheck_(None)
            assert panel._close_timer is not None

    def test_close_cancels_delayed_close(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.close_with_delay()
        mock_timer = panel._close_timer
        panel.close()
        mock_timer.invalidate.assert_called()
        assert panel._close_timer is None

    def test_close_with_delay_after_close_no_crash(self, _mock_appkit):
        panel = _make_panel()
        panel.show(asr_text="test")
        panel.close()
        panel.close_with_delay()

    def test_show_with_model_info_in_html(self, _mock_appkit):
        """show() with stt_info and llm_info should embed them in HTML config."""
        panel = _make_panel()
        panel.show(asr_text="test", stt_info="FunASR", llm_info="openai / gpt-4o")
        assert panel._llm_info == "openai / gpt-4o"
        html_arg = panel._webview.loadHTMLString_baseURL_.call_args[0][0]
        # Config should contain both model names
        assert "FunASR" in html_arg
        assert "openai / gpt-4o" in html_arg
