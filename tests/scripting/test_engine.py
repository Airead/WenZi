"""Tests for script engine."""

from unittest.mock import patch

from voicetext.scripting.engine import ScriptEngine


class TestScriptEngine:
    def test_init_creates_vt(self):
        engine = ScriptEngine(script_dir="/tmp/vt_test_scripts")
        assert engine.vt is not None
        assert engine.vt._reload_callback is not None

    @patch("voicetext.scripting.api.hotkey.HotkeyAPI.start")
    @patch("voicetext.scripting.api.hotkey.HotkeyAPI.stop")
    def test_load_nonexistent_dir(self, mock_stop, mock_start):
        engine = ScriptEngine(script_dir="/tmp/nonexistent_vt_scripts")
        engine.start()
        engine.stop()

    @patch("voicetext.scripting.api.hotkey.HotkeyAPI.start")
    @patch("voicetext.scripting.api.hotkey.HotkeyAPI.stop")
    def test_load_script(self, mock_stop, mock_start, tmp_path):
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        init_py = script_dir / "init.py"
        init_py.write_text(
            'vt.leader("cmd_r", [{"key": "w", "app": "WeChat"}])\n'
        )

        engine = ScriptEngine(script_dir=str(script_dir))
        engine.start()

        assert "cmd_r" in engine._registry.leaders
        assert engine._registry.leaders["cmd_r"].mappings[0].app == "WeChat"

        engine.stop()

    @patch("voicetext.scripting.api.hotkey.HotkeyAPI.start")
    @patch("voicetext.scripting.api.hotkey.HotkeyAPI.stop")
    def test_load_script_with_error(self, mock_stop, mock_start, tmp_path):
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        init_py = script_dir / "init.py"
        init_py.write_text("raise ValueError('test error')\n")

        engine = ScriptEngine(script_dir=str(script_dir))
        # Should not raise, error is caught
        with patch("voicetext.scripting.engine.logger") as mock_logger:
            engine.start()
            mock_logger.error.assert_called()
        engine.stop()

    @patch("voicetext.scripting.api.hotkey.HotkeyAPI.start")
    @patch("voicetext.scripting.api.hotkey.HotkeyAPI.stop")
    def test_reload(self, mock_stop, mock_start, tmp_path):
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        init_py = script_dir / "init.py"
        init_py.write_text(
            'vt.leader("cmd_r", [{"key": "w", "app": "WeChat"}])\n'
        )

        engine = ScriptEngine(script_dir=str(script_dir))
        engine.start()
        assert "cmd_r" in engine._registry.leaders

        # Modify script
        init_py.write_text(
            'vt.leader("alt_r", [{"key": "s", "app": "Slack"}])\n'
        )
        engine.reload()
        assert "alt_r" in engine._registry.leaders
        assert "cmd_r" not in engine._registry.leaders

        engine.stop()

    @patch("voicetext.scripting.api.hotkey.HotkeyAPI.start")
    @patch("voicetext.scripting.api.hotkey.HotkeyAPI.stop")
    def test_chooser_disabled_skips_sources_and_hotkeys(self, mock_stop, mock_start):
        """When chooser.enabled is False, no sources are registered and no hotkeys bound."""
        config = {
            "chooser": {
                "enabled": False,
                "hotkey": "cmd+space",
                "app_search": True,
                "clipboard_history": True,
            },
        }
        engine = ScriptEngine(
            script_dir="/tmp/nonexistent_vt_scripts",
            config=config,
        )
        engine.start()

        # No sources should be registered on the chooser panel
        panel = engine.vt.chooser._get_panel()
        assert len(panel._sources) == 0

        # Clipboard monitor should not be started
        assert engine._clipboard_monitor is None

        engine.stop()

    @patch("voicetext.scripting.api.hotkey.HotkeyAPI.start")
    @patch("voicetext.scripting.api.hotkey.HotkeyAPI.stop")
    def test_chooser_enabled_registers_sources(self, mock_stop, mock_start):
        """When chooser.enabled is True (default), sources are registered normally."""
        config = {
            "chooser": {
                "enabled": True,
                "hotkey": "cmd+space",
                "app_search": True,
                "clipboard_history": False,
                "file_search": False,
                "snippets": False,
                "bookmarks": False,
                "usage_learning": False,
            },
        }
        engine = ScriptEngine(
            script_dir="/tmp/nonexistent_vt_scripts",
            config=config,
        )
        engine.start()

        # App source should be registered
        panel = engine.vt.chooser._get_panel()
        assert len(panel._sources) > 0

        engine.stop()

    def test_vt_module_singleton(self):
        engine = ScriptEngine(script_dir="/tmp/vt_test_scripts")
        import voicetext.scripting.api as api_mod

        assert api_mod.vt is engine.vt
