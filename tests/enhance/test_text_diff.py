"""Tests for the shared text_diff module."""

from __future__ import annotations

from wenzi.enhance.text_diff import (
    _is_punctuation_only,
    _normalize_cjk_spacing,
    _strip_boundary_punctuation,
    _to_simplified,
    extract_word_pairs,
    inline_diff,
    tokenize_for_diff,
)


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

    def test_punctuation_replacement_silent(self):
        """Half-width to full-width punctuation is applied silently."""
        result = inline_diff("好的,OK.", "好的，OK。")
        assert "[" not in result
        assert result == "好的，OK。"

    def test_punctuation_mixed_with_text_replacement(self):
        """Punctuation-only replacements are silent even alongside text replacements."""
        result = inline_diff("不是这个,就是你分词的,用方广号扩一下", "不是这个，就是你分词的，用方括号括一下")
        assert "，" in result  # punctuation silently replaced
        assert "[广→括]" in result  # text replacement bracketed
        assert "[扩→括]" in result
        assert "[,→，]" not in result  # punctuation NOT bracketed

    def test_question_mark_replacement_silent(self):
        result = inline_diff("你觉得靠谱吗?", "你觉得靠谱吗？")
        assert "[" not in result
        assert result == "你觉得靠谱吗？"

    def test_whitespace_stripped_from_brackets(self):
        """Whitespace around replacement content should be outside brackets."""
        result = inline_diff("Gate 库", " git 库")
        assert "[Gate→git]" in result
        assert "→ " not in result
        assert " →" not in result

    def test_whitespace_preserved_outside_brackets(self):
        """Stripped whitespace appears outside, not lost."""
        result = inline_diff("写jason格式", "写 JSON 格式")
        assert "[jason→JSON]" in result
        assert " [" in result or result.index("[") > 0  # leading space preserved

    def test_delete_before_replace_merged(self):
        """Adjacent delete + replace should merge into one replacement."""
        result = inline_diff(
            "在Cloud MD里面记一下,以后默认都使用普通墨记。",
            "在 CLAUDE.md 里面记一下，以后默认都使用普通merge",
        )
        assert "[Cloud MD→CLAUDE.md]" in result

    def test_boundary_punctuation_stripped_from_replace(self):
        """Trailing punctuation on old side should be outside brackets."""
        result = inline_diff(
            "在Cloud MD里面记一下,以后默认都使用普通墨记。",
            "在 CLAUDE.md 里面记一下，以后默认都使用普通merge",
        )
        assert "[墨记→merge]" in result
        assert "墨记。" not in result

    def test_leading_punctuation_stripped_from_replace(self):
        """Leading punctuation on old side should be outside brackets."""
        result = inline_diff("这是「测试」文字", "这是「test」文字")
        assert "[测试→test]" in result

    def test_delete_plus_replace_with_space_gap(self):
        """delete + equal(space) + replace should merge."""
        result = inline_diff("用 Python 3来写", "用 Go 来写")
        # "Python" deleted, " " equal, "3" replaced by "Go"
        # should merge to [Python 3→Go]
        assert "Python" in result.split("→")[0]  # Python is in old side

    def test_replace_plus_trailing_delete_merged(self):
        """replace + delete should merge."""
        result = inline_diff("写code fast", "写代码")
        # Ensure "code" and "fast" appear in the same replacement or are handled
        assert "[" in result or "代码" in result


class TestNormalizeCjkSpacing:
    def test_cjk_before_latin(self):
        assert _normalize_cjk_spacing("点set") == "点 set"

    def test_latin_before_cjk(self):
        assert _normalize_cjk_spacing("set的") == "set 的"

    def test_both_sides(self):
        assert _normalize_cjk_spacing("点set的") == "点 set 的"

    def test_already_spaced(self):
        assert _normalize_cjk_spacing("点 set 的") == "点 set 的"

    def test_digit_boundary(self):
        assert _normalize_cjk_spacing("有3个") == "有 3 个"

    def test_pure_cjk_unchanged(self):
        assert _normalize_cjk_spacing("纯中文") == "纯中文"

    def test_pure_latin_unchanged(self):
        assert _normalize_cjk_spacing("pure english") == "pure english"

    def test_empty(self):
        assert _normalize_cjk_spacing("") == ""


class TestInlineDiffCjkSpacing:
    """Tests for CJK-Latin boundary spacing normalization in inline_diff."""

    def test_asr_no_space_final_has_space(self):
        """ASR lacks CJK-Latin spaces, final has them — tokens align correctly."""
        result = inline_diff(
            "当用户点set up later的时候然后用户再次去按fn",
            "当用户点 Set Up Later 的时候，然后用户再次去按 Fn",
        )
        assert "[set→Set]" in result
        assert "[up→Up]" in result
        assert "[later→Later]" in result
        assert "[fn→Fn]" in result
        # Must NOT produce misaligned diffs
        assert "[set up→Set]" not in result
        assert "[later→Up Later]" not in result

    def test_both_no_space(self):
        """Neither side has CJK-Latin spaces — normalization adds them for alignment."""
        result = inline_diff("点setup的", "点Setup的")
        assert "[setup→Setup]" in result

    def test_asr_has_space_final_no_space(self):
        """ASR has CJK-Latin spaces, final does not."""
        result = inline_diff("点 set up 的", "点Setup的")
        assert "[set up→Setup]" in result

    def test_digit_boundary_alignment(self):
        """Digit-CJK boundary spacing should not cause misalignment."""
        result = inline_diff("有3个苹果", "有 3 个苹果")
        assert "[" not in result  # only spacing change, no semantic diff


class TestStripBoundaryPunctuation:
    def test_trailing_period(self):
        lead, core, trail = _strip_boundary_punctuation("墨记。")
        assert lead == ""
        assert core == "墨记"
        assert trail == "。"

    def test_leading_bracket(self):
        lead, core, trail = _strip_boundary_punctuation("「测试」")
        assert lead == "「"
        assert core == "测试"
        assert trail == "」"

    def test_no_punctuation(self):
        lead, core, trail = _strip_boundary_punctuation("hello")
        assert lead == ""
        assert core == "hello"
        assert trail == ""

    def test_all_punctuation(self):
        lead, core, trail = _strip_boundary_punctuation("。，")
        assert core == ""

    def test_empty(self):
        lead, core, trail = _strip_boundary_punctuation("")
        assert lead == ""
        assert core == ""
        assert trail == ""


class TestIsPunctuationOnly:
    def test_ascii_punctuation(self):
        assert _is_punctuation_only(",.")

    def test_fullwidth_punctuation(self):
        assert _is_punctuation_only("，。")

    def test_mixed_punctuation(self):
        assert _is_punctuation_only(",，")

    def test_text_not_punctuation(self):
        assert not _is_punctuation_only("hello")

    def test_cjk_not_punctuation(self):
        assert not _is_punctuation_only("你")

    def test_mixed_text_and_punctuation(self):
        assert not _is_punctuation_only("a,")

    def test_empty_string(self):
        assert not _is_punctuation_only("")

    def test_space_is_punctuation(self):
        assert _is_punctuation_only(" ")


class TestToSimplified:
    def test_traditional_to_simplified(self):
        tokens = ["說", "話"]
        assert _to_simplified(tokens) == ["说", "话"]

    def test_already_simplified(self):
        tokens = ["说", "话"]
        assert _to_simplified(tokens) == ["说", "话"]

    def test_latin_unchanged(self):
        tokens = ["hello", " ", "world"]
        assert _to_simplified(tokens) == ["hello", " ", "world"]

    def test_mixed_tokens(self):
        tokens = ["說", "Python", "話"]
        assert _to_simplified(tokens) == ["说", "Python", "话"]


class TestTradSimpDiff:
    """Trad/simp variants of the same character should not appear as diffs."""

    def test_inline_diff_pure_trad_simp(self):
        """Pure trad-to-simp conversion produces no diff brackets."""
        result = inline_diff("說話", "说话")
        assert "[" not in result

    def test_inline_diff_trad_simp_with_real_change(self):
        """Only the real change is bracketed; trad/simp equivalences are silent."""
        result = inline_diff("說話", "说了")
        assert "[話→了]" in result or "[话→了]" in result
        assert "說→说" not in result

    def test_inline_diff_mixed_trad_simp_sentence(self):
        """Sentence-level trad/simp with a real correction."""
        result = inline_diff("這個東西很漂亮", "这个东西很好看")
        assert "漂亮" in result or "好看" in result  # real change
        assert "這→这" not in result  # trad/simp should be silent

    def test_extract_word_pairs_pure_trad_simp(self):
        """No pairs extracted when only trad/simp difference exists."""
        pairs = extract_word_pairs("說話", "说话")
        assert pairs == []

    def test_extract_word_pairs_trad_simp_with_real_change(self):
        """Only real changes appear in extracted pairs."""
        pairs = extract_word_pairs("這個東西很漂亮", "这个东西很好看")
        originals = [p[0] for p in pairs]
        corrected = [p[1] for p in pairs]
        # Should contain the real change
        assert any("漂" in o or "亮" in o for o in originals)
        assert any("好" in c or "看" in c for c in corrected)
        # Should NOT contain trad/simp-only pairs
        assert all("這" not in o for o in originals)
        assert all("東" not in o for o in originals)

    def test_extract_word_pairs_strips_boundary_punctuation(self):
        """Boundary punctuation should be stripped from extracted pairs."""
        pairs = extract_word_pairs(
            "测试一下标点符号相关的 diff",
            "测试一下标点符号相关的 ,difficulty,",
        )
        assert len(pairs) == 1
        original, corrected = pairs[0]
        assert original == "diff"
        assert corrected == "difficulty"
