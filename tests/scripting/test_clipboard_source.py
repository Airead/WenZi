"""Tests for clipboard history data source."""

import time
from unittest.mock import MagicMock, patch

from voicetext.scripting.clipboard_monitor import ClipboardEntry, ClipboardMonitor
from voicetext.scripting.sources.clipboard_source import (
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
        monitor.default_image_dir = MagicMock(return_value="/tmp/test_images")
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
        monitor.default_image_dir = MagicMock(return_value="/tmp/test_images")
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
