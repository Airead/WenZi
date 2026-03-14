"""Menu building logic extracted from VoiceTextApp."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict

if TYPE_CHECKING:
    from voicetext.app import VoiceTextApp

from voicetext.statusbar import StatusMenuItem

logger = logging.getLogger(__name__)


class MenuBuilder:
    """Builds and rebuilds menu structures for VoiceTextApp.

    Holds a reference to the app to access config, enhancer, and other state.
    """

    def __init__(self, app: VoiceTextApp) -> None:
        self._app = app

    def build_hotkey_menu(self) -> None:
        """(Re)build the Hotkey submenu from config."""
        app = self._app
        # Clear rumps-level items
        for key in list(app._hotkey_menu.keys()):
            del app._hotkey_menu[key]
        app._hotkey_menu_items.clear()
        # Clear NSMenu-level items (separators are not tracked by rumps)
        ns_submenu = app._hotkey_menu._menuitem.submenu()
        if ns_submenu:
            ns_submenu.removeAllItems()

        hotkeys: Dict[str, bool] = app._config.get("hotkeys", {"fn": True})
        for key_name, enabled in hotkeys.items():
            item = StatusMenuItem(key_name, callback=app._on_hotkey_item_click)
            item.state = 1 if enabled else 0
            item._hotkey_name = key_name
            app._hotkey_menu_items[key_name] = item
            app._hotkey_menu.add(item)

        app._hotkey_menu.add(None)
        app._hotkey_menu.add(app._hotkey_record_item)

    def build_model_menu(self) -> None:
        """Build or rebuild the entire STT Model submenu."""
        from voicetext.transcription.model_registry import PRESETS, build_remote_asr_models, is_backend_available

        app = self._app
        if app._model_menu._menu is not None:
            app._model_menu.clear()
        app._model_menu_items.clear()
        app._remote_asr_menu_items.clear()

        # Local presets
        prev_backend = None
        for preset in PRESETS:
            if prev_backend is not None and preset.backend != prev_backend:
                app._model_menu.add(None)
            prev_backend = preset.backend

            backend_ok = is_backend_available(preset.backend)
            if backend_ok:
                title = preset.display_name
            else:
                title = f"{preset.display_name} (N/A)"
            item = StatusMenuItem(title)
            item._preset_id = preset.id
            if backend_ok:
                item.set_callback(app._model_controller.on_model_select)
            else:
                item.set_callback(None)
            if preset.id == app._current_preset_id:
                item.state = 1
            app._model_menu_items[preset.id] = item
            app._model_menu.add(item)

        # Remote ASR models
        asr_cfg = app._config.get("asr", {})
        providers = asr_cfg.get("providers", {})
        remote_models = build_remote_asr_models(providers)

        if remote_models:
            app._model_menu.add(None)
            for rm in remote_models:
                key = (rm.provider, rm.model)
                item = StatusMenuItem(rm.display_name)
                item._remote_asr = rm
                item.set_callback(app._model_controller.on_remote_asr_select)
                if key == app._current_remote_asr:
                    item.state = 1
                app._remote_asr_menu_items[key] = item
                app._model_menu.add(item)

        # Management items
        app._model_menu.add(None)
        app._model_menu.add(app._asr_add_provider_item)

        # Rebuild remove submenu
        if app._asr_remove_provider_menu._menu is not None:
            app._asr_remove_provider_menu.clear()
        app._asr_remove_provider_items.clear()
        for pname in providers:
            item = StatusMenuItem(pname)
            item._provider_name = pname
            item.set_callback(app._model_controller.on_asr_remove_provider)
            app._asr_remove_provider_items[pname] = item
            app._asr_remove_provider_menu.add(item)

        if providers:
            app._model_menu.add(app._asr_remove_provider_menu)

    def build_llm_model_menu(self) -> None:
        """Build or rebuild the LLM Model top-level submenu."""
        app = self._app
        if app._llm_model_menu._menu is not None:
            app._llm_model_menu.clear()
        app._llm_model_menu_items.clear()

        if not app._enhancer:
            return

        providers = app._enhancer.providers_with_models
        current_key = (app._enhancer.provider_name, app._enhancer.model_name)
        first_provider = True

        for pname, models in providers.items():
            if not first_provider:
                app._llm_model_menu.add(None)
            first_provider = False

            for mname in models:
                key = (pname, mname)
                title = f"{pname} / {mname}"
                item = StatusMenuItem(title)
                item._llm_provider = pname
                item._llm_model = mname
                item.set_callback(app._model_controller.on_llm_model_select)
                if key == current_key:
                    item.state = 1
                app._llm_model_menu_items[key] = item
                app._llm_model_menu.add(item)

        # Management items
        app._llm_model_menu.add(None)
        app._llm_model_menu.add(app._llm_add_provider_item)

        # Rebuild remove submenu
        if app._llm_remove_provider_menu._menu is not None:
            app._llm_remove_provider_menu.clear()
        app._llm_remove_provider_items.clear()

        for pname in providers:
            item = StatusMenuItem(pname)
            item._provider_name = pname
            item.set_callback(app._model_controller.on_enhance_remove_provider)
            app._llm_remove_provider_items[pname] = item
            app._llm_remove_provider_menu.add(item)

        if providers:
            app._llm_model_menu.add(app._llm_remove_provider_menu)

    def rebuild_enhance_mode_menu(self) -> None:
        """Rebuild mode menu items from current enhancer modes."""
        from voicetext.enhance.enhancer import MODE_OFF

        app = self._app
        # Remove old mode items (keep Off)
        for mode_id, item in list(app._enhance_menu_items.items()):
            if mode_id != MODE_OFF:
                app._enhance_menu.pop(item.title)
                del app._enhance_menu_items[mode_id]

        # Re-add from enhancer, inserting before "Add Mode..."
        if app._enhancer:
            for mode_id, label in app._enhancer.available_modes:
                item = StatusMenuItem(label)
                item._enhance_mode = mode_id
                item.set_callback(app._on_enhance_mode_select)
                if mode_id == app._enhance_mode:
                    item.state = 1
                app._enhance_menu_items[mode_id] = item
                app._enhance_menu.insert_before(
                    app._enhance_add_mode_item.title, item
                )

    def update_model_checkmarks(self) -> None:
        """Sync menu item checkmarks with current model state (thread-safe)."""
        import Foundation
        if not Foundation.NSThread.isMainThread():
            from PyObjCTools import AppHelper
            AppHelper.callAfter(self.update_model_checkmarks)
            return
        app = self._app
        for preset_id, item in app._model_menu_items.items():
            item.state = 1 if preset_id == app._current_preset_id else 0
        for key, item in app._remote_asr_menu_items.items():
            item.state = 1 if key == app._current_remote_asr else 0
