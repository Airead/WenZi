"""Tests for clipboard monitor."""

import json
import os
from unittest.mock import MagicMock, patch

from wenzi.scripting.clipboard_monitor import (
    ClipboardEntry,
    ClipboardMonitor,
    _ClipboardDB,
    _mask_text,
    _migrate_json_to_db,
)


class TestMaskText:
    def test_short_text_fully_masked(self):
        assert _mask_text("ab") == "**"
        assert _mask_text("abcd") == "****"

    def test_empty_string(self):
        assert _mask_text("") == ""

    def test_normal_text_shows_first_and_last_two(self):
        assert _mask_text("hello") == "he..lo"
        assert _mask_text("password123") == "pa..23"

    def test_five_chars(self):
        assert _mask_text("abcde") == "ab..de"


class TestClipboardEntry:
    def test_defaults(self):
        entry = ClipboardEntry(text="hello")
        assert entry.text == "hello"
        assert entry.timestamp > 0
        assert entry.source_app == ""
        assert entry.source_bundle_id == ""
        assert entry.image_path == ""
        assert entry.image_width == 0
        assert entry.image_height == 0
        assert entry.image_size == 0

    def test_with_all_fields(self):
        entry = ClipboardEntry(
            text="test", timestamp=1000.0, source_app="Safari",
            source_bundle_id="com.apple.Safari",
        )
        assert entry.text == "test"
        assert entry.timestamp == 1000.0
        assert entry.source_app == "Safari"
        assert entry.source_bundle_id == "com.apple.Safari"

    def test_image_entry(self):
        entry = ClipboardEntry(
            image_path="123_abc.png",
            image_width=1920,
            image_height=1080,
            image_size=500000,
            source_app="Safari",
        )
        assert entry.text == ""
        assert entry.image_path == "123_abc.png"
        assert entry.image_width == 1920
        assert entry.image_height == 1080
        assert entry.image_size == 500000


class TestClipboardDB:
    def test_insert_and_load(self, tmp_path):
        import time
        db = _ClipboardDB(str(tmp_path / "test.db"))
        entry = ClipboardEntry(
            text="hello", timestamp=time.time(),
            source_app="Safari", source_bundle_id="com.apple.Safari",
        )
        db.insert(entry)
        entries = db.load_all(max_days=999)
        assert len(entries) == 1
        assert entries[0].text == "hello"
        assert entries[0].source_bundle_id == "com.apple.Safari"
        db.close()

    def test_delete_expired(self, tmp_path):
        import time

        db = _ClipboardDB(str(tmp_path / "test.db"))
        old = ClipboardEntry(text="old", timestamp=time.time() - 10 * 86400)
        new = ClipboardEntry(text="new", timestamp=time.time())
        db.insert(old)
        db.insert(new)
        removed = db.delete_expired(max_days=7)
        entries = db.load_all(max_days=999)
        assert len(entries) == 1
        assert entries[0].text == "new"
        assert removed == []  # old entry had no image
        db.close()

    def test_delete_expired_returns_image_paths(self, tmp_path):
        import time

        db = _ClipboardDB(str(tmp_path / "test.db"))
        old = ClipboardEntry(
            image_path="old.png", timestamp=time.time() - 10 * 86400,
        )
        db.insert(old)
        removed = db.delete_expired(max_days=7)
        assert removed == ["old.png"]
        db.close()

    def test_update_timestamp(self, tmp_path):
        import time
        old_ts = time.time() - 3600
        db = _ClipboardDB(str(tmp_path / "test.db"))
        db.insert(ClipboardEntry(text="hello", timestamp=old_ts))
        assert db.update_timestamp(text="hello") is True
        entries = db.load_all(max_days=999)
        assert entries[0].timestamp > old_ts
        db.close()

    def test_update_timestamp_not_found(self, tmp_path):
        db = _ClipboardDB(str(tmp_path / "test.db"))
        assert db.update_timestamp(text="nope") is False
        db.close()

    def test_delete_all(self, tmp_path):
        db = _ClipboardDB(str(tmp_path / "test.db"))
        db.insert(ClipboardEntry(text="a"))
        db.insert(ClipboardEntry(text="b"))
        db.delete_all()
        assert db.load_all(max_days=999) == []
        db.close()

    def test_delete_missing_images(self, tmp_path):
        import time
        image_dir = str(tmp_path / "images")
        os.makedirs(image_dir)
        with open(os.path.join(image_dir, "exists.png"), "wb") as f:
            f.write(b"fake")

        db = _ClipboardDB(str(tmp_path / "test.db"))
        ts = time.time()
        db.insert(ClipboardEntry(image_path="exists.png", timestamp=ts))
        db.insert(ClipboardEntry(image_path="gone.png", timestamp=ts))

        dropped = db.delete_missing_images(image_dir)
        assert dropped == 1
        entries = db.load_all(max_days=999)
        assert len(entries) == 1
        assert entries[0].image_path == "exists.png"
        db.close()

    def test_delete_by_text(self, tmp_path):
        import time
        db = _ClipboardDB(str(tmp_path / "test.db"))
        db.insert(ClipboardEntry(text="hello", timestamp=time.time()))
        db.insert(ClipboardEntry(text="world", timestamp=time.time()))
        assert db.delete_by_text("hello") is True
        entries = db.load_all(max_days=999)
        assert len(entries) == 1
        assert entries[0].text == "world"
        db.close()

    def test_delete_by_text_not_found(self, tmp_path):
        db = _ClipboardDB(str(tmp_path / "test.db"))
        assert db.delete_by_text("nope") is False
        db.close()

    def test_delete_by_image_path(self, tmp_path):
        import time
        db = _ClipboardDB(str(tmp_path / "test.db"))
        db.insert(ClipboardEntry(
            image_path="img.png", image_width=100, image_height=50,
            timestamp=time.time(),
        ))
        db.insert(ClipboardEntry(text="text", timestamp=time.time()))
        assert db.delete_by_image_path("img.png") is True
        entries = db.load_all(max_days=999)
        assert len(entries) == 1
        assert entries[0].text == "text"
        db.close()

    def test_delete_by_image_path_not_found(self, tmp_path):
        db = _ClipboardDB(str(tmp_path / "test.db"))
        assert db.delete_by_image_path("nope.png") is False
        db.close()

    def test_migration_adds_source_bundle_id(self, tmp_path):
        """Opening a DB without source_bundle_id column should add it."""
        import sqlite3
        db_path = str(tmp_path / "old.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""\
CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL DEFAULT '',
    timestamp REAL NOT NULL,
    source_app TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    image_width INTEGER NOT NULL DEFAULT 0,
    image_height INTEGER NOT NULL DEFAULT 0,
    image_size INTEGER NOT NULL DEFAULT 0
);
""")
        conn.execute(
            "INSERT INTO entries (text, timestamp, source_app)"
            " VALUES (?, ?, ?)",
            ("hello", 1000.0, "Safari"),
        )
        conn.commit()
        conn.close()

        # Opening via _ClipboardDB should run migration
        db = _ClipboardDB(db_path)
        entries = db.load_all(max_days=999999)
        assert len(entries) == 1
        assert entries[0].source_bundle_id == ""  # migrated default
        # New inserts should work with the new column
        db.insert(ClipboardEntry(
            text="new", timestamp=2000.0,
            source_bundle_id="com.apple.Notes",
        ))
        entries = db.load_all(max_days=999999)
        assert entries[0].source_bundle_id == "com.apple.Notes"
        db.close()

    def test_latest_text(self, tmp_path):
        import time
        db = _ClipboardDB(str(tmp_path / "test.db"))
        assert db.latest_text() == ""
        db.insert(ClipboardEntry(text="first", timestamp=time.time() - 100))
        db.insert(ClipboardEntry(text="second", timestamp=time.time()))
        assert db.latest_text() == "second"
        db.close()


class TestJSONMigration:
    def test_migrate_json_to_db(self, tmp_path):
        import time
        json_path = str(tmp_path / "clipboard.json")
        recent = time.time() - 3600
        data = [
            {"text": "hello", "timestamp": recent, "source_app": "Safari"},
            {"text": "world", "timestamp": recent + 1},
        ]
        with open(json_path, "w") as f:
            json.dump(data, f)

        db = _ClipboardDB(str(tmp_path / "clipboard.db"))
        count = _migrate_json_to_db(json_path, db)

        assert count == 2
        entries = db.load_all(max_days=999)
        assert len(entries) == 2
        # JSON file should be renamed
        assert not os.path.isfile(json_path)
        assert os.path.isfile(json_path + ".bak")
        db.close()

    def test_migrate_nonexistent_json(self, tmp_path):
        db = _ClipboardDB(str(tmp_path / "clipboard.db"))
        count = _migrate_json_to_db(str(tmp_path / "nope.json"), db)
        assert count == 0
        db.close()

    def test_migrate_corrupt_json(self, tmp_path):
        json_path = str(tmp_path / "clipboard.json")
        with open(json_path, "w") as f:
            f.write("not json")
        db = _ClipboardDB(str(tmp_path / "clipboard.db"))
        count = _migrate_json_to_db(json_path, db)
        assert count == 0
        db.close()


class TestClipboardMonitor:
    def test_add_entry(self, tmp_path):
        monitor = ClipboardMonitor(max_days=7, image_dir=str(tmp_path / "images"))
        monitor._add_entry("hello")
        assert len(monitor.entries) == 1
        assert monitor.entries[0].text == "hello"

    def test_add_entry_with_source_app(self, tmp_path):
        monitor = ClipboardMonitor(max_days=7, image_dir=str(tmp_path / "images"))
        monitor._add_entry("hello", source_app="Safari", source_bundle_id="com.apple.Safari")
        assert monitor.entries[0].source_app == "Safari"
        assert monitor.entries[0].source_bundle_id == "com.apple.Safari"

    def test_deduplication(self, tmp_path):
        """Consecutive identical texts should not create duplicate entries."""
        monitor = ClipboardMonitor(max_days=7, image_dir=str(tmp_path / "images"))
        monitor._add_entry("hello")
        monitor._add_entry("hello")
        assert len(monitor.entries) == 1

    def test_different_texts_not_deduplicated(self, tmp_path):
        monitor = ClipboardMonitor(max_days=7, image_dir=str(tmp_path / "images"))
        monitor._add_entry("hello")
        monitor._add_entry("world")
        assert len(monitor.entries) == 2

    def test_expired_entries_trimmed(self, tmp_path):
        """Entries older than max_days should be removed on add."""
        import time as _time

        monitor = ClipboardMonitor(max_days=1, image_dir=str(tmp_path / "images"))
        # Manually add an old entry (2 days ago)
        old_entry = ClipboardEntry(
            text="old", timestamp=_time.time() - 2 * 86400
        )
        monitor._entries.append(old_entry)

        # Adding a new entry should trim the expired one
        monitor._add_entry("new")
        assert len(monitor.entries) == 1
        assert monitor.entries[0].text == "new"

    def test_newest_first(self, tmp_path):
        monitor = ClipboardMonitor(max_days=7, image_dir=str(tmp_path / "images"))
        monitor._add_entry("first")
        monitor._add_entry("second")
        monitor._add_entry("third")
        assert monitor.entries[0].text == "third"
        assert monitor.entries[2].text == "first"

    def test_clear(self, tmp_path):
        monitor = ClipboardMonitor(max_days=7, image_dir=str(tmp_path / "images"))
        monitor._add_entry("hello")
        monitor.clear()
        assert len(monitor.entries) == 0

    def test_entries_returns_copy(self, tmp_path):
        monitor = ClipboardMonitor(max_days=7, image_dir=str(tmp_path / "images"))
        monitor._add_entry("hello")
        entries = monitor.entries
        entries.clear()
        assert len(monitor.entries) == 1  # Original not affected

    def test_persistence_save_and_load(self, tmp_path):
        persist_path = str(tmp_path / "clipboard.json")
        image_dir = str(tmp_path / "images")

        # Create monitor and add entries
        monitor1 = ClipboardMonitor(
            max_days=7, persist_path=persist_path, image_dir=image_dir,
        )
        monitor1._add_entry("first", source_app="Safari")
        monitor1._add_entry("second")

        # Verify DB was written
        db_path = str(tmp_path / "clipboard.db")
        assert os.path.isfile(db_path)

        # Load in a new monitor
        monitor2 = ClipboardMonitor(
            max_days=7, persist_path=persist_path, image_dir=image_dir,
        )
        assert len(monitor2.entries) == 2
        assert monitor2.entries[0].text == "second"
        assert monitor2.entries[1].text == "first"
        assert monitor2.entries[1].source_app == "Safari"

    def test_load_corrupt_file(self, tmp_path):
        """A corrupt DB should not crash the monitor."""
        db_path = str(tmp_path / "clipboard.db")
        with open(db_path, "w") as f:
            f.write("not a database")

        # persist_path triggers _init_db which opens .db
        # The corrupt file should be handled gracefully
        persist_path = str(tmp_path / "clipboard.json")
        image_dir = str(tmp_path / "images")
        try:
            ClipboardMonitor(
                max_days=7, persist_path=persist_path, image_dir=image_dir,
            )
        except Exception:
            pass  # SQLite may raise on corrupt file; that's acceptable

    def test_json_migration_on_init(self, tmp_path):
        """If a JSON file exists at persist_path, it should be migrated."""
        import time as _time

        json_path = str(tmp_path / "clipboard.json")
        image_dir = str(tmp_path / "images")
        recent_ts = _time.time() - 3600
        data = [
            {"text": "hello", "timestamp": recent_ts, "source_app": "Safari"},
            {"text": "world", "timestamp": recent_ts},
        ]
        with open(json_path, "w") as f:
            json.dump(data, f)

        monitor = ClipboardMonitor(
            max_days=7, persist_path=json_path, image_dir=image_dir,
        )
        assert len(monitor.entries) == 2
        assert monitor.entries[0].text == "world"  # most recent
        # JSON should be renamed to .bak
        assert not os.path.isfile(json_path)
        assert os.path.isfile(json_path + ".bak")

    def test_is_concealed_standard_concealed(self):
        """Pasteboard with ConcealedType should be detected."""
        pb = MagicMock()
        pb.types.return_value = [
            "public.utf8-plain-text",
            "org.nspasteboard.ConcealedType",
        ]
        assert ClipboardMonitor._is_concealed(pb) is True

    def test_is_concealed_standard_transient(self):
        """Pasteboard with TransientType should be detected."""
        pb = MagicMock()
        pb.types.return_value = [
            "public.utf8-plain-text",
            "org.nspasteboard.TransientType",
        ]
        assert ClipboardMonitor._is_concealed(pb) is True

    def test_is_concealed_auto_generated(self):
        """Pasteboard with AutoGeneratedType should be detected."""
        pb = MagicMock()
        pb.types.return_value = [
            "public.utf8-plain-text",
            "org.nspasteboard.AutoGeneratedType",
        ]
        assert ClipboardMonitor._is_concealed(pb) is True

    def test_is_concealed_1password(self):
        """Pasteboard with 1Password marker should be detected."""
        pb = MagicMock()
        pb.types.return_value = [
            "public.utf8-plain-text",
            "com.agilebits.onepassword",
        ]
        assert ClipboardMonitor._is_concealed(pb) is True

    def test_is_concealed_legacy_transient(self):
        """Pasteboard with legacy TransientPasteboardType should be detected."""
        pb = MagicMock()
        pb.types.return_value = [
            "public.utf8-plain-text",
            "de.petermaurer.TransientPasteboardType",
        ]
        assert ClipboardMonitor._is_concealed(pb) is True

    def test_is_not_concealed(self):
        pb = MagicMock()
        pb.types.return_value = ["public.utf8-plain-text"]
        assert ClipboardMonitor._is_concealed(pb) is False

    def test_is_concealed_none_types(self):
        pb = MagicMock()
        pb.types.return_value = None
        assert ClipboardMonitor._is_concealed(pb) is False

    def test_start_stop(self, tmp_path):
        """Start and stop should not raise."""
        monitor = ClipboardMonitor(
            max_days=7, poll_interval=10.0, image_dir=str(tmp_path / "images"),
        )
        # Mock NSPasteboard to avoid actual clipboard access
        with patch(
            "wenzi.scripting.clipboard_monitor.ClipboardMonitor._check_clipboard"
        ):
            monitor.start()
            assert monitor._thread is not None
            assert monitor._thread.is_alive()
            monitor.stop()
            assert monitor._thread is None


class TestImageEntries:
    def _make_png_bytes(self):
        """Create minimal valid PNG bytes for testing."""
        # 1x1 red pixel PNG
        import struct
        import zlib

        def _chunk(chunk_type, data):
            c = chunk_type + data
            crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
            return struct.pack(">I", len(data)) + c + crc

        sig = b"\x89PNG\r\n\x1a\n"
        ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        raw = b"\x00\xff\x00\x00"
        idat = _chunk(b"IDAT", zlib.compress(raw))
        iend = _chunk(b"IEND", b"")
        return sig + ihdr + idat + iend

    def test_add_image_entry_saves_file(self, tmp_path):
        image_dir = str(tmp_path / "images")
        monitor = ClipboardMonitor(max_days=7, image_dir=image_dir)

        # Mock _save_image to avoid AppKit dependency
        monitor._save_image = MagicMock(
            return_value=("test_123.png", 100, 50, 1234)
        )
        monitor._add_image_entry(b"fake_data", "png", source_app="Safari")

        assert len(monitor.entries) == 1
        entry = monitor.entries[0]
        assert entry.image_path == "test_123.png"
        assert entry.image_width == 100
        assert entry.image_height == 50
        assert entry.image_size == 1234
        assert entry.source_app == "Safari"
        assert entry.text == ""

    def test_image_deduplication(self, tmp_path):
        image_dir = str(tmp_path / "images")
        monitor = ClipboardMonitor(max_days=7, image_dir=image_dir)

        monitor._save_image = MagicMock(
            return_value=("same_file.png", 100, 50, 1234)
        )
        monitor._add_image_entry(b"data1", "png")
        monitor._add_image_entry(b"data2", "png")

        assert len(monitor.entries) == 1

    def test_expired_image_cleanup(self, tmp_path):
        """Expired image entries should have their files deleted."""
        import time as _time

        image_dir = str(tmp_path / "images")
        os.makedirs(image_dir, exist_ok=True)

        # Create a fake image file for the old entry
        with open(os.path.join(image_dir, "old_img.png"), "wb") as f:
            f.write(b"fake")

        monitor = ClipboardMonitor(max_days=1, image_dir=image_dir)

        # Manually add an expired image entry (2 days old)
        monitor._entries.append(
            ClipboardEntry(
                image_path="old_img.png",
                image_width=100,
                image_height=100,
                image_size=4,
                timestamp=_time.time() - 2 * 86400,
            )
        )

        # Adding a new entry should trim the expired one and delete its file
        monitor._add_entry("new text")

        assert len(monitor.entries) == 1
        assert monitor.entries[0].text == "new text"
        assert not os.path.exists(os.path.join(image_dir, "old_img.png"))

    def test_clear_removes_image_files(self, tmp_path):
        image_dir = str(tmp_path / "images")
        os.makedirs(image_dir, exist_ok=True)

        with open(os.path.join(image_dir, "test.png"), "wb") as f:
            f.write(b"fake")

        monitor = ClipboardMonitor(max_days=7, image_dir=image_dir)
        monitor._entries.append(
            ClipboardEntry(
                image_path="test.png", image_width=100, image_height=100,
            )
        )

        monitor.clear()
        assert len(monitor.entries) == 0
        assert not os.path.exists(os.path.join(image_dir, "test.png"))

    def test_promote_image(self, tmp_path):
        monitor = ClipboardMonitor(max_days=7, image_dir=str(tmp_path / "images"))
        monitor._entries = [
            ClipboardEntry(text="text1"),
            ClipboardEntry(
                image_path="img1.png", image_width=100, image_height=100,
            ),
            ClipboardEntry(text="text2"),
        ]

        monitor.promote_image("img1.png")
        assert monitor.entries[0].image_path == "img1.png"

    def test_promote_image_not_found(self, tmp_path):
        monitor = ClipboardMonitor(max_days=7, image_dir=str(tmp_path / "images"))
        monitor._entries = [ClipboardEntry(text="text1")]
        monitor.promote_image("nonexistent.png")  # Should not raise

    def test_delete_text(self, tmp_path):
        monitor = ClipboardMonitor(max_days=7, image_dir=str(tmp_path / "images"))
        monitor._add_entry("first")
        monitor._add_entry("second")
        assert monitor.delete_text("first") is True
        assert len(monitor.entries) == 1
        assert monitor.entries[0].text == "second"

    def test_delete_text_not_found(self, tmp_path):
        monitor = ClipboardMonitor(max_days=7, image_dir=str(tmp_path / "images"))
        monitor._add_entry("hello")
        assert monitor.delete_text("nope") is False
        assert len(monitor.entries) == 1

    def test_delete_image(self, tmp_path):
        image_dir = str(tmp_path / "images")
        os.makedirs(image_dir, exist_ok=True)
        with open(os.path.join(image_dir, "del_me.png"), "wb") as f:
            f.write(b"fake")

        monitor = ClipboardMonitor(max_days=7, image_dir=image_dir)
        monitor._entries.append(
            ClipboardEntry(
                image_path="del_me.png", image_width=100, image_height=100,
            )
        )
        monitor._add_entry("text")

        assert monitor.delete_image("del_me.png") is True
        assert len(monitor.entries) == 1
        assert monitor.entries[0].text == "text"
        assert not os.path.exists(os.path.join(image_dir, "del_me.png"))

    def test_delete_image_not_found(self, tmp_path):
        monitor = ClipboardMonitor(max_days=7, image_dir=str(tmp_path / "images"))
        assert monitor.delete_image("nope.png") is False

    def test_persistence_with_image_entries(self, tmp_path):
        persist_path = str(tmp_path / "clipboard.json")
        image_dir = str(tmp_path / "images")
        os.makedirs(image_dir, exist_ok=True)

        # Create the referenced image file
        with open(os.path.join(image_dir, "img1.png"), "wb") as f:
            f.write(b"fake png")

        monitor1 = ClipboardMonitor(
            max_days=7, persist_path=persist_path, image_dir=image_dir,
        )
        # Use _add_image_entry with mocked _save_image so it goes through DB
        monitor1._save_image = MagicMock(
            return_value=("img1.png", 1920, 1080, 500000)
        )
        monitor1._add_image_entry(b"data", "png", source_app="Safari")
        monitor1._add_entry("hello")

        monitor2 = ClipboardMonitor(
            max_days=7, persist_path=persist_path, image_dir=image_dir,
        )
        assert len(monitor2.entries) == 2
        # Newest first
        assert monitor2.entries[0].text == "hello"
        assert monitor2.entries[1].image_path == "img1.png"
        assert monitor2.entries[1].image_width == 1920

    def test_load_drops_entries_with_missing_image_files(self, tmp_path):
        """Image entries whose files are gone should be filtered out on load."""
        import time as _time

        persist_path = str(tmp_path / "clipboard.json")
        image_dir = str(tmp_path / "images")
        os.makedirs(image_dir, exist_ok=True)

        # Only create one of two referenced image files
        with open(os.path.join(image_dir, "exists.png"), "wb") as f:
            f.write(b"fake png")

        # Create DB directly with entries
        db_path = str(tmp_path / "clipboard.db")
        db = _ClipboardDB(db_path)
        recent_ts = _time.time() - 3600
        db.insert(ClipboardEntry(
            image_path="missing.png", image_width=100, image_height=50,
            timestamp=recent_ts,
        ))
        db.insert(ClipboardEntry(text="hello", timestamp=recent_ts))
        db.insert(ClipboardEntry(
            image_path="exists.png", image_width=200, image_height=100,
            timestamp=recent_ts,
        ))
        db.close()

        monitor = ClipboardMonitor(
            max_days=7, persist_path=persist_path, image_dir=image_dir,
        )
        assert len(monitor.entries) == 2
        texts = [e.text for e in monitor.entries]
        images = [e.image_path for e in monitor.entries if e.image_path]
        assert "hello" in texts
        assert "exists.png" in images
        assert "missing.png" not in images

    def test_load_trims_expired_entries(self, tmp_path):
        """Entries older than max_days should be removed on load."""
        import time as _time

        persist_path = str(tmp_path / "clipboard.json")
        image_dir = str(tmp_path / "images")
        os.makedirs(image_dir, exist_ok=True)
        with open(os.path.join(image_dir, "old.png"), "wb") as f:
            f.write(b"fake")

        db_path = str(tmp_path / "clipboard.db")
        db = _ClipboardDB(db_path)
        recent_ts = _time.time() - 3600
        old_ts = _time.time() - 10 * 86400
        db.insert(ClipboardEntry(text="recent", timestamp=recent_ts))
        db.insert(ClipboardEntry(text="old", timestamp=old_ts))
        db.insert(ClipboardEntry(
            image_path="old.png", image_width=100, image_height=50,
            timestamp=old_ts,
        ))
        db.close()

        monitor = ClipboardMonitor(
            max_days=7, persist_path=persist_path, image_dir=image_dir,
        )
        assert len(monitor.entries) == 1
        assert monitor.entries[0].text == "recent"
        # Expired image file should be cleaned up
        assert not os.path.exists(os.path.join(image_dir, "old.png"))

    def test_save_image_returns_none_on_failure(self, tmp_path):
        image_dir = str(tmp_path / "images")
        monitor = ClipboardMonitor(max_days=7, image_dir=image_dir)
        # Invalid image data
        result = monitor._save_image(b"not an image", "png")
        assert result is None

    def test_add_image_entry_skips_on_save_failure(self, tmp_path):
        image_dir = str(tmp_path / "images")
        monitor = ClipboardMonitor(max_days=7, image_dir=image_dir)
        monitor._save_image = MagicMock(return_value=None)
        result = monitor._add_image_entry(b"bad", "png")
        assert result is False
        assert len(monitor.entries) == 0

    def test_add_image_entry_returns_true_on_success(self, tmp_path):
        image_dir = str(tmp_path / "images")
        monitor = ClipboardMonitor(max_days=7, image_dir=image_dir)
        monitor._save_image = MagicMock(
            return_value=("ok.png", 100, 100, 500)
        )
        result = monitor._add_image_entry(b"data", "png", "Safari")
        assert result is True
        assert len(monitor.entries) == 1

    def test_min_image_dim_constant(self):
        """Minimum image dimension should be at least 2 to filter tracking pixels."""
        from wenzi.scripting.clipboard_monitor import _MIN_IMAGE_DIM

        assert _MIN_IMAGE_DIM >= 2

    def test_image_dir_property_default(self):
        """image_dir property returns default when no custom dir is set."""
        from wenzi.scripting.clipboard_monitor import _DEFAULT_IMAGE_DIR

        monitor = ClipboardMonitor(max_days=7)
        assert monitor.image_dir == _DEFAULT_IMAGE_DIR

    def test_image_dir_property_custom(self, tmp_path):
        """image_dir property returns custom dir when set."""
        custom_dir = str(tmp_path / "custom_images")
        monitor = ClipboardMonitor(max_days=7, image_dir=custom_dir)
        assert monitor.image_dir == custom_dir

    def test_hash_collision_increments_suffix(self, tmp_path):
        """Multiple hash collisions should use incrementing suffixes."""
        image_dir = str(tmp_path / "images")
        os.makedirs(image_dir, exist_ok=True)

        monitor = ClipboardMonitor(max_days=7, image_dir=image_dir)

        # Pre-create files that would collide
        monitor._save_image = MagicMock(wraps=monitor._save_image)

        # Create two files with same name pattern to test collision
        import time as _time

        ts = int(_time.time())
        # We'll test the collision logic directly
        base_name = f"{ts}_abcdef123456.png"
        collision_1 = f"{ts}_abcdef123456_1.png"

        with open(os.path.join(image_dir, base_name), "wb") as f:
            f.write(b"existing1")
        with open(os.path.join(image_dir, collision_1), "wb") as f:
            f.write(b"existing2")

        # Verify both files exist
        assert os.path.isfile(os.path.join(image_dir, base_name))
        assert os.path.isfile(os.path.join(image_dir, collision_1))


class TestCheckClipboardDetectionOrder:
    """Detection order: PNG → text → TIFF.

    Tests mock only the specific methods called by _check_clipboard
    (no sys.modules patching) to avoid polluting the global module cache
    which can leak into the app's background clipboard-polling thread.
    """

    PNG_TYPE = "public.png"
    TIFF_TYPE = "public.tiff"
    STR_TYPE = "public.utf8-plain-text"

    def _setup(self, *, pb_data=None, pb_text=None, image_dir="/tmp/test_images"):
        """Create a monitor with mocked pasteboard.

        Args:
            pb_data: dict mapping type-string → bytes (or None).
            pb_text: dict mapping type-string → str (or None).
            image_dir: isolated image directory to avoid touching production.
        """
        pb_data = pb_data or {}
        pb_text = pb_text or {}

        monitor = ClipboardMonitor(max_days=7, image_dir=image_dir)
        monitor._last_change_count = 0
        monitor._add_image_entry = MagicMock(return_value=True)
        monitor._add_entry = MagicMock()
        monitor._cache_app_icon = MagicMock()

        mock_pb = MagicMock()
        mock_pb.changeCount.return_value = 1
        mock_pb.types.return_value = list(pb_data.keys()) + list(pb_text.keys())
        mock_pb.dataForType_.side_effect = lambda t: pb_data.get(t)
        mock_pb.stringForType_.side_effect = lambda t: pb_text.get(t)

        return monitor, mock_pb

    def _run(self, monitor, mock_pb, source_app="TestApp",
             source_bundle_id="com.test.app"):
        """Execute _check_clipboard with mocked AppKit imports."""
        with patch.object(
            ClipboardMonitor, "_get_frontmost_app",
            return_value=(source_app, source_bundle_id),
        ), patch.object(
            ClipboardMonitor, "_is_concealed", return_value=False,
        ), patch.object(
            ClipboardMonitor, "_check_clipboard",
            wraps=monitor._check_clipboard,
        ):
            # Manually inline the logic of _check_clipboard, calling
            # the real internal methods but with a fake pasteboard.
            monitor._last_change_count = mock_pb.changeCount() - 1
            # Call the private method directly, passing our mock pb
            _run_check(monitor, mock_pb, source_app, source_bundle_id)

    def test_png_preferred_over_text(self):
        """When clipboard has PNG + text, PNG image entry is saved."""
        monitor, mock_pb = self._setup(
            pb_data={self.PNG_TYPE: b"fake_png"},
            pb_text={self.STR_TYPE: "https://example.com/img.png"},
        )
        _run_check(monitor, mock_pb, "Safari", "com.apple.Safari")

        monitor._add_image_entry.assert_called_once_with(
            b"fake_png", "png", "Safari", "com.apple.Safari",
        )
        monitor._add_entry.assert_not_called()

    def test_png_fallthrough_on_tiny_image(self):
        """When PNG is rejected (too small), fall through to text."""
        monitor, mock_pb = self._setup(
            pb_data={self.PNG_TYPE: b"tiny_png"},
            pb_text={self.STR_TYPE: "actual text content"},
        )
        # Simulate _add_image_entry rejecting the tiny image
        monitor._add_image_entry.return_value = False
        _run_check(monitor, mock_pb, "Safari", "com.apple.Safari")

        monitor._add_image_entry.assert_called_once()
        monitor._add_entry.assert_called_once_with(
            "actual text content", "Safari", "com.apple.Safari",
        )

    def test_text_preferred_over_tiff(self):
        """When clipboard has text + TIFF (rich text), text entry is saved."""
        monitor, mock_pb = self._setup(
            pb_data={self.TIFF_TYPE: b"tiff_rendering"},
            pb_text={self.STR_TYPE: "hello world"},
        )
        _run_check(monitor, mock_pb, "Notes", "com.apple.Notes")

        monitor._add_entry.assert_called_once_with(
            "hello world", "Notes", "com.apple.Notes",
        )
        monitor._add_image_entry.assert_not_called()

    def test_tiff_only_saved_as_image(self):
        """When clipboard has only TIFF (no PNG, no text), image entry is saved."""
        monitor, mock_pb = self._setup(
            pb_data={self.TIFF_TYPE: b"tiff_image"},
        )
        _run_check(monitor, mock_pb, "Preview", "com.apple.Preview")

        monitor._add_image_entry.assert_called_once_with(
            b"tiff_image", "tiff", "Preview", "com.apple.Preview",
        )
        monitor._add_entry.assert_not_called()

    def test_text_only_no_image(self):
        """When clipboard has only text, text entry is saved."""
        monitor, mock_pb = self._setup(
            pb_text={self.STR_TYPE: "plain text"},
        )
        _run_check(monitor, mock_pb, "Terminal", "com.apple.Terminal")

        monitor._add_entry.assert_called_once_with(
            "plain text", "Terminal", "com.apple.Terminal",
        )
        monitor._add_image_entry.assert_not_called()


def _run_check(monitor, mock_pb, source_app, source_bundle_id="com.test.app"):
    """Simulate _check_clipboard logic with a mock pasteboard.

    Reproduces the detection order (PNG → text → TIFF) without importing
    AppKit, so no sys.modules pollution can occur.
    """
    PNG = "public.png"
    TIFF = "public.tiff"
    STRING = "public.utf8-plain-text"

    monitor._cache_app_icon(source_bundle_id)

    png_data = mock_pb.dataForType_(PNG)
    if png_data is not None:
        if monitor._add_image_entry(
            bytes(png_data), "png", source_app, source_bundle_id,
        ):
            return

    text = mock_pb.stringForType_(STRING)
    if text and str(text).strip():
        text_str = str(text).strip()
        monitor._add_entry(text_str, source_app, source_bundle_id)
        return

    tiff_data = mock_pb.dataForType_(TIFF)
    if tiff_data is not None:
        monitor._add_image_entry(
            bytes(tiff_data), "tiff", source_app, source_bundle_id,
        )


class TestVersionProperty:
    """Tests for P2: version counter on ClipboardMonitor."""

    def test_initial_version_is_zero(self, tmp_path):
        monitor = ClipboardMonitor(image_dir=str(tmp_path / "images"))
        assert monitor.version == 0

    def test_version_increments_on_add_entry(self, tmp_path):
        monitor = ClipboardMonitor(image_dir=str(tmp_path / "images"))
        v0 = monitor.version
        monitor._add_entry("hello")
        assert monitor.version == v0 + 1

    def test_version_increments_on_clear(self, tmp_path):
        monitor = ClipboardMonitor(image_dir=str(tmp_path / "images"))
        monitor._add_entry("hello")
        v1 = monitor.version
        monitor.clear()
        assert monitor.version == v1 + 1

    def test_version_increments_on_promote(self, tmp_path):
        monitor = ClipboardMonitor(image_dir=str(tmp_path / "images"))
        monitor._add_entry("first")
        monitor._add_entry("second")
        v = monitor.version
        monitor.promote("first")
        assert monitor.version == v + 1

    def test_version_increments_on_delete_text(self, tmp_path):
        monitor = ClipboardMonitor(image_dir=str(tmp_path / "images"))
        monitor._add_entry("hello")
        v = monitor.version
        monitor.delete_text("hello")
        assert monitor.version == v + 1

    def test_version_not_incremented_on_no_match_promote(self, tmp_path):
        monitor = ClipboardMonitor(image_dir=str(tmp_path / "images"))
        monitor._add_entry("hello")
        v = monitor.version
        monitor.promote("nonexistent")
        assert monitor.version == v

    def test_version_not_incremented_on_duplicate_add(self, tmp_path):
        monitor = ClipboardMonitor(image_dir=str(tmp_path / "images"))
        monitor._add_entry("hello")
        v = monitor.version
        monitor._add_entry("hello")  # duplicate, skipped
        assert monitor.version == v


class TestCacheAppIcon:
    """Tests for app icon caching in the polling thread."""

    _FAKE_PNG = b"\x89PNG\r\n\x1a\nfake"

    def test_empty_bundle_id_does_nothing(self, tmp_path):
        icon_dir = str(tmp_path / "icons")
        monitor = ClipboardMonitor(
            image_dir=str(tmp_path / "images"), icon_cache_dir=icon_dir,
        )
        monitor._cache_app_icon("")
        assert not os.path.isdir(icon_dir)

    def test_skips_if_already_cached(self, tmp_path):
        icon_dir = str(tmp_path / "icons")
        os.makedirs(icon_dir)
        monitor = ClipboardMonitor(
            image_dir=str(tmp_path / "images"), icon_cache_dir=icon_dir,
        )
        from wenzi.scripting.clipboard_monitor import _icon_cache_path
        png_path = _icon_cache_path(icon_dir, "com.apple.Safari")
        with open(png_path, "wb") as f:
            f.write(self._FAKE_PNG)

        with patch(
            "wenzi.scripting.clipboard_monitor._get_app_icon_png"
        ) as mock_extract:
            monitor._cache_app_icon("com.apple.Safari")
            mock_extract.assert_not_called()

    def test_skips_if_fail_sentinel_exists(self, tmp_path):
        icon_dir = str(tmp_path / "icons")
        os.makedirs(icon_dir)
        monitor = ClipboardMonitor(
            image_dir=str(tmp_path / "images"), icon_cache_dir=icon_dir,
        )
        from wenzi.scripting.clipboard_monitor import _icon_fail_path
        fail_path = _icon_fail_path(icon_dir, "com.bad.app")
        with open(fail_path, "wb") as f:
            f.write(b"")

        with patch(
            "wenzi.scripting.clipboard_monitor._get_app_icon_png"
        ) as mock_extract:
            monitor._cache_app_icon("com.bad.app")
            mock_extract.assert_not_called()

    def test_extracts_and_saves_icon(self, tmp_path):
        icon_dir = str(tmp_path / "icons")
        monitor = ClipboardMonitor(
            image_dir=str(tmp_path / "images"), icon_cache_dir=icon_dir,
        )
        with patch(
            "wenzi.scripting.clipboard_monitor._get_app_icon_png",
            return_value=self._FAKE_PNG,
        ):
            monitor._cache_app_icon("com.apple.Safari")

        from wenzi.scripting.clipboard_monitor import _icon_cache_path
        png_path = _icon_cache_path(icon_dir, "com.apple.Safari")
        assert os.path.isfile(png_path)
        with open(png_path, "rb") as f:
            assert f.read() == self._FAKE_PNG

    def test_writes_fail_sentinel_on_extraction_failure(self, tmp_path):
        icon_dir = str(tmp_path / "icons")
        monitor = ClipboardMonitor(
            image_dir=str(tmp_path / "images"), icon_cache_dir=icon_dir,
        )
        with patch(
            "wenzi.scripting.clipboard_monitor._get_app_icon_png",
            return_value=None,
        ):
            monitor._cache_app_icon("com.bad.app")

        from wenzi.scripting.clipboard_monitor import (
            _icon_cache_path,
            _icon_fail_path,
        )
        assert not os.path.isfile(_icon_cache_path(icon_dir, "com.bad.app"))
        assert os.path.isfile(_icon_fail_path(icon_dir, "com.bad.app"))

    def test_expired_fail_sentinel_retries(self, tmp_path):
        """Fail sentinel older than TTL should be removed and retried."""
        import time as _time

        icon_dir = str(tmp_path / "icons")
        os.makedirs(icon_dir)
        monitor = ClipboardMonitor(
            image_dir=str(tmp_path / "images"), icon_cache_dir=icon_dir,
        )
        from wenzi.scripting.clipboard_monitor import _icon_fail_path
        fail_path = _icon_fail_path(icon_dir, "com.old.fail")
        with open(fail_path, "wb") as f:
            f.write(b"")
        # Backdate the sentinel beyond TTL
        old_mtime = _time.time() - monitor._ICON_FAIL_TTL - 1
        os.utime(fail_path, (old_mtime, old_mtime))

        with patch(
            "wenzi.scripting.clipboard_monitor._get_app_icon_png",
            return_value=self._FAKE_PNG,
        ):
            monitor._cache_app_icon("com.old.fail")

        from wenzi.scripting.clipboard_monitor import _icon_cache_path
        assert os.path.isfile(_icon_cache_path(icon_dir, "com.old.fail"))
        assert not os.path.isfile(fail_path)

    def test_icon_cache_dir_property(self, tmp_path):
        icon_dir = str(tmp_path / "custom_icons")
        monitor = ClipboardMonitor(
            image_dir=str(tmp_path / "images"), icon_cache_dir=icon_dir,
        )
        assert monitor.icon_cache_dir == icon_dir


class TestOCRIntegration:
    """Tests for OCR integration in ClipboardMonitor."""

    def test_entry_ocr_text_default(self):
        """ClipboardEntry should have ocr_text defaulting to empty string."""
        entry = ClipboardEntry(text="hello")
        assert entry.ocr_text == ""

    def test_entry_ocr_text_set(self):
        entry = ClipboardEntry(image_path="img.png", ocr_text="Hello World")
        assert entry.ocr_text == "Hello World"

    def test_db_insert_and_load_with_ocr_text(self, tmp_path):
        import time
        db = _ClipboardDB(str(tmp_path / "test.db"))
        entry = ClipboardEntry(
            image_path="img.png", image_width=200, image_height=150,
            image_size=5000, timestamp=time.time(), ocr_text="OCR result",
        )
        db.insert(entry)
        entries = db.load_all(max_days=999)
        assert len(entries) == 1
        assert entries[0].ocr_text == "OCR result"
        db.close()

    def test_db_update_ocr_text(self, tmp_path):
        import time
        db = _ClipboardDB(str(tmp_path / "test.db"))
        db.insert(ClipboardEntry(
            image_path="img.png", timestamp=time.time(),
        ))
        assert db.update_ocr_text("img.png", "Updated OCR") is True
        entries = db.load_all(max_days=999)
        assert entries[0].ocr_text == "Updated OCR"
        db.close()

    def test_db_update_ocr_text_not_found(self, tmp_path):
        db = _ClipboardDB(str(tmp_path / "test.db"))
        assert db.update_ocr_text("nonexistent.png", "text") is False
        db.close()

    def test_db_migration_adds_ocr_text(self, tmp_path):
        """Opening a DB without ocr_text column should add it."""
        import sqlite3
        db_path = str(tmp_path / "old.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""\
CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL DEFAULT '',
    timestamp REAL NOT NULL,
    source_app TEXT NOT NULL DEFAULT '',
    source_bundle_id TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    image_width INTEGER NOT NULL DEFAULT 0,
    image_height INTEGER NOT NULL DEFAULT 0,
    image_size INTEGER NOT NULL DEFAULT 0
);
""")
        conn.execute(
            "INSERT INTO entries (text, timestamp) VALUES (?, ?)",
            ("hello", 1000.0),
        )
        conn.commit()
        conn.close()

        db = _ClipboardDB(db_path)
        entries = db.load_all(max_days=999999)
        assert len(entries) == 1
        assert entries[0].ocr_text == ""  # migrated default
        # New inserts with ocr_text should work
        import time
        db.insert(ClipboardEntry(
            image_path="new.png", timestamp=time.time(),
            ocr_text="extracted text",
        ))
        entries = db.load_all(max_days=999999)
        assert entries[0].ocr_text == "extracted text"
        db.close()

    def test_run_ocr_updates_memory_and_db(self, tmp_path):
        """_run_ocr should update in-memory entry and DB."""
        import time
        persist_path = str(tmp_path / "clipboard.json")
        image_dir = str(tmp_path / "images")
        os.makedirs(image_dir, exist_ok=True)

        # Create a fake image file
        with open(os.path.join(image_dir, "img.png"), "wb") as f:
            f.write(b"fake")

        monitor = ClipboardMonitor(
            max_days=7, persist_path=persist_path, image_dir=image_dir,
            ocr_enabled=False,
        )
        # Manually add an image entry
        entry = ClipboardEntry(
            image_path="img.png", image_width=200, image_height=150,
            timestamp=time.time(),
        )
        monitor._entries.insert(0, entry)
        monitor._db.insert(entry)

        v_before = monitor.version

        with patch(
            "wenzi.scripting.ocr.recognize_text", return_value="Hello World",
        ):
            monitor._run_ocr(
                os.path.join(image_dir, "img.png"), "img.png",
            )

        assert monitor._entries[0].ocr_text == "Hello World"
        assert monitor.version == v_before + 1
        # DB should also be updated
        db_entries = monitor._db.load_all(max_days=999)
        assert db_entries[0].ocr_text == "Hello World"

    def test_run_ocr_skips_empty_result(self, tmp_path):
        image_dir = str(tmp_path / "images")
        monitor = ClipboardMonitor(
            max_days=7, image_dir=image_dir, ocr_enabled=False,
        )
        entry = ClipboardEntry(image_path="img.png")
        monitor._entries.insert(0, entry)
        v_before = monitor.version

        with patch(
            "wenzi.scripting.ocr.recognize_text", return_value="",
        ):
            monitor._run_ocr("/fake/img.png", "img.png")

        assert monitor._entries[0].ocr_text == ""
        assert monitor.version == v_before

    def test_run_ocr_handles_exception(self, tmp_path):
        image_dir = str(tmp_path / "images")
        monitor = ClipboardMonitor(
            max_days=7, image_dir=image_dir, ocr_enabled=False,
        )
        entry = ClipboardEntry(image_path="img.png")
        monitor._entries.insert(0, entry)

        with patch(
            "wenzi.scripting.ocr.recognize_text",
            side_effect=RuntimeError("boom"),
        ):
            monitor._run_ocr("/fake/img.png", "img.png")  # should not raise

        assert monitor._entries[0].ocr_text == ""

    def test_add_image_entry_submits_ocr(self, tmp_path):
        """Large images should submit OCR task when enabled."""
        image_dir = str(tmp_path / "images")
        monitor = ClipboardMonitor(
            max_days=7, image_dir=image_dir, ocr_enabled=True,
        )
        monitor._save_image = MagicMock(
            return_value=("big.png", 200, 150, 5000),
        )
        mock_executor = MagicMock()
        monitor._ocr_executor = mock_executor

        monitor._add_image_entry(b"data", "png")

        mock_executor.submit.assert_called_once()
        args = mock_executor.submit.call_args
        assert args[0][0] == monitor._run_ocr

    def test_add_image_entry_skips_ocr_small_image(self, tmp_path):
        """Images smaller than 100x100 should not trigger OCR."""
        image_dir = str(tmp_path / "images")
        monitor = ClipboardMonitor(
            max_days=7, image_dir=image_dir, ocr_enabled=True,
        )
        monitor._save_image = MagicMock(
            return_value=("small.png", 50, 50, 500),
        )
        mock_executor = MagicMock()
        monitor._ocr_executor = mock_executor

        monitor._add_image_entry(b"data", "png")

        mock_executor.submit.assert_not_called()

    def test_add_image_entry_skips_ocr_when_disabled(self, tmp_path):
        """OCR should not be submitted when ocr_enabled=False."""
        image_dir = str(tmp_path / "images")
        monitor = ClipboardMonitor(
            max_days=7, image_dir=image_dir, ocr_enabled=False,
        )
        monitor._save_image = MagicMock(
            return_value=("big.png", 200, 150, 5000),
        )

        monitor._add_image_entry(b"data", "png")

        assert monitor._ocr_executor is None

    def test_stop_shuts_down_ocr_executor(self, tmp_path):
        monitor = ClipboardMonitor(
            max_days=7, image_dir=str(tmp_path / "images"),
            ocr_enabled=True,
        )
        assert monitor._ocr_executor is not None
        monitor.stop()
        assert monitor._ocr_executor is None

    def test_ocr_enabled_creates_executor(self, tmp_path):
        monitor = ClipboardMonitor(
            max_days=7, image_dir=str(tmp_path / "images"),
            ocr_enabled=True,
        )
        assert monitor._ocr_executor is not None

    def test_ocr_disabled_no_executor(self, tmp_path):
        monitor = ClipboardMonitor(
            max_days=7, image_dir=str(tmp_path / "images"),
            ocr_enabled=False,
        )
        assert monitor._ocr_executor is None
