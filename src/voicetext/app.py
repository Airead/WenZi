"""VoiceText macOS menubar application."""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import rumps
from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions
from CoreFoundation import kCFBooleanTrue

from .auto_vocab_builder import AutoVocabBuilder
from .config import load_config, save_config
from .conversation_history import ConversationHistory
from .correction_log import CorrectionLogger
from .usage_stats import UsageStats
from .enhancer import MODE_OFF, TextEnhancer, create_enhancer
from .result_window import ResultPreviewPanel
from .hotkey import HoldHotkeyListener, TapHotkeyListener
from .input import (
    copy_selection_to_clipboard,
    get_clipboard_text,
    has_clipboard_text,
    set_clipboard_text,
    type_text,
)
from .model_registry import (
    PRESET_BY_ID,
    PRESETS,
    ModelPreset,
    RemoteASRModel,
    build_remote_asr_models,
    get_model_cache_dir,
    is_backend_available,
    is_model_cached,
    resolve_preset_from_config,
)
from .recorder import Recorder
from .transcriber import create_transcriber


logger = logging.getLogger(__name__)

LOG_DIR = Path.home() / "Library" / "Logs" / "VoiceText"
LOG_FILE = LOG_DIR / "voicetext.log"

# Approximate FunASR total model size in bytes (ASR + VAD + PUNC)
_FUNASR_APPROX_SIZE = 502 * 1024 * 1024


class VoiceTextApp(rumps.App):
    """Menubar app: hold hotkey to record, release to transcribe and type."""

    def __init__(self, config_path: Optional[str] = None) -> None:
        super().__init__("VoiceText", icon=None, title="VT")

        self._config_path = config_path
        self._config = load_config(config_path)
        self._setup_logging()

        audio_cfg = self._config["audio"]
        self._recorder = Recorder(
            sample_rate=audio_cfg["sample_rate"],
            block_ms=audio_cfg["block_ms"],
            device=audio_cfg.get("device"),
            max_session_bytes=audio_cfg["max_session_bytes"],
            silence_rms=audio_cfg.get("silence_rms", Recorder.DEFAULT_SILENCE_RMS),
        )

        asr_cfg = self._config["asr"]

        # Migrate old flat base_url/api_key to provider format
        self._migrate_asr_config(asr_cfg)

        # Remote ASR state: (provider_name, model_name) or None
        self._current_remote_asr: Optional[Tuple[str, str]] = None
        default_provider = asr_cfg.get("default_provider")
        default_model = asr_cfg.get("default_model")

        if default_provider and default_model:
            # Start with remote model
            providers = asr_cfg.get("providers", {})
            pcfg = providers.get(default_provider, {})
            if pcfg and default_model in pcfg.get("models", []):
                self._current_remote_asr = (default_provider, default_model)
                self._transcriber = create_transcriber(
                    backend="whisper-api",
                    base_url=pcfg["base_url"],
                    api_key=pcfg["api_key"],
                    model=default_model,
                    language=asr_cfg.get("language"),
                    temperature=asr_cfg.get("temperature"),
                )
            else:
                # Provider/model not found, fall back to local
                self._transcriber = create_transcriber(
                    backend=asr_cfg.get("backend", "funasr"),
                    use_vad=asr_cfg.get("use_vad", True),
                    use_punc=asr_cfg.get("use_punc", True),
                    language=asr_cfg.get("language"),
                    model=asr_cfg.get("model"),
                    temperature=asr_cfg.get("temperature"),
                )
        else:
            self._transcriber = create_transcriber(
                backend=asr_cfg.get("backend", "funasr"),
                use_vad=asr_cfg.get("use_vad", True),
                use_punc=asr_cfg.get("use_punc", True),
                language=asr_cfg.get("language"),
                model=asr_cfg.get("model"),
                temperature=asr_cfg.get("temperature"),
            )

        self._output_method = self._config["output"]["method"]
        self._append_newline = self._config["output"]["append_newline"]
        self._preview_enabled = self._config["output"].get("preview", True)
        self._hotkey_listener: Optional[HoldHotkeyListener] = None
        self._clipboard_hotkey_listener: Optional[TapHotkeyListener] = None
        self._busy = False
        self._preview_panel = ResultPreviewPanel()
        self._enhance_cancel_event: threading.Event | None = None
        self._correction_logger = CorrectionLogger()
        self._conversation_history = ConversationHistory()
        self._usage_stats = UsageStats()

        # Resolve current preset (None if using remote)
        self._current_preset_id: Optional[str] = None
        if not self._current_remote_asr:
            self._current_preset_id = asr_cfg.get("preset")
            if not self._current_preset_id:
                self._current_preset_id = resolve_preset_from_config(
                    asr_cfg.get("backend", "funasr"),
                    asr_cfg.get("model"),
                )

        # Menu items
        self._status_item = rumps.MenuItem("Ready")
        self._status_item.set_callback(None)
        hotkey_name = self._config["hotkey"]
        self._hotkey_item = rumps.MenuItem(f"Hotkey: {hotkey_name}")
        self._hotkey_item.set_callback(None)

        # STT Model submenu
        self._model_menu = rumps.MenuItem("STT Model")
        self._model_menu_items: Dict[str, rumps.MenuItem] = {}
        self._remote_asr_menu_items: Dict[Tuple[str, str], rumps.MenuItem] = {}
        self._asr_add_provider_item = rumps.MenuItem(
            "Add ASR Provider...", callback=self._on_asr_add_provider
        )
        self._asr_remove_provider_menu = rumps.MenuItem("Remove ASR Provider")
        self._asr_remove_provider_items: Dict[str, rumps.MenuItem] = {}
        self._build_model_menu()

        # AI Enhance
        self._enhancer = create_enhancer(self._config)
        ai_cfg = self._config.get("ai_enhance", {})
        self._enhance_mode: str = ai_cfg.get("mode", "proofread")
        if self._enhancer and not ai_cfg.get("enabled", False):
            self._enhance_mode = MODE_OFF

        # Auto vocabulary builder
        vocab_cfg = ai_cfg.get("vocabulary", {})
        self._auto_vocab_builder = AutoVocabBuilder(
            config=self._config,
            enabled=vocab_cfg.get("auto_build", True),
            threshold=vocab_cfg.get("auto_build_threshold", 10),
            on_build_done=self._update_vocab_title,
        )
        if self._enhancer:
            self._auto_vocab_builder.set_enhancer(self._enhancer)

        # AI Enhance submenu (mode selection only)
        self._enhance_menu = rumps.MenuItem("AI Enhance")
        self._enhance_menu_items: Dict[str, rumps.MenuItem] = {}

        # Fixed "Off" item
        off_item = rumps.MenuItem("Off")
        off_item._enhance_mode = MODE_OFF
        off_item.set_callback(self._on_enhance_mode_select)
        if self._enhance_mode == MODE_OFF:
            off_item.state = 1
        self._enhance_menu_items[MODE_OFF] = off_item
        self._enhance_menu.add(off_item)

        # Dynamic mode items from enhancer
        if self._enhancer:
            for mode_id, label in self._enhancer.available_modes:
                item = rumps.MenuItem(label)
                item._enhance_mode = mode_id
                item.set_callback(self._on_enhance_mode_select)
                if mode_id == self._enhance_mode:
                    item.state = 1
                self._enhance_menu_items[mode_id] = item
                self._enhance_menu.add(item)

        # Add Mode item
        self._enhance_menu.add(rumps.separator)
        self._enhance_add_mode_item = rumps.MenuItem(
            "Add Mode...", callback=self._on_enhance_add_mode
        )
        self._enhance_menu.add(self._enhance_add_mode_item)

        # Top-level toggle items (promoted from AI Enhance)
        vocab_enabled = ai_cfg.get("vocabulary", {}).get("enabled", False)
        self._enhance_vocab_item = rumps.MenuItem(
            "Vocabulary", callback=self._on_vocab_toggle
        )
        self._enhance_vocab_item.state = 1 if vocab_enabled else 0
        self._update_vocab_title()

        history_enabled = ai_cfg.get("conversation_history", {}).get("enabled", False)
        self._enhance_history_item = rumps.MenuItem(
            "Conversation History", callback=self._on_history_toggle
        )
        self._enhance_history_item.state = 1 if history_enabled else 0

        # LLM Model top-level submenu
        self._llm_model_menu = rumps.MenuItem("LLM Model")
        self._llm_model_menu_items: Dict[Tuple[str, str], rumps.MenuItem] = {}
        self._llm_add_provider_item = rumps.MenuItem(
            "Add Provider...", callback=self._on_enhance_add_provider
        )
        self._llm_remove_provider_menu = rumps.MenuItem("Remove Provider")
        self._llm_remove_provider_items: Dict[str, rumps.MenuItem] = {}
        self._build_llm_model_menu()

        # AI Settings submenu (low-frequency AI configuration)
        self._ai_settings_menu = rumps.MenuItem("AI Settings")

        # Thinking toggle
        self._enhance_thinking_item = rumps.MenuItem(
            "Thinking", callback=self._on_enhance_thinking_toggle
        )
        if self._enhancer and self._enhancer.thinking:
            self._enhance_thinking_item.state = 1
        self._ai_settings_menu.add(self._enhance_thinking_item)

        # Build vocabulary action
        self._ai_settings_menu.add(rumps.separator)
        self._enhance_vocab_build_item = rumps.MenuItem(
            "Build Vocabulary...", callback=self._on_vocab_build
        )
        self._ai_settings_menu.add(self._enhance_vocab_build_item)

        self._enhance_auto_build_item = rumps.MenuItem(
            "Auto Build Vocabulary", callback=self._on_auto_build_toggle
        )
        self._enhance_auto_build_item.state = 1 if vocab_cfg.get("auto_build", True) else 0
        self._ai_settings_menu.add(self._enhance_auto_build_item)

        self._ai_settings_menu.add(rumps.separator)
        self._enhance_edit_config_item = rumps.MenuItem(
            "Edit Config...", callback=self._on_enhance_edit_config
        )
        self._ai_settings_menu.add(self._enhance_edit_config_item)

        self._preview_item = rumps.MenuItem(
            "Preview", callback=self._on_preview_toggle
        )
        self._preview_item.state = 1 if self._preview_enabled else 0

        self._clipboard_enhance_item = rumps.MenuItem(
            "Enhance Clipboard", callback=self._on_clipboard_enhance
        )

        # Debug submenu
        self._debug_menu = rumps.MenuItem("Debug")

        # Log level submenu
        self._debug_level_menu = rumps.MenuItem("Log Level")
        self._debug_level_items: Dict[str, rumps.MenuItem] = {}
        current_level = self._config["logging"]["level"]
        for level_name in ("DEBUG", "INFO", "WARNING", "ERROR"):
            item = rumps.MenuItem(level_name)
            item._log_level = level_name
            item.set_callback(self._on_debug_level_select)
            if level_name == current_level:
                item.state = 1
            self._debug_level_items[level_name] = item
            self._debug_level_menu.add(item)
        self._debug_menu.add(self._debug_level_menu)

        # Print Prompt toggle
        self._debug_print_prompt_item = rumps.MenuItem(
            "Print Prompt", callback=self._on_debug_print_prompt_toggle
        )
        self._debug_menu.add(self._debug_print_prompt_item)

        # Print Request Body toggle
        self._debug_print_request_body_item = rumps.MenuItem(
            "Print Request Body", callback=self._on_debug_print_request_body_toggle
        )
        self._debug_menu.add(self._debug_print_request_body_item)

        # Copy Log Path (moved into Debug)
        self._debug_menu.add(rumps.separator)
        self._copy_log_item = rumps.MenuItem(
            "Copy Log Path", callback=self._on_copy_log_path
        )
        self._debug_menu.add(self._copy_log_item)

        # Show Config / Reload Config items
        self._show_config_item = rumps.MenuItem(
            "Show Config...", callback=self._on_show_config
        )
        self._reload_config_item = rumps.MenuItem(
            "Reload Config", callback=self._on_reload_config
        )

        # Usage Stats item
        self._usage_stats_item = rumps.MenuItem(
            "Usage Stats", callback=self._on_show_usage_stats
        )

        # About item
        self._about_item = rumps.MenuItem("About VoiceText", callback=self._on_about)

        self.menu = [
            self._status_item,
            self._hotkey_item,
            None,
            self._model_menu,
            self._llm_model_menu,
            self._enhance_menu,
            None,
            self._clipboard_enhance_item,
            self._preview_item,
            self._enhance_vocab_item,
            self._enhance_history_item,
            None,
            self._ai_settings_menu,
            self._debug_menu,
            None,
            self._show_config_item,
            self._reload_config_item,
            self._usage_stats_item,
            self._about_item,
        ]
        self.quit_button.set_callback(self._on_quit_click)

    def _setup_logging(self) -> None:
        level = self._config["logging"]["level"]
        log_level = getattr(logging, level, logging.INFO)
        fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter(fmt))

        logging.basicConfig(
            level=log_level,
            format=fmt,
            handlers=[logging.StreamHandler(), file_handler],
        )

    def _set_status(self, text: str) -> None:
        """Update menu bar title and status menu item."""
        self.title = text
        self._status_item.title = text

    def _on_hotkey_press(self) -> None:
        """Called when hotkey is pressed down - start recording."""
        if self._busy:
            return
        logger.info("Hotkey pressed, starting recording")
        self._set_status("Recording...")
        self._recorder.start()

    def _on_hotkey_release(self) -> None:
        """Called when hotkey is released - stop recording and transcribe."""
        if not self._recorder.is_recording:
            return
        logger.info("Hotkey released, stopping recording")
        wav_data = self._recorder.stop()
        if not wav_data:
            self._set_status("VT")
            return

        self._busy = True

        if self._preview_enabled:
            self._set_status("Transcribing...")
            # Show preview immediately, transcribe in background
            def _do_preview():
                try:
                    self._do_transcribe_with_preview(
                        asr_text=None,
                        use_enhance=bool(self._enhancer and self._enhancer.is_active),
                        audio_duration=0.0,
                        wav_data=wav_data,
                    )
                except Exception as e:
                    logger.error("Preview transcription failed: %s", e)
                    self._set_status("Error")
                    self._busy = False

            threading.Thread(target=_do_preview, daemon=True).start()
        else:
            self._set_status("Transcribing...")
            # Run transcription in background to keep UI responsive
            def _do_transcribe():
                try:
                    from .transcriber import BaseTranscriber

                    audio_duration = BaseTranscriber.wav_duration_seconds(wav_data)
                    self._transcriber.skip_punc = bool(
                        self._enhancer and self._enhancer.is_active
                    )
                    text = self._transcriber.transcribe(wav_data)
                    if text and text.strip():
                        asr_text = text.strip()
                        use_enhance = bool(self._enhancer and self._enhancer.is_active)
                        self._do_transcribe_direct(asr_text, use_enhance)
                    else:
                        self._set_status("(empty)")
                        logger.warning("Transcription returned empty text")
                except Exception as e:
                    logger.error("Transcription failed: %s", e)
                    self._set_status("Error")
                finally:
                    self._busy = False

            threading.Thread(target=_do_transcribe, daemon=True).start()

    def _do_transcribe_direct(self, asr_text: str, use_enhance: bool) -> None:
        """Original flow: enhance (if enabled) and type directly."""
        try:
            self._usage_stats.record_transcription(
                mode="direct", enhance_mode=self._enhance_mode
            )
        except Exception as e:
            logger.error("Failed to record usage stats: %s", e)

        text = asr_text
        enhanced_text = None
        if use_enhance:
            self._set_status("Enhancing...")
            try:
                loop = asyncio.new_event_loop()
                text, _usage = loop.run_until_complete(self._enhancer.enhance(text))
                loop.close()
                enhanced_text = text
                try:
                    self._usage_stats.record_token_usage(_usage)
                except Exception as e:
                    logger.error("Failed to record token usage: %s", e)
            except Exception as e:
                logger.error("AI enhancement failed: %s", e)

        type_text(
            text.strip(),
            append_newline=self._append_newline,
            method=self._output_method,
        )
        self._set_status("VT")

        try:
            self._usage_stats.record_confirm(modified=False)
        except Exception as e:
            logger.error("Failed to record usage stats: %s", e)

        try:
            self._conversation_history.log(
                asr_text=asr_text,
                enhanced_text=enhanced_text,
                final_text=text.strip(),
                enhance_mode=self._enhance_mode,
                preview_enabled=False,
            )
        except Exception as e:
            logger.error("Failed to log conversation: %s", e)

    def _do_transcribe_with_preview(
        self, asr_text: str | None, use_enhance: bool,
        audio_duration: float = 0.0, wav_data: Optional[bytes] = None,
    ) -> None:
        """Show preview panel, optionally run AI enhance, wait for user decision.

        If asr_text is None, the panel opens immediately in a loading state
        and STT runs in the background.
        """
        from PyObjCTools import AppHelper
        import time

        try:
            self._usage_stats.record_transcription(
                mode="preview", enhance_mode=self._enhance_mode
            )
        except Exception as e:
            logger.error("Failed to record usage stats: %s", e)

        self._current_preview_asr_text = asr_text or ""

        result_event = threading.Event()
        result_holder = {"text": None, "confirmed": False, "enhanced_text": None}

        def on_confirm(text: str, correction_info: dict | None = None) -> None:
            result_holder["text"] = text
            result_holder["confirmed"] = True
            if correction_info is not None:
                try:
                    self._correction_logger.log(
                        asr_text=correction_info["asr_text"],
                        enhanced_text=correction_info["enhanced_text"],
                        final_text=correction_info["final_text"],
                        enhance_mode=self._enhance_mode,
                    )
                    self._auto_vocab_builder.on_correction_logged()
                except Exception as e:
                    logger.error("Failed to log correction: %s", e)
            try:
                self._usage_stats.record_confirm(modified=correction_info is not None)
            except Exception as e:
                logger.error("Failed to record usage stats: %s", e)
            result_event.set()

        def on_cancel() -> None:
            result_holder["confirmed"] = False
            # Stop any in-flight streaming enhancement
            if self._enhance_cancel_event is not None:
                self._enhance_cancel_event.set()
            try:
                self._usage_stats.record_cancel()
            except Exception as e:
                logger.error("Failed to record usage stats: %s", e)
            result_event.set()

        # Build mode list for the segmented control
        available_modes = []
        if self._enhancer:
            available_modes = [("off", "Off")] + self._enhancer.available_modes

        # Build ASR info string (duration only when popup available, else model+duration)
        asr_info_parts = []
        if audio_duration > 0:
            asr_info_parts.append(f"{audio_duration:.1f}s")
        # Store duration for re-transcription info updates
        self._preview_audio_duration = audio_duration

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
                    if preset.id == self._current_preset_id and not self._current_remote_asr:
                        stt_current_index = len(stt_models) - 1

            # Remote ASR models
            asr_cfg = self._config.get("asr", {})
            providers = asr_cfg.get("providers", {})
            remote_models = build_remote_asr_models(providers)
            for rm in remote_models:
                stt_models.append(rm.display_name)
                stt_model_keys.append(("remote", (rm.provider, rm.model)))
                if self._current_remote_asr == (rm.provider, rm.model):
                    stt_current_index = len(stt_models) - 1

        self._preview_stt_keys = stt_model_keys

        # Add model name to asr_info when no popup (backward compat)
        if not stt_models:
            try:
                asr_info_parts.insert(0, self._transcriber.model_display_name)
            except Exception:
                pass
        asr_info = "  ".join(asr_info_parts)

        # Build LLM model list for popup
        llm_models: List[str] = []
        llm_model_keys: list = []
        llm_current_index = 0

        if self._enhancer:
            providers_with = self._enhancer.providers_with_models
            current_llm = (self._enhancer.provider_name, self._enhancer.model_name)
            for pname, models in providers_with.items():
                for mname in models:
                    key = (pname, mname)
                    llm_models.append(f"{pname} / {mname}")
                    llm_model_keys.append(key)
                    if key == current_llm:
                        llm_current_index = len(llm_models) - 1

        self._preview_llm_keys = llm_model_keys

        # Build enhance info string
        enhance_info = ""
        if self._enhancer:
            parts = []
            if self._enhancer.provider_name:
                parts.append(self._enhancer.provider_name)
            if self._enhancer.model_name:
                parts.append(self._enhancer.model_name)
            enhance_info = " / ".join(parts)

        # Determine whether STT needs to run in background
        need_stt = asr_text is None
        display_asr_text = "" if need_stt else asr_text

        # Show panel on main thread, then start enhancement/STT after panel is built
        def _show():
            self._activate_for_dialog()
            self._preview_panel.show(
                asr_text=display_asr_text,
                show_enhance=use_enhance,
                on_confirm=on_confirm,
                on_cancel=on_cancel,
                available_modes=available_modes,
                current_mode=self._enhance_mode,
                on_mode_change=self._on_preview_mode_change,
                asr_info=asr_info if not need_stt else "",
                asr_wav_data=wav_data,
                enhance_info=enhance_info,
                stt_models=stt_models if stt_models else None,
                stt_current_index=stt_current_index,
                on_stt_model_change=self._on_preview_stt_change if stt_models else None,
                llm_models=llm_models if llm_models else None,
                llm_current_index=llm_current_index,
                on_llm_model_change=self._on_preview_llm_change if llm_models else None,
                punc_enabled=not self._transcriber.skip_punc,
                on_punc_toggle=self._on_preview_punc_toggle if wav_data else None,
                thinking_enabled=self._enhancer.thinking if self._enhancer else False,
                on_thinking_toggle=self._on_preview_thinking_toggle if self._enhancer else None,
            )
            if need_stt:
                # Show loading state and disable STT popup during transcription
                self._preview_panel.set_asr_loading()
                if use_enhance:
                    self._preview_panel.set_enhance_loading()
            elif use_enhance:
                # ASR already available, start enhancement immediately
                self._preview_panel.enhance_request_id += 1
                self._run_enhance_in_background(
                    asr_text, self._preview_panel.enhance_request_id, result_holder
                )

        AppHelper.callAfter(_show)
        self._set_status("Preview...")

        # Run STT in background if needed
        if need_stt:
            def _do_stt():
                try:
                    from .transcriber import BaseTranscriber

                    audio_dur = BaseTranscriber.wav_duration_seconds(wav_data)
                    self._preview_audio_duration = audio_dur
                    self._transcriber.skip_punc = bool(
                        self._enhancer and self._enhancer.is_active
                    )
                    text = self._transcriber.transcribe(wav_data)
                    if text and text.strip():
                        stt_text = text.strip()
                    else:
                        stt_text = "(empty)"
                        logger.warning("Transcription returned empty text")

                    self._current_preview_asr_text = stt_text

                    # Build ASR info
                    parts = []
                    if not stt_models:
                        try:
                            parts.insert(0, self._transcriber.model_display_name)
                        except Exception:
                            pass
                    if audio_dur > 0:
                        parts.append(f"{audio_dur:.1f}s")
                    new_asr_info = "  ".join(parts)

                    request_id = self._preview_panel._asr_request_id

                    def _on_stt_done():
                        self._preview_panel.set_asr_result(
                            stt_text, asr_info=new_asr_info, request_id=request_id,
                        )
                        # Start enhancement now that ASR is ready
                        if use_enhance and stt_text != "(empty)":
                            self._preview_panel.enhance_request_id += 1
                            self._run_enhance_in_background(
                                stt_text, self._preview_panel.enhance_request_id,
                                result_holder,
                            )
                        elif use_enhance:
                            # Empty text — clear enhance loading
                            self._preview_panel.set_enhance_off()

                    AppHelper.callAfter(_on_stt_done)
                except Exception as e:
                    logger.error("Background STT failed: %s", e)
                    self._preview_panel.set_asr_result(
                        f"(error: {e})",
                        request_id=self._preview_panel._asr_request_id,
                    )

            threading.Thread(target=_do_stt, daemon=True).start()

        # Wait for user decision
        result_event.wait()
        self._busy = False

        # Restore menu bar mode and inject text
        AppHelper.callAfter(self._restore_accessory)
        time.sleep(0.1)  # Brief delay for target app to regain focus

        if result_holder["confirmed"] and result_holder["text"]:
            final_text = result_holder["text"].strip()
            type_text(
                final_text,
                append_newline=self._append_newline,
                method=self._output_method,
            )
            self._set_status("VT")

            try:
                self._conversation_history.log(
                    asr_text=self._current_preview_asr_text,
                    enhanced_text=result_holder["enhanced_text"],
                    final_text=final_text,
                    enhance_mode=self._enhance_mode,
                    preview_enabled=True,
                )
            except Exception as e:
                logger.error("Failed to log conversation: %s", e)
        else:
            self._set_status("VT")
            logger.info("Preview cancelled by user")

    _CLIPBOARD_MAX_CHARS = 300

    def _on_clipboard_enhance(self, _sender=None) -> None:
        """Handle Enhance Clipboard menu item or hotkey activation.

        May be called from a background thread (Quartz event tap).
        Launches a worker thread that simulates Cmd+C to capture the
        current selection, then validates and enhances the clipboard text.
        """
        threading.Thread(
            target=self._on_clipboard_enhance_worker, daemon=True
        ).start()

    def _on_clipboard_enhance_worker(self) -> None:
        """Worker-thread implementation of clipboard enhance.

        Simulates Cmd+C to copy the current selection, then validates
        the clipboard content. UI dialogs are dispatched to the main thread.
        """
        from PyObjCTools import AppHelper

        if self._busy:
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

        self._busy = True
        self._set_status("Enhancing...")

        try:
            self._do_clipboard_with_preview(clipboard_text)
        except Exception as e:
            logger.error("Clipboard enhance failed: %s", e)
            self._set_status("Error")
        finally:
            self._busy = False

    def _clipboard_enhance_show_error(self, title: str, message: str) -> None:
        """Show an error alert on the main thread for clipboard enhance."""
        self._topmost_alert(title=title, message=message)
        self._restore_accessory()

    def _do_clipboard_with_preview(self, clipboard_text: str) -> None:
        """Show preview panel for clipboard text enhancement."""
        from PyObjCTools import AppHelper
        import time

        self._current_preview_asr_text = clipboard_text

        result_event = threading.Event()
        result_holder = {"text": None, "confirmed": False, "enhanced_text": None}

        def on_confirm(text: str, correction_info: dict | None = None) -> None:
            result_holder["text"] = text
            result_holder["confirmed"] = True
            result_event.set()

        def on_cancel() -> None:
            result_holder["confirmed"] = False
            # Stop any in-flight streaming enhancement
            if self._enhance_cancel_event is not None:
                self._enhance_cancel_event.set()
            result_event.set()

        # Build mode list for the segmented control
        available_modes = []
        if self._enhancer:
            available_modes = [("off", "Off")] + self._enhancer.available_modes

        # Build LLM model list for popup
        llm_models: List[str] = []
        llm_model_keys: list = []
        llm_current_index = 0

        if self._enhancer:
            providers_with = self._enhancer.providers_with_models
            current_llm = (self._enhancer.provider_name, self._enhancer.model_name)
            for pname, models in providers_with.items():
                for mname in models:
                    key = (pname, mname)
                    llm_models.append(f"{pname} / {mname}")
                    llm_model_keys.append(key)
                    if key == current_llm:
                        llm_current_index = len(llm_models) - 1

        self._preview_llm_keys = llm_model_keys

        # Build enhance info string
        enhance_info = ""
        if self._enhancer:
            parts = []
            if self._enhancer.provider_name:
                parts.append(self._enhancer.provider_name)
            if self._enhancer.model_name:
                parts.append(self._enhancer.model_name)
            enhance_info = " / ".join(parts)

        use_enhance = bool(self._enhancer and self._enhancer.is_active)

        def _show():
            self._activate_for_dialog()
            self._preview_panel.show(
                asr_text=clipboard_text,
                show_enhance=use_enhance,
                on_confirm=on_confirm,
                on_cancel=on_cancel,
                available_modes=available_modes,
                current_mode=self._enhance_mode,
                on_mode_change=self._on_preview_mode_change,
                asr_info="",
                asr_wav_data=None,
                enhance_info=enhance_info,
                stt_models=None,
                stt_current_index=0,
                on_stt_model_change=None,
                llm_models=llm_models if llm_models else None,
                llm_current_index=llm_current_index,
                on_llm_model_change=self._on_preview_llm_change if llm_models else None,
                source="clipboard",
                thinking_enabled=self._enhancer.thinking if self._enhancer else False,
                on_thinking_toggle=self._on_preview_thinking_toggle if self._enhancer else None,
            )
            if use_enhance:
                self._preview_panel.enhance_request_id += 1
                self._run_enhance_in_background(
                    clipboard_text, self._preview_panel.enhance_request_id, result_holder
                )

        AppHelper.callAfter(_show)
        self._set_status("Preview...")

        result_event.wait()

        AppHelper.callAfter(self._restore_accessory)
        time.sleep(0.1)

        clip_cfg = self._config.get("clipboard_enhance", {})
        output_mode = clip_cfg.get("output", "clipboard")

        if result_holder["confirmed"] and result_holder["text"]:
            final_text = result_holder["text"].strip()
            if output_mode == "type_text":
                type_text(
                    final_text,
                    append_newline=self._append_newline,
                    method=self._output_method,
                )
            else:
                set_clipboard_text(final_text)
                rumps.notification("VoiceText", "Clipboard Updated", final_text[:80])
            self._set_status("VT")
        else:
            self._set_status("VT")
            logger.info("Clipboard enhance cancelled by user")

    def _run_enhance_in_background(
        self, asr_text: str, request_id: int, result_holder: dict | None = None,
    ) -> None:
        """Run AI enhancement in a background thread with streaming."""
        # Cancel any in-flight enhancement and create a fresh cancel event
        if self._enhance_cancel_event is not None:
            self._enhance_cancel_event.set()
        cancel_event = threading.Event()
        self._enhance_cancel_event = cancel_event

        def _enhance():
            try:
                loop = asyncio.new_event_loop()
                collected: list[str] = []
                usage = None
                cancelled = False

                async def _stream():
                    nonlocal usage, cancelled
                    gen = self._enhancer.enhance_stream(asr_text)
                    try:
                        async for chunk, chunk_usage in gen:
                            # Check cancellation between chunks
                            if cancel_event is not None and cancel_event.is_set():
                                cancelled = True
                                return
                            if chunk:
                                collected.append(chunk)
                                self._preview_panel.append_enhance_text(
                                    chunk, request_id=request_id
                                )
                            if chunk_usage is not None:
                                usage = chunk_usage
                    finally:
                        # Explicitly close the async generator so that
                        # enhance_stream's finally block runs and closes
                        # the HTTP stream, stopping remote generation.
                        await gen.aclose()

                loop.run_until_complete(_stream())
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

                if cancelled:
                    logger.info("AI enhancement cancelled by user")
                    return

                enhanced = "".join(collected).strip() or asr_text
                if result_holder is not None:
                    result_holder["enhanced_text"] = enhanced
                try:
                    self._usage_stats.record_token_usage(usage)
                except Exception as e:
                    logger.error("Failed to record token usage: %s", e)
                system_prompt = self._enhancer.last_system_prompt
                self._preview_panel.set_enhance_complete(
                    request_id=request_id, usage=usage,
                    system_prompt=system_prompt,
                )
            except Exception as e:
                logger.error("AI enhancement failed: %s", e)
                self._preview_panel.set_enhance_result(
                    f"(error: {e})", request_id=request_id
                )

        threading.Thread(target=_enhance, daemon=True).start()

    def _on_preview_mode_change(self, mode_id: str) -> None:
        """Handle mode switch from the preview panel's segmented control."""
        from PyObjCTools import AppHelper

        # Update enhance mode
        self._enhance_mode = mode_id

        # Sync menu bar checkmarks
        for m, item in self._enhance_menu_items.items():
            item.state = 1 if m == mode_id else 0

        # Update enhancer state
        if self._enhancer:
            if mode_id == MODE_OFF:
                self._enhancer._enabled = False
            else:
                self._enhancer._enabled = True
                self._enhancer.mode = mode_id

        # Persist to config
        self._config.setdefault("ai_enhance", {})
        self._config["ai_enhance"]["enabled"] = mode_id != MODE_OFF
        self._config["ai_enhance"]["mode"] = mode_id
        save_config(self._config, self._config_path)
        logger.info("AI enhance mode set to (from preview): %s", mode_id)

        # Update panel UI
        if mode_id == MODE_OFF:
            AppHelper.callAfter(self._preview_panel.set_enhance_off)
        else:
            AppHelper.callAfter(self._preview_panel.set_enhance_loading)
            self._preview_panel.enhance_request_id += 1
            asr_text = getattr(self, "_current_preview_asr_text", "")
            self._run_enhance_in_background(
                asr_text, self._preview_panel.enhance_request_id
            )

    def _on_preview_stt_change(self, index: int) -> None:
        """Handle STT model popup change from the preview panel."""
        from PyObjCTools import AppHelper

        if index < 0 or index >= len(self._preview_stt_keys):
            return

        key_type, key_value = self._preview_stt_keys[index]

        # Check if same as current
        if key_type == "preset":
            if key_value == self._current_preset_id and not self._current_remote_asr:
                return
        elif key_type == "remote":
            if key_value == self._current_remote_asr:
                return

        old_index = self._preview_stt_keys.index(
            ("preset", self._current_preset_id) if not self._current_remote_asr
            else ("remote", self._current_remote_asr)
        ) if (
            ("preset", self._current_preset_id) if not self._current_remote_asr
            else ("remote", self._current_remote_asr)
        ) in self._preview_stt_keys else 0

        # Show loading state
        self._preview_panel.set_asr_loading()
        request_id = self._preview_panel.asr_request_id

        old_transcriber = self._transcriber
        wav_data = self._preview_panel._asr_wav_data

        def _do_switch():
            try:
                old_transcriber.cleanup()

                asr_cfg = self._config.get("asr", {})
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
                    self._enhancer and self._enhancer.is_active
                )
                new_text = new_transcriber.transcribe(wav_data)

                # Build new ASR info (duration only since model is in popup)
                audio_duration = getattr(self, "_preview_audio_duration", 0.0)
                new_asr_info = f"{audio_duration:.1f}s" if audio_duration > 0 else ""

                def _on_success():
                    self._transcriber = new_transcriber
                    if key_type == "preset":
                        self._current_preset_id = key_value
                        self._current_remote_asr = None
                        self._config["asr"]["preset"] = key_value
                        preset = PRESET_BY_ID[key_value]
                        self._config["asr"]["backend"] = preset.backend
                        self._config["asr"]["model"] = preset.model
                        self._config["asr"]["language"] = preset.language
                        self._config["asr"]["default_provider"] = None
                        self._config["asr"]["default_model"] = None
                    else:
                        prov, mod = key_value
                        self._current_remote_asr = key_value
                        self._current_preset_id = None
                        self._config["asr"]["default_provider"] = prov
                        self._config["asr"]["default_model"] = mod

                    self._update_model_checkmarks()
                    save_config(self._config, self._config_path)

                    self._preview_panel.set_asr_result(
                        new_text, asr_info=new_asr_info, request_id=request_id,
                    )
                    self._current_preview_asr_text = new_text

                    # Re-run enhance if mode is not Off
                    if self._enhance_mode != MODE_OFF and self._enhancer:
                        self._preview_panel.set_enhance_loading()
                        self._preview_panel.enhance_request_id += 1
                        self._run_enhance_in_background(
                            new_text, self._preview_panel.enhance_request_id
                        )

                AppHelper.callAfter(_on_success)
                logger.info("Preview STT switched to index %d", index)

            except Exception as e:
                logger.error("Preview STT switch failed: %s", e)

                def _on_failure():
                    # Try to restore old transcriber
                    self._try_restore_previous_model(
                        self._current_preset_id if not self._current_remote_asr else None
                    )
                    self._preview_panel.set_stt_popup_index(old_index)
                    # Restore ASR text
                    asr_text = getattr(self, "_current_preview_asr_text", "")
                    if self._preview_panel._asr_text_view is not None:
                        self._preview_panel._asr_text_view.setString_(
                            asr_text or f"(STT switch error: {e})"
                        )

                AppHelper.callAfter(_on_failure)

        threading.Thread(target=_do_switch, daemon=True).start()

    def _on_preview_llm_change(self, index: int) -> None:
        """Handle LLM model popup change from the preview panel."""
        if not self._enhancer or index < 0 or index >= len(self._preview_llm_keys):
            return

        pname, mname = self._preview_llm_keys[index]
        if pname == self._enhancer.provider_name and mname == self._enhancer.model_name:
            return

        # Update enhancer
        self._enhancer.provider_name = pname
        self._enhancer.model_name = mname

        # Update menu checkmarks
        current_key = (pname, mname)
        for key, item in self._llm_model_menu_items.items():
            item.state = 1 if key == current_key else 0

        # Persist
        self._config.setdefault("ai_enhance", {})
        self._config["ai_enhance"]["default_provider"] = pname
        self._config["ai_enhance"]["default_model"] = mname
        save_config(self._config, self._config_path)
        logger.info("Preview LLM switched to: %s / %s", pname, mname)

        # Re-run enhance if mode is not Off
        if self._enhance_mode != MODE_OFF:
            self._preview_panel.set_enhance_loading()
            self._preview_panel.enhance_request_id += 1
            asr_text = getattr(self, "_current_preview_asr_text", "")
            self._run_enhance_in_background(
                asr_text, self._preview_panel.enhance_request_id
            )

    def _on_preview_punc_toggle(self, enabled: bool) -> None:
        """Handle Punc checkbox toggle from the preview panel."""
        from PyObjCTools import AppHelper

        self._transcriber.skip_punc = not enabled
        logger.info("Punctuation restoration %s (from preview)", "enabled" if enabled else "disabled")

        # Re-transcribe with updated punc setting
        wav_data = self._preview_panel._asr_wav_data
        if not wav_data:
            return

        self._preview_panel.set_asr_loading()
        request_id = self._preview_panel.asr_request_id

        def _do_retranscribe():
            try:
                new_text = self._transcriber.transcribe(wav_data)
                audio_duration = getattr(self, "_preview_audio_duration", 0.0)
                new_asr_info = f"{audio_duration:.1f}s" if audio_duration > 0 else ""

                def _on_done():
                    self._preview_panel.set_asr_result(
                        new_text, asr_info=new_asr_info, request_id=request_id,
                    )
                    self._current_preview_asr_text = new_text

                    # Re-run enhance if mode is not Off
                    if self._enhance_mode != MODE_OFF and self._enhancer:
                        self._preview_panel.set_enhance_loading()
                        self._preview_panel.enhance_request_id += 1
                        self._run_enhance_in_background(
                            new_text, self._preview_panel.enhance_request_id
                        )

                AppHelper.callAfter(_on_done)
            except Exception as e:
                logger.error("Punc toggle re-transcribe failed: %s", e)

                def _on_fail():
                    asr_text = getattr(self, "_current_preview_asr_text", "")
                    self._preview_panel.set_asr_result(
                        asr_text, request_id=request_id,
                    )

                AppHelper.callAfter(_on_fail)

        threading.Thread(target=_do_retranscribe, daemon=True).start()

    def _on_enhance_mode_select(self, sender) -> None:
        """Handle AI enhance mode menu item click."""
        mode = sender._enhance_mode

        # Update checkmarks
        for m, item in self._enhance_menu_items.items():
            item.state = 1 if m == mode else 0

        self._enhance_mode = mode

        # Update enhancer state
        if self._enhancer:
            if mode == MODE_OFF:
                self._enhancer._enabled = False
            else:
                self._enhancer._enabled = True
                self._enhancer.mode = mode

        # Persist to config
        self._config.setdefault("ai_enhance", {})
        self._config["ai_enhance"]["enabled"] = mode != MODE_OFF
        self._config["ai_enhance"]["mode"] = mode
        save_config(self._config, self._config_path)
        logger.info("AI enhance mode set to: %s", mode)

    _ADD_MODE_TEMPLATE = """\
---
label: My New Mode
order: 60
---
You are a helpful assistant. Process the user's input as follows:
1. Describe what this mode should do
2. Add more instructions here

Output only the processed text without any explanation."""

    def _on_enhance_add_mode(self, _) -> None:
        """Show dialog for adding a new enhancement mode."""
        def _run():
            try:
                self._do_add_mode()
            except Exception as e:
                logger.error("Add mode failed: %s", e, exc_info=True)
            finally:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(self._restore_accessory)

        threading.Thread(target=_run, daemon=True).start()

    def _do_add_mode(self) -> None:
        """Internal implementation for adding a new enhancement mode file."""
        from .mode_loader import DEFAULT_MODES_DIR, parse_mode_file

        resp = self._run_multiline_window(
            title="Add Enhancement Mode",
            message=(
                "Edit the template below, then click Save.\n\n"
                "  label  – display name in menu\n"
                "  order  – sort weight (smaller = higher)\n"
                "  body   – system prompt for the LLM"
            ),
            default_text=self._ADD_MODE_TEMPLATE,
            ok="Save",
            dimensions=(420, 220),
        )
        if resp is None:
            return

        # Ask for filename (mode ID)
        name_resp = self._run_window(
            title="Mode ID",
            message=(
                "Enter a short ID for this mode (used as filename).\n"
                "Only letters, numbers, hyphens, and underscores."
            ),
            default_text="my_mode",
        )
        if name_resp is None:
            return

        import re
        mode_id = name_resp.text.strip()
        if not mode_id or not re.match(r"^[A-Za-z0-9_-]+$", mode_id):
            self._activate_for_dialog()
            self._topmost_alert(
                "Invalid ID",
                "Mode ID must contain only letters, numbers, hyphens, or underscores.",
            )
            return

        modes_dir = os.path.expanduser(DEFAULT_MODES_DIR)
        os.makedirs(modes_dir, exist_ok=True)
        file_path = os.path.join(modes_dir, f"{mode_id}.md")

        if os.path.exists(file_path):
            self._activate_for_dialog()
            self._topmost_alert(
                "Already Exists",
                f"A mode file '{mode_id}.md' already exists.\n"
                "Edit it directly or choose a different ID.",
            )
            return

        # Validate that the content is parseable
        # Write to a temp location first to validate
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(resp.text)
            tmp_path = tmp.name

        try:
            mode_def = parse_mode_file(tmp_path)
        finally:
            os.unlink(tmp_path)

        if mode_def is None or not mode_def.prompt.strip():
            self._activate_for_dialog()
            self._topmost_alert("Invalid Content", "The mode file has no prompt content.")
            return

        # Save the file
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(resp.text)
            if not resp.text.endswith("\n"):
                f.write("\n")
        logger.info("Created new mode file: %s", file_path)

        # Reload modes and rebuild menu
        if self._enhancer:
            self._enhancer.reload_modes()
            self._rebuild_enhance_mode_menu()

        self._activate_for_dialog()
        self._topmost_alert("Mode Added", f"Enhancement mode '{mode_id}' has been added.")

    def _rebuild_enhance_mode_menu(self) -> None:
        """Rebuild mode menu items from current enhancer modes."""
        # Remove old mode items (keep Off)
        for mode_id, item in list(self._enhance_menu_items.items()):
            if mode_id != MODE_OFF:
                self._enhance_menu.pop(item.title)
                del self._enhance_menu_items[mode_id]

        # Re-add from enhancer, inserting before "Add Mode..."
        if self._enhancer:
            for mode_id, label in self._enhancer.available_modes:
                item = rumps.MenuItem(label)
                item._enhance_mode = mode_id
                item.set_callback(self._on_enhance_mode_select)
                if mode_id == self._enhance_mode:
                    item.state = 1
                self._enhance_menu_items[mode_id] = item
                self._enhance_menu.insert_before(
                    self._enhance_add_mode_item.title, item
                )

    def _on_llm_model_select(self, sender) -> None:
        """Handle LLM model menu item click."""
        pname = sender._llm_provider
        mname = sender._llm_model
        if not self._enhancer:
            return
        if pname == self._enhancer.provider_name and mname == self._enhancer.model_name:
            return

        self._enhancer.provider_name = pname
        self._enhancer.model_name = mname

        # Update checkmarks
        current_key = (pname, mname)
        for key, item in self._llm_model_menu_items.items():
            item.state = 1 if key == current_key else 0

        # Persist to config
        self._config.setdefault("ai_enhance", {})
        self._config["ai_enhance"]["default_provider"] = pname
        self._config["ai_enhance"]["default_model"] = mname
        save_config(self._config, self._config_path)
        logger.info("LLM model set to: %s / %s", pname, mname)

    def _on_enhance_thinking_toggle(self, sender) -> None:
        """Toggle AI thinking mode."""
        if not self._enhancer:
            return

        new_value = not self._enhancer.thinking
        self._enhancer.thinking = new_value
        sender.state = 1 if new_value else 0

        # Persist to config
        self._config.setdefault("ai_enhance", {})
        self._config["ai_enhance"]["thinking"] = new_value
        save_config(self._config, self._config_path)
        logger.info("AI thinking set to: %s", new_value)

    def _on_preview_thinking_toggle(self, enabled: bool) -> None:
        """Handle Thinking checkbox toggle from preview panel."""
        if not self._enhancer:
            return

        self._enhancer.thinking = enabled
        self._enhance_thinking_item.state = 1 if enabled else 0

        # Persist to config
        self._config.setdefault("ai_enhance", {})
        self._config["ai_enhance"]["thinking"] = enabled
        save_config(self._config, self._config_path)
        logger.info("AI thinking set to: %s (from preview panel)", enabled)

    def _update_vocab_title(self) -> None:
        """Update the Vocabulary menu item title with the current entry count."""
        from .vocabulary import get_vocab_entry_count

        count = 0
        if self._enhancer and self._enhancer.vocab_index is not None:
            count = self._enhancer.vocab_index.entry_count
        if count == 0:
            count = get_vocab_entry_count()

        if count > 0:
            self._enhance_vocab_item.title = f"Vocabulary ({count})"
        else:
            self._enhance_vocab_item.title = "Vocabulary"

    def _on_vocab_toggle(self, sender) -> None:
        """Toggle vocabulary-based retrieval."""
        if not self._enhancer:
            return

        new_value = not self._enhancer.vocab_enabled
        self._enhancer.vocab_enabled = new_value
        sender.state = 1 if new_value else 0

        # Persist to config
        self._config.setdefault("ai_enhance", {})
        self._config["ai_enhance"].setdefault("vocabulary", {})
        self._config["ai_enhance"]["vocabulary"]["enabled"] = new_value
        save_config(self._config, self._config_path)
        logger.info("Vocabulary set to: %s", new_value)

    def _on_auto_build_toggle(self, sender) -> None:
        """Toggle automatic vocabulary building."""
        new_value = not self._auto_vocab_builder._enabled
        self._auto_vocab_builder._enabled = new_value
        sender.state = 1 if new_value else 0

        # Persist to config
        self._config.setdefault("ai_enhance", {})
        self._config["ai_enhance"].setdefault("vocabulary", {})
        self._config["ai_enhance"]["vocabulary"]["auto_build"] = new_value
        save_config(self._config, self._config_path)
        logger.info("Auto vocabulary build set to: %s", new_value)

    def _on_history_toggle(self, sender) -> None:
        """Toggle conversation history context injection."""
        if not self._enhancer:
            return

        new_value = not self._enhancer.history_enabled
        self._enhancer.history_enabled = new_value
        sender.state = 1 if new_value else 0

        # Persist to config
        self._config.setdefault("ai_enhance", {})
        self._config["ai_enhance"].setdefault("conversation_history", {})
        self._config["ai_enhance"]["conversation_history"]["enabled"] = new_value
        save_config(self._config, self._config_path)
        logger.info("Conversation history set to: %s", new_value)

    def _on_vocab_build(self, _sender) -> None:
        """Build vocabulary from correction logs in a background thread."""
        if not self._enhancer:
            self._topmost_alert("AI Enhance is not configured.")
            return

        if self._auto_vocab_builder.is_building():
            self._topmost_alert("Vocabulary is being auto-built. Please wait.")
            return

        logger.info("Starting vocabulary build...")

        cancel_event = threading.Event()

        from .vocab_build_window import VocabBuildProgressPanel

        # Build enhance info string for the progress panel
        enhance_info = ""
        if self._enhancer:
            parts = []
            if self._enhancer.provider_name:
                parts.append(self._enhancer.provider_name)
            if self._enhancer.model_name:
                parts.append(self._enhancer.model_name)
            enhance_info = " / ".join(parts)

        progress_panel = VocabBuildProgressPanel()
        # _on_vocab_build runs on the main thread (rumps callback), so show directly
        progress_panel.show(
            on_cancel=lambda: cancel_event.set(),
            enhance_info=enhance_info,
        )

        def _build():
            import asyncio as _asyncio

            from .vocabulary_builder import BuildCallbacks, VocabularyBuilder

            ai_cfg = self._config.get("ai_enhance", {})
            logger.info("VocabularyBuilder initializing...")
            builder = VocabularyBuilder(ai_cfg)

            callbacks = BuildCallbacks(
                on_batch_start=lambda i, t: (
                    progress_panel.clear_stream_text(),
                    progress_panel.update_status(f"Batch {i}/{t} — extracting..."),
                ),
                on_stream_chunk=lambda chunk: progress_panel.append_stream_text(chunk),
                on_batch_done=lambda i, t, c: progress_panel.update_status(
                    f"Batch {i}/{t} done — {c} entries found"
                ),
                on_usage_update=lambda p, c, t: progress_panel.update_token_usage(p, c, t),
            )

            old_title = self.title
            self.title = "VT ⏳"
            try:
                loop = _asyncio.new_event_loop()
                summary = loop.run_until_complete(
                    builder.build(cancel_event=cancel_event, callbacks=callbacks)
                )
                # Shut down async generators before closing the loop to avoid
                # "Task was destroyed but it is pending" warnings from streams
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

                # Reload vocabulary index if enhancer has one
                if self._enhancer and self._enhancer.vocab_index is not None:
                    self._enhancer.vocab_index.reload()
                self._update_vocab_title()

                cancelled = summary.get("cancelled", False)
                status = "Cancelled" if cancelled else "Built"
                msg = (
                    f"{summary['total_entries']} entries "
                    f"({summary['new_entries']} new)"
                )
                progress_panel.update_status(f"{status}: {msg}")
                try:
                    rumps.notification("VoiceText", f"Vocabulary {status}", msg)
                except Exception:
                    logger.debug("Notification center unavailable, skipping notification")
            except Exception as e:
                logger.error("Vocabulary build failed: %s", e)
                progress_panel.update_status(f"Failed: {e}")
                try:
                    rumps.notification(
                        "VoiceText", "Vocabulary Build Failed", str(e)
                    )
                except Exception:
                    logger.debug("Notification center unavailable, skipping notification")
            finally:
                self.title = old_title
                progress_panel.close()

        t = threading.Thread(target=_build, daemon=True)
        t.start()

    def _build_llm_model_menu(self) -> None:
        """Build or rebuild the LLM Model top-level submenu."""
        if self._llm_model_menu._menu is not None:
            self._llm_model_menu.clear()
        self._llm_model_menu_items.clear()

        if not self._enhancer:
            return

        providers = self._enhancer.providers_with_models
        current_key = (self._enhancer.provider_name, self._enhancer.model_name)
        first_provider = True

        for pname, models in providers.items():
            if not first_provider:
                self._llm_model_menu.add(None)  # separator
            first_provider = False

            for mname in models:
                key = (pname, mname)
                title = f"{pname} / {mname}"
                item = rumps.MenuItem(title)
                item._llm_provider = pname
                item._llm_model = mname
                item.set_callback(self._on_llm_model_select)
                if key == current_key:
                    item.state = 1
                self._llm_model_menu_items[key] = item
                self._llm_model_menu.add(item)

        # Management items
        self._llm_model_menu.add(None)  # separator
        self._llm_model_menu.add(self._llm_add_provider_item)

        # Rebuild remove submenu
        if self._llm_remove_provider_menu._menu is not None:
            self._llm_remove_provider_menu.clear()
        self._llm_remove_provider_items.clear()

        for pname in providers:
            item = rumps.MenuItem(pname)
            item._provider_name = pname
            item.set_callback(self._on_enhance_remove_provider)
            self._llm_remove_provider_items[pname] = item
            self._llm_remove_provider_menu.add(item)

        if providers:
            self._llm_model_menu.add(self._llm_remove_provider_menu)

    # ── Remote ASR provider management ────────────────────────────────

    def _build_model_menu(self) -> None:
        """Build or rebuild the entire STT Model submenu."""
        if self._model_menu._menu is not None:
            self._model_menu.clear()
        self._model_menu_items.clear()
        self._remote_asr_menu_items.clear()

        # Local presets
        for preset in PRESETS:
            backend_ok = is_backend_available(preset.backend)
            if backend_ok:
                title = preset.display_name
            else:
                title = f"{preset.display_name} (N/A)"
            item = rumps.MenuItem(title)
            item._preset_id = preset.id
            if backend_ok:
                item.set_callback(self._on_model_select)
            else:
                item.set_callback(None)
            if preset.id == self._current_preset_id:
                item.state = 1
            self._model_menu_items[preset.id] = item
            self._model_menu.add(item)

        # Remote ASR models
        asr_cfg = self._config.get("asr", {})
        providers = asr_cfg.get("providers", {})
        remote_models = build_remote_asr_models(providers)

        if remote_models:
            self._model_menu.add(None)  # separator
            for rm in remote_models:
                key = (rm.provider, rm.model)
                item = rumps.MenuItem(rm.display_name)
                item._remote_asr = rm
                item.set_callback(self._on_remote_asr_select)
                if key == self._current_remote_asr:
                    item.state = 1
                self._remote_asr_menu_items[key] = item
                self._model_menu.add(item)

        # Management items
        self._model_menu.add(None)  # separator
        self._model_menu.add(self._asr_add_provider_item)

        # Rebuild remove submenu
        if self._asr_remove_provider_menu._menu is not None:
            self._asr_remove_provider_menu.clear()
        self._asr_remove_provider_items.clear()
        for pname in providers:
            item = rumps.MenuItem(pname)
            item._provider_name = pname
            item.set_callback(self._on_asr_remove_provider)
            self._asr_remove_provider_items[pname] = item
            self._asr_remove_provider_menu.add(item)

        if providers:
            self._model_menu.add(self._asr_remove_provider_menu)

    def _on_remote_asr_select(self, sender) -> None:
        """Handle remote ASR model menu item click."""
        rm: RemoteASRModel = sender._remote_asr
        key = (rm.provider, rm.model)

        if key == self._current_remote_asr:
            return

        if self._busy:
            rumps.notification(
                "VoiceText",
                "Cannot switch model",
                "Please wait for current operation to finish.",
            )
            return

        self._busy = True
        old_transcriber = self._transcriber

        def _do_switch():
            try:
                self._set_status("Switching...")
                old_transcriber.cleanup()

                asr_cfg = self._config["asr"]
                new_transcriber = create_transcriber(
                    backend="whisper-api",
                    base_url=rm.base_url,
                    api_key=rm.api_key,
                    model=rm.model,
                    language=asr_cfg.get("language"),
                    temperature=asr_cfg.get("temperature"),
                )
                new_transcriber.initialize()

                self._transcriber = new_transcriber
                self._current_remote_asr = key
                self._current_preset_id = None
                self._update_model_checkmarks()

                # Persist to config
                self._config["asr"]["default_provider"] = rm.provider
                self._config["asr"]["default_model"] = rm.model
                save_config(self._config, self._config_path)

                self._set_status("VT")
                logger.info("Switched to remote ASR: %s", rm.display_name)
                try:
                    rumps.notification(
                        "VoiceText",
                        "Model switched",
                        f"Now using: {rm.display_name}",
                    )
                except Exception:
                    logger.debug("Notification unavailable, skipping")

            except Exception as e:
                logger.error("Remote ASR switch failed: %s", e)
                self._set_status("Error")
                try:
                    rumps.notification(
                        "VoiceText",
                        "Model switch failed",
                        str(e)[:100],
                    )
                except Exception:
                    logger.debug("Notification unavailable, skipping")
            finally:
                self._busy = False

        threading.Thread(target=_do_switch, daemon=True).start()

    _ADD_ASR_PROVIDER_TEMPLATE = """\
name: my-provider
base_url: https://api.groq.com/openai/v1
api_key: gsk-xxx
models:
  whisper-large-v3-turbo"""

    _ASR_PROVIDER_DRAFT_FILENAME = ".asr_provider_draft"

    def _get_asr_provider_draft_path(self) -> str:
        from .config import DEFAULT_CONFIG_DIR
        config_dir = self._config_path or DEFAULT_CONFIG_DIR
        parent = os.path.dirname(os.path.expanduser(config_dir))
        return os.path.join(parent, self._ASR_PROVIDER_DRAFT_FILENAME)

    def _load_asr_provider_draft(self) -> str:
        draft_path = self._get_asr_provider_draft_path()
        try:
            with open(draft_path, "r", encoding="utf-8") as f:
                content = f.read()
            if content.strip():
                return content
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug("Could not read ASR provider draft: %s", e)
        return self._ADD_ASR_PROVIDER_TEMPLATE

    def _save_asr_provider_draft(self, text: str) -> None:
        draft_path = self._get_asr_provider_draft_path()
        try:
            os.makedirs(os.path.dirname(draft_path), exist_ok=True)
            with open(draft_path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            logger.debug("Could not save ASR provider draft: %s", e)

    def _remove_asr_provider_draft(self) -> None:
        draft_path = self._get_asr_provider_draft_path()
        try:
            os.remove(draft_path)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug("Could not remove ASR provider draft: %s", e)

    def _on_asr_add_provider(self, _) -> None:
        """Add a new ASR provider via multi-step dialog."""
        def _run():
            try:
                self._do_add_asr_provider()
            except Exception as e:
                logger.error("Add ASR provider failed: %s", e, exc_info=True)
            finally:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(self._restore_accessory)

        threading.Thread(target=_run, daemon=True).start()

    def _do_add_asr_provider(self) -> None:
        """Internal implementation for adding an ASR provider."""
        template = self._load_asr_provider_draft()
        while True:
            resp = self._run_multiline_window(
                title="Add ASR Provider",
                message=(
                    "name       — unique provider name\n"
                    "base_url   — OpenAI-compatible ASR endpoint\n"
                    "api_key    — authentication key\n"
                    "models     — one model per line (indented)"
                ),
                default_text=template,
                ok="Verify",
                dimensions=(380, 140),
            )
            if resp is None:
                self._save_asr_provider_draft(template)
                return

            parsed = self._parse_asr_provider_text(resp.text)
            if isinstance(parsed, str):
                self._activate_for_dialog()
                self._topmost_alert("Validation Error", parsed)
                template = resp.text
                self._save_asr_provider_draft(resp.text)
                continue

            name, base_url, api_key, models = parsed

            # Check for duplicate
            providers = self._config.get("asr", {}).get("providers", {})
            if name in providers:
                self._activate_for_dialog()
                self._topmost_alert("Error", f"ASR provider '{name}' already exists.")
                template = resp.text
                self._save_asr_provider_draft(resp.text)
                continue

            # Verify connection
            self._activate_for_dialog()
            self._topmost_alert(
                "Verifying...",
                f"Testing connection to {base_url}\nModel: {models[0]}",
            )

            from .transcriber_whisper_api import WhisperAPITranscriber

            err = WhisperAPITranscriber.verify_provider(base_url, api_key, models[0])

            if err:
                self._activate_for_dialog()
                result = self._topmost_alert(
                    title="Verification Failed",
                    message=f"{err}\n\nEdit and retry?",
                    ok="Edit",
                    cancel="Cancel",
                )
                if result != 1:
                    self._save_asr_provider_draft(resp.text)
                    return
                template = resp.text
                self._save_asr_provider_draft(resp.text)
                continue

            # Verify passed — ask to save
            self._activate_for_dialog()
            result = self._topmost_alert(
                title="Verification Passed",
                message=(
                    f"Provider: {name}\n"
                    f"URL: {base_url}\n"
                    f"Models: {', '.join(models)}\n\n"
                    "Save this provider?"
                ),
                ok="Save",
                cancel="Cancel",
            )
            if result != 1:
                self._save_asr_provider_draft(resp.text)
                return

            # Save to config
            self._config.setdefault("asr", {})
            providers_cfg = self._config["asr"].setdefault("providers", {})
            providers_cfg[name] = {
                "base_url": base_url,
                "api_key": api_key,
                "models": models,
            }
            save_config(self._config, self._config_path)
            self._remove_asr_provider_draft()

            self._build_model_menu()

            rumps.notification(
                "VoiceText", "ASR Provider added", f"{name} ({', '.join(models)})"
            )
            logger.info("Added ASR provider: %s", name)
            return

    @staticmethod
    def _parse_asr_provider_text(text: str):
        """Parse ASR provider config text.

        Returns (name, base_url, api_key, models) on success,
        or a string error message on failure.
        """
        lines = text.strip().splitlines()
        fields = {}
        in_models = False
        models = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("models:"):
                in_models = True
                inline = stripped[len("models:"):].strip()
                if inline:
                    models.append(inline)
                continue
            if in_models:
                is_indented = line.startswith(" ") or line.startswith("\t")
                if not is_indented and ":" in stripped:
                    in_models = False
                else:
                    models.append(stripped)
                    continue
            if ":" in stripped:
                key, _, val = stripped.partition(":")
                fields[key.strip().lower()] = val.strip()

        name = fields.get("name", "").strip()
        base_url = fields.get("base_url", "").strip()
        api_key = fields.get("api_key", "").strip()

        errors = []
        if not name:
            errors.append("name is required")
        if not base_url:
            errors.append("base_url is required")
        if not api_key:
            errors.append("api_key is required")
        if not models:
            errors.append("at least one model is required")

        if errors:
            return "\n".join(errors)

        return name, base_url, api_key, models

    def _on_asr_remove_provider(self, sender) -> None:
        """Remove an ASR provider after confirmation."""
        try:
            pname = sender._provider_name

            self._activate_for_dialog()
            result = self._topmost_alert(
                title="Remove ASR Provider",
                message=f"Remove ASR provider '{pname}' and all its models?",
                ok="Remove",
                cancel="Cancel",
            )
            if result != 1:
                return

            # If currently using a model from this provider, switch to default
            if self._current_remote_asr and self._current_remote_asr[0] == pname:
                self._transcriber.cleanup()
                asr_cfg = self._config["asr"]
                self._transcriber = create_transcriber(
                    backend=asr_cfg.get("backend", "funasr"),
                    use_vad=asr_cfg.get("use_vad", True),
                    use_punc=asr_cfg.get("use_punc", True),
                    language=asr_cfg.get("language"),
                    model=asr_cfg.get("model"),
                    temperature=asr_cfg.get("temperature"),
                )
                self._current_remote_asr = None
                self._current_preset_id = resolve_preset_from_config(
                    asr_cfg.get("backend", "funasr"),
                    asr_cfg.get("model"),
                )
                self._config["asr"]["default_provider"] = None
                self._config["asr"]["default_model"] = None

            # Remove from config
            providers_cfg = self._config.get("asr", {}).get("providers", {})
            providers_cfg.pop(pname, None)
            save_config(self._config, self._config_path)

            self._build_model_menu()
            self._update_model_checkmarks()

            rumps.notification("VoiceText", "ASR Provider removed", pname)
            logger.info("Removed ASR provider: %s", pname)
        except Exception as e:
            logger.error("Remove ASR provider failed: %s", e, exc_info=True)
        finally:
            self._restore_accessory()

    @staticmethod
    def _migrate_asr_config(asr_cfg: Dict[str, Any]) -> None:
        """Migrate old flat base_url/api_key to provider format."""
        base_url = asr_cfg.pop("base_url", None)
        api_key = asr_cfg.pop("api_key", None)

        if not base_url or not api_key:
            return

        providers = asr_cfg.setdefault("providers", {})
        if providers:
            # Already has providers, don't overwrite
            return

        # Infer provider name from URL
        if "groq.com" in base_url:
            name = "groq"
            models = ["whisper-large-v3-turbo"]
        else:
            name = "migrated"
            model = asr_cfg.get("model") or "whisper-large-v3-turbo"
            models = [model]

        providers[name] = {
            "base_url": base_url,
            "api_key": api_key,
            "models": models,
        }

        # If the current backend was whisper-api, set as default
        if asr_cfg.get("backend") == "whisper-api":
            asr_cfg["default_provider"] = name
            asr_cfg["default_model"] = models[0]

        logger.info("Migrated ASR config: base_url/api_key → provider '%s'", name)

    @staticmethod
    def _activate_for_dialog():
        """Set activation policy so modal dialogs can show from non-bundled process.

        Safe to call from any thread.
        """
        def _do():
            from AppKit import NSApp
            NSApp.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular
            NSApp.activateIgnoringOtherApps_(True)

        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_do)

    @staticmethod
    def _restore_accessory():
        """Restore accessory activation policy (statusbar-only).

        Safe to call from any thread.
        """
        def _do():
            from AppKit import NSApp
            NSApp.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_do)

    @staticmethod
    def _topmost_alert(title=None, message="", ok=None, cancel=None):
        """Show an NSAlert at NSStatusWindowLevel so it stays on top.

        Safe to call from any thread — dispatches to main thread if needed.
        """
        from PyObjCTools import AppHelper

        result_holder = {"value": 0}
        done_event = threading.Event()

        def _show():
            from AppKit import NSAlert, NSStatusWindowLevel

            VoiceTextApp._activate_for_dialog()

            alert = NSAlert.alloc().init()
            if title is not None:
                alert.setMessageText_(str(title))
            if message:
                alert.setInformativeText_(str(message))
            alert.addButtonWithTitle_(ok or "OK")
            if cancel:
                cancel_text = cancel if isinstance(cancel, str) else "Cancel"
                alert.addButtonWithTitle_(cancel_text)
            alert.setAlertStyle_(0)  # informational
            alert.window().setLevel_(NSStatusWindowLevel)
            alert.window().setFloatingPanel_(True)
            alert.window().setHidesOnDeactivate_(False)

            # NSAlertFirstButtonReturn = 1000, NSAlertSecondButtonReturn = 1001
            result = alert.runModal()
            result_holder["value"] = 1 if result == 1000 else 0
            done_event.set()

        if threading.current_thread() is threading.main_thread():
            _show()
        else:
            AppHelper.callAfter(_show)
            done_event.wait()

        return result_holder["value"]

    @staticmethod
    def _run_window(title: str, message: str, default_text: str = "",
                    ok: str = "OK", cancel: str = "Cancel",
                    dimensions: tuple = (320, 22), secure: bool = False):
        """Run a rumps.Window with proper app activation.

        Safe to call from any thread — dispatches to main thread if needed.
        Returns Response or None on cancel.
        """
        from PyObjCTools import AppHelper

        result_holder = {"resp": None}
        done_event = threading.Event()

        def _show():
            from AppKit import NSStatusWindowLevel

            VoiceTextApp._activate_for_dialog()
            w = rumps.Window(
                title=title, message=message, default_text=default_text,
                ok=ok, cancel=cancel, dimensions=dimensions, secure=secure,
            )
            w._alert.window().setLevel_(NSStatusWindowLevel)
            w._alert.window().setFloatingPanel_(True)
            w._alert.window().setHidesOnDeactivate_(False)
            resp = w.run()
            result_holder["resp"] = resp if resp.clicked == 1 else None
            done_event.set()

        if threading.current_thread() is threading.main_thread():
            _show()
        else:
            AppHelper.callAfter(_show)
            done_event.wait()

        return result_holder["resp"]

    @staticmethod
    def _run_multiline_window(title: str, message: str, default_text: str = "",
                              ok: str = "OK", cancel: str = "Cancel",
                              dimensions: tuple = (380, 180)):
        """Show a floating NSPanel with a multiline NSTextView (Enter = newline).

        Must be called from a background thread.  The panel is created on the
        main thread via ``callAfter`` and the caller blocks on a
        ``threading.Event`` — the same pattern used by ResultPreviewPanel so
        that ``setFloatingPanel_(True)`` reliably keeps the window on top.

        Returns a Response-like object with .clicked and .text, or None on cancel.
        """
        from PyObjCTools import AppHelper

        result_holder = {"clicked": 0, "text": ""}
        done_event = threading.Event()

        # Store panel ref at method level to prevent garbage collection
        panel_holder = [None]

        def _show():
            try:
                from AppKit import (
                    NSApp, NSBackingStoreBuffered, NSBezelBorder, NSButton,
                    NSClosableWindowMask, NSFont, NSPanel, NSScrollView,
                    NSStatusWindowLevel, NSTextField, NSTextView, NSTitledWindowMask,
                )
                from Foundation import NSMakeRect

                padding = 12
                btn_h = 32
                btn_w = 90
                line_count = max(message.count("\n") + 1, 1)
                label_h = 16 * line_count
                width, height = dimensions
                panel_w = width + 2 * padding
                panel_h = padding + btn_h + padding + height + padding + label_h + padding

                panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                    NSMakeRect(0, 0, panel_w, panel_h),
                    NSTitledWindowMask | NSClosableWindowMask,
                    NSBackingStoreBuffered,
                    False,
                )
                panel.setTitle_(title)
                panel.setLevel_(NSStatusWindowLevel)
                panel.setFloatingPanel_(True)
                panel.setHidesOnDeactivate_(False)
                panel.center()

                content = panel.contentView()
                y = padding

                # -- helper to close panel and signal the waiting thread ------
                text_view_holder = [None]

                def _finish(clicked):
                    tv = text_view_holder[0]
                    text_val = tv.string() if tv is not None else ""
                    result_holder["clicked"] = clicked
                    result_holder["text"] = text_val
                    panel.setDelegate_(None)
                    panel.orderOut_(None)
                    VoiceTextApp._restore_accessory()
                    done_event.set()

                # Action target for OK / Cancel / Close
                target_cls = _get_multiline_panel_target_class()
                btn_target = target_cls.alloc().init()
                btn_target._finish_callback = _finish

                # Buttons row (right-aligned)
                cancel_btn = NSButton.alloc().initWithFrame_(
                    NSMakeRect(panel_w - padding - btn_w, y, btn_w, btn_h)
                )
                cancel_btn.setTitle_(cancel)
                cancel_btn.setBezelStyle_(1)
                cancel_btn.setKeyEquivalent_("\x1b")  # ESC
                cancel_btn.setTarget_(btn_target)
                cancel_btn.setAction_(b"cancelClicked:")
                content.addSubview_(cancel_btn)

                ok_btn = NSButton.alloc().initWithFrame_(
                    NSMakeRect(panel_w - padding - 2 * btn_w - 8, y, btn_w, btn_h)
                )
                ok_btn.setTitle_(ok)
                ok_btn.setBezelStyle_(1)
                ok_btn.setKeyEquivalent_("")
                ok_btn.setTarget_(btn_target)
                ok_btn.setAction_(b"okClicked:")
                content.addSubview_(ok_btn)

                y += btn_h + padding

                # Multiline text view
                scroll_view = NSScrollView.alloc().initWithFrame_(
                    NSMakeRect(padding, y, width, height)
                )
                scroll_view.setHasVerticalScroller_(True)
                scroll_view.setBorderType_(NSBezelBorder)

                text_view = NSTextView.alloc().initWithFrame_(
                    NSMakeRect(0, 0, width, height)
                )
                text_view.setMinSize_(NSMakeRect(0, 0, width, 0).size)
                text_view.setMaxSize_(NSMakeRect(0, 0, 1e7, 1e7).size)
                text_view.setVerticallyResizable_(True)
                text_view.setHorizontallyResizable_(False)
                text_view.textContainer().setWidthTracksTextView_(True)
                text_view.setFont_(NSFont.userFixedPitchFontOfSize_(12.0))
                text_view.setString_(default_text)
                scroll_view.setDocumentView_(text_view)
                content.addSubview_(scroll_view)
                text_view_holder[0] = text_view

                y += height + padding

                # Message label
                msg_label = NSTextField.labelWithString_(message)
                msg_label.setFrame_(NSMakeRect(padding, y, width, label_h))
                msg_label.setFont_(NSFont.systemFontOfSize_(12))
                content.addSubview_(msg_label)

                # Handle close button (X) as cancel
                panel.setDelegate_(btn_target)

                # Keep refs alive until panel is dismissed
                panel_holder[0] = (panel, btn_target)

                VoiceTextApp._activate_for_dialog()
                panel.makeKeyAndOrderFront_(None)
                panel.makeFirstResponder_(text_view)
                NSApp.activateIgnoringOtherApps_(True)
            except Exception as e:
                logger.error("_run_multiline_window _show failed: %s", e, exc_info=True)
                done_event.set()

        AppHelper.callAfter(_show)
        done_event.wait()

        if result_holder["clicked"] != 1:
            return None

        class _Response:
            pass

        resp = _Response()
        resp.clicked = 1
        resp.text = result_holder["text"]
        return resp

    def _on_enhance_add_provider(self, _) -> None:
        """Add a new AI provider via multi-step dialog."""
        def _run():
            try:
                self._do_add_provider()
            except Exception as e:
                logger.error("Add provider failed: %s", e, exc_info=True)
            finally:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(self._restore_accessory)

        threading.Thread(target=_run, daemon=True).start()

    _ADD_PROVIDER_TEMPLATE = """\
name: my-provider
base_url: https://api.openai.com/v1
api_key: sk-xxx
models:
  gpt-4o
  gpt-4o-mini"""

    _PROVIDER_DRAFT_FILENAME = ".provider_draft"

    def _get_provider_draft_path(self) -> str:
        """Return the path to the provider draft cache file."""
        from .config import DEFAULT_CONFIG_DIR
        config_dir = self._config_path or DEFAULT_CONFIG_DIR
        parent = os.path.dirname(os.path.expanduser(config_dir))
        return os.path.join(parent, self._PROVIDER_DRAFT_FILENAME)

    def _load_provider_draft(self) -> str:
        """Load cached draft text, or return the default template."""
        draft_path = self._get_provider_draft_path()
        try:
            with open(draft_path, "r", encoding="utf-8") as f:
                content = f.read()
            if content.strip():
                return content
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug("Could not read provider draft: %s", e)
        return self._ADD_PROVIDER_TEMPLATE

    def _save_provider_draft(self, text: str) -> None:
        """Cache the user's draft text for next time."""
        draft_path = self._get_provider_draft_path()
        try:
            os.makedirs(os.path.dirname(draft_path), exist_ok=True)
            with open(draft_path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            logger.debug("Could not save provider draft: %s", e)

    def _remove_provider_draft(self) -> None:
        """Remove the draft cache file after a successful save."""
        draft_path = self._get_provider_draft_path()
        try:
            os.remove(draft_path)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug("Could not remove provider draft: %s", e)

    def _do_add_provider(self) -> None:
        """Internal implementation for adding a provider."""
        if not self._enhancer:
            self._activate_for_dialog()
            self._topmost_alert("Error", "AI enhancer is not initialized.")
            return

        template = self._load_provider_draft()
        while True:
            resp = self._run_multiline_window(
                title="Add AI Provider",
                message=(
                    "name       — unique provider name\n"
                    "base_url   — OpenAI-compatible API endpoint\n"
                    "api_key    — authentication key\n"
                    "models     — one model per line (indented)\n"
                    "extra_body — optional, extra JSON for request body"
                ),
                default_text=template,
                ok="Verify",
                dimensions=(380, 180),
            )
            if resp is None:
                # User cancelled — cache their input for next time
                self._save_provider_draft(template)
                return

            parsed = self._parse_provider_text(resp.text)
            if isinstance(parsed, str):
                # Validation error
                self._activate_for_dialog()
                self._topmost_alert("Validation Error", parsed)
                template = resp.text
                self._save_provider_draft(resp.text)
                continue

            name, base_url, api_key, models, extra_body = parsed

            if name in self._enhancer.provider_names:
                self._activate_for_dialog()
                self._topmost_alert("Error", f"Provider '{name}' already exists.")
                template = resp.text
                self._save_provider_draft(resp.text)
                continue

            # Verify connection
            self._activate_for_dialog()
            self._topmost_alert("Verifying...", f"Testing connection to {base_url}\nModel: {models[0]}")

            import asyncio
            loop = asyncio.new_event_loop()
            try:
                err = loop.run_until_complete(
                    self._enhancer.verify_provider(
                        base_url, api_key, models[0], extra_body=extra_body or None
                    )
                )
            finally:
                loop.close()

            if err:
                self._activate_for_dialog()
                result = self._topmost_alert(
                    title="Verification Failed",
                    message=f"{err}\n\nEdit and retry?",
                    ok="Edit",
                    cancel="Cancel",
                )
                if result != 1:
                    self._save_provider_draft(resp.text)
                    return
                template = resp.text
                self._save_provider_draft(resp.text)
                continue

            # Verify passed — ask to save
            self._activate_for_dialog()
            result = self._topmost_alert(
                title="Verification Passed",
                message=(
                    f"Provider: {name}\n"
                    f"URL: {base_url}\n"
                    f"Models: {', '.join(models)}\n\n"
                    "Save this provider?"
                ),
                ok="Save",
                cancel="Cancel",
            )
            if result != 1:
                self._save_provider_draft(resp.text)
                return

            # Save
            success = self._enhancer.add_provider(
                name, base_url, api_key, models, extra_body=extra_body or None
            )
            if not success:
                self._activate_for_dialog()
                self._topmost_alert(
                    "Error",
                    "Failed to initialize provider. "
                    "Check that the openai package is installed.",
                )
                return

            self._config.setdefault("ai_enhance", {})
            providers_cfg = self._config["ai_enhance"].setdefault("providers", {})
            pcfg_save: Dict[str, Any] = {
                "base_url": base_url,
                "api_key": api_key,
                "models": models,
            }
            if extra_body:
                pcfg_save["extra_body"] = extra_body
            providers_cfg[name] = pcfg_save
            save_config(self._config, self._config_path)
            self._remove_provider_draft()

            self._build_llm_model_menu()

            rumps.notification(
                "VoiceText", "Provider added", f"{name} ({', '.join(models)})"
            )
            logger.info("Added AI provider: %s", name)
            return

    @staticmethod
    def _parse_provider_text(text: str):
        """Parse the provider config text.

        Returns (name, base_url, api_key, models, extra_body) on success,
        or a string error message on failure.
        """
        import json as _json

        lines = text.strip().splitlines()
        fields = {}
        in_models = False
        models = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("models:"):
                in_models = True
                # Handle inline value: "models: gpt-4o"
                inline = stripped[len("models:"):].strip()
                if inline:
                    models.append(inline)
                continue
            if in_models:
                # A non-indented line with "key:" pattern ends the models section
                is_indented = line.startswith(" ") or line.startswith("\t")
                if not is_indented and ":" in stripped:
                    in_models = False
                else:
                    models.append(stripped)
                    continue
            if ":" in stripped:
                key, _, val = stripped.partition(":")
                fields[key.strip().lower()] = val.strip()

        name = fields.get("name", "").strip()
        base_url = fields.get("base_url", "").strip()
        api_key = fields.get("api_key", "").strip()
        extra_body_raw = fields.get("extra_body", "").strip()

        extra_body = {}
        if extra_body_raw:
            try:
                extra_body = _json.loads(extra_body_raw)
                if not isinstance(extra_body, dict):
                    return "extra_body must be a JSON object"
            except _json.JSONDecodeError as e:
                return f"extra_body is not valid JSON: {e}"

        errors = []
        if not name:
            errors.append("name is required")
        if not base_url:
            errors.append("base_url is required")
        if not api_key:
            errors.append("api_key is required")
        if not models:
            errors.append("at least one model is required")

        if errors:
            return "\n".join(errors)

        return name, base_url, api_key, models, extra_body

    def _on_enhance_remove_provider(self, sender) -> None:
        """Remove an AI provider after confirmation."""
        try:
            pname = sender._provider_name
            if not self._enhancer:
                return

            self._activate_for_dialog()

            result = self._topmost_alert(
                title="Remove Provider",
                message=f"Remove provider '{pname}' and all its models?",
                ok="Remove",
                cancel="Cancel",
            )
            if result != 1:
                return

            self._enhancer.remove_provider(pname)

            # Persist to config
            self._config.setdefault("ai_enhance", {})
            providers_cfg = self._config["ai_enhance"].get("providers", {})
            providers_cfg.pop(pname, None)
            self._config["ai_enhance"]["default_provider"] = self._enhancer.provider_name
            self._config["ai_enhance"]["default_model"] = self._enhancer.model_name
            save_config(self._config, self._config_path)

            # Rebuild LLM model menu
            self._build_llm_model_menu()

            rumps.notification("VoiceText", "Provider removed", pname)
            logger.info("Removed AI provider: %s", pname)
        except Exception as e:
            logger.error("Remove provider failed: %s", e, exc_info=True)
        finally:
            self._restore_accessory()

    def _on_preview_toggle(self, sender) -> None:
        """Toggle preview window on/off."""
        self._preview_enabled = not self._preview_enabled
        sender.state = 1 if self._preview_enabled else 0

        self._config["output"]["preview"] = self._preview_enabled
        save_config(self._config, self._config_path)
        logger.info("Preview set to: %s", self._preview_enabled)

    def _on_enhance_edit_config(self, _) -> None:
        """Open the config file in the default editor."""
        try:
            from .config import DEFAULT_CONFIG_PATH

            config_path = self._config_path or DEFAULT_CONFIG_PATH
            expanded = os.path.expanduser(config_path)
            subprocess.Popen(["open", expanded])
        except Exception as e:
            logger.error("Failed to open config file: %s", e, exc_info=True)

    def _on_copy_log_path(self, _) -> None:
        """Copy the log file path to clipboard."""
        path_str = str(LOG_FILE)
        subprocess.run(["pbcopy"], input=path_str.encode(), check=True)
        rumps.notification("VoiceText", "Log path copied", path_str)

    def _on_debug_level_select(self, sender) -> None:
        """Handle debug log level selection."""
        level_name = sender._log_level
        log_level = getattr(logging, level_name, logging.INFO)

        # Update all loggers
        logging.getLogger().setLevel(log_level)
        for handler in logging.getLogger().handlers:
            handler.setLevel(log_level)

        # Update checkmarks
        for item in self._debug_level_items.values():
            item.state = 0
        sender.state = 1

        # Persist to config
        self._config["logging"]["level"] = level_name
        save_config(self._config, self._config_path)
        logger.info("Log level changed to: %s", level_name)

    def _on_debug_print_prompt_toggle(self, sender) -> None:
        """Toggle printing prompts to log."""
        sender.state = not sender.state
        if self._enhancer:
            self._enhancer.debug_print_prompt = bool(sender.state)
        logger.info("Debug print prompt: %s", bool(sender.state))

    def _on_debug_print_request_body_toggle(self, sender) -> None:
        """Toggle printing AI request body to log."""
        sender.state = not sender.state
        if self._enhancer:
            self._enhancer.debug_print_request_body = bool(sender.state)
        logger.info("Debug print request body: %s", bool(sender.state))

    def _on_model_select(self, sender) -> None:
        """Handle local model menu item click."""
        preset_id = sender._preset_id

        # Ignore if already active
        if preset_id == self._current_preset_id and not self._current_remote_asr:
            return

        # Reject if busy (transcribing or switching)
        if self._busy:
            rumps.notification(
                "VoiceText",
                "Cannot switch model",
                "Please wait for current operation to finish.",
            )
            return

        preset = PRESET_BY_ID[preset_id]
        self._busy = True

        # Disable all model menu callbacks during switch
        for item in self._model_menu_items.values():
            item.set_callback(None)
        for item in self._remote_asr_menu_items.values():
            item.set_callback(None)

        old_preset_id = self._current_preset_id
        old_transcriber = self._transcriber

        def _do_switch():
            stop_event = threading.Event()
            monitor_thread = None

            try:
                # Cleanup current model
                self._set_status("Unloading...")
                old_transcriber.cleanup()

                # Check if model is cached; if not, start download monitor
                cached = is_model_cached(preset)
                if not cached:
                    monitor_thread = threading.Thread(
                        target=self._monitor_download_progress,
                        args=(preset, stop_event),
                        daemon=True,
                    )
                    monitor_thread.start()
                else:
                    self._set_status("Loading...")

                # Create and initialize new transcriber
                asr_cfg = self._config["asr"]
                new_transcriber = create_transcriber(
                    backend=preset.backend,
                    use_vad=asr_cfg.get("use_vad", True),
                    use_punc=asr_cfg.get("use_punc", True),
                    language=preset.language or asr_cfg.get("language"),
                    model=preset.model,
                    temperature=asr_cfg.get("temperature"),
                )
                new_transcriber.initialize()

                # Stop download monitor
                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)

                # Success: update state
                self._transcriber = new_transcriber
                self._current_preset_id = preset_id
                self._current_remote_asr = None
                self._update_model_checkmarks()

                # Persist to config
                self._config["asr"]["preset"] = preset_id
                self._config["asr"]["backend"] = preset.backend
                self._config["asr"]["model"] = preset.model
                self._config["asr"]["language"] = preset.language
                self._config["asr"]["default_provider"] = None
                self._config["asr"]["default_model"] = None
                save_config(self._config, self._config_path)

                self._set_status("VT")
                logger.info("Switched to model: %s", preset.display_name)
                try:
                    rumps.notification(
                        "VoiceText",
                        "Model switched",
                        f"Now using: {preset.display_name}",
                    )
                except Exception:
                    logger.debug("Notification unavailable, skipping")

            except Exception as e:
                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)

                logger.error("Model switch failed: %s", e)
                self._set_status("Error")
                try:
                    rumps.notification(
                        "VoiceText",
                        "Model switch failed",
                        str(e)[:100],
                    )
                except Exception:
                    logger.debug("Notification unavailable, skipping")

                # Try to restore previous model
                self._try_restore_previous_model(old_preset_id)

            finally:
                # Re-enable model menu callbacks (only for available backends)
                for pid, item in self._model_menu_items.items():
                    p = PRESET_BY_ID[pid]
                    if is_backend_available(p.backend):
                        item.set_callback(self._on_model_select)
                for item in self._remote_asr_menu_items.values():
                    item.set_callback(self._on_remote_asr_select)
                self._busy = False

        threading.Thread(target=_do_switch, daemon=True).start()

    def _monitor_download_progress(
        self, preset: ModelPreset, stop_event: threading.Event
    ) -> None:
        """Monitor download progress by checking cache directory size."""
        expected_size = self._get_expected_model_size(preset)
        if not expected_size:
            self._set_status("Downloading...")
            stop_event.wait()
            return

        cache_dir = get_model_cache_dir(preset)

        while not stop_event.is_set():
            current_size = _get_dir_size(cache_dir)
            pct = min(int(current_size / expected_size * 100), 99)
            self._set_status(f"DL {pct}%")
            stop_event.wait(1.0)

    def _get_expected_model_size(self, preset: ModelPreset) -> Optional[int]:
        """Get expected total download size for a preset."""
        if preset.backend == "funasr":
            return _FUNASR_APPROX_SIZE

        if preset.backend == "mlx-whisper" and preset.model:
            try:
                from huggingface_hub import model_info

                info = model_info(preset.model)
                total = sum(
                    s.size for s in (info.siblings or []) if s.size is not None
                )
                return total if total > 0 else None
            except Exception:
                logger.debug("Could not get model size for %s", preset.model)
                return None

        return None

    def _update_model_checkmarks(self) -> None:
        """Update checkmark state on all model menu items (local + remote)."""
        for pid, item in self._model_menu_items.items():
            item.state = 1 if pid == self._current_preset_id else 0
        for key, item in self._remote_asr_menu_items.items():
            item.state = 1 if key == self._current_remote_asr else 0

    def _try_restore_previous_model(self, old_preset_id: Optional[str]) -> None:
        """Attempt to restore the previous model after a failed switch."""
        if not old_preset_id or old_preset_id not in PRESET_BY_ID:
            return

        old_preset = PRESET_BY_ID[old_preset_id]
        try:
            logger.info("Restoring previous model: %s", old_preset.display_name)
            self._set_status("Restoring...")
            asr_cfg = self._config["asr"]
            restored = create_transcriber(
                backend=old_preset.backend,
                use_vad=asr_cfg.get("use_vad", True),
                use_punc=asr_cfg.get("use_punc", True),
                language=old_preset.language or asr_cfg.get("language"),
                model=old_preset.model,
                temperature=asr_cfg.get("temperature"),
            )
            restored.initialize()
            self._transcriber = restored
            self._current_preset_id = old_preset_id
            self._update_model_checkmarks()
            self._set_status("VT")
            logger.info("Previous model restored")
        except Exception as e2:
            logger.error("Failed to restore previous model: %s", e2)
            self._set_status("Error")

    def _build_config_info(self) -> str:
        """Build a summary string of current configuration."""
        # ASR Model
        if self._current_remote_asr:
            pname, mname = self._current_remote_asr
            asr_model = f"{pname} / {mname} (remote)"
        else:
            preset = PRESET_BY_ID.get(self._current_preset_id)
            asr_model = preset.display_name if preset else self._current_preset_id or "N/A"

        # AI Enhance mode
        enhance_mode = self._enhance_mode if self._enhance_mode else "Off"

        _on = "\u2705"   # ✅
        _off = "\u274C"  # ❌

        # Provider / Model / Thinking
        if self._enhancer:
            provider = self._enhancer.provider_name or "N/A"
            model = self._enhancer.model_name or "N/A"
            thinking = _on if self._enhancer.thinking else _off
        else:
            provider = "N/A"
            model = "N/A"
            thinking = "N/A"

        preview = _on if self._preview_enabled else _off
        vocabulary = _on if self._enhance_vocab_item.state else _off
        history = _on if self._enhance_history_item.state else _off
        output = self._output_method
        hotkey = self._config["hotkey"]
        log_level = self._config["logging"]["level"]
        from .config import DEFAULT_CONFIG_PATH
        config_path = os.path.expanduser(self._config_path or DEFAULT_CONFIG_PATH)

        return (
            f"ASR Model:      {asr_model}\n"
            f"AI Enhance:     {enhance_mode}\n"
            f"AI Provider:    {provider}\n"
            f"AI Model:       {model}\n"
            f"Thinking:       {thinking}\n"
            f"Preview:        {preview}\n"
            f"Vocabulary:     {vocabulary}\n"
            f"History:        {history}\n"
            f"Output:         {output}\n"
            f"Hotkey:         {hotkey}\n"
            f"Log Level:      {log_level}\n"
            f"Config Path:    {config_path}"
        )

    def _on_show_config(self, _) -> None:
        """Show current configuration in a dialog."""
        from AppKit import NSAlert, NSFont, NSStatusWindowLevel, NSTextField
        from Foundation import NSMakeRect

        info = self._build_config_info()

        self._activate_for_dialog()

        alert = NSAlert.alloc().init()
        alert.setMessageText_("Current Configuration")
        alert.addButtonWithTitle_("OK")
        alert.setAlertStyle_(0)

        # Use a monospace text field as accessory to keep alignment and force width
        text_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 360, 210))
        text_field.setStringValue_(info)
        text_field.setEditable_(False)
        text_field.setBezeled_(False)
        text_field.setDrawsBackground_(False)
        text_field.setSelectable_(True)
        text_field.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12.0, 0.0))
        alert.setAccessoryView_(text_field)

        alert.window().setLevel_(NSStatusWindowLevel)
        alert.window().setFloatingPanel_(True)
        alert.window().setHidesOnDeactivate_(False)
        alert.runModal()
        self._restore_accessory()

    def _on_reload_config(self, _) -> None:
        """Reload configuration from disk and apply changes."""
        try:
            new_config = load_config(self._config_path)
        except Exception as e:
            logger.error("Failed to reload config: %s", e)
            rumps.notification("VoiceText", "Reload Failed", str(e))
            return

        self._config = new_config

        # Output settings
        self._output_method = new_config["output"]["method"]
        self._append_newline = new_config["output"]["append_newline"]
        self._preview_enabled = new_config["output"].get("preview", True)
        self._preview_item.state = 1 if self._preview_enabled else 0

        # Logging level
        level_name = new_config["logging"]["level"]
        log_level = getattr(logging, level_name, logging.INFO)
        logging.getLogger().setLevel(log_level)
        for handler in logging.getLogger().handlers:
            handler.setLevel(log_level)
        for item in self._debug_level_items.values():
            item.state = 1 if item._log_level == level_name else 0

        # AI enhance settings
        ai_cfg = new_config.get("ai_enhance", {})
        if self._enhancer:
            new_mode = ai_cfg.get("mode", "proofread")
            if not ai_cfg.get("enabled", False):
                new_mode = MODE_OFF
            self._enhance_mode = new_mode
            if new_mode == MODE_OFF:
                self._enhancer._enabled = False
            else:
                self._enhancer._enabled = True
                self._enhancer.mode = new_mode
            for m, item in self._enhance_menu_items.items():
                item.state = 1 if m == new_mode else 0

            # Thinking
            self._enhancer.thinking = ai_cfg.get("thinking", False)
            self._enhance_thinking_item.state = 1 if self._enhancer.thinking else 0

            # Vocabulary
            vocab_cfg = ai_cfg.get("vocabulary", {})
            self._enhancer.vocab_enabled = vocab_cfg.get("enabled", False)
            self._enhance_vocab_item.state = 1 if self._enhancer.vocab_enabled else 0

            # Conversation history
            hist_cfg = ai_cfg.get("conversation_history", {})
            self._enhancer.history_enabled = hist_cfg.get("enabled", False)
            self._enhance_history_item.state = 1 if self._enhancer.history_enabled else 0

            # LLM provider/model
            new_provider = ai_cfg.get("default_provider")
            new_model = ai_cfg.get("default_model")
            if new_provider and new_model:
                self._enhancer.provider_name = new_provider
                self._enhancer.model_name = new_model
                current_key = (new_provider, new_model)
                for key, item in self._llm_model_menu_items.items():
                    item.state = 1 if key == current_key else 0

        # Clipboard enhance hotkey
        clip_cfg = new_config.get("clipboard_enhance", {})
        new_clip_hotkey = clip_cfg.get("hotkey", "")
        old_clip_hotkey = ""
        if self._clipboard_hotkey_listener:
            old_clip_hotkey = self._clipboard_hotkey_listener._hotkey_str
        if new_clip_hotkey != old_clip_hotkey:
            if self._clipboard_hotkey_listener:
                self._clipboard_hotkey_listener.stop()
                self._clipboard_hotkey_listener = None
            if new_clip_hotkey:
                self._clipboard_hotkey_listener = TapHotkeyListener(
                    hotkey_str=new_clip_hotkey,
                    on_activate=self._on_clipboard_enhance,
                )
                self._clipboard_hotkey_listener.start()

        logger.info("Configuration reloaded successfully")
        rumps.notification("VoiceText", "Config Reloaded", "Configuration has been reloaded.")

    def _on_show_usage_stats(self, _) -> None:
        """Show usage statistics in a large dialog with today + cumulative stats."""
        from AppKit import NSAlert, NSFont, NSStatusWindowLevel, NSTextField
        from Foundation import NSMakeRect

        try:
            s = self._usage_stats.get_stats()
            today = self._usage_stats.get_today_stats()
        except Exception as e:
            logger.error("Failed to get usage stats: %s", e)
            self._topmost_alert("Error", f"Failed to load usage stats: {e}")
            self._restore_accessory()
            return

        def _fmt_section(label: str, data: dict) -> list[str]:
            t = data.get("totals", {})
            tk = data.get("token_usage", {})
            em = data.get("enhance_mode_usage", {})

            lines = [f"--- {label} ---"]
            lines.append(f"Transcriptions: {t.get('transcriptions', 0)}")
            lines.append(
                f"  Direct: {t.get('direct_mode', 0)}  |  "
                f"Preview: {t.get('preview_mode', 0)}"
            )
            lines.append(
                f"  Accept: {t.get('direct_accept', 0)}  |  "
                f"Modified: {t.get('user_modification', 0)}  |  "
                f"Cancel: {t.get('cancel', 0)}"
            )

            total_tk = tk.get("total_tokens", 0)
            prompt_tk = tk.get("prompt_tokens", 0)
            comp_tk = tk.get("completion_tokens", 0)
            lines.append(
                f"Tokens: {total_tk:,} total  "
                f"(\u2191{prompt_tk:,}  \u2193{comp_tk:,})"
            )

            if em:
                lines.append("Enhance modes:")
                for mode, count in sorted(em.items()):
                    lines.append(f"  {mode}: {count}")
            return lines

        parts = _fmt_section(f"Today ({today.get('date', '')})", today)
        parts.append("")
        parts += _fmt_section("All Time", s)

        first = s.get("first_recorded")
        if first:
            parts.append(f"Since: {first[:10]}")

        # Stored data stats
        from .vocabulary import get_vocab_entry_count

        conversation_count = self._conversation_history.count()
        correction_count = self._correction_logger.count()
        vocab_count = get_vocab_entry_count()
        parts.append("")
        parts.append("--- Stored Data ---")
        parts.append(f"Conversations: {conversation_count} records")
        parts.append(f"Corrections:   {correction_count} records")
        parts.append(f"Vocabulary:    {vocab_count} entries")

        text = "\n".join(parts)

        self._activate_for_dialog()

        alert = NSAlert.alloc().init()
        alert.setMessageText_("Usage Statistics")
        alert.addButtonWithTitle_("OK")
        alert.setAlertStyle_(0)

        field_width = 480
        text_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, field_width, 0))
        text_field.setStringValue_(text)
        text_field.setEditable_(False)
        text_field.setBezeled_(False)
        text_field.setDrawsBackground_(False)
        text_field.setSelectable_(True)
        text_field.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12.0, 0.0))
        # Auto-size height to fit content
        text_field.sizeToFit()
        frame = text_field.frame()
        text_field.setFrame_(NSMakeRect(0, 0, field_width, frame.size.height))
        alert.setAccessoryView_(text_field)

        alert.window().setLevel_(NSStatusWindowLevel)
        alert.window().setFloatingPanel_(True)
        alert.window().setHidesOnDeactivate_(False)
        alert.runModal()
        self._restore_accessory()

    def _on_about(self, _) -> None:
        from . import __version__
        from ._build_info import BUILD_DATE, GIT_HASH

        message = f"Version: {__version__}\nBuild:   {GIT_HASH}\nDate:    {BUILD_DATE}"
        self._topmost_alert(title="VoiceText", message=message)
        self._restore_accessory()

    def _on_quit_click(self, _) -> None:
        if self._hotkey_listener:
            self._hotkey_listener.stop()
        if self._clipboard_hotkey_listener:
            self._clipboard_hotkey_listener.stop()
        rumps.quit_application()

    @staticmethod
    def _ensure_accessibility() -> bool:
        """Check and prompt for accessibility permission if needed."""
        if AXIsProcessTrusted():
            logger.info("Accessibility permission granted")
            return True
        # Only prompt if not yet trusted
        options = {"AXTrustedCheckOptionPrompt": kCFBooleanTrue}
        AXIsProcessTrustedWithOptions(options)
        logger.warning("Accessibility permission not granted, prompting user")
        return False

    def run(self, **kwargs) -> None:
        """Initialize models and start the app."""
        self._ensure_accessibility()

        # Load models in background
        def _init_models():
            try:
                self._set_status("Loading...")
                self._transcriber.initialize()
                self._set_status("VT")
                logger.info("Models loaded, app ready")
            except Exception as e:
                logger.error("Model initialization failed: %s", e)
                self._set_status("Error")

        threading.Thread(target=_init_models, daemon=True).start()

        # Start hotkey listener
        hotkey_name = self._config["hotkey"]
        self._hotkey_listener = HoldHotkeyListener(
            key_name=hotkey_name,
            on_press=self._on_hotkey_press,
            on_release=self._on_hotkey_release,
        )
        self._hotkey_listener.start()

        # Start clipboard enhance hotkey listener if configured
        clip_hotkey = self._config.get("clipboard_enhance", {}).get("hotkey", "")
        if clip_hotkey:
            self._clipboard_hotkey_listener = TapHotkeyListener(
                hotkey_str=clip_hotkey,
                on_activate=self._on_clipboard_enhance,
            )
            self._clipboard_hotkey_listener.start()

        super().run(**kwargs)


def _get_dir_size(path) -> int:
    """Calculate total size of all files in a directory."""
    from pathlib import Path

    target = Path(path)
    if not target.exists():
        return 0
    total = 0
    try:
        for f in target.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except OSError:
        pass
    return total


_MultilinePanelTarget = None


def _get_multiline_panel_target_class():
    """Lazily create NSObject subclass for multiline panel OK/Cancel/Close actions."""
    global _MultilinePanelTarget
    if _MultilinePanelTarget is None:
        from Foundation import NSObject

        class MultilinePanelTarget(NSObject):
            """Handles OK, Cancel, and window close for the multiline panel."""

            _finish_callback = None  # set per instance: callable(int)

            def okClicked_(self, sender):
                if self._finish_callback is not None:
                    cb = self._finish_callback
                    self._finish_callback = None
                    cb(1)

            def cancelClicked_(self, sender):
                if self._finish_callback is not None:
                    cb = self._finish_callback
                    self._finish_callback = None
                    cb(0)

            def windowWillClose_(self, notification):
                if self._finish_callback is not None:
                    cb = self._finish_callback
                    self._finish_callback = None
                    cb(0)

        _MultilinePanelTarget = MultilinePanelTarget
    return _MultilinePanelTarget


def main() -> None:
    """Entry point."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    app = VoiceTextApp(config_path=config_path)  # None uses default path
    app.run()


if __name__ == "__main__":
    main()
