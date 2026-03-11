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
from .enhancer import EnhanceMode, TextEnhancer, create_enhancer
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
        self._hotkey_listener: Optional[HoldHotkeyListener] = None
        self._busy = False

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
        self._enhance_mode = EnhanceMode(ai_cfg.get("mode", "proofread"))
        if self._enhancer and not ai_cfg.get("enabled", False):
            self._enhance_mode = EnhanceMode.OFF

        # AI Enhance submenu
        self._enhance_menu = rumps.MenuItem("AI Enhance")
        self._enhance_menu_items: Dict[str, rumps.MenuItem] = {}
        _mode_labels = {
            "off": "Off",
            "proofread": "纠错润色",
            "format": "格式化",
            "complete": "智能补全",
            "enhance": "全面增强",
        }
        for mode in EnhanceMode:
            label = _mode_labels.get(mode.value, mode.value)
            item = rumps.MenuItem(label)
            item._enhance_mode = mode
            item.set_callback(self._on_enhance_mode_select)
            if mode == self._enhance_mode:
                item.state = 1
            self._enhance_menu_items[mode.value] = item
            self._enhance_menu.add(item)

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

        self._copy_log_item = rumps.MenuItem(
            "Copy Log Path", callback=self._on_copy_log_path
        )

        self.menu = [
            self._status_item,
            self._hotkey_item,
            None,
            self._model_menu,
            self._enhance_menu,
            self._copy_log_item,
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
                    # AI enhancement step
                    if self._enhancer and self._enhancer.is_active:
                        self._set_status("Enhancing...")
                        try:
                            loop = asyncio.new_event_loop()
                            text = loop.run_until_complete(
                                self._enhancer.enhance(text)
                            )
                            loop.close()
                        except Exception as e:
                            logger.error("AI enhancement failed: %s", e)

                    type_text(
                        text.strip(),
                        append_newline=self._append_newline,
                        method=self._output_method,
                    )
                    self._set_status("VT")
                else:
                    self._set_status("(empty)")
                    logger.warning("Transcription returned empty text")
            except Exception as e:
                logger.error("Transcription failed: %s", e)
                self._set_status("Error")
            finally:
                self._busy = False

        threading.Thread(target=_do_transcribe, daemon=True).start()

    def _on_enhance_mode_select(self, sender) -> None:
        """Handle AI enhance mode menu item click."""
        mode = sender._enhance_mode

        # Update checkmarks
        for m, item in self._enhance_menu_items.items():
            item.state = 1 if m == mode.value else 0

        self._enhance_mode = mode

        # Update enhancer state
        if self._enhancer:
            if mode == EnhanceMode.OFF:
                self._enhancer._enabled = False
            else:
                self._enhancer._enabled = True
                self._enhancer.mode = mode

        # Persist to config
        self._config.setdefault("ai_enhance", {})
        self._config["ai_enhance"]["enabled"] = mode != EnhanceMode.OFF
        self._config["ai_enhance"]["mode"] = mode.value
        save_config(self._config, self._config_path)
        logger.info("AI enhance mode set to: %s", mode.value)

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
    def _run_window(title: str, message: str, default_text: str = "",
                    ok: str = "OK", cancel: str = "Cancel",
                    dimensions: tuple = (320, 22), secure: bool = False):
        """Run a rumps.Window with proper app activation. Returns Response or None on cancel."""
        VoiceTextApp._activate_for_dialog()
        w = rumps.Window(
            title=title, message=message, default_text=default_text,
            ok=ok, cancel=cancel, dimensions=dimensions, secure=secure,
        )
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
        from AppKit import NSApp, NSAlert, NSScrollView, NSTextView, NSBezelBorder
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
            rumps.alert("Error", "AI enhancer is not initialized.")
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
                rumps.alert("Validation Error", parsed)
                template = resp.text
                self._save_provider_draft(resp.text)
                continue

            name, base_url, api_key, models = parsed

            if name in self._enhancer.provider_names:
                self._activate_for_dialog()
                rumps.alert("Error", f"Provider '{name}' already exists.")
                template = resp.text
                self._save_provider_draft(resp.text)
                continue

            # Verify connection
            self._activate_for_dialog()
            rumps.alert("Verifying...", f"Testing connection to {base_url}\nModel: {models[0]}")

            import asyncio
            loop = asyncio.new_event_loop()
            try:
                err = loop.run_until_complete(
                    self._enhancer.verify_provider(base_url, api_key, models[0])
                )
            finally:
                loop.close()

            if err:
                self._activate_for_dialog()
                result = rumps.alert(
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
            result = rumps.alert(
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
            success = self._enhancer.add_provider(name, base_url, api_key, models)
            if not success:
                self._activate_for_dialog()
                rumps.alert(
                    "Error",
                    "Failed to initialize provider. "
                    "Check that the openai package is installed.",
                )
                return

            self._config.setdefault("ai_enhance", {})
            providers_cfg = self._config["ai_enhance"].setdefault("providers", {})
            providers_cfg[name] = {
                "base_url": base_url,
                "api_key": api_key,
                "models": models,
            }
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

    def _on_enhance_remove_provider(self, sender) -> None:
        """Remove an AI provider after confirmation."""
        try:
            pname = sender._provider_name
            if not self._enhancer:
                return

            self._activate_for_dialog()

            result = rumps.alert(
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
