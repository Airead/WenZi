"""Tests for the update checker controller."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from wenzi.controllers.update_controller import (
    UpdateController,
    _fetch_latest_release,
    _is_newer,
    _parse_version,
)


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
        with patch("threading.Thread") as mock_thread:
            ctrl.start()
            mock_thread.assert_not_called()

    def test_start_enabled_launches_thread(self):
        app = _make_app()
        ctrl = UpdateController(app)
        with patch("threading.Thread") as mock_thread:
            mock_instance = MagicMock()
            mock_thread.return_value = mock_instance
            ctrl.start()
            mock_thread.assert_called_once()
            mock_instance.start.assert_called_once()


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


class TestUpdateControllerCheckUpdate:
    @patch("wenzi.controllers.update_controller._fetch_latest_release")
    @patch("wenzi.controllers.update_controller.threading.Timer")
    def test_skip_dev_mode(self, mock_timer_cls, mock_fetch):
        app = _make_app()
        ctrl = UpdateController(app)

        with patch("wenzi.__version__", "dev"):
            ctrl._check_update()

        mock_fetch.assert_not_called()

    @patch("wenzi.controllers.update_controller._fetch_latest_release")
    @patch("wenzi.controllers.update_controller.threading.Timer")
    def test_dev_version_env_override(self, mock_timer_cls, mock_fetch):
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
    @patch("wenzi.controllers.update_controller.threading.Timer")
    def test_new_version_triggers_menu_update(self, mock_timer_cls, mock_fetch):
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
    @patch("wenzi.controllers.update_controller.threading.Timer")
    def test_same_version_no_menu_update(self, mock_timer_cls, mock_fetch):
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
    @patch("wenzi.controllers.update_controller.threading.Timer")
    def test_fetch_failure_no_crash(self, mock_timer_cls, mock_fetch):
        app = _make_app()
        ctrl = UpdateController(app)

        with patch("wenzi.__version__", "0.1.2"):
            ctrl._check_update()  # should not raise

    @patch("wenzi.controllers.update_controller._fetch_latest_release")
    @patch("wenzi.controllers.update_controller.threading.Timer")
    def test_always_schedules_next(self, mock_timer_cls, mock_fetch):
        mock_fetch.return_value = None
        app = _make_app()
        ctrl = UpdateController(app)

        with patch("wenzi.__version__", "0.1.2"):
            ctrl._check_update()

        mock_timer_cls.assert_called_once()
        mock_timer_cls.return_value.start.assert_called_once()


class TestUpdateControllerMenuClick:
    def test_click_opens_browser(self):
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
