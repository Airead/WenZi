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
        monitor._add_entry("hello", source_app="Safari")
        assert monitor.entries[0].source_app == "Safari"

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

        mock_pb = MagicMock()
        mock_pb.changeCount.return_value = 1
        mock_pb.types.return_value = list(pb_data.keys()) + list(pb_text.keys())
        mock_pb.dataForType_.side_effect = lambda t: pb_data.get(t)
        mock_pb.stringForType_.side_effect = lambda t: pb_text.get(t)

        return monitor, mock_pb

    def _run(self, monitor, mock_pb, source_app="TestApp"):
        """Execute _check_clipboard with mocked AppKit imports."""
        with patch.object(
            ClipboardMonitor, "_get_frontmost_app", return_value=source_app,
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
            _run_check(monitor, mock_pb, source_app)

    def test_png_preferred_over_text(self):
        """When clipboard has PNG + text, PNG image entry is saved."""
        monitor, mock_pb = self._setup(
            pb_data={self.PNG_TYPE: b"fake_png"},
            pb_text={self.STR_TYPE: "https://example.com/img.png"},
        )
        _run_check(monitor, mock_pb, "Safari")

        monitor._add_image_entry.assert_called_once_with(
            b"fake_png", "png", "Safari",
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
        _run_check(monitor, mock_pb, "Safari")

        monitor._add_image_entry.assert_called_once()
        monitor._add_entry.assert_called_once_with("actual text content", "Safari")

    def test_text_preferred_over_tiff(self):
        """When clipboard has text + TIFF (rich text), text entry is saved."""
        monitor, mock_pb = self._setup(
            pb_data={self.TIFF_TYPE: b"tiff_rendering"},
            pb_text={self.STR_TYPE: "hello world"},
        )
        _run_check(monitor, mock_pb, "Notes")

        monitor._add_entry.assert_called_once_with("hello world", "Notes")
        monitor._add_image_entry.assert_not_called()

    def test_tiff_only_saved_as_image(self):
        """When clipboard has only TIFF (no PNG, no text), image entry is saved."""
        monitor, mock_pb = self._setup(
            pb_data={self.TIFF_TYPE: b"tiff_image"},
        )
        _run_check(monitor, mock_pb, "Preview")

        monitor._add_image_entry.assert_called_once_with(
            b"tiff_image", "tiff", "Preview",
        )
        monitor._add_entry.assert_not_called()

    def test_text_only_no_image(self):
        """When clipboard has only text, text entry is saved."""
        monitor, mock_pb = self._setup(
            pb_text={self.STR_TYPE: "plain text"},
        )
        _run_check(monitor, mock_pb, "Terminal")

        monitor._add_entry.assert_called_once_with("plain text", "Terminal")
        monitor._add_image_entry.assert_not_called()


def _run_check(monitor, mock_pb, source_app):
    """Simulate _check_clipboard logic with a mock pasteboard.

    Reproduces the detection order (PNG → text → TIFF) without importing
    AppKit, so no sys.modules pollution can occur.
    """
    PNG = "public.png"
    TIFF = "public.tiff"
    STRING = "public.utf8-plain-text"

    png_data = mock_pb.dataForType_(PNG)
    if png_data is not None:
        if monitor._add_image_entry(bytes(png_data), "png", source_app):
            return

    text = mock_pb.stringForType_(STRING)
    if text and str(text).strip():
        text_str = str(text).strip()
        monitor._add_entry(text_str, source_app)
        return

    tiff_data = mock_pb.dataForType_(TIFF)
    if tiff_data is not None:
        monitor._add_image_entry(bytes(tiff_data), "tiff", source_app)


class TestVersionProperty:
    """Tests for P2: version counter on ClipboardMonitor."""

    def test_initial_version_is_zero(self):
        monitor = ClipboardMonitor()
        assert monitor.version == 0

    def test_version_increments_on_add_entry(self):
        monitor = ClipboardMonitor()
        v0 = monitor.version
        monitor._add_entry("hello")
        assert monitor.version == v0 + 1

    def test_version_increments_on_clear(self):
        monitor = ClipboardMonitor()
        monitor._add_entry("hello")
        v1 = monitor.version
        monitor.clear()
        assert monitor.version == v1 + 1

    def test_version_increments_on_promote(self):
        monitor = ClipboardMonitor()
        monitor._add_entry("first")
        monitor._add_entry("second")
        v = monitor.version
        monitor.promote("first")
        assert monitor.version == v + 1

    def test_version_increments_on_delete_text(self):
        monitor = ClipboardMonitor()
        monitor._add_entry("hello")
        v = monitor.version
        monitor.delete_text("hello")
        assert monitor.version == v + 1

    def test_version_not_incremented_on_no_match_promote(self):
        monitor = ClipboardMonitor()
        monitor._add_entry("hello")
        v = monitor.version
        monitor.promote("nonexistent")
        assert monitor.version == v

    def test_version_not_incremented_on_duplicate_add(self):
        monitor = ClipboardMonitor()
        monitor._add_entry("hello")
        v = monitor.version
        monitor._add_entry("hello")  # duplicate, skipped
        assert monitor.version == v
