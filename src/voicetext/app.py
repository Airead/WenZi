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
from typing import Dict, Optional

import rumps
from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions
from CoreFoundation import kCFBooleanTrue

from .config import load_config, save_config
from .conversation_history import ConversationHistory
from .correction_log import CorrectionLogger
from .enhancer import MODE_OFF, TextEnhancer, create_enhancer
from .result_window import ResultPreviewPanel
from .hotkey import HoldHotkeyListener
from .input import type_text
from .model_registry import (
    PRESET_BY_ID,
    PRESETS,
    ModelPreset,
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
        self._busy = False
        self._preview_panel = ResultPreviewPanel()
        self._correction_logger = CorrectionLogger()
        self._conversation_history = ConversationHistory()

        # Resolve current preset
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

        # Model submenu
        self._model_menu = rumps.MenuItem("Model")
        self._model_menu_items: Dict[str, rumps.MenuItem] = {}
        for preset in PRESETS:
            backend_ok = is_backend_available(preset.backend)
            if backend_ok:
                title = preset.display_name
            else:
                title = f"{preset.display_name} (N/A)"
            item = rumps.MenuItem(title)
            # Tag the item with preset id
            item._preset_id = preset.id
            if backend_ok:
                item.set_callback(self._on_model_select)
            else:
                item.set_callback(None)
            if preset.id == self._current_preset_id:
                item.state = 1
            self._model_menu_items[preset.id] = item
            self._model_menu.add(item)

        # AI Enhance
        self._enhancer = create_enhancer(self._config)
        ai_cfg = self._config.get("ai_enhance", {})
        self._enhance_mode: str = ai_cfg.get("mode", "proofread")
        if self._enhancer and not ai_cfg.get("enabled", False):
            self._enhance_mode = MODE_OFF

        # AI Enhance submenu
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

        # Provider submenu
        self._enhance_menu.add(rumps.separator)
        self._enhance_provider_menu = rumps.MenuItem("Provider")
        self._enhance_provider_items: Dict[str, rumps.MenuItem] = {}
        if self._enhancer:
            for pname in self._enhancer.provider_names:
                item = rumps.MenuItem(pname)
                item._provider_name = pname
                item.set_callback(self._on_enhance_provider_select)
                if pname == self._enhancer.provider_name:
                    item.state = 1
                self._enhance_provider_items[pname] = item
                self._enhance_provider_menu.add(item)
        self._enhance_menu.add(self._enhance_provider_menu)

        # Model submenu
        self._enhance_model_menu = rumps.MenuItem("Model")
        self._enhance_model_items: Dict[str, rumps.MenuItem] = {}
        self._build_enhance_model_menu()
        self._enhance_menu.add(self._enhance_model_menu)

        # Thinking toggle
        self._enhance_thinking_item = rumps.MenuItem(
            "Thinking", callback=self._on_enhance_thinking_toggle
        )
        if self._enhancer and self._enhancer.thinking:
            self._enhance_thinking_item.state = 1
        self._enhance_menu.add(self._enhance_thinking_item)

        # Vocabulary toggle
        vocab_enabled = ai_cfg.get("vocabulary", {}).get("enabled", False)
        self._enhance_vocab_item = rumps.MenuItem(
            "Vocabulary", callback=self._on_vocab_toggle
        )
        self._enhance_vocab_item.state = 1 if vocab_enabled else 0
        self._enhance_menu.add(self._enhance_vocab_item)

        # Build vocabulary action
        self._enhance_vocab_build_item = rumps.MenuItem(
            "Build Vocabulary...", callback=self._on_vocab_build
        )
        self._enhance_menu.add(self._enhance_vocab_build_item)

        # Conversation history toggle
        history_enabled = ai_cfg.get("conversation_history", {}).get("enabled", False)
        self._enhance_history_item = rumps.MenuItem(
            "Conversation History", callback=self._on_history_toggle
        )
        self._enhance_history_item.state = 1 if history_enabled else 0
        self._enhance_menu.add(self._enhance_history_item)

        # Provider configuration items
        self._enhance_menu.add(rumps.separator)
        self._enhance_add_provider_item = rumps.MenuItem(
            "Add Provider...", callback=self._on_enhance_add_provider
        )
        self._enhance_menu.add(self._enhance_add_provider_item)

        self._enhance_remove_provider_menu = rumps.MenuItem("Remove Provider")
        self._enhance_remove_provider_items: Dict[str, rumps.MenuItem] = {}
        self._build_enhance_remove_provider_menu()
        self._enhance_menu.add(self._enhance_remove_provider_menu)

        self._enhance_edit_config_item = rumps.MenuItem(
            "Edit Config...", callback=self._on_enhance_edit_config
        )
        self._enhance_menu.add(self._enhance_edit_config_item)

        self._preview_item = rumps.MenuItem(
            "Preview", callback=self._on_preview_toggle
        )
        self._preview_item.state = 1 if self._preview_enabled else 0

        self._copy_log_item = rumps.MenuItem(
            "Copy Log Path", callback=self._on_copy_log_path
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

        self.menu = [
            self._status_item,
            self._hotkey_item,
            None,
            self._model_menu,
            self._enhance_menu,
            self._preview_item,
            self._copy_log_item,
            self._debug_menu,
            None,
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
        self._set_status("Transcribing...")

        # Run transcription in background to keep UI responsive
        def _do_transcribe():
            try:
                text = self._transcriber.transcribe(wav_data)
                if text and text.strip():
                    asr_text = text.strip()
                    use_enhance = bool(self._enhancer and self._enhancer.is_active)

                    if self._preview_enabled:
                        self._do_transcribe_with_preview(asr_text, use_enhance)
                    else:
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
        text = asr_text
        enhanced_text = None
        if use_enhance:
            self._set_status("Enhancing...")
            try:
                loop = asyncio.new_event_loop()
                text = loop.run_until_complete(self._enhancer.enhance(text))
                loop.close()
                enhanced_text = text
            except Exception as e:
                logger.error("AI enhancement failed: %s", e)

        type_text(
            text.strip(),
            append_newline=self._append_newline,
            method=self._output_method,
        )
        self._set_status("VT")

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

    def _do_transcribe_with_preview(self, asr_text: str, use_enhance: bool) -> None:
        """Show preview panel, optionally run AI enhance, wait for user decision."""
        from PyObjCTools import AppHelper
        import time

        self._current_preview_asr_text = asr_text

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
                except Exception as e:
                    logger.error("Failed to log correction: %s", e)
            result_event.set()

        def on_cancel() -> None:
            result_holder["confirmed"] = False
            result_event.set()

        # Build mode list for the segmented control
        available_modes = []
        if self._enhancer:
            available_modes = [("off", "Off")] + self._enhancer.available_modes

        # Show panel on main thread, then start enhancement after panel is built
        def _show():
            self._activate_for_dialog()
            self._preview_panel.show(
                asr_text=asr_text,
                show_enhance=use_enhance,
                on_confirm=on_confirm,
                on_cancel=on_cancel,
                available_modes=available_modes,
                current_mode=self._enhance_mode,
                on_mode_change=self._on_preview_mode_change,
            )
            # Start enhancement after show() so request_id is not reset
            if use_enhance:
                self._preview_panel.enhance_request_id += 1
                self._run_enhance_in_background(
                    asr_text, self._preview_panel.enhance_request_id, result_holder
                )

        AppHelper.callAfter(_show)
        self._set_status("Preview...")

        # Wait for user decision
        result_event.wait()

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
                    asr_text=asr_text,
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

    def _run_enhance_in_background(
        self, asr_text: str, request_id: int, result_holder: dict | None = None
    ) -> None:
        """Run AI enhancement in a background thread."""

        def _enhance():
            try:
                loop = asyncio.new_event_loop()
                enhanced = loop.run_until_complete(
                    self._enhancer.enhance(asr_text)
                )
                loop.close()
                if result_holder is not None:
                    result_holder["enhanced_text"] = enhanced
                self._preview_panel.set_enhance_result(enhanced, request_id=request_id)
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
        try:
            self._do_add_mode()
        except Exception as e:
            logger.error("Add mode failed: %s", e, exc_info=True)
        finally:
            self._restore_accessory()

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

    def _on_enhance_provider_select(self, sender) -> None:
        """Handle AI enhance provider menu item click."""
        pname = sender._provider_name
        if not self._enhancer or pname == self._enhancer.provider_name:
            return

        self._enhancer.provider_name = pname

        # Update provider checkmarks
        for name, item in self._enhance_provider_items.items():
            item.state = 1 if name == pname else 0

        # Rebuild model submenu for new provider
        self._build_enhance_model_menu()

        # Persist to config
        self._config.setdefault("ai_enhance", {})
        self._config["ai_enhance"]["default_provider"] = self._enhancer.provider_name
        self._config["ai_enhance"]["default_model"] = self._enhancer.model_name
        save_config(self._config, self._config_path)
        logger.info("AI enhance provider set to: %s", pname)

    def _on_enhance_model_select(self, sender) -> None:
        """Handle AI enhance model menu item click."""
        mname = sender._model_name
        if not self._enhancer or mname == self._enhancer.model_name:
            return

        self._enhancer.model_name = mname

        # Update model checkmarks
        for name, item in self._enhance_model_items.items():
            item.state = 1 if name == mname else 0

        # Persist to config
        self._config.setdefault("ai_enhance", {})
        self._config["ai_enhance"]["default_model"] = mname
        save_config(self._config, self._config_path)
        logger.info("AI enhance model set to: %s", mname)

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

        logger.info("Starting vocabulary build...")

        cancel_event = threading.Event()

        from .vocab_build_window import VocabBuildProgressPanel

        progress_panel = VocabBuildProgressPanel()
        # _on_vocab_build runs on the main thread (rumps callback), so show directly
        progress_panel.show(on_cancel=lambda: cancel_event.set())

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

    def _build_enhance_model_menu(self) -> None:
        """Build or rebuild the AI enhance model submenu."""
        # Clear existing items — only call .clear() if the native menu exists
        if self._enhance_model_menu._menu is not None:
            self._enhance_model_menu.clear()
        self._enhance_model_items.clear()

        if not self._enhancer:
            return

        for mname in self._enhancer.model_names:
            item = rumps.MenuItem(mname)
            item._model_name = mname
            item.set_callback(self._on_enhance_model_select)
            if mname == self._enhancer.model_name:
                item.state = 1
            self._enhance_model_items[mname] = item
            self._enhance_model_menu.add(item)

    def _build_enhance_remove_provider_menu(self) -> None:
        """Build or rebuild the remove-provider submenu."""
        if self._enhance_remove_provider_menu._menu is not None:
            self._enhance_remove_provider_menu.clear()
        self._enhance_remove_provider_items.clear()

        if not self._enhancer:
            return

        for pname in self._enhancer.provider_names:
            item = rumps.MenuItem(pname)
            item._provider_name = pname
            item.set_callback(self._on_enhance_remove_provider)
            self._enhance_remove_provider_items[pname] = item
            self._enhance_remove_provider_menu.add(item)

    def _build_enhance_provider_menu(self) -> None:
        """Rebuild the provider selection submenu."""
        if self._enhance_provider_menu._menu is not None:
            self._enhance_provider_menu.clear()
        self._enhance_provider_items.clear()

        if not self._enhancer:
            return

        for pname in self._enhancer.provider_names:
            item = rumps.MenuItem(pname)
            item._provider_name = pname
            item.set_callback(self._on_enhance_provider_select)
            if pname == self._enhancer.provider_name:
                item.state = 1
            self._enhance_provider_items[pname] = item
            self._enhance_provider_menu.add(item)

    @staticmethod
    def _activate_for_dialog():
        """Set activation policy so modal dialogs can show from non-bundled process."""
        from AppKit import NSApp
        NSApp.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular
        NSApp.activateIgnoringOtherApps_(True)

    @staticmethod
    def _restore_accessory():
        """Restore accessory activation policy (statusbar-only)."""
        from AppKit import NSApp
        NSApp.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    @staticmethod
    def _topmost_alert(title=None, message="", ok=None, cancel=None):
        """Show an NSAlert at NSStatusWindowLevel so it stays on top."""
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

        # NSAlertFirstButtonReturn = 1000, NSAlertSecondButtonReturn = 1001
        result = alert.runModal()
        return 1 if result == 1000 else 0

    @staticmethod
    def _run_window(title: str, message: str, default_text: str = "",
                    ok: str = "OK", cancel: str = "Cancel",
                    dimensions: tuple = (320, 22), secure: bool = False):
        """Run a rumps.Window with proper app activation. Returns Response or None on cancel."""
        from AppKit import NSStatusWindowLevel

        VoiceTextApp._activate_for_dialog()
        w = rumps.Window(
            title=title, message=message, default_text=default_text,
            ok=ok, cancel=cancel, dimensions=dimensions, secure=secure,
        )
        w._alert.window().setLevel_(NSStatusWindowLevel)
        resp = w.run()
        if resp.clicked != 1:
            return None
        return resp

    @staticmethod
    def _run_multiline_window(title: str, message: str, default_text: str = "",
                              ok: str = "OK", cancel: str = "Cancel",
                              dimensions: tuple = (380, 180)):
        """Run a modal dialog with a multiline NSTextView (Enter = newline).

        Returns a Response-like object with .clicked and .text, or None on cancel.
        """
        from AppKit import (
            NSApp, NSAlert, NSScrollView, NSTextView, NSBezelBorder,
            NSStatusWindowLevel,
        )
        from Foundation import NSMakeRect

        VoiceTextApp._activate_for_dialog()

        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.addButtonWithTitle_(ok)
        alert.addButtonWithTitle_(cancel)
        alert.setAlertStyle_(0)  # informational

        width, height = dimensions
        scroll_view = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, width, height)
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
        text_view.setFont_(
            __import__("AppKit").NSFont.userFixedPitchFontOfSize_(12.0)
        )
        text_view.setString_(default_text)
        scroll_view.setDocumentView_(text_view)

        alert.setAccessoryView_(scroll_view)
        alert.window().setInitialFirstResponder_(text_view)
        alert.window().setLevel_(NSStatusWindowLevel)

        # NSAlertFirstButtonReturn = 1000
        result = alert.runModal()
        clicked = 1 if result == 1000 else 0
        text = text_view.string()

        if clicked != 1:
            return None

        class _Response:
            pass

        resp = _Response()
        resp.clicked = clicked
        resp.text = text
        return resp

    def _on_enhance_add_provider(self, _) -> None:
        """Add a new AI provider via multi-step dialog."""
        try:
            self._do_add_provider()
        except Exception as e:
            logger.error("Add provider failed: %s", e, exc_info=True)
        finally:
            self._restore_accessory()

    _ADD_PROVIDER_TEMPLATE = """\
name: my-provider
base_url: https://api.openai.com/v1
api_key: sk-xxx
models:
  gpt-4o
  gpt-4o-mini
extra_body: {"chat_template_kwargs": {"enable_thinking": false}}"""

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
                    "Fill in the provider config below, then click Verify.\n"
                    "Models: one per line under 'models:'."
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

            self._build_enhance_provider_menu()
            self._build_enhance_model_menu()
            self._build_enhance_remove_provider_menu()

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

            # Rebuild menus
            self._build_enhance_provider_menu()
            self._build_enhance_model_menu()
            self._build_enhance_remove_provider_menu()

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
        """Handle model menu item click."""
        preset_id = sender._preset_id

        # Ignore if already active
        if preset_id == self._current_preset_id:
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
                self._update_model_checkmark(preset_id)

                # Persist to config
                self._config["asr"]["preset"] = preset_id
                self._config["asr"]["backend"] = preset.backend
                self._config["asr"]["model"] = preset.model
                self._config["asr"]["language"] = preset.language
                save_config(self._config, self._config_path)

                self._set_status("VT")
                rumps.notification(
                    "VoiceText",
                    "Model switched",
                    f"Now using: {preset.display_name}",
                )
                logger.info("Switched to model: %s", preset.display_name)

            except Exception as e:
                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)

                logger.error("Model switch failed: %s", e)
                self._set_status("Error")
                rumps.notification(
                    "VoiceText",
                    "Model switch failed",
                    str(e)[:100],
                )

                # Try to restore previous model
                self._try_restore_previous_model(old_preset_id)

            finally:
                # Re-enable model menu callbacks (only for available backends)
                for pid, item in self._model_menu_items.items():
                    p = PRESET_BY_ID[pid]
                    if is_backend_available(p.backend):
                        item.set_callback(self._on_model_select)
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

    def _update_model_checkmark(self, preset_id: str) -> None:
        """Update checkmark state on model menu items."""
        for pid, item in self._model_menu_items.items():
            item.state = 1 if pid == preset_id else 0

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
            self._update_model_checkmark(old_preset_id)
            self._set_status("VT")
            logger.info("Previous model restored")
        except Exception as e2:
            logger.error("Failed to restore previous model: %s", e2)
            self._set_status("Error")

    def _on_quit_click(self, _) -> None:
        if self._hotkey_listener:
            self._hotkey_listener.stop()
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


def main() -> None:
    """Entry point."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    app = VoiceTextApp(config_path=config_path)  # None uses default path
    app.run()


if __name__ == "__main__":
    main()
