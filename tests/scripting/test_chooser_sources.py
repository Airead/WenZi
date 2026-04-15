"""Tests for chooser data structures and fuzzy matching."""

from wenzi.scripting.sources import (
    ChooserItem,
    ChooserSource,
    _chars_in_order,
    _word_initials,
    fuzzy_match,
    fuzzy_match_fields,
    set_pinyin_enabled,
)


class TestChooserItem:
    def test_defaults(self):
        item = ChooserItem(title="Safari")
        assert item.title == "Safari"
        assert item.subtitle == ""
        assert item.action is None
        assert item.reveal_path is None

    def test_with_all_fields(self):
        called = []
        item = ChooserItem(
            title="Safari",
            subtitle="Web browser",
            action=lambda: called.append(True),
            reveal_path="/Applications/Safari.app",
        )
        assert item.title == "Safari"
        assert item.subtitle == "Web browser"
        assert item.reveal_path == "/Applications/Safari.app"
        item.action()
        assert called == [True]


class TestChooserSource:
    def test_defaults(self):
        src = ChooserSource(name="apps")
        assert src.name == "apps"
        assert src.prefix is None
        assert src.search is None
        assert src.priority == 0

    def test_with_prefix(self):
        src = ChooserSource(name="clipboard", prefix="cb", priority=10)
        assert src.prefix == "cb"
        assert src.priority == 10

    def test_with_search_function(self):
        items = [ChooserItem(title="Safari")]
        src = ChooserSource(
            name="apps",
            search=lambda q: [i for i in items if q.lower() in i.title.lower()],
        )
        result = src.search("saf")
        assert len(result) == 1
        assert result[0].title == "Safari"

        result = src.search("chrome")
        assert len(result) == 0


class TestChooserSourceStatic:
    def test_load_field(self):
        items = [ChooserItem(title="server-01")]
        src = ChooserSource(name="ssh", prefix="ss", load=lambda: items)
        assert src.load is not None
        assert src.search is None
        assert src.load() == items

    def test_load_and_search_mutually_exclusive_by_convention(self):
        """Static sources use load, dynamic sources use search."""
        static = ChooserSource(name="s", load=lambda: [])
        assert static.load is not None
        assert static.search is None

        dynamic = ChooserSource(name="d", search=lambda q: [])
        assert dynamic.search is not None
        assert dynamic.load is None


class TestWordInitials:
    def test_space_separated(self):
        assert _word_initials("System Configuration") == "sc"

    def test_camel_case(self):
        assert _word_initials("DragonDrop") == "dd"

    def test_mixed_words(self):
        assert _word_initials("Visual Studio Code") == "vsc"

    def test_single_word(self):
        assert _word_initials("Safari") == "s"

    def test_hyphenated(self):
        assert _word_initials("my-cool-app") == "mca"

    def test_underscored(self):
        assert _word_initials("my_cool_app") == "mca"

    def test_empty_string(self):
        assert _word_initials("") == ""

    def test_all_lowercase(self):
        assert _word_initials("safari") == "s"

    def test_camel_case_multi(self):
        assert _word_initials("ReadKit") == "rk"


class TestCharsInOrder:
    def test_basic_match(self):
        assert _chars_in_order("sfr", "safari") is True

    def test_no_match(self):
        assert _chars_in_order("xyz", "safari") is False

    def test_empty_query(self):
        assert _chars_in_order("", "safari") is True

    def test_same_string(self):
        assert _chars_in_order("safari", "safari") is True

    def test_partial_order_mismatch(self):
        assert _chars_in_order("ras", "safari") is False


class TestFuzzyMatch:
    def test_exact_prefix(self):
        matched, score = fuzzy_match("saf", "Safari")
        assert matched is True
        assert score == 100

    def test_exact_match(self):
        matched, score = fuzzy_match("safari", "Safari")
        assert matched is True
        assert score == 100

    def test_substring_match(self):
        matched, score = fuzzy_match("far", "Safari")
        assert matched is True
        assert score == 60

    def test_camel_case_initials(self):
        matched, score = fuzzy_match("dd", "DragonDrop")
        assert matched is True
        assert score == 80

    def test_word_initials(self):
        matched, score = fuzzy_match("sc", "System Configuration")
        assert matched is True
        assert score == 80

    def test_scattered_chars(self):
        matched, score = fuzzy_match("sfr", "Safari")
        assert matched is True
        assert score == 40

    def test_no_match(self):
        matched, score = fuzzy_match("xyz", "Safari")
        assert matched is False
        assert score == 0

    def test_empty_query(self):
        matched, score = fuzzy_match("", "Safari")
        assert matched is False
        assert score == 0

    def test_case_insensitive(self):
        matched, score = fuzzy_match("SAF", "safari")
        assert matched is True
        assert score == 100

    def test_initials_multi_word(self):
        matched, score = fuzzy_match("vsc", "Visual Studio Code")
        assert matched is True
        assert score == 80

    def test_readkit_initials(self):
        matched, score = fuzzy_match("rk", "ReadKit")
        assert matched is True
        assert score == 80

    def test_prefix_beats_substring(self):
        _, prefix_score = fuzzy_match("sa", "Safari")
        _, sub_score = fuzzy_match("af", "Safari")
        assert prefix_score > sub_score

    def test_initials_beats_scattered(self):
        _, initials_score = fuzzy_match("dd", "DragonDrop")
        _, scattered_score = fuzzy_match("dp", "DragonDrop")
        assert initials_score > scattered_score

    def test_multi_term_all_match(self):
        matched, score = fuzzy_match("btc euc", "aws-euc1a-btc-fw-12")
        assert matched is True
        assert score == 60  # both are substring matches

    def test_multi_term_partial_miss(self):
        matched, score = fuzzy_match("btc xyz", "aws-euc1a-btc-fw-12")
        assert matched is False

    def test_multi_term_order_irrelevant(self):
        _, score1 = fuzzy_match("btc euc", "aws-euc1a-btc-fw-12")
        _, score2 = fuzzy_match("euc btc", "aws-euc1a-btc-fw-12")
        assert score1 == score2


class TestFuzzyMatchFields:
    def test_single_term_matches_any_field(self):
        matched, score = fuzzy_match_fields("rsync", ("deploy", "rsync -avz ops@aws"))
        assert matched is True
        assert score == 100  # prefix match

    def test_multi_term_and_logic(self):
        """'rsync merger' should match when both terms hit across fields."""
        matched, score = fuzzy_match_fields(
            "rsync merger", ("deploy", "", "rsync -avz ops@aws-merger-00", ""),
        )
        assert matched is True
        assert score > 0

    def test_multi_term_partial_miss(self):
        """Fails when one term doesn't match any field."""
        matched, score = fuzzy_match_fields(
            "rsync nonexistent", ("deploy", "", "rsync -avz ops@aws-merger-00", ""),
        )
        assert matched is False

    def test_empty_query(self):
        matched, score = fuzzy_match_fields("", ("foo", "bar"))
        assert matched is False

    def test_single_term_degrades_to_fuzzy_match(self):
        """Single-term query should give the same result as fuzzy_match best."""
        matched, score = fuzzy_match_fields("saf", ("Safari", "Web browser"))
        fm_matched, fm_score = fuzzy_match("saf", "Safari")
        assert matched == fm_matched
        assert score == fm_score

    def test_score_averages_across_terms(self):
        # "saf" prefix-matches "Safari" (100), "web" prefix-matches "Web browser" (100)
        matched, score = fuzzy_match_fields("saf web", ("Safari", "Web browser"))
        assert matched is True
        assert score == 100  # avg of 100 + 100

    def test_terms_can_match_same_field(self):
        """Both terms can match the same field."""
        matched, score = fuzzy_match_fields(
            "rsync aws", ("", "", "rsync -avz ops@aws-merger-00", ""),
        )
        assert matched is True


class TestPinyinMatch:
    """Pinyin search for Chinese text."""

    def test_full_pinyin_prefix(self):
        """Full pinyin prefix matches Chinese text."""
        matched, score = fuzzy_match("xitong", "系统设置")
        assert matched is True
        assert score == 75

    def test_full_pinyin_exact(self):
        """Full pinyin matches entire Chinese text."""
        matched, score = fuzzy_match("xitongshezhi", "系统设置")
        assert matched is True
        assert score == 75

    def test_full_pinyin_substring(self):
        """Pinyin substring matches Chinese text."""
        matched, score = fuzzy_match("shezhi", "系统设置")
        assert matched is True
        assert score == 65

    def test_pinyin_initials_prefix(self):
        """Pinyin initials prefix matches Chinese text.

        "系统设置" → initials "xtsz" (xi tong she zhi).
        """
        matched, score = fuzzy_match("xtsz", "系统设置")
        assert matched is True
        assert score == 70

    def test_pinyin_initials_partial(self):
        """Partial pinyin initials match via scattered chars in initials."""
        matched, score = fuzzy_match("xsz", "系统设置")
        assert matched is True
        assert score == 50

    def test_pinyin_scattered_full(self):
        """Scattered chars in full pinyin match."""
        matched, score = fuzzy_match("xtsh", "系统设置")
        assert matched is True
        assert score >= 40

    def test_pinyin_case_insensitive(self):
        """Pinyin matching is case-insensitive."""
        matched, score = fuzzy_match("XTSZ", "系统设置")
        assert matched is True
        assert score == 70

    def test_pinyin_no_match(self):
        """Non-matching pinyin returns False."""
        matched, _ = fuzzy_match("abc", "系统设置")
        assert matched is False

    def test_ascii_against_ascii_no_pinyin(self):
        """ASCII query against ASCII text should not trigger pinyin path."""
        matched, score = fuzzy_match("saf", "Safari")
        assert matched is True
        assert score == 100  # prefix match, not pinyin

    def test_pinyin_mixed_text(self):
        """Chinese text mixed with ASCII: '微信 WeChat'."""
        matched, score = fuzzy_match("wx", "微信")
        assert matched is True
        assert score == 70  # pinyin initials

    def test_pinyin_fields_match(self):
        """fuzzy_match_fields works with pinyin across fields."""
        matched, score = fuzzy_match_fields(
            "xtsh", ("System Settings", "系统设置"),
        )
        assert matched is True
        assert score > 0

    def test_pinyin_score_ordering(self):
        """Full pinyin prefix scores higher than initials."""
        _, full_score = fuzzy_match("xitong", "系统设置")
        _, init_score = fuzzy_match("xtsh", "系统设置")
        assert full_score > init_score


class TestPinyinDisabled:
    """Pinyin matching can be disabled via set_pinyin_enabled."""

    def setup_method(self):
        set_pinyin_enabled(False)

    def teardown_method(self):
        set_pinyin_enabled(True)

    def test_pinyin_skipped_when_disabled(self):
        """Pinyin queries should not match Chinese text when disabled."""
        matched, _ = fuzzy_match("xitong", "系统设置")
        assert matched is False

    def test_pinyin_initials_skipped(self):
        matched, _ = fuzzy_match("xtsz", "系统设置")
        assert matched is False

    def test_ascii_match_still_works(self):
        """Non-pinyin strategies remain unaffected."""
        matched, score = fuzzy_match("saf", "Safari")
        assert matched is True
        assert score == 100

    def test_scattered_still_works(self):
        matched, score = fuzzy_match("sfr", "Safari")
        assert matched is True
        assert score == 40
