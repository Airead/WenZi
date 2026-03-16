"""Tests for the Chooser API."""

from unittest.mock import patch

from voicetext.scripting.sources import ChooserItem, ChooserSource, ModifierAction
from voicetext.scripting.api.chooser import (
    ChooserAPI,
    _dict_to_chooser_item,
    _parse_modifiers,
)


class TestParseModifiers:
    def test_none_input(self):
        assert _parse_modifiers(None) is None

    def test_empty_dict(self):
        assert _parse_modifiers({}) is None

    def test_valid_modifiers(self):
        result = _parse_modifiers({
            "alt": {"subtitle": "Show path", "action": lambda: None},
            "cmd": {"subtitle": "Copy"},
        })
        assert result is not None
        assert isinstance(result["alt"], ModifierAction)
        assert result["alt"].subtitle == "Show path"
        assert result["alt"].action is not None
        assert result["cmd"].subtitle == "Copy"
        assert result["cmd"].action is None

    def test_ignores_non_dict_values(self):
        result = _parse_modifiers({"alt": "invalid"})
        assert result is None


class TestDictToChooserItem:
    def test_minimal_dict(self):
        item = _dict_to_chooser_item({"title": "Hello"})
        assert isinstance(item, ChooserItem)
        assert item.title == "Hello"
        assert item.subtitle == ""
        assert item.item_id == ""

    def test_all_fields(self):
        def action_fn():
            pass

        def sec_fn():
            pass

        def del_fn():
            pass

        item = _dict_to_chooser_item({
            "title": "Test",
            "subtitle": "Sub",
            "icon": "data:image/png;base64,abc",
            "item_id": "test-1",
            "action": action_fn,
            "secondary_action": sec_fn,
            "reveal_path": "/tmp/test",
            "modifiers": {"alt": {"subtitle": "Alt action", "action": action_fn}},
            "delete_action": del_fn,
            "preview": {"type": "text", "content": "Preview text"},
        })
        assert item.title == "Test"
        assert item.subtitle == "Sub"
        assert item.icon == "data:image/png;base64,abc"
        assert item.item_id == "test-1"
        assert item.action is action_fn
        assert item.secondary_action is sec_fn
        assert item.reveal_path == "/tmp/test"
        assert item.modifiers is not None
        assert item.modifiers["alt"].subtitle == "Alt action"
        assert item.delete_action is del_fn
        assert item.preview == {"type": "text", "content": "Preview text"}


class TestChooserAPI:
    def test_register_source(self):
        api = ChooserAPI()
        src = ChooserSource(name="test", search=lambda q: [])
        api.register_source(src)
        assert "test" in api.panel._sources

    def test_is_visible_default(self):
        api = ChooserAPI()
        assert api.is_visible is False

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

    def test_source_decorator_all_fields(self):
        """Decorator should pass through all ChooserItem fields."""
        api = ChooserAPI()
        del_called = []
        sec_called = []

        @api.source("full")
        def search_full(query):
            return [{
                "title": "Full Item",
                "subtitle": "sub",
                "icon": "data:image/png;base64,x",
                "item_id": "full-1",
                "action": lambda: None,
                "secondary_action": lambda: sec_called.append(True),
                "reveal_path": "/path",
                "modifiers": {
                    "alt": {"subtitle": "Alt text", "action": lambda: None},
                },
                "delete_action": lambda: del_called.append(True),
                "preview": {"type": "text", "content": "preview"},
            }]

        items = api.panel._sources["full"].search("x")
        assert len(items) == 1
        item = items[0]
        assert item.item_id == "full-1"
        assert item.secondary_action is not None
        item.secondary_action()
        assert sec_called == [True]
        assert item.modifiers is not None
        assert item.modifiers["alt"].subtitle == "Alt text"
        assert item.delete_action is not None
        item.delete_action()
        assert del_called == [True]
        assert item.preview == {"type": "text", "content": "preview"}

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

    def test_pick_registers_source_and_shows(self):
        api = ChooserAPI()
        results = []
        items = [{"title": "A"}, {"title": "B"}]

        with patch("PyObjCTools.AppHelper.callAfter") as mock_call:
            api.pick(items, callback=lambda item: results.append(item))
            mock_call.assert_called_once()

        # A temporary source should be registered
        source_names = list(api.panel._sources.keys())
        pick_sources = [n for n in source_names if n.startswith("__pick_")]
        assert len(pick_sources) == 1

        # Search should return items when query is empty
        src = api.panel._sources[pick_sources[0]]
        found = src.search("")
        assert len(found) == 2
        assert found[0].title == "A"
        assert found[1].title == "B"

    def test_pick_search_filters_by_query(self):
        api = ChooserAPI()
        items = [{"title": "Apple"}, {"title": "Banana"}, {"title": "Avocado"}]

        with patch("PyObjCTools.AppHelper.callAfter"):
            api.pick(items, callback=lambda item: None)

        pick_name = [n for n in api.panel._sources if n.startswith("__pick_")][0]
        src = api.panel._sources[pick_name]

        found = src.search("ap")
        titles = [i.title for i in found]
        assert "Apple" in titles
        assert "Banana" not in titles
        assert "Avocado" not in titles

    def test_pick_callback_on_select(self):
        api = ChooserAPI()
        results = []
        items = [{"title": "X", "subtitle": "x-sub"}]

        with patch("PyObjCTools.AppHelper.callAfter"):
            api.pick(items, callback=lambda item: results.append(item))

        pick_name = [n for n in api.panel._sources if n.startswith("__pick_")][0]
        src = api.panel._sources[pick_name]
        found = src.search("")
        # Simulate user selecting the item
        found[0].action()
        assert len(results) == 1
        assert results[0] == {"title": "X", "subtitle": "x-sub"}

    def test_pick_callback_none_on_close(self):
        api = ChooserAPI()
        results = []

        with patch("PyObjCTools.AppHelper.callAfter") as mock_call:
            api.pick(
                [{"title": "A"}],
                callback=lambda item: results.append(item),
            )
            # Extract the on_close callback from the callAfter call
            _, kwargs = mock_call.call_args
            on_close = kwargs.get("on_close")

        assert on_close is not None
        on_close()
        assert results == [None]

    def test_on_event_decorator(self):
        api = ChooserAPI()
        received = []

        @api.on("select")
        def handler(item):
            received.append(item)

        api._fire_event("select", {"title": "Test"})
        assert received == [{"title": "Test"}]

    def test_on_event_multiple_handlers(self):
        api = ChooserAPI()
        log1, log2 = [], []

        @api.on("close")
        def h1():
            log1.append(True)

        @api.on("close")
        def h2():
            log2.append(True)

        api._fire_event("close")
        assert log1 == [True]
        assert log2 == [True]

    def test_fire_event_handler_error_does_not_propagate(self):
        api = ChooserAPI()

        @api.on("open")
        def bad_handler():
            raise RuntimeError("boom")

        # Should not raise
        api._fire_event("open")
