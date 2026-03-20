"""Recording and direct transcription flow extracted from WenZiApp."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from wenzi.app import WenZiApp

from wenzi.config import save_config
from wenzi.input import type_text

logger = logging.getLogger(__name__)


class RecordingController:
    """Handles hotkey → recording → transcription → output flow."""

    def __init__(self, app: WenZiApp) -> None:
        self._app = app
        self._streaming_active = False
        self._live_overlay = None
        self._prefer_mode: Optional[str] = None
        # Saved state for restoring after a per-hotkey mode override
        self._saved_mode: Optional[tuple] = None  # (enhance_mode, enhancer_mode, enhancer_enabled)
        # Set by on_cancel_recording to abort a pending _delayed_start
        self._cancel_delayed = threading.Event()
        # Watchdog timer: auto-stop recording if hotkey release event is lost
        self._recording_watchdog: Optional[threading.Timer] = None
        # Guard against watchdog + normal release racing into on_hotkey_release
        self._release_lock = threading.Lock()
        self._release_done = False

    def _fire_scripting_event(self, event_name: str, **kwargs) -> None:
        """Fire a scripting event if the script engine is available."""
        engine = getattr(self._app, "_script_engine", None)
        if engine is None:
            return
        try:
            engine.wz._registry.fire_event(event_name, **kwargs)
        except Exception:
            logger.debug("Failed to fire scripting event %s", event_name)

    # ------------------------------------------------------------------
    # Recording watchdog — auto-stop if hotkey release event is lost
    # ------------------------------------------------------------------

    def _start_recording_watchdog(self) -> None:
        """Start a watchdog timer that auto-stops recording after max seconds."""
        self._cancel_recording_watchdog()
        max_sec = self._app._config.get("audio", {}).get(
            "max_recording_seconds", 120
        )
        if max_sec and max_sec > 0:
            self._recording_watchdog = threading.Timer(
                max_sec, self._on_recording_timeout
            )
            self._recording_watchdog.daemon = True
            self._recording_watchdog.start()

    def _cancel_recording_watchdog(self) -> None:
        """Cancel the watchdog timer if running."""
        if self._recording_watchdog is not None:
            self._recording_watchdog.cancel()
            self._recording_watchdog = None

    def _on_recording_timeout(self) -> None:
        """Called when recording exceeds the maximum allowed duration."""
        if not self._app._recorder.is_recording:
            return
        logger.warning(
            "Recording watchdog triggered — auto-stopping "
            "(possible missed hotkey release)"
        )
        self.on_hotkey_release()

    _voice_init_lock = threading.Lock()

    def _try_enable_voice_input(self) -> None:
        """Attempt to initialize voice input when user presses the hotkey.

        Called when _voice_input_available is False. Tries to initialize
        the transcriber (in case user enabled Dictation since startup).
        On success, marks voice input as available. On failure, shows a
        three-option setup dialog (Open Settings / Cancel / Don't Ask Again).

        Uses a lock to prevent concurrent initialization from rapid
        hotkey presses.
        """
        if not self._voice_init_lock.acquire(blocking=False):
            return  # another attempt already in progress
        app = self._app

        def _attempt():
            try:
                from wenzi.transcription.apple import check_siri_available

                ok, _ = check_siri_available()
                if not ok:
                    self._show_dictation_setup(app)
                    return

                app._transcriber.initialize()
                app._voice_input_available = True
                app._set_status("WZ")
                logger.info("Voice input enabled after deferred initialization")
            except Exception:
                logger.debug("Deferred voice init failed, prompting user")
                self._show_dictation_setup(app)
            finally:
                self._voice_init_lock.release()

        threading.Thread(target=_attempt, daemon=True).start()

    @staticmethod
    def _show_dictation_setup(app) -> None:
        """Show the three-option Dictation setup dialog and handle the choice."""
        from wenzi.transcription.apple import prompt_siri_setup

        choice = prompt_siri_setup()
        app._handle_dictation_setup_choice(choice)

    def on_hotkey_press(self, key_name: str = "") -> None:
        """Called when hotkey is pressed down - start recording."""
        app = self._app
        if app._config_degraded:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(app._show_config_error_alert)
            return
        if not app._voice_input_available:
            self._try_enable_voice_input()
            return
        if app._busy:
            return

        # Allow on_hotkey_release to proceed (reset from previous cycle)
        self._release_done = False

        # Restore previous override before applying a new one
        self._restore_mode()

        # Extract prefer_mode from hotkey config and apply override
        self._prefer_mode = None
        hotkey_value = app._config.get("hotkeys", {}).get(key_name)
        if isinstance(hotkey_value, dict):
            prefer_mode = hotkey_value.get("mode")
            if prefer_mode is not None:
                self._prefer_mode = prefer_mode
                self._apply_prefer_mode(prefer_mode)

        logger.info("Hotkey pressed, starting recording")
        self._fire_scripting_event("recording_start")
        app._set_status("Recording...")
        app._sound_manager.play("start")
        if app._sound_manager.enabled:
            app._usage_stats.record_sound_feedback()

        app._recording_started.clear()
        self._cancel_delayed.clear()

        def _delayed_start():
            import time
            time.sleep(0.35)
            if not app._busy and not self._cancel_delayed.is_set():
                self._start_recording_and_update_indicator()
            app._recording_started.set()

        if app._sound_manager.enabled:
            # Show indicator immediately in grayscale while sound plays
            initial_dev = app._recorder.last_device_name if app._recording_indicator.show_device_name else None
            self.start_recording_indicator(initial_dev)
            self._show_mode_on_indicator()
            if app._transcriber.supports_streaming:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(self._show_live_overlay, False)
            threading.Thread(target=_delayed_start, daemon=True).start()
        else:
            # No sound delay — start recording first, then show in active state
            self._start_recording_and_update_indicator(show_active=True)
            app._recording_started.set()

        self._start_recording_watchdog()

    def _apply_prefer_mode(self, mode: str) -> None:
        """Temporarily override the enhance mode for this recording session."""
        app = self._app

        # Save current state for later restore
        self._saved_mode = (
            app._enhance_mode,
            app._enhancer.mode if app._enhancer else None,
            app._enhancer._enabled if app._enhancer else None,
        )

        self._switch_active_mode(mode)

        logger.info("Prefer mode applied: %s, saved: %s",
                     mode, self._saved_mode[0])

    def _restore_mode(self) -> None:
        """Restore the original enhance mode after a per-hotkey override."""
        if self._saved_mode is None:
            return

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

        # Sync menu checkmarks back
        for m, item in app._enhance_menu_items.items():
            from PyObjCTools import AppHelper
            AppHelper.callAfter(
                lambda i=item, s=(1 if m == orig_mode else 0): i.setState_(s)
            )

        logger.info("Mode restored to: %s", orig_mode)

    def _build_mode_list(self) -> List[Tuple[str, str]]:
        """Return ordered list of (mode_id, label) including Off."""
        from wenzi.enhance.enhancer import MODE_OFF

        app = self._app
        modes: List[Tuple[str, str]] = [(MODE_OFF, "Off")]
        if app._enhancer:
            modes.extend(app._enhancer.available_modes)
        return modes

    def _switch_active_mode(self, mode: str) -> None:
        """Set the enhance mode on app and enhancer without saving/restoring."""
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

    def _update_indicator_mode(self, modes: List[Tuple[str, str]], idx: int) -> None:
        """Update the indicator with mode label and nav arrows."""
        from PyObjCTools import AppHelper

        label = modes[idx][1]
        can_prev = idx > 0
        can_next = idx < len(modes) - 1
        AppHelper.callAfter(
            self._app._recording_indicator.update_mode, label, can_prev, can_next
        )

    def _show_mode_on_indicator(self) -> None:
        """Show the current mode label with nav hints on the indicator."""
        modes = self._build_mode_list()
        if len(modes) <= 1:
            return

        current = self._app._enhance_mode
        idx = next((i for i, (mid, _) in enumerate(modes) if mid == current), -1)
        if idx < 0:
            return

        self._update_indicator_mode(modes, idx)

    def _navigate_mode(self, delta: int) -> None:
        """Move to the next (+1) or previous (-1) mode while recording."""
        modes = self._build_mode_list()
        if len(modes) <= 1:
            return

        current = self._app._enhance_mode
        idx = next((i for i, (mid, _) in enumerate(modes) if mid == current), -1)
        new_idx = idx + delta
        if idx < 0 or new_idx < 0 or new_idx >= len(modes):
            return  # at boundary or not found

        new_mode = modes[new_idx][0]

        # Save original mode on first arrow key change
        if self._saved_mode is None:
            self._apply_prefer_mode(new_mode)
        else:
            self._switch_active_mode(new_mode)

        self._update_indicator_mode(modes, new_idx)
        logger.info("Mode nav %s → %s", "prev" if delta < 0 else "next", new_mode)

    def on_mode_prev(self) -> None:
        """Switch to the previous mode while recording."""
        self._navigate_mode(-1)

    def on_mode_next(self) -> None:
        """Switch to the next mode while recording."""
        self._navigate_mode(+1)

    def _start_recording_and_update_indicator(self, show_active: bool = False) -> None:
        """Start the recorder and update the indicator with the device name.

        Args:
            show_active: If True, the indicator and live overlay have not been
                shown yet.  Show them now directly in active (color) state so
                the user never sees the grayscale phase.
        """
        from PyObjCTools import AppHelper

        app = self._app
        dev_name = app._recorder.start()
        self._start_streaming_if_supported()

        if show_active:
            # No sound delay path — show everything in active state at once
            indicator_dev = dev_name if app._recording_indicator.show_device_name else None
            self.start_recording_indicator(indicator_dev)
            self._show_mode_on_indicator()
            AppHelper.callAfter(app._recording_indicator.set_recording_active)
        else:
            # Sound delay path — indicator already visible in grayscale
            if dev_name and app._recording_indicator.show_device_name:
                AppHelper.callAfter(app._recording_indicator.update_device_name, dev_name)
            AppHelper.callAfter(app._recording_indicator.set_recording_active)

    def on_restart_recording(self) -> None:
        """Called when restart key (space) is pressed during recording."""
        self._cancel_recording_watchdog()
        with self._release_lock:
            self._release_done = False
        app = self._app
        if not app._recorder.is_recording:
            return
        logger.info("Restart key pressed, restarting recording")

        # Stop streaming if active
        if self._streaming_active:
            app._recorder.clear_on_audio_chunk()
            try:
                app._transcriber.stop_streaming()
            except Exception:
                logger.exception("Failed to stop streaming during restart")
            self._streaming_active = False
            self._hide_live_overlay()

        # Stop current recording and discard audio
        app._recorder.stop()

        # Stop indicator and level polling
        self.stop_recording_indicator()

        # Reset state
        app._recording_started.clear()
        self._cancel_delayed.clear()

        # Replay prompt sound and restart recording
        app._set_status("Recording...")
        app._sound_manager.play("start")
        if app._sound_manager.enabled:
            app._usage_stats.record_sound_feedback()

        def _delayed_start():
            import time
            time.sleep(0.35)
            if not app._busy and not self._cancel_delayed.is_set():
                self._start_recording_and_update_indicator()
            app._recording_started.set()

        if app._sound_manager.enabled:
            initial_dev = app._recorder.last_device_name if app._recording_indicator.show_device_name else None
            self.start_recording_indicator(initial_dev)
            self._show_mode_on_indicator()
            if app._transcriber.supports_streaming:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(self._show_live_overlay, False)
            threading.Thread(target=_delayed_start, daemon=True).start()
        else:
            self._start_recording_and_update_indicator(show_active=True)
            app._recording_started.set()

        self._start_recording_watchdog()

    def on_preview_history(self) -> None:
        """Called when preview_history_key is pressed during recording — cancel and show history."""
        self.on_cancel_recording()
        # Show last preview history record
        self._app._preview_controller.on_show_last_preview()

    def on_cancel_recording(self) -> None:
        """Called when cancel key (cmd) is pressed during recording — discard and stop."""
        self._cancel_recording_watchdog()
        app = self._app
        if not app._recorder.is_recording:
            # Recording hasn't started yet (e.g. still in sound feedback
            # delay).  Signal _delayed_start to skip recorder.start(),
            # then clean up indicator and overlays.
            self._cancel_delayed.set()
            self._hide_live_overlay()
            self.stop_recording_indicator()
            app._recording_started.set()
            app._set_status("WZ")
            self._restore_mode()
            return
        logger.info("Cancel key pressed, cancelling recording")

        # Stop streaming if active
        if self._streaming_active:
            app._recorder.clear_on_audio_chunk()
            try:
                app._transcriber.stop_streaming()
            except Exception:
                logger.exception("Failed to stop streaming during cancel")
            self._streaming_active = False

        # Always hide live overlay (it may have been shown in faded state
        # before streaming actually started)
        self._hide_live_overlay()

        # Stop current recording and discard audio
        app._recorder.stop()

        # Stop indicator
        self.stop_recording_indicator()

        # Reset state
        app._recording_started.clear()
        app._busy = False
        app._set_status("WZ")
        self._restore_mode()

    def on_hotkey_release(self, key_name: str = "") -> None:
        """Called when hotkey is released - stop recording and transcribe."""
        self._cancel_recording_watchdog()
        # Ensure only one thread enters the stop-and-transcribe path
        # (guards against watchdog timer + normal release racing)
        with self._release_lock:
            if self._release_done:
                return
            self._release_done = True
        app = self._app
        # Wait for delayed start to finish (if sound feedback caused a delay)
        if not app._recording_started.wait(timeout=1.0):
            return
        if not app._recorder.is_recording:
            return
        logger.info("Hotkey released, stopping recording")

        streaming_active = self._streaming_active

        # Disconnect audio chunk callback before stopping recorder
        app._recorder.clear_on_audio_chunk()

        wav_data = app._recorder.stop()

        # Record audio duration for usage statistics
        audio_duration = 0.0
        if wav_data:
            try:
                from wenzi.transcription.base import BaseTranscriber
                audio_duration = BaseTranscriber.wav_duration_seconds(wav_data)
                app._usage_stats.record_recording_duration(audio_duration)
            except Exception as e:
                logger.error("Failed to record recording duration: %s", e)
        app._last_audio_duration = audio_duration

        self._fire_scripting_event("recording_stop", audio_duration=audio_duration)

        if streaming_active:
            # Streaming path: get final text from the streaming session
            self._streaming_active = False
            use_enhance = bool(app._enhancer and app._enhancer.is_active)
            self.stop_recording_indicator(
                animate=app._preview_enabled or use_enhance
            )

            app._busy = True

            def _do_streaming_stop():
                from PyObjCTools import AppHelper
                try:
                    text = app._transcriber.stop_streaming()
                    self._hide_live_overlay()
                    if text and text.strip():
                        asr_text = text.strip()
                        if app._preview_enabled:
                            app._do_transcribe_with_preview(
                                asr_text=asr_text,
                                use_enhance=use_enhance,
                                audio_duration=0.0,
                                wav_data=wav_data,
                            )
                        else:
                            self.do_transcribe_direct(asr_text, use_enhance)
                    else:
                        AppHelper.callAfter(app._recording_indicator.hide)
                        app._set_status("(empty)")
                        logger.warning("Streaming transcription returned empty text")
                except Exception as e:
                    logger.error("Streaming stop failed: %s", e)
                    self._hide_live_overlay()
                    AppHelper.callAfter(app._recording_indicator.hide)
                    app._set_status("Error")
                finally:
                    app._busy = False

            threading.Thread(target=_do_streaming_stop, daemon=True).start()
            return

        # Non-streaming (batch) path
        if not wav_data:
            self.stop_recording_indicator()
            app._set_status("WZ")
            return
        use_enhance = bool(app._enhancer and app._enhancer.is_active)
        # Always keep indicator alive for animate-out: preview mode animates
        # into preview panel; direct mode animates into the streaming overlay
        # (which is now shown immediately after recording ends)
        self.stop_recording_indicator(animate=True)

        app._busy = True

        if app._preview_enabled:
            app._set_status("Transcribing...")
            # Show preview immediately, transcribe in background
            def _do_preview():
                try:
                    app._do_transcribe_with_preview(
                        asr_text=None,
                        use_enhance=bool(app._enhancer and app._enhancer.is_active),
                        audio_duration=0.0,
                        wav_data=wav_data,
                    )
                except Exception as e:
                    logger.error("Preview transcription failed: %s", e)
                    app._set_status("Error")
                    app._busy = False

            threading.Thread(target=_do_preview, daemon=True).start()
        else:
            app._set_status("Transcribing...")
            # Show overlay immediately so user knows recording has ended;
            # register cancel_event so ESC can abort transcription too
            stt_info = app._current_stt_model()
            llm_info = app._current_llm_model()
            direct_cancel = threading.Event()

            from PyObjCTools import AppHelper

            def _on_esc_cancel():
                app._busy = False
                app._set_status("WZ")

            def _show_direct_overlay():
                app._recording_indicator.hide()
                app._streaming_overlay.show(
                    asr_text="",
                    cancel_event=direct_cancel,
                    stt_info=stt_info,
                    llm_info=llm_info if use_enhance else "",
                    on_cancel=_on_esc_cancel,
                )

            AppHelper.callAfter(_show_direct_overlay)

            # Run transcription in background to keep UI responsive
            def _do_transcribe():
                try:
                    if direct_cancel.is_set():
                        logger.info("Transcription cancelled via ESC (before start)")
                        return
                    app._transcriber.skip_punc = bool(
                        app._enhancer and app._enhancer.is_active
                    )
                    hotwords, _ = app._build_dynamic_hotwords()
                    text = app._transcriber.transcribe(wav_data, hotwords=hotwords)
                    if direct_cancel.is_set():
                        logger.info("Transcription cancelled via ESC (after transcribe)")
                        return
                    if text and text.strip():
                        asr_text = text.strip()
                        use_enhance_now = bool(
                            app._enhancer and app._enhancer.is_active
                        )
                        self.do_transcribe_direct(
                            asr_text, use_enhance_now,
                            overlay_already_shown=True,
                        )
                    else:
                        AppHelper.callAfter(app._streaming_overlay.close)
                        app._set_status("(empty)")
                        logger.warning("Transcription returned empty text")
                except Exception as e:
                    logger.error("Transcription failed: %s", e)
                    AppHelper.callAfter(app._streaming_overlay.close)
                    app._set_status("Error")
                    from wenzi.ui_helpers import topmost_alert, restore_accessory
                    topmost_alert(
                        title="Transcription Failed",
                        message=(
                            f"Error: {str(e)[:200]}\n\n"
                            "Please check the log or try switching to "
                            "a different model."
                        ),
                    )
                    restore_accessory()
                finally:
                    # Only reset _busy if not cancelled — on_cancel already
                    # reset it, and a new recording may have started since.
                    if not direct_cancel.is_set():
                        app._busy = False

            threading.Thread(target=_do_transcribe, daemon=True).start()

    def _start_streaming_if_supported(self) -> None:
        """If transcriber supports streaming, start a streaming session."""
        app = self._app
        if not app._transcriber.supports_streaming:
            return

        try:
            from PyObjCTools import AppHelper

            def _on_partial(text: str, is_final: bool) -> None:
                AppHelper.callAfter(self._update_live_overlay, text)

            app._transcriber.start_streaming(_on_partial)
            app._recorder.set_on_audio_chunk(app._transcriber.feed_audio)
            self._streaming_active = True

            # Activate the overlay (already shown in faded state),
            # or show it now if it wasn't pre-created.
            if self._live_overlay is not None:
                AppHelper.callAfter(self._live_overlay.set_active)
            else:
                AppHelper.callAfter(self._show_live_overlay)
            logger.info("Streaming transcription started")
        except Exception:
            logger.exception("Failed to start streaming, will use batch mode")
            self._streaming_active = False

    def _show_live_overlay(self, active: bool = True) -> None:
        """Show the live transcription overlay (must be called on main thread).

        Args:
            active: If False, the overlay is shown in a faded state.
        """
        try:
            app = self._app
            if hasattr(app, "_live_overlay") and app._live_overlay is not None:
                self._live_overlay = app._live_overlay
            else:
                from wenzi.ui.live_transcription_overlay import LiveTranscriptionOverlay
                self._live_overlay = LiveTranscriptionOverlay()
            self._live_overlay.show(active=active)
            logger.info("Live transcription overlay shown (active=%s)", active)
        except Exception:
            logger.exception("Failed to show live overlay")

    def _update_live_overlay(self, text: str) -> None:
        """Update the live transcription overlay text (main thread)."""
        if self._live_overlay is not None:
            self._live_overlay.update_text(text)
            logger.debug("Live overlay updated: %s", text[:50] if text else "(empty)")

    def _hide_live_overlay(self) -> None:
        """Hide and close the live transcription overlay."""
        from PyObjCTools import AppHelper

        def _close():
            if self._live_overlay is not None:
                self._live_overlay.close()
                self._live_overlay = None

        AppHelper.callAfter(_close)

    def start_recording_indicator(self, device_name: Optional[str] = None) -> None:
        """Show visual indicator and start polling audio level."""
        from PyObjCTools import AppHelper

        app = self._app
        AppHelper.callAfter(app._recording_indicator.show, device_name)

        # Stop any existing poll thread
        if app._level_poll_stop is not None:
            app._level_poll_stop.set()

        stop_event = threading.Event()
        app._level_poll_stop = stop_event

        def _poll_level():
            while not stop_event.is_set():
                level = app._recorder.current_level
                AppHelper.callAfter(app._recording_indicator.update_level, level)
                stop_event.wait(0.05)

        threading.Thread(target=_poll_level, daemon=True).start()

    def stop_recording_indicator(self, animate: bool = False) -> None:
        """Hide visual indicator and stop polling.

        Args:
            animate: If True, only stop level polling but don't hide the panel
                     (caller will animate it out separately).
        """
        from PyObjCTools import AppHelper

        app = self._app
        if app._level_poll_stop is not None:
            app._level_poll_stop.set()
            app._level_poll_stop = None
        if not animate:
            AppHelper.callAfter(app._recording_indicator.hide)

    def on_sound_feedback_toggle(self, sender) -> None:
        """Toggle sound feedback on/off."""
        app = self._app
        app._sound_manager.enabled = not app._sound_manager.enabled
        sender.state = 1 if app._sound_manager.enabled else 0

        fb_cfg = app._config.setdefault("feedback", {})
        fb_cfg["sound_enabled"] = app._sound_manager.enabled
        save_config(app._config, app._config_path)

    def on_visual_indicator_toggle(self, sender) -> None:
        """Toggle visual recording indicator on/off."""
        app = self._app
        app._recording_indicator.enabled = not app._recording_indicator.enabled
        sender.state = 1 if app._recording_indicator.enabled else 0

        fb_cfg = app._config.setdefault("feedback", {})
        fb_cfg["visual_indicator"] = app._recording_indicator.enabled
        save_config(app._config, app._config_path)

    def do_transcribe_direct(
        self,
        asr_text: str,
        use_enhance: bool,
        overlay_already_shown: bool = False,
    ) -> None:
        """Original flow: enhance (if enabled) and type directly.

        Args:
            asr_text: Transcribed text from ASR.
            use_enhance: Whether to run AI enhancement.
            overlay_already_shown: If True, the streaming overlay is already
                visible (shown immediately after recording ended). The method
                will update ASR text and reuse it instead of creating a new one.
        """
        from PyObjCTools import AppHelper

        app = self._app

        try:
            app._usage_stats.record_transcription(
                mode="direct", enhance_mode=app._enhance_mode
            )
        except Exception as e:
            logger.error("Failed to record usage stats: %s", e)

        self._fire_scripting_event("transcription_done", asr_text=asr_text)

        text = asr_text
        enhanced_text = None
        cancel_event = threading.Event()

        if use_enhance:
            app._set_status("Enhancing...")

            if overlay_already_shown:
                # Overlay already visible — update ASR text and register
                # cancel event for ESC key support
                app._streaming_overlay.set_asr_text(asr_text)
                app._streaming_overlay.set_cancel_event(cancel_event)
            else:
                # Legacy path (streaming transcription): show overlay now
                indicator_frame = app._recording_indicator.current_frame
                stt_info = app._current_stt_model()
                llm_info = app._current_llm_model()

                def _show_overlay():
                    app._recording_indicator.animate_out(
                        completion=lambda: app._streaming_overlay.show(
                            asr_text=asr_text,
                            cancel_event=cancel_event,
                            animate_from_frame=indicator_frame,
                            stt_info=stt_info,
                            llm_info=llm_info,
                        )
                    )

                AppHelper.callAfter(_show_overlay)

            try:
                current_mode_def = app._enhancer.get_mode_definition(app._enhance_mode)
                chain_steps: list[str] = []
                if current_mode_def and current_mode_def.steps:
                    for step_id in current_mode_def.steps:
                        step_def = app._enhancer.get_mode_definition(step_id)
                        if step_def:
                            chain_steps.append(step_id)
                        else:
                            logger.warning("Chain step '%s' not found, skipping", step_id)

                if chain_steps:
                    text = self._run_direct_chain_stream(
                        asr_text, chain_steps, cancel_event
                    )
                else:
                    text = self._run_direct_single_stream(asr_text, cancel_event)

                if cancel_event.is_set():
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
                if cancel_event.is_set():
                    AppHelper.callAfter(app._streaming_overlay.close)
                else:
                    app._streaming_overlay.close_with_delay()
        else:
            # No enhancement — update overlay with ASR result, then fade out
            if overlay_already_shown:
                app._streaming_overlay.set_asr_text(asr_text)
                app._streaming_overlay.close_with_delay()

        if cancel_event.is_set():
            app._set_status("WZ")
            return

        self._fire_scripting_event("output_text", final_text=text.strip())

        type_text(
            text.strip(),
            append_newline=app._append_newline,
            method=app._output_method,
        )
        app._set_status("WZ")

        try:
            app._usage_stats.record_confirm(modified=False)
        except Exception as e:
            logger.error("Failed to record usage stats: %s", e)

        try:
            app._usage_stats.record_output_method(copy_to_clipboard=False)
        except Exception as e:
            logger.error("Failed to record output method: %s", e)

        try:
            app._conversation_history.log(
                asr_text=asr_text,
                enhanced_text=enhanced_text,
                final_text=text.strip(),
                enhance_mode=app._enhance_mode,
                preview_enabled=False,
                stt_model=app._current_stt_model(),
                llm_model=app._current_llm_model(),
                audio_duration=getattr(app, "_last_audio_duration", 0.0),
            )
        except Exception as e:
            logger.error("Failed to log conversation: %s", e)

    def _run_direct_single_stream(
        self, asr_text: str, cancel_event: threading.Event
    ) -> str:
        """Run single-step streaming enhancement, updating overlay."""
        app = self._app
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
                        app._enhancer.cancel_stream()
                        return
                    if is_thinking == "retry" and chunk:
                        had_thinking = True
                        app._streaming_overlay.append_thinking_text(chunk)
                        label = chunk.strip().strip("()\n")
                        app._streaming_overlay.set_status(f"\u23f3 {label}")
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
            finally:
                await gen.aclose()

        try:
            loop.run_until_complete(_stream())
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

        if usage:
            try:
                app._usage_stats.record_token_usage(usage)
            except Exception as e:
                logger.error("Failed to record token usage: %s", e)
            app._streaming_overlay.set_complete(usage)

        return "".join(collected).strip() or asr_text

    def _run_direct_chain_stream(
        self,
        asr_text: str,
        chain_steps: list[str],
        cancel_event: threading.Event,
    ) -> str:
        """Run multi-step chain streaming enhancement, updating overlay."""
        app = self._app
        loop = asyncio.new_event_loop()
        total_steps = len(chain_steps)
        input_text = asr_text
        original_mode = app._enhancer.mode
        total_usage: dict[str, int] = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }

        try:
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
                                    app._enhancer.cancel_stream()
                                    return
                                if is_thinking == "retry" and chunk:
                                    had_thinking = True
                                    app._streaming_overlay.append_thinking_text(chunk)
                                    label = chunk.strip().strip("()\n")
                                    app._streaming_overlay.set_status(
                                        f"\u23f3 Step {step_idx}/{total_steps}: {label}"
                                    )
                                elif is_thinking and chunk:
                                    had_thinking = True
                                    thinking_tokens += len(chunk)
                                    app._streaming_overlay.append_thinking_text(
                                        chunk, thinking_tokens=thinking_tokens
                                    )
                                elif chunk:
                                    if had_thinking:
                                        had_thinking = False
                                        # Don't clear previous steps' content
                                    collected.append(chunk)
                                    completion_tokens += len(chunk)
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
                    try:
                        app._usage_stats.record_token_usage(step_usage)
                    except Exception as e:
                        logger.error("Failed to record token usage: %s", e)

                if total_usage["total_tokens"] > 0:
                    app._streaming_overlay.set_complete(total_usage)

                return input_text.strip() or asr_text
            finally:
                app._enhancer.mode = original_mode
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()
