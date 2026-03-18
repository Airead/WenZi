"""Quick edit panel — floating editor for clipboard/snippet content.

Provides a topmost, always-visible editor panel.  The user can edit
the content, switch to other apps to copy/paste, and then press
⌥Enter (or click Copy) to save the result to the system clipboard.

Keyboard:
  - ⌥Enter: Save to clipboard and close
  - Esc: Cancel and close
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-loaded close delegate
# ---------------------------------------------------------------------------

_CloseDelegate = None


def _get_close_delegate_class():
    global _CloseDelegate
    if _CloseDelegate is not None:
        return _CloseDelegate

    from Foundation import NSObject

    class QuickEditCloseDelegate(NSObject):
        """Close delegate — treats window close button as cancel."""

        _panel_ref = None

        def windowWillClose_(self, notification):
            if self._panel_ref is not None:
                self._panel_ref.close()

    _CloseDelegate = QuickEditCloseDelegate
    return _CloseDelegate


# ---------------------------------------------------------------------------
# Module-level reference to keep the active panel alive
# ---------------------------------------------------------------------------

_active_panel: Optional["QuickEditPanel"] = None


def open_quick_edit(content: str, *, reveal_path: Optional[str] = None) -> None:
    """Open the quick-edit panel on the main thread.

    Safe to call from any thread — dispatches to the main run loop.

    Args:
        content: Text to pre-fill in the editor.
        reveal_path: Optional file path (e.g. snippet config) — when set,
            a "Copy Path" button appears on the left side of the button bar.
    """
    from PyObjCTools import AppHelper

    def _show():
        global _active_panel
        if _active_panel is not None:
            _active_panel.close()
        _active_panel = QuickEditPanel()
        _active_panel.show(content, reveal_path=reveal_path)

    AppHelper.callAfter(_show)


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

_BUTTON_WIDTH = 100
_BUTTON_HEIGHT = 32
_GAP = 8


class QuickEditPanel:
    """Floating, always-on-top editor panel for quick text editing."""

    _PANEL_WIDTH = 480
    _PANEL_HEIGHT = 320
    _PADDING = 12

    def __init__(self) -> None:
        self._panel = None
        self._text_view = None
        self._delegate = None
        self._event_monitor = None
        self._reveal_path: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(
        self,
        content: str,
        *,
        reveal_path: Optional[str] = None,
    ) -> None:
        """Show the editor panel with *content* pre-filled."""
        self._reveal_path = reveal_path
        self._build_panel(content)

        from AppKit import NSApp

        NSApp.setActivationPolicy_(0)  # Regular (foreground)
        self._panel.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

        self._install_event_monitor()

    def close(self) -> None:
        """Close the editor panel and restore accessory mode."""
        global _active_panel
        self._remove_event_monitor()

        if self._panel is not None:
            self._delegate = None
            self._panel.orderOut_(None)
            self._panel = None

        self._text_view = None

        from AppKit import NSApp

        NSApp.setActivationPolicy_(1)  # Accessory

        if _active_panel is self:
            _active_panel = None

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _save_to_clipboard(self) -> None:
        """Copy the editor content to the clipboard and close."""
        if self._text_view is None:
            return

        text = str(self._text_view.string())
        if not text:
            self.close()
            return

        from wenzi.scripting.sources import copy_to_clipboard

        copy_to_clipboard(text)
        self.close()

        from PyObjCTools import AppHelper

        def _hud():
            from wenzi.ui.hud import show_hud

            preview = text.replace("\n", " ").strip()
            if len(preview) > 40:
                preview = preview[:37] + "..."
            show_hud(f"Copied\n{preview}")

        AppHelper.callAfter(_hud)

    def _copy_reveal_path(self) -> None:
        """Copy the snippet file path to the clipboard."""
        if not self._reveal_path:
            return

        import os

        from wenzi.scripting.sources import copy_to_clipboard

        path = self._reveal_path
        copy_to_clipboard(path)
        self.close()

        from PyObjCTools import AppHelper

        def _hud():
            from wenzi.ui.hud import show_hud

            display = path.replace(os.path.expanduser("~"), "~")
            show_hud(f"Path Copied\n{display}")

        AppHelper.callAfter(_hud)

    # -- ObjC button targets --

    def copyClicked_(self, sender):
        self._save_to_clipboard()

    def cancelClicked_(self, sender):
        self.close()

    def copyPathClicked_(self, sender):
        self._copy_reveal_path()

    # ------------------------------------------------------------------
    # Keyboard handling
    # ------------------------------------------------------------------

    def _handle_key_event(self, event):
        """Handle ⌥Enter (save) — other keys pass through."""
        try:
            if self._panel is None or not self._panel.isKeyWindow():
                return event

            from AppKit import (
                NSAlternateKeyMask,
                NSCommandKeyMask,
                NSDeviceIndependentModifierFlagsMask,
            )

            flags = (
                event.modifierFlags() & NSDeviceIndependentModifierFlagsMask
            )
            chars = event.charactersIgnoringModifiers()
            if not chars:
                return event

            char = chars[0] if isinstance(chars, str) else str(chars)

            # ⌥Enter → save to clipboard
            if char == "\r" and (flags & NSAlternateKeyMask):
                self._save_to_clipboard()
                return None  # consume

            # ⌘Enter → copy path (only when reveal_path is set)
            if (
                char == "\r"
                and (flags & NSCommandKeyMask)
                and self._reveal_path
            ):
                self._copy_reveal_path()
                return None  # consume

        except Exception:
            logger.debug(
                "Exception in quick edit key handler", exc_info=True,
            )

        return event

    def _install_event_monitor(self) -> None:
        """Install a local event monitor for ⌥Enter."""
        self._remove_event_monitor()
        from AppKit import NSEvent, NSKeyDownMask

        self._event_monitor = (
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                NSKeyDownMask, self._handle_key_event,
            )
        )

    def _remove_event_monitor(self) -> None:
        """Remove the local event monitor."""
        if self._event_monitor is not None:
            from AppKit import NSEvent

            NSEvent.removeMonitor_(self._event_monitor)
            self._event_monitor = None

    # ------------------------------------------------------------------
    # Panel construction
    # ------------------------------------------------------------------

    def _build_panel(self, content: str) -> None:
        """Build the NSPanel and subviews."""
        from AppKit import (
            NSBackingStoreBuffered,
            NSButton,
            NSClosableWindowMask,
            NSColor,
            NSFont,
            NSMiniaturizableWindowMask,
            NSPanel,
            NSResizableWindowMask,
            NSScreen,
            NSScrollView,
            NSStatusWindowLevel,
            NSTextView,
            NSTitledWindowMask,
        )
        from Foundation import NSMakeRect, NSMakeSize

        P = self._PADDING
        style = (
            NSTitledWindowMask
            | NSClosableWindowMask
            | NSResizableWindowMask
            | NSMiniaturizableWindowMask
        )

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
            style,
            NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_("Quick Edit")
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setMinSize_(NSMakeSize(300, 200))

        # Center on screen
        screen = NSScreen.mainScreen()
        if screen:
            sf = screen.visibleFrame()
            pf = panel.frame()
            x = sf.origin.x + (sf.size.width - pf.size.width) / 2
            y = sf.origin.y + (sf.size.height - pf.size.height) / 2
            panel.setFrameOrigin_((x, y))
        else:
            panel.center()

        # Close delegate
        delegate_cls = _get_close_delegate_class()
        delegate = delegate_cls.alloc().init()
        delegate._panel_ref = self
        panel.setDelegate_(delegate)
        self._delegate = delegate

        cv = panel.contentView()

        # Ensure Edit menu for Cmd+A/C/V/X
        from wenzi.ui.result_window_web import _ensure_edit_menu

        _ensure_edit_menu()

        # -- Button bar (bottom) --
        y = P

        # Right side: Cancel, then Copy
        copy_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                self._PANEL_WIDTH - P - _BUTTON_WIDTH,
                y,
                _BUTTON_WIDTH,
                _BUTTON_HEIGHT,
            )
        )
        copy_btn.setTitle_("Copy [\u2325\u21b5]")
        copy_btn.setBezelStyle_(1)
        copy_btn.setTarget_(self)
        copy_btn.setAction_(b"copyClicked:")
        # Pin right: flexible left margin (1)
        copy_btn.setAutoresizingMask_(1)
        cv.addSubview_(copy_btn)

        cancel_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                self._PANEL_WIDTH - P - 2 * _BUTTON_WIDTH - _GAP,
                y,
                _BUTTON_WIDTH,
                _BUTTON_HEIGHT,
            )
        )
        cancel_btn.setTitle_("Cancel")
        cancel_btn.setBezelStyle_(1)
        cancel_btn.setKeyEquivalent_("\x1b")  # Esc
        cancel_btn.setTarget_(self)
        cancel_btn.setAction_(b"cancelClicked:")
        # Pin right: flexible left margin (1)
        cancel_btn.setAutoresizingMask_(1)
        cv.addSubview_(cancel_btn)

        # Left side: Copy Path (only for snippets)
        if self._reveal_path:
            path_btn_w = _BUTTON_WIDTH + 40
            path_btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(P, y, path_btn_w, _BUTTON_HEIGHT)
            )
            path_btn.setTitle_("Copy Path [\u2318\u21b5]")
            path_btn.setBezelStyle_(1)
            path_btn.setKeyEquivalent_("\r")
            from AppKit import NSCommandKeyMask

            path_btn.setKeyEquivalentModifierMask_(NSCommandKeyMask)
            path_btn.setTarget_(self)
            path_btn.setAction_(b"copyPathClicked:")
            # Pin left: flexible right margin (4)
            path_btn.setAutoresizingMask_(4)
            cv.addSubview_(path_btn)

        y += _BUTTON_HEIGHT + _GAP

        # -- Text view (fills remaining space) --
        text_h = self._PANEL_HEIGHT - y - P
        inner_w = self._PANEL_WIDTH - 2 * P

        scroll_view = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(P, y, inner_w, text_h)
        )
        scroll_view.setHasVerticalScroller_(True)
        scroll_view.setBorderType_(3)  # NSBezelBorder
        # Flexible width (2) + flexible height (16)
        scroll_view.setAutoresizingMask_(2 | 16)

        text_view = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, inner_w - 2, text_h - 2)
        )
        text_view.setFont_(NSFont.userFixedPitchFontOfSize_(13.0))
        text_view.setTextColor_(NSColor.labelColor())
        text_view.setBackgroundColor_(NSColor.textBackgroundColor())
        text_view.setRichText_(False)
        text_view.setAutoresizingMask_(2)  # width sizable
        text_view.textContainer().setWidthTracksTextView_(True)
        text_view.setAllowsUndo_(True)

        text_view.setString_(content)

        scroll_view.setDocumentView_(text_view)
        cv.addSubview_(scroll_view)
        self._text_view = text_view

        self._panel = panel
