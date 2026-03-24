"""Tests for cc_sessions.auto_reload module."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from cc_sessions.auto_reload import AutoReloadWatcher


class TestAutoReloadWatcher:
    """Test kqueue-based auto reload watcher."""

    def test_detects_new_lines(self, tmp_path: Path):
        """Watcher fires callback when new JSONL lines are appended."""
        f = tmp_path / "session.jsonl"
        f.write_text('{"type":"user","message":"hello"}\n')

        received: list = []
        event = threading.Event()

        def on_new(lines):
            received.extend(lines)
            event.set()

        w = AutoReloadWatcher(str(f), on_new_lines=on_new)
        w.start(skip_existing=True)
        try:
            # Append a new line
            with open(f, "a") as fh:
                fh.write('{"type":"assistant","message":"hi"}\n')

            assert event.wait(timeout=3), "callback not called within timeout"
            assert len(received) == 1
            assert received[0]["type"] == "assistant"
        finally:
            w.stop()

    def test_skips_existing_content(self, tmp_path: Path):
        """With skip_existing=True, existing content is not reported."""
        f = tmp_path / "session.jsonl"
        f.write_text('{"type":"user","message":"old"}\n')

        received: list = []
        event = threading.Event()

        def on_new(lines):
            received.extend(lines)
            event.set()

        w = AutoReloadWatcher(str(f), on_new_lines=on_new)
        w.start(skip_existing=True)
        try:
            # Append new content
            with open(f, "a") as fh:
                fh.write('{"type":"user","message":"new"}\n')

            assert event.wait(timeout=3)
            assert all(line["message"] != "old" for line in received)
            assert received[0]["message"] == "new"
        finally:
            w.stop()

    def test_partial_line_buffering(self, tmp_path: Path):
        """Incomplete lines are buffered until a newline arrives."""
        f = tmp_path / "session.jsonl"
        f.write_text("")

        received: list = []
        event = threading.Event()

        def on_new(lines):
            received.extend(lines)
            event.set()

        w = AutoReloadWatcher(str(f), on_new_lines=on_new)
        w.start(skip_existing=True)
        try:
            # Write a partial line (no trailing newline)
            with open(f, "a") as fh:
                fh.write('{"type":"us')
                fh.flush()

            # Give kqueue time to fire — should NOT trigger callback
            time.sleep(0.5)
            assert len(received) == 0, "partial line should not trigger callback"

            # Complete the line
            with open(f, "a") as fh:
                fh.write('er","message":"done"}\n')

            assert event.wait(timeout=3)
            assert len(received) == 1
            assert received[0]["type"] == "user"
            assert received[0]["message"] == "done"
        finally:
            w.stop()

    def test_multiple_lines_at_once(self, tmp_path: Path):
        """Multiple lines written at once are all reported."""
        f = tmp_path / "session.jsonl"
        f.write_text("")

        received: list = []
        event = threading.Event()

        def on_new(lines):
            received.extend(lines)
            if len(received) >= 3:
                event.set()

        w = AutoReloadWatcher(str(f), on_new_lines=on_new)
        w.start(skip_existing=True)
        try:
            with open(f, "a") as fh:
                fh.write('{"n":1}\n{"n":2}\n{"n":3}\n')

            assert event.wait(timeout=3)
            assert len(received) == 3
            assert [r["n"] for r in received] == [1, 2, 3]
        finally:
            w.stop()

    def test_malformed_lines_skipped(self, tmp_path: Path):
        """Malformed JSON lines are skipped without crashing."""
        f = tmp_path / "session.jsonl"
        f.write_text("")

        received: list = []
        event = threading.Event()

        def on_new(lines):
            received.extend(lines)
            event.set()

        w = AutoReloadWatcher(str(f), on_new_lines=on_new)
        w.start(skip_existing=True)
        try:
            with open(f, "a") as fh:
                fh.write('not json\n{"valid":true}\n')

            assert event.wait(timeout=3)
            assert len(received) == 1
            assert received[0]["valid"] is True
        finally:
            w.stop()

    def test_stop_and_restart(self, tmp_path: Path):
        """Watcher can be stopped and restarted."""
        f = tmp_path / "session.jsonl"
        f.write_text('{"init":true}\n')

        received: list = []
        event = threading.Event()

        def on_new(lines):
            received.extend(lines)
            event.set()

        w = AutoReloadWatcher(str(f), on_new_lines=on_new)

        # First run
        w.start(skip_existing=True)
        with open(f, "a") as fh:
            fh.write('{"round":1}\n')
        assert event.wait(timeout=3)
        w.stop()
        assert not w.running

        # Reset and restart
        received.clear()
        event.clear()
        w.start(skip_existing=True)
        try:
            with open(f, "a") as fh:
                fh.write('{"round":2}\n')
            assert event.wait(timeout=3)
            assert received[0]["round"] == 2
        finally:
            w.stop()

    def test_running_property(self, tmp_path: Path):
        """The running property reflects thread state."""
        f = tmp_path / "session.jsonl"
        f.write_text("")

        w = AutoReloadWatcher(str(f), on_new_lines=lambda _lines: None)
        assert not w.running
        w.start()
        try:
            assert w.running
        finally:
            w.stop()
        assert not w.running

    def test_start_idempotent(self, tmp_path: Path):
        """Calling start() twice does not create a second thread."""
        f = tmp_path / "session.jsonl"
        f.write_text("")

        w = AutoReloadWatcher(str(f), on_new_lines=lambda _lines: None)
        w.start()
        try:
            thread1 = w._thread
            w.start()  # second call — should be no-op
            assert w._thread is thread1
        finally:
            w.stop()

    def test_file_delete_and_recreate(self, tmp_path: Path):
        """Watcher recovers when the file is deleted and recreated."""
        f = tmp_path / "session.jsonl"
        f.write_text('{"init":true}\n')

        received: list = []
        event = threading.Event()

        def on_new(lines):
            received.extend(lines)
            event.set()

        w = AutoReloadWatcher(str(f), on_new_lines=on_new)
        w.start(skip_existing=True)
        try:
            # Delete and recreate the file
            f.unlink()
            time.sleep(0.3)
            f.write_text('{"after_recreate":true}\n')

            assert event.wait(timeout=5), "callback not called after file recreate"
            assert any(line.get("after_recreate") for line in received)
        finally:
            w.stop()

    def test_callback_error_does_not_crash_watcher(self, tmp_path: Path):
        """A failing callback does not stop the watcher."""
        f = tmp_path / "session.jsonl"
        f.write_text("")

        call_count = 0
        event = threading.Event()

        def on_new(lines):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")
            event.set()

        w = AutoReloadWatcher(str(f), on_new_lines=on_new)
        w.start(skip_existing=True)
        try:
            # First write — callback raises
            with open(f, "a") as fh:
                fh.write('{"n":1}\n')
            time.sleep(0.5)

            # Second write — watcher should still be alive
            with open(f, "a") as fh:
                fh.write('{"n":2}\n')
            assert event.wait(timeout=3), "watcher died after callback error"
            assert w.running
        finally:
            w.stop()
