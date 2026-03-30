"""Window Switcher — switch between open windows via the launcher.

Lists all visible windows across applications and lets the user
focus any window by pressing Enter.  Prefix: ``w``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading

logger = logging.getLogger(__name__)

_ICON_SIZE = 32


def _get_app_icon_png(app_path: str) -> bytes | None:
    """Return raw PNG bytes for the app icon at *app_path*, or None."""
    try:
        from AppKit import (
            NSBitmapImageRep,
            NSCompositingOperationCopy,
            NSImage,
            NSPNGFileType,
            NSWorkspace,
        )
        from Foundation import NSMakeRect, NSSize

        icon = NSWorkspace.sharedWorkspace().iconForFile_(app_path)
        if icon is None:
            return None

        size = NSSize(_ICON_SIZE, _ICON_SIZE)
        target = NSImage.alloc().initWithSize_(size)
        target.lockFocus()
        icon.drawInRect_fromRect_operation_fraction_(
            NSMakeRect(0, 0, _ICON_SIZE, _ICON_SIZE),
            NSMakeRect(0, 0, icon.size().width, icon.size().height),
            NSCompositingOperationCopy,
            1.0,
        )
        target.unlockFocus()

        rep = NSBitmapImageRep.imageRepWithData_(target.TIFFRepresentation())
        png_data = rep.representationUsingType_properties_(NSPNGFileType, None)
        return bytes(png_data) if png_data else None
    except Exception:
        logger.debug("Failed to get icon for %s", app_path, exc_info=True)
        return None


class _IconCache:
    """Thread-safe icon cache backed by disk."""

    def __init__(self) -> None:
        from wenzi.config import DEFAULT_ICON_CACHE_DIR

        self._cache_dir = os.path.expanduser(DEFAULT_ICON_CACHE_DIR)
        self._mem: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, app_path: str) -> str:
        """Return a ``file://`` URL for the app icon, or empty string."""
        if not app_path:
            return ""
        with self._lock:
            if app_path in self._mem:
                return self._mem[app_path]

        key = hashlib.md5(app_path.encode()).hexdigest()
        png_path = os.path.join(self._cache_dir, f"{key}.png")

        # Check disk cache
        if os.path.isfile(png_path):
            url = "file://" + png_path
            with self._lock:
                self._mem[app_path] = url
            return url

        # Extract and cache
        png = _get_app_icon_png(app_path)
        if png is None:
            with self._lock:
                self._mem[app_path] = ""
            return ""

        os.makedirs(self._cache_dir, exist_ok=True)
        try:
            with open(png_path, "wb") as f:
                f.write(png)
        except OSError:
            logger.debug("Failed to cache icon for %s", app_path, exc_info=True)

        url = "file://" + png_path
        with self._lock:
            self._mem[app_path] = url
        return url


def setup(wz) -> None:
    """Entry point called by the ScriptEngine plugin loader."""
    icons = _IconCache()

    @wz.chooser.source(
        "window-switcher",
        prefix="w",
        priority=5,
        description="Switch windows",
        action_hints={"enter": "Focus"},
    )
    def search(query: str) -> list:
        windows = wz.window.list()

        items = []
        for w in windows:
            items.append({
                "title": w["title"],
                "subtitle": w["app_name"],
                "icon": icons.get(w["app_path"]),
                "item_id": f"win:{w['pid']}:{w['window_index']}",
                "action": (
                    lambda pid=w["pid"], idx=w["window_index"]:
                        wz.window.focus(pid, idx)
                ),
                "modifiers": {
                    "cmd": {
                        "subtitle": "Close window",
                        "action": (
                            lambda pid=w["pid"], idx=w["window_index"]:
                                wz.window.close(pid, idx)
                        ),
                    },
                },
            })

        if not query.strip():
            return items

        from wenzi.scripting.sources import fuzzy_match

        filtered = []
        for item in items:
            m1, _ = fuzzy_match(query, item["title"])
            m2, _ = fuzzy_match(query, item["subtitle"])
            if m1 or m2:
                filtered.append(item)
        return filtered
