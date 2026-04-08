"""Floating overlay panel that displays partial transcription during recording."""

from __future__ import annotations

import logging
import weakref

logger = logging.getLogger(__name__)

# Panel dimensions
_PANEL_WIDTH = 350
_PANEL_MIN_HEIGHT = 40
_PANEL_MAX_HEIGHT = 160
_PADDING = 10
_CORNER_RADIUS = 10
_SCREEN_Y_OFFSET = 80  # offset below center (below recording indicator)


# Module-level NSView subclass for drawRect_-based background
try:
    from AppKit import NSAppearanceNameAqua as _AQUA
    from AppKit import NSAppearanceNameDarkAqua as _DARK_AQUA
    from AppKit import NSBezierPath as _BP
    from AppKit import NSColor as _NC
    from AppKit import NSView as _NV

    def _make_bg_color():
        def _provider(appearance):
            name = appearance.bestMatchFromAppearancesWithNames_([_AQUA, _DARK_AQUA])
            if name == _DARK_AQUA:
                return _NC.colorWithSRGBRed_green_blue_alpha_(0.15, 0.15, 0.15, 0.85)
            return _NC.colorWithSRGBRed_green_blue_alpha_(0.95, 0.95, 0.95, 0.85)
        return _NC.colorWithName_dynamicProvider_("WenZiLiveBg", _provider)

    _BG_COLOR = _make_bg_color()

    class _LiveBgView(_NV):
        def isOpaque(self):
            return False

        def drawRect_(self, rect):
            _BG_COLOR.setFill()
            _BP.bezierPathWithRoundedRect_xRadius_yRadius_(
                rect, _CORNER_RADIUS, _CORNER_RADIUS
            ).fill()

        def refresh_(self, timer):
            self.setNeedsDisplay_(True)

except Exception:
    _LiveBgView = None


class LiveTranscriptionOverlay:
    """Non-interactive floating overlay that shows partial STT text during recording.

    Must be created, shown, updated, and hidden on the main thread
    (or via AppHelper.callAfter).
    """

    # Alpha value for the inactive (waiting-for-recording) state
    _INACTIVE_ALPHA = 0.35

    # Track all live instances for bulk cleanup (weak references to allow GC)
    _instances: weakref.WeakSet[LiveTranscriptionOverlay] = weakref.WeakSet()

    def __init__(self) -> None:
        self._panel: object = None
        self._text_field: object = None
        self._content_view: object = None
        self._screen_center_y: float = 0  # cached for repositioning
        self._appearance_timer: object = None
        self._active: bool = True
        LiveTranscriptionOverlay._instances.add(self)

    _TEXT_COLOR = None

    @classmethod
    def _dynamic_text_color(cls):
        """Return a cached dynamic text color that contrasts with the background."""
        if cls._TEXT_COLOR is None:
            from AppKit import NSAppearanceNameAqua, NSAppearanceNameDarkAqua, NSColor

            def _provider(appearance):
                name = appearance.bestMatchFromAppearancesWithNames_(
                    [NSAppearanceNameAqua, NSAppearanceNameDarkAqua]
                )
                if name == NSAppearanceNameDarkAqua:
                    return NSColor.colorWithSRGBRed_green_blue_alpha_(0.95, 0.95, 0.95, 1.0)
                return NSColor.colorWithSRGBRed_green_blue_alpha_(0.1, 0.1, 0.1, 1.0)

            cls._TEXT_COLOR = NSColor.colorWithName_dynamicProvider_(
                "WenZiLiveText", _provider
            )
        return cls._TEXT_COLOR

    def show(self, active: bool = True) -> None:
        """Create and show the overlay panel. Must be called on the main thread.

        Args:
            active: If False, the panel is shown in a faded state (waiting for
                recording to start). Call ``set_active()`` to switch to full
                opacity later.
        """
        try:
            from AppKit import (
                NSColor,
                NSFont,
                NSPanel,
                NSScreen,
                NSStatusWindowLevel,
                NSTextField,
            )
            from Foundation import NSMakeRect

            if self._panel is not None:
                self.hide()

            init_h = _PANEL_MIN_HEIGHT
            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, _PANEL_WIDTH, init_h),
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
            panel.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 8))  # canJoinAllSpaces | stationary | fullScreenAuxiliary

            # Content view with drawRect_-based rounded background
            content = _LiveBgView.alloc().initWithFrame_(
                NSMakeRect(0, 0, _PANEL_WIDTH, init_h)
            )

            # Text field for partial transcription (no line limit, wraps freely)
            inner_width = _PANEL_WIDTH - 2 * _PADDING
            inner_height = init_h - 2 * _PADDING
            tf = NSTextField.wrappingLabelWithString_("")
            tf.setFrame_(NSMakeRect(_PADDING, _PADDING, inner_width, inner_height))
            tf.setFont_(NSFont.systemFontOfSize_(14.0))
            tf.setTextColor_(self._dynamic_text_color())
            tf.setMaximumNumberOfLines_(0)  # unlimited lines
            tf.setAlignment_(1)  # NSTextAlignmentCenter
            content.addSubview_(tf)
            self._text_field = tf
            self._content_view = content

            panel.setContentView_(content)

            # Position at screen center, offset below the recording indicator
            screen = NSScreen.mainScreen()
            if screen:
                sf = screen.visibleFrame()
                x = sf.origin.x + (sf.size.width - _PANEL_WIDTH) / 2
                self._screen_center_y = (
                    sf.origin.y + (sf.size.height - init_h) / 2 - _SCREEN_Y_OFFSET
                )
                panel.setFrameOrigin_((x, self._screen_center_y))

            self._active = active
            if not active:
                panel.setAlphaValue_(self._INACTIVE_ALPHA)

            panel.orderFront_(None)
            self._panel = panel
            # Refresh timer for dynamic background (same as recording indicator)
            from Foundation import NSTimer
            self._appearance_timer = (
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    0.5, content, b"refresh:", None, True,
                )
            )

            logger.debug("Live transcription overlay shown")
        except Exception:
            logger.error("Failed to show live transcription overlay", exc_info=True)

    def set_active(self) -> None:
        """Switch the overlay from faded to full opacity.

        Must be called on the main thread.
        """
        if self._active:
            return
        self._active = True
        if self._panel is not None:
            self._panel.setAlphaValue_(1.0)

    def hide(self) -> None:
        """Hide and clean up the overlay panel. Must be called on the main thread."""
        try:
            if self._appearance_timer is not None:
                self._appearance_timer.invalidate()
                self._appearance_timer = None
            if self._panel is not None:
                self._panel.orderOut_(None)
                self._panel = None
            self._text_field = None
            self._content_view = None
            LiveTranscriptionOverlay._instances.discard(self)
            logger.debug("Live transcription overlay hidden")
        except Exception as e:
            logger.warning("Failed to hide live transcription overlay: %s", e)

    def update_text(self, text: str) -> None:
        """Update the displayed partial transcription text. Must be called on the main thread."""
        if self._text_field is None or self._panel is None:
            return

        self._text_field.setStringValue_(text)
        self._resize_panel()

    def _resize_panel(self) -> None:
        """Resize the panel to fit the current text content, up to _PANEL_MAX_HEIGHT."""
        from Foundation import NSMakeRect

        tf = self._text_field
        panel = self._panel
        content = self._content_view
        if tf is None or panel is None or content is None:
            return

        # Calculate the height the text needs at the available width
        inner_width = _PANEL_WIDTH - 2 * _PADDING
        # cellSizeForBounds_ returns the size needed to render the text
        needed = tf.cell().cellSizeForBounds_(
            NSMakeRect(0, 0, inner_width, 10000)
        )
        text_h = needed.height
        new_h = min(max(text_h + 2 * _PADDING, _PANEL_MIN_HEIGHT), _PANEL_MAX_HEIGHT)

        old_frame = panel.frame()
        if abs(old_frame.size.height - new_h) < 1:
            return  # no meaningful change

        # Grow upward: keep the top edge stable by adjusting y origin
        new_y = old_frame.origin.y + old_frame.size.height - new_h
        panel.setFrame_display_(
            NSMakeRect(old_frame.origin.x, new_y, _PANEL_WIDTH, new_h), True
        )
        content.setFrame_(NSMakeRect(0, 0, _PANEL_WIDTH, new_h))

        # Reposition text field: pin to top of the content view
        inner_height = new_h - 2 * _PADDING
        tf.setFrame_(NSMakeRect(_PADDING, _PADDING, inner_width, inner_height))

    def close(self) -> None:
        """Alias for hide() for consistency with other panels."""
        self.hide()
        LiveTranscriptionOverlay._instances.discard(self)

    @classmethod
    def close_all(cls) -> None:
        """Close every live overlay instance. Must be called on the main thread."""
        for inst in list(cls._instances):
            inst.close()
