"""Snippet editor panel — lightweight NSPanel for creating new snippets.

Provides a floating, always-on-top panel with Name, Keyword, and Content
fields.  The Name field supports ``category/name`` format to place snippets
in subdirectories.

Keyboard:
  - Tab / Shift+Tab: cycle between Name → Keyword → Content
  - Enter: save (Shift+Enter for newline in Content)
  - Esc: cancel
"""

from __future__ import annotations

import datetime
import logging
import os
from collections.abc import Callable

from wenzi.i18n import t

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-loaded PyObjC helpers (avoid import at module level)
# ---------------------------------------------------------------------------

_CloseDelegate = None


def _get_close_delegate_class():
    global _CloseDelegate
    if _CloseDelegate is not None:
        return _CloseDelegate

    from Foundation import NSObject

    class SnippetEditorCloseDelegate(NSObject):
        """Close delegate — treats window close button as cancel."""

        _panel_ref = None

        def windowWillClose_(self, notification):
            if self._panel_ref is not None:
                self._panel_ref.close()

    _CloseDelegate = SnippetEditorCloseDelegate
    return _CloseDelegate


class SnippetEditorPanel:
    """Floating panel for creating a new snippet."""

    _PANEL_WIDTH = 400
    _PADDING = 16
    _LABEL_HEIGHT = 18
    _FIELD_HEIGHT = 24
    _CONTENT_HEIGHT = 120
    _BUTTON_HEIGHT = 32
    _BUTTON_WIDTH = 120
    _GAP = 8

    def __init__(self, snippet_store) -> None:
        self._store = snippet_store
        self._panel = None
        self._name_field = None
        self._keyword_field = None
        self._content_view = None  # NSTextView (multi-line)
        self._content_scroll = None
        self._error_label = None
        self._delegate = None
        self._event_monitor = None
        self._on_saved: Callable | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(
        self,
        initial_query: str = "",
        on_saved: Callable | None = None,
    ) -> None:
        """Show the editor panel.

        Args:
            initial_query: Pre-fill the keyword field with the launcher query.
            on_saved: Callback after a snippet is successfully saved.
        """
        self._on_saved = on_saved
        last_cat = self._store.last_category
        self._build_panel(initial_query, last_cat)

        from AppKit import NSApp

        NSApp.setActivationPolicy_(0)  # Regular (foreground)
        self._panel.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

        # Select only the "untitled" part so user can type to replace
        self._panel.makeFirstResponder_(self._name_field)
        editor = self._panel.fieldEditor_forObject_(True, self._name_field)
        if editor:
            prefix_len = len(last_cat) + 1 if last_cat else 0
            untitled_len = len("untitled")
            editor.setSelectedRange_((prefix_len, untitled_len))

        self._install_event_monitor()
        self._check_duplicates()

    def close(self) -> None:
        """Close the editor panel and restore accessory mode."""
        self._remove_event_monitor()

        if self._panel is not None:
            self._delegate = None
            self._panel.orderOut_(None)
            self._panel = None

        self._name_field = None
        self._keyword_field = None
        self._content_view = None
        self._content_scroll = None
        self._error_label = None

        from AppKit import NSApp

        NSApp.setActivationPolicy_(1)  # Accessory

    # ------------------------------------------------------------------
    # Save logic
    # ------------------------------------------------------------------

    def _do_save(self) -> None:
        """Validate and save the snippet."""
        name_raw = str(self._name_field.stringValue()).strip()
        keyword = str(self._keyword_field.stringValue()).strip()
        content = str(self._content_view.string())

        # Validate name
        if not name_raw:
            self._show_error(t("snippet_editor.error.name_empty"))
            self._panel.makeFirstResponder_(self._name_field)
            return

        # Parse category/name from name_raw
        category = ""
        name = name_raw
        if "/" in name_raw:
            parts = name_raw.rsplit("/", 1)
            category = parts[0].strip()
            name = parts[1].strip()
            if not name:
                self._show_error(t("snippet_editor.error.name_empty_after_slash"))
                self._focus_name_end()
                return

        # Check if file already exists
        if self._store.file_exists(name, category):
            self._show_error(t("snippet_editor.error.file_exists", name=name))
            self._focus_name_end()
            return

        # Check keyword uniqueness
        if keyword and self._store.find_by_keyword(keyword) is not None:
            self._show_error(t("snippet_editor.error.keyword_exists", keyword=keyword))
            self._panel.makeFirstResponder_(self._keyword_field)
            return

        # Check content uniqueness
        if content:
            dup = self._store.find_by_content(content)
            if dup is not None:
                self._show_error(
                    t("snippet_editor.error.content_duplicates", label=self._dup_label(dup)),
                )
                return

        # Save
        ok = self._store.add(
            name=name, keyword=keyword, content=content, category=category,
        )
        if not ok:
            self._show_error(t("snippet_editor.error.save_failed"))
            return

        # Remember category for next time
        self._store.last_category = category

        callback = self._on_saved
        saved_path = self._store.snippet_path(name, category)
        self.close()

        # Show HUD with saved path
        from PyObjCTools import AppHelper

        def _show_hud():
            from wenzi.ui.hud import show_hud

            # Use path relative to home for brevity
            display_path = saved_path.replace(os.path.expanduser("~"), "~")
            show_hud(t("snippet_editor.hud.saved", path=display_path))

        AppHelper.callAfter(_show_hud)

        if callback is not None:
            callback()

    def _focus_name_end(self) -> None:
        """Focus the name field with cursor at end (not selecting all)."""
        self._panel.makeFirstResponder_(self._name_field)
        editor = self._panel.fieldEditor_forObject_(True, self._name_field)
        if editor:
            length = editor.string().length() if editor.string() else 0
            editor.setSelectedRange_((length, 0))

    def _check_duplicates(self) -> None:
        """Check pre-filled keyword and content for duplicates on show."""
        keyword = str(self._keyword_field.stringValue()).strip()
        if keyword:
            existing = self._store.find_by_keyword(keyword)
            if existing is not None:
                self._show_error(t("snippet_editor.error.keyword_exists", keyword=keyword))
                return

        content = str(self._content_view.string()) if self._content_view else ""
        if content:
            dup = self._store.find_by_content(content)
            if dup is not None:
                self._show_error(
                    t("snippet_editor.error.content_duplicates", label=self._dup_label(dup)),
                )

    @staticmethod
    def _dup_label(dup: dict) -> str:
        """Format a snippet dict as 'category/name' for error messages."""
        cat = dup.get("category", "")
        name = dup.get("name", "")
        return f"{cat}/{name}" if cat else name

    def _show_error(self, message: str) -> None:
        """Display an error message in the error label."""
        if self._error_label is not None:
            self._error_label.setStringValue_(message)
            self._error_label.setHidden_(False)

    def _clear_error(self) -> None:
        """Hide the error label."""
        if self._error_label is not None:
            self._error_label.setStringValue_("")
            self._error_label.setHidden_(True)

    # ------------------------------------------------------------------
    # Keyboard handling
    # ------------------------------------------------------------------

    def _handle_key_event(self, event):
        """Handle Cmd+Enter (save) and Esc (close)."""
        try:
            if self._panel is None or not self._panel.isKeyWindow():
                return event

            from AppKit import NSDeviceIndependentModifierFlagsMask

            modifier_flags = (
                event.modifierFlags() & NSDeviceIndependentModifierFlagsMask
            )
            chars = event.charactersIgnoringModifiers()
            if not chars:
                return event

            char = chars[0] if isinstance(chars, str) else str(chars)

            # Tab in Content → move focus to Name
            if char == "\t" and self._content_view is not None:
                responder = self._panel.firstResponder()
                # NSTextView's first responder is its field editor (itself)
                if responder is self._content_view or (
                    hasattr(responder, "delegate")
                    and responder.delegate() is self._content_view
                ):
                    self._panel.makeFirstResponder_(self._name_field)
                    return None  # consume

            # Enter (without Shift) → save; Shift+Enter in Content → newline
            if char == "\r":
                from AppKit import NSShiftKeyMask

                if not (modifier_flags & NSShiftKeyMask):
                    self._clear_error()
                    self._do_save()
                    return None  # consume

        except Exception:
            logger.debug(
                "Exception in snippet editor key handler", exc_info=True,
            )

        return event

    def _install_event_monitor(self) -> None:
        """Install a local event monitor for Cmd+Enter."""
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

    def _build_panel(self, initial_query: str, last_category: str = "") -> None:
        """Build the NSPanel and all subviews."""
        from AppKit import (
            NSBackingStoreBuffered,
            NSButton,
            NSClosableWindowMask,
            NSColor,
            NSFont,
            NSPanel,
            NSScreen,
            NSScrollView,
            NSStatusWindowLevel,
            NSTextField,
            NSTextView,
            NSTitledWindowMask,
        )
        from Foundation import NSMakeRect

        P = self._PADDING
        inner_w = self._PANEL_WIDTH - 2 * P

        # Calculate total height (bottom to top)
        total_h = P  # bottom
        total_h += self._BUTTON_HEIGHT  # buttons
        total_h += self._GAP
        total_h += self._LABEL_HEIGHT  # error label
        total_h += self._GAP
        total_h += self._CONTENT_HEIGHT  # content
        total_h += self._LABEL_HEIGHT  # content label
        total_h += self._GAP
        total_h += self._FIELD_HEIGHT  # keyword
        total_h += self._LABEL_HEIGHT  # keyword label
        total_h += self._GAP
        total_h += self._FIELD_HEIGHT  # name
        total_h += self._LABEL_HEIGHT  # name label
        total_h += P  # top

        # Build panel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, total_h),
            NSTitledWindowMask | NSClosableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_(t("snippet_editor.title"))
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)

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

        content_view = panel.contentView()

        # Ensure Edit menu is available for Cmd+A/C/V/X
        from wenzi.ui.result_window_web import _ensure_edit_menu

        _ensure_edit_menu()

        # Layout bottom-to-top
        y = P

        # -- Buttons row --
        cancel_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                self._PANEL_WIDTH - P - 2 * self._BUTTON_WIDTH - self._GAP,
                y,
                self._BUTTON_WIDTH,
                self._BUTTON_HEIGHT,
            )
        )
        cancel_btn.setTitle_(t("snippet_editor.btn.cancel"))
        cancel_btn.setBezelStyle_(1)
        cancel_btn.setKeyEquivalent_("\x1b")  # Esc
        cancel_btn.setTarget_(self)
        cancel_btn.setAction_(b"cancelClicked:")
        content_view.addSubview_(cancel_btn)

        save_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                self._PANEL_WIDTH - P - self._BUTTON_WIDTH,
                y,
                self._BUTTON_WIDTH,
                self._BUTTON_HEIGHT,
            )
        )
        save_btn.setTitle_(t("snippet_editor.btn.save"))
        save_btn.setBezelStyle_(1)
        save_btn.setTarget_(self)
        save_btn.setAction_(b"saveClicked:")
        content_view.addSubview_(save_btn)

        y += self._BUTTON_HEIGHT + self._GAP

        # -- Error label --
        error_label = NSTextField.labelWithString_("")
        error_label.setFrame_(NSMakeRect(P, y, inner_w, self._LABEL_HEIGHT))
        error_label.setFont_(NSFont.systemFontOfSize_(11))
        error_label.setTextColor_(NSColor.systemRedColor())
        error_label.setHidden_(True)
        content_view.addSubview_(error_label)
        self._error_label = error_label

        y += self._LABEL_HEIGHT + self._GAP

        # -- Content (multi-line) --
        scroll_view = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(P, y, inner_w, self._CONTENT_HEIGHT)
        )
        scroll_view.setHasVerticalScroller_(True)
        scroll_view.setBorderType_(3)  # NSBezelBorder

        text_view = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, inner_w - 2, self._CONTENT_HEIGHT - 2)
        )
        text_view.setFont_(NSFont.userFixedPitchFontOfSize_(12.0))
        text_view.setTextColor_(NSColor.labelColor())
        text_view.setBackgroundColor_(NSColor.textBackgroundColor())
        text_view.setRichText_(False)
        text_view.setAutoresizingMask_(1)  # width sizable
        text_view.textContainer().setWidthTracksTextView_(True)

        # Pre-fill with clipboard content
        try:
            from wenzi.input import get_clipboard_text
            clipboard_text = get_clipboard_text()
        except Exception:
            clipboard_text = None
        if clipboard_text:
            text_view.setString_(clipboard_text)

        scroll_view.setDocumentView_(text_view)
        content_view.addSubview_(scroll_view)
        self._content_view = text_view
        self._content_scroll = scroll_view

        y += self._CONTENT_HEIGHT

        content_label = NSTextField.labelWithString_(t("snippet_editor.label.content"))
        content_label.setFrame_(NSMakeRect(P, y, inner_w, self._LABEL_HEIGHT))
        content_label.setFont_(NSFont.systemFontOfSize_(11))
        content_label.setTextColor_(NSColor.secondaryLabelColor())
        content_view.addSubview_(content_label)

        y += self._LABEL_HEIGHT + self._GAP

        # -- Keyword field --
        keyword_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(P, y, inner_w, self._FIELD_HEIGHT)
        )
        keyword_field.setFont_(NSFont.systemFontOfSize_(13))
        keyword_field.setPlaceholderString_(t("snippet_editor.placeholder.optional"))
        keyword_field.setStringValue_(initial_query)
        keyword_field.setTarget_(self)
        keyword_field.setAction_(b"fieldEnterPressed:")
        content_view.addSubview_(keyword_field)
        self._keyword_field = keyword_field

        y += self._FIELD_HEIGHT

        keyword_label = NSTextField.labelWithString_(t("snippet_editor.label.keyword"))
        keyword_label.setFrame_(NSMakeRect(P, y, inner_w, self._LABEL_HEIGHT))
        keyword_label.setFont_(NSFont.systemFontOfSize_(11))
        keyword_label.setTextColor_(NSColor.secondaryLabelColor())
        content_view.addSubview_(keyword_label)

        y += self._LABEL_HEIGHT + self._GAP

        # -- Name field --
        now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        _UNTITLED = "untitled"
        base_name = f"{_UNTITLED}__{now_str}"
        prefix = f"{last_category}/" if last_category else ""
        default_name = f"{prefix}{base_name}"

        name_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(P, y, inner_w, self._FIELD_HEIGHT)
        )
        name_field.setFont_(NSFont.systemFontOfSize_(13))
        name_field.setPlaceholderString_(t("snippet_editor.placeholder.category"))
        name_field.setStringValue_(default_name)
        name_field.setTarget_(self)
        name_field.setAction_(b"fieldEnterPressed:")
        content_view.addSubview_(name_field)
        self._name_field = name_field

        y += self._FIELD_HEIGHT

        name_label = NSTextField.labelWithString_(t("snippet_editor.label.name"))
        name_label.setFrame_(NSMakeRect(P, y, inner_w, self._LABEL_HEIGHT))
        name_label.setFont_(NSFont.systemFontOfSize_(11))
        name_label.setTextColor_(NSColor.secondaryLabelColor())
        content_view.addSubview_(name_label)

        self._panel = panel

    # ------------------------------------------------------------------
    # Button actions
    # ------------------------------------------------------------------

    def cancelClicked_(self, sender):
        self.close()

    def saveClicked_(self, sender):
        self._clear_error()
        self._do_save()

    def fieldEnterPressed_(self, sender):
        """Enter in Name or Keyword field triggers save."""
        self._clear_error()
        self._do_save()

