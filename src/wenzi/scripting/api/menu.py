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


# macOS virtual keycodes for common characters (US keyboard layout)
_KEYCODE_MAP = {
    "A": 0, "B": 11, "C": 8, "D": 2, "E": 14, "F": 3, "G": 5,
    "H": 4, "I": 34, "J": 38, "K": 40, "L": 37, "M": 46, "N": 45,
    "O": 31, "P": 35, "Q": 12, "R": 15, "S": 1, "T": 17, "U": 32,
    "V": 9, "W": 13, "X": 7, "Y": 16, "Z": 6,
    "0": 29, "1": 18, "2": 19, "3": 20, "4": 21, "5": 23,
    "6": 22, "7": 26, "8": 28, "9": 25,
    ",": 43, ".": 47, "/": 44, ";": 41, "'": 39,
    "[": 33, "]": 30, "\\": 42, "-": 27, "=": 24, "`": 50,
}


def _char_to_keycode(char: str):
    """Map a character to its macOS virtual keycode, or None."""
    return _KEYCODE_MAP.get(char.upper())


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

        Strategy:
          1. If the item has a keyboard shortcut, send it directly to the
             target process via ``CGEventPostToPid`` — instant, no
             activation needed.
          2. Otherwise, activate the app, wait for it to become frontmost,
             re-find the menu item via AX, and perform AXPress.

        Args:
            item: A dict from ``app_menu()``.
            pid: Target process ID.  Must be provided — the chooser's
                 ``_previous_app`` is cleared before the action runs.

        Returns True if the action was dispatched.
        """
        if pid is None:
            pid = self._get_previous_pid()
        if pid is None:
            return False

        cmd_char = item.get("_cmd_char", "")
        cmd_mods = item.get("_cmd_mods", 0)

        if cmd_char:
            return self._trigger_via_keystroke(pid, cmd_char, cmd_mods)
        return self._trigger_via_axpress(pid, item.get("path", ""))

    def _trigger_via_keystroke(self, pid: int, cmd_char: str, cmd_mods: int) -> bool:
        """Send a keyboard shortcut directly to a process via CGEventPostToPid."""
        try:
            import Quartz

            keycode = _char_to_keycode(cmd_char)
            if keycode is None:
                logger.debug("No keycode mapping for %r", cmd_char)
                return False

            # Build CGEvent modifier flags
            # AX cmd_mods: bit0=Shift, bit1=Ctrl, bit2=Option
            # Cmd is always implied
            flags = Quartz.kCGEventFlagMaskCommand
            if cmd_mods & 1:
                flags |= Quartz.kCGEventFlagMaskShift
            if cmd_mods & 2:
                flags |= Quartz.kCGEventFlagMaskControl
            if cmd_mods & 4:
                flags |= Quartz.kCGEventFlagMaskAlternate

            event_down = Quartz.CGEventCreateKeyboardEvent(None, keycode, True)
            Quartz.CGEventSetFlags(event_down, flags)
            Quartz.CGEventPostToPid(pid, event_down)
            Quartz.CFRelease(event_down)

            event_up = Quartz.CGEventCreateKeyboardEvent(None, keycode, False)
            Quartz.CGEventSetFlags(event_up, flags)
            Quartz.CGEventPostToPid(pid, event_up)
            Quartz.CFRelease(event_up)

            logger.info("app_menu_trigger keystroke: pid=%s char=%s mods=%s", pid, cmd_char, cmd_mods)
            return True
        except Exception:
            logger.info("app_menu_trigger keystroke failed", exc_info=True)
            return False

    def _trigger_via_axpress(self, pid: int, path: str) -> bool:
        """Activate the app, re-find the menu item by path, and AXPress."""
        if not path:
            return False

        # Activate the target app
        try:
            from AppKit import NSRunningApplication

            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app:
                app.activateWithOptions_(2)  # IgnoringOtherApps
        except Exception:
            logger.debug("Failed to activate app", exc_info=True)

        import time
        time.sleep(0.15)

        # Re-find and press
        try:
            from ApplicationServices import (
                AXUIElementCopyAttributeValue,
                AXUIElementCreateApplication,
                AXUIElementPerformAction,
            )

            ax_app = AXUIElementCreateApplication(pid)
            ax_menu_bar = _ax_attr(ax_app, "AXMenuBar")
            if ax_menu_bar is None:
                return False

            # Navigate the AX tree by path segments
            parts = [p.strip() for p in path.split(" > ")]
            current = ax_menu_bar
            target = None

            for i, part in enumerate(parts):
                err, children = AXUIElementCopyAttributeValue(
                    current, "AXChildren", None,
                )
                if err != 0 or not children:
                    return False
                found = None
                for child in children:
                    err, title = AXUIElementCopyAttributeValue(
                        child, "AXTitle", None,
                    )
                    if err == 0 and str(title) == part:
                        found = child
                        break
                if found is None:
                    return False
                if i < len(parts) - 1:
                    err, subs = AXUIElementCopyAttributeValue(
                        found, "AXChildren", None,
                    )
                    if err != 0 or not subs:
                        return False
                    current = subs[0]
                else:
                    target = found

            if target is None:
                return False

            AXUIElementPerformAction(target, "AXPress")
            logger.info("app_menu_trigger AXPress: pid=%s path=%r", pid, path)
            return True
        except Exception:
            logger.info("app_menu_trigger AXPress failed: %s", path, exc_info=True)
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
                    "_cmd_char": cmd_char,
                    "_cmd_mods": cmd_mods,
                    "_ax_element": child,
                })

        return results
