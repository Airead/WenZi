"""Tests for scripting registry."""

import threading

from wenzi.scripting.registry import (
    LeaderMapping,
    ScriptingRegistry,
)


def _noop():
    pass


class TestScriptingRegistry:
    def test_register_leader(self):
        reg = ScriptingRegistry()
        mappings = [
            LeaderMapping(key="w", app="WeChat"),
            LeaderMapping(key="s", app="Slack"),
        ]
        reg.register_leader("cmd_r", mappings)
        assert "cmd_r" in reg.leaders
        assert len(reg.leaders["cmd_r"].mappings) == 2
        assert reg.leaders["cmd_r"].mappings[0].app == "WeChat"

    def test_register_leader_overwrites(self):
        reg = ScriptingRegistry()
        reg.register_leader("cmd_r", [LeaderMapping(key="w", app="WeChat")])
        reg.register_leader("cmd_r", [LeaderMapping(key="s", app="Slack")])
        assert len(reg.leaders["cmd_r"].mappings) == 1
        assert reg.leaders["cmd_r"].mappings[0].app == "Slack"

    def test_register_hotkey(self):
        reg = ScriptingRegistry()
        reg.register_hotkey("ctrl+cmd+v", _noop)
        assert len(reg.hotkeys) == 1
        assert reg.hotkeys[0].hotkey_str == "ctrl+cmd+v"
        assert reg.hotkeys[0].callback is _noop

    def test_register_timer(self):
        reg = ScriptingRegistry()
        entry = reg.register_timer(1.0, _noop, repeating=False)
        assert entry.timer_id in reg.timers
        assert entry.interval == 1.0
        assert entry.repeating is False

    def test_cancel_timer(self):
        reg = ScriptingRegistry()
        entry = reg.register_timer(10.0, _noop)
        # Set a real timer to verify cancel
        t = threading.Timer(10.0, _noop)
        t.daemon = True
        entry._timer = t
        t.start()

        reg.cancel_timer(entry.timer_id)
        assert entry.timer_id not in reg.timers
        # Timer.cancel() prevents firing but thread may still be alive briefly
        t.join(timeout=1.0)
        assert not t.is_alive()

    def test_cancel_nonexistent_timer(self):
        reg = ScriptingRegistry()
        reg.cancel_timer("nonexistent")  # Should not raise

    def test_clear(self):
        reg = ScriptingRegistry()
        reg.register_leader("cmd_r", [LeaderMapping(key="w", app="WeChat")])
        reg.register_hotkey("ctrl+v", _noop)
        entry = reg.register_timer(10.0, _noop)
        reg.register_event("test_event", _noop)

        # Add a real timer
        t = threading.Timer(10.0, _noop)
        t.daemon = True
        entry._timer = t
        t.start()

        reg.clear()
        assert len(reg.leaders) == 0
        assert len(reg.hotkeys) == 0
        assert len(reg.timers) == 0
        assert len(reg._event_listeners) == 0
        t.join(timeout=1.0)
        assert not t.is_alive()


class TestEventListeners:
    def test_register_event(self):
        reg = ScriptingRegistry()
        reg.register_event("test", _noop)
        assert len(reg._event_listeners["test"]) == 1
        assert reg._event_listeners["test"][0] is _noop

    def test_register_multiple_events(self):
        reg = ScriptingRegistry()
        cb1, cb2 = lambda d: None, lambda d: None
        reg.register_event("test", cb1)
        reg.register_event("test", cb2)
        assert len(reg._event_listeners["test"]) == 2

    def test_unregister_event(self):
        reg = ScriptingRegistry()
        reg.register_event("test", _noop)
        reg.unregister_event("test", _noop)
        assert len(reg._event_listeners["test"]) == 0

    def test_unregister_event_not_registered(self):
        reg = ScriptingRegistry()
        reg.unregister_event("test", _noop)  # Should not raise

    def test_fire_event(self):
        reg = ScriptingRegistry()
        received = []
        done = threading.Event()

        def handler(data):
            received.append(data)
            done.set()

        reg.register_event("test", handler)
        reg.fire_event("test", key="value")
        done.wait(timeout=2.0)
        assert len(received) == 1
        assert received[0] == {"key": "value"}

    def test_fire_event_multiple_handlers(self):
        reg = ScriptingRegistry()
        results = []
        done = threading.Event()

        def h1(data):
            results.append("h1")

        def h2(data):
            results.append("h2")
            done.set()

        reg.register_event("test", h1)
        reg.register_event("test", h2)
        reg.fire_event("test")
        done.wait(timeout=2.0)
        assert results == ["h1", "h2"]

    def test_fire_event_no_handlers(self):
        reg = ScriptingRegistry()
        reg.fire_event("nonexistent")  # Should not raise

    def test_fire_event_handler_error_does_not_propagate(self):
        reg = ScriptingRegistry()
        done = threading.Event()

        def bad_handler(data):
            done.set()
            raise RuntimeError("boom")

        reg.register_event("test", bad_handler)
        reg.fire_event("test")
        done.wait(timeout=2.0)  # Should not hang

    def test_clear_removes_events(self):
        reg = ScriptingRegistry()
        reg.register_event("test", _noop)
        reg.clear()
        assert len(reg._event_listeners) == 0


class TestLeaderMapping:
    def test_defaults(self):
        m = LeaderMapping(key="w")
        assert m.key == "w"
        assert m.desc == ""
        assert m.app is None
        assert m.func is None
        assert m.exec_cmd is None

    def test_with_app(self):
        m = LeaderMapping(key="w", app="WeChat", desc="WeChat messenger")
        assert m.app == "WeChat"
        assert m.desc == "WeChat messenger"

    def test_with_func(self):
        def hello():
            return "hello"

        m = LeaderMapping(key="d", func=hello, desc="test func")
        assert m.func is hello
