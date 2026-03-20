"""Settings panel callbacks extracted from WenZiApp."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wenzi.app import WenZiApp

from wenzi.config import save_config
from wenzi.enhance.enhancer import MODE_OFF
from wenzi.transcription.model_registry import (
    PRESET_BY_ID,
    PRESETS,
    build_remote_asr_models,
    clear_model_cache,
    is_backend_available,
    is_model_cached,
)
from wenzi.statusbar import send_notification
from wenzi.transcription.base import create_transcriber
from wenzi.ui_helpers import restore_accessory, topmost_alert

logger = logging.getLogger(__name__)


class SettingsController:
    """Handles all Settings panel callbacks."""

    def __init__(self, app: WenZiApp) -> None:
        self._app = app

    def _save_and_reload(self) -> None:
        """Save config and reload the Settings panel if it is visible.

        Safe to call from any thread — the UI reload is dispatched to the
        main thread automatically.
        """
        from PyObjCTools import AppHelper

        app = self._app
        save_config(app._config, app._config_path)
        if app._settings_panel.is_visible:
            AppHelper.callAfter(self.on_open_settings, None)

    def on_open_settings(self, _) -> None:
        """Open the Settings panel with current state and callbacks."""
        from wenzi.enhance.vocabulary import get_vocab_entry_count

        app = self._app

        # Collect current state
        hotkeys = app._config.get("hotkeys", {"fn": True})

        # STT presets — only show backends that are available
        stt_presets = []
        for preset in PRESETS:
            if is_backend_available(preset.backend):
                stt_presets.append((preset.id, preset.display_name, True))

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
            vocab_count = get_vocab_entry_count(app._data_dir)

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
            "current_preset_id": app._current_preset_id,
            "current_remote_asr": app._current_remote_asr,
            "stt_presets": stt_presets,
            "stt_remote_models": stt_remote,
            "llm_models": llm_models,
            "current_llm": current_llm,
            "model_timeout": app._config.get("ai_enhance", {}).get("connection_timeout", 10),
            "enhance_modes": enhance_modes,
            "current_enhance_mode": app._enhance_mode,
            "thinking": bool(app._enhancer and app._enhancer.thinking),
            "vocab_enabled": bool(app._enhancer and app._enhancer.vocab_enabled),
            "vocab_count": vocab_count,
            "auto_build": app._auto_vocab_builder._enabled,
            "vocab_build_model": self._get_vocab_build_model(llm_models),
            "history_enabled": bool(
                app._enhancer and app._enhancer.history_enabled
            ),
            "history_max_entries": (
                app._enhancer.history_max_entries if app._enhancer else 10
            ),
            "history_refresh_threshold": (
                app._enhancer.history_refresh_threshold if app._enhancer else 50
            ),
            "input_context_level": app._config.get("ai_enhance", {}).get("input_context", "basic"),
            "config_dir": app._config_dir,
            "scripting_enabled": app._config.get("scripting", {}).get(
                "enabled", False
            ),
            "launcher": self._build_launcher_state(),
        }

        callbacks = {
            "on_hotkey_toggle": self.hotkey_toggle,
            "on_hotkey_mode_select": self.hotkey_mode_select,
            "on_hotkey_delete": self.hotkey_delete,
            "on_record_hotkey": lambda: app._on_record_hotkey(None),
            "on_restart_key_select": self.restart_key_select,
            "on_cancel_key_select": self.cancel_key_select,
            "on_scripting_toggle": self.scripting_toggle,
            "on_sound_toggle": self.sound_toggle,
            "on_visual_toggle": self.visual_toggle,
            "on_device_name_toggle": self.show_device_name_toggle,
            "on_preview_toggle": self.preview_toggle,
            "on_stt_select": self.stt_select,
            "on_stt_remote_select": self.stt_remote_select,
            "on_stt_add_provider": lambda: app._model_controller.on_asr_add_provider(None),
            "on_stt_remove_provider": self.stt_remove_provider,
            "on_llm_select": self.llm_select,
            "on_llm_add_provider": lambda: app._model_controller.on_enhance_add_provider(None),
            "on_llm_remove_provider": self.llm_remove_provider,
            "on_model_timeout": self.model_timeout_change,
            "on_enhance_mode_select": self.enhance_mode_select,
            "on_enhance_mode_edit": self.enhance_mode_edit,
            "on_enhance_add_mode": lambda: app._on_enhance_add_mode(None),
            "on_thinking_toggle": self.thinking_toggle,
            "on_vocab_toggle": self.vocab_toggle,
            "on_auto_build_toggle": self.auto_build_toggle,
            "on_history_toggle": self.history_toggle,
            "on_history_max_entries": self.history_max_entries_change,
            "on_history_refresh_threshold": self.history_refresh_threshold_change,
            "on_input_context_change": self.input_context_change,
            "on_vocab_build_model_select": self.vocab_build_model_select,
            "on_vocab_build": lambda: app._on_vocab_build(None),
            "on_tab_change": self.tab_change,
            "on_reveal_config_folder": self.reveal_config_folder,
            "on_config_dir_browse": self.config_dir_browse,
            "on_config_dir_reset": self.config_dir_reset,
            "on_launcher_toggle": self.launcher_toggle,
            "on_launcher_hotkey_record": self.launcher_hotkey_record,
            "on_launcher_hotkey_clear": self.launcher_hotkey_clear,
            "on_launcher_source_toggle": self.launcher_source_toggle,
            "on_launcher_prefix_change": self.launcher_prefix_change,
            "on_launcher_usage_learning_toggle": self.launcher_usage_learning_toggle,
            "on_launcher_switch_english_toggle": self.launcher_switch_english_toggle,
            "on_launcher_refresh_icons": self.launcher_refresh_icons,
            "on_launcher_source_hotkey_record": self.launcher_source_hotkey_record,
            "on_launcher_source_hotkey_clear": self.launcher_source_hotkey_clear,
            "on_new_snippet_hotkey_record": self.new_snippet_hotkey_record,
            "on_new_snippet_hotkey_clear": self.new_snippet_hotkey_clear,
            "_reopen": lambda: self.on_open_settings(None),
        }

        # Call show() directly — do NOT use callAfter, because the menu
        # callback context keeps the app active; deferring would let the app
        # fall back to accessory mode before the panel is displayed.
        app._settings_panel.show(state, callbacks)

    def hotkey_toggle(self, key_name: str, enabled: bool) -> None:
        """Handle hotkey toggle from Settings panel."""
        app = self._app
        current = app._config["hotkeys"].get(key_name)
        if enabled:
            # Restore previous dict value (preserve mode) or set True
            if isinstance(current, dict):
                pass  # already a dict, keep it
            else:
                app._config["hotkeys"][key_name] = True
        else:
            app._config["hotkeys"][key_name] = False
        self._save_and_reload()

        if app._hotkey_listener:
            if enabled:
                app._hotkey_listener.enable_key(key_name)
            else:
                app._hotkey_listener.disable_key(key_name)

        # Sync menu item if it exists
        menu_item = app._hotkey_menu_items.get(key_name)
        if menu_item:
            menu_item.state = 1 if enabled else 0

    def hotkey_mode_select(self, key_name: str, mode_id: str | None) -> None:
        """Handle per-hotkey mode selection from Settings panel.

        Args:
            key_name: The hotkey name.
            mode_id: The mode to bind, or None for system default.
        """
        app = self._app
        hotkeys = app._config.setdefault("hotkeys", {})
        if mode_id is None:
            # System default — store as plain True (remove dict)
            hotkeys[key_name] = True
        else:
            hotkeys[key_name] = {"mode": mode_id}
        self._save_and_reload()
        logger.info("Hotkey %s mode set to: %s", key_name, mode_id)

    def hotkey_delete(self, key_name: str) -> None:
        """Delete a hotkey from config (fn cannot be deleted)."""
        from wenzi.hotkey import _is_fn_key

        app = self._app
        if _is_fn_key(key_name):
            return

        app._config.get("hotkeys", {}).pop(key_name, None)
        self._save_and_reload()

        if app._hotkey_listener:
            app._hotkey_listener.disable_key(key_name)

        # Remove menu item if it exists
        menu_item = app._hotkey_menu_items.pop(key_name, None)
        if menu_item:
            menu_item.menu().removeItem_(menu_item)

        logger.info("Hotkey %s deleted (from settings)", key_name)

    def restart_key_select(self, key_name: str) -> None:
        """Handle restart key selection from Settings panel."""
        app = self._app
        fb_cfg = app._config.setdefault("feedback", {})
        fb_cfg["restart_key"] = key_name
        self._save_and_reload()

        if app._hotkey_listener:
            app._hotkey_listener.set_restart_key(key_name)
        logger.info("Restart key set to: %s (from settings)", key_name)

    def cancel_key_select(self, key_name: str) -> None:
        """Handle cancel key selection from Settings panel."""
        app = self._app
        fb_cfg = app._config.setdefault("feedback", {})
        fb_cfg["cancel_key"] = key_name
        self._save_and_reload()

        if app._hotkey_listener:
            app._hotkey_listener.set_cancel_key(key_name)
        logger.info("Cancel key set to: %s (from settings)", key_name)

    def scripting_toggle(self, enabled: bool) -> None:
        """Handle scripting toggle from Settings panel."""
        app = self._app
        scripting_cfg = app._config.setdefault("scripting", {})
        scripting_cfg["enabled"] = enabled
        self._save_and_reload()
        logger.info("Scripting set to: %s (requires restart)", enabled)

    def sound_toggle(self, enabled: bool) -> None:
        """Handle sound toggle from Settings panel."""
        app = self._app
        app._sound_manager.enabled = enabled
        app._sound_feedback_item.state = 1 if enabled else 0

        fb_cfg = app._config.setdefault("feedback", {})
        fb_cfg["sound_enabled"] = enabled
        self._save_and_reload()

    def visual_toggle(self, enabled: bool) -> None:
        """Handle visual indicator toggle from Settings panel."""
        app = self._app
        app._recording_indicator.enabled = enabled
        app._visual_indicator_item.state = 1 if enabled else 0

        fb_cfg = app._config.setdefault("feedback", {})
        fb_cfg["visual_indicator"] = enabled
        self._save_and_reload()

    def show_device_name_toggle(self, enabled: bool) -> None:
        """Handle show device name toggle from Settings panel."""
        app = self._app
        app._recording_indicator.show_device_name = enabled
        app._recorder._query_device_name_enabled = enabled

        fb_cfg = app._config.setdefault("feedback", {})
        fb_cfg["show_device_name"] = enabled
        self._save_and_reload()

    def preview_toggle(self, enabled: bool) -> None:
        """Handle preview toggle from Settings panel."""
        app = self._app
        app._preview_enabled = enabled
        app._preview_item.state = 1 if enabled else 0

        app._config["output"]["preview"] = enabled
        self._save_and_reload()
        logger.info("Preview set to: %s (from settings)", enabled)

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
                # For Apple Speech, verify Siri/Dictation is enabled first
                if preset.backend == "apple":
                    from wenzi.transcription.apple import (
                        check_siri_available,
                        prompt_enable_siri,
                    )

                    app._set_status("Checking...")
                    ok, err = check_siri_available(
                        language=preset.language
                        or app._config.get("asr", {}).get("language", "zh"),
                        on_device=(preset.model == "on-device"),
                    )
                    if not ok:
                        logger.warning("Apple Speech preflight failed: %s", err)
                        prompt_enable_siri()
                        # Revert settings panel radio back to the previous model
                        from PyObjCTools import AppHelper

                        AppHelper.callAfter(
                            app._settings_panel.update_stt_model,
                            old_preset_id,
                            app._current_remote_asr,
                        )
                        app._set_status("WZ")
                        return

                app._set_status("Unloading...")
                old_transcriber.cleanup()

                cached = is_model_cached(preset)
                if not cached:
                    monitor_args = app._model_controller._make_download_monitor_args(preset)
                    monitor_thread = threading.Thread(
                        target=app._model_controller._monitor_download_progress,
                        args=(stop_event, monitor_args),
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
                    hotwords=app._load_hotwords(),
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
                self._save_and_reload()

                app._set_status("WZ")
                logger.info("Switched to model: %s (from settings)", preset.display_name)
                try:
                    send_notification("WenZi", "Model switched",
                                      f"Now using: {preset.display_name}")
                except Exception:
                    logger.debug("Notification unavailable, skipping")

            except Exception as e:
                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)
                logger.error("Model switch failed: %s", e)
                app._set_status("Error")

                can_clear = preset.backend not in ("apple", "whisper-api")
                if can_clear:
                    result = topmost_alert(
                        title="Model Switch Failed",
                        message=(
                            f"Failed to load model: {preset.display_name}\n\n"
                            f"Error: {str(e)[:200]}\n\n"
                            "This may be caused by corrupted cache files. "
                            "Click 'Clear Cache & Retry' to delete cached "
                            "files and try again."
                        ),
                        ok="Clear Cache & Retry",
                        cancel="Close",
                    )
                    restore_accessory()
                    if result == 1:
                        self._clear_cache_and_retry_switch(
                            preset, old_preset_id
                        )
                        return
                else:
                    topmost_alert(
                        title="Model Switch Failed",
                        message=(
                            f"Failed to load model: {preset.display_name}\n\n"
                            f"Error: {str(e)[:200]}"
                        ),
                    )
                    restore_accessory()

                app._model_controller._try_restore_previous_model(old_preset_id)
                self._revert_settings_panel_selection(old_preset_id)

            finally:
                app._busy = False

        threading.Thread(target=_do_switch, daemon=True).start()

    def _clear_cache_and_retry_switch(self, preset, old_preset_id) -> None:
        """Clear model cache and retry the switch (settings panel path)."""
        app = self._app
        stop_event = threading.Event()
        monitor_thread = None
        try:
            app._set_status("Clearing...")
            clear_model_cache(preset)

            monitor_args = app._model_controller._make_download_monitor_args(preset)
            monitor_thread = threading.Thread(
                target=app._model_controller._monitor_download_progress,
                args=(stop_event, monitor_args),
                daemon=True,
            )
            monitor_thread.start()

            asr_cfg = app._config["asr"]
            new_transcriber = create_transcriber(
                backend=preset.backend,
                use_vad=asr_cfg.get("use_vad", True),
                use_punc=asr_cfg.get("use_punc", True),
                language=preset.language or asr_cfg.get("language"),
                model=preset.model,
                temperature=asr_cfg.get("temperature"),
                hotwords=app._load_hotwords(),
            )
            new_transcriber.initialize()

            stop_event.set()
            monitor_thread.join(timeout=2)

            app._transcriber = new_transcriber
            app._current_preset_id = preset.id
            app._current_remote_asr = None
            app._menu_builder.update_model_checkmarks()

            app._config["asr"]["preset"] = preset.id
            app._config["asr"]["backend"] = preset.backend
            app._config["asr"]["model"] = preset.model
            app._config["asr"]["language"] = preset.language
            app._config["asr"]["default_provider"] = None
            app._config["asr"]["default_model"] = None
            self._save_and_reload()

            app._set_status("WZ")
            logger.info(
                "Model switched after cache clear: %s (from settings)",
                preset.display_name,
            )
        except Exception as e2:
            stop_event.set()
            if monitor_thread:
                monitor_thread.join(timeout=2)
            logger.error("Retry after cache clear failed: %s", e2)
            app._set_status("Error")
            topmost_alert(
                title="Model Switch Failed",
                message=(
                    f"Retry failed.\n\n"
                    f"Error: {str(e2)[:200]}\n\n"
                    "Please check your network connection and try again."
                ),
            )
            restore_accessory()
            app._model_controller._try_restore_previous_model(old_preset_id)
            self._revert_settings_panel_selection(old_preset_id)
        finally:
            app._busy = False

    def _revert_settings_panel_selection(self, old_preset_id) -> None:
        """Revert settings panel radio to the previous model after switch failure."""
        from PyObjCTools import AppHelper

        app = self._app
        AppHelper.callAfter(
            app._settings_panel.update_stt_model,
            old_preset_id,
            app._current_remote_asr,
        )

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
                    hotwords=app._load_hotwords(),
                )
                new_transcriber.initialize()

                app._transcriber = new_transcriber
                app._current_remote_asr = key
                app._current_preset_id = None
                app._menu_builder.update_model_checkmarks()

                app._config["asr"]["default_provider"] = provider
                app._config["asr"]["default_model"] = model
                self._save_and_reload()

                app._set_status("WZ")
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
        self._save_and_reload()
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

    def model_timeout_change(self, value: int) -> None:
        """Handle model timeout change from Settings panel."""
        app = self._app
        if app._enhancer:
            app._enhancer._connection_timeout = value
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"]["connection_timeout"] = value
        self._save_and_reload()
        logger.info("Model connection timeout set to: %ds (from settings)", value)

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
        self._save_and_reload()
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
        self._save_and_reload()
        logger.info("AI thinking set to: %s (from settings)", enabled)

    def input_context_change(self, level: str) -> None:
        """Handle input context level change from Settings panel."""
        app = self._app
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"]["input_context"] = level
        if app._enhancer:
            app._enhancer.input_context_level = level
        save_config(app._config, app._config_path)
        logger.info("Input context level set to: %s (from settings)", level)

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
        self._save_and_reload()
        logger.info("Vocabulary set to: %s (from settings)", enabled)

    def _get_vocab_build_model(self, llm_models) -> tuple | None:
        """Return the current (provider, model) for vocab building, or None for default."""
        app = self._app
        vocab_cfg = app._config.get("ai_enhance", {}).get("vocabulary", {})
        bp = vocab_cfg.get("build_provider", "")
        bm = vocab_cfg.get("build_model", "")
        if bp and bm:
            # Verify the pair still exists in available models
            for provider, model, _ in llm_models:
                if provider == bp and model == bm:
                    return (bp, bm)
        return None

    def vocab_build_model_select(self, provider: str, model: str) -> None:
        """Handle vocab build model selection from Settings panel."""
        app = self._app
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"].setdefault("vocabulary", {})
        app._config["ai_enhance"]["vocabulary"]["build_provider"] = provider
        app._config["ai_enhance"]["vocabulary"]["build_model"] = model
        self._save_and_reload()
        logger.info("Vocabulary build model set to: %s / %s (from settings)", provider or "default", model or "default")

    def auto_build_toggle(self, enabled: bool) -> None:
        """Handle auto build toggle from Settings panel."""
        app = self._app
        app._auto_vocab_builder._enabled = enabled
        app._enhance_auto_build_item.state = 1 if enabled else 0

        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"].setdefault("vocabulary", {})
        app._config["ai_enhance"]["vocabulary"]["auto_build"] = enabled
        self._save_and_reload()
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
        self._save_and_reload()
        logger.info("Conversation history set to: %s (from settings)", enabled)

    def history_max_entries_change(self, value: int) -> None:
        """Handle history max_entries change from Settings panel."""
        app = self._app
        if not app._enhancer:
            return
        app._enhancer.history_max_entries = value
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"].setdefault("conversation_history", {})
        app._config["ai_enhance"]["conversation_history"]["max_entries"] = value
        self._save_and_reload()
        logger.info("History max_entries set to: %d (from settings)", value)

    def history_refresh_threshold_change(self, value: int) -> None:
        """Handle history refresh_threshold change from Settings panel."""
        app = self._app
        if not app._enhancer:
            return
        app._enhancer.history_refresh_threshold = value
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"].setdefault("conversation_history", {})
        app._config["ai_enhance"]["conversation_history"]["refresh_threshold"] = value
        self._save_and_reload()
        logger.info("History refresh_threshold set to: %d (from settings)", value)

    def reveal_config_folder(self) -> None:
        """Open the config directory in Finder."""
        import subprocess

        subprocess.Popen(["open", self._app._config_dir])

    def tab_change(self, tab_id: str) -> None:
        """Persist the last active settings tab."""
        app = self._app
        app._config.setdefault("ui", {})["settings_last_tab"] = tab_id
        self._save_and_reload()

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

        from wenzi.config import save_config_dir_preference

        save_config_dir_preference(new_dir)
        app._settings_panel.update_config_dir(new_dir)
        logger.info("Config directory preference set to: %s", new_dir)

        self._prompt_restart(
            f"Config directory changed to:\n{new_dir}\n\n"
            "A restart is required for this to take effect."
        )

    def config_dir_reset(self) -> None:
        """Reset config directory to default."""
        from wenzi.config import DEFAULT_CONFIG_DIR, reset_config_dir_preference

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
        """Restart the application."""
        from wenzi.statusbar import restart_application
        restart_application()

    # ── Launcher tab ─────────────────────────────────────────────────

    def _build_launcher_state(self) -> dict:
        """Build launcher state dict for the settings panel."""
        app = self._app
        chooser_cfg = app._config.get("scripting", {}).get("chooser", {})
        return {
            "enabled": chooser_cfg.get("enabled", True),
            "hotkey": chooser_cfg.get("hotkey", "cmd+space"),
            "app_search": chooser_cfg.get("app_search", True),
            "clipboard_history": chooser_cfg.get("clipboard_history", True),
            "file_search": chooser_cfg.get("file_search", True),
            "snippets": chooser_cfg.get("snippets", True),
            "bookmarks": chooser_cfg.get("bookmarks", True),
            "usage_learning": chooser_cfg.get("usage_learning", True),
            "prefixes": chooser_cfg.get("prefixes", {
                "clipboard": "cb",
                "files": "f",
                "snippets": "sn",
                "bookmarks": "bm",
            }),
            "source_hotkeys": chooser_cfg.get("source_hotkeys", {
                "clipboard": "",
                "files": "",
                "snippets": "",
                "bookmarks": "",
            }),
            "new_snippet_hotkey": chooser_cfg.get("new_snippet_hotkey", ""),
            "switch_to_english": chooser_cfg.get("switch_to_english", True),
        }

    def launcher_toggle(self, enabled: bool) -> None:
        """Handle launcher enable/disable toggle from Settings panel."""
        app = self._app
        chooser_cfg = app._config.setdefault("scripting", {}).setdefault(
            "chooser", {}
        )
        chooser_cfg["enabled"] = enabled
        self._save_and_reload()

        engine = getattr(app, "_script_engine", None)
        if engine is not None:
            if enabled:
                engine.enable_chooser()
            else:
                engine.disable_chooser()

        logger.info("Launcher set to: %s", enabled)

    def launcher_hotkey_record(self) -> None:
        """Record a new launcher hotkey via modal dialog."""
        app = self._app
        recorded_key = app.record_combo_hotkey_modal()
        if not recorded_key:
            return

        chooser_cfg = app._config.setdefault("scripting", {}).setdefault(
            "chooser", {}
        )
        old_hotkey = chooser_cfg.get("hotkey", "")
        chooser_cfg["hotkey"] = recorded_key
        self._save_and_reload()

        engine = getattr(app, "_script_engine", None)
        if engine is not None and chooser_cfg.get("enabled", True):
            engine.rebind_chooser_hotkey(old_hotkey, recorded_key)

        app._settings_panel.update_launcher_hotkey(recorded_key)
        logger.info("Launcher hotkey recorded: %s", recorded_key)

    def launcher_hotkey_clear(self) -> None:
        """Clear the launcher hotkey."""
        app = self._app
        chooser_cfg = app._config.setdefault("scripting", {}).setdefault(
            "chooser", {}
        )
        old_hotkey = chooser_cfg.get("hotkey", "")
        chooser_cfg["hotkey"] = ""
        self._save_and_reload()

        if old_hotkey:
            engine = getattr(app, "_script_engine", None)
            if engine is not None:
                engine.rebind_chooser_hotkey(old_hotkey, "")

        app._settings_panel.update_launcher_hotkey("")
        logger.info("Launcher hotkey cleared")

    def launcher_source_toggle(self, config_key: str, enabled: bool) -> None:
        """Handle launcher source toggle from Settings panel."""
        app = self._app
        chooser_cfg = app._config.setdefault("scripting", {}).setdefault(
            "chooser", {}
        )
        chooser_cfg[config_key] = enabled
        self._save_and_reload()

        engine = getattr(app, "_script_engine", None)
        if engine is not None and chooser_cfg.get("enabled", True):
            if enabled:
                engine.enable_source(config_key)
            else:
                engine.disable_source(config_key)

        logger.info("Launcher source %s set to: %s", config_key, enabled)

    def launcher_prefix_change(self, prefix_key: str, value: str) -> None:
        """Handle launcher prefix change from Settings panel."""
        app = self._app
        chooser_cfg = app._config.setdefault("scripting", {}).setdefault(
            "chooser", {}
        )
        prefixes = chooser_cfg.setdefault("prefixes", {})
        old_value = prefixes.get(prefix_key, "")
        prefixes[prefix_key] = value
        self._save_and_reload()

        # Re-register the source with the new prefix
        engine = getattr(app, "_script_engine", None)
        source_config_map = {
            "clipboard": "clipboard_history",
            "files": "file_search",
            "snippets": "snippets",
            "bookmarks": "bookmarks",
        }
        config_key = source_config_map.get(prefix_key)
        if (
            engine is not None
            and config_key
            and chooser_cfg.get("enabled", True)
            and chooser_cfg.get(config_key, True)
            and old_value != value
        ):
            engine.disable_source(config_key)
            engine.enable_source(config_key)

        logger.info("Launcher prefix %s set to: %r", prefix_key, value)

    def launcher_refresh_icons(self) -> None:
        """Clear all cached icons and re-extract them."""
        import shutil

        # Clear app icon disk cache
        from wenzi.config import DEFAULT_ICON_CACHE_DIR
        icon_cache_dir = os.path.expanduser(DEFAULT_ICON_CACHE_DIR)
        if os.path.isdir(icon_cache_dir):
            shutil.rmtree(icon_cache_dir, ignore_errors=True)
            logger.info("Cleared app icon cache: %s", icon_cache_dir)

        # Clear browser icon in-memory cache
        try:
            from wenzi.scripting.sources.bookmark_source import (
                _browser_icon_cache,
            )

            _browser_icon_cache.clear()
            logger.info("Cleared browser icon memory cache")
        except ImportError:
            pass

        # Force app source rescan so icons are re-extracted
        app = self._app
        scripting_cfg = app._config.get("scripting", {})
        if scripting_cfg.get("enabled") and hasattr(app, "_script_engine"):
            try:
                engine = app._script_engine
                # Find the app source and trigger rescan
                panel = engine.wz.chooser._get_panel()
                for src in panel._sources.values():
                    if src.name == "apps" and hasattr(src, "search"):
                        # The search function is bound to AppSource
                        # We can't easily access it, but clearing disk
                        # cache is enough — next search will re-extract
                        pass
            except Exception:
                logger.debug("Could not trigger app rescan", exc_info=True)

        topmost_alert(
            title="Icon Cache Cleared",
            message="App and browser icon caches have been cleared. "
            "Icons will be re-extracted on next search.",
        )
        restore_accessory()
        logger.info("Icon cache refresh completed")

    def launcher_source_hotkey_record(self, source_key: str) -> None:
        """Record a combo hotkey for a specific data source."""
        app = self._app
        recorded_key = app.record_combo_hotkey_modal()
        if recorded_key:
            chooser_cfg = app._config.setdefault("scripting", {}).setdefault(
                "chooser", {}
            )
            source_hotkeys = chooser_cfg.setdefault("source_hotkeys", {})
            source_hotkeys[source_key] = recorded_key
            self._save_and_reload()

            # Dynamically bind the new hotkey
            prefixes = chooser_cfg.get("prefixes", {})
            prefix = prefixes.get(source_key, "")
            if prefix and hasattr(app, "_script_engine"):
                app._script_engine.wz.hotkey.bind(
                    recorded_key,
                    lambda p=prefix: app._script_engine.wz.chooser.show_source(p),
                )
                app._script_engine.wz.hotkey.start()

            app._settings_panel.update_source_hotkey(source_key, recorded_key)
            logger.info(
                "Source hotkey recorded: %s -> %s", source_key, recorded_key,
            )

    def launcher_source_hotkey_clear(self, source_key: str) -> None:
        """Clear the hotkey for a specific data source."""
        app = self._app
        chooser_cfg = app._config.setdefault("scripting", {}).setdefault(
            "chooser", {}
        )
        source_hotkeys = chooser_cfg.setdefault("source_hotkeys", {})
        old_hotkey = source_hotkeys.get(source_key, "")
        source_hotkeys[source_key] = ""
        self._save_and_reload()

        # Unbind the old hotkey
        if old_hotkey and hasattr(app, "_script_engine"):
            app._script_engine.wz.hotkey.unbind(old_hotkey)

        app._settings_panel.update_source_hotkey(source_key, "")
        logger.info("Source hotkey cleared: %s", source_key)

    def new_snippet_hotkey_record(self) -> None:
        """Record a combo hotkey for New Snippet."""
        app = self._app
        recorded_key = app.record_combo_hotkey_modal()
        if recorded_key:
            chooser_cfg = app._config.setdefault("scripting", {}).setdefault(
                "chooser", {}
            )
            old_hotkey = chooser_cfg.get("new_snippet_hotkey", "")
            chooser_cfg["new_snippet_hotkey"] = recorded_key
            self._save_and_reload()

            if hasattr(app, "_script_engine"):
                app._script_engine.rebind_new_snippet_hotkey(
                    old_hotkey, recorded_key,
                )

            app._settings_panel.update_new_snippet_hotkey(recorded_key)
            logger.info("New snippet hotkey recorded: %s", recorded_key)

    def new_snippet_hotkey_clear(self) -> None:
        """Clear the New Snippet hotkey."""
        app = self._app
        chooser_cfg = app._config.setdefault("scripting", {}).setdefault(
            "chooser", {}
        )
        old_hotkey = chooser_cfg.get("new_snippet_hotkey", "")
        chooser_cfg["new_snippet_hotkey"] = ""
        self._save_and_reload()

        if old_hotkey and hasattr(app, "_script_engine"):
            app._script_engine.wz.hotkey.unbind(old_hotkey)

        app._settings_panel.update_new_snippet_hotkey("")
        logger.info("New snippet hotkey cleared")

    def launcher_usage_learning_toggle(self, enabled: bool) -> None:
        """Handle launcher usage learning toggle from Settings panel."""
        app = self._app
        chooser_cfg = app._config.setdefault("scripting", {}).setdefault(
            "chooser", {}
        )
        chooser_cfg["usage_learning"] = enabled
        self._save_and_reload()

        engine = getattr(app, "_script_engine", None)
        if engine is not None and chooser_cfg.get("enabled", True):
            engine.set_usage_learning(enabled)

        logger.info("Launcher usage learning set to: %s", enabled)

    def launcher_switch_english_toggle(self, enabled: bool) -> None:
        """Handle launcher switch-to-English toggle from Settings panel."""
        app = self._app
        chooser_cfg = app._config.setdefault("scripting", {}).setdefault(
            "chooser", {}
        )
        chooser_cfg["switch_to_english"] = enabled
        self._save_and_reload()

        engine = getattr(app, "_script_engine", None)
        if engine is not None:
            engine.wz.chooser._get_panel()._switch_english = enabled

        logger.info("Launcher switch-to-English set to: %s", enabled)
