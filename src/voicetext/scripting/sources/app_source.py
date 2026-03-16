"""App search data source for the Chooser.

Scans /Applications, /System/Applications, and ~/Applications for .app
bundles, checks running status via NSWorkspace, and provides substring
matching with running apps ranked first.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
from typing import List, Optional

from voicetext.scripting.sources import (
    ChooserItem,
    ChooserSource,
    ModifierAction,
    fuzzy_match,
)

logger = logging.getLogger(__name__)

_ICON_SIZE = 32
_DEFAULT_ICON_CACHE_DIR = os.path.expanduser("~/.config/VoiceText/icon_cache")

# Directories to scan for applications
_APP_DIRS = [
    "/Applications",
    "/System/Applications",
    "/System/Applications/Utilities",
    "/System/Library/CoreServices",
    "/System/Library/CoreServices/Applications",
    os.path.expanduser("~/Applications"),
]

# Internal system app filters (applied only to /System/Library/CoreServices)
_CORE_SERVICES_DIR = "/System/Library/CoreServices"
_CORE_SERVICES_SKIP_SUFFIXES = (
    "Agent", "Server", "Service", "Helper", "Handler",
    "Dispatcher", "Forwarder", "Stub", "Trampoline",
    "Launcher", "Host", "Monitor", "Replayer",
)
_CORE_SERVICES_SKIP_NAMES = {
    "loginwindow", "rcd", "liquiddetectiond",
    "screencaptureui", "UIKitSystem",
}


def _get_display_name(path: str, fallback: str) -> str:
    """Return the localized display name for an app bundle path."""
    try:
        from Foundation import NSFileManager

        fm = NSFileManager.defaultManager()
        display = fm.displayNameAtPath_(path)
        if display:
            name = str(display)
            # displayNameAtPath_ may include ".app" for non-localized names
            if name.endswith(".app"):
                name = name[:-4]
            return name
    except Exception:
        pass
    return fallback


def _get_app_icon_png(path: str) -> Optional[bytes]:
    """Return raw PNG bytes for the app icon, or None on failure."""
    try:
        from AppKit import (
            NSBitmapImageRep,
            NSCompositingOperationCopy,
            NSImage,
            NSPNGFileType,
            NSWorkspace,
        )
        from Foundation import NSMakeRect, NSSize

        ws = NSWorkspace.sharedWorkspace()
        icon = ws.iconForFile_(path)
        if icon is None:
            return None

        # Render into a fixed-size image to control pixel output
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
        logger.debug("Failed to get icon for %s", path, exc_info=True)
        return None


def _png_to_data_uri(png_bytes: bytes) -> str:
    """Convert raw PNG bytes to a data:image/png;base64 URI."""
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _cache_key(path: str) -> str:
    """Return a stable cache filename for an app path."""
    return hashlib.md5(path.encode()).hexdigest()


def _is_internal_app(name: str) -> bool:
    """Return True if *name* looks like an internal system component."""
    if name in _CORE_SERVICES_SKIP_NAMES:
        return True
    return any(name.endswith(suffix) for suffix in _CORE_SERVICES_SKIP_SUFFIXES)


def _scan_apps() -> list[dict]:
    """Scan application directories and return a list of app info dicts.

    Each dict has keys: name (str), display_name (str), path (str).
    ``name`` is the English bundle name (for dedup and running-status matching),
    ``display_name`` is the localized name shown to the user.
    Icons are extracted lazily on first access to avoid slow startup.
    """
    apps = []
    seen = set()

    for app_dir in _APP_DIRS:
        if not os.path.isdir(app_dir):
            continue
        try:
            entries = os.listdir(app_dir)
        except OSError:
            continue

        is_core_services = app_dir == _CORE_SERVICES_DIR
        for entry in entries:
            if not entry.endswith(".app"):
                continue
            full_path = os.path.join(app_dir, entry)
            name = entry[:-4]  # Strip ".app"
            if name in seen:
                continue
            if is_core_services and _is_internal_app(name):
                continue
            seen.add(name)
            display_name = _get_display_name(full_path, name)
            apps.append({
                "name": name,
                "display_name": display_name,
                "path": full_path,
            })

    logger.info("Scanned %d apps from %s", len(apps), _APP_DIRS)
    return apps


def _get_running_app_names() -> set[str]:
    """Return a set of currently running application names."""
    try:
        from AppKit import NSWorkspace

        workspace = NSWorkspace.sharedWorkspace()
        running = workspace.runningApplications()
        return {
            str(app.localizedName())
            for app in running
            if app.localizedName()
        }
    except Exception:
        logger.debug("Failed to get running apps", exc_info=True)
        return set()


def _launch_app(path: str) -> None:
    """Launch or activate an application by path."""
    try:
        from AppKit import NSWorkspace

        workspace = NSWorkspace.sharedWorkspace()
        workspace.launchApplication_(path)
    except Exception:
        logger.exception("Failed to launch app: %s", path)


class AppSource:
    """Application search data source.

    Scans app directories once on init and caches the list.
    Running status is checked on every search for fresh results.
    Icons are extracted lazily and cached to disk for fast subsequent loads.
    """

    def __init__(self, icon_cache_dir: Optional[str] = None) -> None:
        self._apps: list[dict] = []
        self._scanned = False
        self._icon_cache: dict[str, str] = {}  # path → data URI (memory)
        self._icon_lock = threading.Lock()
        self._icon_cache_dir = icon_cache_dir or _DEFAULT_ICON_CACHE_DIR

    def _get_icon(self, path: str) -> str:
        """Return cached icon data URI, checking disk then extracting.

        Thread-safe: protected by ``_icon_lock`` so the preload thread
        and main-thread search cannot race on the cache dict.
        """
        with self._icon_lock:
            if path in self._icon_cache:
                return self._icon_cache[path]

        data_uri = self._load_icon_from_disk(path)
        if data_uri:
            with self._icon_lock:
                self._icon_cache[path] = data_uri
            return data_uri

        png = _get_app_icon_png(path)
        if png is None:
            with self._icon_lock:
                self._icon_cache[path] = ""
            return ""

        data_uri = _png_to_data_uri(png)
        with self._icon_lock:
            self._icon_cache[path] = data_uri
        self._save_icon_to_disk(path, png)
        return data_uri

    def _load_icon_from_disk(self, app_path: str) -> str:
        """Load a cached icon from disk if it exists and is still fresh."""
        key = _cache_key(app_path)
        png_path = os.path.join(self._icon_cache_dir, f"{key}.png")
        meta_path = os.path.join(self._icon_cache_dir, f"{key}.meta")

        if not os.path.isfile(png_path) or not os.path.isfile(meta_path):
            return ""

        try:
            with open(meta_path, "r") as f:
                cached_mtime = float(f.read().strip())

            current_mtime = os.path.getmtime(app_path)
            if cached_mtime != current_mtime:
                return ""  # app updated, cache stale

            with open(png_path, "rb") as f:
                png = f.read()
            return _png_to_data_uri(png) if png else ""
        except Exception:
            logger.debug("Failed to load cached icon for %s", app_path, exc_info=True)
            return ""

    def _save_icon_to_disk(self, app_path: str, png: bytes) -> None:
        """Save icon PNG and metadata to disk cache."""
        try:
            os.makedirs(self._icon_cache_dir, exist_ok=True)
            key = _cache_key(app_path)
            png_path = os.path.join(self._icon_cache_dir, f"{key}.png")
            meta_path = os.path.join(self._icon_cache_dir, f"{key}.meta")

            with open(png_path, "wb") as f:
                f.write(png)

            mtime = os.path.getmtime(app_path)
            with open(meta_path, "w") as f:
                f.write(str(mtime))
        except Exception:
            logger.debug("Failed to cache icon for %s", app_path, exc_info=True)

    def _ensure_scanned(self) -> None:
        if not self._scanned:
            self._apps = _scan_apps()
            self._scanned = True
            self._preload_icons_async()

    def rescan(self) -> None:
        """Force a rescan of application directories."""
        self._apps = _scan_apps()
        self._scanned = True
        self._preload_icons_async()

    def _preload_icons_async(self) -> None:
        """Preload all app icons in a background thread."""
        apps = list(self._apps)

        def _load():
            for app in apps:
                path = app["path"]
                with self._icon_lock:
                    already_cached = path in self._icon_cache
                if not already_cached:
                    self._get_icon(path)

        threading.Thread(target=_load, daemon=True).start()

    def search(self, query: str) -> List[ChooserItem]:
        """Search apps by fuzzy matching, running apps first.

        Matches against both the English bundle name and the localized
        display name so users can search in any language.  Uses fuzzy
        matching with CamelCase, initials, substring, and scattered
        character strategies.
        """
        self._ensure_scanned()

        if not query.strip():
            return []

        running = _get_running_app_names()

        matches = []
        for app in self._apps:
            name = app["name"]
            display_name = app["display_name"]
            matched_name, score_name = fuzzy_match(query, name)
            matched_display, score_display = fuzzy_match(query, display_name)
            if not matched_name and not matched_display:
                continue
            score = max(score_name, score_display)
            # Match running status against both English and localized names
            is_running = name in running or display_name in running
            path = app["path"]
            matches.append((is_running, score, display_name, path))

        # Sort: running apps first, then by score descending, then alphabetical
        matches.sort(key=lambda x: (not x[0], -x[1], x[2].lower()))

        return [
            ChooserItem(
                title=display_name,
                subtitle="Running" if is_running else "Application",
                icon=self._get_icon(path),
                item_id=f"app:{path}",
                action=lambda p=path: _launch_app(p),
                reveal_path=path,
                modifiers={
                    "alt": ModifierAction(
                        subtitle=path,
                        action=lambda p=path: _launch_app(p),
                    ),
                },
            )
            for is_running, _score, display_name, path in matches
        ]

    def as_chooser_source(self) -> ChooserSource:
        """Return a ChooserSource wrapping this AppSource."""
        return ChooserSource(
            name="apps",
            prefix=None,
            search=self.search,
            priority=10,
        )
