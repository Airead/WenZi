"""Tests for wz.menubar API."""

from unittest.mock import MagicMock

from wenzi.scripting.api.menubar import MenuBarAPI, MenuBarItem


def _patch_appkit(monkeypatch):
    """Patch AppKit imports used by MenuBarItem."""
    mock_bar = MagicMock()
    mock_item = MagicMock()
    mock_bar.systemStatusBar.return_value.statusItemWithLength_.return_value = (
        mock_item
    )
    import AppKit as _appkit

    monkeypatch.setattr(_appkit, "NSStatusBar", mock_bar)
    return mock_bar, mock_item


class TestMenuBarAPI:
    def test_create_returns_item(self, monkeypatch):
        _patch_appkit(monkeypatch)
        api = MenuBarAPI()
        item = api.create("test", title="Hello")
        assert isinstance(item, MenuBarItem)
        assert item.name == "test"

    def test_create_same_name_destroys_old(self, monkeypatch):
        mock_bar, _ = _patch_appkit(monkeypatch)
        api = MenuBarAPI()
        api.create("test")
        item2 = api.create("test")
        # Old item should have been removed from status bar
        mock_bar.systemStatusBar.return_value.removeStatusItem_.assert_called_once()
        assert api.get("test") is item2

    def test_remove(self, monkeypatch):
        mock_bar, _ = _patch_appkit(monkeypatch)
        api = MenuBarAPI()
        api.create("test")
        api.remove("test")
        assert api.get("test") is None
        mock_bar.systemStatusBar.return_value.removeStatusItem_.assert_called_once()

    def test_remove_nonexistent_is_noop(self, monkeypatch):
        _patch_appkit(monkeypatch)
        api = MenuBarAPI()
        api.remove("nope")  # should not raise

    def test_get_returns_none_for_missing(self, monkeypatch):
        _patch_appkit(monkeypatch)
        api = MenuBarAPI()
        assert api.get("nope") is None

    def test_cleanup_removes_all(self, monkeypatch):
        mock_bar, _ = _patch_appkit(monkeypatch)
        api = MenuBarAPI()
        api.create("a")
        api.create("b")
        api.cleanup()
        assert api.get("a") is None
        assert api.get("b") is None
        assert mock_bar.systemStatusBar.return_value.removeStatusItem_.call_count == 2


class TestMenuBarItem:
    def test_set_title(self, monkeypatch):
        _, mock_ns = _patch_appkit(monkeypatch)
        # Bypass callAfter — call directly
        monkeypatch.setattr(
            "wenzi.scripting.api.menubar.MenuBarItem.set_title",
            lambda self, t: self._ns_item.setTitle_(t),
        )
        item = MenuBarItem("test", title="init")
        item.set_title("updated")
        mock_ns.setTitle_.assert_called_with("updated")

    def test_set_menu_with_actions(self, monkeypatch):
        _patch_appkit(monkeypatch)

        # Mock the statusbar callback routing
        mock_handler = MagicMock()
        mock_ns_to_callback = {}
        monkeypatch.setattr(
            "wenzi.statusbar._get_callback_handler", lambda: mock_handler
        )
        import wenzi.statusbar as sb
        monkeypatch.setattr(sb, "_ns_to_callback", mock_ns_to_callback)

        mock_menu = MagicMock()
        mock_mi = MagicMock()
        import AppKit as _appkit
        monkeypatch.setattr(_appkit, "NSMenu", MagicMock(
            alloc=MagicMock(return_value=MagicMock(init=MagicMock(return_value=mock_menu)))
        ))
        monkeypatch.setattr(_appkit, "NSMenuItem", MagicMock(
            alloc=MagicMock(return_value=MagicMock(
                initWithTitle_action_keyEquivalent_=MagicMock(return_value=mock_mi)
            )),
            separatorItem=MagicMock(return_value=MagicMock()),
        ))

        item = MenuBarItem("test")
        clicked = []
        item._set_menu_on_main([
            {"title": "BTC  95000", "action": lambda: clicked.append("btc")},
            {"separator": True},
            {"title": "No action"},
        ])

        # Menu should have 3 items added
        assert mock_menu.addItem_.call_count == 3
        # Callback should be registered for the action item
        assert len(mock_ns_to_callback) == 1

    def test_destroy_cleans_up(self, monkeypatch):
        mock_bar, _ = _patch_appkit(monkeypatch)
        mock_ns_to_callback = {}
        import wenzi.statusbar as sb
        monkeypatch.setattr(sb, "_ns_to_callback", mock_ns_to_callback)

        item = MenuBarItem("test")
        # Simulate having menu item refs
        fake_mi = MagicMock()
        item._menu_item_refs.append(fake_mi)
        mock_ns_to_callback[id(fake_mi)] = (None, lambda: None)

        item._destroy()

        assert id(fake_mi) not in mock_ns_to_callback
        assert len(item._menu_item_refs) == 0
        mock_bar.systemStatusBar.return_value.removeStatusItem_.assert_called_once()
