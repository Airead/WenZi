"""Enhance mode management, vocabulary, and toggle actions extracted from VoiceTextApp."""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicetext.app import VoiceTextApp

from voicetext.config import save_config
from voicetext.statusbar import send_notification
from voicetext.enhance.vocabulary import get_vocab_entry_count
from voicetext.ui_helpers import (
    activate_for_dialog,
    restore_accessory,
    topmost_alert,
    run_window,
    run_multiline_window,
)

logger = logging.getLogger(__name__)


class EnhanceModeController:
    """Handles enhance mode selection, add mode, vocab toggles, and vocab build."""

    def __init__(self, app: VoiceTextApp) -> None:
        self._app = app

    def on_enhance_mode_select(self, sender) -> None:
        """Handle AI enhance mode menu item click."""
        from voicetext.enhance.enhancer import MODE_OFF

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
        from voicetext.enhance.mode_loader import DEFAULT_MODES_DIR, parse_mode_file

        resp = run_multiline_window(
            title="Add Enhancement Mode",
            message=(
                "Edit the template below, then click Save.\n\n"
                "  label  – display name in menu\n"
                "  order  – sort weight (smaller = higher)\n"
                "  body   – system prompt for the LLM"
            ),
            default_text=self._ADD_MODE_TEMPLATE,
            ok="Save",
            dimensions=(420, 220),
        )
        if resp is None:
            return

        # Ask for filename (mode ID)
        name_resp = run_window(
            title="Mode ID",
            message=(
                "Enter a short ID for this mode (used as filename).\n"
                "Only letters, numbers, hyphens, and underscores."
            ),
            default_text="my_mode",
        )
        if name_resp is None:
            return

        import re
        mode_id = name_resp.text.strip()
        if not mode_id or not re.match(r"^[A-Za-z0-9_-]+$", mode_id):
            activate_for_dialog()
            topmost_alert(
                "Invalid ID",
                "Mode ID must contain only letters, numbers, hyphens, or underscores.",
            )
            return

        modes_dir = os.path.expanduser(DEFAULT_MODES_DIR)
        os.makedirs(modes_dir, exist_ok=True)
        file_path = os.path.join(modes_dir, f"{mode_id}.md")

        if os.path.exists(file_path):
            activate_for_dialog()
            topmost_alert(
                "Already Exists",
                f"A mode file '{mode_id}.md' already exists.\n"
                "Edit it directly or choose a different ID.",
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
            topmost_alert("Invalid Content", "The mode file has no prompt content.")
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
        topmost_alert("Mode Added", f"Enhancement mode '{mode_id}' has been added.")

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

    def update_vocab_title(self) -> None:
        """Update the Vocabulary menu item title with the current entry count."""

        app = self._app
        count = 0
        if app._enhancer and app._enhancer.vocab_index is not None:
            count = app._enhancer.vocab_index.entry_count
        if count == 0:
            count = get_vocab_entry_count()

        if count > 0:
            app._enhance_vocab_item.title = f"Vocabulary ({count})"
        else:
            app._enhance_vocab_item.title = "Vocabulary"

    def on_vocab_toggle(self, sender) -> None:
        """Toggle vocabulary-based retrieval."""
        app = self._app
        if not app._enhancer:
            return

        new_value = not app._enhancer.vocab_enabled
        app._enhancer.vocab_enabled = new_value
        sender.state = 1 if new_value else 0

        # Persist to config
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"].setdefault("vocabulary", {})
        app._config["ai_enhance"]["vocabulary"]["enabled"] = new_value
        save_config(app._config, app._config_path)
        logger.info("Vocabulary set to: %s", new_value)

    def on_auto_build_toggle(self, sender) -> None:
        """Toggle automatic vocabulary building."""
        app = self._app
        new_value = not app._auto_vocab_builder._enabled
        app._auto_vocab_builder._enabled = new_value
        sender.state = 1 if new_value else 0

        # Persist to config
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"].setdefault("vocabulary", {})
        app._config["ai_enhance"]["vocabulary"]["auto_build"] = new_value
        save_config(app._config, app._config_path)
        logger.info("Auto vocabulary build set to: %s", new_value)

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

    def on_vocab_build(self, _sender) -> None:
        """Build vocabulary from correction logs in a background thread."""
        app = self._app
        if not app._enhancer:
            topmost_alert("AI Enhance is not configured.")
            return

        if app._auto_vocab_builder.is_building():
            topmost_alert("Vocabulary is being auto-built. Please wait.")
            return

        logger.info("Starting vocabulary build...")

        cancel_event = threading.Event()

        from voicetext.ui.vocab_build_window import VocabBuildProgressPanel

        # Build enhance info string for the progress panel
        enhance_info = ""
        if app._enhancer:
            parts = []
            if app._enhancer.provider_name:
                parts.append(app._enhancer.provider_name)
            if app._enhancer.model_name:
                parts.append(app._enhancer.model_name)
            enhance_info = " / ".join(parts)

        progress_panel = VocabBuildProgressPanel()
        # _on_vocab_build runs on the main thread (rumps callback), so show directly
        progress_panel.show(
            on_cancel=lambda: cancel_event.set(),
            enhance_info=enhance_info,
        )

        def _build():
            import asyncio as _asyncio

            from voicetext.enhance.vocabulary_builder import BuildCallbacks, VocabularyBuilder

            ai_cfg = app._config.get("ai_enhance", {})
            logger.info("VocabularyBuilder initializing...")
            builder = VocabularyBuilder(ai_cfg)

            callbacks = BuildCallbacks(
                on_batch_start=lambda i, t: (
                    progress_panel.clear_stream_text(),
                    progress_panel.update_status(f"Batch {i}/{t} — extracting..."),
                ),
                on_stream_chunk=lambda chunk: progress_panel.append_stream_text(chunk),
                on_batch_done=lambda i, t, c: progress_panel.update_status(
                    f"Batch {i}/{t} done — {c} entries found"
                ),
                on_usage_update=lambda p, c, t: progress_panel.update_token_usage(p, c, t),
            )

            old_status = app._current_status
            app._set_status("VT \u23f3")
            try:
                loop = _asyncio.new_event_loop()
                summary = loop.run_until_complete(
                    builder.build(cancel_event=cancel_event, callbacks=callbacks)
                )
                # Shut down async generators before closing the loop to avoid
                # "Task was destroyed but it is pending" warnings from streams
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

                # Reload vocabulary index if enhancer has one
                if app._enhancer and app._enhancer.vocab_index is not None:
                    app._enhancer.vocab_index.reload()
                self.update_vocab_title()

                cancelled = summary.get("cancelled", False)
                status = "Cancelled" if cancelled else "Built"
                msg = (
                    f"{summary['total_entries']} entries "
                    f"({summary['new_entries']} new)"
                )
                progress_panel.update_status(f"{status}: {msg}")
                try:
                    send_notification("VoiceText", f"Vocabulary {status}", msg)
                except Exception:
                    logger.debug("Notification center unavailable, skipping notification")
            except Exception as e:
                logger.error("Vocabulary build failed: %s", e)
                progress_panel.update_status(f"Failed: {e}")
                try:
                    send_notification(
                        "VoiceText", "Vocabulary Build Failed", str(e)
                    )
                except Exception:
                    logger.debug("Notification center unavailable, skipping notification")
            finally:
                app._set_status(old_status or "VT")
                progress_panel.close()

        t = threading.Thread(target=_build, daemon=True)
        t.start()

    def on_preview_toggle(self, sender) -> None:
        """Toggle preview window on/off."""
        app = self._app
        app._preview_enabled = not app._preview_enabled
        sender.state = 1 if app._preview_enabled else 0

        app._config["output"]["preview"] = app._preview_enabled
        save_config(app._config, app._config_path)
        logger.info("Preview set to: %s", app._preview_enabled)
