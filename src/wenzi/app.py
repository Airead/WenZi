"""WenZi (闻字) macOS menubar application."""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions
from CoreFoundation import kCFBooleanTrue

from .enhance.auto_vocab_builder import AutoVocabBuilder
from .config import load_config, migrate_legacy_paths, resolve_config_dir, save_config, set_config_readonly
from .controllers.enhance_controller import EnhanceController
from .enhance.conversation_history import ConversationHistory
from .usage_stats import UsageStats
from .enhance.enhancer import MODE_OFF, create_enhancer
from .ui.result_window import ResultPreviewPanel as NativeResultPreviewPanel
from .ui.result_window_web import ResultPreviewPanel as WebResultPreviewPanel
from .ui.settings_window import SettingsPanel
from .hotkey import MultiHotkeyListener, TapHotkeyListener, _is_fn_key
from .transcription.model_registry import (
    PRESET_BY_ID,
    clear_model_cache,
    find_fallback_preset,
    is_model_cached,
    resolve_preset_from_config,
)
from .audio.recorder import Recorder
from .audio.recording_indicator import RecordingIndicatorPanel
from .audio.sound_manager import SoundManager
from .statusbar import (
    StatusBarApp,
    StatusMenuItem,
    quit_application,
)
from .ui.streaming_overlay import StreamingOverlayPanel
from .controllers.menu_builder import MenuBuilder
from .controllers.model_controller import ModelController, migrate_asr_config
from .controllers.preview_controller import PreviewController
from .controllers.recording_controller import RecordingController
from .controllers.settings_controller import SettingsController
from .controllers.config_controller import ConfigController
from .controllers.enhance_mode_controller import EnhanceModeController
from .controllers.update_controller import UpdateController
from .transcription.base import create_transcriber
from .ui_helpers import (
    activate_for_dialog,
    restore_accessory,
    topmost_alert,
)


logger = logging.getLogger(__name__)


LOG_DIR = Path.home() / "Library" / "Logs" / "WenZi"
LOG_FILE = LOG_DIR / "wenzi.log"

# Map status strings to SF Symbol names for menu bar icons
_STATUS_ICONS: Dict[str, str] = {
    "WZ": "mic.fill",
    "Recording...": "waveform",
    "Transcribing...": "text.bubble",
    "Enhancing...": "sparkles",
    "Preview...": "eye",
    "(empty)": "mic.slash",
    "Error": "exclamationmark.triangle",
    "Config Error": "exclamationmark.triangle",
    "Switching...": "arrow.triangle.2.circlepath",
    "Loading...": "cpu",
    "Unloading...": "arrow.up.circle",
    "Downloading...": "arrow.down.circle",
    "Restoring...": "arrow.counterclockwise",
    "VT \u23f3": "book.fill",
}

# Cache for SF Symbol NSImage objects
_sf_symbol_cache: Dict[str, Any] = {}

# Canonical order for modifier display
_MOD_DISPLAY_ORDER = ["ctrl", "alt", "shift", "cmd"]
# Map left/right variants to canonical names
_MOD_CANONICAL = {
    "cmd": "cmd", "cmd_r": "cmd",
    "ctrl": "ctrl", "ctrl_r": "ctrl",
    "alt": "alt", "alt_r": "alt",
    "shift": "shift", "shift_r": "shift",
}


def format_combo_display(modifiers: set[str], trigger: str | None) -> str:
    """Format a combo hotkey for display in the recording alert.

    Args:
        modifiers: Set of canonical modifier names (e.g. {"alt", "cmd"}).
        trigger: Non-modifier trigger key, or None.

    Returns:
        Human-readable display string like "Alt + Cmd + V".
    """
    parts = [m.capitalize() for m in _MOD_DISPLAY_ORDER if m in modifiers]
    if trigger:
        parts.append(trigger.upper())
    else:
        parts.append("...")
    return " + ".join(parts)


def build_combo_string(modifiers: set[str], trigger: str) -> str:
    """Build the hotkey config string from modifiers and trigger.

    Args:
        modifiers: Set of canonical modifier names.
        trigger: Non-modifier trigger key name.

    Returns:
        Hotkey string like "alt+cmd+v".
    """
    parts = [m for m in _MOD_DISPLAY_ORDER if m in modifiers]
    parts.append(trigger)
    return "+".join(parts)


class WenZiApp(StatusBarApp):
    """Menubar app: hold hotkey to record, release to transcribe and type."""

    def __init__(self, config_dir: Optional[str] = None) -> None:
        super().__init__("WenZi", icon=None, title="WZ")
        self._current_status = "WZ"

        # Seed the SF Symbol icon so the first render shows an icon, not text
        nsimage = self._sf_symbol_image("mic.fill", "WenZi")
        if nsimage is not None:
            self._icon_nsimage = nsimage
            self._title = None  # clear text; icon takes over

        import os
        migrate_legacy_paths()
        self._config_dir = resolve_config_dir(config_dir)
        self._config_path = os.path.join(self._config_dir, "config.json")
        self._config, config_error = load_config(self._config_path)
        self._config_error = config_error
        self._config_degraded = config_error is not None
        if self._config_degraded:
            set_config_readonly(True)
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
        migrate_asr_config(asr_cfg)

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
        self._preview_type = self._config["output"].get("preview_type", "web")
        self._hotkey_listener: Optional[MultiHotkeyListener] = None
        self._busy = False
        self._preview_panel = (
            WebResultPreviewPanel() if self._preview_type == "web"
            else NativeResultPreviewPanel()
        )
        self._conversation_history = ConversationHistory(config_dir=self._config_dir)
        self._usage_stats = UsageStats(stats_dir=self._config_dir)

        # Feedback: sound + visual indicator
        fb_cfg = self._config.get("feedback", {})
        self._sound_manager = SoundManager(
            enabled=fb_cfg.get("sound_enabled", True),
            volume=fb_cfg.get("sound_volume", 0.4),
            config_dir=self._config_dir,
        )
        self._recording_indicator = RecordingIndicatorPanel()
        self._recording_indicator.enabled = fb_cfg.get("visual_indicator", True)
        show_device = fb_cfg.get("show_device_name", False)
        self._recording_indicator.show_device_name = show_device
        self._recorder._query_device_name_enabled = show_device
        self._streaming_overlay = StreamingOverlayPanel()
        self._level_poll_stop: threading.Event | None = None
        self._recording_started = threading.Event()

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
        self._status_item = StatusMenuItem("Ready")
        self._status_item.set_callback(None)
        # Hotkey submenu
        self._hotkey_menu = StatusMenuItem("Hotkey")
        self._hotkey_menu_items: Dict[str, StatusMenuItem] = {}
        self._hotkey_record_item = StatusMenuItem(
            "Record Hotkey...", callback=self._on_record_hotkey
        )
        self._menu_builder = MenuBuilder(self)
        self._model_controller = ModelController(self)
        self._settings_controller = SettingsController(self)
        self._recording_controller = RecordingController(self)
        self._preview_controller = PreviewController(self)
        self._config_controller = ConfigController(self)
        self._enhance_mode_controller = EnhanceModeController(self)
        self._menu_builder.build_hotkey_menu()

        # STT Model submenu
        self._model_menu = StatusMenuItem("STT Model")
        self._model_menu_items: Dict[str, StatusMenuItem] = {}
        self._remote_asr_menu_items: Dict[Tuple[str, str], StatusMenuItem] = {}
        self._asr_add_provider_item = StatusMenuItem(
            "Add ASR Provider...", callback=self._model_controller.on_asr_add_provider
        )
        self._asr_remove_provider_menu = StatusMenuItem("Remove ASR Provider")
        self._asr_remove_provider_items: Dict[str, StatusMenuItem] = {}
        self._menu_builder.build_model_menu()

        # AI Enhance
        self._enhancer = create_enhancer(
            self._config,
            config_dir=self._config_dir,
            conversation_history=self._conversation_history,
        )
        ai_cfg = self._config.get("ai_enhance", {})
        self._enhance_mode: str = ai_cfg.get("mode", "proofread")
        if self._enhancer and not ai_cfg.get("enabled", False):
            self._enhance_mode = MODE_OFF

        self._enhance_controller = EnhanceController(
            enhancer=self._enhancer,
            preview_panel=self._preview_panel,
            usage_stats=self._usage_stats,
        )
        self._enhance_controller.enhance_mode = self._enhance_mode

        # Auto vocabulary builder
        vocab_cfg = ai_cfg.get("vocabulary", {})
        self._auto_vocab_builder = AutoVocabBuilder(
            config=self._config,
            enabled=vocab_cfg.get("auto_build", True),
            threshold=vocab_cfg.get("auto_build_threshold", 10),
            on_build_done=self._update_vocab_title,
            conversation_history=self._conversation_history,
            config_dir=self._config_dir,
        )
        if self._enhancer:
            self._auto_vocab_builder.set_enhancer(self._enhancer)

        # AI Enhance submenu (mode selection only)
        self._enhance_menu = StatusMenuItem("AI Enhance")
        self._enhance_menu_items: Dict[str, StatusMenuItem] = {}

        # Fixed "Off" item
        off_item = StatusMenuItem("Off")
        off_item._enhance_mode = MODE_OFF
        off_item.set_callback(self._on_enhance_mode_select)
        if self._enhance_mode == MODE_OFF:
            off_item.state = 1
        self._enhance_menu_items[MODE_OFF] = off_item
        self._enhance_menu.add(off_item)

        # Dynamic mode items from enhancer
        if self._enhancer:
            for mode_id, label in self._enhancer.available_modes:
                item = StatusMenuItem(label)
                item._enhance_mode = mode_id
                item.set_callback(self._on_enhance_mode_select)
                if mode_id == self._enhance_mode:
                    item.state = 1
                self._enhance_menu_items[mode_id] = item
                self._enhance_menu.add(item)

        # Add Mode item
        self._enhance_menu.add(None)
        self._enhance_add_mode_item = StatusMenuItem(
            "Add Mode...", callback=self._on_enhance_add_mode
        )
        self._enhance_menu.add(self._enhance_add_mode_item)

        # Top-level toggle items (promoted from AI Enhance)
        vocab_enabled = ai_cfg.get("vocabulary", {}).get("enabled", False)
        self._enhance_vocab_item = StatusMenuItem(
            "Vocabulary", callback=self._on_vocab_toggle
        )
        self._enhance_vocab_item.state = 1 if vocab_enabled else 0
        self._update_vocab_title()

        history_enabled = ai_cfg.get("conversation_history", {}).get("enabled", False)
        self._enhance_history_item = StatusMenuItem(
            "Conversation History", callback=self._on_history_toggle
        )
        self._enhance_history_item.state = 1 if history_enabled else 0

        self._browse_history_item = StatusMenuItem(
            "Browse History...", callback=self._on_browse_history
        )

        # LLM Model top-level submenu
        self._llm_model_menu = StatusMenuItem("LLM Model")
        self._llm_model_menu_items: Dict[Tuple[str, str], StatusMenuItem] = {}
        self._llm_add_provider_item = StatusMenuItem(
            "Add Provider...", callback=self._model_controller.on_enhance_add_provider
        )
        self._llm_remove_provider_menu = StatusMenuItem("Remove Provider")
        self._llm_remove_provider_items: Dict[str, StatusMenuItem] = {}
        self._menu_builder.build_llm_model_menu()

        # AI Settings submenu (low-frequency AI configuration)
        self._ai_settings_menu = StatusMenuItem("AI Settings")

        # Thinking toggle
        self._enhance_thinking_item = StatusMenuItem(
            "Thinking", callback=self._on_enhance_thinking_toggle
        )
        if self._enhancer and self._enhancer.thinking:
            self._enhance_thinking_item.state = 1
        self._ai_settings_menu.add(self._enhance_thinking_item)

        # Build vocabulary action
        self._ai_settings_menu.add(None)
        self._enhance_vocab_build_item = StatusMenuItem(
            "Build Vocabulary...", callback=self._on_vocab_build
        )
        self._ai_settings_menu.add(self._enhance_vocab_build_item)

        self._enhance_auto_build_item = StatusMenuItem(
            "Auto Build Vocabulary", callback=self._on_auto_build_toggle
        )
        self._enhance_auto_build_item.state = 1 if vocab_cfg.get("auto_build", True) else 0
        self._ai_settings_menu.add(self._enhance_auto_build_item)

        self._ai_settings_menu.add(None)
        self._enhance_edit_config_item = StatusMenuItem(
            "Edit Config...", callback=self._on_enhance_edit_config
        )
        self._ai_settings_menu.add(self._enhance_edit_config_item)

        self._preview_item = StatusMenuItem(
            "Preview", callback=self._on_preview_toggle
        )
        self._preview_item.state = 1 if self._preview_enabled else 0

        self._clipboard_enhance_item = StatusMenuItem(
            "Enhance Clipboard", callback=self._preview_controller.on_clipboard_enhance
        )

        # Feedback toggle items
        self._sound_feedback_item = StatusMenuItem(
            "Sound Feedback", callback=self._recording_controller.on_sound_feedback_toggle
        )
        self._sound_feedback_item.state = 1 if self._sound_manager.enabled else 0

        self._visual_indicator_item = StatusMenuItem(
            "Visual Indicator", callback=self._recording_controller.on_visual_indicator_toggle
        )
        self._visual_indicator_item.state = 1 if self._recording_indicator.enabled else 0

        # View Logs top-level item (replaces Debug submenu)
        self._view_logs_item = StatusMenuItem(
            "View Logs...", callback=self._on_view_logs
        )

        # Show Config / Reload Config items
        self._show_config_item = StatusMenuItem(
            "Show Config...", callback=self._on_show_config
        )
        self._reload_config_item = StatusMenuItem(
            "Reload Config", callback=self._on_reload_config
        )

        # Usage Stats item
        self._usage_stats_item = StatusMenuItem(
            "Usage Stats", callback=self._on_show_usage_stats
        )

        # Restart / About / Help items
        self._restart_item = StatusMenuItem("Restart", callback=self._on_restart)
        self._about_item = StatusMenuItem("About WenZi", callback=self._on_about)
        self._help_item = StatusMenuItem(
            "Help", callback=self._menu_builder.on_help_click
        )

        # History browser (lazy-created)
        self._history_browser = None

        # Settings panel
        self._settings_panel = SettingsPanel()
        self._settings_item = StatusMenuItem(
            "Settings...", callback=self._on_open_settings
        )

        if self._config_degraded:
            self._config_error_item = StatusMenuItem(
                "Config Error...", callback=lambda _: self._show_config_error_alert()
            )
            self.menu = [
                self._config_error_item,
                None,
                self._view_logs_item,
            ]
        else:
            self.menu = [
                self._status_item,
                None,
                self._clipboard_enhance_item,
                self._browse_history_item,
                self._settings_item,
                None,
                self._view_logs_item,
                self._usage_stats_item,
                self._about_item,
                self._help_item,
                None,
                self._restart_item,
            ]
        self.quit_button.set_callback(self._on_quit_click)

        # Update checker
        self._update_controller = UpdateController(self)

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

        # Root logger stays at INFO to suppress noisy third-party DEBUG output
        # (numba, urllib3, etc.). Only our own logger gets the user-configured level.
        logging.basicConfig(
            level=logging.INFO,
            format=fmt,
            handlers=[logging.StreamHandler(), file_handler],
        )
        logging.getLogger("wenzi").setLevel(log_level)

    @staticmethod
    def _sf_symbol_image(name: str, description: str = "") -> Any:
        """Create an NSImage from an SF Symbol name, or return None."""
        cached = _sf_symbol_cache.get(name)
        if cached is not None:
            return cached
        try:
            from AppKit import NSImage, NSImageSymbolConfiguration
            if not hasattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_"):
                return None
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                name, description or name
            )
            if img is not None:
                config = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
                    17.0, 0  # 0 = NSFontWeightRegular
                )
                img = img.imageWithSymbolConfiguration_(config)
                img.setTemplate_(True)
                _sf_symbol_cache[name] = img
            return img
        except Exception:
            return None

    def _set_status(self, text: str) -> None:
        """Update menu bar icon/title and status menu item (thread-safe)."""
        import Foundation
        if not Foundation.NSThread.isMainThread():
            from PyObjCTools import AppHelper
            AppHelper.callAfter(self._set_status, text)
            return

        self._current_status = text
        self._status_item.title = text  # dropdown menu always shows text

        # Resolve SF Symbol
        symbol_name = _STATUS_ICONS.get(text)
        bar_title = None
        if symbol_name is None:
            if text.startswith("DL "):
                symbol_name = "arrow.down.circle"
                bar_title = text[3:]  # show "X%" next to icon
            else:
                symbol_name = "mic.fill"  # safe fallback

        nsimage = self._sf_symbol_image(symbol_name, text)
        if nsimage is not None:
            self._icon_nsimage = nsimage
            self._update_status_bar_icon()
            self.title = bar_title  # clear text when icon is set
        else:
            self.title = text  # fallback to text-only if SF Symbols unavailable

    def _start_recording_indicator(self) -> None:
        self._recording_controller.start_recording_indicator()

    def _stop_recording_indicator(self, animate: bool = False) -> None:
        self._recording_controller.stop_recording_indicator(animate=animate)

    # ------------------------------------------------------------------
    # Hotkey management
    # ------------------------------------------------------------------

    def _start_hotkey_listeners(self) -> None:
        hotkeys = self._config.get("hotkeys", {"fn": True})
        fb_cfg = self._config.get("feedback", {})
        restart_key = fb_cfg.get("restart_key", "cmd")
        cancel_key = fb_cfg.get("cancel_key", "space")
        preview_history_key = fb_cfg.get("preview_history_key", "z")
        active_keys = [k for k, v in hotkeys.items() if v]
        if active_keys:
            self._hotkey_listener = MultiHotkeyListener(
                key_names=active_keys,
                on_press=self._recording_controller.on_hotkey_press,
                on_release=self._recording_controller.on_hotkey_release,
                on_restart=self._recording_controller.on_restart_recording,
                on_cancel=self._recording_controller.on_cancel_recording,
                restart_key=restart_key,
                cancel_key=cancel_key,
                on_preview_history=self._recording_controller.on_preview_history,
                preview_history_key=preview_history_key,
                on_mode_prev=self._recording_controller.on_mode_prev,
                on_mode_next=self._recording_controller.on_mode_next,
            )
            self._hotkey_listener.start()

    def _on_hotkey_item_click(self, sender) -> None:
        """Handle click on a hotkey menu item — show enable/disable/delete alert."""
        from AppKit import NSAlert, NSStatusWindowLevel
        from PyObjCTools import AppHelper

        key_name = sender._hotkey_name
        enabled = bool(sender.state)
        is_fn = _is_fn_key(key_name)

        activate_for_dialog()
        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"Hotkey: {key_name}")
        state_text = "enabled" if enabled else "disabled"
        toggle_text = "Disable" if enabled else "Enable"
        alert.setInformativeText_(f'"{key_name}" is currently {state_text}.')
        alert.addButtonWithTitle_("Cancel")
        alert.addButtonWithTitle_(toggle_text)
        if not is_fn:
            alert.addButtonWithTitle_("Delete")

        alert.setAlertStyle_(0)
        alert.window().setLevel_(NSStatusWindowLevel)
        result = alert.runModal()
        restore_accessory()

        # 1000=first(Cancel), 1001=second(Disable/Enable), 1002=third(Delete)
        # Defer all state changes to avoid modifying menu/listeners during
        # the NSMenu callback (AppKit crashes if the clicked item is removed
        # or Quartz event taps are stopped mid-callback).
        if result == 1001:
            new_state = not enabled
            def _toggle():
                try:
                    self._config["hotkeys"][key_name] = new_state
                    sender.state = 1 if new_state else 0
                    save_config(self._config, self._config_path)
                    if self._hotkey_listener:
                        if new_state:
                            self._hotkey_listener.enable_key(key_name)
                        else:
                            self._hotkey_listener.disable_key(key_name)
                except Exception:
                    logger.exception("Failed to toggle hotkey %s", key_name)
            AppHelper.callAfter(_toggle)
        elif result == 1002 and not is_fn:
            def _delete():
                try:
                    del self._config["hotkeys"][key_name]
                    save_config(self._config, self._config_path)
                    if self._hotkey_listener:
                        self._hotkey_listener.disable_key(key_name)
                    self._menu_builder.build_hotkey_menu()
                except Exception:
                    logger.exception("Failed to delete hotkey %s", key_name)
            AppHelper.callAfter(_delete)

    def record_hotkey_modal(self) -> str | None:
        """Show a modal alert to record a hotkey and return the key name.

        Returns the recorded key name string, or None if cancelled/timed out.
        Reusable by any caller that needs to record a single hotkey.
        """
        from AppKit import NSAlert, NSStatusWindowLevel
        from PyObjCTools import AppHelper

        if not self._hotkey_listener:
            return None

        activate_for_dialog()
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Record Hotkey")
        alert.setInformativeText_("Press any key to register it as a hotkey...")
        alert.addButtonWithTitle_("Cancel")
        alert.setAlertStyle_(0)
        alert.window().setLevel_(NSStatusWindowLevel)

        recorded_key = None

        def on_recorded(key_name):
            nonlocal recorded_key
            recorded_key = key_name
            def _close():
                from AppKit import NSApp
                NSApp.abortModal()
            AppHelper.callAfter(_close)

        def on_timeout():
            def _close():
                from AppKit import NSApp
                NSApp.abortModal()
            AppHelper.callAfter(_close)

        def on_unrecognized(debug_info):
            def _update():
                alert.setInformativeText_(
                    f"Unsupported key: {debug_info}\n"
                    "Add it to _SPECIAL_VK in hotkey.py, then retry."
                )
            AppHelper.callAfter(_update)

        self._hotkey_listener.record_next_key(
            on_recorded=on_recorded,
            on_timeout=on_timeout,
            timeout=10.0,
            on_unrecognized=on_unrecognized,
        )
        alert.runModal()
        self._hotkey_listener.cancel_record()
        restore_accessory()

        return recorded_key

    def record_combo_hotkey_modal(self) -> str | None:
        """Show a modal alert to record a hotkey combo (e.g. cmd+alt+v).

        Shows live preview of the current key combination.
        Enter to confirm, ESC to cancel, Delete to reset.

        Returns:
            Hotkey string like "cmd+alt+v", or None if cancelled.
        """
        from AppKit import NSAlert, NSApp, NSStatusWindowLevel
        from PyObjCTools import AppHelper
        from .hotkey import _QuartzAllKeysListener

        activate_for_dialog()
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Record Hotkey Combo")
        alert.setInformativeText_("Press a key combination...\n\n"
                                  "Enter = confirm | ESC = cancel | Delete = reset")
        alert.addButtonWithTitle_("Cancel")
        alert.setAlertStyle_(0)
        alert.window().setLevel_(NSStatusWindowLevel)

        combo_modifiers: set[str] = set()
        combo_trigger: list[str | None] = [None]  # mutable container
        result: list[str | None] = [None]

        def _update_display():
            """Update alert text on the main thread."""
            mods = set(combo_modifiers)
            trigger = combo_trigger[0]
            if not mods and not trigger:
                text = "Press a key combination..."
            elif mods and not trigger:
                text = format_combo_display(mods, None)
            else:
                text = format_combo_display(mods, trigger)
                text += "\n(Press Enter to confirm)"
            text += "\n\nEnter = confirm | ESC = cancel | Delete = reset"

            def _do():
                alert.setInformativeText_(text)
            AppHelper.callAfter(_do)

        def _on_press(name: str):
            canonical = _MOD_CANONICAL.get(name)
            if canonical:
                combo_modifiers.add(canonical)
                _update_display()
                return

            # Enter key (keycode 36 → "return" not in map; use name check)
            if name == "return" or name == "enter":
                # Confirm if we have a complete combo
                if combo_modifiers and combo_trigger[0]:
                    result[0] = build_combo_string(
                        set(combo_modifiers), combo_trigger[0]
                    )
                    AppHelper.callAfter(lambda: NSApp.abortModal())
                return

            # ESC → cancel
            if name == "esc":
                AppHelper.callAfter(lambda: NSApp.abortModal())
                return

            # Delete/Backspace → reset
            if name == "delete" or name == "backspace":
                combo_modifiers.clear()
                combo_trigger[0] = None
                _update_display()
                return

            # Non-modifier key → set as trigger
            combo_trigger[0] = name
            _update_display()

        def _on_release(name: str):
            canonical = _MOD_CANONICAL.get(name)
            if canonical:
                combo_modifiers.discard(canonical)
                _update_display()

        listener = _QuartzAllKeysListener(
            on_press=_on_press,
            on_release=_on_release,
            listen_only=True,
        )
        listener.start()
        alert.runModal()
        listener.stop()
        restore_accessory()

        return result[0]

    def _on_record_hotkey(self, _) -> None:
        """Show 'press any key' alert and record a hotkey."""
        from PyObjCTools import AppHelper

        recorded_key = self.record_hotkey_modal()
        if recorded_key:
            def _apply():
                try:
                    hotkeys = self._config.setdefault("hotkeys", {})
                    hotkeys[recorded_key] = True
                    save_config(self._config, self._config_path)
                    if self._hotkey_listener:
                        self._hotkey_listener.enable_key(recorded_key)
                    self._menu_builder.build_hotkey_menu()
                    logger.info("Recorded new hotkey: %s", recorded_key)
                    # Refresh settings panel to show the new hotkey row
                    if self._settings_panel and self._settings_panel._panel is not None:
                        self._settings_controller.on_open_settings(None)
                except Exception:
                    logger.exception("Failed to apply recorded hotkey %s", recorded_key)
            AppHelper.callAfter(_apply)

    def _do_transcribe_direct(self, asr_text: str, use_enhance: bool) -> None:
        self._recording_controller.do_transcribe_direct(asr_text, use_enhance)

    def _current_stt_model(self) -> str:
        """Return display name of the current STT model."""
        try:
            return self._transcriber.model_display_name
        except Exception:
            return ""

    def _current_llm_model(self) -> str:
        """Return display name of the current LLM model."""
        if not self._enhancer:
            return ""
        parts = []
        if self._enhancer.provider_name:
            parts.append(self._enhancer.provider_name)
        if self._enhancer.model_name:
            parts.append(self._enhancer.model_name)
        return " / ".join(parts)

    def _do_transcribe_with_preview(
        self, asr_text: str | None, use_enhance: bool,
        audio_duration: float = 0.0, wav_data: Optional[bytes] = None,
    ) -> None:
        self._preview_controller.do_transcribe_with_preview(
            asr_text, use_enhance, audio_duration, wav_data
        )

    # ── Enhance mode management (EnhanceModeController) ────────────

    def _on_enhance_mode_select(self, sender) -> None:
        self._enhance_mode_controller.on_enhance_mode_select(sender)

    def _on_enhance_add_mode(self, _) -> None:
        self._enhance_mode_controller.on_enhance_add_mode(_)

    def _on_enhance_thinking_toggle(self, sender) -> None:
        self._enhance_mode_controller.on_enhance_thinking_toggle(sender)

    def _update_vocab_title(self) -> None:
        self._enhance_mode_controller.update_vocab_title()

    def _on_vocab_toggle(self, sender) -> None:
        self._enhance_mode_controller.on_vocab_toggle(sender)

    def _on_auto_build_toggle(self, sender) -> None:
        self._enhance_mode_controller.on_auto_build_toggle(sender)

    def _on_history_toggle(self, sender) -> None:
        self._enhance_mode_controller.on_history_toggle(sender)

    def _on_vocab_build(self, _sender) -> None:
        self._enhance_mode_controller.on_vocab_build(_sender)

    def _on_preview_toggle(self, sender) -> None:
        self._enhance_mode_controller.on_preview_toggle(sender)

    # ── Config / debug / info display (ConfigController) ─────────

    def _on_enhance_edit_config(self, _) -> None:
        self._config_controller.on_enhance_edit_config(_)

    def _on_view_logs(self, _) -> None:
        self._config_controller.on_view_logs(_)

    def _on_log_level_change(self, level_name: str) -> None:
        self._config_controller.on_log_level_change(level_name)

    def _on_print_prompt_change(self, enabled: bool) -> None:
        self._config_controller.on_print_prompt_change(enabled)

    def _on_print_request_body_change(self, enabled: bool) -> None:
        self._config_controller.on_print_request_body_change(enabled)

    def _build_config_info(self) -> str:
        return self._config_controller.build_config_info()

    def _on_show_config(self, _) -> None:
        self._config_controller.on_show_config(_)

    def _on_reload_config(self, _) -> None:
        self._config_controller.on_reload_config(_)

    def _on_browse_history(self, _=None) -> None:
        self._config_controller.on_browse_history(_)

    def _on_show_usage_stats(self, _) -> None:
        self._config_controller.on_show_usage_stats(_)

    def _on_about(self, _) -> None:
        self._config_controller.on_about(_)

    def _on_restart(self, _) -> None:
        from wenzi.statusbar import restart_application
        restart_application()

    # ── Settings panel ────────────────────────────────────────────────

    def _on_open_settings(self, _) -> None:
        """Open the Settings panel with current state and callbacks."""
        if self._config_degraded:
            self._show_config_error_alert()
            return
        self._settings_controller.on_open_settings(_)

    def _on_quit_click(self, _) -> None:
        self._update_controller.stop()
        if hasattr(self, "_script_engine") and self._script_engine:
            self._script_engine.stop()
        if self._hotkey_listener:
            self._hotkey_listener.stop()
        if self._clipboard_hotkey_listener:
            self._clipboard_hotkey_listener.stop()
        if self._settings_panel.is_visible:
            self._settings_panel.close()
        quit_application()

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

    def _warmup(self) -> None:
        """Pre-create heavy objects after the event loop starts.

        Runs on the main thread via AppHelper.callAfter() so that the
        first recording / preview interaction feels instant.
        """
        try:
            self._sound_manager.warmup()
        except Exception:
            logger.debug("Sound warmup failed", exc_info=True)
        try:
            if hasattr(self._preview_panel, "warmup"):
                self._preview_panel.warmup()
        except Exception:
            logger.debug("Preview panel warmup failed", exc_info=True)

    def _show_config_error(self) -> None:
        """Show config error alert if config loading failed."""
        if self._config_error is None:
            return
        self._set_status("Config Error")
        self._show_config_error_alert()

    def _show_config_error_alert(self) -> None:
        """Show an alert about the config error with a 'Show in Finder' button."""
        if self._config_error is None:
            return
        result = topmost_alert(
            title="Configuration Error",
            message=(
                f"Failed to load {self._config_error.path}\n\n"
                f"{self._config_error.message}\n\n"
                "The app is running with default settings and will not "
                "save any changes. Please fix the config file and restart."
            ),
            ok="Show in Finder",
            cancel="Close",
        )
        restore_accessory()
        if result:
            import subprocess
            subprocess.Popen(
                ["open", "-R", self._config_error.path],
            )

    def _show_model_load_error_alert(self, error: Exception) -> None:
        """Show alert when model initialization fails, with option to clear cache."""
        preset_id = self._current_preset_id
        preset = PRESET_BY_ID.get(preset_id) if preset_id else None
        # Only offer cache clear for local models that have a cache directory
        can_clear = preset is not None and preset.backend not in ("apple", "whisper-api")

        if can_clear:
            result = topmost_alert(
                title="Model Load Failed",
                message=(
                    f"Failed to initialize model.\n\n"
                    f"Error: {str(error)[:200]}\n\n"
                    "This may be caused by corrupted cache files from an "
                    "interrupted download. Click 'Clear Cache & Retry' to "
                    "delete cached files and try again."
                ),
                ok="Clear Cache & Retry",
                cancel="Close",
            )
            restore_accessory()
            if result == 1:
                self._clear_cache_and_reinitialize(preset)
        else:
            topmost_alert(
                title="Model Load Failed",
                message=(
                    f"Failed to initialize model.\n\n"
                    f"Error: {str(error)[:200]}\n\n"
                    "Please check the log file for details."
                ),
            )
            restore_accessory()

    def _clear_cache_and_reinitialize(self, preset) -> None:
        """Clear model cache and retry initialization on a background thread."""
        def _do():
            stop_event = threading.Event()
            monitor_thread = None
            try:
                self._set_status("Clearing...")
                clear_model_cache(preset)
                monitor_args = self._model_controller._make_download_monitor_args(preset)
                monitor_thread = threading.Thread(
                    target=self._model_controller._monitor_download_progress,
                    args=(stop_event, monitor_args),
                    daemon=True,
                )
                monitor_thread.start()
                self._transcriber.cleanup()
                asr_cfg = self._config["asr"]
                self._transcriber = create_transcriber(
                    backend=preset.backend,
                    use_vad=asr_cfg.get("use_vad", True),
                    use_punc=asr_cfg.get("use_punc", True),
                    language=preset.language or asr_cfg.get("language"),
                    model=preset.model,
                    temperature=asr_cfg.get("temperature"),
                )
                self._transcriber.initialize()
                stop_event.set()
                monitor_thread.join(timeout=2)
                self._set_status("WZ")
                logger.info("Model reinitialized after cache clear")
            except Exception as e2:
                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)
                logger.error("Retry after cache clear failed: %s", e2)
                self._set_status("Error")
                topmost_alert(
                    title="Model Load Failed",
                    message=(
                        f"Retry failed.\n\n"
                        f"Error: {str(e2)[:200]}\n\n"
                        "Please check your network connection and try "
                        "switching models from the menu."
                    ),
                )
                restore_accessory()

        threading.Thread(target=_do, daemon=True).start()

    def run(self, **kwargs) -> None:
        """Initialize models and start the app."""
        self._ensure_accessibility()

        # Load models in background
        def _init_models():
            try:
                # For Apple Speech, verify Siri/Dictation before initializing
                asr_cfg = self._config["asr"]
                if (
                    not self._current_remote_asr
                    and asr_cfg.get("backend") == "apple"
                ):
                    from .transcription.apple import check_siri_available

                    self._set_status("Checking...")
                    siri_ok, _ = check_siri_available(
                        language=asr_cfg.get("language") or "zh",
                        on_device=(asr_cfg.get("model") == "on-device"),
                    )
                    if not siri_ok:
                        fallback = find_fallback_preset()
                        if fallback:
                            logger.warning(
                                "Siri/Dictation disabled, using %s for this session",
                                fallback.display_name,
                            )
                            self._transcriber = create_transcriber(
                                backend=fallback.backend,
                                use_vad=asr_cfg.get("use_vad", True),
                                use_punc=asr_cfg.get("use_punc", True),
                                language=fallback.language
                                or asr_cfg.get("language"),
                                model=fallback.model,
                                temperature=asr_cfg.get("temperature"),
                            )
                            self._current_preset_id = fallback.id
                            self._menu_builder.update_model_checkmarks()
                        else:
                            logger.warning(
                                "Siri/Dictation disabled and no fallback available"
                            )

                stop_event = threading.Event()
                monitor_thread = None
                preset = PRESET_BY_ID.get(self._current_preset_id)
                need_monitor = (
                    not self._current_remote_asr
                    and preset
                    and not is_model_cached(preset)
                )
                if need_monitor:
                    monitor_args = self._model_controller._make_download_monitor_args(preset)
                    monitor_thread = threading.Thread(
                        target=self._model_controller._monitor_download_progress,
                        args=(stop_event, monitor_args),
                        daemon=True,
                    )
                    monitor_thread.start()
                elif not self._config_degraded:
                    self._set_status("Loading...")

                self._transcriber.initialize()

                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)
                if not self._config_degraded:
                    self._set_status("WZ")
                logger.info("Models loaded, app ready")
            except Exception as e:
                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)
                logger.error("Model initialization failed: %s", e)
                if not self._config_degraded:
                    self._set_status("Error")
                self._show_model_load_error_alert(e)

        threading.Thread(target=_init_models, daemon=True).start()

        # Start hotkey listeners
        self._start_hotkey_listeners()

        # Start scripting engine if enabled
        scripting_cfg = self._config.get("scripting", {})
        if scripting_cfg.get("enabled", False):
            from .scripting import ScriptEngine

            script_dir = scripting_cfg.get("script_dir")
            self._script_engine = ScriptEngine(
                script_dir=script_dir, config=scripting_cfg
            )
            self._script_engine.start()

        # Start clipboard enhance hotkey listener if configured
        clip_hotkey = self._config.get("clipboard_enhance", {}).get("hotkey", "")
        if clip_hotkey:
            self._clipboard_hotkey_listener = TapHotkeyListener(
                hotkey_str=clip_hotkey,
                on_activate=self._preview_controller.on_clipboard_enhance,
            )
            self._clipboard_hotkey_listener.start()

        # Schedule warmup after the event loop starts to pre-create heavy
        # objects (WKWebView, NSSound) so the first user interaction is snappy.
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._warmup)

        # Start background update checker
        if not self._config_degraded:
            AppHelper.callAfter(self._update_controller.start)

        # Show config error alert after the event loop starts
        if self._config_error is not None:
            AppHelper.callAfter(self._show_config_error)

        super().run(**kwargs)



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
    import signal

    signal.signal(signal.SIGINT, lambda *_: quit_application())

    config_dir = sys.argv[1] if len(sys.argv) > 1 else None
    app = WenZiApp(config_dir=config_dir)  # None uses default dir
    app.run()


if __name__ == "__main__":
    main()
