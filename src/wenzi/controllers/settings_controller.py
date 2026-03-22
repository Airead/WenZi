"""Settings panel callbacks extracted from WenZiApp."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import webbrowser
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wenzi.app import WenZiApp

from wenzi import get_version
from wenzi.config import BUILTIN_REGISTRY_URL, save_config
from wenzi.enhance.enhancer import MODE_OFF
from wenzi.i18n import build_doc_url, t
from wenzi.transcription.model_registry import (
    PRESET_BY_ID,
    PRESETS,
    build_remote_asr_models,
    clear_model_cache,
    is_backend_available,
    is_model_cached,
)
from wenzi.scripting.plugin_installer import PluginInstaller
from wenzi.scripting.plugin_registry import PluginInfo, PluginRegistry, PluginStatus
from wenzi.statusbar import send_notification
from wenzi.transcription.base import create_transcriber
from wenzi.ui_helpers import restore_accessory, topmost_alert

logger = logging.getLogger(__name__)

# Launcher data source definitions: (config_key, i18n_label_key, prefix_key)
_LAUNCHER_SOURCE_DEFS = [
    ("app_search", "applications", None),
    ("clipboard_history", "clipboard_history", "clipboard"),
    ("file_search", "file_search", "files"),
    ("snippets", "snippets", "snippets"),
    ("bookmarks", "bookmarks", "bookmarks"),
]


class SettingsController:
    """Handles all Settings panel callbacks."""

    def __init__(self, app: WenZiApp) -> None:
        self._app = app
        plugins_dir = os.path.join(app._config_dir, "plugins")
        self._plugin_registry = PluginRegistry(plugins_dir=plugins_dir)
        self._plugin_installer = PluginInstaller(plugins_dir=plugins_dir)
        self._registry_cache_dir = os.path.join(app._config_dir, "registry_cache")
        self._needs_reload = False
        self._last_plugin_infos: list[PluginInfo] = []

    def _save_and_reload(self) -> None:
        """Save config and refresh the Settings panel if visible."""
        from PyObjCTools import AppHelper

        app = self._app
        save_config(app._config, app._config_path)
        if app._settings_panel.is_visible:
            AppHelper.callAfter(self._refresh_panel)

    def _refresh_panel(self) -> None:
        """Push updated state to the settings panel (incremental refresh)."""
        state = self._collect_state()
        self._app._settings_panel.update_state(state)

    def _collect_state(self) -> dict:
        """Build the current state dict for the Settings panel."""
        from wenzi.enhance.vocabulary import get_vocab_entry_count

        app = self._app

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
        ai_providers_cfg = app._config.get("ai_enhance", {}).get("providers", {})
        if app._enhancer:
            for pname, models in app._enhancer.providers_with_models.items():
                pcfg = ai_providers_cfg.get(pname, {})
                # All models from a provider share the same API key config
                has_api_key = bool(pcfg.get("api_key"))
                for mname in models:
                    llm_models.append(
                        (pname, mname, f"{pname} / {mname}", has_api_key)
                    )
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
        return {
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
            "language": app._config.get("language", "auto"),
            "launcher": self._build_launcher_state(),
        }

    def on_open_settings(self, _) -> None:
        """Open the Settings panel with current state and callbacks."""
        app = self._app
        state = self._collect_state()

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
            "on_language_change": self.language_change,
            "on_plugins_tab_open": self._on_plugins_tab_open,
            "on_plugin_install_by_id": self._on_plugin_install_by_id,
            "on_plugin_install_url": self._on_plugin_install_url,
            "on_plugin_update": self._on_plugin_update,
            "on_plugin_uninstall": self._on_plugin_uninstall,
            "on_plugin_toggle": self._on_plugin_toggle,
            "on_plugin_reload": self._on_plugin_reload,
            "on_registry_add": self._on_registry_add,
            "on_registry_remove": self._on_registry_remove,
            "on_open_doc": self.open_doc_link,
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

    def language_change(self, lang_value: str) -> None:
        """Handle language change from settings UI."""
        app = self._app
        app._config["language"] = lang_value
        save_config(app._config, app._config_path)
        topmost_alert(
            title=t("settings.general_tab.language_restart_title"),
            message=t("settings.general_tab.language_restart_message"),
        )
        restore_accessory()

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
                t("alert.settings.cannot_switch"),
                t("alert.settings.cannot_switch.message"),
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

                    app._set_status("statusbar.status.checking")
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
                        app._set_status("statusbar.status.ready")
                        return

                app._set_status("statusbar.status.unloading")
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
                    app._set_status("statusbar.status.loading")

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

                app._set_status("statusbar.status.ready")
                logger.info("Switched to model: %s (from settings)", preset.display_name)
                try:
                    send_notification(t("app.name"), t("notification.model.switched"),
                                      t("notification.model.switched.subtitle", name=preset.display_name))
                except Exception:
                    logger.debug("Notification unavailable, skipping")

            except Exception as e:
                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)
                logger.error("Model switch failed: %s", e)
                app._set_status("statusbar.status.error")

                can_clear = preset.backend not in ("apple", "whisper-api")
                if can_clear:
                    result = topmost_alert(
                        title=t("alert.model.switch_failed.title"),
                        message=t("alert.model.switch_failed.cache_message",
                                  name=preset.display_name, error=str(e)[:200]),
                        ok=t("alert.model.cache_retry"),
                        cancel=t("common.close"),
                    )
                    restore_accessory()
                    if result == 1:
                        self._clear_cache_and_retry_switch(
                            preset, old_preset_id
                        )
                        return
                else:
                    topmost_alert(
                        title=t("alert.model.switch_failed.title"),
                        message=t("alert.model.switch_failed.message",
                                  name=preset.display_name, error=str(e)[:200]),
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
            app._set_status("statusbar.status.clearing")
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

            app._set_status("statusbar.status.ready")
            logger.info(
                "Model switched after cache clear: %s (from settings)",
                preset.display_name,
            )
        except Exception as e2:
            stop_event.set()
            if monitor_thread:
                monitor_thread.join(timeout=2)
            logger.error("Retry after cache clear failed: %s", e2)
            app._set_status("statusbar.status.error")
            topmost_alert(
                title=t("alert.model.switch_failed.title"),
                message=t("alert.model.switch_failed.retry_message", error=str(e2)[:200]),
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
                t("alert.settings.cannot_switch"),
                t("alert.settings.cannot_switch.message"),
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
                app._set_status("statusbar.status.switching")
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

                app._set_status("statusbar.status.ready")
                logger.info("Switched to remote ASR: %s / %s (from settings)",
                            provider, model)
            except Exception as e:
                logger.error("Remote ASR switch failed: %s", e)
                app._set_status("statusbar.status.error")
            finally:
                app._busy = False

        threading.Thread(target=_do_switch, daemon=True).start()

    def stt_remove_provider(self, provider: str = "") -> None:
        """Handle STT remove provider from Settings panel."""
        app = self._app
        if provider:
            item = app._asr_remove_provider_items.get(provider)
            if item:
                app._model_controller.on_asr_remove_provider(item)
                return
        # Fallback: remove first provider if no name given
        asr_cfg = app._config.get("asr", {})
        providers = asr_cfg.get("providers", {})
        if providers:
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

    def llm_remove_provider(self, provider: str = "") -> None:
        """Handle LLM remove provider from Settings panel."""
        app = self._app
        if provider:
            item = app._llm_remove_provider_items.get(provider)
            if item:
                app._model_controller.on_enhance_remove_provider(item)
                return
        # Fallback: remove first provider if no name given
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
        self._save_and_reload()
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
            for provider, model, *_ in llm_models:
                if provider == bp and model == bm:
                    return (bp, bm)
        return None

    def vocab_build_model_select(self, value: str) -> None:
        """Handle vocab build model selection from Settings panel.

        Args:
            value: Combined "provider/model" string from the webview select.
        """
        if "/" in value:
            provider, model = value.split("/", 1)
        else:
            provider, model = value, ""
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

    def open_doc_link(self, path: str) -> None:
        """Open a documentation page in the default browser."""
        try:
            webbrowser.open(build_doc_url(path))
        except Exception as e:
            logger.error("Failed to open doc URL: %s", e)

    def reveal_config_folder(self) -> None:
        """Open the config directory in Finder."""
        import subprocess

        subprocess.Popen(["open", self._app._config_dir])

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
            title=t("alert.settings.restart_required.title"),
            message=message,
            ok=t("alert.update.restart_now"),
            cancel=t("common.later"),
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
        prefixes = chooser_cfg.get("prefixes", {
            "clipboard": "cb",
            "files": "f",
            "snippets": "sn",
            "bookmarks": "bm",
        })
        source_hotkeys = chooser_cfg.get("source_hotkeys", {})

        sources = []
        for config_key, label_key, prefix_key in _LAUNCHER_SOURCE_DEFS:
            sources.append({
                "config_key": config_key,
                "label_key": label_key,
                "enabled": chooser_cfg.get(config_key, True),
                "prefix_key": prefix_key,
                "prefix": prefixes.get(prefix_key, "") if prefix_key else "",
                "hotkey": source_hotkeys.get(prefix_key, "") if prefix_key else "",
            })

        # Collect registered chooser sources from scripting registry
        registered_sources = []
        engine = getattr(app, "_script_engine", None)
        if engine is not None:
            registry = getattr(engine, "_registry", None)
            if registry is not None:
                for name, src in registry.chooser_sources.items():
                    registered_sources.append({
                        "name": name,
                        "prefix": getattr(src, "prefix", ""),
                    })

        return {
            "enabled": chooser_cfg.get("enabled", True),
            "hotkey": chooser_cfg.get("hotkey", ""),
            "usage_learning": chooser_cfg.get("usage_learning", True),
            "switch_english": chooser_cfg.get("switch_to_english", True),
            "new_snippet_hotkey": chooser_cfg.get("new_snippet_hotkey", ""),
            "sources": sources,
            "registered_sources": registered_sources,
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
            title=t("alert.settings.icon_cache_cleared.title"),
            message=t("alert.settings.icon_cache_cleared.message"),
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

    # ---------------------------------------------------------------------------
    # Plugin management callbacks
    # ---------------------------------------------------------------------------

    def _on_plugins_tab_open(self) -> None:
        """Show cached/local plugins immediately, then fetch fresh registries."""
        from PyObjCTools import AppHelper

        app = self._app
        panel = app._settings_panel
        self._update_registries_state()

        # Immediate render from cache + local scan
        extra = app._config.get("plugins", {}).get("extra_registries", [])
        current_ver = get_version()
        cached_official, cached_extras = self._get_cached_registry_paths(extra)
        if cached_official:
            try:
                infos = self._plugin_registry.merge_registries(
                    official_source=cached_official,
                    extra_sources=cached_extras,
                    current_wenzi_version=current_ver,
                )
                self._last_plugin_infos = infos
            except Exception:
                logger.debug("Failed to load cached registries", exc_info=True)
        # Render whatever we have (cached + local, or local-only)
        self._finish_plugins_fetch(loading=True)

        # Background fetch for fresh data
        def _fetch():
            try:
                from wenzi.scripting.plugin_meta import read_source

                os.makedirs(self._registry_cache_dir, exist_ok=True)
                # Fetch and cache each registry
                sources = [(BUILTIN_REGISTRY_URL, "official.toml")]
                for i, url in enumerate(extra):
                    sources.append((url, f"extra_{i}.toml"))
                for url, fname in sources:
                    raw = read_source(url)
                    cache_path = os.path.join(self._registry_cache_dir, fname)
                    tmp = cache_path + ".tmp"
                    with open(tmp, "wb") as f:
                        f.write(raw)
                    os.replace(tmp, cache_path)

                # Re-merge from fresh cache
                cached_official, cached_extras = self._get_cached_registry_paths(extra)
                infos = self._plugin_registry.merge_registries(
                    official_source=cached_official or BUILTIN_REGISTRY_URL,
                    extra_sources=cached_extras,
                    current_wenzi_version=current_ver,
                )
                self._last_plugin_infos = infos
                AppHelper.callAfter(self._finish_plugins_fetch)
            except Exception as e:
                logger.error("Failed to fetch plugins", exc_info=True)
                AppHelper.callAfter(
                    panel.update_state,
                    {"plugins_loading": False, "plugins_error": str(e)},
                )

        threading.Thread(target=_fetch, daemon=True).start()

    def _get_cached_registry_paths(
        self, extra_urls: list[str],
    ) -> tuple[str | None, list[str]]:
        """Return (cached_official, cached_extras) paths if they exist."""
        official = os.path.join(self._registry_cache_dir, "official.toml")
        cached_official = official if os.path.isfile(official) else None
        cached_extras = []
        for i in range(len(extra_urls)):
            p = os.path.join(self._registry_cache_dir, f"extra_{i}.toml")
            if os.path.isfile(p):
                cached_extras.append(p)
        return cached_official, cached_extras

    def _on_plugin_install_by_id(
        self, plugin_id: str, ref: str | None = None
    ) -> None:
        """Install a plugin by its registry ID, optionally at a specific ref."""
        source_url = None
        for info in self._last_plugin_infos:
            if info.meta.id == plugin_id:
                source_url = info.source_url
                break
        if not source_url:
            self._app._settings_panel.update_state(
                {"plugins_error": f"Plugin {plugin_id} not found in registries"}
            )
            return
        if ref:
            from wenzi.scripting.plugin_installer import (
                replace_github_ref,
                resolve_ref,
            )

            try:
                download_url = replace_github_ref(source_url, resolve_ref(ref))
            except ValueError as e:
                self._app._settings_panel.update_state(
                    {"plugins_error": f"Invalid ref: {e}"}
                )
                return
            self._on_plugin_install_url(download_url, pinned_ref=ref, plugin_id=plugin_id)
        else:
            self._on_plugin_install_url(source_url, plugin_id=plugin_id)

    def _on_plugin_install_url(
        self, url: str, pinned_ref: str | None = None, plugin_id: str = "",
    ) -> None:
        """Install a plugin from a URL in background."""
        self._run_plugin_op(
            plugin_id,
            lambda progress: self._plugin_installer.install(
                url, pinned_ref=pinned_ref, progress=progress,
            ),
            label="Install",
        )

    def _on_plugin_update(self, plugin_id: str) -> None:
        """Update an installed plugin in background."""
        self._run_plugin_op(
            plugin_id,
            lambda progress: self._plugin_installer.update(plugin_id, progress=progress),
            label="Update",
        )

    def _run_plugin_op(
        self,
        plugin_id: str,
        op: Callable[..., None],
        label: str,
    ) -> None:
        """Run a plugin install/update operation in a background thread with progress."""
        from PyObjCTools import AppHelper

        panel = self._app._settings_panel
        action = label.lower()  # "install" or "update"

        def _progress(current: int, total: int) -> None:
            AppHelper.callAfter(
                panel.update_state,
                {"plugin_progress": {
                    "id": plugin_id, "current": current,
                    "total": total, "action": action,
                }},
            )

        def _do_op():
            try:
                AppHelper.callAfter(
                    panel.update_state,
                    {"plugin_progress": {
                        "id": plugin_id, "current": 0,
                        "total": 0, "action": action,
                    }},
                )
                op(progress=_progress)
                self._needs_reload = True
                AppHelper.callAfter(
                    panel.update_state, {"plugin_progress": None},
                )
                AppHelper.callAfter(self._auto_reload_if_needed)
            except Exception as e:
                AppHelper.callAfter(
                    panel.update_state,
                    {"plugins_error": f"{label} failed: {e}", "plugin_progress": None},
                )

        threading.Thread(target=_do_op, daemon=True).start()

    def _on_plugin_uninstall(self, plugin_id: str) -> None:
        """Uninstall a plugin."""
        from PyObjCTools import AppHelper

        try:
            self._plugin_installer.uninstall(plugin_id)
            self._needs_reload = True
            AppHelper.callAfter(self._auto_reload_if_needed)
        except Exception as e:
            self._app._settings_panel.update_state(
                {"plugins_error": f"Uninstall failed: {e}"}
            )

    def _on_plugin_toggle(self, plugin_id: str, enabled: bool) -> None:
        """Enable or disable a plugin."""
        from PyObjCTools import AppHelper

        scripting = self._app._config.setdefault("scripting", {})
        disabled = list(scripting.get("disabled_plugins", []))
        changed = False
        if enabled and plugin_id in disabled:
            disabled.remove(plugin_id)
            changed = True
        elif not enabled and plugin_id not in disabled:
            disabled.append(plugin_id)
            changed = True
        if not changed:
            return
        scripting["disabled_plugins"] = disabled
        self._save_and_reload()
        self._needs_reload = True
        AppHelper.callAfter(self._auto_reload_if_needed)

    def _on_plugin_reload(self) -> None:
        """Reload all plugins in the script engine."""
        app = self._app
        engine = getattr(app, "_script_engine", None)
        if engine is not None:
            engine.reload()
        self._needs_reload = False
        app._settings_panel.update_state(
            {"show_reload_banner": False, "plugins_error": None}
        )
        self._refresh_plugin_state()

    def _auto_reload_if_needed(self) -> None:
        """Auto-reload plugins if a reload is pending."""
        if not self._needs_reload:
            return
        self._on_plugin_reload()

    def _finish_plugins_fetch(self, *, loading: bool = False) -> None:
        """Recompute plugin statuses on main thread after background fetch.

        Local-dir scanning must happen here (not in the fetch thread) to
        avoid a race where stale data overwrites fresher state from
        ``_refresh_plugin_state``.
        """
        if self._last_plugin_infos:
            self._recompute_plugin_statuses()
        plugins_data = self._plugin_infos_to_state(self._last_plugin_infos or [])
        self._app._settings_panel.update_state(
            {"plugins": plugins_data, "plugins_loading": loading}
        )

    def _recompute_plugin_statuses(self) -> None:
        """Update statuses in ``_last_plugin_infos`` from a fresh local scan."""
        local_index = self._plugin_registry._build_local_index()
        for info in self._last_plugin_infos:
            status, installed_ver = self._plugin_registry._compute_status(
                info.meta.id,
                info.meta.version,
                info.meta.min_wenzi_version,
                get_version(),
                local_index,
            )
            info.status = status
            info.installed_version = installed_ver

    def _refresh_plugin_state(self) -> None:
        """Recompute plugin statuses from cached registry data + fresh local scan.

        Unlike ``_on_plugins_tab_open``, this does NOT re-fetch registries from
        the network — it only re-scans the local plugins directory to update
        installation statuses.
        """
        if not self._last_plugin_infos:
            self._on_plugins_tab_open()
            return
        self._recompute_plugin_statuses()
        plugins_data = self._plugin_infos_to_state(self._last_plugin_infos)
        self._app._settings_panel.update_state({"plugins": plugins_data})


    def _on_registry_add(self, url: str) -> None:
        """Add an extra plugin registry URL."""
        plugins_cfg = self._app._config.setdefault("plugins", {})
        extra = plugins_cfg.setdefault("extra_registries", [])
        if url not in extra:
            extra.append(url)
            self._save_and_reload()
        self._update_registries_state()
        self._on_plugins_tab_open()

    def _on_registry_remove(self, index: int) -> None:
        """Remove an extra plugin registry by index."""
        extra = self._app._config.get("plugins", {}).get("extra_registries", [])
        # index may be passed as float from JS, convert to int
        index = int(index)
        if 0 <= index < len(extra):
            extra.pop(index)
            self._save_and_reload()
            self._clear_extra_registry_cache()
        self._update_registries_state()
        self._on_plugins_tab_open()

    def _clear_extra_registry_cache(self) -> None:
        """Remove all cached extra_*.toml files (re-fetched on next tab open)."""
        import glob

        for path in glob.glob(os.path.join(self._registry_cache_dir, "extra_*.toml")):
            try:
                os.remove(path)
            except OSError:
                pass

    def _update_registries_state(self) -> None:
        """Push current registry list to the settings panel."""
        extra = self._app._config.get("plugins", {}).get("extra_registries", [])
        registries = [{"name": "WenZi Official", "removable": False}]
        for url in extra:
            registries.append({"name": url, "removable": True})
        self._app._settings_panel.update_state({"registries": registries})

    def _get_load_errors_by_id(self) -> dict[str, dict[str, str]]:
        """Return plugin load errors keyed by plugin ID."""
        engine = getattr(self._app, "_script_engine", None)
        if engine is None:
            return {}
        return engine.get_load_errors_by_id()

    @staticmethod
    def _error_fields(load_errors: dict, pid: str) -> dict:
        """Extract load_error/load_traceback fields for a plugin state dict."""
        err = load_errors.get(pid, {})
        return {
            "load_error": err.get("message", ""),
            "load_traceback": err.get("traceback", ""),
        }

    def _plugin_infos_to_state(self, infos: list[PluginInfo]) -> list[dict]:
        """Convert PluginInfo list to serialisable state dicts for the UI."""
        from wenzi.scripting.plugin_meta import load_install_info, scan_local_plugins

        scripting = self._app._config.get("scripting", {})
        disabled = set(scripting.get("disabled_plugins", []))
        load_errors = self._get_load_errors_by_id()

        # Scan once — build both pinned_ref index and local plugin data
        local_scan = scan_local_plugins(self._plugin_registry.plugins_dir)
        pinned_index: dict[str, str] = {}
        install_info_cache: dict[str, dict | None] = {}
        for _name, path, meta in local_scan:
            if meta.id:
                ii = load_install_info(path)
                install_info_cache[meta.id] = ii
                if ii and ii.get("pinned_ref"):
                    pinned_index[meta.id] = ii["pinned_ref"]

        result = []
        for info in infos:
            pid = info.meta.id
            is_enabled = pid not in disabled
            result.append(
                {
                    "id": pid,
                    "name": info.meta.name,
                    "version": info.meta.version,
                    "author": info.meta.author,
                    "description": info.meta.description,
                    "min_wenzi_version": info.meta.min_wenzi_version,
                    "source_url": info.source_url,
                    "registry_name": info.registry_name,
                    "status": info.status.value,
                    "installed_version": info.installed_version or "",
                    "is_official": info.is_official,
                    "enabled": is_enabled,
                    "pinned_ref": pinned_index.get(pid, ""),
                    **self._error_fields(load_errors, pid),
                }
            )
        self._add_local_only_plugins(
            result, disabled, load_errors, local_scan, install_info_cache,
        )
        return result

    def _add_local_only_plugins(
        self, result: list[dict], disabled: set, load_errors: dict[str, dict[str, str]],
        local_scan: list, install_info_cache: dict[str, dict | None],
    ) -> None:
        """Append locally-installed plugins that don't appear in any registry."""
        known_ids = {p["id"] for p in result}
        for entry, _entry_path, meta in local_scan:
            pid = meta.id or entry
            if pid in known_ids:
                continue
            is_enabled = pid not in disabled and entry not in disabled
            ii = install_info_cache.get(pid)
            has_install = ii is not None
            status = (
                PluginStatus.INSTALLED if has_install else PluginStatus.MANUALLY_PLACED
            )
            result.append(
                {
                    "id": pid,
                    "name": meta.name,
                    "version": meta.version,
                    "author": meta.author,
                    "description": meta.description,
                    "min_wenzi_version": meta.min_wenzi_version,
                    "source_url": "",
                    "registry_name": "Local",
                    "status": status.value,
                    "installed_version": meta.version,
                    "is_official": False,
                    "enabled": is_enabled,
                    "pinned_ref": (ii.get("pinned_ref", "") if ii else ""),
                    **self._error_fields(load_errors, pid),
                }
            )
