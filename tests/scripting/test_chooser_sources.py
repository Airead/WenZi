"""Tests for chooser data structures and fuzzy matching."""

from voicetext.scripting.sources import (
    ChooserItem,
    ChooserSource,
    fuzzy_match,
    _word_initials,
    _chars_in_order,
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
