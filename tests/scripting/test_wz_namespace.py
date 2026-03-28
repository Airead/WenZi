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

    @patch("PyObjCTools.AppHelper")
    def test_reload_callback(self, mock_apphelper):
        """reload() defers the callback via AppHelper.callAfter."""
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        wz._reload_callback = lambda: None
        wz.reload()
        mock_apphelper.callAfter.assert_called_once_with(wz._reload_callback)

    @patch("wenzi.statusbar.send_notification")
    def test_notify(self, mock_send):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        wz.notify("Test", "msg")
        mock_send.assert_called_once_with("Test", "", "msg", sound="default")

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
        registered = reg._event_listeners["test_event"]
        assert len(registered) == 1
        # The registered callback is wrapped by wrap_async
        assert registered[0].__wrapped__ is handler

    def test_on_as_decorator(self):
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)

        @wz.on("transcription_done")
        def handler(data):
            pass

        registered = reg._event_listeners["transcription_done"]
        assert len(registered) == 1
        assert registered[0].__wrapped__ is handler

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

    @patch("wenzi.vault._keychain_get", return_value=None)
    @patch("wenzi.vault._keychain_set", return_value=False)
    def test_keychain_property_exists(self, mock_set, mock_get):
        import wenzi.vault as vault_mod
        vault_mod._vault = None
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        from wenzi.scripting.api.keychain import KeychainAPI

        assert isinstance(wz.keychain, KeychainAPI)
        vault_mod._vault = None

    @patch("wenzi.vault._keychain_get", return_value=None)
    @patch("wenzi.vault._keychain_set", return_value=False)
    def test_keychain_is_lazy_singleton(self, mock_set, mock_get):
        import wenzi.vault as vault_mod
        vault_mod._vault = None
        reg = ScriptingRegistry()
        wz = _WZNamespace(reg)
        kc1 = wz.keychain
        kc2 = wz.keychain
        assert kc1 is kc2
        vault_mod._vault = None
