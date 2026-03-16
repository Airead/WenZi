"""vt.chooser — Chooser panel API for user scripts."""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from voicetext.scripting.sources import ChooserItem, ChooserSource, ModifierAction
from voicetext.scripting.ui.chooser_panel import ChooserPanel

logger = logging.getLogger(__name__)


def _parse_modifiers(raw: Optional[Dict]) -> Optional[Dict[str, ModifierAction]]:
    """Convert user-script modifier dicts to ModifierAction objects.

    Accepts::

        {"alt": {"subtitle": "Show path", "action": callable}, ...}
    """
    if not raw:
        return None
    result: Dict[str, ModifierAction] = {}
    for key, val in raw.items():
        if isinstance(val, dict):
            result[key] = ModifierAction(
                subtitle=val.get("subtitle", ""),
                action=val.get("action"),
            )
    return result or None


def _dict_to_chooser_item(item: dict) -> ChooserItem:
    """Convert a plain dict (returned by user script) to a ChooserItem."""
    return ChooserItem(
        title=item.get("title", ""),
        subtitle=item.get("subtitle", ""),
        icon=item.get("icon", ""),
        item_id=item.get("item_id", ""),
        action=item.get("action"),
        secondary_action=item.get("secondary_action"),
        reveal_path=item.get("reveal_path"),
        modifiers=_parse_modifiers(item.get("modifiers")),
        delete_action=item.get("delete_action"),
        preview=item.get("preview"),
    )


class ChooserAPI:
    """API for the Chooser panel, exposed as vt.chooser."""

    def __init__(self) -> None:
        self._panel = ChooserPanel()
        self._panel._event_callback = self._fire_event
        self._event_handlers: Dict[str, List[Callable]] = {}

    @property
    def panel(self) -> ChooserPanel:
        """Access the underlying ChooserPanel."""
        return self._panel

    @property
    def is_visible(self) -> bool:
        """Whether the chooser panel is currently visible."""
        return self._panel.is_visible

    def _get_panel(self) -> ChooserPanel:
        """Internal access to the panel instance."""
        return self._panel

    def register_source(self, source: ChooserSource) -> None:
        """Register a data source."""
        self._panel.register_source(source)

    def unregister_source(self, name: str) -> None:
        """Remove a data source by name."""
        self._panel.unregister_source(name)

    def show(self, initial_query: Optional[str] = None) -> None:
        """Show the chooser panel.

        Args:
            initial_query: If set, pre-fill the search input with this value.
        """
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(
                self._panel.show, initial_query=initial_query,
            )
        except Exception:
            logger.exception("Failed to show chooser")

    def show_source(self, prefix: str) -> None:
        """Show the chooser with a specific source activated.

        Equivalent to the user typing ``prefix `` in the search input.
        """
        self.show(initial_query=prefix + " ")

    def close(self) -> None:
        """Close the chooser panel."""
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self._panel.close)
        except Exception:
            logger.exception("Failed to close chooser")

    def toggle(self) -> None:
        """Toggle the chooser panel visibility."""
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self._panel.toggle)
        except Exception:
            logger.exception("Failed to toggle chooser")

    # ------------------------------------------------------------------
    # pick() — use the chooser as a generic selection UI
    # ------------------------------------------------------------------

    def pick(
        self,
        items: List[dict],
        callback: Callable,
        placeholder: Optional[str] = None,
    ) -> None:
        """Show the chooser with a fixed list of items for the user to pick.

        When the user selects an item, *callback* is called with the original
        dict.  If the user dismisses the panel without selecting, *callback*
        is called with ``None``.

        Args:
            items: List of item dicts (same format as source search results).
            callback: ``callback(item_dict | None)``.
            placeholder: Optional placeholder text shown in the search input.
        """
        source_name = f"__pick_{id(callback)}"
        chooser_items = [_dict_to_chooser_item(d) for d in (items or [])]

        picked = False

        def _wrap_action(ci: ChooserItem, original: dict) -> Callable:
            original_action = ci.action

            def _action():
                nonlocal picked
                picked = True
                if original_action is not None:
                    original_action()
                callback(original)

            return _action

        for ci, orig in zip(chooser_items, items or []):
            ci.action = _wrap_action(ci, orig)

        def _search(query: str) -> List[ChooserItem]:
            if not query.strip():
                return chooser_items
            from voicetext.scripting.sources import fuzzy_match

            results = []
            for ci in chooser_items:
                matched, _ = fuzzy_match(query, ci.title)
                if not matched and ci.subtitle:
                    matched, _ = fuzzy_match(query, ci.subtitle)
                if matched:
                    results.append(ci)
            return results

        def _on_close():
            self._panel.unregister_source(source_name)
            if not picked:
                callback(None)

        src = ChooserSource(
            name=source_name,
            prefix=None,
            search=_search,
            priority=999,  # Highest priority so pick items appear first
        )
        self._panel.register_source(src)

        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self._panel.show, on_close=_on_close)
        except Exception:
            logger.exception("Failed to show chooser for pick()")
            self._panel.unregister_source(source_name)

    # ------------------------------------------------------------------
    # Event hooks
    # ------------------------------------------------------------------

    def on(self, event: str) -> Callable:
        """Decorator to register an event handler.

        Supported events: ``open``, ``close``, ``select``, ``delete``.

        Usage::

            @vt.chooser.on("select")
            def on_select(item):
                print(f"Selected: {item['title']}")
        """

        def decorator(func: Callable) -> Callable:
            self._event_handlers.setdefault(event, []).append(func)
            return func

        return decorator

    def _fire_event(self, event: str, *args) -> None:
        """Invoke all handlers registered for *event*."""
        for handler in self._event_handlers.get(event, []):
            try:
                handler(*args)
            except Exception:
                logger.exception("Chooser event handler error (%s)", event)

    # ------------------------------------------------------------------
    # Source decorator
    # ------------------------------------------------------------------

    def source(
        self,
        name: str,
        prefix: Optional[str] = None,
        priority: int = 0,
    ) -> Callable:
        """Decorator to register a search function as a chooser source.

        The decorated function receives a query string and returns a list of
        item dicts.  All :class:`ChooserItem` fields are supported::

            @vt.chooser.source("todos", prefix="td", priority=5)
            def search_todos(query):
                return [{
                    "title": "Fix bug #123",
                    "subtitle": "backend",
                    "icon": "data:image/png;base64,...",
                    "item_id": "todo-123",
                    "action": lambda: ...,
                    "secondary_action": lambda: ...,
                    "reveal_path": "/path/to/file",
                    "modifiers": {
                        "alt": {"subtitle": "Copy ID", "action": lambda: ...},
                    },
                    "delete_action": lambda: ...,
                    "preview": {"type": "text", "content": "..."},
                }]
        """

        def decorator(func: Callable[[str], List[dict]]) -> Callable:
            def _search(query: str) -> List[ChooserItem]:
                raw_items = func(query)
                return [
                    _dict_to_chooser_item(item)
                    for item in (raw_items or [])
                ]

            src = ChooserSource(
                name=name,
                prefix=prefix,
                search=_search,
                priority=priority,
            )
            self._panel.register_source(src)
            logger.info("User script registered chooser source: %s", name)
            return func

        return decorator
