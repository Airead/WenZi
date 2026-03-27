"""Universal Action controller — orchestrates text capture and action dispatch."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, List

from wenzi.input import get_selected_text
from wenzi.scripting.sources import ChooserItem, ChooserSource, fuzzy_match

if TYPE_CHECKING:
    from wenzi.app import WenZiApp

logger = logging.getLogger(__name__)

_UA_SOURCE_NAME = "_universal_action"
_UA_PREFIX = "!"


class UniversalActionController:
    """Orchestrate Universal Action: capture text → show actions → route result."""

    def __init__(self, app: WenZiApp) -> None:
        self._app = app
        self._selected_text: str = ""

    def trigger(self) -> None:
        """Hotkey callback.  Runs on a background thread (Quartz event tap).

        Captures selected text, then dispatches UI work to the main thread.
        """
        rc = getattr(self._app, "_recording_controller", None)
        if rc is not None and getattr(rc, "_is_busy", False):
            logger.debug("Universal Action ignored: app is busy")
            return

        self._selected_text = get_selected_text() or ""

        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._show_ua_panel)

    def _show_ua_panel(self) -> None:
        """Register temp source and show the chooser in UA mode.  Main thread."""
        items = self._build_action_items()
        if not items:
            logger.info("Universal Action: no actions available")
            return

        def _search(query: str) -> List[ChooserItem]:
            if not query.strip():
                return items
            results = []
            for item in items:
                matched, _ = fuzzy_match(query, item.title)
                if not matched and item.subtitle:
                    matched, _ = fuzzy_match(query, item.subtitle)
                if matched:
                    results.append(item)
            return results

        src = ChooserSource(
            name=_UA_SOURCE_NAME,
            prefix=_UA_PREFIX,
            search=_search,
            priority=999,
        )

        chooser = self._app._script_engine._wz.chooser
        chooser._panel.register_source(src)

        from wenzi.i18n import t

        def _on_close() -> None:
            chooser._panel.unregister_source(_UA_SOURCE_NAME)

        chooser.show_universal_action(
            context_text=self._selected_text,
            on_close=_on_close,
            initial_query=_UA_PREFIX + " ",
            placeholder=t("chooser.ua.filter_placeholder"),
        )

    def _build_action_items(self) -> List[ChooserItem]:
        """Collect all Universal Action items."""
        items: List[ChooserItem] = []
        selected_text = self._selected_text

        # 1. Enhance modes
        enhancer = getattr(self._app, "_enhancer", None)
        if enhancer is not None:
            from wenzi.i18n import t

            subtitle = t("chooser.ua.enhance_subtitle")
            for mode_id, mode_def in enhancer.modes.items():
                captured_mode = mode_id

                def _enhance_action(m=captured_mode):
                    self._on_enhance_mode_selected(m)

                items.append(ChooserItem(
                    title=mode_def.label,
                    subtitle=subtitle,
                    item_id=f"ua:enhance:{mode_id}",
                    action=_enhance_action,
                ))

        # 2. UA-registered commands
        try:
            commands = self._app._script_engine._wz.chooser._command_source._commands
            for cmd in commands.values():
                if not cmd.universal_action:
                    continue
                captured_cmd = cmd

                def _cmd_action(c=captured_cmd, txt=selected_text):
                    self._on_command_selected(c, txt)

                items.append(ChooserItem(
                    title=cmd.title,
                    subtitle=cmd.subtitle,
                    icon=cmd.icon,
                    item_id=f"ua:cmd:{cmd.name}",
                    action=_cmd_action,
                ))
        except (AttributeError, TypeError):
            logger.debug("No command source available for UA")

        # 3. UA-registered sources
        try:
            sources = self._app._script_engine._wz.chooser._panel._sources
            for src_obj in sources.values():
                if not src_obj.universal_action:
                    continue
                captured_src = src_obj

                def _src_action(s=captured_src, txt=selected_text):
                    self._on_source_selected(s, txt)

                items.append(ChooserItem(
                    title=src_obj.description or src_obj.name,
                    subtitle=src_obj.name,
                    item_id=f"ua:src:{src_obj.name}",
                    action=_src_action,
                ))
        except (AttributeError, TypeError):
            logger.debug("No panel sources available for UA")

        return items

    def _on_enhance_mode_selected(self, mode_id: str) -> None:
        """Route selected text through the enhance pipeline via preview."""
        app = self._app
        text = self._selected_text
        if not text:
            return

        app._enhance_mode = mode_id
        if app._enhancer:
            app._enhancer.mode = mode_id

        preview_ctrl = getattr(app, "_preview_controller", None)
        if preview_ctrl is not None:
            threading.Thread(
                target=preview_ctrl._do_clipboard_with_preview,
                args=(text,),
                daemon=True,
            ).start()

    def _on_command_selected(self, cmd, text: str) -> None:
        """Execute a UA command with the selected text as args."""
        if cmd.action is not None:
            try:
                cmd.action(text)
            except Exception:
                logger.exception("UA command %s failed", cmd.name)

    def _on_source_selected(self, source, text: str) -> None:
        """Call source search with selected text, show preview of first result."""
        try:
            results = source.search(text)
            if results and results[0].preview:
                chooser = self._app._script_engine._wz.chooser
                ql = chooser._panel._ql_panel
                if ql is not None:
                    from PyObjCTools import AppHelper

                    AppHelper.callAfter(ql.show_preview, results[0].preview)
        except Exception:
            logger.exception("UA source %s search failed", source.name)
