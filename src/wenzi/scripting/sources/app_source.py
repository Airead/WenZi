"""App search data source for the Chooser.

Scans /Applications, /System/Applications, ~/Applications for .app
bundles, plus an allowlist of user-facing CoreServices apps.  Checks
running status via NSWorkspace and provides fuzzy matching with running
apps ranked first.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import List, Optional

import objc

from wenzi.config import DEFAULT_ICON_CACHE_DIR as _CFG_ICON_CACHE_DIR
from wenzi.scripting.sources import (
    ChooserItem,
    ChooserSource,
    ModifierAction,
    fuzzy_match_fields,
)

logger = logging.getLogger(__name__)

_ICON_SIZE = 32
_DEFAULT_ICON_CACHE_DIR = os.path.expanduser(_CFG_ICON_CACHE_DIR)

# Directories to scan for applications
_APP_DIRS = [
    "/Applications",
    "/System/Applications",
    "/System/Applications/Utilities",
    os.path.expanduser("~/Applications"),
]

# User-facing apps from CoreServices added directly (allowlist)
_CORE_SERVICES_APPS = [
    "/System/Library/CoreServices/Captive Network Assistant.app",
    "/System/Library/CoreServices/Finder.app",
    "/System/Library/CoreServices/Installer.app",
    "/System/Library/CoreServices/Siri.app",
    "/System/Library/CoreServices/Software Update.app",
    "/System/Library/CoreServices/Spotlight.app",
    "/System/Library/CoreServices/VoiceOver.app",
    "/System/Library/CoreServices/Applications/About This Mac.app",
    "/System/Library/CoreServices/Applications/Archive Utility.app",
    "/System/Library/CoreServices/Applications/Directory Utility.app",
    "/System/Library/CoreServices/Applications/Feedback Assistant.app",
    "/System/Library/CoreServices/Applications/Keychain Access.app",
    "/System/Library/CoreServices/Applications/Ticket Viewer.app",
    "/System/Library/CoreServices/Applications/Wireless Diagnostics.app",
]


def _get_display_name(path: str, fallback: str) -> str:
    """Return the localized display name for an app bundle path."""
    with objc.autorelease_pool():
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
    with objc.autorelease_pool():
        try:
            from AppKit import (
                NSBitmapImageRep,
                NSCompositingOperationCopy,
                NSDeviceRGBColorSpace,
                NSGraphicsContext,
                NSPNGFileType,
                NSWorkspace,
            )
            from Foundation import NSMakeRect, NSZeroRect

            ws = NSWorkspace.sharedWorkspace()
            icon = ws.iconForFile_(path)
            if icon is None:
                return None

            # Render into a bitmap rep (thread-safe, no deprecated lockFocus)
            sz = _ICON_SIZE
            rep = NSBitmapImageRep.alloc() \
                .initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
                    None, sz, sz, 8, 4, True, False,
                    NSDeviceRGBColorSpace, 0, 0,
                )
            if rep is None:
                return None
            ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
            if ctx is None:
                return None
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.setCurrentContext_(ctx)
            icon.drawInRect_fromRect_operation_fraction_(
                NSMakeRect(0, 0, sz, sz), NSZeroRect,
                NSCompositingOperationCopy, 1.0,
            )
            NSGraphicsContext.restoreGraphicsState()

            png_data = rep.representationUsingType_properties_(NSPNGFileType, None)
            return bytes(png_data) if png_data else None
        except Exception:
            logger.debug("Failed to get icon for %s", path, exc_info=True)
            return None


def _cache_key(path: str) -> str:
    """Return a stable cache filename for an app path."""
    return hashlib.md5(path.encode()).hexdigest()


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

        for entry in entries:
            if not entry.endswith(".app"):
                continue
            full_path = os.path.join(app_dir, entry)
            name = entry[:-4]  # Strip ".app"
            if name in seen:
                continue
            seen.add(name)
            display_name = _get_display_name(full_path, name)
            apps.append({
                "name": name,
                "display_name": display_name,
                "path": full_path,
            })

    # Add allowlisted CoreServices apps directly
    for full_path in _CORE_SERVICES_APPS:
        if not os.path.isdir(full_path):
            continue
        name = os.path.basename(full_path)[:-4]
        if name in seen:
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


_running_apps_cache: set[str] = set()
_running_apps_time: float = 0.0
_RUNNING_APPS_TTL: float = 2.0  # seconds


def _get_running_app_names() -> set[str]:
    """Return a set of currently running application names.

    Results are cached for ``_RUNNING_APPS_TTL`` seconds so that rapid
    keystroke-driven searches don't re-query NSWorkspace every time.
    """
    global _running_apps_cache, _running_apps_time

    now = time.monotonic()
    if now - _running_apps_time < _RUNNING_APPS_TTL:
        return _running_apps_cache

    try:
        from AppKit import NSWorkspace

        workspace = NSWorkspace.sharedWorkspace()
        running = workspace.runningApplications()
        _running_apps_cache = {
            str(app.localizedName())
            for app in running
            if app.localizedName()
        }
    except Exception:
        logger.debug("Failed to get running apps", exc_info=True)
        _running_apps_cache = set()

    _running_apps_time = now
    return _running_apps_cache


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

    _SCAN_TTL = 30  # seconds before app list is rescanned

    def __init__(self, icon_cache_dir: Optional[str] = None) -> None:
        self._apps: list[dict] = []
        self._scanned = False
        self._last_scan_time: float = 0
        self._icon_cache: dict[str, str] = {}  # path → file:// URL (memory)
        self._icon_lock = threading.Lock()
        self._icon_cache_dir = icon_cache_dir or _DEFAULT_ICON_CACHE_DIR

    def _get_icon(self, path: str) -> str:
        """Return cached icon file:// URL, checking disk then extracting.

        Thread-safe: protected by ``_icon_lock`` so the preload thread
        and main-thread search cannot race on the cache dict.
        """
        with self._icon_lock:
            if path in self._icon_cache:
                return self._icon_cache[path]

        file_url = self._load_icon_from_disk(path)
        if file_url:
            with self._icon_lock:
                self._icon_cache[path] = file_url
            return file_url

        png = _get_app_icon_png(path)
        if png is None:
            with self._icon_lock:
                self._icon_cache[path] = ""
            return ""

        self._save_icon_to_disk(path, png)
        key = _cache_key(path)
        png_path = os.path.join(self._icon_cache_dir, f"{key}.png")
        file_url = "file://" + png_path
        with self._icon_lock:
            self._icon_cache[path] = file_url
        return file_url

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

            return "file://" + png_path
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
        now = time.monotonic()
        if not self._scanned or (now - self._last_scan_time > self._SCAN_TTL):
            if self._scanned:
                logger.debug("App list TTL expired, rescanning")
            self._icon_cache.clear()
            self._apps = _scan_apps()
            self._scanned = True
            self._last_scan_time = now
            self._preload_icons_async()

    def rescan(self) -> None:
        """Force a rescan of application directories."""
        self._icon_cache.clear()
        self._apps = _scan_apps()
        self._scanned = True
        self._last_scan_time = time.monotonic()
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
            matched, score = fuzzy_match_fields(query, (name, display_name))
            if not matched:
                continue
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
        from wenzi.i18n import t

        return ChooserSource(
            name="apps",
            display_name=t("chooser.source.apps"),
            prefix=None,
            search=self.search,
            priority=10,
            description="Search applications",
            action_hints={
                "enter": t("chooser.action.launch"),
                "cmd_enter": t("chooser.action.reveal"),
            },
        )
