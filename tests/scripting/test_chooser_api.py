"""Tests for the Chooser API."""

from unittest.mock import patch

from wenzi.scripting.sources import ChooserItem, ChooserSource, ModifierAction
from wenzi.scripting.api.chooser import (
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

    def test_pick_registers_source_with_prefix_and_shows(self):
        api = ChooserAPI()
        results = []
        items = [{"title": "A"}, {"title": "B"}]

        with patch("PyObjCTools.AppHelper.callAfter") as mock_call:
            api.pick(items, callback=lambda item: results.append(item))
            mock_call.assert_called_once()
            # Should pre-fill with "? " prefix for source isolation
            _, kwargs = mock_call.call_args
            assert kwargs.get("initial_query") == "? "
            assert kwargs.get("placeholder") == "Choose..."

        # A temporary source should be registered with prefix "?"
        source_names = list(api.panel._sources.keys())
        pick_sources = [n for n in source_names if n.startswith("__pick_")]
        assert len(pick_sources) == 1
        src = api.panel._sources[pick_sources[0]]
        assert src.prefix == "?"

        # Search should return items when query is empty
        found = src.search("")
        assert len(found) == 2
        assert found[0].title == "A"
        assert found[1].title == "B"

    def test_pick_assigns_stable_item_ids(self):
        api = ChooserAPI()
        items = [{"title": "A"}, {"title": "B", "item_id": "custom"}]

        with patch("PyObjCTools.AppHelper.callAfter"):
            api.pick(items, callback=lambda item: None)

        pick_name = [n for n in api.panel._sources if n.startswith("__pick_")][0]
        src = api.panel._sources[pick_name]
        found = src.search("")
        # First item gets auto-assigned ID, second keeps its custom ID
        assert found[0].item_id.startswith("__pick_")
        assert found[1].item_id == "custom"

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

    def test_pick_callback_on_select_via_event(self):
        """Selection is tracked via the synchronous select event, then
        callback is called from _on_close — avoiding the deferred-action
        race condition."""
        api = ChooserAPI()
        results = []
        items = [{"title": "X", "subtitle": "x-sub"}]

        with patch("PyObjCTools.AppHelper.callAfter") as mock_call:
            api.pick(items, callback=lambda item: results.append(item))
            _, kwargs = mock_call.call_args
            on_close = kwargs.get("on_close")

        pick_name = [n for n in api.panel._sources if n.startswith("__pick_")][0]
        src = api.panel._sources[pick_name]
        found = src.search("")

        # Simulate the select event fired synchronously by _execute_item
        api._fire_event("select", {
            "title": found[0].title,
            "subtitle": found[0].subtitle,
            "item_id": found[0].item_id,
        })

        # Then close runs → _on_close → callback with selected item
        on_close()
        assert len(results) == 1
        assert results[0] == {"title": "X", "subtitle": "x-sub"}

    def test_pick_callback_none_on_close_without_select(self):
        api = ChooserAPI()
        results = []

        with patch("PyObjCTools.AppHelper.callAfter") as mock_call:
            api.pick(
                [{"title": "A"}],
                callback=lambda item: results.append(item),
            )
            _, kwargs = mock_call.call_args
            on_close = kwargs.get("on_close")

        assert on_close is not None
        # No select event fired — user dismissed the panel
        on_close()
        assert results == [None]

    def test_pick_cleans_up_select_handler_on_close(self):
        api = ChooserAPI()

        with patch("PyObjCTools.AppHelper.callAfter") as mock_call:
            api.pick([{"title": "A"}], callback=lambda item: None)
            _, kwargs = mock_call.call_args
            on_close = kwargs.get("on_close")

        # Handler should be registered
        assert len(api._event_handlers.get("select", [])) == 1
        on_close()
        # Handler should be removed after close
        assert len(api._event_handlers.get("select", [])) == 0

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

    def test_pick_custom_placeholder(self):
        api = ChooserAPI()

        with patch("PyObjCTools.AppHelper.callAfter") as mock_call:
            api.pick(
                [{"title": "A"}],
                callback=lambda item: None,
                placeholder="Select a project...",
            )
            _, kwargs = mock_call.call_args
            assert kwargs.get("placeholder") == "Select a project..."

    def test_pick_default_placeholder(self):
        api = ChooserAPI()

        with patch("PyObjCTools.AppHelper.callAfter") as mock_call:
            api.pick([{"title": "A"}], callback=lambda item: None)
            _, kwargs = mock_call.call_args
            assert kwargs.get("placeholder") == "Choose..."


    def test_open_settings_event(self):
        """Cmd+, in JS sends openSettings → panel fires event → handler called."""
        api = ChooserAPI()
        called = []
        api._event_handlers.setdefault("openSettings", []).append(
            lambda: called.append(True)
        )
        # Simulate the JS message that Cmd+, would send
        with patch("PyObjCTools.AppHelper.callAfter"):
            api.panel._handle_js_message({"type": "openSettings"})
        assert called == [True]


class TestChooserAPICommands:
    def test_register_command(self):
        api = ChooserAPI()
        api.register_command(
            name="test-cmd",
            title="Test Command",
            action=lambda args: None,
        )
        assert "test-cmd" in api._command_source._commands

    def test_unregister_command(self):
        api = ChooserAPI()
        api.register_command(
            name="test-cmd",
            title="Test Command",
            action=lambda args: None,
        )
        api.unregister_command("test-cmd")
        assert "test-cmd" not in api._command_source._commands

    def test_command_decorator(self):
        api = ChooserAPI()
        called = []

        @api.command("greet", title="Greet", subtitle="Say hello")
        def greet(args):
            called.append(args)

        assert "greet" in api._command_source._commands
        entry = api._command_source._commands["greet"]
        assert entry.title == "Greet"
        assert entry.subtitle == "Say hello"
        entry.action("World")
        assert called == ["World"]

    def test_command_decorator_with_modifiers(self):
        api = ChooserAPI()
        alt_called = []

        @api.command(
            "deploy", title="Deploy",
            modifiers={"alt": {"subtitle": "Force", "action": lambda a: alt_called.append(a)}},
        )
        def deploy(args):
            pass

        entry = api._command_source._commands["deploy"]
        assert entry.modifiers is not None
        assert "alt" in entry.modifiers
        assert entry.modifiers["alt"].subtitle == "Force"

    def test_command_source_not_registered_on_init(self):
        api = ChooserAPI()
        # Command source is registered lazily via _ensure_command_source
        assert "commands" not in api.panel._sources

    def test_ensure_command_source_registers(self):
        api = ChooserAPI()
        api._ensure_command_source()
        assert "commands" in api.panel._sources
        assert "commands-promoted" in api.panel._sources
        src = api.panel._sources["commands"]
        assert src.prefix == ">"
        promoted_src = api.panel._sources["commands-promoted"]
        assert promoted_src.prefix is None

    def test_ensure_command_source_after_clear(self):
        api = ChooserAPI()
        api.panel._sources.clear()
        assert "commands" not in api.panel._sources
        api._ensure_command_source()
        assert "commands" in api.panel._sources
        assert "commands-promoted" in api.panel._sources

    def test_promoted_command(self):
        api = ChooserAPI()
        api._ensure_command_source()
        called = []

        @api.command("reload", title="Reload Scripts", promoted=True)
        def reload(args):
            called.append(args)

        entry = api._command_source._commands["reload"]
        assert entry.promoted is True

        # Should appear in promoted (unprefixed) source
        promoted_src = api.panel._sources["commands-promoted"]
        items = promoted_src.search("reload")
        assert len(items) == 1
        assert items[0].title == "Reload Scripts"

    def test_non_promoted_command_not_in_main_search(self):
        api = ChooserAPI()
        api._ensure_command_source()
        api.register_command(
            name="debug-log", title="Debug Log",
            action=lambda args: None,
        )
        promoted_src = api.panel._sources["commands-promoted"]
        items = promoted_src.search("debug")
        assert items == []


class TestHelpCommand:
    def test_help_registered_on_ensure(self):
        api = ChooserAPI()
        api._ensure_command_source()
        assert "help" in api._command_source._commands
        entry = api._command_source._commands["help"]
        assert entry.promoted is True

    def test_help_not_re_registered(self):
        """Calling _ensure_command_source twice should not trigger overwrite."""
        api = ChooserAPI()
        api._ensure_command_source()
        entry1 = api._command_source._commands["help"]
        api._ensure_command_source()
        entry2 = api._command_source._commands["help"]
        assert entry1 is entry2

    def test_help_visible_in_promoted_search(self):
        api = ChooserAPI()
        api._ensure_command_source()
        promoted_src = api.panel._sources["commands-promoted"]
        items = promoted_src.search("help")
        assert len(items) == 1
        assert items[0].title == "Help"

    def test_help_visible_in_prefixed_search(self):
        api = ChooserAPI()
        api._ensure_command_source()
        cmd_src = api.panel._sources["commands"]
        items = cmd_src.search("help")
        assert any(i.title == "Help" for i in items)

    def test_help_action_calls_pick_with_prefixed_sources(self):
        api = ChooserAPI()
        api._ensure_command_source()

        # Register a source with prefix and description
        src = ChooserSource(
            name="clipboard", prefix="cb", search=lambda q: [],
            description="Clipboard history",
        )
        api.register_source(src)

        pick_calls = []

        def mock_pick(items, callback, placeholder="Choose..."):
            pick_calls.append(items)

        api.pick = mock_pick

        # Execute help action
        entry = api._command_source._commands["help"]
        entry.action("")

        assert len(pick_calls) == 1
        items = pick_calls[0]
        # Should include "cb" (clipboard) and ">" (commands)
        prefixes = [i["subtitle"] for i in items]
        assert any("cb" in p for p in prefixes)
        assert any(">" in p for p in prefixes)

    def test_help_action_filters_by_args(self):
        api = ChooserAPI()
        api._ensure_command_source()

        api.register_source(ChooserSource(
            name="clipboard", prefix="cb", search=lambda q: [],
            description="Clipboard history",
        ))
        api.register_source(ChooserSource(
            name="files", prefix="f", search=lambda q: [],
            description="Search files",
        ))

        pick_calls = []
        api.pick = lambda items, callback, placeholder="": pick_calls.append(items)

        entry = api._command_source._commands["help"]
        entry.action("clip")

        assert len(pick_calls) == 1
        items = pick_calls[0]
        # Only clipboard should match "clip"
        assert len(items) == 1
        assert items[0]["title"] == "Clipboard history"

    def test_help_action_skips_sources_without_prefix(self):
        api = ChooserAPI()
        api._ensure_command_source()

        # App source has no prefix
        api.register_source(ChooserSource(
            name="apps", prefix=None, search=lambda q: [],
            description="Search applications",
        ))
        api.register_source(ChooserSource(
            name="clipboard", prefix="cb", search=lambda q: [],
            description="Clipboard history",
        ))

        pick_calls = []
        api.pick = lambda items, callback, placeholder="": pick_calls.append(items)

        entry = api._command_source._commands["help"]
        entry.action("")

        items = pick_calls[0]
        titles = [i["title"] for i in items]
        assert "Search applications" not in titles
        assert "Clipboard history" in titles

    def test_help_uses_source_name_as_fallback(self):
        api = ChooserAPI()
        api._ensure_command_source()

        api.register_source(ChooserSource(
            name="my-source", prefix="ms", search=lambda q: [],
            # No description
        ))

        pick_calls = []
        api.pick = lambda items, callback, placeholder="": pick_calls.append(items)

        entry = api._command_source._commands["help"]
        entry.action("")

        items = pick_calls[0]
        titles = [i["title"] for i in items]
        assert "my-source" in titles


class TestQuitAllCommand:
    def test_quit_all_registered_on_ensure(self):
        api = ChooserAPI()
        api._ensure_command_source()
        assert "quit-all" in api._command_source._commands
        entry = api._command_source._commands["quit-all"]
        assert entry.promoted is True

    def test_quit_all_not_re_registered(self):
        """Calling _ensure_command_source twice should not trigger overwrite."""
        api = ChooserAPI()
        api._ensure_command_source()
        entry1 = api._command_source._commands["quit-all"]
        api._ensure_command_source()
        entry2 = api._command_source._commands["quit-all"]
        assert entry1 is entry2

    def test_quit_all_visible_in_promoted_search(self):
        api = ChooserAPI()
        api._ensure_command_source()
        promoted_src = api.panel._sources["commands-promoted"]
        items = promoted_src.search("quit")
        assert len(items) >= 1
        assert any(i.title == "Quit All Applications" for i in items)

    def test_quit_all_visible_in_prefixed_search(self):
        api = ChooserAPI()
        api._ensure_command_source()
        cmd_src = api.panel._sources["commands"]
        items = cmd_src.search("quit")
        assert any(i.title == "Quit All Applications" for i in items)

    def test_quit_all_action_terminates_regular_apps(self):
        """Action should terminate regular apps, skip Finder and self."""
        api = ChooserAPI()
        api._ensure_command_source()

        own_pid = __import__("os").getpid()

        class FakeApp:
            def __init__(self, pid, bundle, policy):
                self._pid = pid
                self._bundle = bundle
                self._policy = policy
                self.terminated = False

            def activationPolicy(self):
                return self._policy

            def processIdentifier(self):
                return self._pid

            def bundleIdentifier(self):
                return self._bundle

            def terminate(self):
                self.terminated = True

        REGULAR = 0
        ACCESSORY = 1
        PROHIBITED = 2

        safari = FakeApp(1001, "com.apple.Safari", REGULAR)
        finder = FakeApp(1002, "com.apple.finder", REGULAR)
        self_app = FakeApp(own_pid, "com.wenzi.app", REGULAR)
        bg_service = FakeApp(1003, "com.example.daemon", PROHIBITED)
        menubar = FakeApp(1004, "com.example.menubar", ACCESSORY)
        notes = FakeApp(1005, "com.apple.Notes", REGULAR)

        fake_apps = [safari, finder, self_app, bg_service, menubar, notes]

        class FakeWorkspace:
            def runningApplications(self):
                return fake_apps

        with patch.dict("sys.modules", {"AppKit": __import__("types").SimpleNamespace(
            NSWorkspace=type("NSWorkspace", (), {
                "sharedWorkspace": staticmethod(lambda: FakeWorkspace()),
            }),
            NSApplicationActivationPolicyRegular=REGULAR,
        )}):
            entry = api._command_source._commands["quit-all"]
            entry.action("")

        # Regular apps (not Finder, not self) should be terminated
        assert safari.terminated is True
        assert notes.terminated is True
        # Finder, self, background, menubar should NOT be terminated
        assert finder.terminated is False
        assert self_app.terminated is False
        assert bg_service.terminated is False
        assert menubar.terminated is False
