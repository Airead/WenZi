"""Tests for vt.alert API."""

from unittest.mock import patch


class TestAlert:
    @patch("PyObjCTools.AppHelper.callAfter")
    def test_alert_dispatches_to_main_thread(self, mock_call_after):
        from voicetext.scripting.api.alert import alert

        alert("Hello", duration=2.0)
        mock_call_after.assert_called_once()
        args = mock_call_after.call_args[0]
        assert args[1] == "Hello"
        assert args[2] == 2.0
