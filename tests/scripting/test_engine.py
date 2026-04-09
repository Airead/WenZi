"""Tests for script engine."""

import os
import sys
import types
from unittest.mock import patch

from wenzi.scripting.engine import ScriptEngine


class TestScriptEngine:
    def test_init_creates_wz(self):
        engine = ScriptEngine(script_dir="/tmp/wz_test_scripts", plugins_dir="/tmp/nonexistent_vt_plugins")
        assert engine.wz is not None
        assert engine.wz._reload_callback is not None

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_load_nonexistent_dir(self, mock_stop, mock_start):
        engine = ScriptEngine(script_dir="/tmp/nonexistent_vt_scripts", plugins_dir="/tmp/nonexistent_vt_plugins")
        engine.start()
        engine.stop()

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_load_script(self, mock_stop, mock_start, tmp_path):
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        init_py = script_dir / "init.py"
        init_py.write_text(
            'wz.leader("cmd_r", [{"key": "w", "app": "WeChat"}])\n'
        )

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        engine = ScriptEngine(script_dir=str(script_dir), plugins_dir=str(plugins_dir))
        engine.start()

        assert "cmd_r" in engine._registry.leaders
        assert engine._registry.leaders["cmd_r"].mappings[0].app == "WeChat"

        engine.stop()

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_load_script_with_error(self, mock_stop, mock_start, tmp_path):
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        init_py = script_dir / "init.py"
        init_py.write_text("raise ValueError('test error')\n")

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        engine = ScriptEngine(script_dir=str(script_dir), plugins_dir=str(plugins_dir))
        # Should not raise, error is caught
        with patch("wenzi.scripting.engine.logger") as mock_logger:
            engine.start()
            mock_logger.error.assert_called()
        engine.stop()

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_reload(self, mock_stop, mock_start, tmp_path):
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        init_py = script_dir / "init.py"
        init_py.write_text(
            'wz.leader("cmd_r", [{"key": "w", "app": "WeChat"}])\n'
        )

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        engine = ScriptEngine(
            script_dir=str(script_dir),
            plugins_dir=str(plugins_dir),
        )
        engine.start()
        assert "cmd_r" in engine._registry.leaders

        # Modify script
        init_py.write_text(
            'wz.leader("alt_r", [{"key": "s", "app": "Slack"}])\n'
        )
        engine.reload()
        assert "alt_r" in engine._registry.leaders
        assert "cmd_r" not in engine._registry.leaders

        engine.stop()

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_reload_skips_when_already_reloading(self, mock_stop, mock_start, tmp_path):
        """Concurrent reload calls should be skipped to prevent race conditions."""
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        init_py = script_dir / "init.py"
        init_py.write_text("")

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        engine = ScriptEngine(
            script_dir=str(script_dir),
            plugins_dir=str(plugins_dir),
        )
        engine.start()

        # Simulate reload already in progress
        engine._reloading = True
        engine.reload()
        # stop() is only called once (from start), not again from reload
        mock_stop.assert_not_called()

        engine._reloading = False
        engine.stop()

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
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
            plugins_dir="/tmp/nonexistent_vt_plugins",
            config=config,
        )
        engine.start()

        # No sources should be registered on the chooser panel
        panel = engine.wz.chooser._get_panel()
        assert len(panel._sources) == 0

        # Clipboard monitor should not be started
        assert engine._clipboard_monitor is None

        engine.stop()

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
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
            plugins_dir="/tmp/nonexistent_vt_plugins",
            config=config,
        )
        engine.start()

        # App source should be registered
        panel = engine.wz.chooser._get_panel()
        assert len(panel._sources) > 0

        engine.stop()

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_enable_clipboard_at_runtime(self, mock_stop, mock_start):
        """enable_clipboard() starts monitor and registers source."""
        config = {
            "chooser": {
                "enabled": True,
                "clipboard_history": False,
                "app_search": False,
                "file_search": False,
                "snippets": False,
                "bookmarks": False,
                "usage_learning": False,
            },
        }
        engine = ScriptEngine(
            script_dir="/tmp/nonexistent_vt_scripts",
            plugins_dir="/tmp/nonexistent_vt_plugins",
            config=config,
        )
        engine.start()

        # Clipboard should not be running
        assert engine._clipboard_monitor is None
        panel = engine.wz.chooser._get_panel()
        assert "clipboard" not in panel._sources

        # Enable at runtime
        engine.enable_clipboard()
        assert engine._clipboard_monitor is not None
        assert "clipboard" in panel._sources

        # Calling again should be a no-op (not raise)
        engine.enable_clipboard()

        engine.stop()

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_disable_clipboard_at_runtime(self, mock_stop, mock_start):
        """disable_clipboard() stops monitor and unregisters source."""
        config = {
            "chooser": {
                "enabled": True,
                "clipboard_history": True,
                "app_search": False,
                "file_search": False,
                "snippets": False,
                "bookmarks": False,
                "usage_learning": False,
            },
        }
        engine = ScriptEngine(
            script_dir="/tmp/nonexistent_vt_scripts",
            plugins_dir="/tmp/nonexistent_vt_plugins",
            config=config,
        )
        engine.start()

        # Clipboard should be running
        assert engine._clipboard_monitor is not None
        panel = engine.wz.chooser._get_panel()
        assert "clipboard" in panel._sources

        # Disable at runtime
        engine.disable_clipboard()
        assert engine._clipboard_monitor is None
        assert "clipboard" not in panel._sources

        # Calling again should be a no-op (not raise)
        engine.disable_clipboard()

        engine.stop()

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_disable_chooser_at_runtime(self, mock_stop, mock_start):
        """disable_chooser() clears all sources, monitors, and hotkeys."""
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
            plugins_dir="/tmp/nonexistent_vt_plugins",
            config=config,
        )
        engine.start()

        panel = engine.wz.chooser._get_panel()
        assert len(panel._sources) > 0

        engine.disable_chooser()
        assert len(panel._sources) == 0
        assert engine._clipboard_monitor is None
        assert engine._snippet_store is None
        assert engine._usage_tracker is None

        engine.stop()

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_enable_chooser_at_runtime(self, mock_stop, mock_start):
        """enable_chooser() re-registers sources after disable."""
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
            plugins_dir="/tmp/nonexistent_vt_plugins",
            config=config,
        )
        engine.start()

        panel = engine.wz.chooser._get_panel()
        engine.disable_chooser()
        assert len(panel._sources) == 0

        # Re-enable — must bypass the config "enabled" check
        # since disable_chooser doesn't change config
        engine.enable_chooser()
        assert len(panel._sources) > 0

        engine.stop()

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_enable_disable_source_at_runtime(self, mock_stop, mock_start):
        """enable_source / disable_source toggle individual sources."""
        config = {
            "chooser": {
                "enabled": True,
                "hotkey": "cmd+space",
                "app_search": False,
                "clipboard_history": False,
                "file_search": False,
                "snippets": False,
                "bookmarks": False,
                "usage_learning": False,
            },
        }
        engine = ScriptEngine(
            script_dir="/tmp/nonexistent_vt_scripts",
            plugins_dir="/tmp/nonexistent_vt_plugins",
            config=config,
        )
        engine.start()

        panel = engine.wz.chooser._get_panel()
        assert "apps" not in panel._sources

        engine.enable_source("app_search")
        assert "apps" in panel._sources

        engine.disable_source("app_search")
        assert "apps" not in panel._sources

        engine.stop()

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_rebind_chooser_hotkey(self, mock_stop, mock_start):
        """rebind_chooser_hotkey unbinds old and binds new hotkey."""
        config = {
            "chooser": {
                "enabled": True,
                "hotkey": "cmd+space",
                "app_search": False,
                "clipboard_history": False,
                "file_search": False,
                "snippets": False,
                "bookmarks": False,
                "usage_learning": False,
            },
        }
        engine = ScriptEngine(
            script_dir="/tmp/nonexistent_vt_scripts",
            plugins_dir="/tmp/nonexistent_vt_plugins",
            config=config,
        )
        engine.start()

        # Rebind should not raise
        engine.rebind_chooser_hotkey("cmd+space", "alt+space")

        engine.stop()

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_set_usage_learning_at_runtime(self, mock_stop, mock_start):
        """set_usage_learning toggles the tracker on the panel."""
        config = {
            "chooser": {
                "enabled": True,
                "app_search": False,
                "clipboard_history": False,
                "file_search": False,
                "snippets": False,
                "bookmarks": False,
                "usage_learning": False,
            },
        }
        engine = ScriptEngine(
            script_dir="/tmp/nonexistent_vt_scripts",
            plugins_dir="/tmp/nonexistent_vt_plugins",
            config=config,
        )
        engine.start()

        panel = engine.wz.chooser._get_panel()
        assert panel._usage_tracker is None

        engine.set_usage_learning(True)
        assert panel._usage_tracker is not None

        engine.set_usage_learning(False)
        assert panel._usage_tracker is None

        engine.stop()

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_applies_recycle_mode_to_panel(self, mock_stop, mock_start):
        config = {
            "chooser": {
                "enabled": True,
                "app_search": False,
                "clipboard_history": False,
                "file_search": False,
                "snippets": False,
                "bookmarks": False,
                "usage_learning": False,
                "recycle_mode": "preload_html",
            },
        }
        engine = ScriptEngine(
            script_dir="/tmp/nonexistent_vt_scripts",
            plugins_dir="/tmp/nonexistent_vt_plugins",
            config=config,
        )
        engine.start()

        panel = engine.wz.chooser._get_panel()
        assert panel._recycle_mode == "preload_html"

        engine.stop()

    def test_wz_module_singleton(self):
        engine = ScriptEngine(script_dir="/tmp/wz_test_scripts", plugins_dir="/tmp/nonexistent_vt_plugins")
        import wenzi.scripting.api as api_mod

        assert api_mod.wz is engine.wz


class TestMultiFileScripts:
    """Tests for multi-file script support and hot-reload."""

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_scripts_dir_added_to_sys_path(self, mock_stop, mock_start, tmp_path):
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        (script_dir / "init.py").write_text("pass\n")
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        engine = ScriptEngine(script_dir=str(script_dir), plugins_dir=str(plugins_dir))
        engine.start()
        try:
            assert os.path.normpath(str(script_dir)) in sys.path
        finally:
            engine.stop()
            sys.path[:] = [p for p in sys.path if p != os.path.normpath(str(script_dir))]

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_multi_file_import(self, mock_stop, mock_start, tmp_path):
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        (script_dir / "helper.py").write_text("MAGIC = 42\n")
        (script_dir / "init.py").write_text(
            "import helper\nwz._test_magic = helper.MAGIC\n"
        )
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        engine = ScriptEngine(script_dir=str(script_dir), plugins_dir=str(plugins_dir))
        engine.start()
        try:
            assert engine.wz._test_magic == 42
        finally:
            engine.stop()
            sys.path[:] = [p for p in sys.path if p != os.path.normpath(str(script_dir))]
            sys.modules.pop("helper", None)

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_reload_picks_up_submodule_changes(self, mock_stop, mock_start, tmp_path):
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        (script_dir / "helper.py").write_text('VALUE = "old"\n')
        (script_dir / "init.py").write_text(
            "import helper\nwz._test_val = helper.VALUE\n"
        )

        engine = ScriptEngine(
            script_dir=str(script_dir),
            plugins_dir=str(plugins_dir),
        )
        engine.start()
        try:
            assert engine.wz._test_val == "old"

            # Modify sub-module and reload
            (script_dir / "helper.py").write_text('VALUE = "new"\n')
            engine.reload()
            assert engine.wz._test_val == "new"
        finally:
            engine.stop()
            sys.path[:] = [p for p in sys.path if p != os.path.normpath(str(script_dir))]
            sys.modules.pop("helper", None)

    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.start")
    @patch("wenzi.scripting.api.hotkey.HotkeyAPI.stop")
    def test_reload_with_package(self, mock_stop, mock_start, tmp_path):
        """Reload picks up changes in sub-package modules."""
        script_dir = tmp_path / "scripts"
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        pkg_dir = script_dir / "mypkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / "util.py").write_text('MSG = "hello"\n')
        (script_dir / "init.py").write_text(
            "from mypkg.util import MSG\nwz._test_msg = MSG\n"
        )

        engine = ScriptEngine(
            script_dir=str(script_dir),
            plugins_dir=str(plugins_dir),
        )
        engine.start()
        norm_dir = os.path.normpath(str(script_dir))
        try:
            assert engine.wz._test_msg == "hello"

            (pkg_dir / "util.py").write_text('MSG = "world"\n')
            engine.reload()
            assert engine.wz._test_msg == "world"
        finally:
            engine.stop()
            sys.path[:] = [p for p in sys.path if p != norm_dir]
            for name in list(sys.modules):
                if name.startswith("mypkg"):
                    del sys.modules[name]

    def test_purge_only_removes_user_modules(self, tmp_path):
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        engine = ScriptEngine(
            script_dir=str(script_dir),
            plugins_dir=str(plugins_dir),
        )

        # Inject a fake user module under scripts dir
        fake_mod = types.ModuleType("_wz_test_fake")
        fake_mod.__file__ = str(script_dir / "fake.py")
        sys.modules["_wz_test_fake"] = fake_mod

        try:
            engine._purge_user_modules()

            # User module should be purged
            assert "_wz_test_fake" not in sys.modules
            # Stdlib module should remain
            assert "json" in sys.modules or "os" in sys.modules
        finally:
            sys.modules.pop("_wz_test_fake", None)

    def test_purge_no_false_positive_on_sibling_dir(self, tmp_path):
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        sibling_dir = tmp_path / "scripts-extra"
        sibling_dir.mkdir()

        engine = ScriptEngine(script_dir=str(script_dir), plugins_dir="/tmp/nonexistent_vt_plugins")

        fake_mod = types.ModuleType("_wz_test_sibling")
        fake_mod.__file__ = str(sibling_dir / "foo.py")
        sys.modules["_wz_test_sibling"] = fake_mod

        try:
            engine._purge_user_modules()
            # Module in sibling dir must NOT be purged
            assert "_wz_test_sibling" in sys.modules
        finally:
            sys.modules.pop("_wz_test_sibling", None)

    def test_purge_handles_namespace_package(self, tmp_path):
        script_dir = tmp_path / "scripts"
        ns_dir = script_dir / "nspkg"
        ns_dir.mkdir(parents=True)

        engine = ScriptEngine(script_dir=str(script_dir), plugins_dir="/tmp/nonexistent_vt_plugins")

        # Namespace package: has __path__ but no __file__
        fake_ns = types.ModuleType("_wz_test_nspkg")
        fake_ns.__file__ = None
        fake_ns.__path__ = [str(ns_dir)]
        sys.modules["_wz_test_nspkg"] = fake_ns

        try:
            engine._purge_user_modules()
            assert "_wz_test_nspkg" not in sys.modules
        finally:
            sys.modules.pop("_wz_test_nspkg", None)
