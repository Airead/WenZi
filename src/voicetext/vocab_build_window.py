"""Floating progress panel for vocabulary build with streaming LLM output."""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class VocabBuildProgressPanel:
    """Floating NSPanel showing vocabulary build progress and streaming LLM output.

    Layout:
        +-----------------------------------+
        | Build Vocabulary                  |
        | Batch 2/5 - extracting...         |  <- status label
        | +-------------------------------+ |
        | | [{"term": "Python", ...       | |  <- streaming LLM output (read-only)
        | |  "category": "tech",          | |
        | |  ...                          | |
        | +-------------------------------+ |
        |              [Cancel]             |
        +-----------------------------------+
    """

    _PANEL_WIDTH = 520
    _STREAM_HEIGHT = 280
    _LABEL_HEIGHT = 20
    _BUTTON_HEIGHT = 32
    _BUTTON_WIDTH = 90
    _PADDING = 12

    def __init__(self) -> None:
        self._panel = None
        self._info_label = None
        self._status_label = None
        self._token_label = None
        self._stream_text_view = None
        self._stream_font = None
        self._on_cancel: Optional[Callable[[], None]] = None
        self._confirmed_tokens: int = 0
        self._stream_chars: int = 0

    def show(
        self,
        on_cancel: Callable[[], None],
        enhance_info: str = "",
    ) -> None:
        """Show the progress panel. Must be called on the main thread.

        Args:
            on_cancel: Callback when cancel is clicked.
            enhance_info: Provider/model info string to display.
        """
        from AppKit import NSApp

        self._on_cancel = on_cancel
        # Switch to regular activation policy so panel is visible from menubar app
        NSApp.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular
        self._build_panel(enhance_info)
        self._panel.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def update_status(self, text: str) -> None:
        """Update the status label text. Thread-safe."""
        from PyObjCTools import AppHelper

        def _update():
            if self._status_label is not None:
                self._status_label.setStringValue_(text)

        AppHelper.callAfter(_update)

    def append_stream_text(self, chunk: str) -> None:
        """Append text to the streaming output view and auto-scroll. Thread-safe.

        Also updates the token label with a real-time streaming character count.
        """
        self._stream_chars += len(chunk)
        from PyObjCTools import AppHelper

        stream_chars = self._stream_chars
        confirmed = self._confirmed_tokens

        def _append():
            tv = self._stream_text_view
            if tv is None:
                return
            storage = tv.textStorage()
            from Foundation import NSAttributedString, NSDictionary

            # Use the monospace font configured on the text view
            attrs = NSDictionary.dictionaryWithObject_forKey_(
                self._stream_font, "NSFont"
            ) if self._stream_font else None
            if attrs:
                attr_str = NSAttributedString.alloc().initWithString_attributes_(
                    chunk, attrs
                )
            else:
                attr_str = NSAttributedString.alloc().initWithString_(chunk)
            storage.appendAttributedString_(attr_str)
            # Auto-scroll to bottom
            tv.scrollRangeToVisible_((storage.length(), 0))

            # Update token label with streaming progress
            if self._token_label is not None:
                if confirmed > 0:
                    self._token_label.setStringValue_(
                        f"Tokens: {confirmed:,}+  |  streaming... {stream_chars:,} chars received"
                    )
                else:
                    self._token_label.setStringValue_(
                        f"Tokens: streaming... {stream_chars:,} chars received"
                    )

        AppHelper.callAfter(_append)

    def update_token_usage(self, prompt: int, completion: int, total: int) -> None:
        """Update the token usage label with confirmed totals. Thread-safe.

        Resets the streaming character counter since the batch is done.
        """
        self._confirmed_tokens = total
        self._stream_chars = 0
        from PyObjCTools import AppHelper

        def _update():
            if self._token_label is not None:
                self._token_label.setStringValue_(
                    f"Tokens: {total:,}  (prompt {prompt:,} + completion {completion:,})"
                )

        AppHelper.callAfter(_update)

    def clear_stream_text(self) -> None:
        """Clear the streaming output view and reset stream char counter. Thread-safe."""
        self._stream_chars = 0
        from PyObjCTools import AppHelper

        def _clear():
            if self._stream_text_view is not None:
                self._stream_text_view.setString_("")

        AppHelper.callAfter(_clear)

    def close(self) -> None:
        """Close the panel. Thread-safe."""
        from PyObjCTools import AppHelper

        def _close():
            if self._panel is not None:
                # Clear delegate before closing to prevent windowWillClose: re-entry
                self._panel.setDelegate_(None)
                self._close_delegate = None
                self._panel.orderOut_(None)
                self._panel = None
            # Clear callback to prevent double-firing
            self._on_cancel = None
            # Restore accessory activation policy (statusbar-only)
            from AppKit import NSApp
            NSApp.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

        AppHelper.callAfter(_close)

    def _build_panel(self, enhance_info: str = "") -> None:
        """Build the NSPanel and all subviews."""
        from AppKit import (
            NSBackingStoreBuffered,
            NSBezelBorder,
            NSButton,
            NSClosableWindowMask,
            NSColor,
            NSStatusWindowLevel,
            NSFont,
            NSPanel,
            NSScrollView,
            NSTextField,
            NSTextView,
            NSTitledWindowMask,
        )
        from Foundation import NSMakeRect

        content_height = (
            self._PADDING  # bottom
            + self._BUTTON_HEIGHT + self._PADDING  # buttons
            + self._LABEL_HEIGHT + self._PADDING  # token label
            + self._STREAM_HEIGHT + self._PADDING  # stream view
            + self._LABEL_HEIGHT  # status label
            + self._LABEL_HEIGHT  # info label
            + self._PADDING  # top
        )

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, content_height),
            NSTitledWindowMask | NSClosableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_("Build Vocabulary")
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.center()

        # Set delegate to handle close button (X) as cancel
        delegate_cls = _get_panel_close_delegate_class()
        self._close_delegate = delegate_cls.alloc().init()
        self._close_delegate._panel_ref = self
        panel.setDelegate_(self._close_delegate)

        content_view = panel.contentView()
        inner_width = self._PANEL_WIDTH - 2 * self._PADDING

        y = self._PADDING

        # Cancel button
        cancel_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                (self._PANEL_WIDTH - self._BUTTON_WIDTH) / 2,
                y,
                self._BUTTON_WIDTH,
                self._BUTTON_HEIGHT,
            )
        )
        cancel_btn.setTitle_("Cancel")
        cancel_btn.setBezelStyle_(1)
        cancel_btn.setKeyEquivalent_("\x1b")  # ESC
        cancel_btn.setTarget_(self)
        cancel_btn.setAction_(b"cancelClicked:")
        content_view.addSubview_(cancel_btn)

        y += self._BUTTON_HEIGHT + self._PADDING

        # Streaming output (NSScrollView + NSTextView)
        scroll_frame = NSMakeRect(self._PADDING, y, inner_width, self._STREAM_HEIGHT)
        scroll = NSScrollView.alloc().initWithFrame_(scroll_frame)
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSBezelBorder)

        tv = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, inner_width, self._STREAM_HEIGHT)
        )
        tv.setMinSize_(NSMakeRect(0, 0, inner_width, 0).size)
        tv.setMaxSize_(NSMakeRect(0, 0, 1e7, 1e7).size)
        tv.setVerticallyResizable_(True)
        tv.setHorizontallyResizable_(False)
        tv.textContainer().setWidthTracksTextView_(True)
        self._stream_font = NSFont.userFixedPitchFontOfSize_(12.0)
        tv.setFont_(self._stream_font)
        tv.setEditable_(False)
        tv.setBackgroundColor_(NSColor.textBackgroundColor())
        scroll.setDocumentView_(tv)
        content_view.addSubview_(scroll)
        self._stream_text_view = tv

        y += self._STREAM_HEIGHT + self._PADDING

        # Token usage label (below stream)
        token_label = NSTextField.labelWithString_("Tokens: 0")
        token_label.setFrame_(NSMakeRect(self._PADDING, y, inner_width, self._LABEL_HEIGHT))
        token_label.setFont_(NSFont.systemFontOfSize_(11))
        token_label.setTextColor_(NSColor.secondaryLabelColor())
        content_view.addSubview_(token_label)
        self._token_label = token_label

        y += self._LABEL_HEIGHT + self._PADDING

        # Status label
        status_label = NSTextField.labelWithString_("Preparing...")
        status_label.setFrame_(NSMakeRect(self._PADDING, y, inner_width, self._LABEL_HEIGHT))
        status_label.setFont_(NSFont.boldSystemFontOfSize_(12))
        content_view.addSubview_(status_label)
        self._status_label = status_label

        y += self._LABEL_HEIGHT

        # Info label (provider / model)
        info_text = f"Provider: {enhance_info}" if enhance_info else ""
        info_label = NSTextField.labelWithString_(info_text)
        info_label.setFrame_(NSMakeRect(self._PADDING, y, inner_width, self._LABEL_HEIGHT))
        info_label.setFont_(NSFont.systemFontOfSize_(11))
        info_label.setTextColor_(NSColor.secondaryLabelColor())
        content_view.addSubview_(info_label)
        self._info_label = info_label

        self._panel = panel

    def cancelClicked_(self, sender) -> None:
        """Handle cancel button click."""
        callback = self._on_cancel
        self.close()
        if callback is not None:
            callback()


_PanelCloseDelegate = None


def _get_panel_close_delegate_class():
    """Lazily create and cache the NSObject subclass for NSWindowDelegate."""
    global _PanelCloseDelegate
    if _PanelCloseDelegate is None:
        from Foundation import NSObject

        class PanelCloseDelegate(NSObject):
            """NSWindowDelegate that triggers cancel when the panel close button is clicked."""

            _panel_ref = None

            def windowWillClose_(self, notification):
                if self._panel_ref is not None:
                    self._panel_ref.cancelClicked_(None)

        _PanelCloseDelegate = PanelCloseDelegate
    return _PanelCloseDelegate
