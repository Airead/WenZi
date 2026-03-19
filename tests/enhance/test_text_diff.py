"""Tests for the shared text_diff module."""

from __future__ import annotations

from wenzi.enhance.text_diff import inline_diff, tokenize_for_diff


class TestTokenizeForDiff:
    def test_english_word(self):
        assert tokenize_for_diff("cloud") == ["cloud"]

    def test_cjk_characters(self):
        assert tokenize_for_diff("派森") == ["派", "森"]

    def test_mixed_text(self):
        tokens = tokenize_for_diff("我想用cloud来写代码")
        assert "cloud" in tokens
        assert "我" in tokens

    def test_whitespace(self):
        tokens = tokenize_for_diff("gate tag")
        assert " " in tokens

    def test_punctuation(self):
        tokens = tokenize_for_diff("好的，OK。")
        assert "OK" in tokens
        assert "，" in tokens

    def test_empty(self):
        assert tokenize_for_diff("") == []

    def test_alphanumeric(self):
        tokens = tokenize_for_diff("Python3")
        assert tokens == ["Python3"]


class TestInlineDiff:
    def test_identical(self):
        assert inline_diff("没有变化", "没有变化") == "没有变化"

    def test_replacement(self):
        result = inline_diff("派森编程语言", "Python编程语言")
        assert "[派森→Python]" in result
        assert "编程语言" in result

    def test_multiple_replacements(self):
        result = inline_diff("平平和珊珊来了", "萍萍和杉杉来了")
        assert "[平平→萍萍]" in result
        assert "[珊珊→杉杉]" in result

    def test_deletion_silent(self):
        result = inline_diff("多余的文字好", "好")
        assert "[" not in result
        assert "好" in result

    def test_insertion_silent(self):
        result = inline_diff("好", "非常好")
        assert "[" not in result
        assert "非常好" in result

    def test_empty_strings(self):
        assert inline_diff("", "") == ""

    def test_empty_asr(self):
        result = inline_diff("", "新文本")
        assert "新文本" in result

    def test_empty_final(self):
        assert inline_diff("旧文本", "") == ""
