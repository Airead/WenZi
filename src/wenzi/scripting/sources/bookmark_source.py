"""Browser bookmark data source for the Chooser.

Reads bookmarks from Chrome, Safari, Arc, Edge, Brave, and Firefox.
Activated via the "bm" prefix (e.g. "bm github").
"""

from __future__ import annotations

import json
import logging
import os
import subprocess

from wenzi.config import DEFAULT_ICON_CACHE_DIR as _CFG_ICON_CACHE_DIR
from wenzi.scripting.sources import ChooserItem, ChooserSource, fuzzy_match_fields

logger = logging.getLogger(__name__)

_ICON_SIZE = 32
_DEFAULT_ICON_CACHE_DIR = os.path.expanduser(_CFG_ICON_CACHE_DIR)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class Bookmark:
    """A single browser bookmark."""

    __slots__ = ("name", "url", "folder_path", "browser", "profile", "_domain")

    def __init__(
        self,
        name: str,
        url: str,
        folder_path: str = "",
        browser: str = "",
        profile: str | None = None,
    ) -> None:
        self.name = name
        self.url = url
        self.folder_path = folder_path
        self.browser = browser
        self.profile = profile
        self._domain: str | None = None

    def domain(self) -> str:
        """Extract the domain from the URL (cached)."""
        if self._domain is not None:
            return self._domain
        try:
            from urllib.parse import urlparse

            self._domain = urlparse(self.url).netloc
        except Exception:
            self._domain = ""
        return self._domain


# ---------------------------------------------------------------------------
# Chromium-based browsers (Chrome, Arc, Edge, Brave)
# ---------------------------------------------------------------------------

_CHROMIUM_BROWSERS: dict[str, str] = {
    "chrome": "~/Library/Application Support/Google/Chrome",
    "edge": "~/Library/Application Support/Microsoft Edge",
    "brave": "~/Library/Application Support/BraveSoftware/Brave-Browser",
    "arc": "~/Library/Application Support/Arc/User Data",
}


def _read_chromium_bookmarks(
    base_dir: str, browser: str,
) -> list[Bookmark]:
    """Read bookmarks from a Chromium-based browser."""
    base = os.path.expanduser(base_dir)
    if not os.path.isdir(base):
        return []

    bookmarks: list[Bookmark] = []
    seen: set[tuple[str, str]] = set()  # (url, profile) dedup

    # Discover profiles: Default, Profile 1, Profile 2, ...
    profiles = []
    for entry in os.listdir(base):
        profile_dir = os.path.join(base, entry)
        if not os.path.isdir(profile_dir):
            continue
        if entry == "Default" or entry.startswith("Profile "):
            profiles.append((entry, profile_dir))

    if not profiles:
        # Some installs only have Default without profile dirs
        default_dir = os.path.join(base, "Default")
        if os.path.isdir(default_dir):
            profiles.append(("Default", default_dir))

    for profile_name, profile_dir in profiles:
        for filename in ("Bookmarks", "AccountBookmarks"):
            bm_path = os.path.join(profile_dir, filename)
            if not os.path.isfile(bm_path):
                continue
            try:
                with open(bm_path, encoding="utf-8") as f:
                    data = json.load(f)
                roots = data.get("roots", {})
                for root_name in ("bookmark_bar", "other", "synced"):
                    root = roots.get(root_name)
                    if root:
                        _collect_chromium_nodes(
                            root, "", browser, profile_name,
                            bookmarks, seen,
                        )
            except Exception:
                logger.debug(
                    "Failed to read %s bookmarks: %s",
                    browser, bm_path, exc_info=True,
                )

    return bookmarks


def _collect_chromium_nodes(
    node: dict,
    parent_path: str,
    browser: str,
    profile: str,
    out: list[Bookmark],
    seen: set,
) -> None:
    """Recursively collect bookmarks from a Chromium JSON node."""
    node_type = node.get("type", "")
    name = node.get("name", "")

    if node_type == "url":
        url = node.get("url", "")
        if url and (url, profile) not in seen:
            seen.add((url, profile))
            out.append(Bookmark(
                name=name,
                url=url,
                folder_path=parent_path,
                browser=browser,
                profile=profile,
            ))
    elif node_type == "folder" or "children" in node:
        folder_path = f"{parent_path} > {name}" if parent_path else name
        for child in node.get("children", []):
            _collect_chromium_nodes(
                child, folder_path, browser, profile, out, seen,
            )


# ---------------------------------------------------------------------------
# Safari (binary plist)
# ---------------------------------------------------------------------------

_SAFARI_BOOKMARKS_PATH = "~/Library/Safari/Bookmarks.plist"


def _read_safari_bookmarks() -> list[Bookmark]:
    """Read bookmarks from Safari's binary plist file."""
    import plistlib

    path = os.path.expanduser(_SAFARI_BOOKMARKS_PATH)
    if not os.path.isfile(path):
        return []

    try:
        with open(path, "rb") as f:
            data = plistlib.load(f)
        bookmarks: list[Bookmark] = []
        _collect_safari_nodes(data, "", bookmarks)
        return bookmarks
    except PermissionError:
        logger.info(
            "Cannot read Safari bookmarks — Full Disk Access required"
        )
        return []
    except Exception:
        logger.debug("Failed to read Safari bookmarks", exc_info=True)
        return []


def _collect_safari_nodes(
    node: dict, parent_path: str, out: list[Bookmark],
) -> None:
    """Recursively collect bookmarks from a Safari plist node."""
    bm_type = node.get("WebBookmarkType", "")

    if bm_type == "WebBookmarkTypeLeaf":
        url = node.get("URLString", "")
        uri_dict = node.get("URIDictionary", {})
        name = uri_dict.get("title", "") if isinstance(uri_dict, dict) else ""
        if not name:
            name = url
        if url:
            out.append(Bookmark(
                name=name,
                url=url,
                folder_path=parent_path,
                browser="safari",
            ))
    elif bm_type == "WebBookmarkTypeList":
        title = node.get("Title", "")
        # Skip "com.apple.ReadingList" folder
        if title == "com.apple.ReadingList":
            return
        folder_path = f"{parent_path} > {title}" if parent_path and title else title
        for child in node.get("Children", []):
            _collect_safari_nodes(child, folder_path, out)


# ---------------------------------------------------------------------------
# Firefox (SQLite)
# ---------------------------------------------------------------------------

_FIREFOX_BASE = "~/Library/Application Support/Firefox/Profiles"


def _read_firefox_bookmarks() -> list[Bookmark]:
    """Read bookmarks from Firefox's places.sqlite database."""
    base = os.path.expanduser(_FIREFOX_BASE)
    if not os.path.isdir(base):
        return []

    bookmarks: list[Bookmark] = []

    # Find all profile directories (*.default-release, *.default, etc.)
    for entry in os.listdir(base):
        profile_dir = os.path.join(base, entry)
        if not os.path.isdir(profile_dir):
            continue
        places_db = os.path.join(profile_dir, "places.sqlite")
        if not os.path.isfile(places_db):
            continue
        try:
            _read_firefox_places(places_db, bookmarks)
        except Exception:
            logger.debug(
                "Failed to read Firefox bookmarks: %s",
                places_db, exc_info=True,
            )

    return bookmarks


def _read_firefox_places(db_path: str, out: list[Bookmark]) -> None:
    """Read bookmarks from a single Firefox places.sqlite file."""
    # Firefox locks the database; copy to a temp file to avoid locking issues
    import shutil
    import sqlite3
    import tempfile

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    try:
        shutil.copy2(db_path, tmp.name)
        conn = sqlite3.connect(f"file:{tmp.name}?mode=ro", uri=True)
        try:
            # Query bookmarks with their folder hierarchy
            cur = conn.execute("""
                SELECT b.title, p.url, b.parent
                FROM moz_bookmarks b
                JOIN moz_places p ON b.fk = p.id
                WHERE b.type = 1
                  AND p.url NOT LIKE 'place:%'
            """)
            # Build parent folder map
            folder_cur = conn.execute(
                "SELECT id, title, parent FROM moz_bookmarks WHERE type = 2"
            )
            folders: dict[int, tuple[str, int]] = {}
            for fid, ftitle, fparent in folder_cur.fetchall():
                folders[fid] = (ftitle or "", fparent)

            for title, url, parent_id in cur.fetchall():
                if not url:
                    continue
                folder_path = _firefox_folder_path(parent_id, folders)
                out.append(Bookmark(
                    name=title or url,
                    url=url,
                    folder_path=folder_path,
                    browser="firefox",
                ))
        finally:
            conn.close()
    finally:
        os.unlink(tmp.name)


def _firefox_folder_path(parent_id: int, folders: dict[int, tuple]) -> str:
    """Build a folder breadcrumb path for a Firefox bookmark."""
    parts: list[str] = []
    current = parent_id
    seen: set[int] = set()
    while current in folders and current not in seen:
        seen.add(current)
        name, parent = folders[current]
        if name and name not in ("", "root________"):
            parts.append(name)
        current = parent
    parts.reverse()
    return " > ".join(parts)


# ---------------------------------------------------------------------------
# Unified reader
# ---------------------------------------------------------------------------

_BROWSER_LABELS = {
    "chrome": "Chrome",
    "edge": "Edge",
    "brave": "Brave",
    "arc": "Arc",
    "safari": "Safari",
    "firefox": "Firefox",
}

# App name for each browser (used to find the .app bundle)
_BROWSER_APP_NAMES = {
    "chrome": "Google Chrome.app",
    "edge": "Microsoft Edge.app",
    "brave": "Brave Browser.app",
    "arc": "Arc.app",
    "safari": "Safari.app",
    "firefox": "Firefox.app",
}

# Directories to search for browser .app bundles
_APP_SEARCH_DIRS = [
    "/Applications",
    os.path.expanduser("~/Applications"),
    "/System/Applications",
]

# In-memory icon cache: browser_id -> file:// URL string
_browser_icon_cache: dict[str, str] = {}


def _find_browser_app(browser: str) -> str:
    """Find the .app bundle path for a browser, searching multiple dirs."""
    app_name = _BROWSER_APP_NAMES.get(browser, "")
    if not app_name:
        return ""
    for search_dir in _APP_SEARCH_DIRS:
        candidate = os.path.join(search_dir, app_name)
        if os.path.isdir(candidate):
            return candidate
    return ""


def _get_browser_icon(browser: str, icon_cache_dir: str = _DEFAULT_ICON_CACHE_DIR) -> str:
    """Return a file:// URL for the browser's app icon, cached to disk."""
    if browser in _browser_icon_cache:
        return _browser_icon_cache[browser]

    # Check disk cache first
    png_path = os.path.join(icon_cache_dir, f"browser_{browser}.png")
    if os.path.isfile(png_path):
        url = "file://" + png_path
        _browser_icon_cache[browser] = url
        return url

    app_path = _find_browser_app(browser)
    if not app_path:
        _browser_icon_cache[browser] = ""
        return ""

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
        icon = ws.iconForFile_(app_path)
        if icon is None:
            _browser_icon_cache[browser] = ""
            return ""

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
        if png_data:
            os.makedirs(icon_cache_dir, exist_ok=True)
            with open(png_path, "wb") as f:
                f.write(bytes(png_data))
            url = "file://" + png_path
            _browser_icon_cache[browser] = url
            return url
    except Exception:
        logger.debug("Failed to get icon for %s", browser, exc_info=True)

    _browser_icon_cache[browser] = ""
    return ""


def read_all_bookmarks() -> list[Bookmark]:
    """Read bookmarks from all supported browsers."""
    all_bookmarks: list[Bookmark] = []

    for browser, base_dir in _CHROMIUM_BROWSERS.items():
        try:
            all_bookmarks.extend(_read_chromium_bookmarks(base_dir, browser))
        except Exception:
            logger.debug("Error reading %s bookmarks", browser, exc_info=True)

    try:
        all_bookmarks.extend(_read_safari_bookmarks())
    except Exception:
        logger.debug("Error reading Safari bookmarks", exc_info=True)

    try:
        all_bookmarks.extend(_read_firefox_bookmarks())
    except Exception:
        logger.debug("Error reading Firefox bookmarks", exc_info=True)

    return all_bookmarks


# ---------------------------------------------------------------------------
# Chooser source
# ---------------------------------------------------------------------------


def _open_url_in_browser(url: str, browser: str) -> None:
    """Open a URL in the specified browser."""
    app_names = {
        "chrome": "Google Chrome",
        "edge": "Microsoft Edge",
        "brave": "Brave Browser",
        "arc": "Arc",
        "safari": "Safari",
        "firefox": "Firefox",
    }
    app = app_names.get(browser)
    try:
        cmd = ["open", "-a", app, url] if app else ["open", url]
        subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        logger.exception("Failed to open URL: %s", url)


class BookmarkSource:
    """Browser bookmark search data source for the Chooser.

    Activated via the "bm" prefix.  Reads bookmarks from Chrome,
    Safari, Arc, Edge, Brave, and Firefox.  Caches bookmark list
    in memory and refreshes periodically.
    """

    _REFRESH_INTERVAL = 300  # seconds between cache refreshes

    def __init__(self) -> None:
        self._bookmarks: list[Bookmark] = []
        self._last_refresh: float = 0

    def _ensure_loaded(self) -> None:
        import time

        now = time.time()
        if now - self._last_refresh > self._REFRESH_INTERVAL:
            self._bookmarks = read_all_bookmarks()
            self._last_refresh = now
            logger.info("Loaded %d bookmarks from all browsers", len(self._bookmarks))

    def search(self, query: str) -> list[ChooserItem]:
        """Search bookmarks by name, URL domain, or folder path.

        Supports multi-term AND matching: ``"gh rust"`` matches bookmarks
        where both "gh" and "rust" fuzzy-match any combination of fields
        (name, domain, folder path, browser label).
        """
        self._ensure_loaded()

        q = query.strip()
        if not q:
            # Show recent bookmarks (first 20) when no query
            return self._to_items(self._bookmarks[:20])

        results: list[tuple[int, Bookmark]] = []
        for bm in self._bookmarks:
            fields = (
                bm.name,
                bm.domain(),
                bm.folder_path,
                _BROWSER_LABELS.get(bm.browser, ""),
            )
            matched, score = fuzzy_match_fields(q, fields)
            if matched:
                results.append((score, bm))

        results.sort(key=lambda x: (-x[0], x[1].name.lower()))
        return self._to_items([bm for _, bm in results[:50]])

    def _to_items(self, bookmarks: list[Bookmark]) -> list[ChooserItem]:
        """Convert Bookmark objects to ChooserItem list."""
        items = []
        for bm in bookmarks:
            browser_label = _BROWSER_LABELS.get(bm.browser, bm.browser)
            profile_str = f" ({bm.profile})" if bm.profile else ""
            subtitle = f"{bm.url}"
            if bm.folder_path:
                subtitle = f"{bm.folder_path}  —  {browser_label}{profile_str}"
            else:
                subtitle = f"{browser_label}{profile_str}"

            items.append(ChooserItem(
                title=bm.name,
                subtitle=subtitle,
                icon=_get_browser_icon(bm.browser),
                item_id=f"bm:{bm.browser}:{bm.url[:80]}",
                action=lambda u=bm.url, b=bm.browser: _open_url_in_browser(u, b),
                reveal_path=None,
                preview={"type": "text", "content": bm.url},
            ))
        return items

    def as_chooser_source(self, prefix: str = "bm") -> ChooserSource:
        """Return a ChooserSource wrapping this BookmarkSource."""
        from wenzi.i18n import t

        return ChooserSource(
            name="bookmarks",
            display_name=t("chooser.source.bookmarks"),
            prefix=prefix,
            search=self.search,
            priority=5,
            description="Search bookmarks",
            action_hints={
                "enter": t("chooser.action.open"),
            },
        )
