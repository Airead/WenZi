"""Tests for emoji_search plugin."""

import pytest


class TestLoadEmojiData:
    def test_loads_non_empty_records(self):
        from emoji_search import _load_emoji_data

        records, group_map, groups = _load_emoji_data()
        assert len(records) > 1000
        assert len(group_map) > 0
        assert len(groups) >= 5
        first = records[0]
        assert "char" in first
        assert "name_en" in first
        assert "name_zh" in first
        assert "group_en" in first
        assert "group_zh" in first
        assert "subgroup_en" in first
        assert "subgroup_zh" in first

    def test_first_record_is_grinning_face(self):
        from emoji_search import _load_emoji_data

        records, _, _ = _load_emoji_data()
        first = records[0]
        assert first["char"] == "😀"
        assert first["name_en"] == "grinning face"
        assert first["name_zh"] == "笑脸"

    def test_groups_have_char(self):
        from emoji_search import _load_emoji_data

        _, _, groups = _load_emoji_data()
        for g in groups:
            assert g["char"]
            assert g["name_zh"] or g["name_en"]
            assert "chars" in g
            assert len(g["chars"]) > 0


class TestSearchEmojis:
    @pytest.fixture(scope="class")
    def data(self):
        from emoji_search import _load_emoji_data

        return _load_emoji_data()

    def test_empty_query_returns_nothing(self, data):
        from emoji_search import _search_emojis

        records, group_map, _ = data
        assert _search_emojis("", records, group_map) == []
        assert _search_emojis("   ", records, group_map) == []

    def test_search_by_english_name(self, data):
        from emoji_search import _search_emojis

        records, group_map, _ = data
        results = _search_emojis("cat", records, group_map)
        chars = [r["char"] for r in results]
        assert "🐱" in chars

    def test_search_by_chinese_name(self, data):
        from emoji_search import _search_emojis

        records, group_map, _ = data
        results = _search_emojis("笑脸", records, group_map)
        chars = [r["char"] for r in results]
        assert "😀" in chars

    def test_search_by_pinyin(self, data):
        from emoji_search import _search_emojis

        records, group_map, _ = data
        results = _search_emojis("xiaolian", records, group_map)
        chars = [r["char"] for r in results]
        assert "😀" in chars

    def test_search_limits_results(self, data):
        from emoji_search import _MAX_RESULTS, _search_emojis

        records, group_map, _ = data
        results = _search_emojis("face", records, group_map)
        assert len(results) <= _MAX_RESULTS

    def test_search_by_group_name_zh(self, data):
        from emoji_search import _search_emojis

        records, group_map, _ = data
        results = _search_emojis("动物与自然", records, group_map)
        assert len(results) >= 10
        for rec in results:
            assert rec["group_zh"] == "动物与自然"

    def test_search_by_group_name_en(self, data):
        from emoji_search import _search_emojis

        records, group_map, _ = data
        results = _search_emojis("smileys & emotion", records, group_map)
        assert len(results) >= 10
        for rec in results:
            assert rec["group_en"] == "Smileys & Emotion"

    def test_search_by_subgroup_name(self, data):
        from emoji_search import _search_emojis

        records, group_map, _ = data
        results = _search_emojis("face-smiling", records, group_map)
        assert len(results) >= 5
        for rec in results:
            assert rec["subgroup_en"] == "face-smiling"

    def test_search_with_group_filter_zh(self, data):
        from emoji_search import _search_emojis

        records, group_map, _ = data
        results = _search_emojis("mao @动物与自然", records, group_map)
        assert len(results) >= 1
        for rec in results:
            assert rec["group_zh"] == "动物与自然"

    def test_search_with_group_filter_en(self, data):
        from emoji_search import _search_emojis

        records, group_map, _ = data
        results = _search_emojis("face @smileys & emotion", records, group_map)
        assert len(results) >= 1
        for rec in results:
            assert rec["group_en"] == "Smileys & Emotion"

    def test_search_with_subgroup_filter(self, data):
        from emoji_search import _search_emojis

        records, group_map, _ = data
        results = _search_emojis("xiao @脸-微笑", records, group_map)
        assert len(results) >= 1
        for rec in results:
            assert rec["subgroup_zh"] == "脸-微笑"

    def test_search_only_group_filter(self, data):
        from emoji_search import _search_emojis

        records, group_map, _ = data
        results = _search_emojis("@face-smiling", records, group_map)
        assert len(results) >= 5
        for rec in results:
            assert rec["subgroup_en"] == "face-smiling"

    def test_search_with_nonexistent_group_filter_falls_back(self, data):
        from emoji_search import _search_emojis

        records, group_map, _ = data
        results = _search_emojis("cat @nonexistent-group", records, group_map)
        # Falls back to searching all records when group filter matches nothing.
        chars = [r["char"] for r in results]
        assert "🐱" in chars

    def test_search_with_fuzzy_group_filter(self, data):
        from emoji_search import _search_emojis

        records, group_map, _ = data
        results = _search_emojis("mao @dongwu", records, group_map)
        assert len(results) >= 1
        for rec in results:
            assert rec["group_zh"] == "动物与自然"

    def test_search_at_face_eye(self, data):
        from emoji_search import _search_emojis

        records, group_map, _ = data
        results = _search_emojis("@face eye", records, group_map)
        chars = [r["char"] for r in results]
        # Should include emoji explicitly related to eyes.
        assert "😃" in chars  # grinning face with big eyes
        # Should NOT include plain grinning face which has no eye relation.
        assert "😀" not in chars


class TestParseQuery:
    def test_no_at_returns_whole_query(self):
        from emoji_search import _load_emoji_data, _parse_query

        _, group_map, _ = _load_emoji_data()
        assert _parse_query("cat", group_map) == ("cat", None)

    def test_at_face_eye_splits_correctly(self):
        from emoji_search import _load_emoji_data, _parse_query

        _, group_map, _ = _load_emoji_data()
        keyword, group = _parse_query("@face eye", group_map)
        assert keyword == "eye"
        assert group == "face"

    def test_at_with_multiword_group(self):
        from emoji_search import _load_emoji_data, _parse_query

        _, group_map, _ = _load_emoji_data()
        keyword, group = _parse_query("face @smileys & emotion", group_map)
        assert keyword == "face"
        assert group == "smileys & emotion"

    def test_at_with_keyword_before_and_after(self):
        from emoji_search import _load_emoji_data, _parse_query

        _, group_map, _ = _load_emoji_data()
        keyword, group = _parse_query("mao @dongwu miao", group_map)
        assert keyword == "mao miao"
        assert group == "dongwu"

    def test_at_only_returns_empty_filter(self):
        from emoji_search import _load_emoji_data, _parse_query

        _, group_map, _ = _load_emoji_data()
        keyword, group = _parse_query("@", group_map)
        assert keyword == ""
        assert group == ""


class TestGroupItems:
    def test_build_group_items(self):
        from emoji_search import _group_preview_html, _load_emoji_data

        _, _, groups = _load_emoji_data()
        assert len(groups) >= 5
        for g in groups:
            preview = _group_preview_html(g)
            # Preview should contain multiple emoji from the group
            for c in g["chars"][:5]:
                assert c in preview


class TestEmojiItem:
    def test_item_structure(self):
        from emoji_search import _emoji_item

        class FakeWz:
            class FakePasteboard:
                def set(self, text):
                    self.last = text

            def __init__(self):
                self.pasteboard = self.FakePasteboard()
                self.alerts = []

            def type_text(self, text, method="auto"):
                self.last_typed = (text, method)

            def alert(self, text, duration=2.0):
                self.alerts.append((text, duration))

        wz = FakeWz()
        rec = {
            "char": "🐱",
            "name_en": "cat face",
            "name_zh": "猫脸",
            "group_en": "Animals & Nature",
            "group_zh": "动物与自然",
            "subgroup_en": "animal-cat",
            "subgroup_zh": "动物-猫",
        }
        item = _emoji_item(wz, rec)

        assert item["title"] == "🐱"
        assert "猫脸" in item["subtitle"]
        assert "cat face" in item["subtitle"]
        assert item["item_id"] == "emoji:🐱"
        assert item["preview"]["type"] == "html"
        assert "🐱" in item["preview"]["content"]

        # Enter pastes
        item["action"]()
        assert wz.last_typed == ("🐱", "paste")

        # Cmd+Enter copies via secondary_action
        item["secondary_action"]()
        assert wz.pasteboard.last == "🐱"
        assert wz.alerts == [("Emoji copied", 1.2)]
