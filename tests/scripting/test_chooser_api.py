"""Tests for the Chooser API."""

from unittest.mock import patch

from voicetext.scripting.sources import ChooserItem, ChooserSource
from voicetext.scripting.api.chooser import ChooserAPI


class TestChooserAPI:
    def test_register_source(self):
        api = ChooserAPI()
        src = ChooserSource(name="test", search=lambda q: [])
        api.register_source(src)
        assert "test" in api.panel._sources

    def test_source_decorator(self):
        api = ChooserAPI()

        @api.source("bookmarks", prefix=">bm", priority=5)
        def search_bm(query):
            return [{"title": "GitHub", "subtitle": "https://github.com"}]

        assert "bookmarks" in api.panel._sources
        src = api.panel._sources["bookmarks"]
        assert src.prefix == ">bm"
        assert src.priority == 5

        # Test that the search function wraps dicts into ChooserItems
        items = src.search("git")
        assert len(items) == 1
        assert isinstance(items[0], ChooserItem)
        assert items[0].title == "GitHub"

    def test_source_decorator_with_action(self):
        api = ChooserAPI()
        called = []

        @api.source("test")
        def search_test(query):
            return [
                {
                    "title": "Test",
                    "action": lambda: called.append(True),
                    "reveal_path": "/some/path",
                },
            ]

        items = api.panel._sources["test"].search("t")
        assert items[0].action is not None
        items[0].action()
        assert called == [True]
        assert items[0].reveal_path == "/some/path"

    def test_source_decorator_returns_none(self):
        api = ChooserAPI()

        @api.source("empty")
        def search_empty(query):
            return None

        items = api.panel._sources["empty"].search("test")
        assert items == []

    def test_show_calls_panel(self):
        api = ChooserAPI()
        with patch("PyObjCTools.AppHelper.callAfter") as mock_call:
            api.show()
            mock_call.assert_called_once()

    def test_close_calls_panel(self):
        api = ChooserAPI()
        with patch("PyObjCTools.AppHelper.callAfter") as mock_call:
            api.close()
            mock_call.assert_called_once()

    def test_toggle_calls_panel(self):
        api = ChooserAPI()
        with patch("PyObjCTools.AppHelper.callAfter") as mock_call:
            api.toggle()
            mock_call.assert_called_once()
