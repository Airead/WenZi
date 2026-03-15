"""Tests for scripting registry."""

import threading

from voicetext.scripting.registry import (
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
        timer_id = reg.register_timer(1.0, _noop, repeating=False)
        assert timer_id in reg.timers
        assert reg.timers[timer_id].interval == 1.0
        assert reg.timers[timer_id].repeating is False

    def test_cancel_timer(self):
        reg = ScriptingRegistry()
        timer_id = reg.register_timer(10.0, _noop)
        # Set a real timer to verify cancel
        entry = reg.timers[timer_id]
        t = threading.Timer(10.0, _noop)
        t.daemon = True
        entry._timer = t
        t.start()

        reg.cancel_timer(timer_id)
        assert timer_id not in reg.timers
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
        timer_id = reg.register_timer(10.0, _noop)

        # Add a real timer
        entry = reg.timers[timer_id]
        t = threading.Timer(10.0, _noop)
        t.daemon = True
        entry._timer = t
        t.start()

        reg.clear()
        assert len(reg.leaders) == 0
        assert len(reg.hotkeys) == 0
        assert len(reg.timers) == 0
        t.join(timeout=1.0)
        assert not t.is_alive()


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
