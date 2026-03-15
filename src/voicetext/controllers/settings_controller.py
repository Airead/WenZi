"""Settings panel callbacks extracted from VoiceTextApp."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicetext.app import VoiceTextApp

from voicetext.config import save_config
from voicetext.enhance.enhancer import MODE_OFF
from voicetext.transcription.model_registry import (
    PRESET_BY_ID,
    PRESETS,
    build_remote_asr_models,
    is_backend_available,
    is_model_cached,
)
from voicetext.statusbar import send_notification
from voicetext.transcription.base import create_transcriber
from voicetext.ui_helpers import restore_accessory, topmost_alert

logger = logging.getLogger(__name__)


class SettingsController:
    """Handles all Settings panel callbacks."""

    def __init__(self, app: VoiceTextApp) -> None:
        self._app = app

    def on_open_settings(self, _) -> None:
        """Open the Settings panel with current state and callbacks."""
        from voicetext.enhance.vocabulary import get_vocab_entry_count

        app = self._app

        # Collect current state
        hotkeys = app._config.get("hotkeys", {"fn": True})

        # STT presets
        stt_presets = []
        for preset in PRESETS:
            available = is_backend_available(preset.backend)
            stt_presets.append((preset.id, preset.display_name, available))

        # STT remote models
        asr_cfg = app._config.get("asr", {})
        providers = asr_cfg.get("providers", {})
        remote_models = build_remote_asr_models(providers)
        stt_remote = [
            (rm.provider, rm.model, rm.display_name) for rm in remote_models
        ]

        # LLM models
        llm_models = []
        current_llm = None
        if app._enhancer:
            for pname, models in app._enhancer.providers_with_models.items():
                for mname in models:
                    llm_models.append((pname, mname, f"{pname} / {mname}"))
            current_llm = (app._enhancer.provider_name, app._enhancer.model_name)

        # Enhance modes (excluding "off") with order for display
        enhance_modes = []
        if app._enhancer:
            for mode_id, label in app._enhancer.available_modes:
                mode_def = app._enhancer.get_mode_definition(mode_id)
                order = mode_def.order if mode_def else 50
                enhance_modes.append((mode_id, label, order))

        # Vocabulary count
        vocab_count = 0
        if app._enhancer and app._enhancer.vocab_index is not None:
            vocab_count = app._enhancer.vocab_index.entry_count
        if vocab_count == 0:
            vocab_count = get_vocab_entry_count(app._config_dir)

        ui_cfg = app._config.get("ui", {})
        last_tab = ui_cfg.get("settings_last_tab", "general")

        fb_cfg = app._config.get("feedback", {})
        state = {
            "last_tab": last_tab,
            "hotkeys": hotkeys,
            "restart_key": fb_cfg.get("restart_key", "cmd"),
            "cancel_key": fb_cfg.get("cancel_key", "space"),
            "sound_enabled": app._sound_manager.enabled,
            "visual_indicator": app._recording_indicator.enabled,
            "show_device_name": app._recording_indicator.show_device_name,
            "preview": app._preview_enabled,
            "preview_type": app._preview_type,
            "current_preset_id": app._current_preset_id,
            "current_remote_asr": app._current_remote_asr,
            "stt_presets": stt_presets,
            "stt_remote_models": stt_remote,
            "llm_models": llm_models,
            "current_llm": current_llm,
            "enhance_modes": enhance_modes,
            "current_enhance_mode": app._enhance_mode,
            "thinking": bool(app._enhancer and app._enhancer.thinking),
            "vocab_enabled": bool(app._enhancer and app._enhancer.vocab_enabled),
            "vocab_count": vocab_count,
            "auto_build": app._auto_vocab_builder._enabled,
            "history_enabled": bool(
                app._enhancer and app._enhancer.history_enabled
            ),
            "config_dir": app._config_dir,
            "scripting_enabled": app._config.get("scripting", {}).get(
                "enabled", False
            ),
        }

        callbacks = {
            "on_hotkey_toggle": self.hotkey_toggle,
            "on_record_hotkey": lambda: app._on_record_hotkey(None),
            "on_restart_key_select": self.restart_key_select,
            "on_cancel_key_select": self.cancel_key_select,
            "on_scripting_toggle": self.scripting_toggle,
            "on_sound_toggle": self.sound_toggle,
            "on_visual_toggle": self.visual_toggle,
            "on_device_name_toggle": self.show_device_name_toggle,
            "on_preview_toggle": self.preview_toggle,
            "on_preview_type_toggle": self.preview_type_toggle,
            "on_stt_select": self.stt_select,
            "on_stt_remote_select": self.stt_remote_select,
            "on_stt_add_provider": lambda: app._model_controller.on_asr_add_provider(None),
            "on_stt_remove_provider": self.stt_remove_provider,
            "on_llm_select": self.llm_select,
            "on_llm_add_provider": lambda: app._model_controller.on_enhance_add_provider(None),
            "on_llm_remove_provider": self.llm_remove_provider,
            "on_enhance_mode_select": self.enhance_mode_select,
            "on_enhance_mode_edit": self.enhance_mode_edit,
            "on_enhance_add_mode": lambda: app._on_enhance_add_mode(None),
            "on_thinking_toggle": self.thinking_toggle,
            "on_vocab_toggle": self.vocab_toggle,
            "on_auto_build_toggle": self.auto_build_toggle,
            "on_history_toggle": self.history_toggle,
            "on_vocab_build": lambda: app._on_vocab_build(None),
            "on_tab_change": self.tab_change,
            "on_show_config": lambda: app._on_show_config(None),
            "on_edit_config": lambda: app._on_enhance_edit_config(None),
            "on_reload_config": lambda: app._on_reload_config(None),
            "on_config_dir_browse": self.config_dir_browse,
            "on_config_dir_reset": self.config_dir_reset,
        }

        # Call show() directly — do NOT use callAfter, because the menu
        # callback context keeps the app active; deferring would let the app
        # fall back to accessory mode before the panel is displayed.
        app._settings_panel.show(state, callbacks)

    def hotkey_toggle(self, key_name: str, enabled: bool) -> None:
        """Handle hotkey toggle from Settings panel."""
        app = self._app
        app._config["hotkeys"][key_name] = enabled
        save_config(app._config, app._config_path)

        if app._hotkey_listener:
            if enabled:
                app._hotkey_listener.enable_key(key_name)
            else:
                app._hotkey_listener.disable_key(key_name)

        # Sync menu item if it exists
        menu_item = app._hotkey_menu_items.get(key_name)
        if menu_item:
            menu_item.state = 1 if enabled else 0

    def restart_key_select(self, key_name: str) -> None:
        """Handle restart key selection from Settings panel."""
        app = self._app
        fb_cfg = app._config.setdefault("feedback", {})
        fb_cfg["restart_key"] = key_name
        save_config(app._config, app._config_path)

        if app._hotkey_listener:
            app._hotkey_listener.set_restart_key(key_name)
        logger.info("Restart key set to: %s (from settings)", key_name)

    def cancel_key_select(self, key_name: str) -> None:
        """Handle cancel key selection from Settings panel."""
        app = self._app
        fb_cfg = app._config.setdefault("feedback", {})
        fb_cfg["cancel_key"] = key_name
        save_config(app._config, app._config_path)

        if app._hotkey_listener:
            app._hotkey_listener.set_cancel_key(key_name)
        logger.info("Cancel key set to: %s (from settings)", key_name)

    def scripting_toggle(self, enabled: bool) -> None:
        """Handle scripting toggle from Settings panel."""
        app = self._app
        scripting_cfg = app._config.setdefault("scripting", {})
        scripting_cfg["enabled"] = enabled
        save_config(app._config, app._config_path)
        logger.info("Scripting set to: %s (requires restart)", enabled)

    def sound_toggle(self, enabled: bool) -> None:
        """Handle sound toggle from Settings panel."""
        app = self._app
        app._sound_manager.enabled = enabled
        app._sound_feedback_item.state = 1 if enabled else 0

        fb_cfg = app._config.setdefault("feedback", {})
        fb_cfg["sound_enabled"] = enabled
        save_config(app._config, app._config_path)

    def visual_toggle(self, enabled: bool) -> None:
        """Handle visual indicator toggle from Settings panel."""
        app = self._app
        app._recording_indicator.enabled = enabled
        app._visual_indicator_item.state = 1 if enabled else 0

        fb_cfg = app._config.setdefault("feedback", {})
        fb_cfg["visual_indicator"] = enabled
        save_config(app._config, app._config_path)

    def show_device_name_toggle(self, enabled: bool) -> None:
        """Handle show device name toggle from Settings panel."""
        app = self._app
        app._recording_indicator.show_device_name = enabled

        fb_cfg = app._config.setdefault("feedback", {})
        fb_cfg["show_device_name"] = enabled
        save_config(app._config, app._config_path)

    def preview_toggle(self, enabled: bool) -> None:
        """Handle preview toggle from Settings panel."""
        app = self._app
        app._preview_enabled = enabled
        app._preview_item.state = 1 if enabled else 0

        app._config["output"]["preview"] = enabled
        save_config(app._config, app._config_path)
        logger.info("Preview set to: %s (from settings)", enabled)

    def preview_type_toggle(self, use_web: bool) -> None:
        """Handle preview type toggle from Settings panel."""
        from voicetext.ui.result_window import ResultPreviewPanel as NativePanel
        from voicetext.ui.result_window_web import ResultPreviewPanel as WebPanel

        app = self._app
        new_type = "web" if use_web else "native"
        if new_type == app._preview_type:
            return

        app._preview_type = new_type
        app._preview_panel = WebPanel() if use_web else NativePanel()
        app._enhance_controller._preview_panel = app._preview_panel

        app._config["output"]["preview_type"] = new_type
        save_config(app._config, app._config_path)
        logger.info("Preview type set to: %s (from settings)", new_type)

    def stt_select(self, preset_id: str) -> None:
        """Handle STT model selection from Settings panel."""
        app = self._app
        if preset_id == app._current_preset_id and not app._current_remote_asr:
            return
        if app._busy:
            topmost_alert(
                "Cannot switch model",
                "Please wait for current operation to finish.",
            )
            restore_accessory()
            return

        preset = PRESET_BY_ID.get(preset_id)
        if not preset:
            logger.warning("Unknown preset: %s", preset_id)
            return

        app._busy = True
        old_preset_id = app._current_preset_id
        old_transcriber = app._transcriber

        def _do_switch():
            stop_event = threading.Event()
            monitor_thread = None
            try:
                app._set_status("Unloading...")
                old_transcriber.cleanup()

                cached = is_model_cached(preset)
                if not cached:
                    monitor_thread = threading.Thread(
                        target=app._model_controller._monitor_download_progress,
                        args=(preset, stop_event),
                        daemon=True,
                    )
                    monitor_thread.start()
                else:
                    app._set_status("Loading...")

                asr_cfg = app._config["asr"]
                new_transcriber = create_transcriber(
                    backend=preset.backend,
                    use_vad=asr_cfg.get("use_vad", True),
                    use_punc=asr_cfg.get("use_punc", True),
                    language=preset.language or asr_cfg.get("language"),
                    model=preset.model,
                    temperature=asr_cfg.get("temperature"),
                )
                new_transcriber.initialize()

                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)

                app._transcriber = new_transcriber
                app._current_preset_id = preset_id
                app._current_remote_asr = None
                app._menu_builder.update_model_checkmarks()

                app._config["asr"]["preset"] = preset_id
                app._config["asr"]["backend"] = preset.backend
                app._config["asr"]["model"] = preset.model
                app._config["asr"]["language"] = preset.language
                app._config["asr"]["default_provider"] = None
                app._config["asr"]["default_model"] = None
                save_config(app._config, app._config_path)

                app._set_status("VT")
                logger.info("Switched to model: %s (from settings)", preset.display_name)
                try:
                    send_notification("VoiceText", "Model switched",
                                      f"Now using: {preset.display_name}")
                except Exception:
                    logger.debug("Notification unavailable, skipping")

            except Exception as e:
                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)
                logger.error("Model switch failed: %s", e)
                app._set_status("Error")
                app._model_controller._try_restore_previous_model(old_preset_id)

            finally:
                app._busy = False

        threading.Thread(target=_do_switch, daemon=True).start()

    def stt_remote_select(self, provider: str, model: str) -> None:
        """Handle remote STT model selection from Settings panel."""
        app = self._app
        key = (provider, model)
        if key == app._current_remote_asr:
            return
        if app._busy:
            topmost_alert(
                "Cannot switch model",
                "Please wait for current operation to finish.",
            )
            restore_accessory()
            return

        # Find the RemoteASRModel with connection details
        asr_cfg = app._config.get("asr", {})
        providers = asr_cfg.get("providers", {})
        pcfg = providers.get(provider, {})
        if not pcfg:
            logger.warning("Unknown ASR provider: %s", provider)
            return

        app._busy = True
        old_transcriber = app._transcriber

        def _do_switch():
            try:
                app._set_status("Switching...")
                old_transcriber.cleanup()

                new_transcriber = create_transcriber(
                    backend="whisper-api",
                    base_url=pcfg["base_url"],
                    api_key=pcfg["api_key"],
                    model=model,
                    language=asr_cfg.get("language"),
                    temperature=asr_cfg.get("temperature"),
                )
                new_transcriber.initialize()

                app._transcriber = new_transcriber
                app._current_remote_asr = key
                app._current_preset_id = None
                app._menu_builder.update_model_checkmarks()

                app._config["asr"]["default_provider"] = provider
                app._config["asr"]["default_model"] = model
                save_config(app._config, app._config_path)

                app._set_status("VT")
                logger.info("Switched to remote ASR: %s / %s (from settings)",
                            provider, model)
            except Exception as e:
                logger.error("Remote ASR switch failed: %s", e)
                app._set_status("Error")
            finally:
                app._busy = False

        threading.Thread(target=_do_switch, daemon=True).start()

    def stt_remove_provider(self) -> None:
        """Handle STT remove provider from Settings panel."""
        app = self._app
        asr_cfg = app._config.get("asr", {})
        providers = asr_cfg.get("providers", {})
        if providers:
            # Remove the first provider's menu item to trigger existing flow
            first_name = next(iter(providers))
            item = app._asr_remove_provider_items.get(first_name)
            if item:
                app._model_controller.on_asr_remove_provider(item)

    def llm_select(self, provider: str, model: str) -> None:
        """Handle LLM model selection from Settings panel."""
        app = self._app
        if not app._enhancer:
            return
        if provider == app._enhancer.provider_name and model == app._enhancer.model_name:
            return

        app._enhancer.provider_name = provider
        app._enhancer.model_name = model

        # Update menu checkmarks
        current_key = (provider, model)
        for key, item in app._llm_model_menu_items.items():
            item.state = 1 if key == current_key else 0

        # Persist to config
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"]["default_provider"] = provider
        app._config["ai_enhance"]["default_model"] = model
        save_config(app._config, app._config_path)
        logger.info("LLM model set to: %s / %s (from settings)", provider, model)

    def llm_remove_provider(self) -> None:
        """Handle LLM remove provider from Settings panel."""
        app = self._app
        if app._enhancer:
            providers = app._enhancer.providers_with_models
            if providers:
                first_name = next(iter(providers))
                item = app._llm_remove_provider_items.get(first_name)
                if item:
                    app._model_controller.on_enhance_remove_provider(item)

    def enhance_mode_edit(self, mode_id: str) -> None:
        """Open the enhance mode markdown file in TextEdit."""
        try:
            modes_dir = os.path.join(self._app._config_dir, "enhance_modes")
            md_path = os.path.join(modes_dir, f"{mode_id}.md")
            logger.info("Opening mode file: %s", md_path)
            subprocess.Popen(["open", "-a", "TextEdit", md_path])
        except Exception as e:
            logger.error("Failed to open mode file in TextEdit: %s", e, exc_info=True)

    def enhance_mode_select(self, mode_id: str) -> None:
        """Handle enhance mode selection from Settings panel."""
        app = self._app
        # Update menu checkmarks
        for m, item in app._enhance_menu_items.items():
            item.state = 1 if m == mode_id else 0

        app._enhance_mode = mode_id
        app._enhance_controller.enhance_mode = mode_id

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
        logger.info("AI enhance mode set to: %s (from settings)", mode_id)

    def thinking_toggle(self, enabled: bool) -> None:
        """Handle thinking toggle from Settings panel."""
        app = self._app
        if not app._enhancer:
            return
        app._enhancer.thinking = enabled
        app._enhance_thinking_item.state = 1 if enabled else 0

        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"]["thinking"] = enabled
        save_config(app._config, app._config_path)
        logger.info("AI thinking set to: %s (from settings)", enabled)

    def vocab_toggle(self, enabled: bool) -> None:
        """Handle vocabulary toggle from Settings panel."""
        app = self._app
        if not app._enhancer:
            return
        app._enhancer.vocab_enabled = enabled
        app._enhance_vocab_item.state = 1 if enabled else 0

        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"].setdefault("vocabulary", {})
        app._config["ai_enhance"]["vocabulary"]["enabled"] = enabled
        save_config(app._config, app._config_path)
        logger.info("Vocabulary set to: %s (from settings)", enabled)

    def auto_build_toggle(self, enabled: bool) -> None:
        """Handle auto build toggle from Settings panel."""
        app = self._app
        app._auto_vocab_builder._enabled = enabled
        app._enhance_auto_build_item.state = 1 if enabled else 0

        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"].setdefault("vocabulary", {})
        app._config["ai_enhance"]["vocabulary"]["auto_build"] = enabled
        save_config(app._config, app._config_path)
        logger.info("Auto vocabulary build set to: %s (from settings)", enabled)

    def history_toggle(self, enabled: bool) -> None:
        """Handle history toggle from Settings panel."""
        app = self._app
        if not app._enhancer:
            return
        app._enhancer.history_enabled = enabled
        app._enhance_history_item.state = 1 if enabled else 0

        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"].setdefault("conversation_history", {})
        app._config["ai_enhance"]["conversation_history"]["enabled"] = enabled
        save_config(app._config, app._config_path)
        logger.info("Conversation history set to: %s (from settings)", enabled)

    def tab_change(self, tab_id: str) -> None:
        """Persist the last active settings tab."""
        app = self._app
        app._config.setdefault("ui", {})["settings_last_tab"] = tab_id
        save_config(app._config, app._config_path)

    def config_dir_browse(self) -> None:
        """Open a directory picker to choose a custom config directory."""
        from AppKit import NSOpenPanel

        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setCanCreateDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        panel.setTitle_("Select Config Directory")
        panel.setPrompt_("Select")

        result = panel.runModal()
        if result != 1:  # NSModalResponseOK
            return

        url = panel.URL()
        if not url:
            return

        new_dir = str(url.path())
        app = self._app

        # Copy entire config directory (config, enhance_modes, sounds,
        # vocabulary, history, stats, etc.) to the new location.
        # Existing files in the target are not overwritten.
        import shutil

        old_dir = app._config_dir
        if os.path.isdir(old_dir) and os.path.realpath(old_dir) != os.path.realpath(new_dir):
            shutil.copytree(old_dir, new_dir, dirs_exist_ok=True)
            logger.info("Copied config directory %s -> %s", old_dir, new_dir)

        from voicetext.config import save_config_dir_preference

        save_config_dir_preference(new_dir)
        app._settings_panel.update_config_dir(new_dir)
        logger.info("Config directory preference set to: %s", new_dir)

        self._prompt_restart(
            f"Config directory changed to:\n{new_dir}\n\n"
            "A restart is required for this to take effect."
        )

    def config_dir_reset(self) -> None:
        """Reset config directory to default."""
        from voicetext.config import DEFAULT_CONFIG_DIR, reset_config_dir_preference

        reset_config_dir_preference()
        default_dir = os.path.expanduser(DEFAULT_CONFIG_DIR)
        self._app._settings_panel.update_config_dir(default_dir)
        logger.info("Config directory preference reset to default")

        self._prompt_restart(
            f"Config directory reset to default:\n{default_dir}\n\n"
            "A restart is required for this to take effect."
        )

    def _prompt_restart(self, message: str) -> None:
        """Show a dialog asking whether to restart now, and do so if confirmed."""
        # Close settings panel first so the alert is not hidden behind it
        self._app._settings_panel.close()

        result = topmost_alert(
            title="Restart Required",
            message=message,
            ok="Restart Now",
            cancel="Later",
        )
        restore_accessory()
        if result:
            self._restart_app()

    @staticmethod
    def _restart_app() -> None:
        """Spawn a shell watcher that waits for this process to exit, then relaunches."""
        import shlex
        import sys

        pid = os.getpid()
        cmd = shlex.join([sys.executable] + sys.argv)

        # Use /bin/sh so the watcher is fully independent of the Python runtime.
        # `kill -0` checks if the process is still alive; once it's gone, relaunch.
        script = f"while kill -0 {pid} 2>/dev/null; do sleep 0.2; done; exec {cmd}"
        subprocess.Popen(
            ["/bin/sh", "-c", script],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Restart watcher spawned (pid=%d), quitting...", pid)

        from voicetext.statusbar import quit_application
        quit_application()
