"""Tests for vt namespace object."""

from unittest.mock import patch

from voicetext.scripting.registry import ScriptingRegistry
from voicetext.scripting.api import _VTNamespace


class TestVTNamespace:
    def test_attributes_exist(self):
        reg = ScriptingRegistry()
        vt = _VTNamespace(reg)
        assert hasattr(vt, "app")
        assert hasattr(vt, "pasteboard")
        assert hasattr(vt, "timer")
        assert hasattr(vt, "hotkey")
        assert callable(vt.leader)
        assert callable(vt.alert)
        assert callable(vt.notify)
        assert callable(vt.keystroke)
        assert callable(vt.execute)
        assert callable(vt.date)
        assert callable(vt.reload)

    def test_leader_parses_dicts(self):
        reg = ScriptingRegistry()
        vt = _VTNamespace(reg)
        vt.leader("cmd_r", [
            {"key": "w", "app": "WeChat"},
            {"key": "d", "desc": "date", "func": lambda: None},
            {"key": "i", "exec": "/usr/local/bin/code ~/work"},
        ])
        assert "cmd_r" in reg.leaders
        mappings = reg.leaders["cmd_r"].mappings
        assert len(mappings) == 3
        assert mappings[0].app == "WeChat"
        assert mappings[1].func is not None
        assert mappings[2].exec_cmd == "/usr/local/bin/code ~/work"

    def test_date_format(self):
        reg = ScriptingRegistry()
        vt = _VTNamespace(reg)
        result = vt.date("%Y")
        assert len(result) == 4
        assert result.isdigit()

    def test_date_default_format(self):
        reg = ScriptingRegistry()
        vt = _VTNamespace(reg)
        result = vt.date()
        assert len(result) == 10
        assert result[4] == "-"

    def test_reload_callback(self):
        reg = ScriptingRegistry()
        vt = _VTNamespace(reg)
        called = []
        vt._reload_callback = lambda: called.append(1)
        vt.reload()
        assert called == [1]

    @patch("voicetext.statusbar.send_notification")
    def test_notify(self, mock_send):
        reg = ScriptingRegistry()
        vt = _VTNamespace(reg)
        vt.notify("Test", "msg")
        mock_send.assert_called_once_with("Test", "", "msg")

    @patch("voicetext.scripting.api.execute._run")
    def test_execute(self, mock_run):
        mock_run.return_value = "ok"
        reg = ScriptingRegistry()
        vt = _VTNamespace(reg)
        result = vt.execute("echo hi", background=False)
        assert result == "ok"
