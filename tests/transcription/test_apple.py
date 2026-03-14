"""Tests for the Apple Speech transcriber module."""

from unittest.mock import MagicMock


from voicetext.transcription.base import BaseTranscriber
from voicetext.transcription.apple import (
    AppleSpeechTranscriber,
    _LANG_TO_LOCALE,
    _resolve_locale,
)


class TestResolveLocale:
    def test_short_code_to_locale(self):
        assert _resolve_locale("zh") == "zh-CN"
        assert _resolve_locale("en") == "en-US"
        assert _resolve_locale("ja") == "ja-JP"

    def test_full_locale_passthrough(self):
        assert _resolve_locale("zh-TW") == "zh-TW"
        assert _resolve_locale("en-GB") == "en-GB"

    def test_underscore_locale_passthrough(self):
        assert _resolve_locale("zh_TW") == "zh_TW"

    def test_unknown_short_code_passthrough(self):
        assert _resolve_locale("xx") == "xx"

    def test_all_mapped_languages(self):
        for lang, locale in _LANG_TO_LOCALE.items():
            assert _resolve_locale(lang) == locale
            assert "-" in locale  # All locales should be BCP-47 format


class TestAppleSpeechTranscriberInit:
    def test_default_parameters(self):
        t = AppleSpeechTranscriber()
        assert t._language == "zh"
        assert t._locale_id == "zh-CN"
        assert t._on_device is True
        assert t._initialized is False
        assert t._recognizer is None

    def test_custom_language(self):
        t = AppleSpeechTranscriber(language="en")
        assert t._language == "en"
        assert t._locale_id == "en-US"

    def test_full_locale_language(self):
        t = AppleSpeechTranscriber(language="zh-TW")
        assert t._locale_id == "zh-TW"

    def test_on_device_false(self):
        t = AppleSpeechTranscriber(on_device=False)
        assert t._on_device is False

    def test_is_base_transcriber(self):
        t = AppleSpeechTranscriber()
        assert isinstance(t, BaseTranscriber)


class TestAppleSpeechTranscriberProperties:
    def test_initialized_default_false(self):
        t = AppleSpeechTranscriber()
        assert t.initialized is False

    def test_model_display_name_on_device(self):
        t = AppleSpeechTranscriber(on_device=True)
        assert t.model_display_name == "Apple Speech (On-Device)"

    def test_model_display_name_server(self):
        t = AppleSpeechTranscriber(on_device=False)
        assert t.model_display_name == "Apple Speech (Server)"

    def test_skip_punc_default_true(self):
        t = AppleSpeechTranscriber()
        assert t.skip_punc is True


class TestAppleSpeechTranscriberCleanup:
    def test_cleanup_resets_state(self):
        t = AppleSpeechTranscriber()
        t._initialized = True
        t._recognizer = MagicMock()
        t.cleanup()
        assert t.initialized is False
        assert t._recognizer is None

    def test_cleanup_from_uninitialized(self):
        t = AppleSpeechTranscriber()
        t.cleanup()  # Should not raise
        assert t.initialized is False
        assert t._recognizer is None


class TestAppleSpeechTranscriberInitialize:
    def test_already_initialized_noop(self):
        t = AppleSpeechTranscriber()
        t._initialized = True
        t._recognizer = MagicMock()
        # Should return immediately without importing Speech
        t.initialize()
        assert t.initialized is True


class TestAppleSpeechTranscriberOnDeviceParsing:
    def test_on_device_true_by_default(self):
        t = AppleSpeechTranscriber()
        assert t._on_device is True

    def test_on_device_explicit_false(self):
        t = AppleSpeechTranscriber(on_device=False)
        assert t._on_device is False

    def test_on_device_explicit_true(self):
        t = AppleSpeechTranscriber(on_device=True)
        assert t._on_device is True


class TestAppleSpeechTranscriberLanguageMapping:
    """Test that various language inputs are correctly mapped."""

    def test_chinese_default(self):
        t = AppleSpeechTranscriber(language="zh")
        assert t._locale_id == "zh-CN"

    def test_english(self):
        t = AppleSpeechTranscriber(language="en")
        assert t._locale_id == "en-US"

    def test_japanese(self):
        t = AppleSpeechTranscriber(language="ja")
        assert t._locale_id == "ja-JP"

    def test_korean(self):
        t = AppleSpeechTranscriber(language="ko")
        assert t._locale_id == "ko-KR"

    def test_full_locale_preserved(self):
        t = AppleSpeechTranscriber(language="en-GB")
        assert t._locale_id == "en-GB"
