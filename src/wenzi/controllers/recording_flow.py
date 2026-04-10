"""Coroutine-based recording flow replacing the thread-heavy RecordingController.

The entire hotkey → record → transcribe → enhance → output pipeline is
expressed as a single linear coroutine running on the shared asyncio event
loop.  All business state lives on that single thread, eliminating the need
for locks, events, and busy-token bookkeeping.

External signals (hotkey release, cancel, restart, mode navigation) are
delivered through an :class:`asyncio.Queue` and consumed inline by the
coroutine, which decides how to react based on its current phase.
"""

from __future__ import annotations

import asyncio
import enum
import logging
from typing import TYPE_CHECKING

from wenzi import async_loop
from wenzi.config import save_config
from wenzi.controllers import fire_scripting_event
from wenzi.input import type_text
from wenzi.input_context import capture_input_context
from wenzi.ui_helpers import get_frontmost_app, reactivate_app

if TYPE_CHECKING:
    from wenzi.app import WenZiApp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action signal enum — sent from hotkey thread into the asyncio loop
# ---------------------------------------------------------------------------

class Action(enum.Enum):
    RELEASE = "release"
    CANCEL = "cancel"
    CONFIRM_ASR = "confirm_asr"
    RESTART = "restart"
    MODE_PREV = "mode_prev"
    MODE_NEXT = "mode_next"
    PREVIEW_HISTORY = "preview_history"


def _merge_events(
    *events: asyncio.Event,
) -> tuple[asyncio.Event, list[asyncio.Task]]:
    """Return a new Event that is set when any of *events* is set.

    Also returns the background waiter tasks so the caller can cancel
    them when the merged event is no longer needed (avoid task leaks).
    """
    merged = asyncio.Event()

    async def _waiter(ev: asyncio.Event) -> None:
        await ev.wait()
        merged.set()

    tasks = [asyncio.ensure_future(_waiter(ev)) for ev in events]
    return merged, tasks


class _RestartSession(Exception):
    """Sentinel raised inside the coroutine to trigger a recording restart."""
    def __init__(self, key_name: str = "") -> None:
        self.key_name = key_name


# ---------------------------------------------------------------------------
# RecordingFlow — the main coroutine-based recording controller
# ---------------------------------------------------------------------------

class RecordingFlow:
    """Coroutine-based controller for the hotkey → record → output flow."""

    _DELAYED_START_SECS = 0.35
    _START_TIMEOUT = 5.0  # seconds to wait for Recorder.start()

    def __init__(self, app: WenZiApp) -> None:
        self._app = app
        self._loop = async_loop.get_loop()
        self._actions: asyncio.Queue[Action] = asyncio.Queue()
        self._current_task: asyncio.Task | None = None
        # Mode override state (carried over from RecordingController)
        self._prefer_mode: str | None = None
        self._saved_mode: tuple | None = None
        self._no_preview_override = False
        self._input_context = None
        self._target_app = None  # NSRunningApplication to reactivate before typing
        # Sub-tasks managed within a session
        self._level_task: asyncio.Task | None = None
        self._live_overlay = None

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def is_busy(self) -> bool:
        """True while a recording session is in progress."""
        return self._current_task is not None and not self._current_task.done()

    @property
    def input_context(self):
        """The input context captured at the last hotkey press."""
        return self._input_context

    # ------------------------------------------------------------------
    # Hotkey thread entry points (thread-safe)
    # ------------------------------------------------------------------

    def on_press(self, key_name: str = "") -> None:
        """Called from hotkey thread when the hotkey is pressed."""
        future = async_loop.submit(self._handle_press(key_name))
        future.add_done_callback(self._log_future_exception)

    @staticmethod
    def _log_future_exception(future: asyncio.Future) -> None:
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            logger.error("on_press failed: %s", exc, exc_info=exc)

    def send_action(self, action: Action) -> None:
        """Send an action signal into the recording session (thread-safe)."""
        self._loop.call_soon_threadsafe(self._actions.put_nowait, action)

    # Adapters so MultiHotkeyListener / app.py can use the same callback
    # names as the old RecordingController.

    def on_hotkey_press(self, key_name: str = "") -> None:
        self.on_press(key_name)

    def on_hotkey_release(self, key_name: str = "") -> None:
        self.send_action(Action.RELEASE)

    def on_restart_recording(self) -> None:
        self.send_action(Action.RESTART)

    def on_cancel_recording(self) -> None:
        self.send_action(Action.CANCEL)

    def on_preview_history(self) -> None:
        self.send_action(Action.PREVIEW_HISTORY)

    def on_mode_prev(self) -> None:
        self.send_action(Action.MODE_PREV)

    def on_mode_next(self) -> None:
        self.send_action(Action.MODE_NEXT)

    # ------------------------------------------------------------------
    # Asyncio-thread internal methods
    # ------------------------------------------------------------------

    async def _handle_press(self, key_name: str) -> None:
        if self.is_busy:
            return

        app = self._app

        if app._config_degraded:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(app._show_config_error_alert)
            return

        if not app._voice_input_available:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(self._try_enable_voice_input)
            return

        # Capture the frontmost app before any potentially slow AX context
        # lookup so we can reactivate the original target window later.
        ic_level = (
            app._enhancer.input_context_level
            if app._enhancer
            else app._config.get("ai_enhance", {}).get("input_context", "basic")
        )
        self._target_app, self._input_context = await self._loop.run_in_executor(
            None,
            lambda: (
                get_frontmost_app(),
                capture_input_context(ic_level),
            ),
        )

        # Restore previous override before applying a new one
        self._restore_mode()

        # Apply prefer_mode if configured for this hotkey
        self._prefer_mode = None
        hotkey_value = app._config.get("hotkeys", {}).get(key_name)
        if isinstance(hotkey_value, dict):
            prefer_mode = hotkey_value.get("mode")
            if prefer_mode == "no_preview":
                from wenzi.enhance.enhancer import MODE_OFF
                self._no_preview_override = True
                self._prefer_mode = MODE_OFF
                self._apply_prefer_mode(MODE_OFF)
            elif prefer_mode is not None:
                self._prefer_mode = prefer_mode
                self._apply_prefer_mode(prefer_mode)

        self._drain_actions()

        app._busy = True
        logger.info("Hotkey pressed, starting recording session")
        self._current_task = asyncio.create_task(
            self._recording_session(key_name)
        )

    # ------------------------------------------------------------------
    # The recording session coroutine
    # ------------------------------------------------------------------

    async def _recording_session(self, key_name: str) -> None:
        """The full press → delay → record → transcribe → enhance → output flow."""
        from PyObjCTools import AppHelper

        app = self._app
        streaming = False

        try:
            self._fire_scripting_event("recording_start")

            # ① Play start sound + show indicator
            AppHelper.callAfter(app._set_status, "statusbar.status.recording")
            AppHelper.callAfter(app._sound_manager.play, "start")
            if app._sound_manager.enabled:
                AppHelper.callAfter(app._usage_stats.record_sound_feedback)

            initial_dev = (
                app._recorder.last_device_name
                if app._recording_indicator.show_device_name
                else None
            )
            AppHelper.callAfter(app._recording_indicator.show, initial_dev)
            self._show_mode_on_indicator()

            if app._transcriber.supports_streaming:
                AppHelper.callAfter(self._show_live_overlay, False)

            # ② Sound delay — listen for early cancel/release/restart/history
            if app._sound_manager.enabled:
                action = await self._wait_action(
                    Action.RELEASE, Action.CANCEL,
                    Action.RESTART, Action.PREVIEW_HISTORY,
                    timeout=self._DELAYED_START_SECS,
                )
                if action in (Action.CANCEL, Action.RELEASE):
                    AppHelper.callAfter(self._reset_to_idle)
                    return
                elif action == Action.PREVIEW_HISTORY:
                    AppHelper.callAfter(self._reset_and_show_preview)
                    return
                elif action == Action.RESTART:
                    raise _RestartSession(key_name)

            # ③ Start recording (blocking I/O → executor)
            if app._recorder.is_recording:
                logger.warning(
                    "Recorder unexpectedly active, "
                    "stopping orphaned session"
                )
                await self._loop.run_in_executor(
                    None, app._recorder.stop
                )

            try:
                dev_name = await asyncio.wait_for(
                    self._loop.run_in_executor(
                        None, app._recorder.start
                    ),
                    timeout=self._START_TIMEOUT,
                )
            except TimeoutError:
                logger.error(
                    "Recorder.start() timed out after %.0fs, "
                    "aborting session",
                    self._START_TIMEOUT,
                )
                app._recorder.mark_tainted()
                AppHelper.callAfter(self._reset_to_idle)
                return
            if dev_name and app._recording_indicator.show_device_name:
                AppHelper.callAfter(
                    app._recording_indicator.update_device_name, dev_name
                )
            AppHelper.callAfter(app._recording_indicator.set_recording_active)

            # Start streaming transcription if supported
            streaming = self._start_streaming_if_supported()

            # Start level polling
            self._level_task = asyncio.create_task(self._poll_level())

            # ④ Wait for user action during recording
            max_sec = app._config.get("audio", {}).get(
                "max_recording_seconds", 120
            )
            action = await self._wait_action(
                Action.RELEASE, Action.CANCEL, Action.RESTART,
                Action.PREVIEW_HISTORY,
                timeout=max_sec,
            )

            if action is None:
                # Timeout — treat as release
                logger.warning(
                    "Recording watchdog triggered — auto-stopping "
                    "(possible missed hotkey release)"
                )

            if action == Action.CANCEL:
                self._stop_streaming(streaming)
                await self._loop.run_in_executor(None, app._recorder.stop)
                self._cancel_subtasks()
                AppHelper.callAfter(self._reset_to_idle)
                return

            if action == Action.RESTART:
                self._stop_streaming(streaming)
                await self._loop.run_in_executor(None, app._recorder.stop)
                raise _RestartSession(key_name)

            if action == Action.PREVIEW_HISTORY:
                self._stop_streaming(streaming)
                await self._loop.run_in_executor(None, app._recorder.stop)
                self._cancel_subtasks()
                AppHelper.callAfter(self._reset_and_show_preview)
                return

            # ⑤ Release (or timeout) — stop recording
            self._cancel_subtasks()

            if streaming:
                app._recorder.clear_on_audio_chunk()

            wav_data = await self._loop.run_in_executor(
                None, app._recorder.stop
            )

            # Record audio duration
            audio_duration = 0.0
            if wav_data:
                try:
                    from wenzi.transcription.base import BaseTranscriber
                    audio_duration = BaseTranscriber.wav_duration_seconds(wav_data)
                    app._usage_stats.record_recording_duration(audio_duration)
                except Exception as e:
                    logger.error("Failed to record duration: %s", e)
            app._last_audio_duration = audio_duration
            self._fire_scripting_event(
                "recording_stop", audio_duration=audio_duration
            )

            if not wav_data:
                from wenzi.i18n import t
                from wenzi.scripting.api.alert import alert

                alert(t("alert.recording.empty"), duration=2.0)
                AppHelper.callAfter(self._reset_to_idle)
                return

            # Indicate we are busy processing (keep indicator alive for animation)
            AppHelper.callAfter(self._stop_indicator, True)

            # ⑥ Transcribe (or defer to preview/direct for background STT)
            effective_preview = app._preview_enabled and not self._no_preview_override
            if effective_preview and not streaming:
                # Non-streaming preview: open preview immediately and
                # let it run STT in the background (asr_text=None).
                await self._route_to_preview(
                    None, audio_duration, wav_data,
                )
                return

            if not effective_preview and not streaming:
                # Non-streaming direct: show overlay immediately and
                # run STT in the background.
                logger.debug("Routing to direct flow with background STT")
                await self._do_direct_flow(
                    None, wav_data, audio_duration
                )
                app._busy = False
                logger.debug("Direct flow done, session done")
                return

            # All non-streaming paths returned above; only streaming remains.
            try:
                text = await self._loop.run_in_executor(
                    None, app._transcriber.stop_streaming
                )
            except Exception as e:
                logger.error("Streaming stop failed: %s", e)
                text = None
            self._hide_live_overlay()

            logger.debug("Transcription result: %r", text[:100] if text else None)

            if not text or not text.strip():
                AppHelper.callAfter(app._recording_indicator.hide)
                AppHelper.callAfter(
                    app._set_status, "statusbar.status.empty"
                )
                logger.warning("Transcription returned empty text")
                app._busy = False
                return

            asr_text = text.strip()

            # ⑦ Route to preview or direct flow
            if effective_preview:
                await self._route_to_preview(
                    asr_text, audio_duration, wav_data,
                )
            else:
                logger.debug("Routing to direct flow")
                await self._do_direct_flow(
                    asr_text, wav_data, audio_duration
                )
                app._busy = False
                logger.debug("Direct flow done, session done")

        except _RestartSession as rs:
            self._cancel_subtasks()
            self._hide_live_overlay()
            self._drain_actions()
            self._current_task = asyncio.create_task(
                self._recording_session(rs.key_name)
            )
            return
        except asyncio.CancelledError:
            if app._recorder.is_recording:
                self._stop_streaming(streaming)
                await self._loop.run_in_executor(
                    None, app._recorder.stop
                )
            self._cancel_subtasks()
            AppHelper.callAfter(self._reset_to_idle)
        except Exception:
            logger.exception("Recording session failed")
            self._cancel_subtasks()
            AppHelper.callAfter(self._reset_to_idle)

    # ------------------------------------------------------------------
    # Action waiting
    # ------------------------------------------------------------------

    async def _wait_action(
        self, *expected: Action, timeout: float,
    ) -> Action | None:
        """Wait for one of *expected* actions.

        Inline actions (mode navigation) are handled immediately and do not
        interrupt the wait.  Returns ``None`` on timeout.
        """
        deadline = self._loop.time() + timeout
        while True:
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                return None
            try:
                action = await asyncio.wait_for(
                    self._actions.get(), timeout=remaining
                )
            except TimeoutError:
                return None
            if action in expected:
                return action
            self._handle_inline_action(action)

    def _handle_inline_action(self, action: Action) -> None:
        if action == Action.MODE_PREV:
            self._navigate_mode(-1)
        elif action == Action.MODE_NEXT:
            self._navigate_mode(+1)

    async def _watch_cancel(
        self,
        cancel_event: asyncio.Event,
        confirm_asr_event: asyncio.Event | None = None,
    ) -> None:
        """Monitor the action queue for CANCEL / CONFIRM_ASR.

        Inline actions (mode nav) are handled directly.  Other actions
        (RELEASE, RESTART, PREVIEW_HISTORY) are put back so the caller
        can handle them after the enhancement finishes.
        """
        try:
            while True:
                action = await self._actions.get()
                if action == Action.CANCEL:
                    cancel_event.set()
                    return
                if action == Action.CONFIRM_ASR and confirm_asr_event is not None:
                    confirm_asr_event.set()
                    return
                if action in (Action.MODE_PREV, Action.MODE_NEXT):
                    self._handle_inline_action(action)
                else:
                    # Put back unhandled actions for the caller
                    self._actions.put_nowait(action)
                    return
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Preview routing helper
    # ------------------------------------------------------------------

    async def _route_to_preview(
        self,
        asr_text: str | None,
        audio_duration: float,
        wav_data: bytes,
    ) -> None:
        """Route to preview panel via executor thread.

        The preview controller blocks on ``result_event.wait()``
        internally, so it must run in an executor, not on the asyncio
        loop.
        """
        app = self._app
        use_enhance = bool(app._enhancer and app._enhancer.is_active)
        logger.debug("Routing to preview flow (asr_text=%s)",
                      "background STT" if asr_text is None else "ready")
        if asr_text is not None:
            self._fire_scripting_event("transcription_done", asr_text=asr_text)
        await self._loop.run_in_executor(
            None,
            lambda: app._do_transcribe_with_preview(
                asr_text=asr_text,
                use_enhance=use_enhance,
                audio_duration=audio_duration,
                wav_data=wav_data,
            ),
        )
        app._busy = False
        logger.debug("Preview flow done, session done")

    # ------------------------------------------------------------------
    # Direct output flow (no preview panel)
    # ------------------------------------------------------------------

    async def _do_direct_flow(
        self,
        asr_text: str | None,
        wav_data: bytes,
        audio_duration: float,
    ) -> None:
        from PyObjCTools import AppHelper

        app = self._app
        use_enhance = bool(app._enhancer and app._enhancer.is_active)
        need_stt = asr_text is None

        try:
            app._usage_stats.record_transcription(
                mode="direct", enhance_mode=app._enhance_mode
            )
        except Exception as e:
            logger.error("Failed to record usage stats: %s", e)

        text = asr_text or ""
        enhanced_text = None
        cancel_event = asyncio.Event()

        if use_enhance:
            initial_status = (
                "statusbar.status.transcribing" if need_stt
                else "statusbar.status.enhancing"
            )
            AppHelper.callAfter(app._set_status, initial_status)

            # Enter → output ASR directly, ESC → discard all.
            confirm_asr_event = asyncio.Event()
            cancel_watcher = asyncio.create_task(
                self._watch_cancel(cancel_event, confirm_asr_event)
            )

            stt_info = app._current_stt_model()
            llm_info = app._current_llm_model()

            AppHelper.callAfter(
                self._show_streaming_overlay,
                asr_text or "", stt_info, llm_info, True,
            )

            # Background STT if needed
            if need_stt:
                asr_text = await self._background_stt(
                    wav_data, cancel_event, skip_punc=True
                )
                if cancel_event.is_set():
                    cancel_watcher.cancel()
                    AppHelper.callAfter(app._streaming_overlay.close)
                    AppHelper.callAfter(
                        app._set_status, "statusbar.status.ready"
                    )
                    return
                if not asr_text:
                    cancel_watcher.cancel()
                    AppHelper.callAfter(app._streaming_overlay.close)
                    AppHelper.callAfter(
                        app._set_status, "statusbar.status.empty"
                    )
                    return
                text = asr_text

                # Enter pressed during STT: skip enhancement, output ASR
                if confirm_asr_event.is_set():
                    logger.debug("Enter during STT: skipping enhance")
                    cancel_watcher.cancel()
                    AppHelper.callAfter(
                        app._streaming_overlay.close_now
                    )
                    self._fire_scripting_event(
                        "transcription_done", asr_text=asr_text
                    )
                else:
                    AppHelper.callAfter(
                        app._set_status, "statusbar.status.enhancing"
                    )

            if not confirm_asr_event.is_set():
                self._fire_scripting_event(
                    "transcription_done", asr_text=asr_text
                )

                # Resolve chain steps
                try:
                    current_mode_def = app._enhancer.get_mode_definition(
                        app._enhance_mode
                    )
                    chain_steps: list[str] = []
                    if current_mode_def and current_mode_def.steps:
                        for step_id in current_mode_def.steps:
                            step_def = app._enhancer.get_mode_definition(
                                step_id
                            )
                            if step_def:
                                chain_steps.append(step_id)
                            else:
                                logger.warning(
                                    "Chain step '%s' not found, skipping",
                                    step_id,
                                )

                    # Both cancel_event and confirm_asr_event abort the stream
                    abort_event, abort_tasks = _merge_events(
                        cancel_event, confirm_asr_event
                    )

                    try:
                        if chain_steps:
                            text = await self._run_direct_chain_stream(
                                asr_text, chain_steps, abort_event
                            )
                        else:
                            text = await self._run_direct_single_stream(
                                asr_text, abort_event
                            )
                    finally:
                        for t in abort_tasks:
                            t.cancel()
                        results = await asyncio.gather(
                            *abort_tasks, return_exceptions=True
                        )
                        for r in results:
                            if isinstance(r, Exception) and not isinstance(
                                r, asyncio.CancelledError
                            ):
                                logger.warning(
                                    "abort waiter error: %s", r
                                )

                    if confirm_asr_event.is_set():
                        logger.debug("Enter during enhance: using ASR text")
                        text = asr_text
                        enhanced_text = None
                    elif cancel_event.is_set():
                        text = asr_text
                        enhanced_text = None
                    else:
                        enhanced_text = text
                        self._fire_scripting_event(
                            "enhancement_done", enhanced_text=enhanced_text
                        )
                except Exception as e:
                    logger.error("AI enhancement failed: %s", e)
                    text = asr_text
                finally:
                    cancel_watcher.cancel()
                    if cancel_event.is_set() or confirm_asr_event.is_set():
                        # Use _do_close directly so the panel is removed
                        # before type_text runs in the same callAfter queue.
                        AppHelper.callAfter(
                            app._streaming_overlay.close_now
                        )
                    else:
                        AppHelper.callAfter(
                            app._streaming_overlay.close_with_delay
                        )
        else:
            # No enhancement — show overlay for background STT if needed.
            # Streaming already has ASR text; just hide the indicator.
            if need_stt:
                cancel_watcher = asyncio.create_task(
                    self._watch_cancel(cancel_event)
                )
                AppHelper.callAfter(
                    self._show_streaming_overlay,
                    "", app._current_stt_model(), "",
                )

                asr_text = await self._background_stt(
                    wav_data, cancel_event
                )
                cancel_watcher.cancel()
                if cancel_event.is_set() or not asr_text:
                    AppHelper.callAfter(app._streaming_overlay.close)
                    AppHelper.callAfter(
                        app._set_status,
                        "statusbar.status.ready"
                        if cancel_event.is_set()
                        else "statusbar.status.empty",
                    )
                    return
                text = asr_text
                AppHelper.callAfter(
                    app._streaming_overlay.close_with_delay
                )
            else:
                AppHelper.callAfter(app._recording_indicator.hide)

            self._fire_scripting_event(
                "transcription_done", asr_text=asr_text
            )

        logger.debug(
            "Direct flow output: cancel=%s, confirm_asr=%s, text=%r",
            cancel_event.is_set(),
            confirm_asr_event.is_set() if use_enhance else "N/A",
            text[:50] if text else None,
        )

        if cancel_event.is_set():
            AppHelper.callAfter(
                app._set_status, "statusbar.status.ready"
            )
            return

        text = text.strip()

        self._fire_scripting_event(
            "output_text", final_text=text
        )

        # Reactivate the app that was frontmost when the hotkey was pressed,
        # in case the user switched windows during AI enhancement.
        if self._target_app is not None:
            AppHelper.callAfter(reactivate_app, self._target_app)
            await asyncio.sleep(0.15)

        AppHelper.callAfter(
            type_text,
            text,
            append_newline=app._append_newline,
            method=app._output_method,
        )
        AppHelper.callAfter(app._set_status, "statusbar.status.ready")

        try:
            app._usage_stats.record_confirm(modified=False)
        except Exception as e:
            logger.error("Failed to record usage stats: %s", e)
        try:
            app._usage_stats.record_output_method(copy_to_clipboard=False)
        except Exception as e:
            logger.error("Failed to record output method: %s", e)

        self._target_app = None

        try:
            app._conversation_history.log(
                asr_text=asr_text,
                enhanced_text=enhanced_text,
                final_text=text,
                enhance_mode=app._enhance_mode,
                preview_enabled=False,
                stt_model=app._current_stt_model(),
                llm_model=app._current_llm_model(),
                audio_duration=getattr(app, "_last_audio_duration", 0.0),
                input_context=self._input_context,
            )
        except Exception as e:
            logger.error("Failed to log conversation: %s", e)

    # ------------------------------------------------------------------
    # Overlay helpers for direct flow
    # ------------------------------------------------------------------

    def _show_streaming_overlay(
        self,
        asr_text: str,
        stt_info: str,
        llm_info: str,
        with_confirm_asr: bool = False,
    ) -> None:
        """Hide recording indicator and show the streaming overlay immediately.

        Must be called on the main thread (via AppHelper.callAfter).
        """
        app = self._app
        indicator_frame = app._recording_indicator.current_frame
        app._recording_indicator.hide()
        app._streaming_overlay.show(
            asr_text=asr_text,
            animate_from_frame=indicator_frame,
            stt_info=stt_info,
            llm_info=llm_info,
            on_cancel=lambda: self.send_action(Action.CANCEL),
            on_confirm_asr=(
                lambda: self.send_action(Action.CONFIRM_ASR)
            ) if with_confirm_asr else None,
        )

    @staticmethod
    def _show_error_alert(message: str, duration: float = 3.0) -> None:
        """Show a lightweight floating alert for errors."""
        try:
            from wenzi.scripting.api.alert import alert
            alert(message, duration=duration)
        except Exception:
            logger.debug("Error alert failed", exc_info=True)

    def _apply_timeout_fallback(
        self, app, chunk: str, step_idx: int = 0, total_steps: int = 0,
    ) -> tuple[list[str], int]:
        """Update overlay with fallback text on AI timeout."""
        completion_tokens = len(chunk)
        app._streaming_overlay.clear_text()
        app._streaming_overlay.append_text(chunk, completion_tokens=completion_tokens)
        if total_steps > 0:
            msg = (
                f"\u26a0\ufe0f Step {step_idx}/{total_steps}: "
                "AI timed out, using original text"
            )
        else:
            msg = "\u26a0\ufe0f AI timed out, using original text"
        app._streaming_overlay.set_status(msg)
        self._show_error_alert("AI enhancement timed out")
        return [chunk], completion_tokens

    # ------------------------------------------------------------------
    # Background STT for direct flow
    # ------------------------------------------------------------------

    async def _background_stt(
        self,
        wav_data: bytes,
        cancel_event: asyncio.Event,
        skip_punc: bool = False,
    ) -> str | None:
        """Run STT in background, update overlay when done.

        Returns the transcribed text (stripped), or None on empty/failure.
        """
        from PyObjCTools import AppHelper

        app = self._app
        app._transcriber.skip_punc = skip_punc
        hotwords, _ = app._build_dynamic_hotwords()

        text = await self._loop.run_in_executor(
            None, lambda: app._transcriber.transcribe(
                wav_data, hotwords=hotwords
            )
        )

        if cancel_event.is_set():
            return None

        asr_text = (text or "").strip()
        logger.debug("Background STT result: %r", asr_text[:100] if asr_text else None)

        if asr_text:
            AppHelper.callAfter(
                app._streaming_overlay.set_asr_text, asr_text
            )

        return asr_text or None

    # ------------------------------------------------------------------
    # Streaming enhancement helpers (native async — no event loop hacks)
    # ------------------------------------------------------------------

    @staticmethod
    async def _iter_or_cancel(gen, cancel_event: asyncio.Event):
        """Iterate an async generator, aborting immediately on cancel.

        Races each ``__anext__`` against ``cancel_event.wait()`` so
        cancellation is detected even while blocked waiting for a chunk.
        The generator is always closed (``aclose``) on exit.
        """
        aiter = gen.__aiter__()
        cancel_fut = asyncio.ensure_future(cancel_event.wait())
        try:
            while True:
                next_fut = asyncio.ensure_future(aiter.__anext__())
                done, _ = await asyncio.wait(
                    [next_fut, cancel_fut],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_fut in done:
                    next_fut.cancel()
                    # Wait for __anext__ to finish cancellation before
                    # closing the generator (avoids "already running").
                    try:
                        await next_fut
                    except (asyncio.CancelledError, StopAsyncIteration):
                        pass
                    return
                try:
                    yield next_fut.result()
                except StopAsyncIteration:
                    return
        finally:
            cancel_fut.cancel()
            await gen.aclose()

    async def _run_direct_single_stream(
        self, asr_text: str, cancel_event: asyncio.Event,
    ) -> str:
        """Single-step streaming enhancement, updating overlay."""
        app = self._app
        collected: list[str] = []
        usage = None
        completion_tokens = 0
        thinking_tokens = 0
        had_thinking = False

        gen = app._enhancer.enhance_stream(
            asr_text, input_context=self._input_context
        )
        async for chunk, chunk_usage, is_thinking in self._iter_or_cancel(
            gen, cancel_event
        ):
            if is_thinking == "retry" and chunk:
                had_thinking = True
                app._streaming_overlay.append_thinking_text(chunk)
                label = chunk.strip().strip("()\n")
                app._streaming_overlay.set_status(f"\u23f3 {label}")
            elif is_thinking == "timeout" and chunk:
                had_thinking = False
                collected, completion_tokens = self._apply_timeout_fallback(
                    app, chunk,
                )
                break
            elif is_thinking and chunk:
                had_thinking = True
                thinking_tokens += len(chunk)
                app._streaming_overlay.append_thinking_text(
                    chunk, thinking_tokens=thinking_tokens
                )
            elif chunk:
                if had_thinking:
                    had_thinking = False
                    app._streaming_overlay.clear_text()
                collected.append(chunk)
                completion_tokens += len(chunk)
                app._streaming_overlay.append_text(
                    chunk, completion_tokens=completion_tokens
                )
            if chunk_usage is not None:
                usage = chunk_usage

        if usage:
            try:
                app._usage_stats.record_token_usage(usage)
            except Exception as e:
                logger.error("Failed to record token usage: %s", e)
            app._streaming_overlay.set_complete(usage)

        return "".join(collected).strip() or asr_text

    async def _run_direct_chain_stream(
        self,
        asr_text: str,
        chain_steps: list[str],
        cancel_event: asyncio.Event,
    ) -> str:
        """Multi-step chain streaming enhancement, updating overlay."""
        app = self._app
        total_steps = len(chain_steps)
        input_text = asr_text
        original_mode = app._enhancer.mode
        total_usage: dict[str, int] = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }

        try:
            for step_idx, step_id in enumerate(chain_steps, 1):
                if cancel_event.is_set():
                    break

                step_def = app._enhancer.get_mode_definition(step_id)
                step_label = step_def.label if step_def else step_id

                app._streaming_overlay.set_status(
                    f"\u23f3 Step {step_idx}/{total_steps}: {step_label}"
                )
                app._streaming_overlay.set_progress(step_idx, total_steps)

                if step_idx > 1:
                    app._streaming_overlay.clear_text()

                app._enhancer.mode = step_id

                collected: list[str] = []
                step_usage = None
                completion_tokens = 0
                thinking_tokens = 0

                gen = app._enhancer.enhance_stream(
                    input_text, input_context=self._input_context
                )
                async for chunk, chunk_usage, is_thinking in self._iter_or_cancel(
                    gen, cancel_event
                ):
                    if is_thinking == "retry" and chunk:
                        app._streaming_overlay.append_thinking_text(chunk)
                        label = chunk.strip().strip("()\n")
                        app._streaming_overlay.set_status(
                            f"\u23f3 Step {step_idx}/{total_steps}: {label}"
                        )
                    elif is_thinking == "timeout" and chunk:
                        collected, completion_tokens = self._apply_timeout_fallback(
                            app, chunk, step_idx, total_steps,
                        )
                        break
                    elif is_thinking and chunk:
                        thinking_tokens += len(chunk)
                        app._streaming_overlay.append_thinking_text(
                            chunk, thinking_tokens=thinking_tokens
                        )
                    elif chunk:
                        collected.append(chunk)
                        completion_tokens += len(chunk)
                        app._streaming_overlay.append_text(
                            chunk, completion_tokens=completion_tokens
                        )
                    if chunk_usage is not None:
                        step_usage = chunk_usage

                step_result = "".join(collected).strip()
                if step_result:
                    input_text = step_result

                if step_usage:
                    total_usage["prompt_tokens"] += step_usage.get(
                        "prompt_tokens", 0
                    )
                    total_usage["completion_tokens"] += step_usage.get(
                        "completion_tokens", 0
                    )
                    total_usage["total_tokens"] += step_usage.get(
                        "total_tokens", 0
                    )
                try:
                    app._usage_stats.record_token_usage(step_usage)
                except Exception as e:
                    logger.error("Failed to record token usage: %s", e)

            if total_usage["total_tokens"] > 0:
                app._streaming_overlay.set_complete(total_usage)

            return input_text.strip() or asr_text
        finally:
            app._enhancer.mode = original_mode

    # ------------------------------------------------------------------
    # Streaming transcription
    # ------------------------------------------------------------------

    def _start_streaming_if_supported(self) -> bool:
        """Start streaming transcription synchronously. Returns success."""
        from PyObjCTools import AppHelper

        app = self._app
        if not app._transcriber.supports_streaming:
            return False
        try:
            def _on_partial(text: str, is_final: bool) -> None:
                AppHelper.callAfter(self._update_live_overlay, text)

            app._transcriber.start_streaming(_on_partial)
            app._recorder.set_on_audio_chunk(app._transcriber.feed_audio)

            # Activate the overlay (already shown in faded state)
            if self._live_overlay is not None:
                AppHelper.callAfter(self._live_overlay.set_active)
            else:
                AppHelper.callAfter(self._show_live_overlay)
            logger.info("Streaming transcription started")
            return True
        except Exception:
            logger.exception("Failed to start streaming, will use batch mode")
            return False

    def _stop_streaming(self, was_active: bool) -> None:
        """Stop streaming transcription if it was active."""
        if not was_active:
            return
        app = self._app
        app._recorder.clear_on_audio_chunk()
        try:
            app._transcriber.stop_streaming()
        except Exception:
            logger.exception("Failed to stop streaming")

    # ------------------------------------------------------------------
    # Level polling
    # ------------------------------------------------------------------

    async def _poll_level(self) -> None:
        """Poll audio level and update the indicator."""
        from PyObjCTools import AppHelper

        app = self._app
        last_level = -1.0
        try:
            while True:
                level = app._recorder.current_level
                if abs(level - last_level) > 0.02:
                    AppHelper.callAfter(
                        app._recording_indicator.update_level, level
                    )
                    last_level = level
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _show_live_overlay(self, active: bool = True) -> None:
        """Show the live transcription overlay (must be called on main thread)."""
        try:
            app = self._app
            if hasattr(app, "_live_overlay") and app._live_overlay is not None:
                self._live_overlay = app._live_overlay
            else:
                from wenzi.ui.live_transcription_overlay import (
                    LiveTranscriptionOverlay,
                )
                self._live_overlay = LiveTranscriptionOverlay()
            self._live_overlay.show(active=active)
        except Exception:
            logger.exception("Failed to show live overlay")

    def _update_live_overlay(self, text: str) -> None:
        if self._live_overlay is not None:
            self._live_overlay.update_text(text)

    def _hide_live_overlay(self) -> None:
        from PyObjCTools import AppHelper

        def _close():
            from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay
            LiveTranscriptionOverlay.close_all()
            self._live_overlay = None
        AppHelper.callAfter(_close)

    def _stop_indicator(self, animate: bool = False) -> None:
        """Stop level polling and optionally hide the indicator."""
        from PyObjCTools import AppHelper

        self._cancel_level_task()
        if not animate:
            AppHelper.callAfter(self._app._recording_indicator.hide)

    def _reset_to_idle(self) -> None:
        """Common cleanup: hide overlays/indicator and restore idle status."""
        self._app._busy = False
        self._target_app = None
        self._hide_live_overlay()
        self._cancel_level_task()
        self._app._recording_indicator.hide()
        self._app._set_status("statusbar.status.ready")
        self._restore_mode()

    def _reset_and_show_preview(self) -> None:
        """Reset to idle and show the last preview history record."""
        self._reset_to_idle()
        self._app._preview_controller.on_show_last_preview()

    def _cancel_subtasks(self) -> None:
        """Cancel level polling task."""
        self._cancel_level_task()

    def _drain_actions(self) -> None:
        """Discard all pending actions from the queue."""
        while not self._actions.empty():
            try:
                self._actions.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _cancel_level_task(self) -> None:
        if self._level_task and not self._level_task.done():
            self._level_task.cancel()
            self._level_task = None

    # ------------------------------------------------------------------
    # Mode management (carried over from RecordingController)
    # ------------------------------------------------------------------

    def _apply_prefer_mode(self, mode: str) -> None:
        app = self._app
        self._saved_mode = (
            app._enhance_mode,
            app._enhancer.mode if app._enhancer else None,
            app._enhancer._enabled if app._enhancer else None,
        )
        self._switch_active_mode(mode)
        logger.info(
            "Prefer mode applied: %s, saved: %s", mode, self._saved_mode[0]
        )

    def _restore_mode(self) -> None:
        self._no_preview_override = False
        if self._saved_mode is None:
            return
        from PyObjCTools import AppHelper

        app = self._app
        orig_mode, orig_enhancer_mode, orig_enhancer_enabled = self._saved_mode
        self._saved_mode = None

        app._enhance_mode = orig_mode
        app._enhance_controller.enhance_mode = orig_mode

        if app._enhancer:
            if orig_enhancer_mode is not None:
                app._enhancer.mode = orig_enhancer_mode
            if orig_enhancer_enabled is not None:
                app._enhancer._enabled = orig_enhancer_enabled

        for m, item in app._enhance_menu_items.items():
            AppHelper.callAfter(
                lambda i=item, s=(1 if m == orig_mode else 0): i.setState_(s)
            )
        logger.info("Mode restored to: %s", orig_mode)

    def _switch_active_mode(self, mode: str) -> None:
        from wenzi.enhance.enhancer import MODE_OFF

        app = self._app
        app._enhance_mode = mode
        app._enhance_controller.enhance_mode = mode
        if app._enhancer:
            if mode == MODE_OFF:
                app._enhancer._enabled = False
            else:
                app._enhancer._enabled = True
                app._enhancer.mode = mode

    def _build_mode_list(self) -> list[tuple[str, str]]:
        from wenzi.enhance.enhancer import MODE_OFF

        app = self._app
        modes: list[tuple[str, str]] = [(MODE_OFF, "Off")]
        if app._enhancer:
            modes.extend(app._enhancer.available_modes)
        return modes

    def _navigate_mode(self, delta: int) -> None:
        if self._no_preview_override:
            return
        from PyObjCTools import AppHelper

        modes = self._build_mode_list()
        if len(modes) <= 1:
            return
        current = self._app._enhance_mode
        idx = next(
            (i for i, (mid, _) in enumerate(modes) if mid == current), -1
        )
        new_idx = idx + delta
        if idx < 0 or new_idx < 0 or new_idx >= len(modes):
            return

        new_mode = modes[new_idx][0]
        if self._saved_mode is None:
            self._apply_prefer_mode(new_mode)
        else:
            self._switch_active_mode(new_mode)

        label = modes[new_idx][1]
        AppHelper.callAfter(
            self._app._recording_indicator.update_mode,
            label,
        )
        logger.info(
            "Mode nav %s → %s", "prev" if delta < 0 else "next", new_mode
        )

    def _show_mode_on_indicator(self) -> None:
        if self._no_preview_override:
            return
        from PyObjCTools import AppHelper

        modes = self._build_mode_list()
        if len(modes) <= 1:
            return
        current = self._app._enhance_mode
        idx = next(
            (i for i, (mid, _) in enumerate(modes) if mid == current), -1
        )
        if idx < 0:
            return
        label = modes[idx][1]
        AppHelper.callAfter(
            self._app._recording_indicator.update_mode,
            label,
        )

    # ------------------------------------------------------------------
    # Feedback toggles (kept for menu item callbacks)
    # ------------------------------------------------------------------

    def on_sound_feedback_toggle(self, sender) -> None:
        app = self._app
        app._sound_manager.enabled = not app._sound_manager.enabled
        sender.state = 1 if app._sound_manager.enabled else 0
        fb_cfg = app._config.setdefault("feedback", {})
        fb_cfg["sound_enabled"] = app._sound_manager.enabled
        save_config(app._config, app._config_path)

    def on_visual_indicator_toggle(self, sender) -> None:
        app = self._app
        app._recording_indicator.enabled = not app._recording_indicator.enabled
        sender.state = 1 if app._recording_indicator.enabled else 0
        fb_cfg = app._config.setdefault("feedback", {})
        fb_cfg["visual_indicator"] = app._recording_indicator.enabled
        save_config(app._config, app._config_path)

    # ------------------------------------------------------------------
    # Scripting events
    # ------------------------------------------------------------------

    def _fire_scripting_event(self, event_name: str, **kwargs) -> None:
        fire_scripting_event(self._app, event_name, **kwargs)

    # ------------------------------------------------------------------
    # Voice input initialization (delegated to main thread)
    # ------------------------------------------------------------------

    def _try_enable_voice_input(self) -> None:
        """Attempt to initialize voice input (runs on main thread via callAfter)."""
        app = self._app
        try:
            from wenzi.transcription.apple import check_siri_available

            ok, _ = check_siri_available()
            if not ok:
                from wenzi.transcription.apple import prompt_siri_setup
                choice = prompt_siri_setup()
                app._handle_dictation_setup_choice(choice)
                return

            app._transcriber.initialize()
            app._voice_input_available = True
            app._set_status("statusbar.status.ready")
            logger.info("Voice input enabled after deferred initialization")
        except Exception:
            logger.debug("Deferred voice init failed, prompting user")
            from wenzi.transcription.apple import prompt_siri_setup
            choice = prompt_siri_setup()
            app._handle_dictation_setup_choice(choice)
