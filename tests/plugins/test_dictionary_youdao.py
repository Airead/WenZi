"""Tests for dictionary plugin Youdao API client."""

import json
from unittest.mock import MagicMock, patch

import pytest


SUGGEST_RESPONSE = {
    "result": {"msg": "success", "code": 200},
    "data": {
        "entries": [
            {"explain": "int. 喂，你好", "entry": "hello"},
            {"explain": "int. <非规范>打招呼", "entry": "hellow"},
        ],
        "query": "hello",
        "language": "en",
        "type": "dict",
    },
}


class TestSuggest:
    def test_returns_word_and_explain(self):
        body = json.dumps(SUGGEST_RESPONSE).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            from dictionary.youdao import suggest

            results = suggest("hello")

        assert len(results) == 2
        assert results[0] == {"word": "hello", "explain": "int. 喂，你好"}
        assert results[1] == {"word": "hellow", "explain": "int. <非规范>打招呼"}
        # Verify URL contains query
        call_url = mock_open.call_args[0][0]
        assert "q=hello" in call_url

    def test_returns_empty_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            from dictionary.youdao import suggest

            results = suggest("hello")

        assert results == []

    def test_returns_empty_on_bad_json(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from dictionary.youdao import suggest

            results = suggest("hello")

        assert results == []

    def test_returns_empty_when_no_entries(self):
        body = json.dumps({"result": {"code": 200}, "data": {}}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from dictionary.youdao import suggest

            results = suggest("xyz123")

        assert results == []
