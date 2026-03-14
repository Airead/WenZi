"""Floating overlay panel for Direct mode streaming AI enhancement output."""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Panel dimensions
_PANEL_WIDTH = 400
_PANEL_HEIGHT = 200

# Layout constants
_PADDING = 12
_LABEL_HEIGHT = 18
_ASR_TEXT_HEIGHT = 40
_SEPARATOR_HEIGHT = 1
_STATUS_LABEL_HEIGHT = 18
_CORNER_RADIUS = 10
_SCREEN_MARGIN = 20

# ESC key code
_ESC_KEY_CODE = 53


class StreamingOverlayPanel:
    """Non-interactive floating overlay that displays streaming AI enhancement.

    Shows ASR original text at top, streaming enhanced text below.
    Does not steal focus or accept mouse events.
    """

    def __init__(self) -> None:
        self._panel: object = None
        self._asr_label: object = None
        self._status_label: object = None
        self._text_view: object = None
        self._scroll_view: object = None
        self._esc_monitor: object = None
        self._cancel_event: Optional[threading.Event] = None
        self._loading_timer: object = None
        self._loading_seconds: int = 0
        self._llm_info: str = ""

    @staticmethod
    def _dynamic_bg_color():
        """Create a dynamic background color matching RecordingIndicatorPanel."""
        from AppKit import NSColor

        def _provider(appearance):
            name = appearance.bestMatchFromAppearancesWithNames_(
                ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]
            )
            if name and "Dark" in str(name):
                return NSColor.colorWithSRGBRed_green_blue_alpha_(0.9, 0.9, 0.9, 0.85)
            return NSColor.colorWithSRGBRed_green_blue_alpha_(0.1, 0.1, 0.1, 0.85)

        return NSColor.colorWithName_dynamicProvider_(None, _provider)

    @staticmethod
    def _dynamic_text_color():
        """Create a dynamic text color that contrasts with the background."""
        from AppKit import NSColor

        def _provider(appearance):
            name = appearance.bestMatchFromAppearancesWithNames_(
                ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]
            )
            if name and "Dark" in str(name):
                return NSColor.colorWithSRGBRed_green_blue_alpha_(0.1, 0.1, 0.1, 1.0)
            return NSColor.colorWithSRGBRed_green_blue_alpha_(0.95, 0.95, 0.95, 1.0)

        return NSColor.colorWithName_dynamicProvider_(None, _provider)

    @staticmethod
    def _dynamic_secondary_text_color():
        """Create a dynamic secondary text color for ASR text."""
        from AppKit import NSColor

        def _provider(appearance):
            name = appearance.bestMatchFromAppearancesWithNames_(
                ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]
            )
            if name and "Dark" in str(name):
                return NSColor.colorWithSRGBRed_green_blue_alpha_(0.3, 0.3, 0.3, 1.0)
            return NSColor.colorWithSRGBRed_green_blue_alpha_(0.7, 0.7, 0.7, 1.0)

        return NSColor.colorWithName_dynamicProvider_(None, _provider)

    @staticmethod
    def _dynamic_separator_color():
        """Create a dynamic separator color."""
        from AppKit import NSColor

        def _provider(appearance):
            name = appearance.bestMatchFromAppearancesWithNames_(
                ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]
            )
            if name and "Dark" in str(name):
                return NSColor.colorWithSRGBRed_green_blue_alpha_(0.3, 0.3, 0.3, 0.5)
            return NSColor.colorWithSRGBRed_green_blue_alpha_(0.7, 0.7, 0.7, 0.5)

        return NSColor.colorWithName_dynamicProvider_(None, _provider)

    def show(
        self,
        asr_text: str = "",
        cancel_event: Optional[threading.Event] = None,
        animate_from_frame: object = None,
        stt_info: str = "",
        llm_info: str = "",
    ) -> None:
        """Create and show the overlay panel. Must be called on main thread."""
        try:
            from AppKit import (
                NSColor,
                NSFont,
                NSPanel,
                NSScreen,
                NSScrollView,
                NSStatusWindowLevel,
                NSTextField,
                NSTextView,
                NSView,
            )
            from Foundation import NSMakeRect

            if self._panel is not None:
                self.close()

            self._cancel_event = cancel_event
            self._loading_seconds = 0
            self._llm_info = llm_info

            # Create borderless panel
            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, _PANEL_WIDTH, _PANEL_HEIGHT),
                0,  # NSBorderlessWindowMask
                2,  # NSBackingStoreBuffered
                False,
            )
            panel.setLevel_(NSStatusWindowLevel + 1)
            panel.setOpaque_(False)
            panel.setBackgroundColor_(NSColor.clearColor())
            panel.setIgnoresMouseEvents_(True)
            panel.setHasShadow_(True)
            panel.setHidesOnDeactivate_(False)
            panel.setCollectionBehavior_(1 << 4)  # canJoinAllSpaces

            # Build content view with rounded background
            content = NSView.alloc().initWithFrame_(
                NSMakeRect(0, 0, _PANEL_WIDTH, _PANEL_HEIGHT)
            )
            content.setWantsLayer_(True)
            content.layer().setCornerRadius_(_CORNER_RADIUS)
            content.layer().setMasksToBounds_(True)

            # Background layer color
            bg_color = self._dynamic_bg_color()
            content.layer().setBackgroundColor_(bg_color.CGColor())

            inner_width = _PANEL_WIDTH - 2 * _PADDING

            # --- Top section: ASR result ---
            y = _PANEL_HEIGHT - _PADDING - _LABEL_HEIGHT

            asr_title_text = "\U0001f3a4 ASR"
            if stt_info:
                asr_title_text += f"  ({stt_info})"
            asr_title = NSTextField.labelWithString_(asr_title_text)
            asr_title.setFrame_(NSMakeRect(_PADDING, y, inner_width, _LABEL_HEIGHT))
            asr_title.setFont_(NSFont.boldSystemFontOfSize_(11.0))
            asr_title.setTextColor_(self._dynamic_secondary_text_color())
            content.addSubview_(asr_title)

            y -= _ASR_TEXT_HEIGHT
            asr_label = NSTextField.wrappingLabelWithString_(asr_text or "")
            asr_label.setFrame_(NSMakeRect(_PADDING, y, inner_width, _ASR_TEXT_HEIGHT))
            asr_label.setFont_(NSFont.systemFontOfSize_(12.0))
            asr_label.setTextColor_(self._dynamic_secondary_text_color())
            asr_label.setMaximumNumberOfLines_(2)
            content.addSubview_(asr_label)
            self._asr_label = asr_label

            # --- Separator ---
            y -= _SEPARATOR_HEIGHT + 4
            separator = NSView.alloc().initWithFrame_(
                NSMakeRect(_PADDING, y, inner_width, _SEPARATOR_HEIGHT)
            )
            separator.setWantsLayer_(True)
            separator.layer().setBackgroundColor_(
                self._dynamic_separator_color().CGColor()
            )
            content.addSubview_(separator)

            # --- Bottom section: Enhancement streaming ---
            y -= _STATUS_LABEL_HEIGHT + 4
            status_label = NSTextField.labelWithString_(self._ai_label(""))
            status_label.setFrame_(
                NSMakeRect(_PADDING, y, inner_width, _STATUS_LABEL_HEIGHT)
            )
            status_label.setFont_(NSFont.boldSystemFontOfSize_(11.0))
            status_label.setTextColor_(self._dynamic_text_color())
            content.addSubview_(status_label)
            self._status_label = status_label

            # Streaming text area (NSScrollView + NSTextView)
            stream_height = y - _PADDING
            y -= stream_height
            scroll_frame = NSMakeRect(_PADDING, _PADDING, inner_width, stream_height)
            scroll = NSScrollView.alloc().initWithFrame_(scroll_frame)
            scroll.setHasVerticalScroller_(True)
            scroll.setBorderType_(0)  # NSNoBorder
            scroll.setDrawsBackground_(False)

            tv = NSTextView.alloc().initWithFrame_(
                NSMakeRect(0, 0, inner_width, stream_height)
            )
            tv.setMinSize_(NSMakeRect(0, 0, inner_width, 0).size)
            tv.setMaxSize_(NSMakeRect(0, 0, 1e7, 1e7).size)
            tv.setVerticallyResizable_(True)
            tv.setHorizontallyResizable_(False)
            tv.textContainer().setWidthTracksTextView_(True)
            tv.setFont_(NSFont.systemFontOfSize_(13.0))
            tv.setTextColor_(self._dynamic_text_color())
            tv.setEditable_(False)
            tv.setSelectable_(False)
            tv.setDrawsBackground_(False)

            scroll.setDocumentView_(tv)
            content.addSubview_(scroll)
            self._text_view = tv
            self._scroll_view = scroll

            panel.setContentView_(content)

            # Calculate center position as final target
            screen = NSScreen.mainScreen()
            target_x, target_y = 0, 0
            if screen:
                sf = screen.visibleFrame()
                target_x = sf.origin.x + (sf.size.width - _PANEL_WIDTH) / 2
                target_y = sf.origin.y + (sf.size.height - _PANEL_HEIGHT) / 2

            if animate_from_frame is not None:
                from AppKit import NSAnimationContext

                # Start from indicator position/size, expand to center
                panel.setFrame_display_(animate_from_frame, False)
                panel.setAlphaValue_(0.0)
                panel.orderFront_(None)

                target_frame = NSMakeRect(
                    target_x, target_y, _PANEL_WIDTH, _PANEL_HEIGHT
                )
                NSAnimationContext.beginGrouping()
                ctx = NSAnimationContext.currentContext()
                ctx.setDuration_(0.3)
                panel.animator().setFrame_display_(target_frame, True)
                panel.animator().setAlphaValue_(1.0)
                NSAnimationContext.endGrouping()
            else:
                panel.setFrameOrigin_((target_x, target_y))
                panel.orderFront_(None)

            self._panel = panel

            # Register global ESC key monitor
            self._register_esc_monitor()

            # Start loading timer
            self._start_loading_timer()

            logger.debug("Streaming overlay shown")
        except Exception:
            logger.error("Failed to show streaming overlay", exc_info=True)

    def _register_esc_monitor(self) -> None:
        """Register a global key event monitor for ESC key."""
        try:
            from AppKit import NSEvent

            NSKeyDownMask = 1 << 10

            def _handler(event):
                if event.keyCode() == _ESC_KEY_CODE:
                    if self._cancel_event is not None:
                        self._cancel_event.set()
                        logger.info("Streaming cancelled via ESC key")

            self._esc_monitor = (
                NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                    NSKeyDownMask, _handler
                )
            )
        except Exception:
            logger.error("Failed to register ESC monitor", exc_info=True)

    def _remove_esc_monitor(self) -> None:
        """Remove the global ESC key monitor."""
        if self._esc_monitor is not None:
            try:
                from AppKit import NSEvent

                NSEvent.removeMonitor_(self._esc_monitor)
            except Exception:
                logger.error("Failed to remove ESC monitor", exc_info=True)
            self._esc_monitor = None

    # ------------------------------------------------------------------
    # Loading timer (elapsed seconds while waiting for first chunk)
    # ------------------------------------------------------------------

    def _start_loading_timer(self) -> None:
        """Start a 1-second repeating timer that updates the status label."""
        self._stop_loading_timer()
        self._loading_seconds = 0
        try:
            from Foundation import NSTimer

            self._loading_timer = (
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    1.0, self, b"tickLoadingTimer:", None, True,
                )
            )
        except Exception:
            logger.error("Failed to start loading timer", exc_info=True)

    def _stop_loading_timer(self) -> None:
        """Stop the loading timer if running."""
        if self._loading_timer is not None:
            try:
                self._loading_timer.invalidate()
            except Exception:
                pass
            self._loading_timer = None

    def _ai_label(self, suffix: str) -> str:
        """Build the AI status label with optional LLM info prefix."""
        base = "\u2728 AI"
        if self._llm_info:
            base += f" ({self._llm_info})"
        if suffix:
            return f"{base}  {suffix}"
        return base

    def tickLoadingTimer_(self, timer) -> None:
        """NSTimer callback: update status label with elapsed seconds."""
        self._loading_seconds += 1
        if self._status_label is not None:
            self._status_label.setStringValue_(
                self._ai_label(f"\u23f3 {self._loading_seconds}s")
            )

    # ------------------------------------------------------------------
    # Streaming text updates (all thread-safe via callAfter)
    # ------------------------------------------------------------------

    def append_text(self, chunk: str, completion_tokens: int = 0) -> None:
        """Append content text to the streaming text view. Thread-safe."""
        from PyObjCTools import AppHelper

        def _append():
            self._stop_loading_timer()
            tv = self._text_view
            if tv is None:
                return
            from AppKit import NSFont
            from Foundation import NSAttributedString, NSDictionary

            attrs = NSDictionary.dictionaryWithObjects_forKeys_(
                [self._dynamic_text_color(), NSFont.systemFontOfSize_(13.0)],
                ["NSColor", "NSFont"],
            )
            attr_str = (
                NSAttributedString.alloc().initWithString_attributes_(chunk, attrs)
            )
            tv.textStorage().appendAttributedString_(attr_str)
            tv.scrollRangeToVisible_((tv.textStorage().length(), 0))
            # Update status with token count
            if completion_tokens > 0 and self._status_label is not None:
                self._status_label.setStringValue_(
                    self._ai_label(f"Tokens: \u2193{completion_tokens:,}")
                )

        AppHelper.callAfter(_append)

    def append_thinking_text(self, chunk: str, thinking_tokens: int = 0) -> None:
        """Append thinking/reasoning text in italic secondary color. Thread-safe."""
        from PyObjCTools import AppHelper

        def _append():
            self._stop_loading_timer()
            tv = self._text_view
            if tv is None:
                return
            from AppKit import NSFont, NSFontManager
            from Foundation import NSAttributedString, NSDictionary

            font = NSFont.systemFontOfSize_(13.0)
            fm = NSFontManager.sharedFontManager()
            italic_font = fm.convertFont_toHaveTrait_(font, 0x01)  # NSItalicFontMask

            attrs = NSDictionary.dictionaryWithObjects_forKeys_(
                [self._dynamic_secondary_text_color(), italic_font],
                ["NSColor", "NSFont"],
            )
            attr_str = (
                NSAttributedString.alloc().initWithString_attributes_(chunk, attrs)
            )
            tv.textStorage().appendAttributedString_(attr_str)
            tv.scrollRangeToVisible_((tv.textStorage().length(), 0))
            # Update status with thinking token count
            if thinking_tokens > 0 and self._status_label is not None:
                self._status_label.setStringValue_(
                    self._ai_label(f"\u25b6 Thinking: {thinking_tokens:,}")
                )

        AppHelper.callAfter(_append)

    def set_status(self, text: str) -> None:
        """Update the status label. Thread-safe."""
        from PyObjCTools import AppHelper

        def _update():
            if self._status_label is not None:
                self._status_label.setStringValue_(text)

        AppHelper.callAfter(_update)

    def set_complete(self, usage: dict | None = None) -> None:
        """Mark enhancement complete, show final token usage. Thread-safe."""
        from PyObjCTools import AppHelper

        def _update():
            self._stop_loading_timer()
            if self._status_label is None:
                return
            if usage and usage.get("total_tokens"):
                total = usage["total_tokens"]
                prompt = usage.get("prompt_tokens", 0)
                completion = usage.get("completion_tokens", 0)
                self._status_label.setStringValue_(
                    self._ai_label(
                        f"Tokens: {total:,} (\u2191{prompt:,} \u2193{completion:,})"
                    )
                )
            else:
                self._status_label.setStringValue_(self._ai_label(""))

        AppHelper.callAfter(_update)

    def clear_text(self) -> None:
        """Clear the streaming text view. Thread-safe."""
        from PyObjCTools import AppHelper

        def _clear():
            if self._text_view is not None:
                self._text_view.setString_("")

        AppHelper.callAfter(_clear)

    def close(self) -> None:
        """Close and clean up the overlay panel. Thread-safe."""
        from PyObjCTools import AppHelper

        def _close():
            self._stop_loading_timer()
            self._remove_esc_monitor()
            self._cancel_event = None

            if self._panel is not None:
                self._panel.orderOut_(None)
                self._panel = None

            self._asr_label = None
            self._status_label = None
            self._text_view = None
            self._scroll_view = None
            logger.debug("Streaming overlay closed")

        AppHelper.callAfter(_close)
