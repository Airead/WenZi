"""Tests for Apple Speech streaming transcription."""

from __future__ import annotations

import struct
import sys
import time
from unittest.mock import MagicMock

import pytest


def _wait_for_task(t, timeout=0.5):
    """Wait for _stream_task to be assigned by the RunLoop thread."""
    deadline = time.monotonic() + timeout
    while t._stream_task is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert t._stream_task is not None, "timed out waiting for _stream_task"


@pytest.fixture(autouse=True)
def _mock_apple_frameworks(monkeypatch):
    """Mock Speech, AVFoundation, CoreFoundation for headless testing."""
    mock_speech = MagicMock()
    mock_avfoundation = MagicMock()
    mock_corefoundation = MagicMock()
    mock_foundation = MagicMock()

    monkeypatch.setitem(sys.modules, "Speech", mock_speech)
    monkeypatch.setitem(sys.modules, "AVFoundation", mock_avfoundation)
    monkeypatch.setitem(sys.modules, "CoreFoundation", mock_corefoundation)
    monkeypatch.setitem(sys.modules, "Foundation", mock_foundation)

    # Make CFRunLoopRunInMode a no-op
    mock_corefoundation.CFRunLoopRunInMode = MagicMock()
    mock_corefoundation.kCFRunLoopDefaultMode = "default"

    # Speed up timeout-based tests
    from wenzi.transcription import apple

    monkeypatch.setattr(apple, "STREAMING_FINAL_TIMEOUT", 0.01)

    return {
        "Speech": mock_speech,
        "AVFoundation": mock_avfoundation,
        "CoreFoundation": mock_corefoundation,
        "Foundation": mock_foundation,
    }


def _make_transcriber():
    """Create an AppleSpeechTranscriber with mocked initialization."""
    from wenzi.transcription.apple import AppleSpeechTranscriber

    t = AppleSpeechTranscriber(language="zh", on_device=True)
    t._initialized = True
    t._recognizer = MagicMock()
    return t


class TestAppleSpeechStreaming:
    def test_supports_streaming(self):
        t = _make_transcriber()
        assert t.supports_streaming is True

    def test_start_streaming_creates_request(self):
        t = _make_transcriber()
        on_partial = MagicMock()

        t.start_streaming(on_partial)

        assert t._stream_request is not None
        # Task is created on RunLoop thread; wait briefly for it
        _wait_for_task(t)
        assert t._stream_task is not None
        t._recognizer.recognitionTaskWithRequest_resultHandler_.assert_called_once()

        # Cleanup
        t.cancel_streaming()

    def test_feed_audio_appends_buffer(self):
        t = _make_transcriber()
        on_partial = MagicMock()

        t.start_streaming(on_partial)

        samples = struct.pack("<3h", 100, 200, 300)
        t.feed_audio(samples)

        assert t._stream_request.appendAudioPCMBuffer_.called

        t.cancel_streaming()

    def test_feed_audio_noop_when_no_session(self):
        t = _make_transcriber()
        samples = struct.pack("<2h", 100, 200)
        # Should not raise
        t.feed_audio(samples)

    def test_stop_streaming_calls_end_audio(self):
        t = _make_transcriber()
        on_partial = MagicMock()

        t.start_streaming(on_partial)

        # Simulate final result
        t._stream_final_text = "hello world"
        t._stream_final_event.set()

        result = t.stop_streaming()
        assert result == "hello world"

    def test_stop_streaming_timeout_returns_empty(self):
        t = _make_transcriber()
        on_partial = MagicMock()
        t.start_streaming(on_partial)

        # Don't set final event — will timeout
        result = t.stop_streaming()
        assert result == ""

    def test_cancel_streaming_cancels_task(self):
        t = _make_transcriber()
        on_partial = MagicMock()

        t.start_streaming(on_partial)
        # Wait for task to be created on RunLoop thread
        _wait_for_task(t)
        task = t._stream_task

        t.cancel_streaming()

        task.cancel.assert_called_once()
        assert t._stream_request is None

    def test_cleanup_cancels_active_stream(self):
        t = _make_transcriber()
        on_partial = MagicMock()

        t.start_streaming(on_partial)
        t.cleanup()

        assert t._initialized is False
        assert t._stream_request is None

    def test_handler_invokes_on_partial(self):
        """Verify the recognition handler calls on_partial correctly."""
        t = _make_transcriber()
        partials = []

        def on_partial(text, is_final):
            partials.append((text, is_final))

        t.start_streaming(on_partial)
        # Wait for RunLoop thread to create task
        _wait_for_task(t)
        call_args = t._recognizer.recognitionTaskWithRequest_resultHandler_.call_args
        handler = call_args[0][1]

        # Simulate partial results (formattedString returns FULL text)
        mock_result = MagicMock()
        mock_result.bestTranscription().formattedString.return_value = "hello"
        mock_result.isFinal.return_value = False
        handler(mock_result, None)

        assert partials == [("hello", False)]

        # Longer partial — updates
        mock_result2 = MagicMock()
        mock_result2.bestTranscription().formattedString.return_value = "hello world"
        mock_result2.isFinal.return_value = False
        handler(mock_result2, None)

        assert partials[-1] == ("hello world", False)

        # Mid-session isFinal (pause boundary) — accumulated, reported as partial
        mock_result3 = MagicMock()
        mock_result3.bestTranscription().formattedString.return_value = "hello world"
        mock_result3.isFinal.return_value = True
        handler(mock_result3, None)

        # Mid-session final does NOT set the final event
        assert not t._stream_final_event.is_set()
        assert t._stream_accumulated == "hello world"
        # Callback receives accumulated text with is_final=False
        assert partials[-1] == ("hello world", False)

        t.cancel_streaming()

    def test_accumulate_across_segments(self):
        """Two segments separated by a pause produce accumulated text."""
        t = _make_transcriber()
        partials = []

        def on_partial(text, is_final):
            partials.append((text, is_final))

        t.start_streaming(on_partial)
        _wait_for_task(t)
        call_args = t._recognizer.recognitionTaskWithRequest_resultHandler_.call_args
        handler = call_args[0][1]

        # Segment 1: partial then final
        seg1_partial = MagicMock()
        seg1_partial.bestTranscription().formattedString.return_value = "hello"
        seg1_partial.isFinal.return_value = False
        handler(seg1_partial, None)
        assert partials[-1] == ("hello", False)

        seg1_final = MagicMock()
        seg1_final.bestTranscription().formattedString.return_value = "hello world"
        seg1_final.isFinal.return_value = True
        handler(seg1_final, None)
        # Accumulated, reported as partial
        assert partials[-1] == ("hello world", False)
        assert t._stream_accumulated == "hello world"

        # Segment 2: new segment after pause — formattedString only returns new segment text
        seg2_partial = MagicMock()
        seg2_partial.bestTranscription().formattedString.return_value = "nice"
        seg2_partial.isFinal.return_value = False
        handler(seg2_partial, None)
        assert partials[-1] == ("hello worldnice", False)

        seg2_final = MagicMock()
        seg2_final.bestTranscription().formattedString.return_value = "nice to meet you"
        seg2_final.isFinal.return_value = False
        handler(seg2_final, None)
        assert partials[-1] == ("hello worldnice to meet you", False)

        # End session
        t._stream_ending = True
        end_result = MagicMock()
        end_result.bestTranscription().formattedString.return_value = "nice to meet you"
        end_result.isFinal.return_value = True
        handler(end_result, None)

        assert t._stream_final_event.is_set()
        assert t._stream_final_text == "hello worldnice to meet you"
        assert partials[-1] == ("hello worldnice to meet you", True)

        t.cancel_streaming()

    def test_mid_session_final_does_not_set_final_event(self):
        """A mid-session isFinal (pause boundary) must NOT set _stream_final_event."""
        t = _make_transcriber()
        on_partial = MagicMock()

        t.start_streaming(on_partial)
        _wait_for_task(t)
        call_args = t._recognizer.recognitionTaskWithRequest_resultHandler_.call_args
        handler = call_args[0][1]

        mock_result = MagicMock()
        mock_result.bestTranscription().formattedString.return_value = "segment one"
        mock_result.isFinal.return_value = True
        handler(mock_result, None)

        assert not t._stream_final_event.is_set()
        assert t._stream_accumulated == "segment one"
        on_partial.assert_called_with("segment one", False)

        t.cancel_streaming()

    def test_timeout_fallback_to_accumulated(self):
        """When final result times out, stop_streaming returns accumulated text."""
        t = _make_transcriber()
        on_partial = MagicMock()
        t.start_streaming(on_partial)

        # Simulate accumulated text from a completed segment
        t._stream_accumulated = "accumulated text"
        # Don't set final event — will timeout

        result = t.stop_streaming()
        assert result == "accumulated text"

    def test_single_segment_works_normally(self):
        """Single segment recognition (no pause) works end-to-end."""
        t = _make_transcriber()
        partials = []

        def on_partial(text, is_final):
            partials.append((text, is_final))

        t.start_streaming(on_partial)
        _wait_for_task(t)
        call_args = t._recognizer.recognitionTaskWithRequest_resultHandler_.call_args
        handler = call_args[0][1]

        # Partials within single segment
        p1 = MagicMock()
        p1.bestTranscription().formattedString.return_value = "hello"
        p1.isFinal.return_value = False
        handler(p1, None)
        assert partials[-1] == ("hello", False)

        p2 = MagicMock()
        p2.bestTranscription().formattedString.return_value = "hello world"
        p2.isFinal.return_value = False
        handler(p2, None)
        assert partials[-1] == ("hello world", False)

        # End session — single final result
        t._stream_ending = True
        final = MagicMock()
        final.bestTranscription().formattedString.return_value = "hello world"
        final.isFinal.return_value = True
        handler(final, None)

        assert t._stream_final_event.is_set()
        assert t._stream_final_text == "hello world"
        assert partials[-1] == ("hello world", True)

        t.cancel_streaming()

    def test_implicit_segment_reset_on_device(self):
        """On-device model resets partial text without isFinal — detect and accumulate."""
        t = _make_transcriber()
        partials = []

        def on_partial(text, is_final):
            partials.append((text, is_final))

        t.start_streaming(on_partial)
        _wait_for_task(t)
        call_args = t._recognizer.recognitionTaskWithRequest_resultHandler_.call_args
        handler = call_args[0][1]

        # Segment 1: partials grow normally
        for txt in ["这个", "这个现在", "这个现在是迪拜死的"]:
            r = MagicMock()
            r.bestTranscription().formattedString.return_value = txt
            r.isFinal.return_value = False
            handler(r, None)

        assert partials[-1] == ("这个现在是迪拜死的", False)
        assert t._stream_best_partial == "这个现在是迪拜死的"

        # Long pause — on-device model resets text to a short new segment
        r_reset = MagicMock()
        r_reset.bestTranscription().formattedString.return_value = "应"
        r_reset.isFinal.return_value = False
        handler(r_reset, None)

        # Previous best should be accumulated, overlay shows accumulated + new
        assert t._stream_accumulated == "这个现在是迪拜死的"
        assert partials[-1] == ("这个现在是迪拜死的应", False)

        # Segment 2 grows
        r2 = MagicMock()
        r2.bestTranscription().formattedString.return_value = "应该是DS的"
        r2.isFinal.return_value = False
        handler(r2, None)
        assert partials[-1] == ("这个现在是迪拜死的应该是DS的", False)

        # End session
        t._stream_ending = True
        r_final = MagicMock()
        r_final.bestTranscription().formattedString.return_value = "应该是DS的"
        r_final.isFinal.return_value = True
        handler(r_final, None)

        assert t._stream_final_text == "这个现在是迪拜死的应该是DS的"
        assert partials[-1] == ("这个现在是迪拜死的应该是DS的", True)

        t.cancel_streaming()

    def test_implicit_reset_timeout_fallback_includes_best_partial(self):
        """Timeout fallback returns accumulated + current best partial."""
        t = _make_transcriber()
        on_partial = MagicMock()
        t.start_streaming(on_partial)

        # Simulate state: one accumulated segment + current partial in progress
        t._stream_accumulated = "first segment"
        t._stream_best_partial = "second partial"
        # Don't set final event — will timeout

        result = t.stop_streaming()
        assert result == "first segmentsecond partial"

    def test_small_text_fluctuation_not_treated_as_reset(self):
        """Normal ASR text shortening (refinement) should not trigger reset."""
        t = _make_transcriber()
        partials = []

        def on_partial(text, is_final):
            partials.append((text, is_final))

        t.start_streaming(on_partial)
        _wait_for_task(t)
        call_args = t._recognizer.recognitionTaskWithRequest_resultHandler_.call_args
        handler = call_args[0][1]

        # Text grows then shrinks slightly (normal ASR refinement)
        r1 = MagicMock()
        r1.bestTranscription().formattedString.return_value = "hello world"
        r1.isFinal.return_value = False
        handler(r1, None)

        # Slight shortening — more than 50% of previous, should NOT trigger reset
        r2 = MagicMock()
        r2.bestTranscription().formattedString.return_value = "hello wor"
        r2.isFinal.return_value = False
        handler(r2, None)

        assert t._stream_accumulated == ""  # No implicit reset
        assert partials[-1] == ("hello wor", False)

        t.cancel_streaming()

    def test_sample_rate_property(self):
        t = _make_transcriber()
        assert t.sample_rate == 16000
