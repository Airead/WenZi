"""Preview panel and clipboard enhance flow extracted from WenZiApp."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from wenzi.app import WenZiApp

from wenzi.controllers import fire_scripting_event
from wenzi.config import save_config
from wenzi.enhance.enhancer import MODE_OFF
from wenzi.i18n import t
from wenzi.enhance.preview_history import PreviewHistoryStore, PreviewRecord
from wenzi.input import (
    copy_selection_to_clipboard,
    get_clipboard_text,
    has_clipboard_text,
    set_clipboard_text,
    type_text,
)
from wenzi.transcription.model_registry import (
    PRESET_BY_ID,
    PRESETS,
    build_remote_asr_models,
    is_backend_available,
)
from wenzi.transcription.base import create_transcriber
from wenzi.ui_helpers import (
    activate_for_dialog,
    get_frontmost_app,
    reactivate_app,
    restore_accessory,
    topmost_alert,
)

logger = logging.getLogger(__name__)


class PreviewController:
    """Handles preview panel interactions and clipboard enhance flow."""

    _CLIPBOARD_MAX_CHARS = 2000

    _ENHANCE_DEBOUNCE_SECONDS = 0.3

    def __init__(self, app: WenZiApp) -> None:
        self._app = app
        self._enhance_debounce_timer: threading.Timer | None = None
        self._preview_history = PreviewHistoryStore(max_size=10)
        # Track the history record currently being viewed (None = normal mode)
        self._viewing_history_index: int | None = None
        self._result_holder: dict | None = None
        self._input_context = None

    def _fire_scripting_event(self, event_name: str, **kwargs) -> None:
        fire_scripting_event(self._app, event_name, **kwargs)

    def _apply_input_context(self, ctx) -> None:
        """Store input context and sync display text to the preview panel."""
        self._input_context = ctx
        display = ctx.format_for_display() if ctx else ""
        self._app._preview_panel.set_input_context(display)

    # ------------------------------------------------------------------
    # Preview history helpers
    # ------------------------------------------------------------------

    _ACTION_SYMBOLS = {"confirm": "\u23ce", "copy": "\u2398", "cancel": "\u2715"}

    def _build_history_items(self) -> list:
        """Build the history dropdown items list for the preview panel."""
        from datetime import datetime

        items = []
        for record in self._preview_history.get_all():
            try:
                dt = datetime.fromisoformat(record.created_at)
                time_str = dt.strftime("%H:%M:%S")
            except Exception:
                time_str = "?"
            symbol = self._ACTION_SYMBOLS.get(record.action, "?")
            preview = record.final_text[:40] if record.final_text else ""
            items.append({
                "time": time_str,
                "action": symbol,
                "mode": record.enhance_mode if record.enhance_mode != "off" else "",
                "preview": preview,
            })
        return items

    def _log_with_chain_steps(
        self,
        app: WenZiApp,
        *,
        result_holder: dict,
        asr_text: str,
        final_text: str,
        audio_duration: float = 0.0,
        correction_tracked: bool = False,
    ) -> str | None:
        """Log enhancement result to conversation history.

        Returns:
            The timestamp of the logged record, or ``None`` for chain modes.
        """
        if result_holder.get("is_chain"):
            # Design decision: chain modes do NOT write to conversation history.
            #
            # Why:
            #   In a chain like "proofread → translate", the user only sees and
            #   can edit the final output (translation).  Intermediate results
            #   (e.g. the proofread text) are never shown to the user for
            #   verification.  If we logged these unverified intermediate
            #   results as "confirmed" conversation history, the LLM would
            #   treat them as ground-truth correction examples in future
            #   requests — potentially reinforcing errors the user never
            #   approved.
            #
            # What chain modes DO:
            #   During execution, each step *reads* from its own mode's
            #   per-mode history (built from standalone usage of that mode).
            #   This gives each step useful context without polluting the
            #   history with unverified data.
            #
            # If this needs to change:
            #   The key constraint is user verification.  If the UI is updated
            #   to let users review/edit intermediate step results before
            #   confirming, then those verified results CAN be logged under
            #   each step's enhance_mode.
            return None

        return app._conversation_history.log(
            asr_text=asr_text,
            enhanced_text=result_holder.get("enhanced_text"),
            final_text=final_text,
            enhance_mode=app._enhance_mode,
            preview_enabled=True,
            stt_model=app._current_stt_model(),
            llm_model=app._current_llm_model(),
            user_corrected=bool(result_holder.get("user_corrected")),
            audio_duration=audio_duration,
            input_context=self._input_context,
            correction_tracked=correction_tracked,
        )

    def _save_to_preview_history(
        self,
        timestamp: str | None,
        action: str,
        result_holder: dict,
        wav_data: bytes | None,
        audio_duration: float,
        source: str,
    ) -> None:
        """Save a preview result to the in-memory history store.

        Args:
            action: "confirm", "copy", or "cancel".
        """
        from datetime import datetime

        app = self._app
        asr_text = getattr(app, "_current_preview_asr_text", "")
        # For cancel: result_holder["text"] is None, fall back to
        # enhanced_text or asr_text so the history preview is not blank.
        final_text = (
            result_holder.get("text")
            or result_holder.get("enhanced_text")
            or asr_text
            or ""
        ).strip()
        record = PreviewRecord(
            timestamp=timestamp,
            created_at=datetime.now().isoformat(),
            action=action,
            asr_text=asr_text,
            enhanced_text=result_holder.get("enhanced_text"),
            final_text=final_text,
            enhance_mode=app._enhance_mode,
            stt_model=app._current_stt_model(),
            llm_model=app._current_llm_model(),
            wav_data=wav_data,
            audio_duration=audio_duration,
            source=source,
            system_prompt=result_holder.get("system_prompt", ""),
            thinking_text=result_holder.get("thinking_text", ""),
            token_usage=result_holder.get("token_usage"),
            hotwords_detail=list(app._preview_panel.hotwords_detail),
            input_context=self._input_context,
        )
        self._preview_history.add(record)

    def on_show_last_preview(self) -> None:
        """Show the most recent preview history record in a new preview panel."""
        record = self._preview_history.get(0)
        if record is None:
            logger.info("No preview history to show")
            return

        app = self._app
        if app._busy:
            logger.info("Preview history ignored: busy")
            return

        app._busy = True

        def _run():
            try:
                self.do_transcribe_with_preview(
                    asr_text=record.asr_text,
                    use_enhance=bool(app._enhancer and app._enhancer.is_active),
                    audio_duration=record.audio_duration,
                    wav_data=record.wav_data,
                    initial_history_index=0,
                )
            except Exception as e:
                logger.error("Show last preview failed: %s", e)
                app._busy = False

        threading.Thread(target=_run, daemon=True).start()

    def on_select_history(self, index: int) -> None:
        """Handle history item selection from the preview panel dropdown."""
        record = self._preview_history.get(index)
        if record is None:
            return

        self._viewing_history_index = index
        self._apply_input_context(record.input_context)
        app = self._app

        # Update internal state so confirm uses the correct ASR text
        app._current_preview_asr_text = record.asr_text

        # Sync result_holder with the selected history record so that
        # _handle_history_confirm sees the record's own values (not stale
        # data from the current session's initial enhancement).
        if self._result_holder is not None:
            self._result_holder["enhanced_text"] = record.enhanced_text
            self._result_holder["system_prompt"] = record.system_prompt
            self._result_holder["thinking_text"] = record.thinking_text
            self._result_holder["token_usage"] = record.token_usage

        # Load WAV data so Play/Save buttons work
        app._preview_panel._asr_wav_data = record.wav_data

        # Compute asr_info
        asr_info = ""
        if record.audio_duration > 0:
            asr_info = f"{record.audio_duration:.1f}s"

        app._preview_panel.set_hotwords(record.hotwords_detail)
        app._preview_panel.load_history_record(
            asr_text=record.asr_text,
            enhanced_text=record.enhanced_text,
            final_text=record.final_text,
            enhance_mode=record.enhance_mode,
            has_audio=record.wav_data is not None,
            asr_info=asr_info,
            system_prompt=record.system_prompt,
            thinking_text=record.thinking_text,
            token_usage=record.token_usage,
        )

        # Diffs are not persisted in PreviewRecord, so recompute them here
        from wenzi.enhance.text_diff import extract_word_pairs

        # Clear stale diffs from the previous selection.
        # Vocab hits are clear-only: they are detected live during enhancement
        # and recording them again here would inflate hit counts.
        panel = app._preview_panel
        panel.clear_diffs()

        try:
            pairs = extract_word_pairs(record.asr_text, record.enhanced_text)
            if pairs:
                panel.set_asr_diffs(pairs)
        except Exception as e:
            logger.warning("History: failed to compute ASR diffs: %s", e)

        if record.final_text != record.enhanced_text:
            try:
                user_pairs = extract_word_pairs(
                    record.enhanced_text, record.final_text,
                )
                if user_pairs:
                    panel.set_user_diffs(user_pairs)
            except Exception as e:
                logger.warning("History: failed to compute user diffs: %s", e)

        if app._manual_vocab_store is not None:
            try:
                panel.set_manual_vocab_state(
                    app._manual_vocab_store.get_all_for_state(),
                )
            except Exception as e:
                logger.warning("History: failed to sync vocab state: %s", e)

    def _handle_history_confirm(
        self,
        history_index: int,
        result_holder: dict,
        wav_data: bytes | None,
        audio_duration: float,
        source: str,
    ) -> None:
        """Handle confirm/copy from a history record view."""
        app = self._app
        record = self._preview_history.get(history_index)
        if record is None:
            return

        final_text = (result_holder.get("text") or "").strip()
        enhanced_text = result_holder.get("enhanced_text")
        current_mode = app._enhance_mode
        current_stt = app._current_stt_model()
        current_llm = app._current_llm_model()

        if record.timestamp is not None:
            # Record was previously confirmed — update changed fields
            updates = {}
            if final_text != record.final_text:
                updates["final_text"] = final_text
            if enhanced_text != record.enhanced_text:
                updates["enhanced_text"] = enhanced_text
            if current_mode != record.enhance_mode:
                updates["enhance_mode"] = current_mode
            if current_stt != record.stt_model:
                updates["stt_model"] = current_stt
            if current_llm != record.llm_model:
                updates["llm_model"] = current_llm
            if updates:
                try:
                    app._conversation_history.update_record(
                        record.timestamp, **updates
                    )
                except Exception as e:
                    logger.error("Failed to update conversation history: %s", e)
        else:
            # Record was from a cancel — create a new conversation history entry.
            # Note: chain mode step results are not available here (preview
            # history only stores the final result), so we log as a single
            # entry under the current mode.  This is acceptable for a low-
            # frequency path (re-confirming cancelled previews).
            try:
                ts = app._conversation_history.log(
                    asr_text=record.asr_text,
                    enhanced_text=enhanced_text,
                    final_text=final_text,
                    enhance_mode=current_mode,
                    preview_enabled=True,
                    stt_model=current_stt,
                    llm_model=current_llm,
                    user_corrected=final_text != (enhanced_text or record.asr_text),
                    audio_duration=record.audio_duration,
                )
                self._preview_history.update_timestamp(history_index, ts)
            except Exception as e:
                logger.error("Failed to log conversation from history: %s", e)

        # Sync all fields back to in-memory record
        record.final_text = final_text
        record.enhanced_text = enhanced_text
        record.enhance_mode = current_mode
        record.stt_model = current_stt
        record.llm_model = current_llm
        record.action = "copy" if result_holder.get("copy_to_clipboard") else "confirm"
        if "system_prompt" in result_holder:
            record.system_prompt = result_holder["system_prompt"]
        if "thinking_text" in result_holder:
            record.thinking_text = result_holder["thinking_text"]
        if "token_usage" in result_holder:
            record.token_usage = result_holder["token_usage"]

        # Move to front so it won't be evicted first
        self._preview_history.move_to_front(history_index)

    # ------------------------------------------------------------------
    # Preview with transcription (hotkey → record → preview)
    # ------------------------------------------------------------------

    def do_transcribe_with_preview(
        self, asr_text: str | None, use_enhance: bool,
        audio_duration: float, wav_data: bytes | None = None,
        initial_history_index: int | None = None,
    ) -> None:
        """Show preview with ASR text (or run STT in background).

        If *initial_history_index* is set, the panel loads that history
        record immediately after opening (no STT / enhancement).

        If *asr_text* is ``None``, STT runs in a background thread
        and STT runs in the background.
        """
        from PyObjCTools import AppHelper

        app = self._app

        # Save the frontmost app before we steal focus with the preview panel.
        # Used later to reactivate only the focused window (not all windows).
        previous_app = get_frontmost_app()
        self._apply_input_context(app._recording_controller.input_context)

        try:
            app._usage_stats.record_transcription(
                mode="preview", enhance_mode=app._enhance_mode
            )
        except Exception as e:
            logger.error("Failed to record usage stats: %s", e)

        app._current_preview_asr_text = asr_text or ""
        app._enhance_controller.clear_cache()

        result_event = threading.Event()
        result_holder = {"text": None, "confirmed": False, "enhanced_text": None}
        self._result_holder = result_holder

        def on_confirm(
            text: str,
            correction_info: dict | None = None,
            copy_to_clipboard: bool = False,
        ) -> None:
            result_holder["text"] = text
            result_holder["confirmed"] = True
            result_holder["copy_to_clipboard"] = copy_to_clipboard
            result_holder["user_corrected"] = correction_info is not None
            # Stop any in-flight streaming enhancement to save tokens
            app._enhance_controller.cancel()
            try:
                app._usage_stats.record_confirm(modified=correction_info is not None)
            except Exception as e:
                logger.error("Failed to record usage stats: %s", e)
            result_event.set()

        def on_cancel() -> None:
            result_holder["confirmed"] = False
            # Stop any in-flight streaming enhancement
            app._enhance_controller.cancel()
            try:
                app._usage_stats.record_cancel()
            except Exception as e:
                logger.error("Failed to record usage stats: %s", e)
            result_event.set()

        # Build mode list for the segmented control
        available_modes = []
        if app._enhancer:
            available_modes = [("off", "Off")] + app._enhancer.available_modes

        # Build ASR info string (duration only when popup available, else model+duration)
        asr_info_parts = []
        if audio_duration > 0:
            asr_info_parts.append(f"{audio_duration:.1f}s")
        # Store duration for re-transcription info updates
        app._preview_audio_duration = audio_duration

        # Build STT model list for popup
        stt_models: List[str] = []
        stt_model_keys: list = []
        stt_current_index = 0

        if wav_data:
            # Local presets (only available backends)
            for preset in PRESETS:
                if is_backend_available(preset.backend):
                    stt_models.append(preset.display_name)
                    stt_model_keys.append(("preset", preset.id))
                    if preset.id == app._current_preset_id and not app._current_remote_asr:
                        stt_current_index = len(stt_models) - 1

            # Remote ASR models
            asr_cfg = app._config.get("asr", {})
            providers = asr_cfg.get("providers", {})
            remote_models = build_remote_asr_models(providers)
            for rm in remote_models:
                stt_models.append(rm.display_name)
                stt_model_keys.append(("remote", (rm.provider, rm.model)))
                if app._current_remote_asr == (rm.provider, rm.model):
                    stt_current_index = len(stt_models) - 1

        app._preview_stt_keys = stt_model_keys

        # Add model name to asr_info when no popup (backward compat)
        if not stt_models:
            try:
                asr_info_parts.insert(0, app._transcriber.model_display_name)
            except Exception:
                pass
        asr_info = "  ".join(asr_info_parts)

        # Build LLM model list for popup
        llm_models: List[str] = []
        llm_model_keys: list = []
        llm_current_index = 0

        if app._enhancer:
            providers_with = app._enhancer.providers_with_models
            current_llm = (app._enhancer.provider_name, app._enhancer.model_name)
            for pname, models in providers_with.items():
                for mname in models:
                    key = (pname, mname)
                    llm_models.append(f"{pname} / {mname}")
                    llm_model_keys.append(key)
                    if key == current_llm:
                        llm_current_index = len(llm_models) - 1

        app._preview_llm_keys = llm_model_keys

        # Build enhance info string
        enhance_info = ""
        if app._enhancer:
            parts = []
            if app._enhancer.provider_name:
                parts.append(app._enhancer.provider_name)
            if app._enhancer.model_name:
                parts.append(app._enhancer.model_name)
            enhance_info = " / ".join(parts)

        # Determine whether STT needs to run in background
        need_stt = asr_text is None
        display_asr_text = "" if need_stt else asr_text

        # Show panel on main thread, then start enhancement/STT after panel is built
        def _show():
            activate_for_dialog()

            # Get indicator frame for transition animation before animating it out
            indicator_frame = app._recording_indicator.current_frame

            def _show_preview():
                app._preview_panel.show(
                    asr_text=display_asr_text,
                    show_enhance=use_enhance,
                    on_confirm=on_confirm,
                    on_cancel=on_cancel,
                    available_modes=available_modes,
                    current_mode=app._enhance_mode,
                    on_mode_change=self.on_preview_mode_change,
                    asr_info=asr_info if not need_stt else "",
                    asr_wav_data=wav_data,
                    enhance_info=enhance_info,
                    stt_models=stt_models if stt_models else None,
                    stt_current_index=stt_current_index,
                    on_stt_model_change=self.on_preview_stt_change if stt_models else None,
                    llm_models=llm_models if llm_models else None,
                    llm_current_index=llm_current_index,
                    on_llm_model_change=self.on_preview_llm_change if llm_models else None,
                    punc_enabled=not app._transcriber.skip_punc,
                    on_punc_toggle=self.on_preview_punc_toggle if wav_data else None,
                    thinking_enabled=app._enhancer.thinking if app._enhancer else False,
                    on_thinking_toggle=self.on_preview_thinking_toggle if app._enhancer else None,
                    on_google_translate=lambda: app._usage_stats.record_google_translate_open(),
                    on_select_history=self.on_select_history,
                    preview_history_items=self._build_history_items(),
                    animate_from_frame=indicator_frame,
                    on_add_manual_vocab=self._on_add_manual_vocab,
                    on_remove_manual_vocab=self._on_remove_manual_vocab,
                    on_diff_panel_toggle=self._on_diff_panel_toggle,
                    diff_panel_open=app._config.get("ui", {}).get("diff_panel_open", False),
                )
                if initial_history_index is not None:
                    # Load cached history record — skip STT and enhancement
                    self.on_select_history(initial_history_index)
                elif need_stt:
                    # Show loading state and disable STT popup during transcription
                    app._preview_panel.set_asr_loading()
                    if use_enhance:
                        app._preview_panel.set_enhance_loading()
                    # Start STT thread AFTER panel is built to avoid race condition
                    # where fast models (e.g. FunASR) complete before panel exists
                    threading.Thread(target=_do_stt, daemon=True).start()
                elif use_enhance:
                    # ASR already available, start enhancement immediately
                    app._preview_panel.set_enhance_loading()
                    app._preview_panel.enhance_request_id += 1
                    app._enhance_controller.run(
                        asr_text, app._preview_panel.enhance_request_id, result_holder,
                        input_context=self._input_context,
                    )

            if indicator_frame is not None:
                app._recording_indicator.animate_out(completion=_show_preview)
            else:
                _show_preview()

        # Define STT background task (started inside _show_preview after panel is built)
        def _do_stt():
            try:
                from wenzi.transcription.base import BaseTranscriber

                audio_dur = BaseTranscriber.wav_duration_seconds(wav_data)
                app._preview_audio_duration = audio_dur
                app._transcriber.skip_punc = bool(
                    app._enhancer and app._enhancer.is_active
                )
                hotwords, hotwords_detail = app._build_dynamic_hotwords()
                text = app._transcriber.transcribe(wav_data, hotwords=hotwords)
                if text and text.strip():
                    stt_text = text.strip()
                else:
                    stt_text = "(empty)"
                    logger.warning("Transcription returned empty text")

                app._current_preview_asr_text = stt_text
                app._enhance_controller.clear_cache()

                self._fire_scripting_event("transcription_done", asr_text=stt_text)

                # Build ASR info
                parts = []
                if not stt_models:
                    try:
                        parts.insert(0, app._transcriber.model_display_name)
                    except Exception:
                        pass
                if audio_dur > 0:
                    parts.append(f"{audio_dur:.1f}s")
                new_asr_info = "  ".join(parts)

                def _on_stt_done():
                    app._preview_panel.set_hotwords(hotwords_detail)
                    app._preview_panel.set_asr_result(
                        stt_text, asr_info=new_asr_info, request_id=0,
                    )
                    # Start enhancement now that ASR is ready
                    if use_enhance and stt_text != "(empty)":
                        app._preview_panel.enhance_request_id += 1
                        app._enhance_controller.run(
                            stt_text, app._preview_panel.enhance_request_id,
                            result_holder,
                            input_context=self._input_context,
                        )
                    elif use_enhance:
                        # Empty text — clear enhance loading
                        app._preview_panel.set_enhance_off()

                AppHelper.callAfter(_on_stt_done)
            except Exception as e:
                logger.error("Background STT failed: %s", e)
                from wenzi.transcription.model_registry import PRESET_BY_ID

                preset_id = app._current_preset_id
                preset = PRESET_BY_ID.get(preset_id) if preset_id else None
                has_cache = (
                    preset is not None
                    and preset.backend not in ("apple", "whisper-api")
                )
                if has_cache:
                    hint = (
                        "This may be caused by corrupted cache files from "
                        "an interrupted download. Try clearing cache via "
                        "the model load error alert, or switch to a "
                        "different model from the menu."
                    )
                else:
                    hint = (
                        "Please try switching to a different model "
                        "from the menu."
                    )
                app._preview_panel.set_asr_result(
                    f"(error: {e})\n\n{hint}",
                    request_id=0,
                )

        AppHelper.callAfter(_show)
        app._set_status("statusbar.status.preview")

        # Wait for user decision
        result_event.wait()
        app._busy = False

        # Reactivate the previous app's focused window, then restore accessory mode.
        # Order matters: activate first (without AllWindows) so macOS doesn't
        # trigger its own all-windows activation when we drop to accessory.
        def _activate_prev():
            reactivate_app(previous_app)

        def _go_accessory():
            restore_accessory()

        AppHelper.callAfter(_activate_prev)
        AppHelper.callAfter(_go_accessory)
        time.sleep(0.15)  # Brief delay for target app to regain focus

        viewing_idx = self._viewing_history_index
        self._viewing_history_index = None

        if result_holder["confirmed"] and result_holder["text"]:
            final_text = result_holder["text"].strip()
            copy_to_clip = bool(result_holder.get("copy_to_clipboard"))
            if copy_to_clip:
                set_clipboard_text(final_text)
                logger.info("Text copied to clipboard (%d chars)", len(final_text))
            else:
                type_text(
                    final_text,
                    append_newline=app._append_newline,
                    method=app._output_method,
                )
            app._set_status("statusbar.status.ready")

            try:
                app._usage_stats.record_output_method(copy_to_clipboard=copy_to_clip)
            except Exception as e:
                logger.error("Failed to record output method: %s", e)

            if viewing_idx is not None:
                # Confirming from a history record
                self._handle_history_confirm(
                    viewing_idx, result_holder, wav_data,
                    getattr(app, "_preview_audio_duration", 0.0), "voice",
                )
            else:
                # Normal confirm — log to conversation history, then save to preview history
                mode_def = (
                    app._enhancer.get_mode_definition(app._enhance_mode)
                    if app._enhancer
                    else None
                )
                should_track = mode_def is not None and mode_def.track_corrections

                ts = None
                try:
                    ts = self._log_with_chain_steps(
                        app,
                        result_holder=result_holder,
                        asr_text=app._current_preview_asr_text,
                        final_text=final_text,
                        audio_duration=getattr(app, "_preview_audio_duration", 0.0),
                        correction_tracked=should_track,
                    )
                except Exception as e:
                    logger.error("Failed to log conversation: %s", e)

                action = "copy" if copy_to_clip else "confirm"
                self._save_to_preview_history(
                    ts, action, result_holder, wav_data,
                    getattr(app, "_preview_audio_duration", 0.0), "voice",
                )
        else:
            app._set_status("statusbar.status.ready")
            logger.info("Preview cancelled by user")
            # Save cancelled preview to history (timestamp=None)
            if viewing_idx is None:
                self._save_to_preview_history(
                    None, "cancel", result_holder, wav_data,
                    getattr(app, "_preview_audio_duration", 0.0), "voice",
                )

    # ------------------------------------------------------------------
    # Clipboard enhance
    # ------------------------------------------------------------------

    def on_clipboard_enhance(self, _sender=None) -> None:
        """Handle Enhance Clipboard menu item or hotkey activation.

        May be called from a background thread (Quartz event tap).
        Launches a worker thread that simulates Cmd+C to capture the
        current selection, then validates and enhances the clipboard text.
        """
        threading.Thread(
            target=self._on_clipboard_enhance_worker, daemon=True
        ).start()

    def _on_clipboard_enhance_worker(self) -> None:
        """Worker-thread implementation of clipboard enhance."""
        from PyObjCTools import AppHelper

        app = self._app

        if app._busy:
            logger.info("Clipboard enhance ignored: busy")
            return

        # Try to copy the current selection first
        copy_selection_to_clipboard()

        # Now validate the clipboard content
        if not has_clipboard_text():
            AppHelper.callAfter(self._clipboard_enhance_show_error,
                                t("alert.clipboard.not_supported.title"),
                                t("alert.clipboard.not_supported.message"))
            return

        clipboard_text = get_clipboard_text()
        if not clipboard_text or not clipboard_text.strip():
            AppHelper.callAfter(self._clipboard_enhance_show_error,
                                t("alert.clipboard.empty.title"),
                                t("alert.clipboard.empty.message"))
            return

        clipboard_text = clipboard_text.strip()

        if len(clipboard_text) > self._CLIPBOARD_MAX_CHARS:
            AppHelper.callAfter(
                self._clipboard_enhance_show_error,
                t("alert.clipboard.too_long.title"),
                t("alert.clipboard.too_long.message",
                  length=len(clipboard_text), limit=self._CLIPBOARD_MAX_CHARS),
            )
            return

        app._busy = True
        app._set_status("statusbar.status.enhancing")

        try:
            self._do_clipboard_with_preview(clipboard_text)
        except Exception as e:
            logger.error("Clipboard enhance failed: %s", e)
            app._set_status("statusbar.status.error")
        finally:
            app._busy = False

    def _clipboard_enhance_show_error(self, title: str, message: str) -> None:
        """Show an error alert on the main thread for clipboard enhance."""
        topmost_alert(title=title, message=message)
        restore_accessory()

    def _do_clipboard_with_preview(self, clipboard_text: str) -> None:
        """Show preview panel for clipboard text enhancement."""
        from PyObjCTools import AppHelper

        app = self._app

        # Save the frontmost app before we steal focus with the preview panel.
        previous_app = get_frontmost_app()

        try:
            app._usage_stats.record_clipboard_enhance(app._enhance_mode)
        except Exception as e:
            logger.error("Failed to record clipboard enhance: %s", e)

        app._current_preview_asr_text = clipboard_text
        app._enhance_controller.clear_cache()

        result_event = threading.Event()
        result_holder = {"text": None, "confirmed": False, "enhanced_text": None}
        self._result_holder = result_holder

        def on_confirm(
            text: str,
            correction_info: dict | None = None,
            copy_to_clipboard: bool = False,
        ) -> None:
            result_holder["text"] = text
            result_holder["confirmed"] = True
            result_holder["copy_to_clipboard"] = copy_to_clipboard
            result_holder["user_corrected"] = correction_info is not None
            # Stop any in-flight streaming enhancement to save tokens
            app._enhance_controller.cancel()
            result_event.set()

        def on_cancel() -> None:
            result_holder["confirmed"] = False
            # Stop any in-flight streaming enhancement
            app._enhance_controller.cancel()
            result_event.set()

        # Build mode list for the segmented control
        available_modes = []
        if app._enhancer:
            available_modes = [("off", "Off")] + app._enhancer.available_modes

        # Build LLM model list for popup
        llm_models: List[str] = []
        llm_model_keys: list = []
        llm_current_index = 0

        if app._enhancer:
            providers_with = app._enhancer.providers_with_models
            current_llm = (app._enhancer.provider_name, app._enhancer.model_name)
            for pname, models in providers_with.items():
                for mname in models:
                    key = (pname, mname)
                    llm_models.append(f"{pname} / {mname}")
                    llm_model_keys.append(key)
                    if key == current_llm:
                        llm_current_index = len(llm_models) - 1

        app._preview_llm_keys = llm_model_keys

        # Build enhance info string
        enhance_info = ""
        if app._enhancer:
            parts = []
            if app._enhancer.provider_name:
                parts.append(app._enhancer.provider_name)
            if app._enhancer.model_name:
                parts.append(app._enhancer.model_name)
            enhance_info = " / ".join(parts)

        use_enhance = bool(app._enhancer and app._enhancer.is_active)

        def _show():
            activate_for_dialog()
            app._preview_panel.show(
                asr_text=clipboard_text,
                show_enhance=use_enhance,
                on_confirm=on_confirm,
                on_cancel=on_cancel,
                available_modes=available_modes,
                current_mode=app._enhance_mode,
                on_mode_change=self.on_preview_mode_change,
                asr_info="",
                asr_wav_data=None,
                enhance_info=enhance_info,
                stt_models=None,
                stt_current_index=0,
                on_stt_model_change=None,
                llm_models=llm_models if llm_models else None,
                llm_current_index=llm_current_index,
                on_llm_model_change=self.on_preview_llm_change if llm_models else None,
                source="clipboard",
                thinking_enabled=app._enhancer.thinking if app._enhancer else False,
                on_thinking_toggle=self.on_preview_thinking_toggle if app._enhancer else None,
                on_google_translate=lambda: app._usage_stats.record_google_translate_open(),
                on_select_history=self.on_select_history,
                preview_history_items=self._build_history_items(),
                on_add_manual_vocab=self._on_add_manual_vocab,
                on_remove_manual_vocab=self._on_remove_manual_vocab,
                on_diff_panel_toggle=self._on_diff_panel_toggle,
                diff_panel_open=app._config.get("ui", {}).get("diff_panel_open", False),
            )
            if use_enhance:
                app._preview_panel.enhance_request_id += 1
                app._enhance_controller.run(
                    clipboard_text, app._preview_panel.enhance_request_id, result_holder,
                    input_context=None,
                )

        AppHelper.callAfter(_show)
        app._set_status("statusbar.status.preview")

        result_event.wait()

        def _activate_prev():
            reactivate_app(previous_app)

        def _go_accessory():
            restore_accessory()

        AppHelper.callAfter(_activate_prev)
        AppHelper.callAfter(_go_accessory)
        time.sleep(0.15)

        viewing_idx = self._viewing_history_index
        self._viewing_history_index = None

        if result_holder["confirmed"] and result_holder["text"]:
            final_text = result_holder["text"].strip()
            copy_to_clip = bool(result_holder.get("copy_to_clipboard"))
            if copy_to_clip:
                set_clipboard_text(final_text)
                logger.info("Text copied to clipboard (%d chars)", len(final_text))
            else:
                type_text(
                    final_text,
                    append_newline=app._append_newline,
                    method=app._output_method,
                )
            app._set_status("statusbar.status.ready")

            try:
                app._usage_stats.record_clipboard_confirm()
            except Exception as e:
                logger.error("Failed to record clipboard confirm: %s", e)

            try:
                app._usage_stats.record_output_method(copy_to_clipboard=copy_to_clip)
            except Exception as e:
                logger.error("Failed to record output method: %s", e)

            if viewing_idx is not None:
                self._handle_history_confirm(
                    viewing_idx, result_holder, None, 0.0, "clipboard",
                )
            else:
                ts = None
                try:
                    ts = self._log_with_chain_steps(
                        app,
                        result_holder=result_holder,
                        asr_text=clipboard_text,
                        final_text=final_text,
                        audio_duration=0.0,
                    )
                except Exception as e:
                    logger.error("Failed to log conversation: %s", e)
                action = "copy" if copy_to_clip else "confirm"
                self._save_to_preview_history(
                    ts, action, result_holder, None, 0.0, "clipboard",
                )
        else:
            app._set_status("statusbar.status.ready")
            try:
                app._usage_stats.record_clipboard_cancel()
            except Exception as e:
                logger.error("Failed to record clipboard cancel: %s", e)
            logger.info("Clipboard enhance cancelled by user")
            if viewing_idx is None:
                self._save_to_preview_history(
                    None, "cancel", result_holder, None, 0.0, "clipboard",
                )

    # ------------------------------------------------------------------
    # Preview panel callbacks
    # ------------------------------------------------------------------

    def _on_add_manual_vocab(self, variant: str, term: str, source: str) -> None:
        """Handle user clicking a diff card to add to manual vocabulary."""
        app = self._app
        input_ctx = self._input_context
        app._manual_vocab_store.add(
            variant=variant,
            term=term,
            source=source,
            app_bundle_id=getattr(input_ctx, "bundle_id", ""),
            asr_model=app._current_stt_model(),
            llm_model=app._current_llm_model(),
            enhance_mode=app._enhance_mode,
        )
        logger.info("Manual vocab added: %r → %r (source=%s)", variant, term, source)

    def _on_remove_manual_vocab(self, variant: str, term: str) -> None:
        """Handle user clicking a diff card to remove from manual vocabulary."""
        self._app._manual_vocab_store.remove(variant, term)
        logger.info("Manual vocab removed: %r → %r", variant, term)

    def _on_diff_panel_toggle(self, is_open: bool) -> None:
        """Persist the diff panel open/closed preference."""
        app = self._app
        app._config.setdefault("ui", {})["diff_panel_open"] = is_open
        save_config(app._config, app._config_path)

    def on_preview_mode_change(self, mode_id: str) -> None:
        """Handle mode switch from the preview panel's segmented control.

        Uses debounce for enhancement requests: rapid mode switches only
        trigger one API call for the final mode, avoiding wasted HTTP
        requests and tokens.
        """
        from PyObjCTools import AppHelper

        app = self._app

        # Cancel pending debounce timer
        if self._enhance_debounce_timer is not None:
            self._enhance_debounce_timer.cancel()
            self._enhance_debounce_timer = None

        # Update enhance mode immediately (UI state, config, menu)
        app._enhance_mode = mode_id
        app._enhance_controller.enhance_mode = mode_id

        for m, item in app._enhance_menu_items.items():
            item.state = 1 if m == mode_id else 0

        if app._enhancer:
            if mode_id == MODE_OFF:
                app._enhancer._enabled = False
            else:
                app._enhancer._enabled = True
                app._enhancer.mode = mode_id

        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"]["enabled"] = mode_id != MODE_OFF
        app._config["ai_enhance"]["mode"] = mode_id
        save_config(app._config, app._config_path)

        # User explicitly chose this mode — discard any hotkey override so
        # restore won't revert this intentional change.
        app._recording_controller._saved_mode = None

        # Sync settings panel if it's open
        if app._settings_panel and app._settings_panel._panel is not None:
            app._settings_panel.update_enhance_mode(mode_id)

        logger.info("AI enhance mode set to (from preview): %s", mode_id)

        # Cancel in-flight enhancement immediately
        app._enhance_controller.cancel()
        app._preview_panel.enhance_request_id += 1

        if mode_id == MODE_OFF:
            AppHelper.callAfter(app._preview_panel.set_enhance_off)
            if self._result_holder is not None:
                self._result_holder["enhanced_text"] = None
                self._result_holder["system_prompt"] = ""
                self._result_holder["thinking_text"] = ""
                self._result_holder["token_usage"] = None
            return

        # Show loading state immediately as visual feedback
        cached = app._enhance_controller.get_cached()
        if cached is not None:
            app._preview_panel.replay_cached_result(
                display_text=cached.display_text,
                usage=cached.usage,
                system_prompt=cached.system_prompt,
                thinking_text=cached.thinking_text,
                final_text=cached.final_text,
            )
            if self._result_holder is not None:
                self._result_holder["enhanced_text"] = cached.final_text
                self._result_holder["system_prompt"] = cached.system_prompt
                self._result_holder["thinking_text"] = cached.thinking_text
                self._result_holder["token_usage"] = cached.usage
            return

        AppHelper.callAfter(app._preview_panel.set_enhance_loading)

        # Debounce: delay the actual API call
        request_id = app._preview_panel.enhance_request_id

        def _fire_enhance():
            self._enhance_debounce_timer = None
            # Guard against stale timer firing after another mode switch
            if app._preview_panel.enhance_request_id != request_id:
                return
            asr_text = getattr(app, "_current_preview_asr_text", "")
            app._enhance_controller.run(asr_text, request_id, self._result_holder,
                                       input_context=self._input_context)

        self._enhance_debounce_timer = threading.Timer(
            self._ENHANCE_DEBOUNCE_SECONDS, _fire_enhance,
        )
        self._enhance_debounce_timer.daemon = True
        self._enhance_debounce_timer.start()

    def on_preview_stt_change(self, index: int) -> None:
        """Handle STT model popup change from the preview panel."""
        from PyObjCTools import AppHelper

        app = self._app

        if index < 0 or index >= len(app._preview_stt_keys):
            return

        key_type, key_value = app._preview_stt_keys[index]

        # Check if same as current
        if key_type == "preset":
            if key_value == app._current_preset_id and not app._current_remote_asr:
                return
        elif key_type == "remote":
            if key_value == app._current_remote_asr:
                return

        old_index = app._preview_stt_keys.index(
            ("preset", app._current_preset_id) if not app._current_remote_asr
            else ("remote", app._current_remote_asr)
        ) if (
            ("preset", app._current_preset_id) if not app._current_remote_asr
            else ("remote", app._current_remote_asr)
        ) in app._preview_stt_keys else 0

        # Show loading state
        app._preview_panel.set_asr_loading()
        request_id = app._preview_panel.asr_request_id

        old_transcriber = app._transcriber
        wav_data = app._preview_panel._asr_wav_data

        def _do_switch():
            try:
                old_transcriber.cleanup()

                asr_cfg = app._config.get("asr", {})
                if key_type == "preset":
                    preset = PRESET_BY_ID[key_value]
                    new_transcriber = create_transcriber(
                        backend=preset.backend,
                        use_vad=asr_cfg.get("use_vad", True),
                        use_punc=asr_cfg.get("use_punc", True),
                        language=preset.language or asr_cfg.get("language"),
                        model=preset.model,
                        temperature=asr_cfg.get("temperature"),
                        hotwords=app._load_hotwords(),
                    )
                else:
                    prov, mod = key_value
                    providers = asr_cfg.get("providers", {})
                    pcfg = providers.get(prov, {})
                    new_transcriber = create_transcriber(
                        backend="whisper-api",
                        base_url=pcfg.get("base_url"),
                        api_key=pcfg.get("api_key"),
                        model=mod,
                        language=asr_cfg.get("language"),
                        temperature=asr_cfg.get("temperature"),
                        hotwords=app._load_hotwords(),
                    )

                new_transcriber.initialize()

                # Re-transcribe using wav_data
                new_transcriber.skip_punc = bool(
                    app._enhancer and app._enhancer.is_active
                )
                # Reuse cached hotwords — same audio, context unchanged
                cached_detail = app._preview_panel.hotwords_detail
                hotword_terms = (
                    [d.term for d in cached_detail]
                    if cached_detail else None
                )
                new_text = new_transcriber.transcribe(
                    wav_data, hotwords=hotword_terms,
                )

                # Build new ASR info (duration only since model is in popup)
                audio_duration = getattr(app, "_preview_audio_duration", 0.0)
                new_asr_info = f"{audio_duration:.1f}s" if audio_duration > 0 else ""

                def _on_success():
                    app._transcriber = new_transcriber
                    if key_type == "preset":
                        app._current_preset_id = key_value
                        app._current_remote_asr = None
                        app._config["asr"]["preset"] = key_value
                        preset = PRESET_BY_ID[key_value]
                        app._config["asr"]["backend"] = preset.backend
                        app._config["asr"]["model"] = preset.model
                        app._config["asr"]["language"] = preset.language
                        app._config["asr"]["default_provider"] = None
                        app._config["asr"]["default_model"] = None
                    else:
                        prov, mod = key_value
                        app._current_remote_asr = key_value
                        app._current_preset_id = None
                        app._config["asr"]["default_provider"] = prov
                        app._config["asr"]["default_model"] = mod

                    app._menu_builder.update_model_checkmarks()
                    save_config(app._config, app._config_path)

                    app._preview_panel.set_asr_result(
                        new_text, asr_info=new_asr_info, request_id=request_id,
                    )
                    app._current_preview_asr_text = new_text
                    app._enhance_controller.clear_cache()

                    # Re-run enhance if mode is not Off
                    if app._enhance_mode != MODE_OFF and app._enhancer:
                        app._preview_panel.set_enhance_loading()
                        app._preview_panel.enhance_request_id += 1
                        app._enhance_controller.run(
                            new_text, app._preview_panel.enhance_request_id,
                            self._result_holder,
                            input_context=self._input_context,
                        )

                AppHelper.callAfter(_on_success)
                logger.info("Preview STT switched to index %d", index)

            except Exception as e:
                logger.error("Preview STT switch failed: %s", e)
                err_msg = str(e)

                def _on_failure():
                    # Try to restore old transcriber
                    app._model_controller._try_restore_previous_model(
                        app._current_preset_id if not app._current_remote_asr else None
                    )
                    app._preview_panel.set_stt_popup_index(old_index)
                    # Restore ASR text
                    asr_text = getattr(app, "_current_preview_asr_text", "")
                    if app._preview_panel._asr_text_view is not None:
                        app._preview_panel._asr_text_view.setString_(
                            asr_text or f"(STT switch error: {err_msg})"
                        )

                AppHelper.callAfter(_on_failure)

        threading.Thread(target=_do_switch, daemon=True).start()

    def on_preview_llm_change(self, index: int) -> None:
        """Handle LLM model popup change from the preview panel."""
        app = self._app
        if not app._enhancer or index < 0 or index >= len(app._preview_llm_keys):
            return

        pname, mname = app._preview_llm_keys[index]
        if pname == app._enhancer.provider_name and mname == app._enhancer.model_name:
            return

        # Update enhancer
        app._enhancer.provider_name = pname
        app._enhancer.model_name = mname

        # Update menu checkmarks
        current_key = (pname, mname)
        for key, item in app._llm_model_menu_items.items():
            item.state = 1 if key == current_key else 0

        # Persist
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"]["default_provider"] = pname
        app._config["ai_enhance"]["default_model"] = mname
        save_config(app._config, app._config_path)
        logger.info("Preview LLM switched to: %s / %s", pname, mname)

        # Re-run enhance if mode is not Off
        if app._enhance_mode != MODE_OFF:
            cached = app._enhance_controller.get_cached()
            if cached is not None:
                app._preview_panel.replay_cached_result(
                    display_text=cached.display_text,
                    usage=cached.usage,
                    system_prompt=cached.system_prompt,
                    thinking_text=cached.thinking_text,
                    final_text=cached.final_text,
                )
                if self._result_holder is not None:
                    self._result_holder["enhanced_text"] = cached.final_text
                    self._result_holder["system_prompt"] = cached.system_prompt
                    self._result_holder["thinking_text"] = cached.thinking_text
            else:
                app._preview_panel.set_enhance_loading()
                app._preview_panel.enhance_request_id += 1
                asr_text = getattr(app, "_current_preview_asr_text", "")
                app._enhance_controller.run(
                    asr_text, app._preview_panel.enhance_request_id,
                    self._result_holder,
                    input_context=self._input_context,
                )

    def on_preview_punc_toggle(self, enabled: bool) -> None:
        """Handle Punc checkbox toggle from the preview panel."""
        from PyObjCTools import AppHelper

        app = self._app
        app._transcriber.skip_punc = not enabled
        logger.info("Punctuation restoration %s (from preview)", "enabled" if enabled else "disabled")

        # Re-transcribe with updated punc setting
        wav_data = app._preview_panel._asr_wav_data
        if not wav_data:
            return

        app._preview_panel.set_asr_loading()
        request_id = app._preview_panel.asr_request_id

        def _do_retranscribe():
            try:
                new_text = app._transcriber.transcribe(wav_data)
                audio_duration = getattr(app, "_preview_audio_duration", 0.0)
                new_asr_info = f"{audio_duration:.1f}s" if audio_duration > 0 else ""

                def _on_done():
                    app._preview_panel.set_asr_result(
                        new_text, asr_info=new_asr_info, request_id=request_id,
                    )
                    app._current_preview_asr_text = new_text
                    app._enhance_controller.clear_cache()

                    # Re-run enhance if mode is not Off
                    if app._enhance_mode != MODE_OFF and app._enhancer:
                        app._preview_panel.set_enhance_loading()
                        app._preview_panel.enhance_request_id += 1
                        app._enhance_controller.run(
                            new_text, app._preview_panel.enhance_request_id,
                            self._result_holder,
                            input_context=self._input_context,
                        )

                AppHelper.callAfter(_on_done)
            except Exception as e:
                logger.error("Punc toggle re-transcribe failed: %s", e)

                def _on_fail():
                    asr_text = getattr(app, "_current_preview_asr_text", "")
                    app._preview_panel.set_asr_result(
                        asr_text, request_id=request_id,
                    )

                AppHelper.callAfter(_on_fail)

        threading.Thread(target=_do_retranscribe, daemon=True).start()

    def on_preview_thinking_toggle(self, enabled: bool) -> None:
        """Handle Thinking checkbox toggle from preview panel."""
        from PyObjCTools import AppHelper

        app = self._app
        if not app._enhancer:
            return

        app._enhancer.thinking = enabled
        app._enhance_thinking_item.state = 1 if enabled else 0

        # Persist to config
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"]["thinking"] = enabled
        save_config(app._config, app._config_path)
        logger.info("AI thinking set to: %s (from preview panel)", enabled)

        # Re-trigger enhancement if currently active
        if app._enhance_mode != MODE_OFF:
            # Always cancel in-flight stream and invalidate stale chunks
            app._enhance_controller.cancel()
            app._preview_panel.enhance_request_id += 1

            cached = app._enhance_controller.get_cached()
            if cached is not None:
                app._preview_panel.replay_cached_result(
                    display_text=cached.display_text,
                    usage=cached.usage,
                    system_prompt=cached.system_prompt,
                    thinking_text=cached.thinking_text,
                    final_text=cached.final_text,
                )
                if self._result_holder is not None:
                    self._result_holder["enhanced_text"] = cached.final_text
                    self._result_holder["system_prompt"] = cached.system_prompt
                    self._result_holder["thinking_text"] = cached.thinking_text
            else:
                AppHelper.callAfter(app._preview_panel.set_enhance_loading)
                asr_text = getattr(app, "_current_preview_asr_text", "")
                app._enhance_controller.run(
                    asr_text, app._preview_panel.enhance_request_id,
                    self._result_holder,
                    input_context=self._input_context,
                )
