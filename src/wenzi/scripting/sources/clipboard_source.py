"""Clipboard history data source for the Chooser.

Provides search over clipboard history entries recorded by
ClipboardMonitor. Activated via ">cb" prefix or Tab key switching.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, List, Optional

from wenzi.scripting.clipboard_monitor import (
    ClipboardMonitor,
    _icon_cache_path,
)
from wenzi.scripting.sources import (
    ChooserItem, ChooserSource, ModifierAction,
    copy_to_clipboard, paste_text,
)

logger = logging.getLogger(__name__)


def _format_time_ago(timestamp: float) -> str:
    """Format a timestamp as a human-readable relative time."""
    delta = time.time() - timestamp
    if delta < 60:
        return "just now"
    if delta < 3600:
        minutes = int(delta / 60)
        return f"{minutes}m ago"
    if delta < 86400:
        hours = int(delta / 3600)
        return f"{hours}h ago"
    days = int(delta / 86400)
    return f"{days}d ago"



def _format_file_size(size_bytes: int) -> str:
    """Format byte count as human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _paste_image(image_path: str) -> None:
    """Write image from file to clipboard and simulate Cmd+V."""
    try:
        from AppKit import NSPasteboard, NSPasteboardTypePNG

        with open(image_path, "rb") as f:
            png_bytes = f.read()

        from Foundation import NSData

        ns_data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))

        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.declareTypes_owner_(
            [NSPasteboardTypePNG, "org.nspasteboard.ConcealedType"], None
        )
        pb.setData_forType_(ns_data, NSPasteboardTypePNG)

        import subprocess
        import time as _time

        _time.sleep(0.05)
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        logger.exception("Failed to paste image")


def _copy_image_to_clipboard(image_path: str) -> None:
    """Write image to clipboard without pasting. Uses concealed marker."""
    try:
        from AppKit import NSPasteboard, NSPasteboardTypePNG

        with open(image_path, "rb") as f:
            png_bytes = f.read()

        from Foundation import NSData

        ns_data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))

        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.declareTypes_owner_(
            [NSPasteboardTypePNG, "org.nspasteboard.ConcealedType"], None
        )
        pb.setData_forType_(ns_data, NSPasteboardTypePNG)
    except Exception:
        logger.exception("Failed to copy image to clipboard")


class ClipboardSource:
    """Clipboard history search data source.

    Uses a ClipboardMonitor to access recorded entries.
    Supports substring filtering and pastes the selected entry on execute.
    """

    _DEFAULT_MAX_RESULTS = 30

    _CACHE_TTL = 10.0  # seconds before time-ago strings become stale

    def __init__(
        self, monitor: ClipboardMonitor, max_results: int = _DEFAULT_MAX_RESULTS,
    ) -> None:
        self._monitor = monitor
        self._max_results = max_results
        self._empty_cache: Optional[List[ChooserItem]] = None
        self._empty_cache_version: int = -1
        self._empty_cache_time: float = 0.0
        self._icon_mem_cache: Dict[str, str] = {}  # bundle_id → data URI or ""
        self._icon_miss_until: Dict[str, float] = {}  # bundle_id → retry-after ts

    _ICON_MISS_TTL = 30.0  # seconds before rechecking disk for a missed icon

    def _get_icon_uri(self, bundle_id: str) -> str:
        """Return a file:// URL for an app icon.

        Checks memory cache → disk existence (pre-populated by the
        polling thread via ClipboardMonitor._cache_app_icon).
        Never reads file content or does base64 encoding; the browser
        loads the file natively via file:// URL with its own cache.
        """
        if not bundle_id:
            return ""
        if bundle_id in self._icon_mem_cache:
            return self._icon_mem_cache[bundle_id]

        # Avoid repeated disk checks for icons we recently failed to find
        now = time.time()
        miss_until = self._icon_miss_until.get(bundle_id, 0.0)
        if now < miss_until:
            return ""

        icon_dir = self._monitor.icon_cache_dir

        # Check disk cache (normally pre-populated by polling thread)
        png_path = _icon_cache_path(icon_dir, bundle_id)
        if os.path.isfile(png_path):
            uri = "file://" + png_path
            self._icon_mem_cache[bundle_id] = uri
            self._icon_miss_until.pop(bundle_id, None)
            return uri

        # Icon not cached yet — suppress disk checks for a while.
        # Polling thread will cache it on the next clipboard change.
        self._icon_miss_until[bundle_id] = now + self._ICON_MISS_TTL
        return ""

    def search(self, query: str) -> List[ChooserItem]:
        """Search clipboard history entries."""
        q = query.strip().lower()

        # Fast path: return cached results for empty queries
        if not q:
            ver = self._monitor.version
            now = time.time()
            if (
                self._empty_cache is not None
                and self._empty_cache_version == ver
                and (now - self._empty_cache_time) < self._CACHE_TTL
            ):
                return list(self._empty_cache)

        entries = self._monitor.entries

        if not entries:
            self._empty_cache = None
            return []

        results = []

        for entry in entries:
            is_image = bool(entry.image_path)
            time_ago = _format_time_ago(entry.timestamp)
            subtitle = entry.source_app if entry.source_app else ""

            if is_image:
                # Image entries: match query against "image"
                if q and "image" not in q and q not in subtitle.lower():
                    continue

                display = self._format_image_title(entry)

                full_path = os.path.join(self._monitor.image_dir, entry.image_path)
                monitor = self._monitor
                ep = entry.image_path  # capture

                def _do_paste_img(p=full_path, ip=ep, m=monitor):
                    m.promote_image(ip)
                    _paste_image(p)

                def _do_copy_img(p=full_path, ip=ep, m=monitor):
                    m.promote_image(ip)
                    _copy_image_to_clipboard(p)

                def _do_delete_img(ip=ep, m=monitor):
                    m.delete_image(ip)

                _entry = entry  # capture for lambda

                def _lazy_preview(e=_entry):
                    return self._make_preview(e)

                results.append(
                    ChooserItem(
                        title=display,
                        subtitle=f"{subtitle}  {time_ago}".strip() if subtitle else time_ago,
                        icon=self._get_icon_uri(entry.source_bundle_id),
                        item_id=f"cb:img:{ep}",
                        preview=_lazy_preview,
                        action=_do_paste_img,
                        secondary_action=_do_copy_img,
                        delete_action=_do_delete_img,
                    )
                )
                if len(results) >= self._max_results:
                    break
            else:
                # Text entries
                if q and q not in entry.text.lower():
                    continue

                display = entry.text.replace("\n", " ").strip()
                if len(display) > 80:
                    display = display[:77] + "..."

                text = entry.text  # capture
                monitor = self._monitor

                def _do_paste(t=text, m=monitor):
                    m.promote(t)
                    paste_text(t)

                def _do_copy(t=text, m=monitor):
                    m.promote(t)
                    copy_to_clipboard(t)

                def _do_delete_text(t=text, m=monitor):
                    m.delete_text(t)

                # Use first 64 chars of text as stable id
                text_key = text[:64].replace("\n", " ")
                def _do_edit(t=text):
                    from wenzi.scripting.ui.quick_edit_panel import (
                        open_quick_edit,
                    )
                    open_quick_edit(t)

                results.append(
                    ChooserItem(
                        title=display,
                        subtitle=f"{subtitle}  {time_ago}".strip() if subtitle else time_ago,
                        icon=self._get_icon_uri(entry.source_bundle_id),
                        item_id=f"cb:txt:{text_key}",
                        preview={"type": "text", "content": text},
                        action=_do_paste,
                        secondary_action=_do_copy,
                        delete_action=_do_delete_text,
                        modifiers={"alt": ModifierAction(
                            subtitle="Quick Edit",
                            action=_do_edit,
                        )},
                    )
                )
                if len(results) >= self._max_results:
                    break

        # Cache empty-query results for reuse on next open.
        # Use the version read at lookup time (ver) to stay consistent
        # with the entries snapshot used to build results.
        if not q:
            self._empty_cache = list(results)
            self._empty_cache_version = ver
            self._empty_cache_time = time.time()

        return results

    @staticmethod
    def _format_image_title(entry) -> str:
        """Format a human-readable title for an image clipboard entry."""
        parts = ["Image:"]
        if entry.image_width and entry.image_height:
            parts.append(f"{entry.image_width}\u00d7{entry.image_height}")
        if entry.image_size:
            parts.append(f"({_format_file_size(entry.image_size)})")
        return " ".join(parts)

    def _make_preview(self, entry) -> dict:
        """Build a preview dict for the given clipboard entry."""
        if entry.image_path:
            return self._make_image_preview(entry)
        return {"type": "text", "content": entry.text}

    def _make_image_preview(self, entry) -> dict:
        """Build an image preview dict with a file:// URL."""
        info_parts = []
        if entry.image_width and entry.image_height:
            info_parts.append(f"{entry.image_width}\u00d7{entry.image_height}")
        if entry.image_size:
            info_parts.append(_format_file_size(entry.image_size))

        full_path = os.path.join(
            self._monitor.image_dir, entry.image_path,
        )
        src = "file://" + full_path if os.path.isfile(full_path) else ""

        return {
            "type": "image",
            "src": src,
            "info": " \u00b7 ".join(info_parts) if info_parts else "",
        }

    def as_chooser_source(self, prefix: str = "cb") -> ChooserSource:
        """Return a ChooserSource wrapping this ClipboardSource."""
        return ChooserSource(
            name="clipboard",
            prefix=prefix,
            search=self.search,
            priority=5,
            description="Clipboard history",
            action_hints={
                "enter": "Paste",
                "cmd_enter": "Copy",
                "alt_enter": "Edit",
                "delete": "Delete",
            },
            show_preview=True,
        )
