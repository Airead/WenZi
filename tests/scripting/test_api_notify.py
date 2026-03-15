"""Tests for vt.notify API."""

from unittest.mock import patch

from voicetext.scripting.api.notify import notify


class TestNotify:
    @patch("voicetext.statusbar.send_notification")
    def test_notify(self, mock_send):
        notify("Title", "Message")
        mock_send.assert_called_once_with("Title", "", "Message")

    @patch("voicetext.statusbar.send_notification")
    def test_notify_no_message(self, mock_send):
        notify("Title")
        mock_send.assert_called_once_with("Title", "", "")
