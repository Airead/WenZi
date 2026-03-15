"""Clipboard change monitor.

Polls NSPasteboard.changeCount() in a background thread and records
text entries, excluding concealed/transient clipboard content from
password managers and VoiceText itself.

Storage uses SQLite for incremental writes instead of full JSON dumps.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# Pasteboard types that indicate concealed/transient content
_CONCEALED_TYPE = "org.nspasteboard.ConcealedType"
_TRANSIENT_TYPE = "com.nspasteboard.TransientType"

_MAX_TEXT_LENGTH = 10_240  # 10 KB

_DEFAULT_IMAGE_DIR = os.path.expanduser("~/.config/VoiceText/clipboard_images")


@dataclass
class ClipboardEntry:
    """A single clipboard history entry."""

    text: str = ""
    timestamp: float = field(default_factory=time.time)
    source_app: str = ""
    image_path: str = ""  # filename in clipboard_images/ (empty = text entry)
    image_width: int = 0
    image_height: int = 0
    image_size: int = 0  # file size in bytes


# ---------------------------------------------------------------------------
# SQLite storage
# ---------------------------------------------------------------------------

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT    NOT NULL DEFAULT '',
    timestamp  REAL    NOT NULL,
    source_app TEXT    NOT NULL DEFAULT '',
    image_path TEXT    NOT NULL DEFAULT '',
    image_width  INTEGER NOT NULL DEFAULT 0,
    image_height INTEGER NOT NULL DEFAULT 0,
    image_size   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_entries_timestamp ON entries(timestamp);
"""


class _ClipboardDB:
    """Thin wrapper around a SQLite database for clipboard entries."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # -- writes --

    def insert(self, entry: ClipboardEntry) -> None:
        self._conn.execute(
            "INSERT INTO entries"
            " (text, timestamp, source_app, image_path,"
            "  image_width, image_height, image_size)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entry.text, entry.timestamp, entry.source_app,
                entry.image_path, entry.image_width,
                entry.image_height, entry.image_size,
            ),
        )
        self._conn.commit()

    def update_timestamp(self, **match) -> bool:
        """Set timestamp=now for the first row matching *match* kwargs.

        Returns True if a row was updated.
        """
        if not match:
            return False
        where = " AND ".join(f"{k}=?" for k in match)
        vals = list(match.values())
        cur = self._conn.execute(
            f"SELECT id FROM entries WHERE {where} LIMIT 1", vals,  # noqa: S608
        )
        row = cur.fetchone()
        if row is None:
            return False
        self._conn.execute(
            "UPDATE entries SET timestamp=? WHERE id=?",
            (time.time(), row[0]),
        )
        self._conn.commit()
        return True

    def delete_expired(self, max_days: int) -> List[str]:
        """Delete entries older than *max_days*. Returns removed image paths."""
        cutoff = time.time() - max_days * 86400
        cur = self._conn.execute(
            "SELECT image_path FROM entries"
            " WHERE timestamp < ? AND image_path != ''",
            (cutoff,),
        )
        removed = [row[0] for row in cur.fetchall()]
        self._conn.execute("DELETE FROM entries WHERE timestamp < ?", (cutoff,))
        self._conn.commit()
        return removed

    def delete_all(self) -> None:
        self._conn.execute("DELETE FROM entries")
        self._conn.commit()

    def delete_missing_images(self, image_dir: str) -> int:
        """Delete image entries whose files no longer exist on disk."""
        cur = self._conn.execute(
            "SELECT id, image_path FROM entries WHERE image_path != ''",
        )
        to_delete = []
        for row_id, img in cur.fetchall():
            if not os.path.isfile(os.path.join(image_dir, img)):
                to_delete.append(row_id)
        if to_delete:
            placeholders = ",".join("?" * len(to_delete))
            self._conn.execute(
                f"DELETE FROM entries WHERE id IN ({placeholders})",  # noqa: S608
                to_delete,
            )
            self._conn.commit()
        return len(to_delete)

    # -- reads --

    def load_all(self, max_days: int) -> List[ClipboardEntry]:
        """Return all non-expired entries, newest first."""
        cutoff = time.time() - max_days * 86400
        cur = self._conn.execute(
            "SELECT text, timestamp, source_app,"
            " image_path, image_width, image_height, image_size"
            " FROM entries WHERE timestamp >= ?"
            " ORDER BY timestamp DESC",
            (cutoff,),
        )
        return [
            ClipboardEntry(
                text=row[0], timestamp=row[1], source_app=row[2],
                image_path=row[3], image_width=row[4],
                image_height=row[5], image_size=row[6],
            )
            for row in cur.fetchall()
        ]

    def latest_text(self) -> str:
        """Return the text of the most recent entry, or ''."""
        cur = self._conn.execute(
            "SELECT text FROM entries ORDER BY timestamp DESC LIMIT 1",
        )
        row = cur.fetchone()
        return row[0] if row else ""

    def latest_image_path(self) -> str:
        """Return the image_path of the most recent entry, or ''."""
        cur = self._conn.execute(
            "SELECT image_path FROM entries ORDER BY timestamp DESC LIMIT 1",
        )
        row = cur.fetchone()
        return row[0] if row else ""


# ---------------------------------------------------------------------------
# JSON migration helper
# ---------------------------------------------------------------------------


def _migrate_json_to_db(json_path: str, db: _ClipboardDB) -> int:
    """Import entries from an old JSON file into the database.

    Returns the number of entries imported.  The JSON file is renamed
    to ``*.json.bak`` after successful migration.
    """
    if not os.path.isfile(json_path):
        return 0
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return 0
        count = 0
        for d in data:
            if not isinstance(d, dict):
                continue
            entry = ClipboardEntry(
                text=d.get("text", ""),
                timestamp=d.get("timestamp", 0),
                source_app=d.get("source_app", ""),
                image_path=d.get("image_path", ""),
                image_width=d.get("image_width", 0),
                image_height=d.get("image_height", 0),
                image_size=d.get("image_size", 0),
            )
            db.insert(entry)
            count += 1
        # Rename old file so we don't re-import
        os.rename(json_path, json_path + ".bak")
        logger.info("Migrated %d entries from JSON to SQLite", count)
        return count
    except Exception:
        logger.debug("Failed to migrate JSON clipboard history", exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class ClipboardMonitor:
    """Background monitor that records clipboard text changes.

    Polls NSPasteboard.changeCount() at a configurable interval and
    stores entries in memory (newest-first list) backed by SQLite.
    """

    def __init__(
        self,
        max_days: int = 7,
        poll_interval: float = 0.5,
        persist_path: Optional[str] = None,
        image_dir: Optional[str] = None,
    ) -> None:
        self._max_days = max_days
        self._poll_interval = poll_interval
        self._persist_path = persist_path
        self._image_dir = image_dir or _DEFAULT_IMAGE_DIR
        self._entries: List[ClipboardEntry] = []
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_change_count: int = -1
        self._db: Optional[_ClipboardDB] = None

        if persist_path:
            self._init_db()

    def _init_db(self) -> None:
        """Open or create the SQLite database and load entries."""
        db_path = self._db_path()
        self._db = _ClipboardDB(db_path)

        # Migrate legacy JSON file if present
        json_path = self._persist_path
        if json_path and os.path.isfile(json_path):
            _migrate_json_to_db(json_path, self._db)

        # Clean up missing images and expired entries
        dropped = self._db.delete_missing_images(self._image_dir)
        if dropped:
            logger.info("Dropped %d clipboard entries (image files missing)", dropped)

        expired_images = self._db.delete_expired(self._max_days)
        if expired_images:
            logger.info(
                "Dropped %d expired clipboard entries (>%d days)",
                len(expired_images), self._max_days,
            )
            self._cleanup_image_files(expired_images)

        # Load into memory
        with self._lock:
            self._entries = self._db.load_all(self._max_days)
        logger.info("Loaded %d clipboard history entries", len(self._entries))

    def _db_path(self) -> str:
        """Derive the SQLite path from the persist_path."""
        # e.g. ~/.config/VoiceText/clipboard_history.json
        #   -> ~/.config/VoiceText/clipboard_history.db
        base = self._persist_path
        if base and base.endswith(".json"):
            return base[:-5] + ".db"
        return (base or "") + ".db"

    @staticmethod
    def default_image_dir() -> str:
        """Return the default directory for clipboard image storage."""
        return _DEFAULT_IMAGE_DIR

    @property
    def entries(self) -> List[ClipboardEntry]:
        """Return a copy of the history (newest first)."""
        with self._lock:
            return list(self._entries)

    def start(self) -> None:
        """Start the background polling thread."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()

        # Capture current changeCount so we don't record existing content
        try:
            from AppKit import NSPasteboard

            pb = NSPasteboard.generalPasteboard()
            self._last_change_count = pb.changeCount()
        except Exception:
            self._last_change_count = -1

        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Clipboard monitor started (interval=%.1fs)", self._poll_interval)

    def stop(self) -> None:
        """Stop the background polling thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.warning("Clipboard monitor thread did not stop in time")
            self._thread = None
        logger.info("Clipboard monitor stopped")

    def clear(self) -> None:
        """Clear all history entries and associated image files."""
        with self._lock:
            self._entries.clear()
        if self._db:
            self._db.delete_all()
        self._clear_image_dir()

    def _poll_loop(self) -> None:
        """Main polling loop running in a background thread."""
        while not self._stop_event.is_set():
            try:
                self._check_clipboard()
            except Exception:
                logger.debug("Clipboard poll error", exc_info=True)
            self._stop_event.wait(self._poll_interval)

    def _check_clipboard(self) -> None:
        """Check if the clipboard has changed and record new content."""
        from AppKit import (
            NSPasteboard,
            NSPasteboardTypePNG,
            NSPasteboardTypeString,
            NSPasteboardTypeTIFF,
        )

        pb = NSPasteboard.generalPasteboard()
        current_count = pb.changeCount()

        if current_count == self._last_change_count:
            return

        self._last_change_count = current_count

        # Skip concealed/transient content (password managers, VoiceText paste)
        if self._is_concealed(pb):
            logger.debug("Skipping concealed/transient clipboard entry")
            return

        # Try text first (skip entries larger than 10 KB)
        text = pb.stringForType_(NSPasteboardTypeString)
        if text and str(text).strip():
            text_str = str(text).strip()
            if len(text_str) > _MAX_TEXT_LENGTH:
                logger.debug(
                    "Skipping clipboard text: %d chars exceeds 10 KB limit",
                    len(text_str),
                )
                return
            source_app = self._get_frontmost_app()
            self._add_entry(text_str, source_app)
            return

        # Try image (PNG first, then TIFF)
        image_data = pb.dataForType_(NSPasteboardTypePNG)
        image_type = "png"
        if image_data is None:
            image_data = pb.dataForType_(NSPasteboardTypeTIFF)
            image_type = "tiff"
        if image_data is not None:
            source_app = self._get_frontmost_app()
            self._add_image_entry(bytes(image_data), image_type, source_app)

    @staticmethod
    def _is_concealed(pb) -> bool:
        """Check if the pasteboard contains concealed/transient markers."""
        types = pb.types()
        if types is None:
            return False
        type_list = list(types)
        return _CONCEALED_TYPE in type_list or _TRANSIENT_TYPE in type_list

    @staticmethod
    def _get_frontmost_app() -> str:
        """Return the name of the frontmost application."""
        try:
            from AppKit import NSWorkspace

            workspace = NSWorkspace.sharedWorkspace()
            app = workspace.frontmostApplication()
            if app and app.localizedName():
                return str(app.localizedName())
        except Exception:
            pass
        return ""

    def promote(self, text: str) -> None:
        """Move an existing text entry to the top of the history list."""
        with self._lock:
            for i, entry in enumerate(self._entries):
                if entry.text == text:
                    self._entries.pop(i)
                    entry.timestamp = time.time()
                    self._entries.insert(0, entry)
                    break
            else:
                return
        if self._db:
            self._db.update_timestamp(text=text)

    def promote_image(self, image_path: str) -> None:
        """Move an existing image entry to the top of the history list."""
        with self._lock:
            for i, entry in enumerate(self._entries):
                if entry.image_path == image_path:
                    self._entries.pop(i)
                    entry.timestamp = time.time()
                    self._entries.insert(0, entry)
                    break
            else:
                return
        if self._db:
            self._db.update_timestamp(image_path=image_path)

    def _add_image_entry(
        self, image_data: bytes, image_type: str, source_app: str = ""
    ) -> None:
        """Save image data to disk and add an image clipboard entry."""
        result = self._save_image(image_data, image_type)
        if result is None:
            return

        filename, width, height, file_size = result

        entry = ClipboardEntry(
            text="",
            timestamp=time.time(),
            source_app=source_app,
            image_path=filename,
            image_width=width,
            image_height=height,
            image_size=file_size,
        )

        removed: List[str] = []
        with self._lock:
            # Skip if same as the most recent entry (by filename hash)
            if self._entries and self._entries[0].image_path == filename:
                return
            self._entries.insert(0, entry)
            removed = self._trim_expired_locked()

        if self._db:
            self._db.insert(entry)
            if removed:
                self._db.delete_expired(self._max_days)

        if removed:
            self._cleanup_image_files(removed)
        logger.debug(
            "Clipboard image entry added: %s (%dx%d)", filename, width, height,
        )

    def _save_image(
        self, image_data: bytes, image_type: str
    ) -> Optional[tuple]:
        """Save image data as PNG, return (filename, width, height, size) or None."""
        try:
            from AppKit import NSBitmapImageRep, NSPNGFileType
            from Foundation import NSData

            ns_data = NSData.dataWithBytes_length_(image_data, len(image_data))
            rep = NSBitmapImageRep.imageRepWithData_(ns_data)
            if rep is None:
                return None

            width = int(rep.pixelsWide())
            height = int(rep.pixelsHigh())

            # Convert to PNG if needed
            if image_type == "png":
                png_data = image_data
            else:
                png_ns = rep.representationUsingType_properties_(NSPNGFileType, {})
                if png_ns is None:
                    return None
                png_data = bytes(png_ns)

            # Generate filename: timestamp + hash of full content
            ts = int(time.time())
            content_hash = hashlib.sha256(png_data).hexdigest()[:12]
            filename = f"{ts}_{content_hash}.png"

            os.makedirs(self._image_dir, exist_ok=True)
            filepath = os.path.join(self._image_dir, filename)
            # Avoid overwriting an existing file (hash collision)
            if os.path.isfile(filepath):
                filename = f"{ts}_{content_hash}_1.png"
                filepath = os.path.join(self._image_dir, filename)
            with open(filepath, "wb") as f:
                f.write(png_data)

            return filename, width, height, len(png_data)
        except Exception:
            logger.debug("Failed to save clipboard image", exc_info=True)
            return None

    def _cleanup_image_files(self, filenames: List[str]) -> None:
        """Delete image files that are no longer referenced."""
        for fname in filenames:
            if not fname:
                continue
            path = os.path.join(self._image_dir, fname)
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    logger.debug("Removed old clipboard image: %s", fname)
            except Exception:
                logger.debug("Failed to remove image %s", fname, exc_info=True)

    def _clear_image_dir(self) -> None:
        """Remove all files in the image directory."""
        try:
            if os.path.isdir(self._image_dir):
                for fname in os.listdir(self._image_dir):
                    fpath = os.path.join(self._image_dir, fname)
                    if os.path.isfile(fpath):
                        os.remove(fpath)
        except Exception:
            logger.debug("Failed to clear image directory", exc_info=True)

    def _add_entry(self, text: str, source_app: str = "") -> None:
        """Add a new entry, deduplicating consecutive identical texts."""
        entry = ClipboardEntry(
            text=text,
            timestamp=time.time(),
            source_app=source_app,
        )

        removed: List[str] = []
        with self._lock:
            # Skip if same as the most recent entry
            if self._entries and self._entries[0].text == text:
                return
            self._entries.insert(0, entry)
            removed = self._trim_expired_locked()

        if self._db:
            self._db.insert(entry)
            if removed:
                self._db.delete_expired(self._max_days)

        if removed:
            self._cleanup_image_files(removed)
        logger.debug("Clipboard entry added: %s...", text[:40])

    def _trim_expired_locked(self) -> List[str]:
        """Remove entries older than max_days. Must be called with _lock held.

        Returns a list of image filenames that were removed (for cleanup).
        """
        cutoff = time.time() - self._max_days * 86400
        kept = []
        removed_images: List[str] = []
        for entry in self._entries:
            if entry.timestamp >= cutoff:
                kept.append(entry)
            elif entry.image_path:
                removed_images.append(entry.image_path)
        self._entries = kept
        return removed_images
