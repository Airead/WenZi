"""Enhance mode management and toggle actions extracted from WenZiApp."""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wenzi.app import WenZiApp

from wenzi.config import save_config
from wenzi.i18n import t
from wenzi.ui_helpers import (
    activate_for_dialog,
    restore_accessory,
    run_multiline_window,
    run_window,
    topmost_alert,
)

logger = logging.getLogger(__name__)


class EnhanceModeController:
    """Handles enhance mode selection, add mode, vocab toggles, and vocab build."""

    def __init__(self, app: WenZiApp) -> None:
        self._app = app

    def on_enhance_mode_select(self, sender) -> None:
        """Handle AI enhance mode menu item click."""
        from wenzi.enhance.enhancer import MODE_OFF

        app = self._app
        mode = sender._enhance_mode

        # Update checkmarks
        for m, item in app._enhance_menu_items.items():
            item.state = 1 if m == mode else 0

        app._enhance_mode = mode
        app._enhance_controller.enhance_mode = mode

        # Update enhancer state
        if app._enhancer:
            if mode == MODE_OFF:
                app._enhancer._enabled = False
            else:
                app._enhancer._enabled = True
                app._enhancer.mode = mode

        # Persist to config
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"]["enabled"] = mode != MODE_OFF
        app._config["ai_enhance"]["mode"] = mode
        save_config(app._config, app._config_path)
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

    def on_enhance_add_mode(self, _) -> None:
        """Show dialog for adding a new enhancement mode."""
        def _run():
            try:
                self._do_add_mode()
            except Exception as e:
                logger.error("Add mode failed: %s", e, exc_info=True)
            finally:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(restore_accessory)

        threading.Thread(target=_run, daemon=True).start()

    def _do_add_mode(self) -> None:
        """Internal implementation for adding a new enhancement mode file."""
        from wenzi.enhance.mode_loader import DEFAULT_MODES_DIR, parse_mode_file

        resp = run_multiline_window(
            title=t("alert.enhance_mode.add.title"),
            message=t("alert.enhance_mode.add.message"),
            default_text=self._ADD_MODE_TEMPLATE,
            ok=t("common.save"),
            dimensions=(420, 220),
        )
        if resp is None:
            return

        # Ask for filename (mode ID)
        name_resp = run_window(
            title=t("alert.enhance_mode.id.title"),
            message=t("alert.enhance_mode.id.message"),
            default_text="my_mode",
        )
        if name_resp is None:
            return

        import re
        mode_id = name_resp.text.strip()
        if not mode_id or not re.match(r"^[A-Za-z0-9_-]+$", mode_id):
            activate_for_dialog()
            topmost_alert(
                t("alert.enhance_mode.invalid_id.title"),
                t("alert.enhance_mode.invalid_id.message"),
            )
            return

        modes_dir = os.path.expanduser(DEFAULT_MODES_DIR)
        os.makedirs(modes_dir, exist_ok=True)
        file_path = os.path.join(modes_dir, f"{mode_id}.md")

        if os.path.exists(file_path):
            activate_for_dialog()
            topmost_alert(
                t("alert.enhance_mode.already_exists.title"),
                t("alert.enhance_mode.already_exists.message", id=mode_id),
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
            activate_for_dialog()
            topmost_alert(t("alert.enhance_mode.invalid_content.title"), t("alert.enhance_mode.invalid_content.message"))
            return

        # Save the file
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(resp.text)
            if not resp.text.endswith("\n"):
                f.write("\n")
        logger.info("Created new mode file: %s", file_path)

        # Reload modes and rebuild menu
        app = self._app
        if app._enhancer:
            app._enhancer.reload_modes()
            app._menu_builder.rebuild_enhance_mode_menu()

        activate_for_dialog()
        topmost_alert(t("alert.enhance_mode.added.title"), t("alert.enhance_mode.added.message", id=mode_id))

    def on_enhance_thinking_toggle(self, sender) -> None:
        """Toggle AI thinking mode."""
        app = self._app
        if not app._enhancer:
            return

        new_value = not app._enhancer.thinking
        app._enhancer.thinking = new_value
        sender.state = 1 if new_value else 0

        # Persist to config
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"]["thinking"] = new_value
        save_config(app._config, app._config_path)
        logger.info("AI thinking set to: %s", new_value)

    def on_history_toggle(self, sender) -> None:
        """Toggle conversation history context injection."""
        app = self._app
        if not app._enhancer:
            return

        new_value = not app._enhancer.history_enabled
        app._enhancer.history_enabled = new_value
        sender.state = 1 if new_value else 0

        # Persist to config
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"].setdefault("conversation_history", {})
        app._config["ai_enhance"]["conversation_history"]["enabled"] = new_value
        save_config(app._config, app._config_path)
        logger.info("Conversation history set to: %s", new_value)

    def on_preview_toggle(self, sender) -> None:
        """Toggle preview window on/off."""
        app = self._app
        app._preview_enabled = not app._preview_enabled
        sender.state = 1 if app._preview_enabled else 0

        app._config["output"]["preview"] = app._preview_enabled
        save_config(app._config, app._config_path)
        logger.info("Preview set to: %s", app._preview_enabled)
