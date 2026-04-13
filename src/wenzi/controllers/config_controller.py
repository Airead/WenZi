"""Configuration, debug, and info display actions extracted from WenZiApp."""

from __future__ import annotations

import logging
import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wenzi.app import WenZiApp

from wenzi.config import load_config, save_config
from wenzi.i18n import t
from wenzi.statusbar import send_notification
from wenzi.transcription.model_registry import PRESET_BY_ID
from wenzi.ui_helpers import (
    activate_for_dialog,
    restore_accessory,
    topmost_alert,
)

logger = logging.getLogger(__name__)


class ConfigController:
    """Handles config display, reload, log viewer, usage stats, and about."""

    def __init__(self, app: WenZiApp) -> None:
        self._app = app

    def on_enhance_edit_config(self, _) -> None:
        """Open the config file in the default editor."""
        try:
            from wenzi.config import DEFAULT_CONFIG_PATH

            config_path = self._app._config_path or DEFAULT_CONFIG_PATH
            expanded = os.path.expanduser(config_path)
            subprocess.Popen(["open", expanded])
        except Exception as e:
            logger.error("Failed to open config file: %s", e, exc_info=True)

    def on_view_logs(self, _) -> None:
        """Open the in-app log viewer panel."""
        from wenzi.ui.log_viewer_window import LogViewerPanel

        app = self._app
        if not hasattr(app, "_log_viewer") or app._log_viewer is None:
            from wenzi.app import LOG_FILE

            app._log_viewer = LogViewerPanel(
                LOG_FILE,
                on_log_level_change=self.on_log_level_change,
                on_print_prompt_toggle=self.on_print_prompt_change,
                on_print_request_body_toggle=self.on_print_request_body_change,
            )
        current_level = app._config["logging"]["level"]
        print_prompt = bool(
            app._enhancer and app._enhancer.debug_print_prompt
        )
        print_request_body = bool(
            app._enhancer and app._enhancer.debug_print_request_body
        )
        app._log_viewer.show(
            current_level=current_level,
            print_prompt=print_prompt,
            print_request_body=print_request_body,
        )

    def on_log_level_change(self, level_name: str) -> None:
        """Handle log level change from the log viewer panel."""
        log_level = getattr(logging, level_name, logging.INFO)

        # Only update our own logger and handlers; root stays at INFO
        # to avoid flooding with third-party DEBUG output (numba, etc.).
        logging.getLogger("wenzi").setLevel(log_level)
        for handler in logging.getLogger().handlers:
            handler.setLevel(log_level)

        # Persist to config
        app = self._app
        app._config["logging"]["level"] = level_name
        save_config(app._config, app._config_path)
        logger.info("Log level changed to: %s", level_name)

    def on_print_prompt_change(self, enabled: bool) -> None:
        """Handle print prompt toggle from the log viewer panel."""
        if self._app._enhancer:
            self._app._enhancer.debug_print_prompt = enabled
        logger.info("Debug print prompt: %s", enabled)

    def on_print_request_body_change(self, enabled: bool) -> None:
        """Handle print request body toggle from the log viewer panel."""
        if self._app._enhancer:
            self._app._enhancer.debug_print_request_body = enabled
        logger.info("Debug print request body: %s", enabled)

    def build_config_info(self) -> str:
        """Build a summary string of current configuration."""
        app = self._app

        # ASR Model
        if app._current_remote_asr:
            pname, mname = app._current_remote_asr
            asr_model = f"{pname} / {mname} (remote)"
        else:
            preset = PRESET_BY_ID.get(app._current_preset_id)
            asr_model = preset.display_name if preset else app._current_preset_id or "N/A"

        # AI Enhance mode
        enhance_mode = app._enhance_mode if app._enhance_mode else "Off"

        _on = "\u2705"   # ✅
        _off = "\u274c"  # ❌

        # Provider / Model / Thinking
        if app._enhancer:
            provider = app._enhancer.provider_name or "N/A"
            model = app._enhancer.model_name or "N/A"
            thinking = _on if app._enhancer.thinking else _off
        else:
            provider = "N/A"
            model = "N/A"
            thinking = "N/A"

        preview = _on if app._preview_enabled else _off
        vocabulary = _on if app._enhance_vocab_item.state else _off
        history = _on if app._enhance_history_item.state else _off
        output = app._output_method
        hotkeys_dict = app._config.get("hotkeys", {"fn": True})
        active = [k for k, v in hotkeys_dict.items() if v]
        hotkey = ", ".join(active) if active else "none"
        log_level = app._config["logging"]["level"]
        from wenzi.config import DEFAULT_CONFIG_PATH
        config_path = os.path.expanduser(app._config_path or DEFAULT_CONFIG_PATH)

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

    def on_show_config(self, _) -> None:
        """Show current configuration in a dialog."""
        from AppKit import NSAlert, NSFont, NSStatusWindowLevel, NSTextField
        from Foundation import NSMakeRect

        info = self.build_config_info()

        activate_for_dialog()

        alert = NSAlert.alloc().init()
        alert.setMessageText_(t("alert.config.current_title"))
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
        restore_accessory()

    def on_reload_config(self, _) -> None:
        """Reload configuration from disk and apply changes."""
        from wenzi.enhance.enhancer import MODE_OFF

        app = self._app

        try:
            new_config, config_error = load_config(app._config_path)
        except Exception as e:
            logger.error("Failed to reload config: %s", e)
            send_notification(t("app.name"), t("notification.config.reload_failed"), str(e))
            return

        if config_error is not None:
            logger.error("Config reload error: %s", config_error)
            send_notification(t("app.name"), t("notification.config.error"), config_error.message)
            return

        app._config = new_config

        # Output settings
        app._output_method = new_config["output"]["method"]
        app._append_newline = new_config["output"]["append_newline"]
        app._preview_enabled = new_config["output"].get("preview", True)
        app._preview_item.state = 1 if app._preview_enabled else 0

        # Logging level — only update our own logger, not root
        level_name = new_config["logging"]["level"]
        log_level = getattr(logging, level_name, logging.INFO)
        logging.getLogger("wenzi").setLevel(log_level)
        for handler in logging.getLogger().handlers:
            handler.setLevel(log_level)

        # AI enhance settings
        ai_cfg = new_config.get("ai_enhance", {})
        if app._enhancer:
            new_mode = ai_cfg.get("mode", "proofread")
            if not ai_cfg.get("enabled", False):
                new_mode = MODE_OFF
            app._enhance_mode = new_mode
            app._enhance_controller.enhance_mode = new_mode
            if new_mode == MODE_OFF:
                app._enhancer._enabled = False
            else:
                app._enhancer._enabled = True
                app._enhancer.mode = new_mode
            for m, item in app._enhance_menu_items.items():
                item.state = 1 if m == new_mode else 0

            # Thinking
            app._enhancer.thinking = ai_cfg.get("thinking", False)
            app._enhance_thinking_item.state = 1 if app._enhancer.thinking else 0

            # Conversation history
            hist_cfg = ai_cfg.get("conversation_history", {})
            app._enhancer.history_enabled = hist_cfg.get("enabled", False)
            app._enhance_history_item.state = 1 if app._enhancer.history_enabled else 0

            # LLM provider/model
            new_provider = ai_cfg.get("default_provider")
            new_model = ai_cfg.get("default_model")
            if new_provider and new_model:
                app._enhancer.provider_name = new_provider
                app._enhancer.model_name = new_model
                current_key = (new_provider, new_model)
                for key, item in app._llm_model_menu_items.items():
                    item.state = 1 if key == current_key else 0

            # Reload enhancement mode definitions from disk
            app._enhancer.reload_modes()
            app._menu_builder.rebuild_enhance_mode_menu()

        # Feedback settings
        fb_cfg = new_config.get("feedback", {})
        app._sound_manager.enabled = fb_cfg.get("sound_enabled", True)
        app._sound_manager._volume = fb_cfg.get("sound_volume", 0.4)
        app._sound_feedback_item.state = 1 if app._sound_manager.enabled else 0

        app._recording_indicator.enabled = fb_cfg.get("visual_indicator", True)
        app._visual_indicator_item.state = 1 if app._recording_indicator.enabled else 0

        # Clipboard enhance hotkey
        clip_cfg = new_config.get("clipboard_enhance", {})
        new_clip_hotkey = clip_cfg.get("hotkey", "")
        if app._clipboard_hotkey_key is not None:
            app._app_hotkey_tap.remove(app._clipboard_hotkey_key)
            app._clipboard_hotkey_key = None
        if new_clip_hotkey:
            app._clipboard_hotkey_key = app._app_hotkey_tap.add(
                new_clip_hotkey, app._preview_controller.on_clipboard_enhance,
            )

        logger.info("Configuration reloaded successfully")
        send_notification(t("app.name"), t("notification.config.reloaded"), t("notification.config.reloaded.subtitle"))

    def on_browse_history(self, _=None) -> None:
        """Open the conversation history browser panel."""
        app = self._app
        if app._history_browser is None:
            from wenzi.ui.history_browser_window_web import HistoryBrowserPanel
            app._history_browser = HistoryBrowserPanel()

        def _on_history_save(timestamp: str, new_final_text: str) -> None:
            app._usage_stats.record_history_edit()

        app._usage_stats.record_history_browse_open()
        app._history_browser.show(
            conversation_history=app._conversation_history,
            on_save=_on_history_save,
        )

    def on_show_usage_stats(self, _) -> None:
        """Show usage statistics charts panel."""
        from wenzi.ui.stats_panel import StatsChartPanel

        app = self._app
        if not hasattr(app, "_stats_panel") or app._stats_panel is None:
            app._stats_panel = StatsChartPanel()
        app._stats_panel.show(app._usage_stats)

    def on_about(self, _) -> None:
        from wenzi import __version__
        from wenzi._build_info import BUILD_DATE, GIT_HASH

        message = f"Version: {__version__}\nBuild:   {GIT_HASH}\nDate:    {BUILD_DATE}"
        topmost_alert(title=t("alert.about.title"), message=message)
        restore_accessory()
