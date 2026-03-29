"""wz.menu — read-only access to the app's statusbar menu items."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _ax_attr(element, attr):
    """Read an AXUIElement attribute, returning None on failure."""
    from ApplicationServices import AXUIElementCopyAttributeValue
    err, val = AXUIElementCopyAttributeValue(element, attr, None)
    if err != 0:
        return None
    return val


def _build_shortcut(cmd_char: str, modifiers: int) -> str:
    """Build a human-readable shortcut string from AX modifier flags.
    macOS AXMenuItemCmdModifiers bit flags:
      bit 0 (1) = Shift → ⇧
      bit 1 (2) = Control → ⌃
      bit 2 (4) = Option → ⌥
      Cmd (⌘) is always implied when cmd_char is non-empty.
    """
    if not cmd_char:
        return ""
    parts = []
    if modifiers & 4:
        parts.append("⌥")
    if modifiers & 2:
        parts.append("⌃")
    if modifiers & 1:
        parts.append("⇧")
    parts.append("⌘")
    parts.append(cmd_char)
    return "".join(parts)


class MenuAPI:
    """Enumerate and trigger the app's statusbar menu items — ``wz.menu``."""

    def __init__(self) -> None:
        self._root = None  # StatusMenuItem (app's root menu)
        self._wz_ns = None  # _WZNamespace for accessing current chooser

    def _set_root(self, root: Any) -> None:
        """Inject the app's root StatusMenuItem. Called by ScriptEngine."""
        self._root = root

    def list(self, flat: bool = False) -> List[Dict[str, Any]]:
        """Return the menu item tree as a list of dicts.

        Each dict contains: ``title``, ``key``, ``state``, ``has_action``,
        and optionally ``children`` (nested list).

        When *flat* is True, the tree is flattened and each item gets a
        ``path`` field (e.g. ``"Parent > Child"``).
        """
        if self._root is None:
            return []
        items = self._walk(self._root)
        if flat:
            return self._flatten(items)
        return items

    def trigger(self, title: str) -> bool:
        """Trigger a menu item by its title.

        Supports nested items using ``" > "`` as separator
        (e.g. ``"Parent > Child"``).  The callback is dispatched on the
        main thread.

        Returns True if the item was found and triggered.
        """
        if self._root is None:
            return False
        item = self._find(title)
        if item is None:
            return False

        from wenzi.statusbar import _ns_to_callback

        entry = _ns_to_callback.get(id(item._menuitem))
        if entry is None:
            return False

        smitem, callback = entry
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(callback, smitem)
        except Exception:
            logger.exception("Failed to trigger menu item: %s", title)
            return False
        return True

    def _find(self, title: str) -> Any:
        """Find a StatusMenuItem by title or path (``"A > B"``)."""
        from wenzi.statusbar import SeparatorMenuItem

        parts = [p.strip() for p in title.split(" > ")]
        node = self._root
        for part in parts:
            found = None
            for _key, child in node._items.items():
                if isinstance(child, SeparatorMenuItem):
                    continue
                if child.title == part:
                    found = child
                    break
            if found is None:
                return None
            node = found
        return node

    def _walk(self, parent: Any) -> List[Dict[str, Any]]:
        """Recursively walk the menu tree."""
        from wenzi.statusbar import SeparatorMenuItem, _ns_to_callback

        results: List[Dict[str, Any]] = []
        for key, item in parent._items.items():
            if isinstance(item, SeparatorMenuItem):
                continue
            entry: Dict[str, Any] = {
                "title": item.title,
                "key": key,
                "state": item.state,
                "has_action": id(item._menuitem) in _ns_to_callback,
            }
            if item._items:
                entry["children"] = self._walk(item)
            results.append(entry)
        return results

    def _flatten(
        self, items: List[Dict[str, Any]], prefix: str = "",
    ) -> List[Dict[str, Any]]:
        """Flatten a nested item list, adding ``path`` to each item."""
        flat: List[Dict[str, Any]] = []
        for item in items:
            path = f"{prefix} > {item['title']}" if prefix else item["title"]
            children = item.pop("children", None)
            item["path"] = path
            flat.append(item)
            if children:
                flat.extend(self._flatten(children, path))
        return flat

    # ------------------------------------------------------------------
    # AX-based app menu introspection
    # ------------------------------------------------------------------

    def _set_chooser_api(self, chooser_api):
        """Inject the ChooserAPI for accessing previous-app pid."""
        # Kept for tests that inject a mock directly
        self._chooser_api_direct = chooser_api

    def _set_wz_ns(self, wz_ns):
        """Inject the wz namespace for dynamic chooser access.

        This ensures we always use the *current* ChooserAPI instance,
        even after script reloads that recreate it.
        """
        self._wz_ns = wz_ns

    def app_menu(self, pid=None):
        """Return the menu items of an application as a flat list.

        Each dict has: title, path, enabled, shortcut, _ax_element.
        pid defaults to the app frontmost before chooser opened.
        The system Apple menu is excluded — only app-specific menus
        are returned.  Returns empty list on failure.
        """
        if pid is None:
            pid = self._get_previous_pid()
        if pid is None:
            return []
        try:
            from ApplicationServices import (
                AXUIElementCopyAttributeValue,
                AXUIElementCreateApplication,
            )

            ax_app = AXUIElementCreateApplication(pid)
            ax_menu_bar = _ax_attr(ax_app, "AXMenuBar")
            if ax_menu_bar is None:
                return []

            # Walk only app-specific menus, skipping the Apple menu
            err, top_items = AXUIElementCopyAttributeValue(
                ax_menu_bar, "AXChildren", None,
            )
            if err != 0 or not top_items:
                return []

            results = []
            for item in top_items:
                err, title = AXUIElementCopyAttributeValue(
                    item, "AXTitle", None,
                )
                if err != 0 or not title:
                    continue
                title_str = str(title)
                # Skip the system Apple menu
                if title_str == "Apple":
                    continue
                err, subs = AXUIElementCopyAttributeValue(
                    item, "AXChildren", None,
                )
                if err == 0 and subs:
                    for sub in subs:
                        results.extend(
                            self._walk_ax_menu(sub, _prefix=f"{title_str} > ")
                        )
            return results
        except Exception:
            logger.debug("Failed to read app menu for pid=%s", pid, exc_info=True)
            return []

    def app_menu_trigger(self, item, pid=None):
        """Trigger an app menu item obtained from ``app_menu()``.

        Uses AppleScript via System Events to click the menu item
        directly.  This does not require the target app to be
        frontmost and avoids stale AXUIElement references.

        Args:
            item: A dict from ``app_menu()`` (must contain ``path``).
            pid: Target process ID.  Must be provided — the chooser's
                 ``_previous_app`` is cleared before the action runs.

        Returns True if the action was dispatched.
        """
        path = item.get("path")
        if not path:
            return False

        if pid is None:
            pid = self._get_previous_pid()
        if pid is None:
            return False

        # Build AppleScript using pid for precise process targeting
        # (avoids ambiguity when multiple processes share a name)
        parts = [p.strip() for p in path.split(" > ")]
        if len(parts) < 2:
            return False

        menu_name = parts[0]  # e.g. "Tab"
        # Build nested menu item reference from innermost to outermost
        # "File > Share > AirDrop" → click menu item "AirDrop" of menu "Share"
        #                              of menu item "Share" of menu "File"
        ref = f'menu item "{parts[-1]}"'
        for p in reversed(parts[1:-1]):
            ref = f'{ref} of menu "{p}" of menu item "{p}"'
        ref = f'{ref} of menu "{menu_name}" of menu bar item "{menu_name}" of menu bar 1'

        script = (
            f"tell application \"System Events\" to tell "
            f"(first process whose unix id is {pid}) to click {ref}"
        )

        import subprocess

        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                logger.debug(
                    "AppleScript menu trigger failed: %s", result.stderr.strip(),
                )
                return False
            return True
        except Exception:
            logger.debug("Failed to trigger app menu item: %s", path, exc_info=True)
            return False

    def _get_previous_pid(self):
        """Get pid of app that was frontmost before chooser opened."""
        chooser = None
        if self._wz_ns is not None:
            try:
                chooser = self._wz_ns.chooser
            except Exception:
                pass
        if chooser is None:
            chooser = getattr(self, "_chooser_api_direct", None)
        if chooser is None:
            return None
        try:
            prev_app = chooser.panel._previous_app
            if prev_app is not None:
                return prev_app.processIdentifier()
        except Exception:
            logger.debug("Failed to get previous app pid", exc_info=True)
        return None

    def _walk_ax_menu(self, ax_menu_bar, _prefix=""):
        """Recursively walk an AX menu bar, returning a flat list."""
        from ApplicationServices import AXUIElementCopyAttributeValue

        results = []
        err, children = AXUIElementCopyAttributeValue(ax_menu_bar, "AXChildren", None)
        if err != 0 or not children:
            return results

        for child in children:
            err, title = AXUIElementCopyAttributeValue(child, "AXTitle", None)
            if err != 0 or not title:
                continue
            title = str(title)

            err, enabled = AXUIElementCopyAttributeValue(child, "AXEnabled", None)
            enabled = bool(enabled) if err == 0 else True

            err, cmd_char = AXUIElementCopyAttributeValue(child, "AXMenuItemCmdChar", None)
            cmd_char = str(cmd_char) if err == 0 and cmd_char else ""

            err, cmd_mods = AXUIElementCopyAttributeValue(child, "AXMenuItemCmdModifiers", None)
            cmd_mods = int(cmd_mods) if err == 0 and cmd_mods is not None else 0

            err, sub_children = AXUIElementCopyAttributeValue(child, "AXChildren", None)
            if err == 0 and sub_children:
                for sub in sub_children:
                    results.extend(self._walk_ax_menu(sub, _prefix=f"{_prefix}{title} > "))
            else:
                shortcut = _build_shortcut(cmd_char, cmd_mods)
                path = f"{_prefix}{title}"
                results.append({
                    "title": title,
                    "path": path,
                    "enabled": enabled,
                    "shortcut": shortcut,
                    "_ax_element": child,
                })

        return results
