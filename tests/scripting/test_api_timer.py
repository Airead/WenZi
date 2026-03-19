"""Tests for vt.timer API."""

import threading
import time

from wenzi.scripting.registry import ScriptingRegistry
from wenzi.scripting.api.timer import TimerAPI


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

    def test_fire_once_cancel_race(self):
        """Fire and cancel racing on the same timer should not raise or deadlock."""
        reg = ScriptingRegistry()
        api = TimerAPI(reg)
        result = []

        timer_id = api.after(0.02, lambda: result.append(1))
        time.sleep(0.01)
        api.cancel(timer_id)
        # Wait for the timer thread to settle
        time.sleep(0.05)
        # pop_timer is atomic with cancel_timer, so either cancel won
        # (callback never runs) or fire won (callback runs and pops first).
        # In either case the entry should be gone and no exception raised.
        assert timer_id not in reg.timers

    def test_concurrent_cancel_fire_once(self):
        """Many concurrent cancel+fire cycles should not raise."""
        reg = ScriptingRegistry()
        api = TimerAPI(reg)
        errors = []

        def run_cycle():
            try:
                result = []
                tid = api.after(0.01, lambda: result.append(1))
                time.sleep(0.005)
                api.cancel(tid)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run_cycle) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        assert errors == []

    def test_get_timer_thread_safe(self):
        """Registry.get_timer returns entry under lock."""
        reg = ScriptingRegistry()
        entry = reg.register_timer(1.0, lambda: None, repeating=False)
        looked_up = reg.get_timer(entry.timer_id)
        assert looked_up is entry
        assert reg.get_timer("nonexistent") is None

    def test_pop_timer_thread_safe(self):
        """Registry.pop_timer atomically removes and returns entry."""
        reg = ScriptingRegistry()
        entry = reg.register_timer(1.0, lambda: None, repeating=False)
        tid = entry.timer_id
        popped = reg.pop_timer(tid)
        assert popped is entry
        # Second pop should return None
        assert reg.pop_timer(tid) is None
        assert tid not in reg.timers
