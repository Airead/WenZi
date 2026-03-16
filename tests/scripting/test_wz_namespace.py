"""Tests for wz namespace object."""

from unittest.mock import patch

from wenzi.scripting.registry import ScriptingRegistry
from wenzi.scripting.api import _WZNamespace


class TestWZNamespace:
    def test_attributes_exist(self):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        assert hasattr(wz, "app")
        assert hasattr(wz, "pasteboard")
        assert hasattr(wz, "timer")
        assert hasattr(wz, "store")
        assert hasattr(wz, "hotkey")
        assert callable(wz.leader)
        assert callable(wz.alert)
        assert callable(wz.notify)
        assert callable(wz.keystroke)
        assert callable(wz.execute)
        assert callable(wz.date)
        assert callable(wz.reload)
        assert callable(wz.on)
        assert callable(wz.type_text)

    def test_leader_parses_dicts(self):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        wz.leader("cmd_r", [
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

    def test_leader_default_position(self):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        wz.leader("cmd_r", [{"key": "w", "app": "WeChat"}])
        assert reg.leaders["cmd_r"].position == "center"

    def test_leader_custom_position(self):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        wz.leader("alt_r", [{"key": "w", "app": "WeChat"}], position="mouse")
        assert reg.leaders["alt_r"].position == "mouse"

    def test_leader_tuple_position(self):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        wz.leader("shift_r", [{"key": "w", "app": "WeChat"}], position=(0.5, 0.8))
        assert reg.leaders["shift_r"].position == (0.5, 0.8)

    def test_date_format(self):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        result = wz.date("%Y")
        assert len(result) == 4
        assert result.isdigit()

    def test_date_default_format(self):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        result = wz.date()
        assert len(result) == 10
        assert result[4] == "-"

    def test_reload_callback(self):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        called = []
        wz._reload_callback = lambda: called.append(1)
        wz.reload()
        assert called == [1]

    @patch("wenzi.statusbar.send_notification")
    def test_notify(self, mock_send):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        wz.notify("Test", "msg")
        mock_send.assert_called_once_with("Test", "", "msg")

    @patch("wenzi.scripting.api.execute._run")
    def test_execute_returns_dict(self, mock_run):
        mock_run.return_value = {"stdout": "ok", "stderr": "", "returncode": 0}
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        result = wz.execute("echo hi", background=False)
        assert result == {"stdout": "ok", "stderr": "", "returncode": 0}

    @patch("wenzi.scripting.api.execute._run")
    def test_execute_passes_timeout(self, mock_run):
        mock_run.return_value = {"stdout": "", "stderr": "", "returncode": 0}
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        wz.execute("cmd", background=False, timeout=60)
        mock_run.assert_called_once_with("cmd", timeout=60)

    def test_on_registers_event(self):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)

        def handler(data):
            pass

        result = wz.on("test_event", handler)
        assert result is handler
        assert handler in reg._event_listeners["test_event"]

    def test_on_as_decorator(self):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)

        @wz.on("transcription_done")
        def handler(data):
            pass

        assert handler in reg._event_listeners["transcription_done"]

    @patch("wenzi.input.type_text")
    def test_type_text_auto(self, mock_type):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        wz.type_text("hello")
        mock_type.assert_called_once_with("hello", method="auto")

    @patch("wenzi.input.type_text")
    def test_type_text_paste_method(self, mock_type):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        wz.type_text("hello", method="paste")
        mock_type.assert_called_once_with("hello", method="clipboard")

    @patch("wenzi.input.type_text")
    def test_type_text_key_method(self, mock_type):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        wz.type_text("hello", method="key")
        mock_type.assert_called_once_with("hello", method="applescript")

    def test_store_is_store_api(self):
        from wenzi.scripting.api.store import StoreAPI
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        assert isinstance(wz.store, StoreAPI)
