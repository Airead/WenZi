"""File search data source for the Chooser.

Uses macOS Spotlight (mdfind) for fast file search.  Activated via
the "f" prefix (e.g. "f readme").
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import threading
from typing import List, Optional, Set

import objc

from wenzi.config import DEFAULT_ICON_CACHE_DIR as _CFG_ICON_CACHE_DIR
from wenzi.scripting.sources import ChooserItem, ChooserSource
from wenzi.scripting.sources._mdquery import mdquery_search
from wenzi.scripting.sources.app_source import _cache_key as _app_cache_key

logger = logging.getLogger(__name__)

_MAX_RESULTS = 30
_ICON_SIZE = 32
_DEFAULT_ICON_CACHE_DIR = os.path.expanduser(_CFG_ICON_CACHE_DIR)

# Common extensions to pre-warm in background on init
_COMMON_EXTENSIONS = [
    ".pdf", ".txt", ".md", ".py", ".js", ".ts", ".html", ".css",
    ".json", ".yaml", ".yml", ".xml", ".png", ".jpg", ".jpeg",
    ".gif", ".svg", ".mp4", ".mov", ".mp3", ".zip", ".gz", ".dmg",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv",
    ".sh", ".rb", ".go", ".rs", ".java", ".c", ".cpp", ".h",
]


def _icon_png_path_for_ext(icon_cache_dir: str, ext: str) -> str:
    """Return the disk cache path for a file-type icon."""
    safe = ext.lstrip(".") or "_none"
    return os.path.join(icon_cache_dir, f"filetype_{safe}.png")


def _icon_png_path_for_folder(icon_cache_dir: str, path: str) -> str:
    """Return the disk cache path for a folder icon (hashed by path)."""
    h = hashlib.md5(path.encode()).hexdigest()
    return os.path.join(icon_cache_dir, f"folder_{h}.png")


def _resize_icon_to_png(icon, png_path: str, icon_cache_dir: str) -> None:
    """Resize an NSImage icon to _ICON_SIZE and write it as PNG.

    *icon* is an ``NSImage`` obtained from NSWorkspace.  The caller is
    responsible for checking the disk cache beforehand.
    """
    with objc.autorelease_pool():
        from AppKit import (
            NSBitmapImageRep,
            NSCompositingOperationCopy,
            NSDeviceRGBColorSpace,
            NSGraphicsContext,
            NSPNGFileType,
        )
        from Foundation import NSMakeRect, NSZeroRect

        sz = _ICON_SIZE
        rep = NSBitmapImageRep.alloc() \
            .initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
                None, sz, sz, 8, 4, True, False,
                NSDeviceRGBColorSpace, 0, 0,
            )
        if rep is None:
            return
        ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        if ctx is None:
            return
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.setCurrentContext_(ctx)
        icon.drawInRect_fromRect_operation_fraction_(
            NSMakeRect(0, 0, sz, sz), NSZeroRect,
            NSCompositingOperationCopy, 1.0,
        )
        NSGraphicsContext.restoreGraphicsState()

        png_data = rep.representationUsingType_properties_(NSPNGFileType, None)
        if png_data:
            os.makedirs(icon_cache_dir, exist_ok=True)
            with open(png_path, "wb") as f:
                f.write(bytes(png_data))


def _extract_filetype_icon(ext: str, icon_cache_dir: str) -> None:
    """Extract icon for a file extension and write to disk cache.

    Uses NSWorkspace.iconForFileType_ which accepts an extension string.
    Must NOT be called on the main thread.
    """
    png_path = _icon_png_path_for_ext(icon_cache_dir, ext)
    if os.path.isfile(png_path):
        return
    try:
        from AppKit import NSWorkspace

        icon = NSWorkspace.sharedWorkspace().iconForFileType_(ext)
        if icon is None:
            return
        _resize_icon_to_png(icon, png_path, icon_cache_dir)
    except Exception:
        logger.debug("Failed to extract icon for extension %s", ext, exc_info=True)


def _extract_folder_icon(path: str, icon_cache_dir: str) -> None:
    """Extract icon for a specific folder and write to disk cache.

    Uses NSWorkspace.iconForFile_ to get the folder's actual icon
    (including special icons for Downloads, Documents, etc.).
    Must NOT be called on the main thread.
    """
    png_path = _icon_png_path_for_folder(icon_cache_dir, path)
    if os.path.isfile(png_path):
        return
    try:
        from AppKit import NSWorkspace

        icon = NSWorkspace.sharedWorkspace().iconForFile_(path)
        if icon is None:
            return
        _resize_icon_to_png(icon, png_path, icon_cache_dir)
    except Exception:
        logger.debug("Failed to extract folder icon for %s", path, exc_info=True)


def _mdfind(
    query: str, max_results: int = _MAX_RESULTS, content_type: str | None = None,
) -> list[str]:
    """Search files by name using MDQuery (Spotlight C API)."""
    return mdquery_search(query, max_results, content_type=content_type)


def _open_file(path: str) -> None:
    """Open a file with the default application."""
    try:
        subprocess.Popen(  # noqa: S603
            ["open", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        logger.exception("Failed to open file: %s", path)


def _file_type_label(path: str) -> str:
    """Return a short label describing the file type."""
    if os.path.isdir(path):
        return "Folder"
    ext = os.path.splitext(path)[1].lower()
    labels = {
        ".pdf": "PDF",
        ".txt": "Text",
        ".md": "Markdown",
        ".py": "Python",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".html": "HTML",
        ".css": "CSS",
        ".json": "JSON",
        ".yaml": "YAML",
        ".yml": "YAML",
        ".xml": "XML",
        ".png": "Image",
        ".jpg": "Image",
        ".jpeg": "Image",
        ".gif": "Image",
        ".svg": "Image",
        ".mp4": "Video",
        ".mov": "Video",
        ".mp3": "Audio",
        ".zip": "Archive",
        ".gz": "Archive",
        ".dmg": "Disk Image",
        ".app": "Application",
    }
    return labels.get(ext, "File")


class FileSource:
    """File search data source using macOS Spotlight.

    Icons are resolved from disk cache only (no AppKit on main thread).
    Missing icons are extracted in a background thread and become
    available on the next search.
    """

    def __init__(
        self,
        max_results: int = _MAX_RESULTS,
        icon_cache_dir: Optional[str] = None,
    ) -> None:
        self._max_results = max_results
        self._icon_cache_dir = icon_cache_dir or _DEFAULT_ICON_CACHE_DIR
        # Track extensions already submitted for background extraction
        self._pending_exts: Set[str] = set()
        self._lock = threading.Lock()
        self._prewarm_common_extensions()

    def _prewarm_common_extensions(self) -> None:
        """Pre-extract icons for common file extensions in background."""
        cache_dir = self._icon_cache_dir

        def _warm():
            for ext in _COMMON_EXTENSIONS:
                _extract_filetype_icon(ext, cache_dir)

        threading.Thread(target=_warm, daemon=True).start()

    def _get_icon_url(self, path: str) -> str:
        """Return a file:// URL for the file's icon from disk cache.

        Only checks disk — never calls AppKit. Schedules background
        extraction for cache misses.
        """
        ext = os.path.splitext(path)[1].lower()
        if ext == ".app":
            return self._get_app_icon_url(path)

        if not ext:
            ext = "public.data"

        png_path = _icon_png_path_for_ext(self._icon_cache_dir, ext)
        if os.path.isfile(png_path):
            return "file://" + png_path

        # Schedule background extraction
        self._schedule_ext_extraction(ext)
        return ""

    def _get_app_icon_url(self, path: str) -> str:
        """Return file:// URL for a .app icon, reusing app_source's cache."""
        key = _app_cache_key(path)
        png_path = os.path.join(self._icon_cache_dir, f"{key}.png")
        if os.path.isfile(png_path):
            return "file://" + png_path
        return ""

    def _schedule_ext_extraction(self, ext: str) -> None:
        """Submit extension icon extraction to background thread (deduped)."""
        with self._lock:
            if ext in self._pending_exts:
                return
            self._pending_exts.add(ext)

        cache_dir = self._icon_cache_dir

        def _extract(e=ext, d=cache_dir):
            _extract_filetype_icon(e, d)
            with self._lock:
                self._pending_exts.discard(e)

        threading.Thread(target=_extract, daemon=True).start()

    def search(self, query: str) -> List[ChooserItem]:
        """Search files (excluding folders) by name using mdfind."""
        if not query.strip():
            return []

        paths = _mdfind(query, self._max_results, content_type="!public.folder")
        items = []
        for path in paths:
            if not os.path.exists(path):
                continue
            name = os.path.basename(path)
            parent = os.path.dirname(path)
            # Shorten home directory
            home = os.path.expanduser("~")
            if parent.startswith(home):
                parent = "~" + parent[len(home):]

            type_label = _file_type_label(path)
            subtitle = f"{type_label}  {parent}"

            items.append(
                ChooserItem(
                    title=name,
                    subtitle=subtitle,
                    icon=self._get_icon_url(path),
                    item_id=f"file:{path}",
                    action=lambda p=path: _open_file(p),
                    reveal_path=path,
                    preview=_make_file_preview(path),
                )
            )

        return items

    def as_chooser_source(self, prefix: str = "f") -> ChooserSource:
        """Return a ChooserSource wrapping this FileSource."""
        from wenzi.i18n import t

        return ChooserSource(
            name="files",
            display_name=t("chooser.source.files"),
            prefix=prefix,
            search=self.search,
            priority=3,
            description="Search files",
            action_hints={
                "enter": t("chooser.action.open"),
                "cmd_enter": t("chooser.action.reveal"),
                "shift": t("chooser.action.preview"),
            },
        )


class FolderSource:
    """Folder search data source using macOS Spotlight.

    Only returns folders. Icons are resolved from disk cache;
    missing icons are extracted in a background thread.
    """

    def __init__(
        self,
        max_results: int = _MAX_RESULTS,
        icon_cache_dir: Optional[str] = None,
    ) -> None:
        self._max_results = max_results
        self._icon_cache_dir = icon_cache_dir or _DEFAULT_ICON_CACHE_DIR
        self._pending_folders: Set[str] = set()
        self._lock = threading.Lock()

    def _get_icon_url(self, path: str) -> str:
        """Return file:// URL for a folder icon from disk cache."""
        png_path = _icon_png_path_for_folder(self._icon_cache_dir, path)
        if os.path.isfile(png_path):
            return "file://" + png_path

        # Schedule background extraction
        with self._lock:
            if path in self._pending_folders:
                return ""
            self._pending_folders.add(path)

        cache_dir = self._icon_cache_dir

        def _extract(p=path, d=cache_dir):
            _extract_folder_icon(p, d)
            with self._lock:
                self._pending_folders.discard(p)

        threading.Thread(target=_extract, daemon=True).start()
        return ""

    def search(self, query: str) -> List[ChooserItem]:
        """Search folders by name using mdfind."""
        if not query.strip():
            return []

        paths = _mdfind(query, self._max_results, content_type="public.folder")
        items = []
        for path in paths:
            if not os.path.exists(path):
                continue
            name = os.path.basename(path)
            parent = os.path.dirname(path)
            home = os.path.expanduser("~")
            if parent.startswith(home):
                parent = "~" + parent[len(home):]

            items.append(
                ChooserItem(
                    title=name,
                    subtitle=f"Folder  {parent}",
                    icon=self._get_icon_url(path),
                    item_id=f"folder:{path}",
                    action=lambda p=path: _open_file(p),
                    reveal_path=path,
                    preview=_make_file_preview(path),
                )
            )

        return items

    def as_chooser_source(self, prefix: str = "fd") -> ChooserSource:
        """Return a ChooserSource wrapping this FolderSource."""
        from wenzi.i18n import t

        return ChooserSource(
            name="folders",
            display_name=t("chooser.source.folders"),
            prefix=prefix,
            search=self.search,
            priority=3,
            description="Search folders",
            action_hints={
                "enter": t("chooser.action.open"),
                "cmd_enter": t("chooser.action.reveal"),
                "shift": t("chooser.action.preview"),
            },
        )


def _make_file_preview(path: str) -> Optional[dict]:
    """Build a preview dict for a file path."""
    return {"type": "path", "content": path}
