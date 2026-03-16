"""Tests for clipboard history data source."""

import time
from unittest.mock import MagicMock, patch

from wenzi.scripting.clipboard_monitor import ClipboardEntry, ClipboardMonitor
from wenzi.scripting.sources.clipboard_source import (
    ClipboardSource,
    _format_file_size,
    _format_time_ago,
)


class TestFormatTimeAgo:
    def test_just_now(self):
        assert _format_time_ago(time.time() - 10) == "just now"

    def test_minutes(self):
        result = _format_time_ago(time.time() - 180)
        assert "3m ago" == result

    def test_hours(self):
        result = _format_time_ago(time.time() - 7200)
        assert "2h ago" == result

    def test_days(self):
        result = _format_time_ago(time.time() - 172800)
        assert "2d ago" == result


class TestFormatFileSize:
    def test_bytes(self):
        assert _format_file_size(512) == "512 B"

    def test_kilobytes(self):
        assert _format_file_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert _format_file_size(3 * 1024 * 1024) == "3.0 MB"


class TestClipboardSource:
    def _make_monitor_with_entries(self, entries):
        """Create a mock ClipboardMonitor with given entries."""
        monitor = MagicMock(spec=ClipboardMonitor)
        monitor.entries = [
            ClipboardEntry(
                text=e.get("text", ""),
                timestamp=e.get("timestamp", time.time()),
                source_app=e.get("source_app", ""),
                image_path=e.get("image_path", ""),
                image_width=e.get("image_width", 0),
                image_height=e.get("image_height", 0),
                image_size=e.get("image_size", 0),
            )
            for e in entries
        ]
        monitor.image_dir = "/tmp/test_images"
        return monitor

    def test_empty_history(self):
        monitor = self._make_monitor_with_entries([])
        source = ClipboardSource(monitor)
        assert source.search("") == []

    def test_empty_query_returns_all(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {"text": "hello world", "timestamp": now - 60, "source_app": "Safari"},
            {"text": "foo bar", "timestamp": now - 120, "source_app": "Terminal"},
        ])
        source = ClipboardSource(monitor)
        result = source.search("")
        assert len(result) == 2

    def test_substring_filter(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {"text": "hello world", "timestamp": now - 60},
            {"text": "foo bar", "timestamp": now - 120},
            {"text": "hello again", "timestamp": now - 180},
        ])
        source = ClipboardSource(monitor)
        result = source.search("hello")
        assert len(result) == 2
        assert "hello" in result[0].title.lower()

    def test_case_insensitive(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {"text": "Hello World", "timestamp": now},
        ])
        source = ClipboardSource(monitor)
        result = source.search("hello")
        assert len(result) == 1

    def test_long_text_truncated(self):
        now = time.time()
        long_text = "x" * 200
        monitor = self._make_monitor_with_entries([
            {"text": long_text, "timestamp": now},
        ])
        source = ClipboardSource(monitor)
        result = source.search("x")
        assert len(result[0].title) <= 80

    def test_multiline_collapsed(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {"text": "line1\nline2\nline3", "timestamp": now},
        ])
        source = ClipboardSource(monitor)
        result = source.search("line")
        assert "\n" not in result[0].title

    def test_subtitle_with_source_app(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {"text": "hello", "timestamp": now - 120, "source_app": "Safari"},
        ])
        source = ClipboardSource(monitor)
        result = source.search("hello")
        assert "Safari" in result[0].subtitle
        assert "ago" in result[0].subtitle

    def test_subtitle_without_source_app(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {"text": "hello", "timestamp": now - 120},
        ])
        source = ClipboardSource(monitor)
        result = source.search("hello")
        assert "ago" in result[0].subtitle

    def test_action_is_callable(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {"text": "hello", "timestamp": now},
        ])
        source = ClipboardSource(monitor)
        result = source.search("hello")
        assert result[0].action is not None
        assert callable(result[0].action)

    def test_as_chooser_source(self):
        monitor = self._make_monitor_with_entries([])
        source = ClipboardSource(monitor)
        cs = source.as_chooser_source()
        assert cs.name == "clipboard"
        assert cs.prefix == "cb"
        assert cs.priority == 5
        assert cs.search is not None

    def test_text_entry_has_preview(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {"text": "hello world full text", "timestamp": now},
        ])
        source = ClipboardSource(monitor)
        result = source.search("")
        assert result[0].preview is not None
        assert result[0].preview["type"] == "text"
        assert result[0].preview["content"] == "hello world full text"

    def test_text_entry_has_delete_action(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {"text": "hello", "timestamp": now},
        ])
        source = ClipboardSource(monitor)
        result = source.search("")
        assert result[0].delete_action is not None
        assert callable(result[0].delete_action)

    def test_delete_action_calls_monitor_delete_text(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {"text": "hello", "timestamp": now},
        ])
        source = ClipboardSource(monitor)
        result = source.search("")
        result[0].delete_action()
        monitor.delete_text.assert_called_once_with("hello")


class TestImageEntries:
    def _make_monitor_with_entries(self, entries):
        monitor = MagicMock(spec=ClipboardMonitor)
        monitor.entries = [
            ClipboardEntry(
                text=e.get("text", ""),
                timestamp=e.get("timestamp", time.time()),
                source_app=e.get("source_app", ""),
                image_path=e.get("image_path", ""),
                image_width=e.get("image_width", 0),
                image_height=e.get("image_height", 0),
                image_size=e.get("image_size", 0),
            )
            for e in entries
        ]
        monitor.image_dir = "/tmp/test_images"
        return monitor

    def test_image_entry_title(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {
                "image_path": "test.png",
                "image_width": 1450,
                "image_height": 866,
                "image_size": 3600000,
                "timestamp": now - 120,
                "source_app": "Safari",
            },
        ])
        source = ClipboardSource(monitor)
        result = source.search("")
        assert len(result) == 1
        assert "1450" in result[0].title
        assert "866" in result[0].title
        assert "3.4 MB" in result[0].title

    def test_image_entry_subtitle(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {
                "image_path": "test.png",
                "image_width": 100,
                "image_height": 100,
                "image_size": 1000,
                "timestamp": now - 120,
                "source_app": "Safari",
            },
        ])
        source = ClipboardSource(monitor)
        result = source.search("")
        assert "Safari" in result[0].subtitle
        assert "ago" in result[0].subtitle

    def test_image_entry_has_preview(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {
                "image_path": "test.png",
                "image_width": 100,
                "image_height": 100,
                "image_size": 1000,
                "timestamp": now,
            },
        ])
        source = ClipboardSource(monitor)
        # Patch os.path.isfile to return False (no actual file)
        with patch("os.path.isfile", return_value=False):
            result = source.search("")
        assert result[0].preview is not None
        assert result[0].preview["type"] == "image"
        assert result[0].preview["src"] == ""  # no file

    def test_image_entry_has_actions(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {
                "image_path": "test.png",
                "image_width": 100,
                "image_height": 100,
                "image_size": 1000,
                "timestamp": now,
            },
        ])
        source = ClipboardSource(monitor)
        with patch("os.path.isfile", return_value=False):
            result = source.search("")
        assert result[0].action is not None
        assert result[0].secondary_action is not None

    def test_image_search_filter(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {"text": "hello", "timestamp": now},
            {
                "image_path": "test.png",
                "image_width": 100,
                "image_height": 100,
                "image_size": 1000,
                "timestamp": now,
            },
        ])
        source = ClipboardSource(monitor)
        with patch("os.path.isfile", return_value=False):
            # "image" should match image entries
            result = source.search("image")
        assert len(result) == 1
        assert "Image" in result[0].title

    def test_image_text_mixed(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {"text": "hello", "timestamp": now},
            {
                "image_path": "test.png",
                "image_width": 100,
                "image_height": 100,
                "image_size": 1000,
                "timestamp": now,
            },
        ])
        source = ClipboardSource(monitor)
        with patch("os.path.isfile", return_value=False):
            result = source.search("")
        assert len(result) == 2

    def test_image_entry_has_delete_action(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {
                "image_path": "test.png",
                "image_width": 100,
                "image_height": 100,
                "image_size": 1000,
                "timestamp": now,
            },
        ])
        source = ClipboardSource(monitor)
        with patch("os.path.isfile", return_value=False):
            result = source.search("")
        assert result[0].delete_action is not None
        assert callable(result[0].delete_action)

    def test_delete_action_calls_monitor_delete_image(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            {
                "image_path": "test.png",
                "image_width": 100,
                "image_height": 100,
                "image_size": 1000,
                "timestamp": now,
            },
        ])
        source = ClipboardSource(monitor)
        with patch("os.path.isfile", return_value=False):
            result = source.search("")
        result[0].delete_action()
        monitor.delete_image.assert_called_once_with("test.png")


class TestMaxResults:
    """Tests for P0: max_results early break."""

    def _make_monitor_with_entries(self, entries):
        monitor = MagicMock(spec=ClipboardMonitor)
        monitor.entries = [
            ClipboardEntry(
                text=e.get("text", ""),
                timestamp=e.get("timestamp", time.time()),
                source_app=e.get("source_app", ""),
                image_path=e.get("image_path", ""),
                image_width=e.get("image_width", 0),
                image_height=e.get("image_height", 0),
                image_size=e.get("image_size", 0),
            )
            for e in entries
        ]
        monitor.image_dir = "/tmp/test_images"
        monitor.version = 1
        return monitor

    def test_max_results_truncates(self):
        now = time.time()
        entries = [{"text": f"entry {i}", "timestamp": now - i} for i in range(100)]
        monitor = self._make_monitor_with_entries(entries)
        source = ClipboardSource(monitor, max_results=10)
        result = source.search("")
        assert len(result) == 10

    def test_max_results_default_is_50(self):
        now = time.time()
        entries = [{"text": f"entry {i}", "timestamp": now - i} for i in range(80)]
        monitor = self._make_monitor_with_entries(entries)
        source = ClipboardSource(monitor)
        result = source.search("")
        assert len(result) == 50

    def test_max_results_with_filter(self):
        now = time.time()
        entries = [{"text": f"match {i}", "timestamp": now - i} for i in range(100)]
        entries += [{"text": "no hit", "timestamp": now - 200}]
        monitor = self._make_monitor_with_entries(entries)
        source = ClipboardSource(monitor, max_results=5)
        result = source.search("match")
        assert len(result) == 5

    def test_fewer_than_max_returns_all(self):
        now = time.time()
        entries = [{"text": f"entry {i}", "timestamp": now - i} for i in range(3)]
        monitor = self._make_monitor_with_entries(entries)
        source = ClipboardSource(monitor, max_results=10)
        result = source.search("")
        assert len(result) == 3


class TestEmptyQueryCache:
    """Tests for P2: empty query result caching."""

    def _make_monitor_with_entries(self, entries, version=1):
        monitor = MagicMock(spec=ClipboardMonitor)
        monitor.entries = [
            ClipboardEntry(
                text=e.get("text", ""),
                timestamp=e.get("timestamp", time.time()),
                source_app=e.get("source_app", ""),
                image_path=e.get("image_path", ""),
                image_width=e.get("image_width", 0),
                image_height=e.get("image_height", 0),
                image_size=e.get("image_size", 0),
            )
            for e in entries
        ]
        monitor.image_dir = "/tmp/test_images"
        monitor.version = version
        return monitor

    def test_empty_query_caches_results(self):
        now = time.time()
        entries = [{"text": f"entry {i}", "timestamp": now - i} for i in range(3)]
        monitor = self._make_monitor_with_entries(entries, version=1)
        source = ClipboardSource(monitor, max_results=50)

        # First call builds cache
        result1 = source.search("")
        assert len(result1) == 3
        assert source._empty_cache is not None
        assert source._empty_cache_version == 1
        assert source._empty_cache_time > 0

        # Second call returns cached copy
        result2 = source.search("")
        assert len(result2) == 3
        # Verify it's a copy, not the same list
        assert result2 is not source._empty_cache

    def test_cache_invalidated_on_version_change(self):
        now = time.time()
        entries = [{"text": "hello", "timestamp": now}]
        monitor = self._make_monitor_with_entries(entries, version=1)
        source = ClipboardSource(monitor, max_results=50)

        source.search("")
        assert source._empty_cache_version == 1

        # Simulate new clipboard entry
        monitor.version = 2
        monitor.entries = [
            ClipboardEntry(text="new", timestamp=now + 1),
            ClipboardEntry(text="hello", timestamp=now),
        ]

        result = source.search("")
        assert len(result) == 2
        assert source._empty_cache_version == 2

    def test_cache_cleared_when_entries_empty(self):
        now = time.time()
        entries = [{"text": "hello", "timestamp": now}]
        monitor = self._make_monitor_with_entries(entries, version=1)
        source = ClipboardSource(monitor, max_results=50)

        source.search("")
        assert source._empty_cache is not None

        # Entries become empty (version increments as in real monitor)
        monitor.entries = []
        monitor.version = 2
        result = source.search("")
        assert result == []
        assert source._empty_cache is None

    def test_cache_expires_after_ttl(self):
        now = time.time()
        entries = [{"text": "hello", "timestamp": now}]
        monitor = self._make_monitor_with_entries(entries, version=1)
        source = ClipboardSource(monitor, max_results=50)

        source.search("")
        assert source._empty_cache is not None

        # Simulate TTL expiry by backdating cache time
        source._empty_cache_time = now - source._CACHE_TTL - 1

        # Same version but expired — should rebuild (entries re-fetched)
        result = source.search("")
        assert len(result) == 1
        # Cache time should be refreshed
        assert source._empty_cache_time > now - 1

    def test_non_empty_query_does_not_use_cache(self):
        now = time.time()
        entries = [
            {"text": "hello world", "timestamp": now},
            {"text": "goodbye", "timestamp": now - 1},
        ]
        monitor = self._make_monitor_with_entries(entries, version=1)
        source = ClipboardSource(monitor, max_results=50)

        # Build cache with empty query
        source.search("")
        assert source._empty_cache is not None

        # Filtered query should not use cache
        result = source.search("hello")
        assert len(result) == 1
        assert "hello" in result[0].title.lower()
