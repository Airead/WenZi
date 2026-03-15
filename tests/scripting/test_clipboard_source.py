"""Tests for clipboard history data source."""

import time
from unittest.mock import MagicMock

from voicetext.scripting.clipboard_monitor import ClipboardEntry, ClipboardMonitor
from voicetext.scripting.sources.clipboard_source import (
    ClipboardSource,
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


class TestClipboardSource:
    def _make_monitor_with_entries(self, entries):
        """Create a mock ClipboardMonitor with given entries."""
        monitor = MagicMock(spec=ClipboardMonitor)
        monitor.entries = [
            ClipboardEntry(text=text, timestamp=ts, source_app=app)
            for text, ts, app in entries
        ]
        return monitor

    def test_empty_history(self):
        monitor = self._make_monitor_with_entries([])
        source = ClipboardSource(monitor)
        assert source.search("") == []

    def test_empty_query_returns_all(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            ("hello world", now - 60, "Safari"),
            ("foo bar", now - 120, "Terminal"),
        ])
        source = ClipboardSource(monitor)
        result = source.search("")
        assert len(result) == 2

    def test_substring_filter(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            ("hello world", now - 60, ""),
            ("foo bar", now - 120, ""),
            ("hello again", now - 180, ""),
        ])
        source = ClipboardSource(monitor)
        result = source.search("hello")
        assert len(result) == 2
        assert "hello" in result[0].title.lower()

    def test_case_insensitive(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            ("Hello World", now, ""),
        ])
        source = ClipboardSource(monitor)
        result = source.search("hello")
        assert len(result) == 1

    def test_long_text_truncated(self):
        now = time.time()
        long_text = "x" * 200
        monitor = self._make_monitor_with_entries([
            (long_text, now, ""),
        ])
        source = ClipboardSource(monitor)
        result = source.search("x")
        assert len(result[0].title) <= 80

    def test_multiline_collapsed(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            ("line1\nline2\nline3", now, ""),
        ])
        source = ClipboardSource(monitor)
        result = source.search("line")
        assert "\n" not in result[0].title

    def test_subtitle_with_source_app(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            ("hello", now - 120, "Safari"),
        ])
        source = ClipboardSource(monitor)
        result = source.search("hello")
        assert "Safari" in result[0].subtitle
        assert "ago" in result[0].subtitle

    def test_subtitle_without_source_app(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            ("hello", now - 120, ""),
        ])
        source = ClipboardSource(monitor)
        result = source.search("hello")
        assert "ago" in result[0].subtitle

    def test_action_is_callable(self):
        now = time.time()
        monitor = self._make_monitor_with_entries([
            ("hello", now, ""),
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
        assert cs.prefix == ">cb"
        assert cs.priority == 5
        assert cs.search is not None
