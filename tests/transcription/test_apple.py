"""Tests for the Apple Speech transcriber module."""

import sys
from unittest.mock import MagicMock

import pytest

from wenzi.transcription.apple import (
    _LANG_TO_LOCALE,
    AppleSpeechTranscriber,
    _resolve_locale,
)
from wenzi.transcription.base import BaseTranscriber


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


class TestCheckSiriAvailable:
    """Tests for check_siri_available() with mocked Apple frameworks."""

    @pytest.fixture(autouse=True)
    def _mock_apple_frameworks(self, monkeypatch):
        import wenzi.transcription.apple as _apple_mod

        monkeypatch.setattr(_apple_mod, "_SIRI_CHECK_TIMEOUT", 0.01)

        self.mock_speech = MagicMock()
        self.mock_foundation = MagicMock()
        self.mock_corefoundation = MagicMock()

        monkeypatch.setitem(sys.modules, "Speech", self.mock_speech)
        monkeypatch.setitem(sys.modules, "Foundation", self.mock_foundation)
        monkeypatch.setitem(sys.modules, "CoreFoundation", self.mock_corefoundation)

        self.mock_corefoundation.CFRunLoopRunInMode = MagicMock()
        self.mock_corefoundation.kCFRunLoopDefaultMode = "default"

        # Default: authorization granted
        self.mock_speech.SFSpeechRecognizerAuthorizationStatusAuthorized = 3

        def _fake_request_auth(callback):
            callback(3)  # authorized

        self.mock_speech.SFSpeechRecognizer.requestAuthorization_ = _fake_request_auth

        # Default: recognizer available
        mock_recognizer = MagicMock()
        mock_recognizer.isAvailable.return_value = True
        mock_recognizer.supportsOnDeviceRecognition.return_value = True
        self.mock_recognizer = mock_recognizer
        self.mock_speech.SFSpeechRecognizer.alloc.return_value.initWithLocale_.return_value = (
            mock_recognizer
        )

    def test_siri_available_no_error(self):
        """When no error fires within timeout, check returns True."""
        # recognition task handler is never called (no error)
        self.mock_recognizer.recognitionTaskWithRequest_resultHandler_ = MagicMock(
            return_value=MagicMock()
        )
        from wenzi.transcription.apple import check_siri_available

        ok, err = check_siri_available(language="zh", on_device=True)
        assert ok is True
        assert err is None

    def test_siri_disabled_returns_false(self):
        """When recognition fires a 'Siri' error, check returns False."""
        mock_task = MagicMock()

        def _start_recognition(request, handler):
            # Simulate immediate Siri error
            mock_error = MagicMock()
            mock_error.localizedDescription.return_value = (
                "Siri and Dictation are disabled"
            )
            handler(None, mock_error)
            return mock_task

        self.mock_recognizer.recognitionTaskWithRequest_resultHandler_ = (
            _start_recognition
        )

        from wenzi.transcription.apple import check_siri_available

        ok, err = check_siri_available(language="zh", on_device=True)
        assert ok is False
        assert "Siri" in err

    def test_non_siri_error_returns_true(self):
        """Non-Siri errors are ignored (let initialize() handle them)."""
        mock_task = MagicMock()

        def _start_recognition(request, handler):
            mock_error = MagicMock()
            mock_error.localizedDescription.return_value = "No speech detected"
            handler(None, mock_error)
            return mock_task

        self.mock_recognizer.recognitionTaskWithRequest_resultHandler_ = (
            _start_recognition
        )

        from wenzi.transcription.apple import check_siri_available

        ok, err = check_siri_available(language="zh", on_device=True)
        assert ok is True

    def test_auth_not_granted_returns_true(self):
        """Auth denied is not a Siri issue — return True to let init handle it."""

        def _fake_request_auth(callback):
            callback(2)  # denied

        self.mock_speech.SFSpeechRecognizer.requestAuthorization_ = (
            _fake_request_auth
        )

        from wenzi.transcription.apple import check_siri_available

        ok, err = check_siri_available(language="zh", on_device=True)
        assert ok is True  # not a Siri issue

    def test_language_none_does_not_crash(self):
        """Passing language=None should not raise TypeError."""
        self.mock_recognizer.recognitionTaskWithRequest_resultHandler_ = MagicMock(
            return_value=MagicMock()
        )
        from wenzi.transcription.apple import check_siri_available

        ok, err = check_siri_available(language=None, on_device=True)
        assert ok is True

    def test_recognizer_unavailable_returns_true(self):
        """Unavailable recognizer is not a Siri issue."""
        self.mock_speech.SFSpeechRecognizer.alloc.return_value.initWithLocale_.return_value = (
            None
        )

        from wenzi.transcription.apple import check_siri_available

        ok, err = check_siri_available(language="zh", on_device=True)
        assert ok is True
