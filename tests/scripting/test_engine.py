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

    def test_vt_module_singleton(self):
        engine = ScriptEngine(script_dir="/tmp/vt_test_scripts")
        import voicetext.scripting.api as api_mod

        assert api_mod.vt is engine.vt
