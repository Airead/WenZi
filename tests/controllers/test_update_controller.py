"""Tests for the update checker controller."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

from wenzi.controllers.update_controller import (
    UpdateController,
    _fetch_latest_release,
    _find_dmg_url,
    _is_frozen,
    _is_newer,
    _parse_version,
)

# --- _is_frozen ---


class TestIsFrozen:
    def test_false_by_default(self):
        assert _is_frozen() is False

    def test_true_when_sys_frozen(self):
        with patch.object(sys, "frozen", True, create=True):
            assert _is_frozen() is True

    def test_true_when_env_set(self):
        with patch.dict("os.environ", {"WENZI_FORCE_AUTO_UPDATE": "1"}):
            assert _is_frozen() is True

    def test_false_when_env_not_1(self):
        with patch.dict("os.environ", {"WENZI_FORCE_AUTO_UPDATE": "0"}):
            assert _is_frozen() is False


# --- Version parsing and comparison ---


class TestParseVersion:
    def test_basic(self):
        assert _parse_version("0.1.2") == (0, 1, 2)

    def test_with_v_prefix(self):
        assert _parse_version("v0.1.2") == (0, 1, 2)

    def test_major_only(self):
        assert _parse_version("3") == (3,)

    def test_empty(self):
        assert _parse_version("") is None

    def test_invalid(self):
        assert _parse_version("abc") is None

    def test_whitespace(self):
        assert _parse_version(" v1.2.3 ") == (1, 2, 3)


class TestIsNewer:
    def test_newer(self):
        assert _is_newer("v0.2.0", "0.1.2") is True

    def test_same(self):
        assert _is_newer("v0.1.2", "0.1.2") is False

    def test_older(self):
        assert _is_newer("v0.1.1", "0.1.2") is False

    def test_major_bump(self):
        assert _is_newer("v1.0.0", "0.9.99") is True

    def test_invalid_latest(self):
        assert _is_newer("invalid", "0.1.2") is False

    def test_invalid_current(self):
        assert _is_newer("v0.1.2", "dev") is False

    def test_both_invalid(self):
        assert _is_newer("abc", "xyz") is False


# --- Fetch latest release ---


class TestFetchLatestRelease:
    def test_success(self):
        mock_data = {"tag_name": "v0.2.0", "html_url": "https://example.com"}
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_data).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("wenzi.controllers.update_controller.urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_latest_release()
        assert result == mock_data

    def test_network_error(self):
        with patch(
            "wenzi.controllers.update_controller.urllib.request.urlopen",
            side_effect=Exception("Connection refused"),
        ):
            assert _fetch_latest_release() is None

    def test_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status = 404
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("wenzi.controllers.update_controller.urllib.request.urlopen", return_value=mock_resp):
            assert _fetch_latest_release() is None


# --- UpdateController ---


def _make_app(config_overrides=None):
    """Create a mock app for UpdateController tests."""
    app = MagicMock()
    config = {"update_check": {"enabled": True, "interval_hours": 6}}
    if config_overrides:
        config["update_check"].update(config_overrides)
    app._config = config

    # Mock menu with proper item tracking
    menu = MagicMock()
    menu_items = {}

    def insert_before(title, item):
        menu_items[item._menuitem.title()] = item

    def delitem(key):
        menu_items.pop(key, None)

    menu.insert_before = insert_before
    menu.__delitem__ = delitem
    menu.__contains__ = lambda self, key: key in menu_items
    app._menu = menu
    return app


class TestUpdateControllerInit:
    def test_enabled_by_default(self):
        app = _make_app()
        ctrl = UpdateController(app)
        assert ctrl.enabled is True

    def test_disabled_from_config(self):
        app = _make_app({"enabled": False})
        ctrl = UpdateController(app)
        assert ctrl.enabled is False

    def test_interval_from_config(self):
        app = _make_app({"interval_hours": 12})
        ctrl = UpdateController(app)
        assert ctrl._interval == 12 * 3600

    def test_interval_minimum_1_hour(self):
        app = _make_app({"interval_hours": 0})
        ctrl = UpdateController(app)
        assert ctrl._interval == 1 * 3600


class TestUpdateControllerStart:
    def test_start_disabled_noop(self):
        app = _make_app({"enabled": False})
        ctrl = UpdateController(app)
        with patch("wenzi.controllers.update_controller.async_loop") as mock_loop:
            ctrl.start()
            mock_loop.get_loop.assert_not_called()

    def test_start_enabled_submits_check(self):
        app = _make_app()
        ctrl = UpdateController(app)
        with patch("wenzi.controllers.update_controller.async_loop") as mock_loop:
            ctrl.start()
            mock_loop.get_loop.assert_called_once()
            mock_loop.get_loop().run_in_executor.assert_called_once_with(
                None, ctrl._check_update,
            )


class TestUpdateControllerCheckUpdate:
    @patch("wenzi.controllers.update_controller._fetch_latest_release")
    @patch("wenzi.controllers.update_controller.async_loop")
    def test_skip_dev_mode(self, mock_async_loop, mock_fetch):
        app = _make_app()
        ctrl = UpdateController(app)

        with patch("wenzi.__version__", "dev"):
            ctrl._check_update()

        mock_fetch.assert_not_called()

    @patch("wenzi.controllers.update_controller._fetch_latest_release")
    @patch("wenzi.controllers.update_controller.async_loop")
    def test_dev_version_env_override(self, mock_async_loop, mock_fetch):
        mock_fetch.return_value = {
            "tag_name": "v99.0.0",
            "html_url": "https://example.com",
        }
        app = _make_app()
        ctrl = UpdateController(app)

        with patch("wenzi.__version__", "dev"), \
             patch.dict("os.environ", {"WENZI_DEV_VERSION": "0.0.1"}), \
             patch("PyObjCTools.AppHelper") as mock_helper:
            ctrl._check_update()
            mock_helper.callAfter.assert_called_once()

    @patch("wenzi.controllers.update_controller._fetch_latest_release")
    @patch("wenzi.controllers.update_controller.async_loop")
    def test_new_version_triggers_menu_update(self, mock_async_loop, mock_fetch):
        mock_fetch.return_value = {
            "tag_name": "v99.0.0",
            "html_url": "https://github.com/Airead/WenZi/releases/tag/v99.0.0",
        }
        app = _make_app()
        ctrl = UpdateController(app)

        with patch("wenzi.__version__", "0.1.2"), \
             patch("PyObjCTools.AppHelper") as mock_helper:
            ctrl._check_update()
            mock_helper.callAfter.assert_called_once()
            call_args = mock_helper.callAfter.call_args
            assert call_args[0][0] == ctrl._apply_update_menu

    @patch("wenzi.controllers.update_controller._fetch_latest_release")
    @patch("wenzi.controllers.update_controller.async_loop")
    def test_same_version_no_menu_update(self, mock_async_loop, mock_fetch):
        mock_fetch.return_value = {
            "tag_name": "v0.1.2",
            "html_url": "https://example.com",
        }
        app = _make_app()
        ctrl = UpdateController(app)

        with patch("wenzi.__version__", "0.1.2"), \
             patch("PyObjCTools.AppHelper") as mock_helper:
            ctrl._check_update()
            mock_helper.callAfter.assert_not_called()

    @patch("wenzi.controllers.update_controller._fetch_latest_release", return_value=None)
    @patch("wenzi.controllers.update_controller.async_loop")
    def test_fetch_failure_no_crash(self, mock_async_loop, mock_fetch):
        app = _make_app()
        ctrl = UpdateController(app)

        with patch("wenzi.__version__", "0.1.2"):
            ctrl._check_update()  # should not raise

    @patch("wenzi.controllers.update_controller._fetch_latest_release")
    @patch("wenzi.controllers.update_controller.async_loop")
    def test_always_schedules_next(self, mock_async_loop, mock_fetch):
        mock_fetch.return_value = None
        app = _make_app()
        ctrl = UpdateController(app)

        with patch("wenzi.__version__", "0.1.2"):
            ctrl._check_update()

        mock_async_loop.call_later.assert_called_once()


class TestUpdateControllerMenuClick:
    def test_click_opens_browser_not_frozen(self):
        """Non-frozen mode always opens browser."""
        app = _make_app()
        ctrl = UpdateController(app)
        ctrl._release_url = "https://github.com/Airead/WenZi/releases/tag/v0.2.0"

        with patch("wenzi.controllers.update_controller.webbrowser.open") as mock_open:
            ctrl._on_update_click(None)
            mock_open.assert_called_once_with(ctrl._release_url)

    def test_click_no_url_noop(self):
        app = _make_app()
        ctrl = UpdateController(app)
        ctrl._release_url = None

        with patch("wenzi.controllers.update_controller.webbrowser.open") as mock_open:
            ctrl._on_update_click(None)
            mock_open.assert_not_called()

    def test_click_frozen_with_dmg_tries_auto_update(self):
        """In frozen mode with a DMG asset, should try auto-update."""
        app = _make_app()
        ctrl = UpdateController(app)
        ctrl._release_url = "https://github.com/Airead/WenZi/releases/tag/v0.2.0"
        ctrl._release_data = {
            "assets": [
                {
                    "name": "WenZi-0.2.0.dmg",
                    "browser_download_url": "https://example.com/WenZi-0.2.0.dmg",
                }
            ]
        }

        with patch.object(sys, "frozen", True, create=True), \
             patch.object(ctrl, "_try_auto_update") as mock_try:
            ctrl._on_update_click(None)
            mock_try.assert_called_once_with("https://example.com/WenZi-0.2.0.dmg")

    def test_click_frozen_no_dmg_opens_browser(self):
        """In frozen mode without DMG asset, falls back to browser."""
        app = _make_app()
        ctrl = UpdateController(app)
        ctrl._release_url = "https://github.com/Airead/WenZi/releases/tag/v0.2.0"
        ctrl._release_data = {"assets": []}

        with patch.object(sys, "frozen", True, create=True), \
             patch("wenzi.controllers.update_controller.webbrowser.open") as mock_open:
            ctrl._on_update_click(None)
            mock_open.assert_called_once_with(ctrl._release_url)


class TestUpdateControllerStop:
    def test_stop_cancels_timer(self):
        app = _make_app()
        ctrl = UpdateController(app)
        mock_timer = MagicMock()
        ctrl._timer = mock_timer
        ctrl.stop()
        mock_timer.cancel.assert_called_once()
        assert ctrl._timer is None

    def test_stop_no_timer_noop(self):
        app = _make_app()
        ctrl = UpdateController(app)
        ctrl.stop()  # should not raise

    def test_stop_cancels_updater(self):
        app = _make_app()
        ctrl = UpdateController(app)
        mock_updater = MagicMock()
        ctrl._updater = mock_updater
        ctrl.stop()
        mock_updater.cancel.assert_called_once()


# --- _find_dmg_url ---


class TestFindDmgUrl:
    def test_finds_standard_dmg(self):
        data = {
            "assets": [
                {
                    "name": "WenZi-0.2.0.dmg",
                    "browser_download_url": "https://example.com/WenZi-0.2.0.dmg",
                },
                {
                    "name": "checksums.txt",
                    "browser_download_url": "https://example.com/checksums.txt",
                },
            ]
        }
        with patch("wenzi.app.get_build_type", return_value="standard"):
            assert _find_dmg_url(data) == "https://example.com/WenZi-0.2.0.dmg"

    def test_finds_lite_dmg(self):
        data = {
            "assets": [
                {
                    "name": "WenZi-0.2.0.dmg",
                    "browser_download_url": "https://example.com/WenZi-0.2.0.dmg",
                },
                {
                    "name": "WenZi-Lite-0.2.0.dmg",
                    "browser_download_url": "https://example.com/WenZi-Lite-0.2.0.dmg",
                },
            ]
        }
        with patch("wenzi.app.get_build_type", return_value="lite"):
            assert _find_dmg_url(data) == "https://example.com/WenZi-Lite-0.2.0.dmg"

    def test_standard_skips_lite_dmg(self):
        data = {
            "assets": [
                {
                    "name": "WenZi-Lite-0.2.0.dmg",
                    "browser_download_url": "https://example.com/WenZi-Lite-0.2.0.dmg",
                },
            ]
        }
        with patch("wenzi.app.get_build_type", return_value="standard"):
            assert _find_dmg_url(data) is None

    def test_lite_skips_standard_dmg(self):
        data = {
            "assets": [
                {
                    "name": "WenZi-0.2.0.dmg",
                    "browser_download_url": "https://example.com/WenZi-0.2.0.dmg",
                },
            ]
        }
        with patch("wenzi.app.get_build_type", return_value="lite"):
            assert _find_dmg_url(data) is None

    def test_no_dmg_asset(self):
        data = {
            "assets": [
                {
                    "name": "source.tar.gz",
                    "browser_download_url": "https://example.com/source.tar.gz",
                }
            ]
        }
        with patch("wenzi.app.get_build_type", return_value="standard"):
            assert _find_dmg_url(data) is None

    def test_empty_assets(self):
        with patch("wenzi.app.get_build_type", return_value="standard"):
            assert _find_dmg_url({"assets": []}) is None

    def test_no_assets_key(self):
        with patch("wenzi.app.get_build_type", return_value="standard"):
            assert _find_dmg_url({}) is None


# --- Auto-update integration ---


class TestAutoUpdateIntegration:
    def test_start_auto_update_creates_updater(self):
        app = _make_app()
        ctrl = UpdateController(app)
        ctrl._latest_version = "v0.2.0"

        with patch("wenzi.updater.AppUpdater") as MockUpdater:
            mock_instance = MagicMock()
            MockUpdater.return_value = mock_instance
            ctrl._start_auto_update("https://example.com/WenZi-0.2.0.dmg")

            MockUpdater.assert_called_once_with(
                dmg_url="https://example.com/WenZi-0.2.0.dmg",
                version="v0.2.0",
                on_progress=ctrl._on_update_progress,
                on_error=ctrl._on_update_error,
                on_ready=ctrl._on_update_ready,
            )
            mock_instance.start.assert_called_once()
            assert ctrl._updater is mock_instance

    def test_start_auto_update_skips_if_already_running(self):
        app = _make_app()
        ctrl = UpdateController(app)
        ctrl._updater = MagicMock()  # already running

        with patch("wenzi.updater.AppUpdater") as MockUpdater:
            ctrl._start_auto_update("https://example.com/WenZi-0.2.0.dmg")
            MockUpdater.assert_not_called()

    def test_set_menu_title(self):
        app = _make_app()
        ctrl = UpdateController(app)
        mock_item = MagicMock()
        ctrl._update_menu_item = mock_item

        ctrl._set_menu_title("Downloading... 42%")
        assert mock_item.title == "Downloading... 42%"

    def test_show_update_error_resets_updater(self):
        app = _make_app()
        ctrl = UpdateController(app)
        ctrl._updater = MagicMock()
        ctrl._latest_version = "v0.2.0"
        mock_item = MagicMock()
        ctrl._update_menu_item = mock_item

        with patch("wenzi.ui_helpers.topmost_alert", return_value=0), \
             patch("wenzi.ui_helpers.restore_accessory"):
            ctrl._show_update_error("Download failed")

        assert ctrl._updater is None

    def test_show_update_error_opens_browser_on_confirm(self):
        app = _make_app()
        ctrl = UpdateController(app)
        ctrl._updater = MagicMock()
        ctrl._release_url = "https://example.com/releases"

        with patch("wenzi.ui_helpers.topmost_alert", return_value=1), \
             patch("wenzi.ui_helpers.restore_accessory"), \
             patch("wenzi.controllers.update_controller.webbrowser.open") as mock_open:
            ctrl._show_update_error("Download failed")
            mock_open.assert_called_once_with("https://example.com/releases")

    def test_apply_restart_menu(self):
        app = _make_app()
        ctrl = UpdateController(app)
        ctrl._latest_version = "v0.2.0"
        mock_item = MagicMock()
        ctrl._update_menu_item = mock_item

        ctrl._apply_restart_menu()
        from wenzi.controllers.update_controller import _RESTART_TITLE_PREFIX
        assert mock_item.title == f"{_RESTART_TITLE_PREFIX} v0.2.0"
        mock_item.set_callback.assert_called_once_with(ctrl._on_restart_to_update)

    def test_restart_to_update_success(self):
        app = _make_app()
        ctrl = UpdateController(app)
        ctrl._latest_version = "v0.2.0"

        with patch("wenzi.ui_helpers.topmost_alert", return_value=1), \
             patch("wenzi.ui_helpers.restore_accessory"), \
             patch("wenzi.updater.AppUpdater") as MockUpdater:
            MockUpdater.perform_swap_and_relaunch.return_value = True
            ctrl._on_restart_to_update(None)
            MockUpdater.perform_swap_and_relaunch.assert_called_once()
            app._on_quit_click.assert_called_once_with(None)

    def test_restart_to_update_cancelled(self):
        app = _make_app()
        ctrl = UpdateController(app)
        ctrl._latest_version = "v0.2.0"

        with patch("wenzi.ui_helpers.topmost_alert", return_value=0), \
             patch("wenzi.ui_helpers.restore_accessory"):
            ctrl._on_restart_to_update(None)
            app._on_quit_click.assert_not_called()

    def test_restart_to_update_swap_fails(self):
        app = _make_app()
        ctrl = UpdateController(app)
        ctrl._latest_version = "v0.2.0"
        ctrl._release_url = "https://example.com/releases"

        with patch("wenzi.ui_helpers.topmost_alert", return_value=1) as mock_alert, \
             patch("wenzi.ui_helpers.restore_accessory"), \
             patch("wenzi.updater.AppUpdater") as MockUpdater, \
             patch("wenzi.controllers.update_controller.webbrowser.open") as mock_open:
            MockUpdater.perform_swap_and_relaunch.return_value = False
            ctrl._on_restart_to_update(None)
            # Should show error alert and open browser
            assert mock_alert.call_count == 2
            mock_open.assert_called_once_with("https://example.com/releases")
            app._on_quit_click.assert_not_called()

    def test_check_update_saves_release_data(self):
        """_check_update should save full release_data for auto-update."""
        release_data = {
            "tag_name": "v99.0.0",
            "html_url": "https://github.com/Airead/WenZi/releases/tag/v99.0.0",
            "assets": [{"name": "WenZi-99.0.0.dmg", "browser_download_url": "..."}],
        }

        with patch("wenzi.controllers.update_controller._fetch_latest_release", return_value=release_data), \
             patch("wenzi.controllers.update_controller.async_loop"), \
             patch("wenzi.__version__", "0.1.2"), \
             patch("PyObjCTools.AppHelper"):
            app = _make_app()
            ctrl = UpdateController(app)
            ctrl._check_update()
            assert ctrl._release_data is release_data


# --- Staged update on launch ---


class TestTryApplyStagedUpdate:
    def test_no_staged_app(self):
        app = _make_app()
        ctrl = UpdateController(app)
        with patch("wenzi.updater.AppUpdater.get_staged_app_path", return_value=None):
            assert ctrl._try_apply_staged_update() is False

    def test_verification_fails(self, tmp_path):
        staged = tmp_path / ".WenZi-update.app"
        staged.mkdir()
        app = _make_app()
        ctrl = UpdateController(app)
        with patch("wenzi.updater.AppUpdater.get_staged_app_path", return_value=staged), \
             patch("wenzi.updater.AppUpdater._verify_app", side_effect=Exception("bad sig")), \
             patch("wenzi.updater.AppUpdater.cleanup_staged_app") as mock_cleanup:
            assert ctrl._try_apply_staged_update() is False
            mock_cleanup.assert_called_once()

    def test_no_version_in_staged(self, tmp_path):
        staged = tmp_path / ".WenZi-update.app"
        staged.mkdir()
        app = _make_app()
        ctrl = UpdateController(app)
        with patch("wenzi.updater.AppUpdater.get_staged_app_path", return_value=staged), \
             patch("wenzi.updater.AppUpdater._verify_app"), \
             patch("wenzi.updater.AppUpdater.get_app_version", return_value=None), \
             patch("wenzi.updater.AppUpdater.cleanup_staged_app") as mock_cleanup:
            assert ctrl._try_apply_staged_update() is False
            mock_cleanup.assert_called_once()

    def test_staged_not_newer(self, tmp_path):
        staged = tmp_path / ".WenZi-update.app"
        staged.mkdir()
        app = _make_app()
        ctrl = UpdateController(app)
        with patch("wenzi.updater.AppUpdater.get_staged_app_path", return_value=staged), \
             patch("wenzi.updater.AppUpdater._verify_app"), \
             patch("wenzi.updater.AppUpdater.get_app_version", return_value="0.1.0"), \
             patch("wenzi.__version__", "0.1.6"), \
             patch("wenzi.updater.AppUpdater.cleanup_staged_app") as mock_cleanup:
            assert ctrl._try_apply_staged_update() is False
            mock_cleanup.assert_called_once()

    def test_user_clicks_later(self, tmp_path):
        staged = tmp_path / ".WenZi-update.app"
        staged.mkdir()
        app = _make_app()
        ctrl = UpdateController(app)
        with patch("wenzi.updater.AppUpdater.get_staged_app_path", return_value=staged), \
             patch("wenzi.updater.AppUpdater._verify_app"), \
             patch("wenzi.updater.AppUpdater.get_app_version", return_value="2.0.0"), \
             patch("wenzi.__version__", "0.1.6"), \
             patch("wenzi.ui_helpers.topmost_alert", return_value=0), \
             patch("wenzi.ui_helpers.restore_accessory"):
            assert ctrl._try_apply_staged_update() is False
            # Staged app should NOT be cleaned up — user chose "Later"
            app._on_quit_click.assert_not_called()

    def test_successful_apply(self, tmp_path):
        staged = tmp_path / ".WenZi-update.app"
        staged.mkdir()
        app = _make_app()
        ctrl = UpdateController(app)
        with patch("wenzi.updater.AppUpdater.get_staged_app_path", return_value=staged), \
             patch("wenzi.updater.AppUpdater._verify_app"), \
             patch("wenzi.updater.AppUpdater.get_app_version", return_value="2.0.0"), \
             patch("wenzi.__version__", "0.1.6"), \
             patch("wenzi.ui_helpers.topmost_alert", return_value=1), \
             patch("wenzi.ui_helpers.restore_accessory"), \
             patch("wenzi.updater.AppUpdater.perform_swap_and_relaunch", return_value=True):
            assert ctrl._try_apply_staged_update() is True
            app._on_quit_click.assert_called_once_with(None)

    def test_swap_fails_cleans_up(self, tmp_path):
        staged = tmp_path / ".WenZi-update.app"
        staged.mkdir()
        app = _make_app()
        ctrl = UpdateController(app)
        with patch("wenzi.updater.AppUpdater.get_staged_app_path", return_value=staged), \
             patch("wenzi.updater.AppUpdater._verify_app"), \
             patch("wenzi.updater.AppUpdater.get_app_version", return_value="2.0.0"), \
             patch("wenzi.__version__", "0.1.6"), \
             patch("wenzi.ui_helpers.topmost_alert", return_value=1), \
             patch("wenzi.ui_helpers.restore_accessory"), \
             patch("wenzi.updater.AppUpdater.perform_swap_and_relaunch", return_value=False), \
             patch("wenzi.updater.AppUpdater.cleanup_staged_app") as mock_cleanup:
            assert ctrl._try_apply_staged_update() is False
            mock_cleanup.assert_called_once()
            app._on_quit_click.assert_not_called()
