"""Tests for repetition detection in LLM streaming output."""

from wenzi.enhance.repetition import detect_repetition, truncate_repeated


class TestDetectRepetition:
    def test_no_repetition(self):
        text = "Python|tech|派森|编程语言\nKubernetes|tech|库伯尼特斯|容器编排\n"
        assert detect_repetition(text) is False

    def test_single_char_repeated(self):
        """Single character repeated 20+ times should be detected."""
        text = "正常文本" + "的" * 20
        assert detect_repetition(text) is True

    def test_single_char_below_threshold(self):
        """Single character repeated fewer than 20 times should not trigger."""
        text = "正常文本" + "的" * 10
        assert detect_repetition(text) is False

    def test_short_pattern_repeated(self):
        """3-char pattern repeated 7+ times (>=20 total chars)."""
        text = "正常文本" + "好的呀" * 7
        assert detect_repetition(text) is True

    def test_short_pattern_below_threshold(self):
        """3-char pattern repeated only 3 times should not trigger."""
        text = "正常文本" + "好的呀" * 3
        assert detect_repetition(text) is False

    def test_four_char_pattern(self):
        """4-char pattern needs 5 repeats (20 chars)."""
        text = "前缀" + "技术术语" * 5
        assert detect_repetition(text) is True

    def test_line_repeated(self):
        """Full pipe-separated line repeated."""
        line = "cache|tech|Catch|技术术语\n"
        text = "Python|tech|派森|编程语言\n" + line * 4
        assert detect_repetition(text) is True

    def test_line_repeated_below_threshold(self):
        """Line repeated only twice should not trigger (below min_repeats=4)."""
        line = "cache|tech|Catch|技术术语\n"
        text = "Python|tech|派森|编程语言\n" + line * 2
        assert detect_repetition(text) is False

    def test_empty_text(self):
        assert detect_repetition("") is False

    def test_short_text(self):
        assert detect_repetition("hello") is False

    def test_whitespace_only_not_detected(self):
        """Whitespace-only patterns should not trigger."""
        text = "some text" + " " * 50
        assert detect_repetition(text) is False

    def test_two_char_pattern(self):
        """2-char pattern needs 10 repeats (20 chars)."""
        text = "前缀" + "好的" * 10
        assert detect_repetition(text) is True

    def test_custom_thresholds(self):
        """Custom min_repeated_chars and min_repeats."""
        text = "abc" * 5  # 15 chars of repetition
        assert detect_repetition(text, min_repeated_chars=10, min_repeats=3) is True
        assert detect_repetition(text, min_repeated_chars=20, min_repeats=3) is False


class TestTruncateRepeated:
    def test_no_repetition_unchanged(self):
        text = "normal text here"
        assert truncate_repeated(text) == text

    def test_truncate_single_char(self):
        text = "正常文本" + "的" * 30
        result = truncate_repeated(text)
        assert result.startswith("正常文本")
        assert len(result) < len(text)
        # Should keep one occurrence
        assert result.endswith("的")

    def test_truncate_line_repetition(self):
        line = "cache|tech|Catch|技术术语\n"
        text = "Python|tech|派森|编程语言\n" + line * 10
        result = truncate_repeated(text)
        assert "Python|tech|派森|编程语言" in result
        assert len(result) < len(text)

    def test_truncate_preserves_prefix(self):
        """Content before the repetition should be preserved."""
        prefix = "term|category|variants|context\nPython|tech|派森|编程语言\n"
        text = prefix + "重复" * 15
        result = truncate_repeated(text)
        assert result.startswith(prefix)
