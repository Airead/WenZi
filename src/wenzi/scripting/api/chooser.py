"""wz.chooser — Chooser panel API for user scripts."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Dict, List, Optional

from wenzi.scripting.api._async_util import wrap_async
from wenzi.scripting.sources import ChooserItem, ChooserSource, ModifierAction
from wenzi.scripting.sources.command_source import CommandEntry, CommandSource
from wenzi.scripting.ui.chooser_panel import ChooserPanel

logger = logging.getLogger(__name__)


def _wrap_optional(fn: Optional[Callable]) -> Optional[Callable]:
    """Wrap an optional callable with async support."""
    return wrap_async(fn) if fn is not None else None


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
                action=_wrap_optional(val.get("action")),
            )
    return result or None


def _convert_items(raw_items: Optional[List[dict]]) -> List[ChooserItem]:
    """Convert a list of raw dicts to ChooserItem objects."""
    return [_dict_to_chooser_item(item) for item in (raw_items or [])]


def _dict_to_chooser_item(item: dict) -> ChooserItem:
    """Convert a plain dict (returned by user script) to a ChooserItem."""
    return ChooserItem(
        title=item.get("title", ""),
        subtitle=item.get("subtitle", ""),
        icon=item.get("icon", ""),
        item_id=item.get("item_id", ""),
        action=_wrap_optional(item.get("action")),
        secondary_action=_wrap_optional(item.get("secondary_action")),
        reveal_path=item.get("reveal_path"),
        modifiers=_parse_modifiers(item.get("modifiers")),
        delete_action=_wrap_optional(item.get("delete_action")),
        confirm_delete=item.get("confirm_delete", False),
        preview=item.get("preview"),
        icon_badge=item.get("icon_badge", ""),
        icon_accessory=item.get("icon_accessory", ""),
    )


class ChooserAPI:
    """API for the Chooser panel, exposed as wz.chooser."""

    def __init__(self) -> None:
        self._panel = ChooserPanel()
        self._panel._event_callback = self._fire_event
        self._event_handlers: Dict[str, List[Callable]] = {}
        self._command_source = CommandSource()

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

    def _ensure_command_source(self) -> None:
        """Re-register the command source if it was removed (e.g. by disable_chooser)."""
        if "commands" not in self._panel._sources:
            self._panel.register_source(self._command_source.as_chooser_source())
        if "commands-promoted" not in self._panel._sources:
            self._panel.register_source(
                self._command_source.as_promoted_chooser_source(),
            )
        if "help" not in self._command_source._commands:
            self._register_help_command()
        if "quit-all" not in self._command_source._commands:
            self._register_quit_all_command()
        if "reload" not in self._command_source._commands:
            self._register_reload_command()
        if "settings" not in self._command_source._commands:
            self._register_settings_command()

    def _register_help_command(self) -> None:
        """Register the built-in help command."""

        def _help_action(args: str) -> None:
            # Snapshot sources on the calling thread
            sources = list(self._panel._sources.values())
            items = []
            for src in sorted(sources, key=lambda s: (s.prefix or "")):
                if not src.prefix:
                    continue
                desc = src.description or src.name
                # Filter by args if provided
                if args.strip():
                    from wenzi.scripting.sources import fuzzy_match

                    m1, _ = fuzzy_match(args.strip(), desc)
                    m2, _ = fuzzy_match(args.strip(), src.prefix)
                    if not m1 and not m2:
                        continue
                items.append(
                    {
                        "title": desc,
                        "subtitle": f"{src.prefix} <query>",
                        "item_id": f"help:{src.name}",
                        "action": lambda p=src.prefix: self.show_source(p),
                    }
                )
            if items:
                self.pick(
                    items,
                    callback=lambda _: None,
                    placeholder="Available prefixes...",
                )

        self._command_source.register(
            CommandEntry(
                name="help",
                title="Help",
                subtitle="Show available prefixes",
                action=_help_action,
                promoted=True,
            )
        )

    def _register_quit_all_command(self) -> None:
        """Register the built-in quit-all command."""

        def _quit_all_action(args: str) -> None:
            from AppKit import NSApplicationActivationPolicyRegular, NSWorkspace

            workspace = NSWorkspace.sharedWorkspace()
            own_pid = __import__("os").getpid()
            for app in workspace.runningApplications():
                if app.activationPolicy() != NSApplicationActivationPolicyRegular:
                    continue
                if app.processIdentifier() == own_pid:
                    continue
                bundle = app.bundleIdentifier() or ""
                if bundle == "com.apple.finder":
                    continue
                app.terminate()

        self._command_source.register(
            CommandEntry(
                name="quit-all",
                title="Quit All Applications",
                subtitle="Quit all running applications",
                action=_quit_all_action,
                promoted=True,
            )
        )

    def _register_reload_command(self) -> None:
        """Register the built-in reload command."""

        def _reload_action(args: str) -> None:
            import wenzi.scripting.api as _api

            _wz = _api.wz
            if _wz is not None:
                _wz.reload()

        self._command_source.register(
            CommandEntry(
                name="reload",
                title="Reload Scripts",
                subtitle="Reload all scripts and plugins",
                action=_reload_action,
                promoted=True,
            )
        )

    def _register_settings_command(self) -> None:
        """Register the built-in WenZi Settings command."""

        def _settings_action(args: str) -> None:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self._fire_event, "openSettings")

        self._command_source.register(
            CommandEntry(
                name="settings",
                title="WenZi Settings",
                subtitle="Open WenZi preferences",
                icon="⚙️",
                action=_settings_action,
                promoted=True,
            )
        )

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
                self._panel.show,
                initial_query=initial_query,
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

    # Reserved prefix for pick() mode.  Using ``?`` isolates pick items
    # from other sources.  ``>`` is reserved for the command source.
    # User-defined sources should avoid both prefixes.
    _PICK_PREFIX = "?"

    def pick(
        self,
        items: List[dict],
        callback: Callable,
        placeholder: str = "Choose...",
    ) -> None:
        """Show the chooser with a fixed list of items for the user to pick.

        When the user selects an item, *callback* is called with the original
        dict.  If the user dismisses the panel without selecting, *callback*
        is called with ``None``.

        The pick source is isolated via a reserved ``?`` prefix so that
        other registered sources do not contribute results.

        Args:
            items: List of item dicts (same format as source search results).
            callback: ``callback(item_dict | None)``.
            placeholder: Custom placeholder text for the search input.
        """
        callback = wrap_async(callback)
        source_name = f"__pick_{id(callback)}"
        chooser_items = _convert_items(items)

        # Assign stable IDs so the select event can identify pick items.
        pick_id_prefix = f"__pick_{id(callback)}_"
        id_to_orig: Dict[str, dict] = {}
        for i, (ci, orig) in enumerate(zip(chooser_items, items or [])):
            if not ci.item_id:
                ci.item_id = f"{pick_id_prefix}{i}"
            id_to_orig[ci.item_id] = orig

        # Track selection via the synchronous "select" event which fires
        # on the main thread BEFORE close() runs — avoiding the race
        # condition where the deferred action thread sets a flag too late.
        selected: List[Optional[dict]] = [None]

        def _on_select(info: dict) -> None:
            item_id = info.get("item_id", "")
            if item_id in id_to_orig:
                selected[0] = id_to_orig[item_id]

        self._event_handlers.setdefault("select", []).append(_on_select)

        def _search(query: str) -> List[ChooserItem]:
            if not query.strip():
                return chooser_items
            from wenzi.scripting.sources import fuzzy_match

            results = []
            for ci in chooser_items:
                matched, _ = fuzzy_match(query, ci.title)
                if not matched and ci.subtitle:
                    matched, _ = fuzzy_match(query, ci.subtitle)
                if matched:
                    results.append(ci)
            return results

        def _on_close() -> None:
            # Remove our temporary select handler
            handlers = self._event_handlers.get("select", [])
            if _on_select in handlers:
                handlers.remove(_on_select)
            self._panel.unregister_source(source_name)
            callback(selected[0])

        src = ChooserSource(
            name=source_name,
            prefix=self._PICK_PREFIX,
            search=_search,
            priority=999,
        )
        self._panel.register_source(src)

        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(
                self._panel.show,
                on_close=_on_close,
                initial_query=self._PICK_PREFIX + " ",
                placeholder=placeholder,
            )
        except Exception:
            logger.exception("Failed to show chooser for pick()")
            self._panel.unregister_source(source_name)
            handlers = self._event_handlers.get("select", [])
            if _on_select in handlers:
                handlers.remove(_on_select)

    # ------------------------------------------------------------------
    # Event hooks
    # ------------------------------------------------------------------

    def on(self, event: str) -> Callable:
        """Decorator to register an event handler.

        Supported events: ``open``, ``close``, ``select``, ``delete``.

        Usage::

            @wz.chooser.on("select")
            def on_select(item):
                print(f"Selected: {item['title']}")
        """

        def decorator(func: Callable) -> Callable:
            self._event_handlers.setdefault(event, []).append(wrap_async(func))
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

    # ------------------------------------------------------------------
    # Command registration
    # ------------------------------------------------------------------

    def register_command(
        self,
        name: str,
        title: str,
        action: Callable[[str], None],
        subtitle: str = "",
        icon: str = "",
        modifiers: Optional[Dict] = None,
        promoted: bool = False,
    ) -> None:
        """Register a named command in the command palette (``>`` prefix).

        Args:
            name: Unique command name (single token, e.g. ``"reload-scripts"``).
            title: Human-readable title shown in the launcher.
            action: Callback receiving the args string: ``action(args)``.
            subtitle: Optional description shown below the title.
            icon: Optional icon (``file://`` URL or ``data:`` URI).
            modifiers: Optional modifier actions, e.g.
                ``{"alt": {"subtitle": "Force", "action": callable}}``.
            promoted: If ``True``, also appear in the unprefixed main search.
        """
        entry = CommandEntry(
            name=name,
            title=title,
            subtitle=subtitle,
            icon=icon,
            action=wrap_async(action),
            modifiers=_parse_modifiers(modifiers),
            promoted=promoted,
        )
        self._command_source.register(entry)

    def unregister_command(self, name: str) -> None:
        """Remove a registered command by name."""
        self._command_source.unregister(name)

    def command(
        self,
        name: str,
        title: str,
        subtitle: str = "",
        icon: str = "",
        modifiers: Optional[Dict] = None,
        promoted: bool = False,
    ) -> Callable:
        """Decorator to register a function as a chooser command.

        The decorated function receives a single ``args`` string::

            @wz.chooser.command("greet", title="Greet")
            def greet(args):
                name = args.strip() or "World"
                wz.notify.show(f"Hello, {name}!")

        Set ``promoted=True`` to also show in the unprefixed main search::

            @wz.chooser.command("reload", title="Reload Scripts", promoted=True)
            def reload(args):
                wz.reload()
        """

        def decorator(func: Callable[[str], None]) -> Callable:
            self.register_command(
                name=name,
                title=title,
                action=func,
                subtitle=subtitle,
                icon=icon,
                modifiers=modifiers,
                promoted=promoted,
            )
            return func

        return decorator

    # ------------------------------------------------------------------
    # Source decorator
    # ------------------------------------------------------------------

    def source(
        self,
        name: str,
        prefix: Optional[str] = None,
        priority: int = 0,
        action_hints: Optional[dict] = None,
        description: str = "",
        show_preview: bool = False,
        search_timeout: Optional[float] = None,
        debounce_delay: Optional[float] = None,
    ) -> Callable:
        """Decorator to register a search function as a chooser source.

        The decorated function receives a query string and returns a list of
        item dicts.  Both sync and async functions are supported — async
        sources are dispatched to the shared event loop and results are
        merged incrementally.

        All :class:`ChooserItem` fields are supported::

            @wz.chooser.source("todos", prefix="td", priority=5,
                               description="Search TODOs")
            def search_todos(query):
                return [{"title": "Fix bug #123", ...}]

        Async sources::

            @wz.chooser.source("api", prefix="api", search_timeout=3.0)
            async def search_api(query):
                async with aiohttp.ClientSession() as s:
                    resp = await s.get(f"https://api.example.com?q={query}")
                    data = await resp.json()
                return [{"title": r["name"]} for r in data]

        Debounced async sources (wait for user to stop typing)::

            @wz.chooser.source("api", prefix="api", debounce_delay=0.3)
            async def search_api(query):
                ...

        Args:
            search_timeout: Per-source timeout in seconds for async sources.
                None uses the global default (5.0s).
            debounce_delay: Debounce delay in seconds for async sources.
                None uses the global default (0.15s), 0 disables debouncing.
        """

        def decorator(func: Callable[[str], List[dict]]) -> Callable:
            _is_async = asyncio.iscoroutinefunction(func)

            if _is_async:

                async def _async_search(query: str) -> List[ChooserItem]:
                    return _convert_items(await func(query))

                search_fn = _async_search
            else:

                def _search(query: str) -> List[ChooserItem]:
                    return _convert_items(func(query))

                search_fn = _search

            src = ChooserSource(
                name=name,
                prefix=prefix,
                search=search_fn,
                priority=priority,
                description=description,
                action_hints=action_hints,
                show_preview=show_preview,
                is_async=_is_async,
                search_timeout=search_timeout,
                debounce_delay=debounce_delay,
            )
            self._panel.register_source(src)
            logger.info(
                "User script registered chooser source: %s (async=%s)",
                name,
                _is_async,
            )
            return func

        return decorator
