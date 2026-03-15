"""Tests for clipboard monitor."""

from unittest.mock import MagicMock, patch

from voicetext.scripting.clipboard_monitor import ClipboardEntry, ClipboardMonitor


class TestClipboardEntry:
    def test_defaults(self):
        entry = ClipboardEntry(text="hello")
        assert entry.text == "hello"
        assert entry.timestamp > 0
        assert entry.source_app == ""

    def test_with_all_fields(self):
        entry = ClipboardEntry(
            text="test", timestamp=1000.0, source_app="Safari"
        )
        assert entry.text == "test"
        assert entry.timestamp == 1000.0
        assert entry.source_app == "Safari"


class TestClipboardMonitor:
    def test_add_entry(self):
        monitor = ClipboardMonitor(max_items=10)
        monitor._add_entry("hello")
        assert len(monitor.entries) == 1
        assert monitor.entries[0].text == "hello"

    def test_add_entry_with_source_app(self):
        monitor = ClipboardMonitor(max_items=10)
        monitor._add_entry("hello", source_app="Safari")
        assert monitor.entries[0].source_app == "Safari"

    def test_deduplication(self):
        """Consecutive identical texts should not create duplicate entries."""
        monitor = ClipboardMonitor(max_items=10)
        monitor._add_entry("hello")
        monitor._add_entry("hello")
        assert len(monitor.entries) == 1

    def test_different_texts_not_deduplicated(self):
        monitor = ClipboardMonitor(max_items=10)
        monitor._add_entry("hello")
        monitor._add_entry("world")
        assert len(monitor.entries) == 2

    def test_max_items(self):
        monitor = ClipboardMonitor(max_items=3)
        for i in range(5):
            monitor._add_entry(f"item {i}")
        assert len(monitor.entries) == 3
        # Most recent should be first
        assert monitor.entries[0].text == "item 4"

    def test_newest_first(self):
        monitor = ClipboardMonitor(max_items=10)
        monitor._add_entry("first")
        monitor._add_entry("second")
        monitor._add_entry("third")
        assert monitor.entries[0].text == "third"
        assert monitor.entries[2].text == "first"

    def test_clear(self):
        monitor = ClipboardMonitor(max_items=10)
        monitor._add_entry("hello")
        monitor.clear()
        assert len(monitor.entries) == 0

    def test_entries_returns_copy(self):
        monitor = ClipboardMonitor(max_items=10)
        monitor._add_entry("hello")
        entries = monitor.entries
        entries.clear()
        assert len(monitor.entries) == 1  # Original not affected

    def test_persistence_save_and_load(self, tmp_path):
        persist_path = str(tmp_path / "clipboard.json")

        # Create monitor and add entries
        monitor1 = ClipboardMonitor(max_items=10, persist_path=persist_path)
        monitor1._add_entry("first", source_app="Safari")
        monitor1._add_entry("second")

        # Verify file was written
        assert (tmp_path / "clipboard.json").exists()

        # Load in a new monitor
        monitor2 = ClipboardMonitor(max_items=10, persist_path=persist_path)
        assert len(monitor2.entries) == 2
        assert monitor2.entries[0].text == "second"
        assert monitor2.entries[1].text == "first"
        assert monitor2.entries[1].source_app == "Safari"

    def test_load_corrupt_file(self, tmp_path):
        persist_path = str(tmp_path / "clipboard.json")
        with open(persist_path, "w") as f:
            f.write("not json")

        monitor = ClipboardMonitor(max_items=10, persist_path=persist_path)
        assert len(monitor.entries) == 0

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
        monitor = ClipboardMonitor(max_items=10, poll_interval=10.0)
        # Mock NSPasteboard to avoid actual clipboard access
        with patch("voicetext.scripting.clipboard_monitor.ClipboardMonitor._check_clipboard"):
            monitor.start()
            assert monitor._thread is not None
            assert monitor._thread.is_alive()
            monitor.stop()
            assert monitor._thread is None
