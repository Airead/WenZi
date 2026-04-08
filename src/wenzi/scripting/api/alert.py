"""vt.alert — floating on-screen alert overlay (HUD-style)."""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# Singleton panel reference to avoid duplicates
_current_panel = None
_current_close_timer = None

# Layout constants
_FONT_SIZE = 16.0
_H_PADDING = 28
_V_PADDING = 12
_MAX_WIDTH = 600
_MIN_WIDTH = 160
_FADE_DURATION = 0.35


def _measure_text(text: str, font):
    """Measure text size using NSAttributedString for accurate CJK support.

    Returns (ceil_width, ceil_height).  A small buffer is added to width
    because NSAttributedString.size() returns the typographic bounding box
    which can be slightly narrower than what NSTextField actually renders.
    """
    import math

    from AppKit import NSFontAttributeName
    from Foundation import NSAttributedString, NSDictionary

    attrs = NSDictionary.dictionaryWithObject_forKey_(font, NSFontAttributeName)
    astr = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
    size = astr.size()
    return math.ceil(size.width) + 8, math.ceil(size.height)


def alert(text: str, duration: float = 2.0) -> None:
    """Show a brief floating alert message on screen.

    The alert auto-dismisses after *duration* seconds.
    Thread-safe — dispatches to main thread.
    """
    from PyObjCTools import AppHelper

    AppHelper.callAfter(_show_alert, text, duration)


def _show_alert(text: str, duration: float) -> None:
    """Create and display the alert panel. Must run on main thread."""
    global _current_panel, _current_close_timer

    from AppKit import (
        NSAnimationContext,
        NSBackingStoreBuffered,
        NSColor,
        NSFont,
        NSFontWeightMedium,
        NSMakeRect,
        NSPanel,
        NSScreen,
        NSStatusWindowLevel,
        NSTextAlignmentCenter,
        NSTextField,
        NSVisualEffectMaterialHUDWindow,
        NSVisualEffectView,
    )

    # Close existing alert immediately (no fade)
    if _current_panel is not None:
        _current_panel.orderOut_(None)
        _current_panel = None
    if _current_close_timer is not None:
        _current_close_timer.cancel()
        _current_close_timer = None

    # Measure text accurately
    font = NSFont.systemFontOfSize_weight_(_FONT_SIZE, NSFontWeightMedium)
    text_width, text_height = _measure_text(text, font)

    panel_width = min(max(text_width + _H_PADDING * 2, _MIN_WIDTH), _MAX_WIDTH)
    panel_height = text_height + _V_PADDING * 2

    # Borderless panel
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, panel_width, panel_height),
        0,  # NSBorderlessWindowMask
        NSBackingStoreBuffered,
        False,
    )
    panel.setLevel_(NSStatusWindowLevel + 1)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setHasShadow_(True)
    panel.setIgnoresMouseEvents_(True)
    panel.setMovableByWindowBackground_(False)
    panel.setHidesOnDeactivate_(False)
    panel.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 8))  # canJoinAllSpaces | stationary | fullScreenAuxiliary

    # Vibrancy background (macOS frosted glass)
    vibrancy = NSVisualEffectView.alloc().initWithFrame_(
        NSMakeRect(0, 0, panel_width, panel_height)
    )
    vibrancy.setMaterial_(NSVisualEffectMaterialHUDWindow)
    vibrancy.setState_(1)  # NSVisualEffectStateActive — always active
    vibrancy.setWantsLayer_(True)
    vibrancy.layer().setCornerRadius_(panel_height / 2)  # pill shape
    vibrancy.layer().setMasksToBounds_(True)
    panel.contentView().addSubview_(vibrancy)

    # Text label
    label = NSTextField.labelWithString_(text)
    label.setFrame_(
        NSMakeRect(_H_PADDING, _V_PADDING, panel_width - _H_PADDING * 2, text_height)
    )
    label.setFont_(font)
    label.setTextColor_(NSColor.labelColor())
    label.setAlignment_(NSTextAlignmentCenter)
    label.setBackgroundColor_(NSColor.clearColor())
    label.setBezeled_(False)
    label.setEditable_(False)
    label.setSelectable_(False)
    vibrancy.addSubview_(label)

    # Position: center-top of main screen
    screen = NSScreen.mainScreen()
    if screen:
        sf = screen.frame()
        x = sf.origin.x + (sf.size.width - panel_width) / 2
        y = sf.origin.y + sf.size.height - panel_height - 100
        panel.setFrameOrigin_((x, y))

    panel.setAlphaValue_(0.0)
    panel.orderFrontRegardless()
    _current_panel = panel

    # Fade in
    NSAnimationContext.beginGrouping()
    NSAnimationContext.currentContext().setDuration_(0.15)
    panel.animator().setAlphaValue_(1.0)
    NSAnimationContext.endGrouping()

    # Auto-dismiss with fade out
    def _close():
        from PyObjCTools import AppHelper

        AppHelper.callAfter(_dismiss_alert, panel)

    _current_close_timer = threading.Timer(duration, _close)
    _current_close_timer.daemon = True
    _current_close_timer.start()


def _dismiss_alert(panel) -> None:
    """Fade out and close the alert panel. Must run on main thread."""
    global _current_panel, _current_close_timer

    from AppKit import NSAnimationContext

    if _current_panel is not panel:
        # A newer alert has replaced this one; just remove quietly
        try:
            panel.orderOut_(None)
        except Exception:
            pass
        return

    def _on_fade_complete():
        global _current_panel, _current_close_timer
        try:
            panel.orderOut_(None)
        except Exception:
            pass
        if _current_panel is panel:
            _current_panel = None
        _current_close_timer = None

    NSAnimationContext.beginGrouping()
    ctx = NSAnimationContext.currentContext()
    ctx.setDuration_(_FADE_DURATION)
    ctx.setCompletionHandler_(_on_fade_complete)
    panel.animator().setAlphaValue_(0.0)
    NSAnimationContext.endGrouping()
