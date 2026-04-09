"""Pure PyObjC statusbar application framework replacing rumps."""

from __future__ import annotations

import collections
import logging
from collections.abc import Callable
from typing import Any

import AppKit
from AppKit import (
    NSAlert,
    NSApplication,
    NSImage,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSSecureTextField,
    NSStatusBar,
    NSTextField,
    NSUserNotification,
    NSUserNotificationCenter,
)
from Foundation import NSObject
from PyObjCTools import AppHelper

from wenzi.i18n import t

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Callback routing
# ---------------------------------------------------------------------------


class _MenuCallbackTarget(NSObject):
    """Singleton ObjC target that receives all menu item actions."""

    def menuItemClicked_(self, nsmenuitem) -> None:
        entry = _ns_to_callback.get(id(nsmenuitem))
        if entry is not None:
            smitem, callback = entry
            try:
                callback(smitem)
            except Exception:
                logger.exception("Menu callback error")


# Singleton handler instance, created lazily
_callback_handler: _MenuCallbackTarget | None = None
# Map id(NSMenuItem) -> (StatusMenuItem, callable)
_ns_to_callback: dict[int, tuple[StatusMenuItem, Callable]] = {}


def _get_callback_handler() -> _MenuCallbackTarget:
    global _callback_handler
    if _callback_handler is None:
        _callback_handler = _MenuCallbackTarget.alloc().init()
    return _callback_handler


def cleanup_callbacks() -> None:
    """Clear the global callback mapping.

    Should be called on application quit to release references to
    ``StatusMenuItem`` / callable pairs held for menu item routing.
    """
    _ns_to_callback.clear()


# ---------------------------------------------------------------------------
# SeparatorMenuItem
# ---------------------------------------------------------------------------

class SeparatorMenuItem:
    """A visual separator in the menu."""

    def __init__(self) -> None:
        self._menuitem = NSMenuItem.separatorItem()


# Module-level sentinel (mirrors rumps.separator)
separator = object()


# ---------------------------------------------------------------------------
# StatusMenuItem
# ---------------------------------------------------------------------------

class StatusMenuItem:
    """Python-friendly wrapper around NSMenuItem with dict-like submenu management.

    Supports arbitrary Python attributes (e.g. item._enhance_mode = "translate")
    since this is a pure Python class, unlike real AppKit objects.
    """

    def __init__(
        self,
        title: str = "",
        callback: Callable | None = None,
        key: str = "",
    ) -> None:
        if isinstance(title, StatusMenuItem):
            return
        self._menuitem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            str(title), None, key or ""
        )
        self._menu: NSMenu | None = None  # lazy submenu
        self._items: collections.OrderedDict[str, Any] = collections.OrderedDict()
        self._sep_count = 0
        if callback is not None:
            self.set_callback(callback)

    # -- Properties ---------------------------------------------------------

    @property
    def title(self) -> str:
        return str(self._menuitem.title())

    @title.setter
    def title(self, value: str) -> None:
        self._menuitem.setTitle_(str(value))

    @property
    def state(self) -> int:
        return int(self._menuitem.state())

    @state.setter
    def state(self, value: int) -> None:
        self._menuitem.setState_(int(value))

    def set_callback(self, callback: Callable | None, key: str | None = None) -> None:
        """Set or clear the click callback."""
        if key is not None:
            self._menuitem.setKeyEquivalent_(key)
        handler = _get_callback_handler()
        self._menuitem.setTarget_(handler)
        if callback is not None:
            _ns_to_callback[id(self._menuitem)] = (self, callback)
            self._menuitem.setAction_("menuItemClicked:")
        else:
            _ns_to_callback.pop(id(self._menuitem), None)
            self._menuitem.setAction_(None)

    # -- Submenu management -------------------------------------------------

    def _ensure_submenu(self) -> NSMenu:
        if self._menu is None:
            self._menu = NSMenu.alloc().init()
            self._menu.setAutoenablesItems_(False)
            self._menuitem.setSubmenu_(self._menu)
        return self._menu

    def _process_value(self, value: Any) -> tuple[str, Any]:
        """Convert raw value to (key, item) pair."""
        if value is None or value is separator:
            item = SeparatorMenuItem()
            self._sep_count += 1
            return (f"_sep_{self._sep_count}", item)
        if isinstance(value, str):
            value = StatusMenuItem(value)
        if isinstance(value, (StatusMenuItem, SeparatorMenuItem)):
            key = value.title if hasattr(value, "title") else f"_sep_{self._sep_count}"
            return (key, value)
        raise TypeError(f"Cannot add {type(value)} to menu")

    def add(self, item: Any) -> None:
        """Add item to submenu. None creates a separator."""
        key, value = self._process_value(item)
        menu = self._ensure_submenu()
        menu.addItem_(value._menuitem)
        self._items[key] = value

    def pop(self, title: str) -> Any:
        """Remove and return item by title."""
        value = self._items.pop(title)
        if isinstance(value, StatusMenuItem):
            value.set_callback(None)
        if self._menu is not None:
            self._menu.removeItem_(value._menuitem)
        return value

    def insert_before(self, existing_title: str, item: Any) -> None:
        """Insert item before the item with existing_title."""
        key, value = self._process_value(item)
        existing = self._items[existing_title]
        menu = self._ensure_submenu()
        index = menu.indexOfItem_(existing._menuitem)
        menu.insertItem_atIndex_(value._menuitem, index)
        # Rebuild OrderedDict to maintain insertion order
        new_items: collections.OrderedDict = collections.OrderedDict()
        for k, v in self._items.items():
            if k == existing_title:
                new_items[key] = value
            new_items[k] = v
        self._items = new_items

    def clear(self) -> None:
        """Remove all items from submenu."""
        for value in self._items.values():
            if isinstance(value, StatusMenuItem):
                value.set_callback(None)
        if self._menu is not None:
            self._menu.removeAllItems()
        self._items.clear()
        self._sep_count = 0

    def keys(self):
        return self._items.keys()

    def __getitem__(self, key: str) -> Any:
        return self._items[key]

    def __delitem__(self, key: str) -> None:
        value = self._items.pop(key)
        if isinstance(value, StatusMenuItem):
            value.set_callback(None)
        if self._menu is not None:
            self._menu.removeItem_(value._menuitem)

    def __contains__(self, key: str) -> bool:
        return key in self._items

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def update(self, iterable) -> None:
        """Parse iterable of items (like rumps menu setter)."""
        self._parse_menu(iterable)

    def _parse_menu(self, iterable) -> None:
        if isinstance(iterable, StatusMenuItem):
            self.add(iterable)
            return
        for ele in iterable:
            if isinstance(ele, StatusMenuItem):
                self.add(ele)
            elif ele is None or ele is separator:
                self.add(None)
            elif isinstance(ele, (list, tuple)) and len(ele) == 2:
                menuitem, submenu = ele
                if isinstance(menuitem, str):
                    menuitem = StatusMenuItem(menuitem)
                self.add(menuitem)
                menuitem._parse_menu(submenu)
            else:
                self.add(ele)


# ---------------------------------------------------------------------------
# StatusBarApp
# ---------------------------------------------------------------------------

class StatusBarApp:
    """Pure PyObjC statusbar application base class."""

    _QUIT_DEFAULT = object()

    def __init__(
        self,
        name: str,
        icon: str | None = None,
        title: str | None = None,
        quit_button: str | None = _QUIT_DEFAULT,
    ) -> None:
        self._name = name
        self._title = title
        self._icon = icon
        self._icon_nsimage: NSImage | None = None
        self._menu = StatusMenuItem(name)
        if quit_button is self._QUIT_DEFAULT:
            quit_button = t("menu.quit")
        self._quit_button = StatusMenuItem(quit_button) if quit_button else None
        self._nsstatusitem = None
        self._status_item_hidden = False

    # -- Properties ---------------------------------------------------------

    @property
    def title(self) -> str | None:
        return self._title

    @title.setter
    def title(self, value: str | None) -> None:
        self._title = value
        if self._nsstatusitem is not None:
            self._nsstatusitem.setTitle_(value or "")

    @property
    def menu(self) -> StatusMenuItem:
        return self._menu

    @menu.setter
    def menu(self, iterable) -> None:
        self._menu.update(iterable)

    @property
    def quit_button(self) -> StatusMenuItem | None:
        return self._quit_button

    # -- Status bar ---------------------------------------------------------

    def _setup_status_bar(self) -> None:
        # Append quit button (once) regardless of visibility
        if self._quit_button is not None:
            if self._quit_button._menuitem.action() is None:
                self._quit_button.set_callback(lambda _: quit_application())
            if self._quit_button.title not in self._menu:
                self._menu.add(self._quit_button)

        if self._status_item_hidden:
            return

        self._nsstatusitem = NSStatusBar.systemStatusBar().statusItemWithLength_(-1)
        self._nsstatusitem.setHighlightMode_(True)
        self._update_status_bar_icon()
        self._update_status_bar_title()
        self._nsstatusitem.setMenu_(self._menu._ensure_submenu())

    def _update_status_bar_icon(self) -> None:
        if self._nsstatusitem is not None:
            self._nsstatusitem.setImage_(self._icon_nsimage)
            self._fallback_on_name()

    def _update_status_bar_title(self) -> None:
        if self._nsstatusitem is not None:
            self._nsstatusitem.setTitle_(self._title or "")
            self._fallback_on_name()

    def _fallback_on_name(self) -> None:
        si = self._nsstatusitem
        if si is not None and not si.title() and not si.image():
            si.setTitle_(self._name)

    # -- Status item visibility ---------------------------------------------

    def set_status_item_visible(self, visible: bool) -> None:
        """Show or hide the status bar item (menu bar icon).

        NSStatusItem has no hide/show API — we must remove the item entirely
        and re-create it to toggle visibility.
        """
        self._status_item_hidden = not visible
        if visible:
            if self._nsstatusitem is None:
                self._nsstatusitem = NSStatusBar.systemStatusBar().statusItemWithLength_(-1)
                self._nsstatusitem.setHighlightMode_(True)
                self._update_status_bar_icon()
                self._update_status_bar_title()
                self._nsstatusitem.setMenu_(self._menu._ensure_submenu())
        else:
            if self._nsstatusitem is not None:
                NSStatusBar.systemStatusBar().removeStatusItem_(
                    self._nsstatusitem
                )
                self._nsstatusitem = None

    @property
    def status_item_visible(self) -> bool:
        return self._nsstatusitem is not None

    # -- Run loop -----------------------------------------------------------

    def run(self, **kwargs) -> None:
        """Start the application run loop."""
        nsapp = NSApplication.sharedApplication()
        nsapp.activateIgnoringOtherApps_(True)

        nsapp.setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyAccessory
        )

        self._setup_status_bar()

        AppHelper.installMachInterrupt()
        AppHelper.runEventLoop()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def send_notification(
    title: str,
    subtitle: str,
    message: str,
    sound: str | None = "default",
) -> None:
    """Send a macOS notification. Gracefully fails in unbundled mode.

    Args:
        sound: Notification sound name.  ``"default"`` plays the system
            default sound, ``None`` is silent, or pass a macOS sound
            name (e.g. ``"Glass"``, ``"Ping"``, ``"Hero"``).
            Available sounds are in ``/System/Library/Sounds/``.
    """
    try:
        notification = NSUserNotification.alloc().init()
        notification.setTitle_(title)
        notification.setSubtitle_(subtitle)
        notification.setInformativeText_(message)
        if sound is not None:
            sound_name = (
                "NSUserNotificationDefaultSoundName"
                if sound == "default"
                else sound
            )
            notification.setSoundName_(sound_name)
        center = NSUserNotificationCenter.defaultUserNotificationCenter()
        center.deliverNotification_(notification)
    except Exception:
        logger.debug("Notification center unavailable", exc_info=True)


# ---------------------------------------------------------------------------
# Quit / Restart
# ---------------------------------------------------------------------------

def quit_application(sender=None) -> None:
    """Quit the application."""
    NSApplication.sharedApplication().terminate_(sender)


def restart_application() -> None:
    """Spawn a shell watcher that waits for this process to exit, then relaunches."""
    import os
    import shlex
    import subprocess
    import sys

    pid = os.getpid()

    if getattr(sys, "frozen", False):
        # PyInstaller bundle: exec the binary directly, bypassing Launch Services.
        cmd = shlex.quote(sys.executable)
    else:
        cmd = shlex.join([sys.executable] + sys.argv)

    # Use /bin/sh so the watcher is fully independent of the Python runtime.
    # `kill -0` checks if the process is still alive; once it's gone, relaunch.
    script = f"while kill -0 {pid} 2>/dev/null; do sleep 0.2; done; exec {cmd}"
    subprocess.Popen(
        ["/bin/sh", "-c", script],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logging.getLogger(__name__).info(
        "Restart watcher spawned (pid=%d), quitting...", pid
    )
    quit_application()


# ---------------------------------------------------------------------------
# InputWindow
# ---------------------------------------------------------------------------

class Response:
    """Result from an InputWindow interaction."""

    def __init__(self, clicked: int, text: str) -> None:
        self._clicked = clicked
        self._text = text

    @property
    def clicked(self) -> int:
        """1 for OK, 0 for Cancel."""
        return self._clicked

    @property
    def text(self) -> str:
        return self._text

    def __repr__(self) -> str:
        short = self._text if len(self._text) < 21 else self._text[:17] + "..."
        return f"<Response: [clicked: {self._clicked}, text: {short!r}]>"


class InputWindow:
    """Modal input dialog using NSAlert + text field."""

    def __init__(
        self,
        message: str = "",
        title: str = "",
        default_text: str = "",
        ok: str | None = None,
        cancel: str | None = None,
        dimensions: tuple[int, int] = (320, 160),
        secure: bool = False,
    ) -> None:
        self._alert = NSAlert.alloc().init()
        self._alert.setMessageText_(title)
        self._alert.setInformativeText_(message)
        self._alert.addButtonWithTitle_(ok or "OK")
        self._has_cancel = bool(cancel)
        if cancel:
            cancel_title = cancel if isinstance(cancel, str) else "Cancel"
            self._alert.addButtonWithTitle_(cancel_title)
        self._alert.setAlertStyle_(0)  # NSInformationalAlertStyle

        if secure:
            self._textfield = NSSecureTextField.alloc().initWithFrame_(
                NSMakeRect(0, 0, *dimensions)
            )
        else:
            self._textfield = NSTextField.alloc().initWithFrame_(
                NSMakeRect(0, 0, *dimensions)
            )
        self._textfield.setSelectable_(True)
        self._textfield.setStringValue_(default_text or "")
        self._alert.setAccessoryView_(self._textfield)
        self._default_text = default_text

    @property
    def alert(self) -> NSAlert:
        """Access the underlying NSAlert for customization."""
        return self._alert

    # Keep _alert accessible for backward compat with rumps.Window hack pattern
    # (code does w._alert.window().setLevel_(...))

    def run(self) -> Response:
        """Show the dialog and return user input."""
        # Apply dark mode appearance if needed
        try:
            from Foundation import NSUserDefaults
            if NSUserDefaults.standardUserDefaults().stringForKey_("AppleInterfaceStyle") == "Dark":
                self._alert.window().setAppearance_(
                    AppKit.NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark")
                )
        except Exception:
            pass

        result = self._alert.runModal()
        # NSAlertFirstButtonReturn = 1000, NSAlertSecondButtonReturn = 1001
        clicked = 1 if result == 1000 else 0
        self._textfield.validateEditing()
        text = str(self._textfield.stringValue())
        # Reset default text for reuse
        self._textfield.setStringValue_(self._default_text or "")
        return Response(clicked, text)
