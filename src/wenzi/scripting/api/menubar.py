"""Menubar API — create and manage extra status-bar items from scripts."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class MenuBarItem:
    """A single status-bar item created by a script."""

    def __init__(self, name: str, title: str = ""):
        from AppKit import NSStatusBar

        self._name = name
        self._ns_item = NSStatusBar.systemStatusBar().statusItemWithLength_(-1)
        self._ns_item.setTitle_(title)
        self._menu_item_refs: list = []  # prevent GC of NSMenuItems

    @property
    def name(self) -> str:
        return self._name

    def set_title(self, title: str) -> None:
        """Update the status-bar title (main-thread safe)."""
        from PyObjCTools import AppHelper

        AppHelper.callAfter(lambda: self._ns_item.setTitle_(title))

    def set_menu(self, items: list[dict]) -> None:
        """Set the dropdown menu.

        Each dict: ``{"title": str, "action": callable}``
        or ``{"separator": True}``.
        """
        from PyObjCTools import AppHelper

        AppHelper.callAfter(lambda: self._set_menu_on_main(items))

    def _set_menu_on_main(self, items: list[dict]) -> None:
        from AppKit import NSMenu, NSMenuItem
        from wenzi.statusbar import _get_callback_handler, _ns_to_callback

        # Clean up old callbacks
        for mi in self._menu_item_refs:
            _ns_to_callback.pop(id(mi), None)
        self._menu_item_refs.clear()

        menu = NSMenu.alloc().init()
        handler = _get_callback_handler()

        for entry in items:
            if entry.get("separator"):
                menu.addItem_(NSMenuItem.separatorItem())
                continue

            title = entry.get("title", "")
            action = entry.get("action")

            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, "menuItemClicked:" if action else None, ""
            )
            if action:
                mi.setTarget_(handler)
                _ns_to_callback[id(mi)] = (
                    None,
                    lambda _, cb=action: cb(),
                )
            self._menu_item_refs.append(mi)
            menu.addItem_(mi)

        self._ns_item.setMenu_(menu)

    def _destroy(self) -> None:
        """Remove this item from the status bar and clean up callbacks."""
        from AppKit import NSStatusBar
        from wenzi.statusbar import _ns_to_callback

        for mi in self._menu_item_refs:
            _ns_to_callback.pop(id(mi), None)
        self._menu_item_refs.clear()
        NSStatusBar.systemStatusBar().removeStatusItem_(self._ns_item)


class MenuBarAPI:
    """Manage extra status-bar items — ``wz.menubar``."""

    def __init__(self) -> None:
        self._items: dict[str, MenuBarItem] = {}

    def create(self, name: str, title: str = "") -> MenuBarItem:
        """Create (or recreate) a named status-bar item."""
        if name in self._items:
            self._items[name]._destroy()
        item = MenuBarItem(name, title)
        self._items[name] = item
        return item

    def remove(self, name: str) -> None:
        """Remove a named status-bar item."""
        item = self._items.pop(name, None)
        if item is not None:
            item._destroy()

    def get(self, name: str) -> Optional[MenuBarItem]:
        """Return an existing item by name, or None."""
        return self._items.get(name)

    def cleanup(self) -> None:
        """Remove all script-created status-bar items.

        Called automatically on script reload.
        """
        for item in self._items.values():
            item._destroy()
        self._items.clear()
