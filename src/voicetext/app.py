"""VoiceText macOS menubar application."""

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
from .config import load_config, save_config
from .controllers.enhance_controller import EnhanceController
from .enhance.conversation_history import ConversationHistory
from .usage_stats import UsageStats
from .enhance.enhancer import MODE_OFF, create_enhancer
from .ui.history_browser_window import HistoryBrowserPanel
from .ui.result_window import ResultPreviewPanel
from .ui.settings_window import SettingsPanel
from .hotkey import MultiHotkeyListener, TapHotkeyListener, _is_fn_key
from .transcription.model_registry import (
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
from .transcription.base import create_transcriber
from .ui_helpers import (
    activate_for_dialog,
    restore_accessory,
)


logger = logging.getLogger(__name__)


LOG_DIR = Path.home() / "Library" / "Logs" / "VoiceText"
LOG_FILE = LOG_DIR / "voicetext.log"

# Map status strings to SF Symbol names for menu bar icons
_STATUS_ICONS: Dict[str, str] = {
    "VT": "mic.fill",
    "Recording...": "waveform",
    "Transcribing...": "text.bubble",
    "Enhancing...": "sparkles",
    "Preview...": "eye",
    "(empty)": "mic.slash",
    "Error": "exclamationmark.triangle",
    "Switching...": "arrow.triangle.2.circlepath",
    "Loading...": "cpu",
    "Unloading...": "arrow.up.circle",
    "Downloading...": "arrow.down.circle",
    "Restoring...": "arrow.counterclockwise",
    "VT \u23f3": "book.fill",
}

# Cache for SF Symbol NSImage objects
_sf_symbol_cache: Dict[str, Any] = {}


class VoiceTextApp(StatusBarApp):
    """Menubar app: hold hotkey to record, release to transcribe and type."""

    def __init__(self, config_path: Optional[str] = None) -> None:
        super().__init__("VoiceText", icon=None, title="VT")
        self._current_status = "VT"

        # Seed the SF Symbol icon so the first render shows an icon, not text
        nsimage = self._sf_symbol_image("mic.fill", "VoiceText")
        if nsimage is not None:
            self._icon_nsimage = nsimage
            self._title = None  # clear text; icon takes over

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
        self._hotkey_listener: Optional[MultiHotkeyListener] = None
        self._busy = False
        self._preview_panel = ResultPreviewPanel()
        self._conversation_history = ConversationHistory()
        self._usage_stats = UsageStats()

        # Feedback: sound + visual indicator
        fb_cfg = self._config.get("feedback", {})
        self._sound_manager = SoundManager(
            enabled=fb_cfg.get("sound_enabled", True),
            volume=fb_cfg.get("sound_volume", 0.4),
        )
        self._recording_indicator = RecordingIndicatorPanel()
        self._recording_indicator.enabled = fb_cfg.get("visual_indicator", True)
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
        self._enhancer = create_enhancer(self._config)
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

        # About item
        self._about_item = StatusMenuItem("About VoiceText", callback=self._on_about)

        # History browser (lazy-created)
        self._history_browser: Optional[HistoryBrowserPanel] = None

        # Settings panel
        self._settings_panel = SettingsPanel()
        self._settings_item = StatusMenuItem(
            "Settings...", callback=self._on_open_settings
        )

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
        hotkeys: Dict[str, bool] = self._config.get("hotkeys", {"fn": True})
        active_keys = [k for k, v in hotkeys.items() if v]
        if active_keys:
            self._hotkey_listener = MultiHotkeyListener(
                key_names=active_keys,
                on_press=self._recording_controller.on_hotkey_press,
                on_release=self._recording_controller.on_hotkey_release,
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

    def _on_record_hotkey(self, _) -> None:
        """Show 'press any key' alert and record a hotkey."""
        from AppKit import NSAlert, NSStatusWindowLevel
        from PyObjCTools import AppHelper

        if not self._hotkey_listener:
            return

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

    # ── Settings panel ────────────────────────────────────────────────

    def _on_open_settings(self, _) -> None:
        """Open the Settings panel with current state and callbacks."""
        self._settings_controller.on_open_settings(_)

    def _on_quit_click(self, _) -> None:
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

        # Start hotkey listeners
        self._start_hotkey_listeners()

        # Start clipboard enhance hotkey listener if configured
        clip_hotkey = self._config.get("clipboard_enhance", {}).get("hotkey", "")
        if clip_hotkey:
            self._clipboard_hotkey_listener = TapHotkeyListener(
                hotkey_str=clip_hotkey,
                on_activate=self._preview_controller.on_clipboard_enhance,
            )
            self._clipboard_hotkey_listener.start()

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

    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    app = VoiceTextApp(config_path=config_path)  # None uses default path
    app.run()


if __name__ == "__main__":
    main()
