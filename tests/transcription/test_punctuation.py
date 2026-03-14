"""Tests for the punctuation restoration module."""

from unittest.mock import MagicMock

from voicetext.transcription.punctuation import PunctuationRestorer


class TestPunctuationRestorer:
    def test_restore_empty_text(self):
        restorer = PunctuationRestorer()
        restorer._initialized = True
        restorer._model = MagicMock()
        assert restorer.restore("") == ""
        assert restorer.restore("   ") == "   "
        restorer._model.assert_not_called()

    def test_restore_calls_model(self):
        restorer = PunctuationRestorer()
        restorer._initialized = True
        restorer._model = MagicMock(return_value=("你好，世界。",))
        result = restorer.restore("你好世界")
        assert result == "你好，世界。"
        restorer._model.assert_called_once_with("你好世界")

    def test_restore_handles_non_tuple_result(self):
        restorer = PunctuationRestorer()
        restorer._initialized = True
        restorer._model = MagicMock(return_value="你好，世界。")
        result = restorer.restore("你好世界")
        assert result == "你好，世界。"

    def test_restore_returns_original_on_failure(self):
        restorer = PunctuationRestorer()
        restorer._initialized = True
        restorer._model = MagicMock(side_effect=RuntimeError("model error"))
        result = restorer.restore("你好世界")
        assert result == "你好世界"

    def test_cleanup(self):
        restorer = PunctuationRestorer()
        restorer._initialized = True
        restorer._model = MagicMock()
        restorer.cleanup()
        assert restorer.initialized is False
        assert restorer._model is None
