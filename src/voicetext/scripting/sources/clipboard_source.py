"""Clipboard history data source for the Chooser.

Provides search over clipboard history entries recorded by
ClipboardMonitor. Activated via ">cb" prefix or Tab key switching.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import List

from voicetext.scripting.clipboard_monitor import ClipboardMonitor
from voicetext.scripting.sources import ChooserItem, ChooserSource

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


def _paste_text(text: str) -> None:
    """Write text to clipboard and simulate Cmd+V to paste at cursor."""
    try:
        from voicetext.input import _set_pasteboard_concealed

        import subprocess
        import time as _time

        _set_pasteboard_concealed(text)
        _time.sleep(0.05)
        subprocess.run(
            [
                "osascript", "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ],
            capture_output=True, timeout=5,
        )
    except Exception:
        logger.exception("Failed to paste clipboard text")


def _copy_to_clipboard(text: str) -> None:
    """Write text to the system clipboard (without pasting).

    Uses concealed marker so the clipboard monitor does not re-record it,
    but moves the entry to the top of history for freshness.
    """
    try:
        from voicetext.input import _set_pasteboard_concealed

        _set_pasteboard_concealed(text)
    except Exception:
        logger.exception("Failed to copy to clipboard")


def _format_file_size(size_bytes: int) -> str:
    """Format byte count as human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _make_thumbnail_data_uri(image_path: str, max_dim: int = 480) -> str:
    """Read a PNG file, resize to fit *max_dim*, return as base64 data URI."""
    from AppKit import NSBitmapImageRep, NSPNGFileType

    with open(image_path, "rb") as f:
        raw = f.read()

    from Foundation import NSData

    ns_data = NSData.dataWithBytes_length_(raw, len(raw))
    rep = NSBitmapImageRep.imageRepWithData_(ns_data)
    if rep is None:
        return ""

    w, h = int(rep.pixelsWide()), int(rep.pixelsHigh())
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        from AppKit import NSImage
        from Foundation import NSMakeSize

        img = NSImage.alloc().initWithSize_(NSMakeSize(new_w, new_h))
        img.addRepresentation_(rep)

        img.setSize_(NSMakeSize(new_w, new_h))
        img.lockFocus()
        from AppKit import NSGraphicsContext

        NSGraphicsContext.currentContext().setImageInterpolation_(3)  # High
        img.unlockFocus()

        tiff_data = img.TIFFRepresentation()
        rep = NSBitmapImageRep.imageRepWithData_(tiff_data)
        if rep is None:
            return ""

    png_data = rep.representationUsingType_properties_(NSPNGFileType, {})
    if png_data is None:
        return ""

    b64 = base64.b64encode(bytes(png_data)).decode("ascii")
    return f"data:image/png;base64,{b64}"


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

    def __init__(self, monitor: ClipboardMonitor) -> None:
        self._monitor = monitor

    def search(self, query: str) -> List[ChooserItem]:
        """Search clipboard history entries."""
        entries = self._monitor.entries

        if not entries:
            return []

        q = query.strip().lower()
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

                image_dir_val = ClipboardMonitor.default_image_dir()
                full_path = os.path.join(image_dir_val, entry.image_path)
                monitor = self._monitor
                ep = entry.image_path  # capture

                def _do_paste_img(p=full_path, ip=ep, m=monitor):
                    m.promote_image(ip)
                    _paste_image(p)

                def _do_copy_img(p=full_path, ip=ep, m=monitor):
                    m.promote_image(ip)
                    _copy_image_to_clipboard(p)

                preview = self._make_preview(entry)

                results.append(
                    ChooserItem(
                        title=display,
                        subtitle=f"{subtitle}  {time_ago}".strip() if subtitle else time_ago,
                        preview=preview,
                        action=_do_paste_img,
                        secondary_action=_do_copy_img,
                    )
                )
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
                    _paste_text(t)

                def _do_copy(t=text, m=monitor):
                    m.promote(t)
                    _copy_to_clipboard(t)

                preview = self._make_preview(entry)

                results.append(
                    ChooserItem(
                        title=display,
                        subtitle=f"{subtitle}  {time_ago}".strip() if subtitle else time_ago,
                        preview=preview,
                        action=_do_paste,
                        secondary_action=_do_copy,
                    )
                )

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

    @staticmethod
    def _make_preview(entry) -> dict:
        """Build a preview dict for the given clipboard entry."""
        if entry.image_path:
            return ClipboardSource._make_image_preview(entry)
        return {"type": "text", "content": entry.text}

    @staticmethod
    def _make_image_preview(entry) -> dict:
        """Build an image preview dict with a base64 data URI thumbnail."""
        from voicetext.scripting.clipboard_monitor import ClipboardMonitor

        image_dir = ClipboardMonitor.default_image_dir()
        full_path = os.path.join(image_dir, entry.image_path)

        info_parts = []
        if entry.image_width and entry.image_height:
            info_parts.append(f"{entry.image_width}\u00d7{entry.image_height}")
        if entry.image_size:
            info_parts.append(_format_file_size(entry.image_size))

        src = ""
        try:
            if os.path.isfile(full_path):
                src = _make_thumbnail_data_uri(full_path)
        except Exception:
            logger.debug("Failed to create thumbnail for %s", full_path, exc_info=True)

        return {
            "type": "image",
            "src": src,
            "info": " \u00b7 ".join(info_parts) if info_parts else "",
        }

    def as_chooser_source(self) -> ChooserSource:
        """Return a ChooserSource wrapping this ClipboardSource."""
        return ChooserSource(
            name="clipboard",
            prefix="cb",
            search=self.search,
            priority=5,
        )
