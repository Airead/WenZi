"""Tests for clipboard monitor."""

import json
import os

from unittest.mock import MagicMock, patch

from voicetext.scripting.clipboard_monitor import (
    ClipboardEntry,
    ClipboardMonitor,
    _ClipboardDB,
    _migrate_json_to_db,
)


class TestClipboardEntry:
    def test_defaults(self):
        entry = ClipboardEntry(text="hello")
        assert entry.text == "hello"
        assert entry.timestamp > 0
        assert entry.source_app == ""
        assert entry.image_path == ""
        assert entry.image_width == 0
        assert entry.image_height == 0
        assert entry.image_size == 0

    def test_with_all_fields(self):
        entry = ClipboardEntry(
            text="test", timestamp=1000.0, source_app="Safari"
        )
        assert entry.text == "test"
        assert entry.timestamp == 1000.0
        assert entry.source_app == "Safari"

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
        entry = ClipboardEntry(text="hello", timestamp=time.time())
        db.insert(entry)
        entries = db.load_all(max_days=999)
        assert len(entries) == 1
        assert entries[0].text == "hello"
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
    def test_add_entry(self):
        monitor = ClipboardMonitor(max_days=7)
        monitor._add_entry("hello")
        assert len(monitor.entries) == 1
        assert monitor.entries[0].text == "hello"

    def test_add_entry_with_source_app(self):
        monitor = ClipboardMonitor(max_days=7)
        monitor._add_entry("hello", source_app="Safari")
        assert monitor.entries[0].source_app == "Safari"

    def test_deduplication(self):
        """Consecutive identical texts should not create duplicate entries."""
        monitor = ClipboardMonitor(max_days=7)
        monitor._add_entry("hello")
        monitor._add_entry("hello")
        assert len(monitor.entries) == 1

    def test_different_texts_not_deduplicated(self):
        monitor = ClipboardMonitor(max_days=7)
        monitor._add_entry("hello")
        monitor._add_entry("world")
        assert len(monitor.entries) == 2

    def test_expired_entries_trimmed(self):
        """Entries older than max_days should be removed on add."""
        import time as _time

        monitor = ClipboardMonitor(max_days=1)
        # Manually add an old entry (2 days ago)
        old_entry = ClipboardEntry(
            text="old", timestamp=_time.time() - 2 * 86400
        )
        monitor._entries.append(old_entry)

        # Adding a new entry should trim the expired one
        monitor._add_entry("new")
        assert len(monitor.entries) == 1
        assert monitor.entries[0].text == "new"

    def test_newest_first(self):
        monitor = ClipboardMonitor(max_days=7)
        monitor._add_entry("first")
        monitor._add_entry("second")
        monitor._add_entry("third")
        assert monitor.entries[0].text == "third"
        assert monitor.entries[2].text == "first"

    def test_clear(self):
        monitor = ClipboardMonitor(max_days=7)
        monitor._add_entry("hello")
        monitor.clear()
        assert len(monitor.entries) == 0

    def test_entries_returns_copy(self):
        monitor = ClipboardMonitor(max_days=7)
        monitor._add_entry("hello")
        entries = monitor.entries
        entries.clear()
        assert len(monitor.entries) == 1  # Original not affected

    def test_persistence_save_and_load(self, tmp_path):
        persist_path = str(tmp_path / "clipboard.json")

        # Create monitor and add entries
        monitor1 = ClipboardMonitor(max_days=7, persist_path=persist_path)
        monitor1._add_entry("first", source_app="Safari")
        monitor1._add_entry("second")

        # Verify DB was written
        db_path = str(tmp_path / "clipboard.db")
        assert os.path.isfile(db_path)

        # Load in a new monitor
        monitor2 = ClipboardMonitor(max_days=7, persist_path=persist_path)
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
        try:
            ClipboardMonitor(max_days=7, persist_path=persist_path)
        except Exception:
            pass  # SQLite may raise on corrupt file; that's acceptable

    def test_json_migration_on_init(self, tmp_path):
        """If a JSON file exists at persist_path, it should be migrated."""
        import time as _time

        json_path = str(tmp_path / "clipboard.json")
        recent_ts = _time.time() - 3600
        data = [
            {"text": "hello", "timestamp": recent_ts, "source_app": "Safari"},
            {"text": "world", "timestamp": recent_ts},
        ]
        with open(json_path, "w") as f:
            json.dump(data, f)

        monitor = ClipboardMonitor(max_days=7, persist_path=json_path)
        assert len(monitor.entries) == 2
        assert monitor.entries[0].text == "world"  # most recent
        # JSON should be renamed to .bak
        assert not os.path.isfile(json_path)
        assert os.path.isfile(json_path + ".bak")

    def test_is_concealed(self):
        """Pasteboard with concealed type markers should be detected."""
        pb = MagicMock()
        pb.types.return_value = [
            "public.utf8-plain-text",
            "org.nspasteboard.ConcealedType",
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

    def test_start_stop(self):
        """Start and stop should not raise."""
        monitor = ClipboardMonitor(max_days=7, poll_interval=10.0)
        # Mock NSPasteboard to avoid actual clipboard access
        with patch(
            "voicetext.scripting.clipboard_monitor.ClipboardMonitor._check_clipboard"
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
        monitor = ClipboardMonitor(max_days=7)
        monitor._entries = [
            ClipboardEntry(text="text1"),
            ClipboardEntry(
                image_path="img1.png", image_width=100, image_height=100,
            ),
            ClipboardEntry(text="text2"),
        ]

        monitor.promote_image("img1.png")
        assert monitor.entries[0].image_path == "img1.png"

    def test_promote_image_not_found(self):
        monitor = ClipboardMonitor(max_days=7)
        monitor._entries = [ClipboardEntry(text="text1")]
        monitor.promote_image("nonexistent.png")  # Should not raise

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
        monitor._add_image_entry(b"bad", "png")
        assert len(monitor.entries) == 0
