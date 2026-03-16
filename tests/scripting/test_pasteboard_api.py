"""Tests for the Pasteboard API (history support)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List

from voicetext.scripting.api.pasteboard import PasteboardAPI


@dataclass
class _FakeEntry:
    text: str = ""
    timestamp: float = field(default_factory=time.time)
    source_app: str = ""
    image_path: str = ""


class _FakeMonitor:
    """Minimal stub that mimics ClipboardMonitor for testing."""

    def __init__(self, entries: List[_FakeEntry] | None = None) -> None:
        self._entries = entries or []
        self.cleared = False

    @property
    def entries(self) -> list:
        return list(self._entries)

    def clear(self) -> None:
        self.cleared = True
        self._entries.clear()


class TestPasteboardHistory:
    def test_history_no_monitor(self):
        api = PasteboardAPI()
        assert api.history() == []

    def test_history_returns_entries(self):
        api = PasteboardAPI()
        now = time.time()
        monitor = _FakeMonitor([
            _FakeEntry(text="hello", timestamp=now, source_app="Safari"),
            _FakeEntry(text="world", timestamp=now - 60, source_app="Chrome"),
        ])
        api._set_monitor(monitor)

        result = api.history()
        assert len(result) == 2
        assert result[0]["text"] == "hello"
        assert result[0]["source_app"] == "Safari"
        assert "timestamp" in result[0]
        assert result[1]["text"] == "world"

    def test_history_limit(self):
        api = PasteboardAPI()
        entries = [_FakeEntry(text=f"item-{i}") for i in range(10)]
        api._set_monitor(_FakeMonitor(entries))

        result = api.history(limit=3)
        assert len(result) == 3
        assert result[0]["text"] == "item-0"

    def test_history_image_entry(self):
        api = PasteboardAPI()
        api._set_monitor(_FakeMonitor([
            _FakeEntry(image_path="screenshot.png"),
        ]))

        result = api.history()
        assert len(result) == 1
        assert result[0]["image_path"] == "screenshot.png"

    def test_history_text_entry_no_image_key(self):
        api = PasteboardAPI()
        api._set_monitor(_FakeMonitor([_FakeEntry(text="abc")]))

        result = api.history()
        assert "image_path" not in result[0]

    def test_clear_history(self):
        api = PasteboardAPI()
        monitor = _FakeMonitor([_FakeEntry(text="x")])
        api._set_monitor(monitor)

        api.clear_history()
        assert monitor.cleared is True

    def test_clear_history_no_monitor(self):
        api = PasteboardAPI()
        # Should not raise
        api.clear_history()

    def test_set_monitor_none_clears(self):
        api = PasteboardAPI()
        api._set_monitor(_FakeMonitor([_FakeEntry(text="x")]))
        assert len(api.history()) == 1
        api._set_monitor(None)
        assert api.history() == []
