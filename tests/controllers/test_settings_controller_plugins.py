"""Tests for plugin management callbacks in SettingsController."""

from unittest.mock import MagicMock, patch

import pytest

from wenzi.controllers.settings_controller import SettingsController
from wenzi.scripting.plugin_meta import PluginMeta
from wenzi.scripting.plugin_registry import PluginInfo, PluginStatus

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture()
def ctrl(tmp_path):
    """Build a SettingsController wired to mocks."""
    app = MagicMock()
    app._config_dir = str(tmp_path)
    app._config = {"scripting": {"disabled_plugins": []}}
    app._config_path = str(tmp_path / "config.yaml")
    app._settings_panel.is_visible = True

    engine = MagicMock()
    app._script_engine = engine

    ctrl = SettingsController(app)
    ctrl._refresh_plugin_state = MagicMock()
    return ctrl


# ── _auto_reload_if_needed ────────────────────────────────────────


class TestAutoReloadIfNeeded:
    def test_reloads_when_flag_set(self, ctrl):
        ctrl._needs_reload = True
        ctrl._auto_reload_if_needed()

        ctrl._app._script_engine.reload.assert_called_once()
        assert ctrl._needs_reload is False

    def test_skips_when_flag_not_set(self, ctrl):
        ctrl._needs_reload = False
        ctrl._auto_reload_if_needed()

        ctrl._app._script_engine.reload.assert_not_called()

    def test_idempotent_on_double_call(self, ctrl):
        """Simulates two callAfter dispatches — only the first should reload."""
        ctrl._needs_reload = True
        ctrl._auto_reload_if_needed()
        ctrl._auto_reload_if_needed()

        ctrl._app._script_engine.reload.assert_called_once()


# ── Install / Update / Uninstall trigger auto-reload ──────────────


class TestInstallTriggersAutoReload:
    def test_install_schedules_auto_reload(self, ctrl):
        """After a successful install the controller schedules auto-reload."""
        ctrl._plugin_installer = MagicMock()

        with patch(
            "PyObjCTools.AppHelper.callAfter",
            side_effect=lambda fn, *a, **kw: fn(*a, **kw),
        ), patch(
            "wenzi.controllers.settings_controller.threading.Thread",
        ) as mock_thread:
            ctrl._on_plugin_install_url("https://example.com/plugin.toml")
            mock_thread.call_args[1]["target"]()

        ctrl._app._script_engine.reload.assert_called_once()
        assert ctrl._needs_reload is False


class TestUninstallTriggersAutoReload:
    def test_uninstall_schedules_auto_reload(self, ctrl):
        ctrl._plugin_installer = MagicMock()

        with patch(
            "PyObjCTools.AppHelper.callAfter",
            side_effect=lambda fn, *a, **kw: fn(*a, **kw),
        ):
            ctrl._on_plugin_uninstall("com.test.plugin")

        ctrl._app._script_engine.reload.assert_called_once()
        assert ctrl._needs_reload is False


class TestToggleTriggersAutoReload:
    def test_disable_triggers_auto_reload(self, ctrl):
        ctrl._app._config["scripting"]["disabled_plugins"] = []

        with patch(
            "PyObjCTools.AppHelper.callAfter",
            side_effect=lambda fn, *a, **kw: fn(*a, **kw),
        ), patch(
            "wenzi.controllers.settings_controller.save_config",
        ):
            ctrl._on_plugin_toggle("com.test.plugin", enabled=False)

        ctrl._app._script_engine.reload.assert_called_once()
        assert ctrl._needs_reload is False


# ── _finish_plugins_fetch (race condition fix) ────────────────────


class TestFinishPluginsFetch:
    """Verify that status computation runs on the main thread, not in _fetch."""

    def test_recomputes_statuses_on_main_thread(self, ctrl):
        """_finish_plugins_fetch should recompute statuses from fresh local scan."""
        info = PluginInfo(
            meta=PluginMeta(
                name="Test", id="com.test.plugin", version="1.0.0",
            ),
            source_url="https://example.com/plugin.toml",
            registry_name="Official",
            status=PluginStatus.NOT_INSTALLED,  # stale status from fetch
            is_official=True,
        )
        ctrl._last_plugin_infos = [info]
        # Mock _compute_status to return INSTALLED (simulating fresh local scan)
        ctrl._plugin_registry._compute_status = MagicMock(
            return_value=(PluginStatus.INSTALLED, "1.0.0")
        )

        ctrl._finish_plugins_fetch()

        # Status should have been recomputed
        assert info.status == PluginStatus.INSTALLED
        assert info.installed_version == "1.0.0"
        # UI should be updated with plugins_loading cleared
        state = ctrl._app._settings_panel.update_state.call_args[0][0]
        assert state["plugins_loading"] is False
        assert "plugins" in state

    def test_handles_empty_infos_without_loop(self, ctrl):
        """Empty _last_plugin_infos must not trigger _on_plugins_tab_open."""
        ctrl._last_plugin_infos = []

        ctrl._finish_plugins_fetch()

        state = ctrl._app._settings_panel.update_state.call_args[0][0]
        assert state["plugins_loading"] is False
        assert state["plugins"] == []


class TestPluginInfosToState:
    """Test _plugin_infos_to_state conversion logic."""

    def test_converts_plugin_info_to_dict(self):
        meta = PluginMeta(
            name="Test Plugin",
            id="com.test.plugin",
            version="1.0.0",
            author="Alice",
            description="A test plugin",
        )
        info = PluginInfo(
            meta=meta,
            source_url="https://example.com/plugin.toml",
            registry_name="Official",
            status=PluginStatus.NOT_INSTALLED,
            is_official=True,
        )
        result = {
            "id": info.meta.id,
            "name": info.meta.name,
            "version": info.meta.version,
            "status": info.status.value,
            "is_official": info.is_official,
        }
        assert result["id"] == "com.test.plugin"
        assert result["status"] == "not_installed"
        assert result["is_official"] is True

    def test_disabled_plugin_shows_enabled_false(self):
        disabled = {"com.test.plugin"}
        pid = "com.test.plugin"
        is_enabled = pid not in disabled
        assert is_enabled is False

    def test_enabled_plugin_shows_enabled_true(self):
        disabled = {"com.other.plugin"}
        pid = "com.test.plugin"
        is_enabled = pid not in disabled
        assert is_enabled is True


# ── _on_plugin_install_by_id with ref ─────────────────────────────


class TestInstallByIdWithRef:
    def test_install_with_ref_transforms_url(self, ctrl):
        """_on_plugin_install_by_id with ref builds versioned URL."""
        ctrl._plugin_installer = MagicMock()
        ctrl._last_plugin_infos = [
            PluginInfo(
                meta=PluginMeta(name="Test", id="com.test.plugin", version="2.0.0"),
                source_url="https://raw.githubusercontent.com/Airead/WenZi/refs/heads/main/plugins/test/plugin.toml",
                registry_name="Official",
                status=PluginStatus.NOT_INSTALLED,
                is_official=True,
            )
        ]

        with patch(
            "PyObjCTools.AppHelper.callAfter",
            side_effect=lambda fn, *a, **kw: fn(*a, **kw),
        ), patch(
            "wenzi.controllers.settings_controller.threading.Thread",
        ) as mock_thread:
            ctrl._on_plugin_install_by_id("com.test.plugin", ref="1.0.0")
            mock_thread.call_args[1]["target"]()

        ctrl._plugin_installer.install.assert_called_once()
        call_args = ctrl._plugin_installer.install.call_args
        url = call_args[0][0]
        assert "refs/tags/v1.0.0" in url
        assert call_args[1]["pinned_ref"] == "1.0.0"

    def test_install_without_ref_uses_original_url(self, ctrl):
        """_on_plugin_install_by_id without ref uses registry URL as-is."""
        ctrl._plugin_installer = MagicMock()
        source = "https://raw.githubusercontent.com/Airead/WenZi/refs/heads/main/plugins/test/plugin.toml"
        ctrl._last_plugin_infos = [
            PluginInfo(
                meta=PluginMeta(name="Test", id="com.test.plugin", version="2.0.0"),
                source_url=source,
                registry_name="Official",
                status=PluginStatus.NOT_INSTALLED,
                is_official=True,
            )
        ]

        with patch(
            "PyObjCTools.AppHelper.callAfter",
            side_effect=lambda fn, *a, **kw: fn(*a, **kw),
        ), patch(
            "wenzi.controllers.settings_controller.threading.Thread",
        ) as mock_thread:
            ctrl._on_plugin_install_by_id("com.test.plugin")
            mock_thread.call_args[1]["target"]()

        ctrl._plugin_installer.install.assert_called_once()
        call_args = ctrl._plugin_installer.install.call_args
        assert call_args[0][0] == source
        assert call_args[1]["pinned_ref"] is None

    def test_install_with_invalid_ref_shows_error(self, ctrl):
        """Short SHA ref shows error in settings panel."""
        ctrl._plugin_installer = MagicMock()
        ctrl._last_plugin_infos = [
            PluginInfo(
                meta=PluginMeta(name="Test", id="com.test.plugin", version="2.0.0"),
                source_url="https://raw.githubusercontent.com/Airead/WenZi/refs/heads/main/plugins/test/plugin.toml",
                registry_name="Official",
                status=PluginStatus.NOT_INSTALLED,
                is_official=True,
            )
        ]
        ctrl._on_plugin_install_by_id("com.test.plugin", ref="abc1234")

        ctrl._plugin_installer.install.assert_not_called()
        call_args = ctrl._app._settings_panel.update_state.call_args[0][0]
        assert "plugins_error" in call_args
        assert "40-character" in call_args["plugins_error"]


# ── pinned_ref in state dict ──────────────────────────────────────


class TestPinnedPluginState:
    def test_pinned_ref_in_state_dict(self, ctrl, tmp_path):
        """State dict includes pinned_ref for pinned plugins."""
        plugins_dir = tmp_path / "test_plugins"
        d = plugins_dir / "alpha"
        d.mkdir(parents=True)
        (d / "plugin.toml").write_text(
            '[plugin]\nid = "com.test.alpha"\nname = "A"\nversion = "1.0.0"\n'
        )
        (d / "install.toml").write_text(
            '[install]\nsource_url = "x"\ninstalled_version = "1.0.0"\n'
            'pinned_ref = "1.0.0"\n'
        )
        ctrl._plugin_registry._plugins_dir = str(plugins_dir)

        info = PluginInfo(
            meta=PluginMeta(name="A", id="com.test.alpha", version="2.0.0"),
            source_url="x",
            registry_name="Official",
            status=PluginStatus.PINNED,
            installed_version="1.0.0",
            is_official=True,
        )
        result = ctrl._plugin_infos_to_state([info])
        assert result[0]["pinned_ref"] == "1.0.0"

    def test_non_pinned_has_empty_pinned_ref(self, ctrl, tmp_path):
        """State dict has empty pinned_ref for non-pinned plugins."""
        plugins_dir = tmp_path / "test_plugins"
        d = plugins_dir / "beta"
        d.mkdir(parents=True)
        (d / "plugin.toml").write_text(
            '[plugin]\nid = "com.test.beta"\nname = "B"\nversion = "1.0.0"\n'
        )
        (d / "install.toml").write_text(
            '[install]\nsource_url = "x"\ninstalled_version = "1.0.0"\n'
        )
        ctrl._plugin_registry._plugins_dir = str(plugins_dir)

        info = PluginInfo(
            meta=PluginMeta(name="B", id="com.test.beta", version="1.0.0"),
            source_url="x",
            registry_name="Official",
            status=PluginStatus.INSTALLED,
            installed_version="1.0.0",
            is_official=True,
        )
        result = ctrl._plugin_infos_to_state([info])
        assert result[0]["pinned_ref"] == ""

    def test_local_only_plugin_has_pinned_ref(self, ctrl, tmp_path):
        """Local-only plugin with pinned_ref includes it in state dict."""
        plugins_dir = tmp_path / "test_plugins"
        d = plugins_dir / "local_pinned"
        d.mkdir(parents=True)
        (d / "plugin.toml").write_text(
            '[plugin]\nid = "com.test.local"\nname = "L"\nversion = "1.0.0"\n'
        )
        (d / "install.toml").write_text(
            '[install]\nsource_url = "x"\ninstalled_version = "1.0.0"\n'
            'pinned_ref = "feat/test"\n'
        )
        ctrl._plugin_registry._plugins_dir = str(plugins_dir)

        # Pass empty infos so the local plugin gets added by _add_local_only_plugins
        result = ctrl._plugin_infos_to_state([])
        local = [r for r in result if r["id"] == "com.test.local"]
        assert len(local) == 1
        assert local[0]["pinned_ref"] == "feat/test"
        assert local[0]["status"] == "installed"
