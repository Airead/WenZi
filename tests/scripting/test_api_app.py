"""Tests for vt.app API."""

from unittest.mock import MagicMock, patch

from voicetext.scripting.api.app import AppAPI


class TestAppAPI:
    @patch("AppKit.NSWorkspace")
    def test_launch_success(self, mock_ws_cls):
        ws = MagicMock()
        ws.launchApplication_.return_value = True
        mock_ws_cls.sharedWorkspace.return_value = ws

        api = AppAPI()
        assert api.launch("Safari") is True
        ws.launchApplication_.assert_called_once_with("Safari")

    @patch("AppKit.NSWorkspace")
    def test_launch_failure(self, mock_ws_cls):
        ws = MagicMock()
        ws.launchApplication_.return_value = False
        mock_ws_cls.sharedWorkspace.return_value = ws

        api = AppAPI()
        assert api.launch("NonexistentApp") is False

    @patch("AppKit.NSWorkspace")
    def test_launch_with_app_path(self, mock_ws_cls):
        ws = MagicMock()
        ws.launchApplication_.return_value = True
        mock_ws_cls.sharedWorkspace.return_value = ws

        api = AppAPI()
        assert api.launch("/Applications/Safari.app") is True
        ws.launchApplication_.assert_called_once_with("/Applications/Safari.app")

    @patch("AppKit.NSWorkspace")
    def test_frontmost(self, mock_ws_cls):
        mock_app = MagicMock()
        mock_app.localizedName.return_value = "Finder"
        ws = MagicMock()
        ws.frontmostApplication.return_value = mock_app
        mock_ws_cls.sharedWorkspace.return_value = ws

        api = AppAPI()
        assert api.frontmost() == "Finder"

    @patch("AppKit.NSWorkspace")
    def test_frontmost_none(self, mock_ws_cls):
        ws = MagicMock()
        ws.frontmostApplication.return_value = None
        mock_ws_cls.sharedWorkspace.return_value = ws

        api = AppAPI()
        assert api.frontmost() is None
