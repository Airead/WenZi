"""Tests for translate_webview URL generation."""

from voicetext.ui.translate_webview import build_google_translate_url


class TestBuildGoogleTranslateUrl:
    def test_chinese_text_translates_to_english(self):
        url = build_google_translate_url("你好世界")
        assert "sl=zh-CN" in url
        assert "tl=en" in url
        assert "op=translate" in url

    def test_english_text_translates_to_chinese(self):
        url = build_google_translate_url("hello world")
        assert "sl=en" in url
        assert "tl=zh-CN" in url
        assert "op=translate" in url

    def test_mixed_text_detected_as_chinese(self):
        url = build_google_translate_url("Hello 你好")
        assert "sl=zh-CN" in url
        assert "tl=en" in url

    def test_text_is_url_encoded(self):
        url = build_google_translate_url("hello world")
        # Space should be encoded as %20
        assert "hello%20world" in url

    def test_special_characters_encoded(self):
        url = build_google_translate_url("test&foo=bar")
        assert "test%26foo%3Dbar" in url

    def test_url_starts_with_google_translate(self):
        url = build_google_translate_url("test")
        assert url.startswith("https://translate.google.com/")

    def test_chinese_punctuation_detected(self):
        # CJK characters in the range \u4e00-\u9fff
        url = build_google_translate_url("测试一下翻译功能")
        assert "sl=zh-CN" in url

    def test_pure_numbers_treated_as_english(self):
        url = build_google_translate_url("12345")
        assert "sl=en" in url
        assert "tl=zh-CN" in url
