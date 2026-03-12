"""Floating preview panel for ASR and AI enhancement results."""

from __future__ import annotations

import logging
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ResultPreviewPanel:
    """Floating NSPanel that shows ASR result, optional AI enhancement, and editable final text.

    Layout (with mode switcher):
        ┌──────────────────────────────────┐
        │ ASR Result                        │
        │ ┌──────────────────────────────┐  │
        │ │ (read-only NSTextView)       │  │
        │ └──────────────────────────────┘  │
        │ [Off|纠错|格式|补全|增强|翻译EN]  │
        │ AI Enhancement                    │
        │ ┌──────────────────────────────┐  │
        │ │ (read-only NSTextView)       │  │
        │ └──────────────────────────────┘  │
        │ Final Result (editable)           │
        │ ┌──────────────────────────────┐  │
        │ │ (editable NSTextField)       │  │
        │ └──────────────────────────────┘  │
        │           [Cancel]  [Confirm ⏎]   │
        └──────────────────────────────────┘

    Without available_modes, the segmented control and enhance section are hidden
    when show_enhance is False (backward compatible).
    """

    # Panel dimensions
    _PANEL_WIDTH = 480
    _TEXT_HEIGHT = 60
    _EDIT_HEIGHT = 80
    _LABEL_HEIGHT = 20
    _BUTTON_HEIGHT = 32
    _PADDING = 12
    _BUTTON_WIDTH = 90
    _SEGMENT_HEIGHT = 28

    def __init__(self) -> None:
        self._panel = None
        self._asr_text_view = None
        self._enhance_label = None
        self._enhance_scroll = None
        self._enhance_text_view = None
        self._final_text_field = None
        self._mode_segment = None
        self._segment_target = None
        self._on_confirm: Optional[Callable[[str, Optional[dict]], None]] = None
        self._on_cancel: Optional[Callable[[], None]] = None
        self._on_mode_change: Optional[Callable[[str], None]] = None
        self._user_edited = False
        self._show_enhance = False
        self._asr_text = ""
        self._available_modes: List[Tuple[str, str]] = []
        self._current_mode: str = "off"
        self._asr_info: str = ""
        self._asr_wav_data: Optional[bytes] = None
        self._asr_play_button = None
        self._asr_sound = None
        self._enhance_info: str = ""
        self._enhance_request_id: int = 0
        self._system_prompt: str = ""
        self._prompt_button = None
        self._delegate = None
        self._event_monitor = None

    def show(
        self,
        asr_text: str,
        show_enhance: bool,
        on_confirm: Callable[[str, Optional[dict]], None],
        on_cancel: Callable[[], None],
        available_modes: Optional[List[Tuple[str, str]]] = None,
        current_mode: Optional[str] = None,
        on_mode_change: Optional[Callable[[str], None]] = None,
        asr_info: str = "",
        asr_wav_data: Optional[bytes] = None,
        enhance_info: str = "",
    ) -> None:
        """Show the preview panel with ASR text.

        Args:
            asr_text: The raw ASR transcription result.
            show_enhance: Whether to show the AI enhancement section.
            on_confirm: Callback with final text when user confirms.
            on_cancel: Callback when user cancels.
            available_modes: List of (mode_id, label) pairs for mode switcher.
            current_mode: Currently selected mode_id.
            on_mode_change: Callback when user switches mode in the segmented control.
            asr_info: Model/duration info string to display in ASR label.
            asr_wav_data: Raw WAV audio bytes for playback.
            enhance_info: Provider/model info string to display in enhance label.
        """
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self._on_mode_change = on_mode_change
        self._user_edited = False
        self._show_enhance = show_enhance
        self._asr_text = asr_text
        self._available_modes = available_modes or []
        self._current_mode = current_mode or "off"
        self._asr_info = asr_info
        self._asr_wav_data = asr_wav_data
        self._enhance_info = enhance_info
        self._enhance_request_id = 0

        self._build_panel(asr_text, show_enhance)

        self._panel.makeKeyAndOrderFront_(None)
        self._panel.makeFirstResponder_(self._final_text_field)
        # Move cursor to end instead of selecting all text
        editor = self._panel.fieldEditor_forObject_(True, self._final_text_field)
        if editor:
            end = editor.string().length() if editor.string() else 0
            editor.setSelectedRange_((end, 0))

        self._install_event_monitor()

        from AppKit import NSApp

        NSApp.activateIgnoringOtherApps_(True)

    def set_enhance_result(
        self,
        text: str,
        request_id: int = 0,
        usage: dict | None = None,
        system_prompt: str = "",
    ) -> None:
        """Update the AI enhancement result.

        If the user has not manually edited the final text, update it too.
        Stale results (mismatched request_id) are discarded.

        Args:
            text: The enhanced text.
            request_id: Request id to discard stale results.
            usage: Token usage dict with prompt_tokens, completion_tokens, total_tokens.
            system_prompt: The system prompt used for this enhancement.
        """
        if self._enhance_text_view is None:
            return

        from PyObjCTools import AppHelper

        def _update():
            if self._enhance_text_view is None:
                return
            # Discard stale results
            if request_id != 0 and request_id != self._enhance_request_id:
                return
            self._enhance_text_view.setString_(text)
            # Store system prompt and enable button
            if system_prompt:
                self._system_prompt = system_prompt
                if self._prompt_button is not None:
                    self._prompt_button.setEnabled_(True)
            # Update label to remove spinner, include token usage
            if self._enhance_label is not None:
                suffix = ""
                if usage and usage.get("total_tokens"):
                    total = usage["total_tokens"]
                    prompt = usage.get("prompt_tokens", 0)
                    completion = usage.get("completion_tokens", 0)
                    suffix = f"Tokens: {total:,} (\u2191{prompt:,} \u2193{completion:,})"
                self._enhance_label.setStringValue_(self._enhance_label_text(suffix))
            # Auto-update final text if user hasn't edited
            if not self._user_edited and self._final_text_field is not None:
                self._final_text_field.setStringValue_(text)

        AppHelper.callAfter(_update)

    def set_enhance_loading(self) -> None:
        """Show loading state in the enhancement section."""
        from PyObjCTools import AppHelper

        def _update():
            if self._enhance_label is not None:
                self._enhance_label.setStringValue_(self._enhance_label_text("\u23f3 Processing..."))
            if self._enhance_text_view is not None:
                self._enhance_text_view.setString_("")
            self._user_edited = False
            self._show_enhance = True

        AppHelper.callAfter(_update)

    def set_enhance_off(self) -> None:
        """Show off state: clear enhancement and restore ASR text to final field."""
        from PyObjCTools import AppHelper

        def _update():
            if self._enhance_label is not None:
                self._enhance_label.setStringValue_(self._enhance_label_text("Off"))
            if self._enhance_text_view is not None:
                self._enhance_text_view.setString_("")
            if not self._user_edited and self._final_text_field is not None:
                self._final_text_field.setStringValue_(self._asr_text)
            self._show_enhance = False

        AppHelper.callAfter(_update)

    def _enhance_label_text(self, suffix: str = "") -> str:
        """Build the enhance label string with optional provider/model info."""
        base = "AI"
        if self._enhance_info:
            base = f"AI ({self._enhance_info})"
        if suffix:
            return f"{base}  {suffix}"
        return base

    @property
    def enhance_request_id(self) -> int:
        """Return the current enhance request id."""
        return self._enhance_request_id

    @enhance_request_id.setter
    def enhance_request_id(self, value: int) -> None:
        self._enhance_request_id = value

    @property
    def is_visible(self) -> bool:
        """Return True if the panel is currently displayed."""
        return self._panel is not None and self._panel.isVisible()

    def bring_to_front(self) -> None:
        """Bring the panel to the front if it is visible."""
        if self._panel is not None and self._panel.isVisible():
            self._panel.makeKeyAndOrderFront_(None)
            from AppKit import NSApp

            NSApp.activateIgnoringOtherApps_(True)

    def close(self) -> None:
        """Close the panel."""
        self._stop_playback()
        self._remove_event_monitor()
        if self._panel is not None:
            self._panel.orderOut_(None)
            self._panel = None

    def _handle_key_event(self, event):
        """Handle key events for ⌘1~⌘9 mode switching."""
        if self._panel is None or not self._panel.isKeyWindow():
            return event

        from AppKit import NSCommandKeyMask, NSDeviceIndependentModifierFlagsMask

        modifier_flags = event.modifierFlags() & NSDeviceIndependentModifierFlagsMask
        if modifier_flags != NSCommandKeyMask:
            return event

        chars = event.charactersIgnoringModifiers()
        if not chars or len(chars) != 1:
            return event

        char = chars[0] if isinstance(chars, str) else str(chars)
        if char < "1" or char > "9":
            return event

        index = int(char) - 1
        if index >= len(self._available_modes):
            return event

        self._switch_to_mode(index)
        return None  # Consume the event

    def _switch_to_mode(self, index: int) -> None:
        """Switch to the mode at the given index, updating segment and triggering callback."""
        if self._mode_segment is not None:
            self._mode_segment.setSelectedSegment_(index)
        self._on_segment_changed(index)

    def _install_event_monitor(self) -> None:
        """Install a local event monitor for keyboard shortcuts."""
        self._remove_event_monitor()
        if not self._available_modes:
            return

        from AppKit import NSEvent, NSKeyDownMask

        self._event_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSKeyDownMask, self._handle_key_event
        )

    def _remove_event_monitor(self) -> None:
        """Remove the local event monitor if installed."""
        if self._event_monitor is not None:
            from AppKit import NSEvent

            NSEvent.removeMonitor_(self._event_monitor)
            self._event_monitor = None

    def _build_panel(self, asr_text: str, show_enhance: bool) -> None:
        """Build the NSPanel and all subviews."""
        from AppKit import (
            NSApp,
            NSBackingStoreBuffered,
            NSBezelBorder,
            NSButton,
            NSClosableWindowMask,
            NSStatusWindowLevel,
            NSFont,
            NSLineBreakByWordWrapping,
            NSPanel,
            NSScrollView,
            NSSegmentedControl,
            NSTextField,
            NSTextView,
            NSTitledWindowMask,
        )
        from Foundation import NSMakeRect

        has_modes = len(self._available_modes) > 0
        # Always show enhance section when mode switcher is available
        show_enhance_section = show_enhance or has_modes

        # Calculate total height
        content_height = self._PADDING  # bottom padding
        content_height += self._BUTTON_HEIGHT + self._PADDING  # buttons
        content_height += self._EDIT_HEIGHT + self._PADDING  # final edit
        content_height += self._LABEL_HEIGHT  # final label
        if show_enhance_section:
            content_height += self._TEXT_HEIGHT + self._PADDING  # enhance text
            content_height += self._LABEL_HEIGHT  # enhance label
        if has_modes:
            content_height += self._SEGMENT_HEIGHT + self._PADDING  # segmented control
        content_height += self._TEXT_HEIGHT + self._PADDING  # asr text
        content_height += self._LABEL_HEIGHT  # asr label
        content_height += self._PADDING  # top padding

        # Create panel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, content_height),
            NSTitledWindowMask | NSClosableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_("Preview")
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.center()

        content_view = panel.contentView()
        inner_width = self._PANEL_WIDTH - 2 * self._PADDING

        # Layout from bottom to top
        y = self._PADDING

        # Buttons row
        cancel_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                self._PANEL_WIDTH - self._PADDING - 2 * self._BUTTON_WIDTH - 8,
                y,
                self._BUTTON_WIDTH,
                self._BUTTON_HEIGHT,
            )
        )
        cancel_btn.setTitle_("Cancel")
        cancel_btn.setBezelStyle_(1)  # NSRoundedBezelStyle
        cancel_btn.setKeyEquivalent_("\x1b")  # Escape
        cancel_btn.setTarget_(self)
        cancel_btn.setAction_(b"cancelClicked:")
        content_view.addSubview_(cancel_btn)

        confirm_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                self._PANEL_WIDTH - self._PADDING - self._BUTTON_WIDTH,
                y,
                self._BUTTON_WIDTH,
                self._BUTTON_HEIGHT,
            )
        )
        confirm_btn.setTitle_("Confirm \u23ce")
        confirm_btn.setBezelStyle_(1)
        confirm_btn.setKeyEquivalent_("\r")  # Enter
        confirm_btn.setTarget_(self)
        confirm_btn.setAction_(b"confirmClicked:")
        content_view.addSubview_(confirm_btn)

        y += self._BUTTON_HEIGHT + self._PADDING

        # Final result label
        final_label = NSTextField.labelWithString_("Final Result (editable)")
        final_label.setFrame_(NSMakeRect(self._PADDING, y + self._EDIT_HEIGHT, inner_width, self._LABEL_HEIGHT))
        final_label.setFont_(NSFont.boldSystemFontOfSize_(12))
        content_view.addSubview_(final_label)

        # Final result editable text field (NSTextField with wrapping)
        final_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(self._PADDING, y, inner_width, self._EDIT_HEIGHT)
        )
        final_field.setEditable_(True)
        final_field.setBezeled_(True)
        final_field.setFont_(NSFont.userFixedPitchFontOfSize_(12.0))
        final_field.setStringValue_(asr_text)
        final_field.setUsesSingleLineMode_(False)
        final_field.cell().setWraps_(True)
        final_field.cell().setScrollable_(False)
        final_field.cell().setLineBreakMode_(NSLineBreakByWordWrapping)
        # Enter triggers confirm via the button's keyEquivalent
        content_view.addSubview_(final_field)
        self._final_text_field = final_field

        # Set up delegate to track user edits
        self._delegate = _TextFieldEditDelegate.alloc().init()
        self._delegate._panel_ref = self
        final_field.setDelegate_(self._delegate)

        y += self._EDIT_HEIGHT + self._LABEL_HEIGHT + self._PADDING

        # AI Enhancement section
        if show_enhance_section:
            # Determine initial label text
            if not show_enhance:
                enhance_label_text = self._enhance_label_text("Off")
            else:
                enhance_label_text = self._enhance_label_text("\u23f3 Processing...")

            enhance_label = NSTextField.labelWithString_(enhance_label_text)
            enhance_label.setFrame_(NSMakeRect(self._PADDING, y + self._TEXT_HEIGHT, inner_width - 80, self._LABEL_HEIGHT))
            enhance_label.setFont_(NSFont.boldSystemFontOfSize_(12))
            content_view.addSubview_(enhance_label)
            self._enhance_label = enhance_label

            # "Prompt ⓘ" button to view system prompt
            prompt_btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(
                    self._PANEL_WIDTH - self._PADDING - 72,
                    y + self._TEXT_HEIGHT,
                    72,
                    self._LABEL_HEIGHT,
                )
            )
            prompt_btn.setTitle_("Prompt \u24d8")
            prompt_btn.setBezelStyle_(1)
            prompt_btn.setBordered_(True)
            prompt_btn.setFont_(NSFont.systemFontOfSize_(10))
            prompt_btn.setEnabled_(bool(self._system_prompt))
            prompt_btn.setTarget_(self)
            prompt_btn.setAction_(b"promptInfoClicked:")
            content_view.addSubview_(prompt_btn)
            self._prompt_button = prompt_btn

            enhance_scroll, enhance_tv = self._make_text_view(
                NSMakeRect(self._PADDING, y, inner_width, self._TEXT_HEIGHT),
            )
            enhance_tv.setString_("")
            content_view.addSubview_(enhance_scroll)
            self._enhance_text_view = enhance_tv
            self._enhance_scroll = enhance_scroll

            y += self._TEXT_HEIGHT + self._LABEL_HEIGHT + self._PADDING
        else:
            self._enhance_label = None
            self._enhance_text_view = None
            self._enhance_scroll = None
            self._prompt_button = None

        # Mode segmented control
        if has_modes:
            segment = NSSegmentedControl.alloc().initWithFrame_(
                NSMakeRect(self._PADDING, y, inner_width, self._SEGMENT_HEIGHT)
            )
            segment.setSegmentCount_(len(self._available_modes))
            selected_index = 0
            for i, (mode_id, label) in enumerate(self._available_modes):
                segment.setLabel_forSegment_(label, i)
                if mode_id == self._current_mode:
                    selected_index = i
            segment.setSelectedSegment_(selected_index)

            # Create action target for segment changes
            self._segment_target = _SegmentActionTarget.alloc().init()
            self._segment_target._panel_ref = self
            segment.setTarget_(self._segment_target)
            segment.setAction_(b"segmentChanged:")

            content_view.addSubview_(segment)
            self._mode_segment = segment

            y += self._SEGMENT_HEIGHT + self._PADDING
        else:
            self._mode_segment = None
            self._segment_target = None

        # ASR Result label
        asr_label_text = "ASR"
        if self._asr_info:
            asr_label_text = f"ASR ({self._asr_info})"
        play_btn_width = 62
        label_width = inner_width - play_btn_width - 4 if self._asr_wav_data else inner_width
        asr_label = NSTextField.labelWithString_(asr_label_text)
        asr_label.setFrame_(NSMakeRect(self._PADDING, y + self._TEXT_HEIGHT, label_width, self._LABEL_HEIGHT))
        asr_label.setFont_(NSFont.boldSystemFontOfSize_(12))
        content_view.addSubview_(asr_label)

        # "Play ▶" button to replay recorded audio
        if self._asr_wav_data:
            play_btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(
                    self._PANEL_WIDTH - self._PADDING - play_btn_width,
                    y + self._TEXT_HEIGHT,
                    play_btn_width,
                    self._LABEL_HEIGHT,
                )
            )
            play_btn.setTitle_("Play \u25b6")
            play_btn.setBezelStyle_(1)
            play_btn.setBordered_(True)
            play_btn.setFont_(NSFont.systemFontOfSize_(10))
            play_btn.setTarget_(self)
            play_btn.setAction_(b"playAudioClicked:")
            content_view.addSubview_(play_btn)
            self._asr_play_button = play_btn
        else:
            self._asr_play_button = None

        # ASR Result text view (read-only)
        asr_scroll, asr_tv = self._make_text_view(
            NSMakeRect(self._PADDING, y, inner_width, self._TEXT_HEIGHT),
        )
        asr_tv.setString_(asr_text)
        content_view.addSubview_(asr_scroll)
        self._asr_text_view = asr_tv

        self._panel = panel

    @staticmethod
    def _make_text_view(frame):
        """Create a read-only NSScrollView + NSTextView pair."""
        from AppKit import NSBezelBorder, NSColor, NSFont, NSScrollView, NSTextView
        from Foundation import NSMakeRect

        scroll = NSScrollView.alloc().initWithFrame_(frame)
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSBezelBorder)

        tv = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, frame.size.width, frame.size.height)
        )
        tv.setMinSize_(NSMakeRect(0, 0, frame.size.width, 0).size)
        tv.setMaxSize_(NSMakeRect(0, 0, 1e7, 1e7).size)
        tv.setVerticallyResizable_(True)
        tv.setHorizontallyResizable_(False)
        tv.textContainer().setWidthTracksTextView_(True)
        tv.setFont_(NSFont.userFixedPitchFontOfSize_(12.0))
        tv.setEditable_(False)
        tv.setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.95, 0.95, 0.95, 1.0
            )
        )

        scroll.setDocumentView_(tv)
        return scroll, tv

    def _on_user_edit(self) -> None:
        """Called when user edits the final text field."""
        self._user_edited = True

    def _on_segment_changed(self, selected_index: int) -> None:
        """Handle segmented control selection change."""
        if not self._available_modes or selected_index >= len(self._available_modes):
            return
        mode_id = self._available_modes[selected_index][0]
        self._current_mode = mode_id
        if self._on_mode_change is not None:
            self._on_mode_change(mode_id)

    def confirmClicked_(self, sender) -> None:
        """Handle confirm button click."""
        if self._final_text_field is not None and self._on_confirm is not None:
            text = self._final_text_field.stringValue()
            correction_info = None
            if self._user_edited and self._show_enhance and self._enhance_text_view is not None:
                enhanced = self._enhance_text_view.string()
                correction_info = {
                    "asr_text": self._asr_text,
                    "enhanced_text": enhanced,
                    "final_text": text,
                }
            self.close()
            self._on_confirm(text, correction_info)

    def cancelClicked_(self, sender) -> None:
        """Handle cancel button click."""
        self.close()
        if self._on_cancel is not None:
            self._on_cancel()

    def playAudioClicked_(self, sender) -> None:
        """Handle Play ▶ button click — play back the recorded WAV audio."""
        if not self._asr_wav_data:
            return
        self._play_wav(self._asr_wav_data)

    def _play_wav(self, wav_data: bytes) -> None:
        """Play WAV audio data using NSSound."""
        from AppKit import NSSound
        from Foundation import NSData

        # Stop any currently playing sound
        self._stop_playback()

        ns_data = NSData.dataWithBytes_length_(wav_data, len(wav_data))
        sound = NSSound.alloc().initWithData_(ns_data)
        if sound:
            sound.play()
            self._asr_sound = sound

    def _stop_playback(self) -> None:
        """Stop any currently playing audio."""
        if self._asr_sound is not None:
            try:
                self._asr_sound.stop()
            except Exception:
                pass
            self._asr_sound = None

    def promptInfoClicked_(self, sender) -> None:
        """Handle Prompt ⓘ button click — show system prompt in a popup panel."""
        if not self._system_prompt:
            return
        self._show_system_prompt_panel()

    def _show_system_prompt_panel(self) -> None:
        """Display the system prompt in a read-only scrollable panel."""
        from AppKit import (
            NSBackingStoreBuffered,
            NSClosableWindowMask,
            NSFont,
            NSPanel,
            NSResizableWindowMask,
            NSScrollView,
            NSStatusWindowLevel,
            NSTextView,
            NSTitledWindowMask,
        )
        from Foundation import NSMakeRect

        width, height = 520, 400
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, height),
            NSTitledWindowMask | NSClosableWindowMask | NSResizableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_("System Prompt")
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.center()

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, width, height)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setAutoresizingMask_(0x12)  # NSViewWidthSizable | NSViewHeightSizable

        tv = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, width, height)
        )
        tv.setEditable_(False)
        tv.setFont_(NSFont.userFixedPitchFontOfSize_(12.0))
        tv.setString_(self._system_prompt)
        tv.setVerticallyResizable_(True)
        tv.setHorizontallyResizable_(False)
        tv.textContainer().setWidthTracksTextView_(True)
        tv.setAutoresizingMask_(0x10)  # NSViewWidthSizable

        scroll.setDocumentView_(tv)
        panel.contentView().addSubview_(scroll)
        panel.makeKeyAndOrderFront_(None)


def _create_text_field_delegate_class():
    """Create an NSObject subclass for NSTextFieldDelegate."""
    from Foundation import NSObject

    class TextFieldEditDelegate(NSObject):
        """NSTextFieldDelegate that tracks user edits."""

        _panel_ref = None

        def controlTextDidChange_(self, notification):
            if self._panel_ref is not None:
                self._panel_ref._on_user_edit()

    return TextFieldEditDelegate


def _create_segment_action_target_class():
    """Create an NSObject subclass to handle NSSegmentedControl actions."""
    from Foundation import NSObject

    class SegmentActionTarget(NSObject):
        """Action target for NSSegmentedControl."""

        _panel_ref = None

        def segmentChanged_(self, sender):
            if self._panel_ref is not None:
                selected = sender.selectedSegment()
                self._panel_ref._on_segment_changed(selected)

    return SegmentActionTarget


_TextFieldEditDelegate = _create_text_field_delegate_class()
_SegmentActionTarget = _create_segment_action_target_class()
