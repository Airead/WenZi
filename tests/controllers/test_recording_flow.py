"""Tests for RecordingFlow — coroutine-based recording controller."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import wenzi.async_loop as async_loop
from wenzi.controllers.recording_flow import Action, RecordingFlow

_FILE = str(Path(__file__).resolve())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_loop():
    """Ensure a fresh asyncio loop for each test."""
    async_loop.shutdown_sync(timeout=2)
    yield
    async_loop.shutdown_sync(timeout=2)


@pytest.fixture(autouse=True)
def mock_type_text(monkeypatch):
    """Prevent type_text from typing into the real cursor position.

    Every test gets this automatically.  Tests that need to assert on
    type_text calls can use the ``mock_type_text`` fixture directly.
    """
    mock = MagicMock()
    monkeypatch.setattr("wenzi.controllers.recording_flow.type_text", mock)
    return mock


@pytest.fixture
def mock_app(tmp_path):
    """Create a mock WenZiApp with all attributes used by RecordingFlow."""
    app = MagicMock()
    app._busy = False
    app._config_degraded = False
    app._voice_input_available = True
    app._config = {
        "feedback": {"sound_enabled": True, "visual_indicator": True},
    }
    app._config_path = str(tmp_path / "config.json")
    app._sound_manager = MagicMock()
    app._sound_manager.enabled = True
    app._recording_indicator = MagicMock()
    app._recording_indicator.enabled = True
    app._recording_indicator.show_device_name = False
    app._recording_indicator.current_frame = MagicMock()
    app._recorder = MagicMock()
    app._recorder.is_recording = False
    app._recorder.current_level = 0.5
    app._recorder.last_device_name = "MacBook Pro Microphone"
    app._recorder.start.return_value = "MacBook Pro Microphone"
    app._recorder.stop.return_value = b"fake_wav_data"
    app._transcriber = MagicMock()
    app._transcriber.supports_streaming = False
    app._transcriber.transcribe.return_value = f"[mock from {_FILE}::mock_app fixture]"
    app._enhancer = MagicMock()
    app._enhancer.is_active = False
    app._enhancer.mode = "proofread"
    app._enhancer.input_context_level = "basic"
    app._enhance_mode = "proofread"
    app._preview_enabled = False
    app._streaming_overlay = MagicMock()
    app._live_overlay = None
    app._usage_stats = MagicMock()
    app._conversation_history = MagicMock()
    app._enhance_menu_items = {}
    app._enhance_controller = MagicMock()
    app._append_newline = False
    app._output_method = "type"
    app._current_stt_model = MagicMock(return_value="FunASR")
    app._current_llm_model = MagicMock(return_value="openai / gpt-4o")
    app._last_audio_duration = 0.0
    app._build_dynamic_hotwords = MagicMock(return_value=([], None))
    app._preview_controller = MagicMock()
    return app


@pytest.fixture
def flow(mock_app):
    return RecordingFlow(mock_app)


def run(coro):
    """Run a coroutine on the shared loop and return the result."""
    return async_loop.submit(coro).result(timeout=10)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIsNotBusy:
    def test_initially_not_busy(self, flow):
        assert not flow.is_busy

    def test_busy_returns_early(self, flow, mock_app):
        """A second press while busy should be ignored."""
        # Simulate a long-running task
        async def _block():
            flow._current_task = asyncio.current_task()
            await asyncio.sleep(3600)

        task = async_loop.submit(_block())
        # Give it a moment to start
        async_loop.submit(asyncio.sleep(0.01)).result(timeout=2)

        assert flow.is_busy

        # Second press should be ignored
        run(flow._handle_press("fn"))
        # The original task should still be the current one
        task.cancel()


class TestSoundDelay:
    @pytest.fixture(autouse=True)
    def _fast_delay(self, monkeypatch):
        monkeypatch.setattr(RecordingFlow, "_DELAYED_START_SECS", 0.1)

    @patch("wenzi.controllers.recording_flow.capture_input_context", return_value=None)
    @patch("PyObjCTools.AppHelper")
    def test_cancel_during_delay(self, mock_ah, _mock_ic, flow, mock_app):
        """Cancel sent during the sound delay should abort immediately."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = True

        async def _test():
            # Start press (will begin the recording session)
            await flow._handle_press("fn")
            # Give session time to reach the delay
            await asyncio.sleep(0.05)
            assert flow.is_busy
            # Send cancel
            flow._actions.put_nowait(Action.CANCEL)
            # Wait for session to complete
            await flow._current_task

        run(_test())

        # Recorder should never have been started
        mock_app._recorder.start.assert_not_called()
        # Overlay and indicator must be cleaned up
        mock_app._recording_indicator.hide.assert_called()

    @patch("wenzi.controllers.recording_flow.capture_input_context", return_value=None)
    @patch("PyObjCTools.AppHelper")
    def test_release_during_delay(self, mock_ah, _mock_ic, flow, mock_app):
        """Release during sound delay should abort without recording."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = True

        async def _test():
            await flow._handle_press("fn")
            await asyncio.sleep(0.05)
            flow._actions.put_nowait(Action.RELEASE)
            await flow._current_task

        run(_test())

        mock_app._recorder.start.assert_not_called()
        # Overlay and indicator must be cleaned up
        mock_app._recording_indicator.hide.assert_called()

    @patch("wenzi.controllers.recording_flow.capture_input_context", return_value=None)
    @patch("PyObjCTools.AppHelper")
    def test_preview_history_during_delay(self, mock_ah, _mock_ic, flow, mock_app):
        """PREVIEW_HISTORY during sound delay should abort and show history."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = True

        async def _test():
            await flow._handle_press("fn")
            await asyncio.sleep(0.05)
            flow._actions.put_nowait(Action.PREVIEW_HISTORY)
            await flow._current_task

        run(_test())

        mock_app._recorder.start.assert_not_called()
        mock_app._recording_indicator.hide.assert_called()
        mock_app._preview_controller.on_show_last_preview.assert_called_once()

    @patch("wenzi.controllers.recording_flow.capture_input_context", return_value=None)
    @patch("PyObjCTools.AppHelper")
    def test_restart_during_delay(self, mock_ah, _mock_ic, flow, mock_app):
        """RESTART during sound delay should restart the session."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = True
        mock_app._recorder.start.return_value = None

        restart_seen = asyncio.Event()

        def _start_side_effect():
            restart_seen.set()
            return None

        mock_app._recorder.start.side_effect = _start_side_effect

        async def _test():
            # Trigger press, then restart during delay, then release
            await flow._handle_press("fn")
            await asyncio.sleep(0.05)
            flow._actions.put_nowait(Action.RESTART)
            await asyncio.wait_for(restart_seen.wait(), timeout=2.0)
            flow._actions.put_nowait(Action.RELEASE)
            await flow._current_task

        run(_test())

        mock_app._recorder.start.assert_called()


class TestPressContextCapture:
    @patch("wenzi.controllers.recording_flow.capture_input_context")
    @patch("wenzi.controllers.recording_flow.get_frontmost_app")
    def test_frontmost_app_captured_before_input_context(
        self, mock_get_frontmost_app, mock_capture_input_context, flow
    ):
        """The original target app must be saved before slow AX context lookup."""
        call_order: list[str] = []
        target_app = object()

        mock_get_frontmost_app.side_effect = (
            lambda: call_order.append("frontmost") or target_app
        )
        mock_capture_input_context.side_effect = (
            lambda _level: call_order.append("context") or None
        )

        async def _noop_session(_key_name: str) -> None:
            return None

        flow._recording_session = _noop_session

        async def _test():
            await flow._handle_press("fn")
            await flow._current_task

        run(_test())

        assert call_order == ["frontmost", "context"]
        assert flow._target_app is target_app


class TestRecordAndRelease:
    @patch("wenzi.controllers.recording_flow.capture_input_context", return_value=None)
    @patch("PyObjCTools.AppHelper")
    def test_full_flow_no_enhance(
        self, mock_ah, _mock_ic, flow, mock_app, mock_type_text
    ):
        """Full flow: press → record → release → transcribe → type."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False  # Skip delay
        mock_app._enhancer.is_active = False
        _text = f"[mock from {_FILE}::test_full_flow_no_enhance]"
        mock_app._transcriber.transcribe.return_value = _text
        mock_app._recorder.stop.return_value = b"fake_wav"

        async def _test():
            await flow._handle_press("fn")
            # Give it time to reach the wait_action
            await asyncio.sleep(0.05)
            flow._actions.put_nowait(Action.RELEASE)
            await flow._current_task

        run(_test())

        mock_app._recorder.start.assert_called_once()
        mock_app._recorder.stop.assert_called_once()
        mock_app._transcriber.transcribe.assert_called_once()
        mock_type_text.assert_called_once_with(
            _text, append_newline=False, method="type"
        )

    @patch("wenzi.controllers.recording_flow.capture_input_context", return_value=None)
    @patch("PyObjCTools.AppHelper")
    def test_empty_transcription(self, mock_ah, _mock_ic, flow, mock_app):
        """Empty transcription should show empty status."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False
        mock_app._transcriber.transcribe.return_value = ""

        async def _test():
            await flow._handle_press("fn")
            await asyncio.sleep(0.05)
            flow._actions.put_nowait(Action.RELEASE)
            await flow._current_task

        run(_test())

        mock_app._set_status.assert_any_call("statusbar.status.empty")


class TestCancel:
    @patch("wenzi.controllers.recording_flow.capture_input_context", return_value=None)
    @patch("PyObjCTools.AppHelper")
    def test_cancel_during_recording(self, mock_ah, _mock_ic, flow, mock_app):
        """Cancel during recording should stop and not transcribe."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False
        # is_recording starts False (no orphan), becomes True after start()
        mock_app._recorder.is_recording = False

        async def _test():
            await flow._handle_press("fn")
            await asyncio.sleep(0.05)
            flow._actions.put_nowait(Action.CANCEL)
            await flow._current_task

        run(_test())

        mock_app._recorder.stop.assert_called_once()
        mock_app._transcriber.transcribe.assert_not_called()


class TestRestart:
    @patch("wenzi.controllers.recording_flow.capture_input_context", return_value=None)
    @patch("PyObjCTools.AppHelper")
    def test_restart_creates_new_session(
        self, mock_ah, _mock_ic, flow, mock_app
    ):
        """Restart should stop current recording and start a new session."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False
        mock_app._transcriber.transcribe.return_value = f"[mock from {_FILE}::test_restart_creates_new_session]"

        call_count = [0]

        def counting_start():
            call_count[0] += 1
            return "mic"

        mock_app._recorder.start.side_effect = counting_start

        async def _test():
            await flow._handle_press("fn")
            await asyncio.sleep(0.05)
            # First: restart
            flow._actions.put_nowait(Action.RESTART)
            await asyncio.sleep(0.1)
            # Then: release the restarted session
            flow._actions.put_nowait(Action.RELEASE)
            # Wait for the restarted session to complete
            for _ in range(100):
                if not flow.is_busy:
                    break
                await asyncio.sleep(0.05)

        run(_test())

        # Recorder should have been started twice (original + restart)
        assert call_count[0] == 2
        # And stopped twice
        assert mock_app._recorder.stop.call_count == 2


class TestPreviewHistory:
    @patch("wenzi.controllers.recording_flow.capture_input_context", return_value=None)
    @patch("PyObjCTools.AppHelper")
    def test_preview_history_action(self, mock_ah, _mock_ic, flow, mock_app):
        """Preview history action should stop recording and show preview."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False

        async def _test():
            await flow._handle_press("fn")
            await asyncio.sleep(0.05)
            flow._actions.put_nowait(Action.PREVIEW_HISTORY)
            await flow._current_task

        run(_test())

        mock_app._recorder.stop.assert_called_once()
        mock_app._preview_controller.on_show_last_preview.assert_called_once()


class TestWatchdogTimeout:
    @patch("wenzi.controllers.recording_flow.capture_input_context", return_value=None)
    @patch("PyObjCTools.AppHelper")
    def test_timeout_auto_stops(self, mock_ah, _mock_ic, flow, mock_app):
        """Recording should auto-stop on timeout (acts like release)."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False
        mock_app._config["audio"] = {"max_recording_seconds": 0.1}
        mock_app._transcriber.transcribe.return_value = f"[mock from {_FILE}::test_timeout_auto_stops]"

        async def _test():
            await flow._handle_press("fn")
            # Don't send any action — let it timeout
            for _ in range(100):
                if not flow.is_busy:
                    break
                await asyncio.sleep(0.05)

        run(_test())

        mock_app._recorder.stop.assert_called_once()
        mock_app._transcriber.transcribe.assert_called_once()


class TestModeNav:
    def test_build_mode_list(self, flow, mock_app):
        mock_app._enhancer.available_modes = [
            ("proofread", "Proofread"),
            ("translate_en", "Translate EN"),
        ]
        modes = flow._build_mode_list()
        assert modes[0] == ("off", "Off")
        assert modes[1] == ("proofread", "Proofread")

    @patch("PyObjCTools.AppHelper")
    def test_mode_nav_inline(self, mock_ah, flow, mock_app):
        """Mode navigation should be handled inline without interrupting wait."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._enhancer.available_modes = [
            ("proofread", "Proofread"),
            ("translate_en", "Translate EN"),
        ]
        mock_app._enhance_mode = "proofread"

        async def _test():
            # Test inline handling
            action = Action.MODE_NEXT
            flow._handle_inline_action(action)

        run(_test())

        assert mock_app._enhance_mode == "translate_en"


class TestFeedbackToggles:
    @patch("wenzi.controllers.recording_flow.save_config")
    def test_sound_toggle(self, mock_save, flow, mock_app):
        sender = MagicMock()
        flow.on_sound_feedback_toggle(sender)
        assert mock_app._sound_manager.enabled is False
        assert sender.state == 0
        mock_save.assert_called_once()

    @patch("wenzi.controllers.recording_flow.save_config")
    def test_visual_toggle(self, mock_save, flow, mock_app):
        sender = MagicMock()
        flow.on_visual_indicator_toggle(sender)
        assert mock_app._recording_indicator.enabled is False
        assert sender.state == 0
        mock_save.assert_called_once()


class TestStreaming:
    @patch("wenzi.controllers.recording_flow.capture_input_context", return_value=None)
    @patch("PyObjCTools.AppHelper")
    def test_streaming_starts_when_supported(
        self, mock_ah, _mock_ic, flow, mock_app
    ):
        """Streaming transcription should start if transcriber supports it."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False
        mock_app._transcriber.supports_streaming = True
        mock_app._transcriber.stop_streaming.return_value = f"[mock from {_FILE}::test_streaming_starts_when_supported]"

        async def _test():
            await flow._handle_press("fn")
            await asyncio.sleep(0.05)
            flow._actions.put_nowait(Action.RELEASE)
            await flow._current_task

        run(_test())

        mock_app._transcriber.start_streaming.assert_called_once()
        mock_app._recorder.set_on_audio_chunk.assert_called_once()

    @patch("wenzi.controllers.recording_flow.capture_input_context", return_value=None)
    @patch("PyObjCTools.AppHelper")
    def test_streaming_fallback_on_error(
        self, mock_ah, _mock_ic, flow, mock_app
    ):
        """If streaming init fails, should fall back to batch mode."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False
        mock_app._transcriber.supports_streaming = True
        mock_app._transcriber.start_streaming.side_effect = RuntimeError("fail")

        async def _test():
            await flow._handle_press("fn")
            await asyncio.sleep(0.05)
            flow._actions.put_nowait(Action.RELEASE)
            await flow._current_task

        run(_test())

        # Should fall back to batch transcription
        mock_app._transcriber.transcribe.assert_called_once()


class TestStartTimeout:
    @patch("wenzi.controllers.recording_flow.capture_input_context", return_value=None)
    @patch("PyObjCTools.AppHelper")
    def test_start_timeout_resets_to_idle(
        self, mock_ah, _mock_ic, flow, mock_app, monkeypatch
    ):
        """When recorder.start() times out, session should reset to idle."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False
        monkeypatch.setattr(RecordingFlow, "_START_TIMEOUT", 0.1)

        def hanging_start():
            # Block until cancelled — simulates a hung PortAudio call
            import time
            time.sleep(5)

        mock_app._recorder.start.side_effect = hanging_start

        async def _test():
            await flow._handle_press("fn")
            # Wait for session to finish (via timeout)
            for _ in range(100):
                if not flow.is_busy:
                    break
                await asyncio.sleep(0.05)

        run(_test())

        mock_app._recorder.mark_tainted.assert_called_once()
        mock_app._recording_indicator.hide.assert_called()
        assert not flow.is_busy


class TestStreamingTimeoutFallback:
    def test_single_stream_timeout_replaces_partial_output(
        self, flow, mock_app
    ):
        """Timeout fallback must replace partial streamed output."""

        async def _gen():
            yield "partial", None, False
            yield "original text", None, "timeout"

        mock_app._enhancer.enhance_stream.return_value = _gen()
        flow._show_error_alert = MagicMock()

        result = run(
            flow._run_direct_single_stream("original text", asyncio.Event())
        )

        assert result == "original text"
        mock_app._streaming_overlay.clear_text.assert_called_once()
        assert mock_app._streaming_overlay.append_text.call_args_list[-1].args == (
            "original text",
        )
        assert (
            mock_app._streaming_overlay.append_text.call_args_list[-1].kwargs
            == {"completion_tokens": len("original text")}
        )
        mock_app._streaming_overlay.set_status.assert_called_with(
            "\u26a0\ufe0f AI timed out, using original text"
        )
        flow._show_error_alert.assert_called_once_with("AI enhancement timed out")

    def test_chain_stream_timeout_passes_original_text_to_next_step(
        self, flow, mock_app
    ):
        """A timed-out step must not poison the next chain step input."""
        step1_def = MagicMock()
        step1_def.label = "Proofread"
        step2_def = MagicMock()
        step2_def.label = "Translate"
        mock_app._enhancer.get_mode_definition.side_effect = (
            lambda mode_id: {
                "proofread": step1_def,
                "translate": step2_def,
            }.get(mode_id)
        )

        inputs: list[str] = []

        def _make_stream(text: str, input_context=None):
            inputs.append(text)

            async def _gen():
                if len(inputs) == 1:
                    yield "partial", None, False
                    yield "original text", None, "timeout"
                else:
                    yield "translated text", None, False

            return _gen()

        mock_app._enhancer.enhance_stream.side_effect = _make_stream
        flow._show_error_alert = MagicMock()

        result = run(
            flow._run_direct_chain_stream(
                "original text",
                ["proofread", "translate"],
                asyncio.Event(),
            )
        )

        assert result == "translated text"
        assert inputs == ["original text", "original text"]
        flow._show_error_alert.assert_called_once_with("AI enhancement timed out")


class TestOrphanedRecording:
    @patch("wenzi.controllers.recording_flow.capture_input_context", return_value=None)
    @patch("PyObjCTools.AppHelper")
    def test_orphaned_recording_cleaned_up(
        self, mock_ah, _mock_ic, flow, mock_app
    ):
        """An orphaned active recording should be stopped before starting."""
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._sound_manager.enabled = False
        mock_app._recorder.is_recording = True
        mock_app._recorder.start.return_value = "TestMic"
        mock_app._transcriber.transcribe.return_value = (
            f"[mock from {_FILE}::test_orphaned_recording_cleaned_up]"
        )

        async def _test():
            await flow._handle_press("fn")
            await asyncio.sleep(0.05)
            flow._actions.put_nowait(Action.RELEASE)
            await flow._current_task

        run(_test())

        # stop() should be called twice: once for orphan cleanup,
        # once for the normal release
        assert mock_app._recorder.stop.call_count == 2


class TestConfigDegraded:
    @patch("PyObjCTools.AppHelper")
    def test_config_degraded_shows_alert(self, mock_ah, flow, mock_app):
        mock_ah.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
        mock_app._config_degraded = True

        run(flow._handle_press("fn"))

        mock_app._show_config_error_alert.assert_called_once()
        assert not flow.is_busy
