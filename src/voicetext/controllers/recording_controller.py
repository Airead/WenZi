"""Recording and direct transcription flow extracted from VoiceTextApp."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicetext.app import VoiceTextApp

from voicetext.config import save_config
from voicetext.input import type_text

logger = logging.getLogger(__name__)


class RecordingController:
    """Handles hotkey → recording → transcription → output flow."""

    def __init__(self, app: VoiceTextApp) -> None:
        self._app = app

    def on_hotkey_press(self) -> None:
        """Called when hotkey is pressed down - start recording."""
        app = self._app
        if app._busy:
            return
        logger.info("Hotkey pressed, starting recording")
        app._set_status("Recording...")
        app._sound_manager.play("start")
        if app._sound_manager.enabled:
            app._usage_stats.record_sound_feedback()

        app._recording_started.clear()

        def _delayed_start():
            import time
            time.sleep(0.35)
            if not app._busy:
                app._recorder.start()
                self.start_recording_indicator()
            app._recording_started.set()

        if app._sound_manager.enabled:
            threading.Thread(target=_delayed_start, daemon=True).start()
        else:
            app._recorder.start()
            self.start_recording_indicator()
            app._recording_started.set()

    def on_hotkey_release(self) -> None:
        """Called when hotkey is released - stop recording and transcribe."""
        app = self._app
        # Wait for delayed start to finish (if sound feedback caused a delay)
        if not app._recording_started.wait(timeout=1.0):
            return
        if not app._recorder.is_recording:
            return
        logger.info("Hotkey released, stopping recording")
        wav_data = app._recorder.stop()
        if not wav_data:
            self.stop_recording_indicator()
            app._set_status("VT")
            return
        use_enhance = bool(app._enhancer and app._enhancer.is_active)
        # Keep indicator alive for animation when preview or direct+enhance
        self.stop_recording_indicator(
            animate=app._preview_enabled or use_enhance
        )

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
            # Run transcription in background to keep UI responsive
            def _do_transcribe():
                try:
                    app._transcriber.skip_punc = bool(
                        app._enhancer and app._enhancer.is_active
                    )
                    text = app._transcriber.transcribe(wav_data)
                    if text and text.strip():
                        asr_text = text.strip()
                        use_enhance = bool(app._enhancer and app._enhancer.is_active)
                        self.do_transcribe_direct(asr_text, use_enhance)
                    else:
                        app._set_status("(empty)")
                        logger.warning("Transcription returned empty text")
                except Exception as e:
                    logger.error("Transcription failed: %s", e)
                    app._set_status("Error")
                finally:
                    app._busy = False

            threading.Thread(target=_do_transcribe, daemon=True).start()

    def start_recording_indicator(self) -> None:
        """Show visual indicator and start polling audio level."""
        from PyObjCTools import AppHelper

        app = self._app
        AppHelper.callAfter(app._recording_indicator.show)

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

    def do_transcribe_direct(self, asr_text: str, use_enhance: bool) -> None:
        """Original flow: enhance (if enabled) and type directly."""
        from PyObjCTools import AppHelper

        app = self._app

        try:
            app._usage_stats.record_transcription(
                mode="direct", enhance_mode=app._enhance_mode
            )
        except Exception as e:
            logger.error("Failed to record usage stats: %s", e)

        text = asr_text
        enhanced_text = None
        cancel_event = threading.Event()

        if use_enhance:
            app._set_status("Enhancing...")
            # Animate recording indicator out, then show streaming overlay
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
            except Exception as e:
                logger.error("AI enhancement failed: %s", e)
                text = asr_text
            finally:
                AppHelper.callAfter(app._streaming_overlay.close)

        if cancel_event.is_set():
            app._set_status("VT")
            return

        type_text(
            text.strip(),
            append_newline=app._append_newline,
            method=app._output_method,
        )
        app._set_status("VT")

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
                        return
                    if is_thinking == "retry" and chunk:
                        had_thinking = True
                        app._streaming_overlay.append_thinking_text(chunk)
                        label = chunk.strip().strip("()\n")
                        app._streaming_overlay.set_status(f"\u23f3 {label}")
                    elif is_thinking and chunk:
                        had_thinking = True
                        thinking_tokens += 1
                        app._streaming_overlay.append_thinking_text(
                            chunk, thinking_tokens=thinking_tokens
                        )
                    elif chunk:
                        if had_thinking:
                            had_thinking = False
                            app._streaming_overlay.clear_text()
                        collected.append(chunk)
                        completion_tokens += 1
                        app._streaming_overlay.append_text(
                            chunk, completion_tokens=completion_tokens
                        )
                    if chunk_usage is not None:
                        usage = chunk_usage
            finally:
                await gen.aclose()

        loop.run_until_complete(_stream())
        loop.run_until_complete(loop.shutdown_asyncgens())
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
                                thinking_tokens += 1
                                app._streaming_overlay.append_thinking_text(
                                    chunk, thinking_tokens=thinking_tokens
                                )
                            elif chunk:
                                if had_thinking:
                                    had_thinking = False
                                    # Don't clear previous steps' content
                                collected.append(chunk)
                                completion_tokens += 1
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

            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

            if total_usage["total_tokens"] > 0:
                app._streaming_overlay.set_complete(total_usage)

            return input_text.strip() or asr_text
        finally:
            app._enhancer.mode = original_mode
