"""Tests for chooser data structures."""

from voicetext.scripting.sources import ChooserItem, ChooserSource


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
        src = ChooserSource(name="clipboard", prefix=">cb", priority=10)
        assert src.prefix == ">cb"
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
