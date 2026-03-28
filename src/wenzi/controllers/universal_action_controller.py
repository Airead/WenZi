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

        text = get_selected_text() or ""

        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._show_ua_panel, text)

    def _show_ua_panel(self, text: str) -> None:
        """Register temp source and show the chooser in UA mode.  Main thread."""
        self._selected_text = text
        try:
            self._show_ua_panel_inner(text)
        except Exception:
            logger.exception("Universal Action: _show_ua_panel failed")

    def _show_ua_panel_inner(self, text: str) -> None:
        """Inner implementation — separated so exceptions are always logged."""
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
            search=_search,
            priority=999,
        )

        chooser = self._app._script_engine._wz.chooser
        chooser._panel.register_source(src)

        from wenzi.i18n import t

        def _on_close() -> None:
            chooser._panel.unregister_source(_UA_SOURCE_NAME)

        chooser.show_universal_action(
            context_text=text,
            exclusive_source=_UA_SOURCE_NAME,
            on_close=_on_close,
            initial_query="",
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
            for mode_id, label in enhancer.available_modes:
                captured_mode = mode_id

                def _enhance_action(m=captured_mode):
                    self._on_enhance_mode_selected(m)

                items.append(ChooserItem(
                    title=label,
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

        # 3. UA-registered sources — for sync sources, call search("") to
        #    get the help item (title, icon). For async sources, use metadata.
        try:
            sources = self._app._script_engine._wz.chooser._panel._sources
            for src_obj in sources.values():
                if not src_obj.universal_action:
                    continue

                help_item = None
                if not src_obj.is_async:
                    try:
                        help_items = src_obj.search("")
                        help_item = help_items[0] if help_items else None
                    except Exception:
                        pass

                captured_src = src_obj

                def _src_action(s=captured_src, txt=selected_text):
                    self._on_source_selected(s, txt)

                items.append(ChooserItem(
                    title=help_item.title if help_item else (src_obj.description or src_obj.name),
                    subtitle=help_item.subtitle if help_item else src_obj.name,
                    icon=help_item.icon if help_item else "",
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

        from PyObjCTools import AppHelper

        def _set_mode():
            app._enhance_mode = mode_id
            if app._enhancer:
                app._enhancer.mode = mode_id

        AppHelper.callAfter(_set_mode)

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
        """Call source search with selected text, execute first result's action."""
        import asyncio

        def _run():
            try:
                if source.is_async:
                    loop = asyncio.new_event_loop()
                    try:
                        results = loop.run_until_complete(source.search(text))
                    finally:
                        loop.close()
                else:
                    results = source.search(text)
                if not results:
                    return
                first = results[0]
                # Run action on this background thread — same as the normal
                # chooser deferred action path.  Actions that need the main
                # thread (e.g. panel.show) use callAfter internally.
                if first.action is not None:
                    first.action()
                elif first.preview:
                    from PyObjCTools import AppHelper

                    chooser = self._app._script_engine._wz.chooser
                    ql = chooser._panel._ql_panel
                    if ql is not None:
                        AppHelper.callAfter(ql.show_preview, first.preview)
            except Exception:
                logger.exception("UA source %s search failed", source.name)

        threading.Thread(target=_run, daemon=True).start()
