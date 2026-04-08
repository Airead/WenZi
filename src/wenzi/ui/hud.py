"""Lightweight HUD overlay — auto-dismissing toast notification.

Displays a brief message at the center of the screen, similar to
macOS volume/brightness indicators.  Fades in, holds, then fades out.

Usage::

    from wenzi.ui.hud import show_hud
    show_hud("Snippet saved")
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Timing
_FADE_IN = 0.15
_HOLD = 1.2
_FADE_OUT = 0.4

# Layout
_MIN_WIDTH = 200
_MAX_WIDTH = 500
_PADDING_H = 24
_PADDING_V = 16
_CORNER_RADIUS = 14

_current_hud = None


def show_hud(message: str) -> None:
    """Show a HUD message on screen. Must be called from the main thread."""
    global _current_hud
    if _current_hud is not None:
        try:
            _current_hud.orderOut_(None)
        except Exception:
            pass

    from AppKit import (
        NSBackingStoreBuffered,
        NSColor,
        NSFont,
        NSPanel,
        NSScreen,
        NSStatusWindowLevel,
        NSTextField,
    )
    from Foundation import NSMakeRect, NSTimer

    font = NSFont.systemFontOfSize_weight_(13, 0.3)

    # Measure text size to auto-fit
    label = NSTextField.labelWithString_(message)
    label.setFont_(font)
    # White is intentional here: the HUD has a fixed dark (0,0,0,0.7) background
    # regardless of system appearance, so white text is always readable.
    label.setTextColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(1.0, 1.0, 1.0, 1.0))
    label.setAlignment_(1)  # NSTextAlignmentCenter
    label.setMaximumNumberOfLines_(0)  # allow wrapping
    label.setPreferredMaxLayoutWidth_(_MAX_WIDTH - 2 * _PADDING_H)
    label.sizeToFit()
    text_size = label.frame().size

    hud_w = max(_MIN_WIDTH, text_size.width + 2 * _PADDING_H)
    hud_h = text_size.height + 2 * _PADDING_V

    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, hud_w, hud_h),
        0,  # Borderless
        NSBackingStoreBuffered,
        False,
    )
    panel.setLevel_(NSStatusWindowLevel + 2)
    panel.setOpaque_(False)
    panel.setHasShadow_(True)
    panel.setIgnoresMouseEvents_(True)
    panel.setFloatingPanel_(True)
    panel.setHidesOnDeactivate_(False)
    panel.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 8))  # canJoinAllSpaces | stationary | fullScreenAuxiliary
    panel.setBackgroundColor_(NSColor.clearColor())

    # Rounded semi-transparent background
    content = panel.contentView()
    content.setWantsLayer_(True)
    layer = content.layer()
    layer.setCornerRadius_(_CORNER_RADIUS)
    layer.setMasksToBounds_(True)

    bg_color = NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.7)
    layer.setBackgroundColor_(bg_color.CGColor())

    # Position label centered in panel
    label.setFrame_(NSMakeRect(_PADDING_H, _PADDING_V, hud_w - 2 * _PADDING_H, text_size.height))
    content.addSubview_(label)

    # Center on screen
    screen = NSScreen.mainScreen()
    if screen:
        sf = screen.frame()
        x = sf.origin.x + (sf.size.width - hud_w) / 2
        y = sf.origin.y + (sf.size.height - hud_h) / 2
        panel.setFrameOrigin_((x, y))
    else:
        panel.center()

    # Show with fade-in
    panel.setAlphaValue_(0.0)
    panel.orderFrontRegardless()
    _current_hud = panel

    from AppKit import NSAnimationContext

    NSAnimationContext.beginGrouping()
    NSAnimationContext.currentContext().setDuration_(_FADE_IN)
    panel.animator().setAlphaValue_(1.0)
    NSAnimationContext.endGrouping()

    # Schedule fade-out and cleanup
    def _start_fade_out(timer):
        NSAnimationContext.beginGrouping()
        ctx = NSAnimationContext.currentContext()
        ctx.setDuration_(_FADE_OUT)
        panel.animator().setAlphaValue_(0.0)
        NSAnimationContext.endGrouping()

        # Remove after fade-out completes
        def _cleanup(t):
            global _current_hud
            panel.orderOut_(None)
            if _current_hud is panel:
                _current_hud = None

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            _FADE_OUT + 0.05, _TimerHelper.with_callback(_cleanup), b"fire:", None, False,
        )

    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        _HOLD, _TimerHelper.with_callback(_start_fade_out), b"fire:", None, False,
    )


# ---------------------------------------------------------------------------
# NSTimer callback helper (NSTimer requires an NSObject target)
# ---------------------------------------------------------------------------

_TimerHelperClass = None


class _TimerHelper:
    """Wraps a Python callable as an NSTimer-compatible target."""

    @staticmethod
    def with_callback(fn):
        global _TimerHelperClass
        if _TimerHelperClass is None:
            from Foundation import NSObject

            class HUDTimerHelper(NSObject):
                _fn = None

                def fire_(self, timer):
                    if self._fn is not None:
                        self._fn(timer)

            _TimerHelperClass = HUDTimerHelper

        obj = _TimerHelperClass.alloc().init()
        obj._fn = fn
        return obj
