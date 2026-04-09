"""Tests for the wz.menu API."""

from __future__ import annotations

from unittest.mock import MagicMock

from wenzi.scripting.api.menu import MenuAPI
from wenzi.statusbar import StatusMenuItem


def _build_menu():
    """Build a small menu tree for testing."""
    root = StatusMenuItem("root")

    item_a = StatusMenuItem("Alpha", callback=lambda _: None)
    item_a.state = 1
    root.add(item_a)

    root.add(None)  # separator

    parent = StatusMenuItem("Parent")
    child1 = StatusMenuItem("Child1", callback=lambda _: None)
    child2 = StatusMenuItem("Child2")
    parent.add(child1)
    parent.add(child2)
    root.add(parent)

    item_b = StatusMenuItem("Beta", callback=lambda _: None)
    root.add(item_b)

    return root


class TestMenuList:
    def test_list_empty_when_no_root(self):
        api = MenuAPI()
        assert api.list() == []

    def test_list_returns_top_level_items(self):
        api = MenuAPI()
        api._set_root(_build_menu())
        items = api.list()

        titles = [i["title"] for i in items]
        assert titles == ["Alpha", "Parent", "Beta"]

    def test_list_skips_separators(self):
        api = MenuAPI()
        api._set_root(_build_menu())
        items = api.list()
        # 3 real items, separator excluded
        assert len(items) == 3

    def test_list_includes_children(self):
        api = MenuAPI()
        api._set_root(_build_menu())
        items = api.list()

        parent = [i for i in items if i["title"] == "Parent"][0]
        assert len(parent["children"]) == 2
        assert parent["children"][0]["title"] == "Child1"
        assert parent["children"][1]["title"] == "Child2"

    def test_list_item_fields(self):
        api = MenuAPI()
        api._set_root(_build_menu())
        items = api.list()

        alpha = items[0]
        assert alpha["title"] == "Alpha"
        assert alpha["state"] == 1
        assert alpha["has_action"] is True

        parent = items[1]
        assert parent["has_action"] is False
        assert "children" in parent

    def test_list_flat(self):
        api = MenuAPI()
        api._set_root(_build_menu())
        items = api.list(flat=True)

        titles = [i["title"] for i in items]
        assert titles == ["Alpha", "Parent", "Child1", "Child2", "Beta"]

    def test_list_flat_has_path(self):
        api = MenuAPI()
        api._set_root(_build_menu())
        items = api.list(flat=True)

        paths = {i["title"]: i["path"] for i in items}
        assert paths["Alpha"] == "Alpha"
        assert paths["Child1"] == "Parent > Child1"
        assert paths["Child2"] == "Parent > Child2"


class TestMenuTrigger:
    def test_trigger_returns_false_when_no_root(self):
        api = MenuAPI()
        assert api.trigger("anything") is False

    def test_trigger_returns_false_for_missing_item(self):
        api = MenuAPI()
        api._set_root(_build_menu())
        assert api.trigger("NonExistent") is False

    def test_trigger_returns_false_for_item_without_callback(self):
        api = MenuAPI()
        api._set_root(_build_menu())
        # Child2 has no callback
        assert api.trigger("Parent > Child2") is False

    def test_trigger_calls_callback(self):
        from unittest.mock import patch

        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a: fn(*a)):
            called = []
            api = MenuAPI()
            root = StatusMenuItem("root")
            item = StatusMenuItem("Foo", callback=lambda sender: called.append(sender))
            root.add(item)
            api._set_root(root)

            result = api.trigger("Foo")
            assert result is True
            assert len(called) == 1

    def test_trigger_nested_item(self):
        from unittest.mock import patch

        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a: fn(*a)):
            called = []
            api = MenuAPI()
            root = StatusMenuItem("root")
            parent = StatusMenuItem("Parent")
            child = StatusMenuItem("Child", callback=lambda s: called.append(s))
            parent.add(child)
            root.add(parent)
            api._set_root(root)

            result = api.trigger("Parent > Child")
            assert result is True
            assert len(called) == 1


class TestWZNamespaceIntegration:
    def test_wz_menu_property_returns_menu_api(self):
        from wenzi.scripting.api import _WZNamespace
        from wenzi.scripting.registry import ScriptingRegistry

        registry = ScriptingRegistry()
        wz = _WZNamespace(registry)
        assert wz.menu is not None
        from wenzi.scripting.api.menu import MenuAPI
        assert isinstance(wz.menu, MenuAPI)

    def test_wz_menu_is_same_instance(self):
        from wenzi.scripting.api import _WZNamespace
        from wenzi.scripting.registry import ScriptingRegistry

        registry = ScriptingRegistry()
        wz = _WZNamespace(registry)
        assert wz.menu is wz.menu


class TestAppMenu:
    def test_app_menu_returns_empty_when_no_permission(self, monkeypatch):
        import ApplicationServices as _as

        api = MenuAPI()
        monkeypatch.setattr(
            _as, "AXUIElementCreateApplication",
            MagicMock(side_effect=Exception("no permission")),
        )
        result = api.app_menu(pid=12345)
        assert result == []

    def test_app_menu_returns_empty_for_none_pid(self):
        api = MenuAPI()
        assert api.app_menu() == []

    def test_walk_ax_menu_builds_flat_list(self, monkeypatch):
        import ApplicationServices as _as

        api = MenuAPI()

        ax_save = MagicMock()
        ax_quit = MagicMock()
        ax_file_children = MagicMock()

        def _copy_attr_save(attr, _):
            return {
                "AXTitle": (0, "Save"),
                "AXEnabled": (0, True),
                "AXMenuItemCmdChar": (0, "S"),
                "AXMenuItemCmdModifiers": (0, 0),
                "AXChildren": (-25212, None),
            }.get(attr, (-25212, None))

        def _copy_attr_quit(attr, _):
            return {
                "AXTitle": (0, "Quit"),
                "AXEnabled": (0, True),
                "AXMenuItemCmdChar": (0, "Q"),
                "AXMenuItemCmdModifiers": (0, 0),
                "AXChildren": (-25212, None),
            }.get(attr, (-25212, None))

        def _copy_attr_file_children(attr, _):
            return {
                "AXChildren": (0, [ax_save, ax_quit]),
            }.get(attr, (-25212, None))

        ax_save._copy = _copy_attr_save
        ax_quit._copy = _copy_attr_quit
        ax_file_children._copy = _copy_attr_file_children

        ax_file = MagicMock()

        def _copy_attr_file(attr, _):
            return {
                "AXTitle": (0, "File"),
                "AXEnabled": (0, True),
                "AXMenuItemCmdChar": (0, ""),
                "AXMenuItemCmdModifiers": (0, 0),
                "AXChildren": (0, [ax_file_children]),
            }.get(attr, (-25212, None))

        ax_file._copy = _copy_attr_file

        ax_menu_bar = MagicMock()

        def _copy_attr_bar(attr, _):
            return {
                "AXChildren": (0, [ax_file]),
            }.get(attr, (-25212, None))

        ax_menu_bar._copy = _copy_attr_bar

        monkeypatch.setattr(
            _as, "AXUIElementCopyAttributeValue",
            lambda el, attr, _: el._copy(attr, _),
        )
        items = api._walk_ax_menu(ax_menu_bar)

        assert len(items) == 2
        assert items[0]["title"] == "Save"
        assert items[0]["path"] == "File > Save"
        assert items[0]["enabled"] is True
        assert items[0]["shortcut"] == "⌘S"
        assert items[1]["title"] == "Quit"
        assert items[1]["path"] == "File > Quit"
        assert items[1]["shortcut"] == "⌘Q"

    def test_walk_ax_menu_skips_separators_and_empty_titles(self, monkeypatch):
        import ApplicationServices as _as

        api = MenuAPI()

        ax_sep = MagicMock()
        ax_sep._copy = lambda attr, _: {
            "AXTitle": (0, ""),
            "AXEnabled": (0, False),
            "AXMenuItemCmdChar": (0, ""),
            "AXMenuItemCmdModifiers": (0, 0),
            "AXChildren": (-25212, None),
        }.get(attr, (-25212, None))

        ax_real = MagicMock()
        ax_real._copy = lambda attr, _: {
            "AXTitle": (0, "About"),
            "AXEnabled": (0, True),
            "AXMenuItemCmdChar": (0, ""),
            "AXMenuItemCmdModifiers": (0, 0),
            "AXChildren": (-25212, None),
        }.get(attr, (-25212, None))

        ax_sub = MagicMock()
        ax_sub._copy = lambda attr, _: {
            "AXChildren": (0, [ax_sep, ax_real]),
        }.get(attr, (-25212, None))

        ax_menu = MagicMock()
        ax_menu._copy = lambda attr, _: {
            "AXTitle": (0, "App"),
            "AXEnabled": (0, True),
            "AXMenuItemCmdChar": (0, ""),
            "AXMenuItemCmdModifiers": (0, 0),
            "AXChildren": (0, [ax_sub]),
        }.get(attr, (-25212, None))

        ax_bar = MagicMock()
        ax_bar._copy = lambda attr, _: {
            "AXChildren": (0, [ax_menu]),
        }.get(attr, (-25212, None))

        monkeypatch.setattr(
            _as, "AXUIElementCopyAttributeValue",
            lambda el, attr, _: el._copy(attr, _),
        )
        items = api._walk_ax_menu(ax_bar)

        titles = [i["title"] for i in items]
        assert "About" in titles
        assert "" not in titles
        assert len(items) == 1

    def test_app_menu_uses_previous_app_pid(self, monkeypatch):
        import ApplicationServices as _as

        api = MenuAPI()
        mock_chooser = MagicMock()
        mock_chooser.panel._previous_app.processIdentifier.return_value = 99
        api._set_chooser_api(mock_chooser)

        mock_create = MagicMock()
        monkeypatch.setattr(_as, "AXUIElementCreateApplication", mock_create)
        monkeypatch.setattr(
            _as, "AXUIElementCopyAttributeValue",
            lambda el, attr, _: (-25212, None),
        )

        api.app_menu()
        mock_create.assert_called_once_with(99)

    def test_shortcut_with_shift(self, monkeypatch):
        import ApplicationServices as _as

        api = MenuAPI()

        ax_item = MagicMock()
        ax_item._copy = lambda attr, _: {
            "AXTitle": (0, "Redo"),
            "AXEnabled": (0, True),
            "AXMenuItemCmdChar": (0, "Z"),
            "AXMenuItemCmdModifiers": (0, 1),
            "AXChildren": (-25212, None),
        }.get(attr, (-25212, None))

        ax_sub = MagicMock()
        ax_sub._copy = lambda attr, _: {
            "AXChildren": (0, [ax_item]),
        }.get(attr, (-25212, None))

        ax_parent = MagicMock()
        ax_parent._copy = lambda attr, _: {
            "AXTitle": (0, "Edit"),
            "AXEnabled": (0, True),
            "AXMenuItemCmdChar": (0, ""),
            "AXMenuItemCmdModifiers": (0, 0),
            "AXChildren": (0, [ax_sub]),
        }.get(attr, (-25212, None))

        ax_bar = MagicMock()
        ax_bar._copy = lambda attr, _: {
            "AXChildren": (0, [ax_parent]),
        }.get(attr, (-25212, None))

        monkeypatch.setattr(
            _as, "AXUIElementCopyAttributeValue",
            lambda el, attr, _: el._copy(attr, _),
        )
        items = api._walk_ax_menu(ax_bar)

        assert items[0]["shortcut"] == "⇧⌘Z"

    def test_app_menu_skips_apple_menu(self, monkeypatch):
        """app_menu() should exclude the system Apple menu."""
        import ApplicationServices as _as

        api = MenuAPI()

        # Build AX tree: Apple menu + File menu
        ax_about = MagicMock()
        ax_about._copy = lambda attr, _: {
            "AXTitle": (0, "About"),
            "AXEnabled": (0, True),
            "AXMenuItemCmdChar": (0, ""),
            "AXMenuItemCmdModifiers": (0, 0),
            "AXChildren": (-25212, None),
        }.get(attr, (-25212, None))

        ax_apple_sub = MagicMock()
        ax_apple_sub._copy = lambda attr, _: {
            "AXChildren": (0, [ax_about]),
        }.get(attr, (-25212, None))

        ax_apple = MagicMock()
        ax_apple._copy = lambda attr, _: {
            "AXTitle": (0, "Apple"),
            "AXChildren": (0, [ax_apple_sub]),
        }.get(attr, (-25212, None))

        ax_new_tab = MagicMock()
        ax_new_tab._copy = lambda attr, _: {
            "AXTitle": (0, "New Tab"),
            "AXEnabled": (0, True),
            "AXMenuItemCmdChar": (0, "T"),
            "AXMenuItemCmdModifiers": (0, 0),
            "AXChildren": (-25212, None),
        }.get(attr, (-25212, None))

        ax_file_sub = MagicMock()
        ax_file_sub._copy = lambda attr, _: {
            "AXChildren": (0, [ax_new_tab]),
        }.get(attr, (-25212, None))

        ax_file = MagicMock()
        ax_file._copy = lambda attr, _: {
            "AXTitle": (0, "File"),
            "AXChildren": (0, [ax_file_sub]),
        }.get(attr, (-25212, None))

        ax_menu_bar = MagicMock()
        ax_menu_bar._copy = lambda attr, _: {
            "AXChildren": (0, [ax_apple, ax_file]),
        }.get(attr, (-25212, None))

        ax_app = MagicMock()
        ax_app._copy = lambda attr, _: {
            "AXMenuBar": (0, ax_menu_bar),
        }.get(attr, (-25212, None))

        monkeypatch.setattr(
            _as, "AXUIElementCreateApplication", lambda pid: ax_app,
        )
        monkeypatch.setattr(
            _as, "AXUIElementCopyAttributeValue",
            lambda el, attr, _: el._copy(attr, _),
        )

        items = api.app_menu(pid=123)

        titles = [i["title"] for i in items]
        assert "About" not in titles  # Apple menu excluded
        assert "New Tab" in titles
        assert len(items) == 1
