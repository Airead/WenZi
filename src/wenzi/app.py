"""WenZi (闻字) macOS menubar application."""

from __future__ import annotations

import importlib.util
import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path
from typing import Any

from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions
from CoreFoundation import kCFBooleanTrue

from wenzi import async_loop

from .audio.recorder import Recorder
from .audio.recording_indicator import RecordingIndicatorPanel
from .audio.sound_manager import SoundManager
from .config import (
    DEFAULT_LOG_DIR,
    load_config,
    migrate_legacy_paths,
    migrate_xdg_paths,
    resolve_cache_dir,
    resolve_config_dir,
    resolve_data_dir,
    save_config,
    set_config_readonly,
)
from .controllers.config_controller import ConfigController
from .controllers.enhance_controller import EnhanceController
from .controllers.enhance_mode_controller import EnhanceModeController
from .controllers.menu_builder import MenuBuilder
from .controllers.model_controller import ModelController, migrate_asr_config
from .controllers.preview_controller import PreviewController
from .controllers.recording_flow import RecordingFlow
from .controllers.settings_controller import SettingsController
from .controllers.universal_action_controller import UniversalActionController
from .controllers.update_controller import UpdateController
from .enhance.conversation_history import ConversationHistory
from .enhance.enhancer import MODE_OFF, create_enhancer
from .hotkey import MultiHotkeyListener, TapHotkeyListener, _is_fn_key
from .i18n import t
from .statusbar import (
    StatusBarApp,
    StatusMenuItem,
    quit_application,
)
from .transcription.base import create_transcriber
from .transcription.model_registry import (
    PRESET_BY_ID,
    clear_model_cache,
    find_fallback_preset,
    is_backend_available,
    is_model_cached,
    resolve_preset_from_config,
)
from .ui.result_window_web import ResultPreviewPanel
from .ui.settings_window_web import SettingsWebPanel as SettingsPanel
from .ui.streaming_overlay import StreamingOverlayPanel
from .ui_helpers import (
    activate_for_dialog,
    restore_accessory,
    topmost_alert,
)
from .usage_stats import UsageStats

logger = logging.getLogger(__name__)

_build_type_cache: str | None = None


def get_build_type() -> str:
    """Detect build variant: "lite" or "standard".

    Detection priority:
    1. PyInstaller bundle path (packaged app)
    2. WENZI_VERSION environment variable (dev mode)
    3. Package probing (runtime fallback)

    Result is cached after first call.
    """
    global _build_type_cache
    if _build_type_cache is not None:
        return _build_type_cache

    # 1. PyInstaller bundle path
    if getattr(sys, "frozen", False):
        _build_type_cache = "lite" if "WenZi-Lite" in sys.executable else "standard"
        return _build_type_cache

    # 2. Environment variable
    env_version = os.environ.get("WENZI_VERSION")
    if env_version in ("lite", "standard"):
        _build_type_cache = env_version
        return _build_type_cache

    # 3. Package probing
    if importlib.util.find_spec("funasr_onnx") is not None:
        _build_type_cache = "standard"
    else:
        _build_type_cache = "lite"
    return _build_type_cache


LOG_DIR = Path(os.path.expanduser(DEFAULT_LOG_DIR))
LOG_FILE = LOG_DIR / "wenzi.log"

# Map status i18n keys to SF Symbol names for menu bar icons.
# _set_status() receives a key from this dict (or a dynamic string like "DL 50%").
_STATUS_ICONS: dict[str, str] = {
    "statusbar.status.ready": "mic.fill",
    "statusbar.status.recording": "waveform",
    "statusbar.status.transcribing": "text.bubble",
    "statusbar.status.enhancing": "sparkles",
    "statusbar.status.preview": "eye",
    "statusbar.status.empty": "mic.slash",
    "statusbar.status.error": "exclamationmark.triangle",
    "statusbar.status.config_error": "exclamationmark.triangle",
    "statusbar.status.switching": "arrow.triangle.2.circlepath",
    "statusbar.status.loading": "cpu",
    "statusbar.status.unloading": "arrow.up.circle",
    "statusbar.status.downloading": "arrow.down.circle",
    "statusbar.status.restoring": "arrow.counterclockwise",
    "statusbar.status.checking": "cpu",
    "statusbar.status.clearing": "arrow.counterclockwise",
}

# Cache for SF Symbol NSImage objects
_sf_symbol_cache: dict[str, Any] = {}

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

    def __init__(self, config_dir: str | None = None) -> None:
        # Load config and init i18n BEFORE super().__init__() so t() is available
        import os

        from wenzi.i18n import init_i18n

        migrate_legacy_paths()
        migrate_xdg_paths()
        self._config_dir = resolve_config_dir(config_dir)
        self._data_dir = resolve_data_dir()
        self._cache_dir = resolve_cache_dir()
        self._config_path = os.path.join(self._config_dir, "config.json")
        self._config, config_error = load_config(self._config_path)
        self._config_error = config_error
        self._config_degraded = config_error is not None
        if self._config_degraded:
            set_config_readonly(True)

        init_i18n(locale=self._config.get("language"))

        super().__init__(t("app.name"), icon=None, title=t("statusbar.status.ready"))
        self._current_status = "statusbar.status.ready"

        # Seed the SF Symbol icon so the first render shows an icon, not text
        nsimage = self._sf_symbol_image("mic.fill", t("app.name"))
        if nsimage is not None:
            self._icon_nsimage = nsimage
            self._title = None  # clear text; icon takes over
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
        self._current_remote_asr: tuple[str, str] | None = None
        default_provider = asr_cfg.get("default_provider")
        default_model = asr_cfg.get("default_model")

        # Manual vocabulary store: user-curated correction pairs
        from wenzi.enhance.manual_vocabulary import ManualVocabularyStore
        vocab_cfg = self._config.get("ai_enhance", {}).get("vocabulary", {})
        self._manual_vocab_store = ManualVocabularyStore(
            path=os.path.join(self._data_dir, "manual_vocabulary.db"),
            stats_include_app=vocab_cfg.get("stats_include_app", False),
        )

        # Load vocabulary hotwords for ASR injection
        hotwords = self._load_hotwords()

        # Default backend depends on build type
        default_backend = "apple" if get_build_type() == "lite" else "funasr"

        if default_provider and default_model:
            # Start with remote model
            providers = asr_cfg.get("providers", {})
            pcfg = providers.get(default_provider, {})
            if pcfg and default_model in pcfg.get("models", []):
                self._current_remote_asr = (default_provider, default_model)
                self._transcriber = self._create_transcriber_for_remote(
                    base_url=pcfg["base_url"],
                    api_key=pcfg["api_key"],
                    model=default_model,
                    asr_cfg=asr_cfg,
                )
            else:
                # Provider/model not found, fall back to local
                self._transcriber = self._create_local_transcriber(
                    asr_cfg, default_backend, hotwords
                )
        else:
            self._transcriber = self._create_local_transcriber(
                asr_cfg, default_backend, hotwords
            )

        self._output_method = self._config["output"]["method"]
        self._append_newline = self._config["output"]["append_newline"]
        self._preview_enabled = self._config["output"].get("preview", True)
        self._hotkey_listener: MultiHotkeyListener | None = None
        self._voice_input_available = True
        self._busy = False
        self._preview_panel = ResultPreviewPanel()
        self._conversation_history = ConversationHistory(data_dir=self._data_dir)
        self._usage_stats = UsageStats(data_dir=self._data_dir)

        # Feedback: sound + visual indicator
        fb_cfg = self._config.get("feedback", {})
        self._sound_manager = SoundManager(
            enabled=fb_cfg.get("sound_enabled", True),
            volume=fb_cfg.get("sound_volume", 0.1),
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
        self._current_preset_id: str | None = None
        if not self._current_remote_asr:
            self._current_preset_id = asr_cfg.get("preset")
            if not self._current_preset_id:
                self._current_preset_id = resolve_preset_from_config(
                    asr_cfg.get("backend", default_backend),
                    asr_cfg.get("model"),
                )

        # Menu items
        self._status_item = StatusMenuItem(t("statusbar.status.ready"))
        self._status_item.set_callback(None)
        # Hotkey submenu
        self._hotkey_menu = StatusMenuItem(t("menu.hotkey"))
        self._hotkey_menu_items: dict[str, StatusMenuItem] = {}
        self._hotkey_record_item = StatusMenuItem(
            t("menu.record_hotkey"), callback=self._on_record_hotkey
        )
        self._menu_builder = MenuBuilder(self)
        self._model_controller = ModelController(self)
        self._settings_controller = SettingsController(self)
        self._recording_controller = RecordingFlow(self)
        self._preview_controller = PreviewController(self)
        self._config_controller = ConfigController(self)
        self._enhance_mode_controller = EnhanceModeController(self)
        self._menu_builder.build_hotkey_menu()

        # STT Model submenu
        self._model_menu = StatusMenuItem(t("menu.stt_model"))
        self._model_menu_items: dict[str, StatusMenuItem] = {}
        self._remote_asr_menu_items: dict[tuple[str, str], StatusMenuItem] = {}
        self._asr_add_provider_item = StatusMenuItem(
            t("menu.asr_add_provider"), callback=self._model_controller.on_asr_add_provider
        )
        self._asr_remove_provider_menu = StatusMenuItem(t("menu.asr_remove_provider"))
        self._asr_remove_provider_items: dict[str, StatusMenuItem] = {}
        self._menu_builder.build_model_menu()

        # AI Enhance
        self._enhancer = create_enhancer(
            self._config,
            config_dir=self._config_dir,
            data_dir=self._data_dir,
            cache_dir=self._cache_dir,
            conversation_history=self._conversation_history,
            manual_vocab_store=self._manual_vocab_store,
        )
        ai_cfg = self._config.get("ai_enhance", {})
        self._enhance_mode: str = ai_cfg.get("mode", "proofread")
        if self._enhancer and not ai_cfg.get("enabled", False):
            self._enhance_mode = MODE_OFF

        self._enhance_controller = EnhanceController(
            enhancer=self._enhancer,
            preview_panel=self._preview_panel,
            usage_stats=self._usage_stats,
            manual_vocab_store=self._manual_vocab_store,
        )
        self._enhance_controller.enhance_mode = self._enhance_mode

        # AI Enhance submenu (mode selection only)
        self._enhance_menu = StatusMenuItem(t("menu.ai_enhance"))
        self._enhance_menu_items: dict[str, StatusMenuItem] = {}

        # Fixed "Off" item
        off_item = StatusMenuItem(t("menu.ai_enhance.off"))
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
            t("menu.ai_enhance.add_mode"), callback=self._on_enhance_add_mode
        )
        self._enhance_menu.add(self._enhance_add_mode_item)

        # Top-level toggle items
        history_enabled = ai_cfg.get("conversation_history", {}).get("enabled", False)
        self._enhance_history_item = StatusMenuItem(
            t("menu.conversation_history"), callback=self._on_history_toggle
        )
        self._enhance_history_item.state = 1 if history_enabled else 0

        self._browse_history_item = StatusMenuItem(
            t("menu.browse_history"), callback=self._on_browse_history
        )

        # Vocabulary manager (lazy-created)
        self._vocab_controller = None
        self._vocab_manager_item = StatusMenuItem(
            t("menu.vocabulary"), callback=self._on_open_vocab_manager
        )

        # LLM Model top-level submenu
        self._llm_model_menu = StatusMenuItem(t("menu.llm_model"))
        self._llm_model_menu_items: dict[tuple[str, str], StatusMenuItem] = {}
        self._llm_add_provider_item = StatusMenuItem(
            t("menu.llm_add_provider"), callback=self._model_controller.on_enhance_add_provider
        )
        self._llm_remove_provider_menu = StatusMenuItem(t("menu.llm_remove_provider"))
        self._llm_remove_provider_items: dict[str, StatusMenuItem] = {}
        self._menu_builder.build_llm_model_menu()

        # AI Settings submenu (low-frequency AI configuration)
        self._ai_settings_menu = StatusMenuItem(t("menu.ai_settings"))

        # Thinking toggle
        self._enhance_thinking_item = StatusMenuItem(
            t("menu.ai_settings.thinking"), callback=self._on_enhance_thinking_toggle
        )
        if self._enhancer and self._enhancer.thinking:
            self._enhance_thinking_item.state = 1
        self._ai_settings_menu.add(self._enhance_thinking_item)

        self._ai_settings_menu.add(None)
        self._enhance_edit_config_item = StatusMenuItem(
            t("menu.ai_settings.edit_config"), callback=self._on_enhance_edit_config
        )
        self._ai_settings_menu.add(self._enhance_edit_config_item)

        self._preview_item = StatusMenuItem(
            t("menu.preview"), callback=self._on_preview_toggle
        )
        self._preview_item.state = 1 if self._preview_enabled else 0

        self._clipboard_enhance_item = StatusMenuItem(
            t("menu.enhance_clipboard"), callback=self._preview_controller.on_clipboard_enhance
        )

        self._screenshot_item = StatusMenuItem(
            "Screenshot", callback=self._on_screenshot
        )
        self._screenshot_hotkey_listener = None
        self._clipboard_hotkey_listener = None
        self._screenshot_annotation = None

        # Feedback toggle items
        self._sound_feedback_item = StatusMenuItem(
            t("menu.sound_feedback"), callback=self._recording_controller.on_sound_feedback_toggle
        )
        self._sound_feedback_item.state = 1 if self._sound_manager.enabled else 0

        self._visual_indicator_item = StatusMenuItem(
            t("menu.visual_indicator"), callback=self._recording_controller.on_visual_indicator_toggle
        )
        self._visual_indicator_item.state = 1 if self._recording_indicator.enabled else 0

        # View Logs top-level item (replaces Debug submenu)
        self._view_logs_item = StatusMenuItem(
            t("menu.view_logs"), callback=self._on_view_logs
        )

        # Show Config / Reload Config items
        self._show_config_item = StatusMenuItem(
            t("menu.show_config"), callback=self._on_show_config
        )
        self._reload_config_item = StatusMenuItem(
            t("menu.reload_config"), callback=self._on_reload_config
        )

        # Usage Stats item
        self._usage_stats_item = StatusMenuItem(
            t("menu.usage_stats"), callback=self._on_show_usage_stats
        )

        # Restart / About / Help items
        self._restart_item = StatusMenuItem(t("menu.restart"), callback=self._on_restart)
        self._about_item = StatusMenuItem(t("menu.about"), callback=self._on_about)
        self._help_item = StatusMenuItem(
            t("menu.help"), callback=self._menu_builder.on_help_click
        )

        # History browser (lazy-created)
        self._history_browser = None

        # Settings panel
        self._settings_panel = SettingsPanel()
        self._settings_item = StatusMenuItem(
            t("menu.settings"), callback=self._on_open_settings
        )

        if self._config_degraded:
            self._config_error_item = StatusMenuItem(
                t("menu.config_error"), callback=lambda _: self._show_config_error_alert()
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
                self._screenshot_item,
                self._browse_history_item,
                self._vocab_manager_item,
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

        # Dedicated log file for connection pool monitor
        pool_logger = logging.getLogger("wenzi.enhance.pool_monitor")
        if not pool_logger.handlers:
            pool_log_file = LOG_DIR / "pool_monitor.log"
            pool_handler = logging.handlers.RotatingFileHandler(
                pool_log_file, maxBytes=2 * 1024 * 1024, backupCount=2,
                encoding="utf-8",
            )
            pool_handler.setLevel(logging.DEBUG)
            pool_handler.setFormatter(logging.Formatter(fmt))
            pool_logger.addHandler(pool_handler)
        pool_logger.propagate = False

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

    @staticmethod
    def _create_local_transcriber(asr_cfg: dict, default_backend: str, hotwords):
        """Create a local ASR transcriber from config.

        The transcriber object is lightweight — heavy model loading happens
        later in initialize(). Backend availability is checked in
        _init_models() before initialize() is called.
        """
        return create_transcriber(
            backend=asr_cfg.get("backend", default_backend),
            use_vad=asr_cfg.get("use_vad", True),
            use_punc=asr_cfg.get("use_punc", True),
            language=asr_cfg.get("language"),
            model=asr_cfg.get("model"),
            temperature=asr_cfg.get("temperature"),
            hotwords=hotwords,
        )

    def _create_transcriber_for_preset(self, preset, asr_cfg=None):
        """Create a transcriber from a ModelPreset with common config.

        Reads use_vad, use_punc, temperature, and hotwords from asr_cfg.
        Language falls back from preset to config.
        """
        if asr_cfg is None:
            asr_cfg = self._config["asr"]
        return create_transcriber(
            backend=preset.backend,
            use_vad=asr_cfg.get("use_vad", True),
            use_punc=asr_cfg.get("use_punc", True),
            language=preset.language or asr_cfg.get("language"),
            model=preset.model,
            temperature=asr_cfg.get("temperature"),
            hotwords=self._load_hotwords(),
        )

    def _create_transcriber_for_remote(
        self, base_url, api_key, model, asr_cfg=None
    ):
        """Create a whisper-api transcriber for a remote ASR provider."""
        if asr_cfg is None:
            asr_cfg = self._config["asr"]
        return create_transcriber(
            backend="whisper-api",
            base_url=base_url,
            api_key=api_key,
            model=model,
            language=asr_cfg.get("language"),
            temperature=asr_cfg.get("temperature"),
            hotwords=self._load_hotwords(),
        )

    def _load_hotwords(self):
        """Load manual vocabulary hotwords for static ASR injection."""
        try:
            terms = self._manual_vocab_store.get_asr_hotwords()
            if terms:
                logger.info("Loaded %d manual hotwords for ASR injection", len(terms))
                logger.debug("Hotwords: %s", ", ".join(terms))
                return terms
        except Exception:
            logger.warning("Failed to load manual hotwords", exc_info=True)
        return None

    def _build_dynamic_hotwords(self) -> tuple:
        """Build hotword list from manual vocabulary for current transcription.

        Returns a ``(terms, details)`` tuple where *terms* is
        ``Optional[List[str]]`` for ASR injection and *details* is
        ``List[HotwordDetail]`` for the preview panel display.
        """
        from wenzi.enhance.vocabulary import build_hotword_list_detailed

        vocab_cfg = self._config.get("ai_enhance", {}).get("vocabulary", {})
        max_hotwords = vocab_cfg.get("max_dynamic_hotwords", 10)

        asr_model = self._current_stt_model()
        input_ctx = getattr(self, "_recording_controller", None)
        input_ctx = getattr(input_ctx, "input_context", None) if input_ctx else None
        app_bundle_id = getattr(input_ctx, "bundle_id", None) if input_ctx else None

        details = build_hotword_list_detailed(
            max_count=max_hotwords,
            asr_model=asr_model,
            app_bundle_id=app_bundle_id,
            manual_vocab_store=self._manual_vocab_store,
        )

        terms = [d.term for d in details]
        return (terms if terms else None), details

    def _set_status(self, status_key: str) -> None:
        """Update menu bar icon/title and status menu item (thread-safe).

        ``status_key`` is either an i18n key (e.g. "statusbar.status.ready")
        that exists in ``_STATUS_ICONS``, or a dynamic string like "DL 50%"
        or "VB +3".  The translated text is derived via ``t()`` and shown in
        the dropdown menu; the icon is resolved from ``_STATUS_ICONS``.
        """
        import Foundation
        if not Foundation.NSThread.isMainThread():
            from PyObjCTools import AppHelper
            AppHelper.callAfter(self._set_status, status_key)
            return

        self._current_status = status_key
        display_text = t(status_key)
        self._status_item.title = display_text  # dropdown menu always shows text

        # Resolve SF Symbol
        symbol_name = _STATUS_ICONS.get(status_key)
        bar_title = None
        if symbol_name is None:
            if status_key.startswith("DL "):
                symbol_name = "arrow.down.circle"
                bar_title = status_key[3:]  # show "X%" next to icon
            elif status_key.startswith("VB "):
                symbol_name = "book.fill"
                bar_title = status_key[3:]  # show "+N" next to icon
            else:
                symbol_name = "mic.fill"  # safe fallback

        nsimage = self._sf_symbol_image(symbol_name, display_text)
        if nsimage is not None:
            self._icon_nsimage = nsimage
            self._update_status_bar_icon()
            self.title = bar_title  # clear text when icon is set
        else:
            self.title = display_text  # fallback to text-only if SF Symbols unavailable

    def _refresh_status_bar(self) -> None:
        """Re-apply the current status to the menubar icon.

        Called on the next run-loop iteration after a script reload so
        that macOS re-renders the status item even if the internal
        NSStatusBar layout dropped it during the reload cycle.
        """
        self._set_status(self._current_status)

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
        alert.setMessageText_(t("app.hotkey_dialog.title", key=key_name))
        state_text = t("app.hotkey_dialog.enabled") if enabled else t("app.hotkey_dialog.disabled")
        toggle_text = t("app.hotkey_dialog.disable") if enabled else t("app.hotkey_dialog.enable")
        alert.setInformativeText_(t("app.hotkey_dialog.info", key=key_name, state=state_text))
        alert.addButtonWithTitle_(t("common.cancel"))
        alert.addButtonWithTitle_(toggle_text)
        if not is_fn:
            alert.addButtonWithTitle_(t("common.delete"))

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
        alert.setMessageText_(t("app.hotkey_dialog.record_title"))
        alert.setInformativeText_(t("app.hotkey_dialog.record_message"))
        alert.addButtonWithTitle_(t("common.cancel"))
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
        alert.setMessageText_(t("app.hotkey_dialog.combo_title"))
        alert.setInformativeText_(
            t("app.hotkey_dialog.combo_message") + "\n\n"
            + t("app.hotkey_dialog.combo_instructions")
        )
        alert.addButtonWithTitle_(t("common.cancel"))
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
                text = t("app.hotkey_dialog.combo_message")
            elif mods and not trigger:
                text = format_combo_display(mods, None)
            else:
                text = format_combo_display(mods, trigger)
                text += "\n" + t("app.hotkey_dialog.combo_confirm_hint")
            text += "\n\n" + t("app.hotkey_dialog.combo_instructions")

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
        audio_duration: float = 0.0, wav_data: bytes | None = None,
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

    def _on_history_toggle(self, sender) -> None:
        self._enhance_mode_controller.on_history_toggle(sender)

    def _on_preview_toggle(self, sender) -> None:
        self._enhance_mode_controller.on_preview_toggle(sender)

    # ── Config / debug / info display (ConfigController) ─────────

    def _on_enhance_edit_config(self, _) -> None:
        self._config_controller.on_enhance_edit_config(_)

    def _on_view_logs(self, _) -> None:
        self._config_controller.on_view_logs(_)

    def _on_log_level_change(self, level_name: str) -> None:
        self._config_controller.on_log_level_change(level_name)

    def _on_show_config(self, _) -> None:
        self._config_controller.on_show_config(_)

    def _on_reload_config(self, _) -> None:
        self._config_controller.on_reload_config(_)

    def _on_browse_history(self, _=None) -> None:
        self._config_controller.on_browse_history(_)

    def _on_open_vocab_manager(self, _=None) -> None:
        if self._vocab_controller is None:
            from wenzi.controllers.vocab_controller import VocabController

            self._vocab_controller = VocabController(self)
        self._vocab_controller.on_open_vocab_manager(_)

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

    # ── Screenshot ────────────────────────────────────────────────────────

    def _on_screenshot(self, _=None) -> None:
        """Handle screenshot hotkey press or menu item click.

        May be called from a background thread (Quartz event tap).
        Runs macOS ``screencapture -i`` in a background thread, then
        opens the annotation UI on the main thread.
        """
        import threading

        def _capture_and_show():
            import subprocess
            import tempfile

            tmp_dir = os.path.expanduser("~/.cache/WenZi/screenshot_tmp")
            os.makedirs(tmp_dir, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(suffix=".png", dir=tmp_dir)
            os.close(fd)

            result = subprocess.run(
                ["screencapture", "-i", "-x", tmp_path],
                capture_output=True,
            )

            if result.returncode != 0 or not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) == 0:
                # User cancelled or screencapture failed
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                return

            from PyObjCTools import AppHelper

            AppHelper.callAfter(self._show_annotation_ui, tmp_path)

        threading.Thread(target=_capture_and_show, daemon=True).start()

    def _show_annotation_ui(self, image_path: str) -> None:
        """Open the annotation UI for a captured screenshot (main thread)."""
        if self._screenshot_annotation is not None:
            try:
                self._screenshot_annotation.close()
            except Exception:
                pass
            self._screenshot_annotation = None

        from wenzi.screenshot import AnnotationLayer

        self._screenshot_annotation = AnnotationLayer()
        self._screenshot_annotation.show(
            image_path=image_path,
            on_done=self._on_screenshot_done,
            on_cancel=self._on_screenshot_cancel,
            delete_on_close=True,
        )

    def _on_screenshot_done(self) -> None:
        """Screenshot completed — AnnotationLayer already closed itself."""
        self._screenshot_annotation = None

    def _on_screenshot_cancel(self) -> None:
        """Screenshot cancelled — AnnotationLayer already closed itself."""
        self._screenshot_annotation = None

    def _on_quit_click(self, _) -> None:
        self._update_controller.stop()
        if hasattr(self, "_script_engine") and self._script_engine:
            self._script_engine.stop()
        if self._hotkey_listener:
            self._hotkey_listener.stop()
        if self._clipboard_hotkey_listener:
            self._clipboard_hotkey_listener.stop()
        if self._screenshot_hotkey_listener:
            self._screenshot_hotkey_listener.stop()
        if self._settings_panel.is_visible:
            self._settings_panel.close()
        if self._vocab_controller is not None:
            self._vocab_controller.close_panel()
        # Close active UI panels and release resources
        try:
            self._recording_indicator.hide()
        except Exception:
            logger.debug("Recording indicator hide failed", exc_info=True)
        try:
            self._streaming_overlay.close()
        except Exception:
            logger.debug("Streaming overlay close failed", exc_info=True)
        try:
            self._transcriber.cleanup()
        except Exception:
            logger.debug("Transcriber cleanup failed", exc_info=True)
        try:
            self._preview_panel.close()
        except Exception:
            logger.debug("Preview panel close failed", exc_info=True)
        try:
            if self._history_browser is not None:
                self._history_browser.close()
        except Exception:
            logger.debug("History browser close failed", exc_info=True)
        try:
            if self._screenshot_annotation is not None:
                self._screenshot_annotation.close()
        except Exception:
            logger.debug("Screenshot annotation close failed", exc_info=True)
        # Clean up preview history temp files
        try:
            self._preview_controller._preview_history.shutdown()
        except Exception:
            logger.debug("Preview history shutdown failed", exc_info=True)
        # Flush usage stats to disk before shutting down
        try:
            self._usage_stats.shutdown()
        except Exception:
            logger.debug("Usage stats shutdown failed", exc_info=True)
        # Close manual vocabulary database
        try:
            self._manual_vocab_store.close()
        except Exception:
            logger.debug("Manual vocab store close failed", exc_info=True)
        # Shut down the input context executor
        try:
            from wenzi.input_context import shutdown_input_context
            shutdown_input_context()
        except Exception:
            logger.debug("Input context shutdown failed", exc_info=True)
        # Flush encrypted vault to disk before shutting down
        try:
            from wenzi.vault import shutdown_vault
            shutdown_vault()
        except Exception:
            logger.debug("Vault shutdown failed", exc_info=True)
        # Close AI provider clients and shut down the shared asyncio loop
        if self._enhancer:
            try:
                async_loop.submit(self._enhancer.close()).result(timeout=5)
            except Exception:
                logger.debug("Enhancer close timed out or failed", exc_info=True)
        async_loop.shutdown_sync(timeout=5)
        from wenzi.statusbar import cleanup_callbacks

        cleanup_callbacks()
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
        # ResultPreviewPanel warmup intentionally deferred to first show()
        # to avoid loading WebKit framework at startup (~8-12 MB).
        # _build_panel() handles the cold path transparently.

    def _show_config_error(self) -> None:
        """Show config error alert if config loading failed."""
        if self._config_error is None:
            return
        self._set_status("statusbar.status.config_error")
        self._show_config_error_alert()

    def _show_config_error_alert(self) -> None:
        """Show an alert about the config error with a 'Show in Finder' button."""
        if self._config_error is None:
            return
        result = topmost_alert(
            title=t("app.config_error.title"),
            message=t(
                "app.config_error.message",
                path=self._config_error.path,
                error=self._config_error.message,
            ),
            ok=t("app.config_error.show_finder"),
            cancel=t("common.close"),
        )
        restore_accessory()
        if result:
            import subprocess
            subprocess.Popen(
                ["open", "-R", self._config_error.path],
            )

    def _apply_fallback_preset(self, fallback) -> None:
        """Replace the current transcriber with a fallback preset.

        Updates the transcriber, preset ID, and menu checkmarks.
        Called from _init_models() when the configured backend is
        unavailable or Siri/Dictation is disabled.
        """
        self._transcriber = self._create_transcriber_for_preset(fallback)
        self._current_preset_id = fallback.id
        self._menu_builder.update_model_checkmarks()

    def _handle_no_voice_backend(self) -> None:
        """Handle the case where no voice backend is available.

        Shows a three-option dialog:
        - Open Settings: opens Siri settings, voice input via hotkey later
        - Set Up Later: skip for now, prompt again on next launch
        - Don't Ask Again: persist preference, stop hotkey listeners

        In all cases, voice input is marked unavailable for this session.
        The user can still try via hotkey press (Open Settings / Set Up Later)
        which will attempt initialization on demand.
        """
        from .transcription.apple import prompt_siri_setup

        asr_cfg = self._config["asr"]

        # If user previously chose "Don't Ask Again", skip silently
        if asr_cfg.get("voice_input_disabled"):
            logger.info("Voice input disabled by user preference")
            self._voice_input_available = False
            self._stop_voice_hotkeys()
            self._set_status("statusbar.status.ready")
            return

        choice = prompt_siri_setup()
        self._handle_dictation_setup_choice(choice)
        self._set_status("statusbar.status.ready")
        logger.info("Voice input not available, app running without ASR")

    def _handle_dictation_setup_choice(self, choice: str) -> None:
        """Handle the user's choice from the Dictation setup dialog.

        Shared by _handle_no_voice_backend (startup) and
        RecordingController._show_dictation_setup (hotkey press).
        """
        from .transcription.apple import (
            KEYBOARD_SETTINGS_URL,
            SIRI_SETUP_DONT_ASK,
            SIRI_SETUP_OPEN_SETTINGS,
        )

        if choice == SIRI_SETUP_OPEN_SETTINGS:
            import subprocess

            subprocess.Popen(["open", KEYBOARD_SETTINGS_URL])
        elif choice == SIRI_SETUP_DONT_ASK:
            self._config["asr"]["voice_input_disabled"] = True
            save_config(self._config, self._config_path)
            self._stop_voice_hotkeys()

        self._voice_input_available = False

    def _stop_voice_hotkeys(self) -> None:
        """Stop the voice recording hotkey listener."""
        from PyObjCTools import AppHelper

        def _stop():
            if self._hotkey_listener:
                self._hotkey_listener.stop()
                self._hotkey_listener = None

        if threading.current_thread() is threading.main_thread():
            _stop()
        else:
            AppHelper.callAfter(_stop)

    def _show_model_load_error_alert(self, error: Exception) -> None:
        """Show alert when model initialization fails, with option to clear cache."""
        preset_id = self._current_preset_id
        preset = PRESET_BY_ID.get(preset_id) if preset_id else None
        # Only offer cache clear for local models that have a cache directory
        can_clear = preset is not None and preset.backend not in ("apple", "whisper-api")

        if can_clear:
            result = topmost_alert(
                title=t("app.model_error.title"),
                message=t("app.model_error.cache_message", error=str(error)[:200]),
                ok=t("app.model_error.cache_retry"),
                cancel=t("common.close"),
            )
            restore_accessory()
            if result == 1:
                self._clear_cache_and_reinitialize(preset)
        else:
            topmost_alert(
                title=t("app.model_error.title"),
                message=t("app.model_error.generic_message", error=str(error)[:200]),
            )
            restore_accessory()

    def _clear_cache_and_reinitialize(self, preset) -> None:
        """Clear model cache and retry initialization on a background thread."""
        def _do():
            stop_event = threading.Event()
            monitor_thread = None
            try:
                self._set_status("statusbar.status.clearing")
                clear_model_cache(preset)
                monitor_args = self._model_controller._make_download_monitor_args(preset)
                monitor_thread = threading.Thread(
                    target=self._model_controller._monitor_download_progress,
                    args=(stop_event, monitor_args),
                    daemon=True,
                )
                monitor_thread.start()
                self._transcriber.cleanup()
                self._transcriber = self._create_transcriber_for_preset(preset)
                self._transcriber.initialize()
                stop_event.set()
                monitor_thread.join(timeout=2)
                self._set_status("statusbar.status.ready")
                logger.info("Model reinitialized after cache clear")
            except Exception as e2:
                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)
                logger.error("Retry after cache clear failed: %s", e2)
                self._set_status("statusbar.status.error")
                topmost_alert(
                    title=t("app.model_error.title"),
                    message=t("app.model_error.retry_message", error=str(e2)[:200]),
                )
                restore_accessory()

        threading.Thread(target=_do, daemon=True).start()

    def run(self, **kwargs) -> None:
        """Initialize models and start the app."""
        self._ensure_accessibility()

        # Load models in background
        def _init_models():
            try:
                asr_cfg = self._config["asr"]
                preset = PRESET_BY_ID.get(self._current_preset_id)

                # Check if configured backend is installed
                if (
                    not self._current_remote_asr
                    and preset
                    and not is_backend_available(preset.backend)
                ):
                    fallback = PRESET_BY_ID["apple-speech-ondevice"]
                    logger.warning(
                        "Backend %r not available, using %s",
                        preset.backend,
                        fallback.display_name,
                    )
                    self._apply_fallback_preset(fallback)
                    preset = PRESET_BY_ID.get(self._current_preset_id)

                # For Apple Speech, verify Siri/Dictation before initializing
                if (
                    not self._current_remote_asr
                    and preset
                    and preset.backend == "apple"
                ):
                    from .transcription.apple import check_siri_available

                    self._set_status("statusbar.status.checking")
                    siri_ok, _ = check_siri_available(
                        language=asr_cfg.get("language") or "zh",
                        on_device=(preset.model == "on-device"),
                    )
                    if not siri_ok:
                        fallback = find_fallback_preset()
                        if fallback:
                            logger.warning(
                                "Siri/Dictation disabled, using %s for this session",
                                fallback.display_name,
                            )
                            self._apply_fallback_preset(fallback)
                        else:
                            # No alternative backend (e.g. Lite build)
                            self._handle_no_voice_backend()
                            return

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
                    self._set_status("statusbar.status.loading")

                self._transcriber.initialize()

                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)
                if not self._config_degraded:
                    self._set_status("statusbar.status.ready")
                logger.info("Models loaded, app ready")
            except Exception as e:
                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)
                logger.error("Model initialization failed: %s", e)
                if not self._config_degraded:
                    self._set_status("statusbar.status.error")
                self._show_model_load_error_alert(e)

        threading.Thread(target=_init_models, daemon=True).start()

        # Start hotkey listeners (skip if user disabled voice input)
        if not self._config["asr"].get("voice_input_disabled"):
            self._start_hotkey_listeners()

        # Start scripting engine if enabled
        scripting_cfg = self._config.setdefault("scripting", {})
        # Engine reads disabled_plugins from the scripting sub-config;
        # move it there if a legacy config still has it at the top level.
        if "disabled_plugins" in self._config:
            scripting_cfg.setdefault(
                "disabled_plugins", self._config.pop("disabled_plugins")
            )
        self._ua_controller = UniversalActionController(self)

        if scripting_cfg.get("enabled", False):
            from .scripting import ScriptEngine

            script_dir = scripting_cfg.get("script_dir")
            self._script_engine = ScriptEngine(
                script_dir=script_dir, config=scripting_cfg,
                plugins_dir=os.path.join(self._config_dir, "plugins"),
            )
            self._script_engine._ua_controller = self._ua_controller
            self._script_engine.start()
            self._script_engine.wz.menu._set_root(self._menu)
            self._script_engine.wz.menu._set_wz_ns(
                self._script_engine.wz
            )
            self._script_engine.set_open_settings_callback(
                lambda: self._on_open_settings(None)
            )

            self._script_engine.set_system_settings_open_callback(
                self._usage_stats.record_system_settings_open
            )
            self._script_engine.set_post_reload_callback(
                self._refresh_status_bar
            )

        # Start clipboard enhance hotkey listener if configured
        clip_hotkey = self._config.get("clipboard_enhance", {}).get("hotkey", "")
        if clip_hotkey:
            self._clipboard_hotkey_listener = TapHotkeyListener(
                hotkey_str=clip_hotkey,
                on_activate=self._preview_controller.on_clipboard_enhance,
            )
            self._clipboard_hotkey_listener.start()

        # Start screenshot hotkey listener if configured
        ss_cfg = self._config.get("screenshot", {})
        if ss_cfg.get("enabled", False) and ss_cfg.get("hotkey", ""):
            self._screenshot_hotkey_listener = TapHotkeyListener(
                hotkey_str=ss_cfg["hotkey"],
                on_activate=self._on_screenshot,
            )
            self._screenshot_hotkey_listener.start()

        # Schedule warmup after the event loop starts to pre-create heavy
        # objects (WKWebView, NSSound) so the first user interaction is snappy.
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._warmup)

        # Start background update checker
        if not self._config_degraded:
            AppHelper.callAfter(self._update_controller.start)

        # Hide status bar icon if configured (set flag before super().run()
        # creates the NSStatusItem, so the icon never appears)
        fb_cfg = self._config.get("feedback", {})
        if fb_cfg.get("hide_status_icon", False):
            self._status_item_hidden = True

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
    import faulthandler
    import signal

    faulthandler.enable()  # dump traceback on SIGSEGV/SIGABRT/SIGBUS
    signal.signal(signal.SIGINT, lambda *_: quit_application())

    config_dir = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("WENZI_CONFIG_DIR")
    app = WenZiApp(config_dir=config_dir)  # None uses default dir
    app.run()


if __name__ == "__main__":
    main()
