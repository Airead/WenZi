"""Tests for vt.timer API."""

import threading
import time

from voicetext.scripting.registry import ScriptingRegistry
from voicetext.scripting.api.timer import TimerAPI


class TestTimerAPI:
    def test_after(self):
        reg = ScriptingRegistry()
        api = TimerAPI(reg)
        result = []
        done = threading.Event()

        def cb():
            result.append(1)
            done.set()

        timer_id = api.after(0.05, cb)
        assert timer_id in reg.timers
        done.wait(timeout=2.0)
        assert result == [1]
        # One-shot timer should be removed after firing
        time.sleep(0.05)
        assert timer_id not in reg.timers

    def test_every(self):
        reg = ScriptingRegistry()
        api = TimerAPI(reg)
        result = []
        done = threading.Event()

        def cb():
            result.append(1)
            if len(result) >= 3:
                done.set()

        timer_id = api.every(0.05, cb)
        done.wait(timeout=2.0)
        api.cancel(timer_id)
        assert len(result) >= 3

    def test_cancel(self):
        reg = ScriptingRegistry()
        api = TimerAPI(reg)
        result = []

        timer_id = api.after(0.5, lambda: result.append(1))
        api.cancel(timer_id)
        time.sleep(0.6)
        assert result == []
        assert timer_id not in reg.timers

    def test_cancel_nonexistent(self):
        reg = ScriptingRegistry()
        api = TimerAPI(reg)
        api.cancel("nonexistent")  # Should not raise
