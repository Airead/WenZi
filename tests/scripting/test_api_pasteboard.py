"""Tests for vt.pasteboard API."""

from unittest.mock import patch

from voicetext.scripting.api.pasteboard import PasteboardAPI


class TestPasteboardAPI:
    @patch("voicetext.input.get_clipboard_text")
    def test_get(self, mock_get):
        mock_get.return_value = "hello"
        api = PasteboardAPI()
        assert api.get() == "hello"
        mock_get.assert_called_once()

    @patch("voicetext.input.set_clipboard_text")
    def test_set(self, mock_set):
        api = PasteboardAPI()
        api.set("world")
        mock_set.assert_called_once_with("world")

    @patch("voicetext.input.get_clipboard_text")
    def test_get_none(self, mock_get):
        mock_get.return_value = None
        api = PasteboardAPI()
        assert api.get() is None
