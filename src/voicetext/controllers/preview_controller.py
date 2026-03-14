"""Preview panel and clipboard enhance flow extracted from VoiceTextApp."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from voicetext.app import VoiceTextApp

from voicetext.config import save_config
from voicetext.enhance.enhancer import MODE_OFF
from voicetext.input import (
    copy_selection_to_clipboard,
    get_clipboard_text,
    has_clipboard_text,
    set_clipboard_text,
    type_text,
)
from voicetext.transcription.model_registry import (
    PRESET_BY_ID,
    PRESETS,
    build_remote_asr_models,
    is_backend_available,
)
from voicetext.transcription.base import create_transcriber
from voicetext.ui_helpers import activate_for_dialog, restore_accessory, topmost_alert

logger = logging.getLogger(__name__)


class PreviewController:
    """Handles preview panel interactions and clipboard enhance flow."""

    _CLIPBOARD_MAX_CHARS = 2000

    def __init__(self, app: VoiceTextApp) -> None:
        self._app = app

    # ------------------------------------------------------------------
    # Preview with transcription (hotkey → record → preview)
    # ------------------------------------------------------------------

    def do_transcribe_with_preview(
        self, asr_text: str | None, use_enhance: bool,
        audio_duration: float, wav_data: bytes | None = None,
    ) -> None:
        """Show preview with ASR text (or run STT in background).

        If *asr_text* is ``None``, STT runs in a background thread
        and STT runs in the background.
        """
        from PyObjCTools import AppHelper

        app = self._app

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

        def on_confirm(
            text: str,
            correction_info: dict | None = None,
            copy_to_clipboard: bool = False,
        ) -> None:
            result_holder["text"] = text
            result_holder["confirmed"] = True
            result_holder["copy_to_clipboard"] = copy_to_clipboard
            result_holder["user_corrected"] = correction_info is not None
            if correction_info is not None:
                app._auto_vocab_builder.on_correction_logged()
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
                    on_browse_history=app._on_browse_history,
                    animate_from_frame=indicator_frame,
                )
                if need_stt:
                    # Show loading state and disable STT popup during transcription
                    app._preview_panel.set_asr_loading()
                    if use_enhance:
                        app._preview_panel.set_enhance_loading()
                    # Start STT thread AFTER panel is built to avoid race condition
                    # where fast models (e.g. FunASR) complete before panel exists
                    threading.Thread(target=_do_stt, daemon=True).start()
                elif use_enhance:
                    # ASR already available, start enhancement immediately
                    app._preview_panel.enhance_request_id += 1
                    app._enhance_controller.run(
                        asr_text, app._preview_panel.enhance_request_id, result_holder
                    )

            if indicator_frame is not None:
                app._recording_indicator.animate_out(completion=_show_preview)
            else:
                _show_preview()

        # Define STT background task (started inside _show_preview after panel is built)
        def _do_stt():
            try:
                from voicetext.transcription.base import BaseTranscriber

                audio_dur = BaseTranscriber.wav_duration_seconds(wav_data)
                app._preview_audio_duration = audio_dur
                app._transcriber.skip_punc = bool(
                    app._enhancer and app._enhancer.is_active
                )
                text = app._transcriber.transcribe(wav_data)
                if text and text.strip():
                    stt_text = text.strip()
                else:
                    stt_text = "(empty)"
                    logger.warning("Transcription returned empty text")

                app._current_preview_asr_text = stt_text
                app._enhance_controller.clear_cache()

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
                    app._preview_panel.set_asr_result(
                        stt_text, asr_info=new_asr_info, request_id=0,
                    )
                    # Start enhancement now that ASR is ready
                    if use_enhance and stt_text != "(empty)":
                        app._preview_panel.enhance_request_id += 1
                        app._enhance_controller.run(
                            stt_text, app._preview_panel.enhance_request_id,
                            result_holder,
                        )
                    elif use_enhance:
                        # Empty text — clear enhance loading
                        app._preview_panel.set_enhance_off()

                AppHelper.callAfter(_on_stt_done)
            except Exception as e:
                logger.error("Background STT failed: %s", e)
                app._preview_panel.set_asr_result(
                    f"(error: {e})",
                    request_id=0,
                )

        AppHelper.callAfter(_show)
        app._set_status("Preview...")

        # Wait for user decision
        result_event.wait()
        app._busy = False

        # Restore menu bar mode and inject text
        AppHelper.callAfter(restore_accessory)
        time.sleep(0.1)  # Brief delay for target app to regain focus

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
            app._set_status("VT")

            try:
                app._usage_stats.record_output_method(copy_to_clipboard=copy_to_clip)
            except Exception as e:
                logger.error("Failed to record output method: %s", e)

            try:
                app._conversation_history.log(
                    asr_text=app._current_preview_asr_text,
                    enhanced_text=result_holder["enhanced_text"],
                    final_text=final_text,
                    enhance_mode=app._enhance_mode,
                    preview_enabled=True,
                    stt_model=app._current_stt_model(),
                    llm_model=app._current_llm_model(),
                    user_corrected=bool(result_holder.get("user_corrected")),
                )
            except Exception as e:
                logger.error("Failed to log conversation: %s", e)
        else:
            app._set_status("VT")
            logger.info("Preview cancelled by user")

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
                                "Clipboard Content Not Supported",
                                "The clipboard does not contain text. "
                                "Please copy some text first.")
            return

        clipboard_text = get_clipboard_text()
        if not clipboard_text or not clipboard_text.strip():
            AppHelper.callAfter(self._clipboard_enhance_show_error,
                                "Clipboard Empty",
                                "No text found in clipboard.")
            return

        clipboard_text = clipboard_text.strip()

        if len(clipboard_text) > self._CLIPBOARD_MAX_CHARS:
            AppHelper.callAfter(
                self._clipboard_enhance_show_error,
                "Text Too Long",
                f"The clipboard contains {len(clipboard_text)} characters "
                f"(limit: {self._CLIPBOARD_MAX_CHARS}).\n\n"
                "Please copy a shorter text and try again.",
            )
            return

        app._busy = True
        app._set_status("Enhancing...")

        try:
            self._do_clipboard_with_preview(clipboard_text)
        except Exception as e:
            logger.error("Clipboard enhance failed: %s", e)
            app._set_status("Error")
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

        try:
            app._usage_stats.record_clipboard_enhance(app._enhance_mode)
        except Exception as e:
            logger.error("Failed to record clipboard enhance: %s", e)

        app._current_preview_asr_text = clipboard_text
        app._enhance_controller.clear_cache()

        result_event = threading.Event()
        result_holder = {"text": None, "confirmed": False, "enhanced_text": None}

        def on_confirm(
            text: str,
            correction_info: dict | None = None,
            copy_to_clipboard: bool = False,
        ) -> None:
            result_holder["text"] = text
            result_holder["confirmed"] = True
            result_holder["copy_to_clipboard"] = copy_to_clipboard
            result_holder["user_corrected"] = correction_info is not None
            if correction_info is not None:
                app._auto_vocab_builder.on_correction_logged()
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
                on_browse_history=app._on_browse_history,
            )
            if use_enhance:
                app._preview_panel.enhance_request_id += 1
                app._enhance_controller.run(
                    clipboard_text, app._preview_panel.enhance_request_id, result_holder
                )

        AppHelper.callAfter(_show)
        app._set_status("Preview...")

        result_event.wait()

        AppHelper.callAfter(restore_accessory)
        time.sleep(0.1)

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
            app._set_status("VT")

            try:
                app._usage_stats.record_clipboard_confirm()
            except Exception as e:
                logger.error("Failed to record clipboard confirm: %s", e)

            try:
                app._usage_stats.record_output_method(copy_to_clipboard=copy_to_clip)
            except Exception as e:
                logger.error("Failed to record output method: %s", e)

            try:
                app._conversation_history.log(
                    asr_text=clipboard_text,
                    enhanced_text=result_holder.get("enhanced_text"),
                    final_text=final_text,
                    enhance_mode=app._enhance_mode,
                    preview_enabled=True,
                    stt_model=app._current_stt_model(),
                    llm_model=app._current_llm_model(),
                    user_corrected=bool(result_holder.get("user_corrected")),
                )
            except Exception as e:
                logger.error("Failed to log conversation: %s", e)
        else:
            app._set_status("VT")
            try:
                app._usage_stats.record_clipboard_cancel()
            except Exception as e:
                logger.error("Failed to record clipboard cancel: %s", e)
            logger.info("Clipboard enhance cancelled by user")

    # ------------------------------------------------------------------
    # Preview panel callbacks
    # ------------------------------------------------------------------

    def on_preview_mode_change(self, mode_id: str) -> None:
        """Handle mode switch from the preview panel's segmented control."""
        from PyObjCTools import AppHelper

        app = self._app

        # Update enhance mode
        app._enhance_mode = mode_id
        app._enhance_controller.enhance_mode = mode_id

        # Sync menu bar checkmarks
        for m, item in app._enhance_menu_items.items():
            item.state = 1 if m == mode_id else 0

        # Update enhancer state
        if app._enhancer:
            if mode_id == MODE_OFF:
                app._enhancer._enabled = False
            else:
                app._enhancer._enabled = True
                app._enhancer.mode = mode_id

        # Persist to config
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"]["enabled"] = mode_id != MODE_OFF
        app._config["ai_enhance"]["mode"] = mode_id
        save_config(app._config, app._config_path)
        logger.info("AI enhance mode set to (from preview): %s", mode_id)

        # Update panel UI
        if mode_id == MODE_OFF:
            AppHelper.callAfter(app._preview_panel.set_enhance_off)
        else:
            cached = app._enhance_controller.get_cached()
            if cached is not None:
                app._preview_panel.replay_cached_result(
                    display_text=cached.display_text,
                    usage=cached.usage,
                    system_prompt=cached.system_prompt,
                    thinking_text=cached.thinking_text,
                    final_text=cached.final_text,
                )
            else:
                AppHelper.callAfter(app._preview_panel.set_enhance_loading)
                app._preview_panel.enhance_request_id += 1
                asr_text = getattr(app, "_current_preview_asr_text", "")
                app._enhance_controller.run(
                    asr_text, app._preview_panel.enhance_request_id
                )

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
                    )

                new_transcriber.initialize()

                # Re-transcribe using wav_data
                new_transcriber.skip_punc = bool(
                    app._enhancer and app._enhancer.is_active
                )
                new_text = new_transcriber.transcribe(wav_data)

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
                            new_text, app._preview_panel.enhance_request_id
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
            else:
                app._preview_panel.set_enhance_loading()
                app._preview_panel.enhance_request_id += 1
                asr_text = getattr(app, "_current_preview_asr_text", "")
                app._enhance_controller.run(
                    asr_text, app._preview_panel.enhance_request_id
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
                            new_text, app._preview_panel.enhance_request_id
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
            cached = app._enhance_controller.get_cached()
            if cached is not None:
                app._preview_panel.replay_cached_result(
                    display_text=cached.display_text,
                    usage=cached.usage,
                    system_prompt=cached.system_prompt,
                    thinking_text=cached.thinking_text,
                    final_text=cached.final_text,
                )
            else:
                AppHelper.callAfter(app._preview_panel.set_enhance_loading)
                app._preview_panel.enhance_request_id += 1
                asr_text = getattr(app, "_current_preview_asr_text", "")
                app._enhance_controller.run(
                    asr_text, app._preview_panel.enhance_request_id
                )
