"""Tests for the async_demo plugin."""

import time
from unittest.mock import patch

import pytest

from wenzi.scripting.api import _WZNamespace
from wenzi.scripting.registry import ScriptingRegistry


@pytest.fixture(autouse=True)
def _fast_async_delays(monkeypatch):
    """Reduce async_demo sleep durations to speed up tests."""
    import async_demo.commands as cmds

    monkeypatch.setattr(cmds, "_CONCURRENT_DELAYS", (0.01, 0.01, 0.01))
    monkeypatch.setattr(cmds, "_ERROR_DELAY", 0.01)
    monkeypatch.setattr(cmds, "_RUN_DELAY", 0.01)
    monkeypatch.setattr(cmds, "_PICK_DELAY", 0.01)
    monkeypatch.setattr(cmds, "_SOURCE_DELAY", 0.01)


def _make_wz():
    """Create a wz namespace with chooser API initialized."""
    reg = ScriptingRegistry()
    wz = _WZNamespace(reg)
    _ = wz.chooser
    wz.chooser._ensure_command_source()
    return wz, reg


def _wait_for(predicate, timeout=5.0, interval=0.05):
    """Poll until predicate returns True or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class TestAsyncDemoRegistration:
    """Verify the plugin registers all expected commands."""

    def test_setup_registers_commands(self):
        wz, reg = _make_wz()
        from async_demo import setup

        setup(wz)

        cs = wz.chooser._command_source
        expected = [
            "async-sleep", "async-fetch", "async-timer",
            "async-concurrent", "async-error",
            "async-run", "async-pick",
        ]
        for name in expected:
            assert name in cs._commands, f"Command {name!r} not registered"

    def test_event_listener_registered(self):
        wz, reg = _make_wz()
        from async_demo import setup

        setup(wz)

        handlers = reg._event_listeners.get("transcription_done", [])
        assert len(handlers) >= 1


class TestAsyncSleepCommand:
    """Test the async-sleep command."""

    @patch("wenzi.scripting.api.alert._show_alert", new=lambda *a, **kw: None)
    @patch("wenzi.statusbar.send_notification")
    def test_sleep_and_notify(self, mock_notify):
        wz, reg = _make_wz()
        from async_demo import setup

        setup(wz)

        cs = wz.chooser._command_source
        entry = cs._commands["async-sleep"]
        entry.action("0.1")

        assert _wait_for(lambda: mock_notify.called, timeout=5.0)


class TestAsyncConcurrentCommand:
    """Test the async-concurrent command."""

    @patch("wenzi.scripting.api.alert._show_alert", new=lambda *a, **kw: None)
    @patch("wenzi.statusbar.send_notification")
    def test_tasks_run_concurrently(self, mock_notify):
        wz, reg = _make_wz()
        from async_demo import setup

        setup(wz)

        cs = wz.chooser._command_source
        entry = cs._commands["async-concurrent"]
        entry.action("")

        assert _wait_for(lambda: mock_notify.called, timeout=5.0)

        call_args = mock_notify.call_args
        body = call_args[0][2] if len(call_args[0]) > 2 else ""
        assert "A done" in body
        assert "B done" in body
        assert "C done" in body


class TestAsyncErrorCommand:
    """Test that async errors are logged."""

    def test_error_is_logged(self, caplog):
        with patch("wenzi.scripting.api.alert._show_alert", new=lambda *a, **kw: None):
            wz, reg = _make_wz()
            from async_demo import setup

            setup(wz)

            cs = wz.chooser._command_source
            entry = cs._commands["async-error"]
            entry.action("")

            assert _wait_for(
                lambda: any(
                    "Intentional async error" in r.message
                    for r in caplog.records
                ),
                timeout=5.0,
            )


class TestAsyncRunCommand:
    """Test the wz.run() command."""

    @patch("wenzi.scripting.api.alert._show_alert", new=lambda *a, **kw: None)
    @patch("wenzi.statusbar.send_notification")
    def test_run_completes(self, mock_notify):
        wz, reg = _make_wz()
        from async_demo import setup

        setup(wz)

        cs = wz.chooser._command_source
        entry = cs._commands["async-run"]
        entry.action("")

        assert _wait_for(lambda: mock_notify.called, timeout=5.0)

        call_args = mock_notify.call_args
        body = call_args[0][2] if len(call_args[0]) > 2 else ""
        assert "completed" in body
