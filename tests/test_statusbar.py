"""Tests for the statusbar module (pure PyObjC replacement for rumps)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from voicetext.statusbar import (
    Response,
    StatusBarApp,
    StatusMenuItem,
    quit_application,
    send_notification,
)


# ---------------------------------------------------------------------------
# StatusMenuItem
# ---------------------------------------------------------------------------


class TestStatusMenuItem:
    def test_create_with_title(self):
        item = StatusMenuItem("Hello")
        assert item.title == "Hello"

    def test_title_setter(self):
        item = StatusMenuItem("Old")
        item.title = "New"
        assert item.title == "New"

    def test_state_default(self):
        item = StatusMenuItem("Item")
        assert item.state == 0

    def test_state_setter(self):
        item = StatusMenuItem("Item")
        item.state = 1
        assert item.state == 1

    def test_callback_set(self):
        cb = MagicMock()
        item = StatusMenuItem("Click", callback=cb)
        assert item._menuitem.action() == "menuItemClicked:"

    def test_callback_clear(self):
        item = StatusMenuItem("Click", callback=lambda _: None)
        item.set_callback(None)
        assert item._menuitem.action() is None

    def test_custom_attributes(self):
        """StatusMenuItem allows arbitrary Python attributes."""
        item = StatusMenuItem("Item")
        item._enhance_mode = "translate"
        item._preset_id = "whisper-large"
        assert item._enhance_mode == "translate"
        assert item._preset_id == "whisper-large"

    def test_add_submenu_items(self):
        parent = StatusMenuItem("Parent")
        child1 = StatusMenuItem("Child1")
        child2 = StatusMenuItem("Child2")
        parent.add(child1)
        parent.add(child2)
        assert list(parent.keys()) == ["Child1", "Child2"]
        assert parent["Child1"] is child1
        assert len(parent) == 2

    def test_add_separator(self):
        parent = StatusMenuItem("Parent")
        parent.add(StatusMenuItem("A"))
        parent.add(None)
        parent.add(StatusMenuItem("B"))
        assert len(parent) == 3

    def test_pop(self):
        parent = StatusMenuItem("Parent")
        child = StatusMenuItem("Child")
        parent.add(child)
        removed = parent.pop("Child")
        assert removed is child
        assert len(parent) == 0

    def test_pop_missing_raises(self):
        parent = StatusMenuItem("Parent")
        with pytest.raises(KeyError):
            parent.pop("Missing")

    def test_clear(self):
        parent = StatusMenuItem("Parent")
        parent.add(StatusMenuItem("A"))
        parent.add(StatusMenuItem("B"))
        parent.clear()
        assert len(parent) == 0

    def test_delitem(self):
        parent = StatusMenuItem("Parent")
        parent.add(StatusMenuItem("A"))
        del parent["A"]
        assert "A" not in parent

    def test_contains(self):
        parent = StatusMenuItem("Parent")
        parent.add(StatusMenuItem("X"))
        assert "X" in parent
        assert "Y" not in parent

    def test_insert_before(self):
        parent = StatusMenuItem("Parent")
        parent.add(StatusMenuItem("A"))
        parent.add(StatusMenuItem("C"))
        parent.insert_before("C", StatusMenuItem("B"))
        assert list(parent.keys()) == ["A", "B", "C"]

    def test_update_from_list(self):
        parent = StatusMenuItem("Root")
        items = [
            StatusMenuItem("Item1"),
            None,
            StatusMenuItem("Item2"),
        ]
        parent.update(items)
        assert "Item1" in parent
        assert "Item2" in parent
        assert len(parent) == 3  # Item1, separator, Item2

    def test_keys_del_rebuild_menu(self):
        """Simulate the hotkey menu rebuild pattern from app.py."""
        menu = StatusMenuItem("Hotkey")
        menu.add(StatusMenuItem("right_cmd"))
        menu.add(StatusMenuItem("fn"))
        # Clear using keys + del (like app.py line 619-620)
        for key in list(menu.keys()):
            del menu[key]
        assert len(menu) == 0

    def test_lazy_submenu_creation(self):
        item = StatusMenuItem("Item")
        assert item._menu is None
        item.add(StatusMenuItem("Child"))
        assert item._menu is not None

    def test_menu_check_pattern(self):
        """Test the pattern `if item._menu is not None` used in app.py."""
        item = StatusMenuItem("Item")
        assert item._menu is None
        item.add(StatusMenuItem("Child"))
        assert item._menu is not None
        item.clear()
        # After clear, _menu still exists but is empty
        assert item._menu is not None
        assert len(item) == 0


# ---------------------------------------------------------------------------
# StatusBarApp
# ---------------------------------------------------------------------------


class TestStatusBarApp:
    def test_init(self):
        app = StatusBarApp("TestApp", title="TA")
        assert app._name == "TestApp"
        assert app.title == "TA"

    def test_quit_button(self):
        app = StatusBarApp("TestApp")
        assert app.quit_button is not None
        assert app.quit_button.title == "Quit"

    def test_no_quit_button(self):
        app = StatusBarApp("TestApp", quit_button=None)
        assert app.quit_button is None

    def test_menu_setter(self):
        app = StatusBarApp("TestApp")
        item1 = StatusMenuItem("Item1")
        item2 = StatusMenuItem("Item2")
        app.menu = [item1, None, item2]
        assert "Item1" in app.menu
        assert "Item2" in app.menu

    def test_title_property(self):
        app = StatusBarApp("TestApp", title="V1")
        app.title = "V2"
        assert app.title == "V2"

    def test_icon_nsimage_property(self):
        app = StatusBarApp("TestApp")
        assert app._icon_nsimage is None
        mock_image = MagicMock()
        app._icon_nsimage = mock_image
        assert app._icon_nsimage is mock_image


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class TestResponse:
    def test_clicked_ok(self):
        r = Response(1, "hello")
        assert r.clicked == 1
        assert r.text == "hello"

    def test_clicked_cancel(self):
        r = Response(0, "")
        assert r.clicked == 0

    def test_repr(self):
        r = Response(1, "short")
        assert "clicked: 1" in repr(r)

    def test_repr_long_text(self):
        r = Response(1, "a" * 30)
        assert "..." in repr(r)


# ---------------------------------------------------------------------------
# send_notification
# ---------------------------------------------------------------------------


class TestSendNotification:
    @patch("voicetext.statusbar.NSUserNotificationCenter")
    @patch("voicetext.statusbar.NSUserNotification")
    def test_send_notification(self, mock_notif_cls, mock_center_cls):
        mock_notif = MagicMock()
        mock_notif_cls.alloc.return_value.init.return_value = mock_notif
        mock_center = MagicMock()
        mock_center_cls.defaultUserNotificationCenter.return_value = mock_center

        send_notification("Title", "Sub", "Msg")

        mock_notif.setTitle_.assert_called_once_with("Title")
        mock_notif.setSubtitle_.assert_called_once_with("Sub")
        mock_notif.setInformativeText_.assert_called_once_with("Msg")
        mock_center.deliverNotification_.assert_called_once_with(mock_notif)

    def test_send_notification_graceful_failure(self):
        """Should not raise even when notification center is unavailable."""
        with patch(
            "voicetext.statusbar.NSUserNotification",
            side_effect=Exception("unavailable"),
        ):
            send_notification("T", "S", "M")  # should not raise


# ---------------------------------------------------------------------------
# quit_application
# ---------------------------------------------------------------------------


class TestQuitApplication:
    @patch("voicetext.statusbar.NSApplication")
    def test_quit(self, mock_nsapp_cls):
        mock_nsapp = MagicMock()
        mock_nsapp_cls.sharedApplication.return_value = mock_nsapp
        quit_application()
        mock_nsapp.terminate_.assert_called_once()
