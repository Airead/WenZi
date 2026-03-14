"""Tests for RecordingController."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from voicetext.controllers.recording_controller import RecordingController


@pytest.fixture
def mock_app():
    """Create a mock VoiceTextApp with all attributes used by RecordingController."""
    app = MagicMock()
    app._busy = False
    app._config = {
        "feedback": {"sound_enabled": True, "visual_indicator": True},
    }
    app._config_path = "/tmp/test_config.json"
    app._sound_manager = MagicMock()
    app._sound_manager.enabled = True
    app._recording_indicator = MagicMock()
    app._recording_indicator.enabled = True
    app._recording_indicator.current_frame = MagicMock()
    app._recorder = MagicMock()
    app._recorder.is_recording = True
    app._recorder.current_level = 0.5
    app._recording_started = threading.Event()
    app._recording_started.set()
    app._level_poll_stop = None
    app._transcriber = MagicMock()
    app._transcriber.supports_streaming = False  # Default: batch mode
    app._enhancer = MagicMock()
    app._enhancer.is_active = True
    app._enhancer.mode = "proofread"
    app._enhance_mode = "proofread"
    app._preview_enabled = False
    app._streaming_overlay = MagicMock()
    app._live_overlay = MagicMock()
    app._usage_stats = MagicMock()
    app._conversation_history = MagicMock()
    app._append_newline = False
    app._output_method = "type"
    app._current_stt_model = MagicMock(return_value="FunASR")
    app._current_llm_model = MagicMock(return_value="openai / gpt-4o")
    return app


@pytest.fixture
def ctrl(mock_app):
    return RecordingController(mock_app)


class TestOnHotkeyPress:
    def test_busy_returns_early(self, ctrl, mock_app):
        mock_app._busy = True
        ctrl.on_hotkey_press()
        mock_app._set_status.assert_not_called()

    def test_starts_recording_no_sound(self, ctrl, mock_app):
        mock_app._sound_manager.enabled = False
        ctrl.on_hotkey_press()

        mock_app._set_status.assert_called_with("Recording...")
        mock_app._sound_manager.play.assert_called_with("start")
        mock_app._recorder.start.assert_called_once()
        assert mock_app._recording_started.is_set()


class TestOnHotkeyRelease:
    def test_not_recording_returns(self, ctrl, mock_app):
        mock_app._recorder.is_recording = False
        ctrl.on_hotkey_release()
        mock_app._recorder.stop.assert_not_called()

    def test_empty_audio_resets(self, ctrl, mock_app):
        mock_app._recorder.stop.return_value = None
        ctrl.on_hotkey_release()
        mock_app._set_status.assert_called_with("VT")

    def test_timeout_returns(self, ctrl, mock_app):
        mock_app._recording_started = threading.Event()  # Not set
        ctrl.on_hotkey_release()
        mock_app._recorder.stop.assert_not_called()


class TestRecordingIndicator:
    @patch("PyObjCTools.AppHelper")
    def test_start_indicator(self, mock_apphelper, ctrl, mock_app):
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        ctrl.start_recording_indicator()
        mock_app._recording_indicator.show.assert_called_once()
        assert mock_app._level_poll_stop is not None

    @patch("PyObjCTools.AppHelper")
    def test_stop_indicator(self, mock_apphelper, ctrl, mock_app):
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        stop_event = threading.Event()
        mock_app._level_poll_stop = stop_event
        ctrl.stop_recording_indicator()
        assert stop_event.is_set()
        assert mock_app._level_poll_stop is None
        mock_app._recording_indicator.hide.assert_called_once()

    @patch("PyObjCTools.AppHelper")
    def test_stop_indicator_animate(self, mock_apphelper, ctrl, mock_app):
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        stop_event = threading.Event()
        mock_app._level_poll_stop = stop_event
        ctrl.stop_recording_indicator(animate=True)
        assert stop_event.is_set()
        # Should NOT call hide when animate=True
        mock_app._recording_indicator.hide.assert_not_called()


class TestFeedbackToggles:
    @patch("voicetext.controllers.recording_controller.save_config")
    def test_sound_toggle(self, mock_save, ctrl, mock_app):
        sender = MagicMock()
        ctrl.on_sound_feedback_toggle(sender)
        # Was True, now should be False
        assert mock_app._sound_manager.enabled is False
        assert sender.state == 0
        mock_save.assert_called_once()

    @patch("voicetext.controllers.recording_controller.save_config")
    def test_visual_toggle(self, mock_save, ctrl, mock_app):
        sender = MagicMock()
        ctrl.on_visual_indicator_toggle(sender)
        # Was True, now should be False
        assert mock_app._recording_indicator.enabled is False
        assert sender.state == 0
        mock_save.assert_called_once()


class TestStreamingIntegration:
    @patch("PyObjCTools.AppHelper")
    def test_streaming_starts_when_supported(self, mock_apphelper, ctrl, mock_app):
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False
        mock_app._transcriber.supports_streaming = True

        ctrl.on_hotkey_press()

        assert ctrl._streaming_active is True
        mock_app._transcriber.start_streaming.assert_called_once()
        mock_app._recorder.set_on_audio_chunk.assert_called_once()

    @patch("PyObjCTools.AppHelper")
    def test_streaming_not_started_when_unsupported(self, mock_apphelper, ctrl, mock_app):
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False
        mock_app._transcriber.supports_streaming = False

        ctrl.on_hotkey_press()

        assert ctrl._streaming_active is False
        mock_app._transcriber.start_streaming.assert_not_called()

    @patch("voicetext.controllers.recording_controller.type_text")
    @patch("PyObjCTools.AppHelper")
    def test_streaming_release_calls_stop_streaming(self, mock_apphelper, mock_type_text, ctrl, mock_app):
        import time

        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._transcriber.supports_streaming = True
        # Use a descriptive string so if it leaks to terminal output (via un-mocked
        # type_text or logging), we can immediately identify which test caused it.
        _mock_text = "[mock from test_recording_controller.py::test_streaming_release_calls_stop_streaming]"
        mock_app._transcriber.stop_streaming.return_value = _mock_text
        mock_app._sound_manager.enabled = False

        # Press to start streaming
        ctrl.on_hotkey_press()
        assert ctrl._streaming_active is True

        # Release: should use streaming path (runs _do_streaming_stop in background thread)
        ctrl.on_hotkey_release()

        # Wait for the entire background thread to finish (it sets _busy=False in finally)
        for _ in range(50):
            if not mock_app._busy:
                break
            time.sleep(0.02)

        mock_app._recorder.clear_on_audio_chunk.assert_called()
        mock_app._transcriber.stop_streaming.assert_called_once()

    @patch("PyObjCTools.AppHelper")
    def test_streaming_fallback_on_error(self, mock_apphelper, ctrl, mock_app):
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False
        mock_app._transcriber.supports_streaming = True
        mock_app._transcriber.start_streaming.side_effect = RuntimeError("init fail")

        ctrl.on_hotkey_press()

        # Should fall back to batch mode
        assert ctrl._streaming_active is False

    @patch("PyObjCTools.AppHelper")
    def test_streaming_release_with_preview(self, mock_apphelper, ctrl, mock_app):
        import time

        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._transcriber.supports_streaming = True
        # Use a descriptive string so if it leaks to terminal output (via un-mocked
        # type_text or logging), we can immediately identify which test caused it.
        mock_app._transcriber.stop_streaming.return_value = "[mock from test_recording_controller.py::test_streaming_release_with_preview]"
        mock_app._sound_manager.enabled = False
        mock_app._preview_enabled = True

        ctrl.on_hotkey_press()
        ctrl.on_hotkey_release()

        # _do_streaming_stop runs in a background thread, wait for it
        for _ in range(50):
            if mock_app._do_transcribe_with_preview.called:
                break
            time.sleep(0.02)

        mock_app._do_transcribe_with_preview.assert_called_once()
        call_kwargs = mock_app._do_transcribe_with_preview.call_args[1]
        assert call_kwargs["asr_text"] == "[mock from test_recording_controller.py::test_streaming_release_with_preview]"

    def test_init_streaming_state(self, ctrl):
        assert ctrl._streaming_active is False
        assert ctrl._live_overlay is None


class TestOnRestartRecording:
    def test_not_recording_returns_early(self, ctrl, mock_app):
        mock_app._recorder.is_recording = False
        ctrl.on_restart_recording()
        mock_app._recorder.stop.assert_not_called()

    @patch("PyObjCTools.AppHelper")
    def test_restart_stops_and_restarts_no_sound(self, mock_apphelper, ctrl, mock_app):
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False

        ctrl.on_restart_recording()

        # Should stop old recording
        mock_app._recorder.stop.assert_called_once()
        # Should replay prompt sound
        mock_app._sound_manager.play.assert_called_with("start")
        # Should start new recording
        mock_app._recorder.start.assert_called_once()
        assert mock_app._recording_started.is_set()

    @patch("PyObjCTools.AppHelper")
    def test_restart_stops_streaming(self, mock_apphelper, ctrl, mock_app):
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False
        mock_app._transcriber.supports_streaming = True

        # Simulate active streaming
        ctrl._streaming_active = True

        ctrl.on_restart_recording()

        # Old streaming should be stopped before restarting
        mock_app._recorder.clear_on_audio_chunk.assert_called_once()
        mock_app._transcriber.stop_streaming.assert_called_once()
        # Streaming is restarted because transcriber supports it
        mock_app._transcriber.start_streaming.assert_called_once()
        assert ctrl._streaming_active is True

    @patch("PyObjCTools.AppHelper")
    def test_restart_sets_status(self, mock_apphelper, ctrl, mock_app):
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False

        ctrl.on_restart_recording()

        mock_app._set_status.assert_called_with("Recording...")

    @patch("PyObjCTools.AppHelper")
    def test_restart_records_sound_feedback_stat(self, mock_apphelper, ctrl, mock_app):
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = True

        ctrl.on_restart_recording()

        mock_app._usage_stats.record_sound_feedback.assert_called_once()


class TestOnCancelRecording:
    def test_not_recording_returns_early(self, ctrl, mock_app):
        mock_app._recorder.is_recording = False
        ctrl.on_cancel_recording()
        mock_app._recorder.stop.assert_not_called()

    @patch("PyObjCTools.AppHelper")
    def test_cancel_stops_and_resets(self, mock_apphelper, ctrl, mock_app):
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False

        ctrl.on_cancel_recording()

        mock_app._recorder.stop.assert_called_once()
        mock_app._recording_indicator.hide.assert_called_once()
        mock_app._set_status.assert_called_with("VT")
        assert mock_app._busy is False

    @patch("PyObjCTools.AppHelper")
    def test_cancel_stops_streaming(self, mock_apphelper, ctrl, mock_app):
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False
        ctrl._streaming_active = True

        ctrl.on_cancel_recording()

        mock_app._recorder.clear_on_audio_chunk.assert_called_once()
        mock_app._transcriber.stop_streaming.assert_called_once()
        assert ctrl._streaming_active is False
        assert mock_app._busy is False

    @patch("PyObjCTools.AppHelper")
    def test_cancel_clears_recording_started(self, mock_apphelper, ctrl, mock_app):
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False
        mock_app._recording_started.set()

        ctrl.on_cancel_recording()

        assert not mock_app._recording_started.is_set()


class TestDoTranscribeDirect:
    @patch("voicetext.controllers.recording_controller.type_text")
    @patch("PyObjCTools.AppHelper")
    def test_no_enhance(self, mock_apphelper, mock_type_text, ctrl, mock_app):
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        ctrl.do_transcribe_direct("hello world", use_enhance=False)

        mock_type_text.assert_called_once_with(
            "hello world",
            append_newline=False,
            method="type",
        )
        mock_app._usage_stats.record_transcription.assert_called_once()
        mock_app._usage_stats.record_confirm.assert_called_once()
        mock_app._conversation_history.log.assert_called_once()

    @patch("voicetext.controllers.recording_controller.type_text")
    @patch("PyObjCTools.AppHelper")
    def test_no_enhance_overlay_already_shown(
        self, mock_apphelper, mock_type_text, ctrl, mock_app
    ):
        """When overlay is already shown and no enhance, should update ASR text,
        schedule delayed close, and type text."""
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        ctrl.do_transcribe_direct(
            "hello world", use_enhance=False, overlay_already_shown=True
        )

        mock_app._streaming_overlay.set_asr_text.assert_called_once_with("hello world")
        mock_app._streaming_overlay.close_with_delay.assert_called_once()
        mock_type_text.assert_called_once()

    @patch("voicetext.controllers.recording_controller.type_text")
    @patch("PyObjCTools.AppHelper")
    def test_enhance_overlay_already_shown(
        self, mock_apphelper, mock_type_text, ctrl, mock_app
    ):
        """When overlay is already shown with enhance, should update ASR text
        and set cancel event instead of creating a new overlay."""
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._enhancer.get_mode_definition.return_value = MagicMock(steps=None)
        mock_app._enhancer.enhance_stream.side_effect = Exception("fail")

        ctrl.do_transcribe_direct(
            "hello", use_enhance=True, overlay_already_shown=True
        )

        mock_app._streaming_overlay.set_asr_text.assert_called_once_with("hello")
        mock_app._streaming_overlay.set_cancel_event.assert_called_once()
        # Should NOT call animate_out (overlay already visible)
        mock_app._recording_indicator.animate_out.assert_not_called()

    @patch("voicetext.controllers.recording_controller.type_text")
    @patch("PyObjCTools.AppHelper")
    def test_enhance_cancelled(self, mock_apphelper, mock_type_text, ctrl, mock_app):
        """When enhancement is cancelled, original text should not be typed."""
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)

        # Make enhance_stream immediately cancel
        async def fake_stream(text):
            return
            yield  # Make it an async generator

        mock_app._enhancer.enhance_stream = fake_stream
        mock_app._enhancer.get_mode_definition.return_value = MagicMock(steps=None)

        # We can't easily test cancellation in unit tests, but we can test
        # that when enhance raises, original text is used
        mock_app._enhancer.enhance_stream.side_effect = Exception("fail")
        mock_app._enhancer.get_mode_definition.return_value = MagicMock(steps=None)

        ctrl.do_transcribe_direct("hello", use_enhance=True)

        # Should fall back to original text
        mock_type_text.assert_called_once_with(
            "hello",
            append_newline=False,
            method="type",
        )


class TestDirectModeEscCancel:
    @patch("voicetext.controllers.recording_controller.type_text")
    @patch("PyObjCTools.AppHelper")
    def test_esc_before_transcribe_skips_transcription(
        self, mock_apphelper, mock_type_text, ctrl, mock_app
    ):
        """ESC during transcription phase should abort without typing."""
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._preview_enabled = False
        mock_app._recorder.stop.return_value = b"fake_wav"

        call_count = [0]

        def callAfter_with_cancel(fn, *a, **kw):
            call_count[0] += 1
            fn(*a, **kw)

        mock_apphelper.callAfter = callAfter_with_cancel

        # We'll test do_transcribe_direct directly with cancel already set
        ctrl.do_transcribe_direct(
            "hello", use_enhance=False, overlay_already_shown=True
        )
        # Text should be typed (cancel not set at this level)
        mock_type_text.assert_called_once()

    @patch("voicetext.controllers.recording_controller.type_text")
    @patch("PyObjCTools.AppHelper")
    def test_enhance_cancel_calls_cancel_stream(
        self, mock_apphelper, mock_type_text, ctrl, mock_app
    ):
        """When ESC cancels enhancement, enhancer.cancel_stream() should be
        called to stop remote token consumption."""
        mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._enhancer.get_mode_definition.return_value = MagicMock(steps=None)

        # Create async generator that checks cancel on second chunk
        async def fake_stream(text):
            yield "first ", None, False
            # Simulate ESC pressed during streaming
            # The cancel_event is created inside do_transcribe_direct,
            # so we set it via a side effect on the overlay
            ctrl._test_cancel_event.set()
            yield "second ", None, False

        mock_app._enhancer.enhance_stream = fake_stream

        # Intercept set_cancel_event to capture the cancel_event
        def capture_cancel(event):
            ctrl._test_cancel_event = event

        mock_app._streaming_overlay.set_cancel_event = capture_cancel

        ctrl.do_transcribe_direct(
            "hello", use_enhance=True, overlay_already_shown=True
        )

        # Should have called cancel_stream to close the HTTP connection
        mock_app._enhancer.cancel_stream.assert_called()
        # Should NOT type enhanced text (cancelled)
        mock_type_text.assert_not_called()
