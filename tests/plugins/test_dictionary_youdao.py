"""Tests for dictionary plugin Youdao API client."""

import json
from unittest.mock import MagicMock, patch


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


LOOKUP_RESPONSE = {
    "ec": {
        "exam_type": ["CET4", "CET6"],
        "word": {
            "usphone": "həˈloʊ",
            "ukphone": "həˈləʊ",
            "trs": [
                {"pos": "int.", "tran": "喂，你好"},
                {"pos": "n.", "tran": "招呼，问候"},
            ],
            "wfs": [
                {"wf": {"name": "复数", "value": "hellos"}},
            ],
            "return-phrase": "hello",
        },
    },
    "simple": {
        "word": [{"usphone": "həˈloʊ", "ukphone": "həˈləʊ"}],
    },
    "phrs": {
        "phrs": [{"headword": "say hello", "translation": "打招呼"}],
    },
    "syno": {
        "synos": [{"pos": "int.", "ws": ["hallo", "hi"], "tran": "喂"}],
    },
    "blng_sents_part": {
        "sentence-pair": [
            {
                "sentence": "Hello, how are you?",
                "sentence-translation": "你好，你怎么样？",
            },
        ],
    },
    "etym": {
        "etyms": {
            "zh": [{"word": "hello", "value": "感叹词，打招呼用语", "desc": "喂"}],
        },
    },
}


class TestLookup:
    def test_returns_parsed_json(self):
        body = json.dumps(LOOKUP_RESPONSE).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            from dictionary.youdao import lookup

            result = lookup("hello", "en2zh-CHS")

        assert "ec" in result
        assert result["ec"]["word"]["usphone"] == "həˈloʊ"
        # Verify URL contains direction
        call_url = mock_open.call_args[0][0]
        assert "t=en2zh-CHS" in call_url
        assert "q=hello" in call_url

    def test_zh2en_direction(self):
        body = json.dumps({"web_trans": {}}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            from dictionary.youdao import lookup

            lookup("你好", "zh2en")

        call_url = mock_open.call_args[0][0]
        assert "t=zh2en" in call_url
        assert "le=zh" in call_url

    def test_returns_empty_dict_on_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            from dictionary.youdao import lookup

            result = lookup("hello", "en2zh-CHS")

        assert result == {}

    def test_special_characters_encoded_in_url(self):
        """Slash, hash, ampersand in queries must be percent-encoded."""
        body = json.dumps({"ec": {}}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            from dictionary.youdao import lookup

            lookup("a/b#c&d", "en2zh-CHS")

        call_url = mock_open.call_args[0][0]
        # '/' '#' '&' must NOT appear literally in the query part
        # They should be encoded as %2F %23 %26
        assert "a%2Fb%23c%26d" in call_url
