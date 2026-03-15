"""vt.alert — floating on-screen alert overlay."""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# Singleton panel reference to avoid duplicates
_current_panel = None
_current_close_timer = None


def _dynamic_bg_color():
    """Semi-transparent background that adapts to light/dark mode."""
    from AppKit import NSColor

    def _provider(appearance):
        name = appearance.bestMatchFromAppearancesWithNames_(
            ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]
        )
        if name and "Dark" in str(name):
            return NSColor.colorWithSRGBRed_green_blue_alpha_(0.15, 0.15, 0.15, 0.92)
        return NSColor.colorWithSRGBRed_green_blue_alpha_(0.97, 0.97, 0.97, 0.92)

    return NSColor.colorWithName_dynamicProvider_(None, _provider)


def _dynamic_text_color():
    """Text color that adapts to light/dark mode."""
    from AppKit import NSColor

    def _provider(appearance):
        name = appearance.bestMatchFromAppearancesWithNames_(
            ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]
        )
        if name and "Dark" in str(name):
            return NSColor.colorWithSRGBRed_green_blue_alpha_(0.95, 0.95, 0.95, 1.0)
        return NSColor.colorWithSRGBRed_green_blue_alpha_(0.1, 0.1, 0.1, 1.0)

    return NSColor.colorWithName_dynamicProvider_(None, _provider)


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
        NSBackingStoreBuffered,
        NSColor,
        NSFont,
        NSMakeRect,
        NSPanel,
        NSScreen,
        NSStatusWindowLevel,
        NSTextAlignmentCenter,
        NSTextField,
    )

    # Close existing alert
    if _current_panel is not None:
        _current_panel.orderOut_(None)
        _current_panel = None
    if _current_close_timer is not None:
        _current_close_timer.cancel()
        _current_close_timer = None

    # Measure text to size the panel
    font = NSFont.systemFontOfSize_(18.0)
    padding = 24
    # Rough width estimate: 11px per character, min 200
    text_width = max(len(text) * 11, 200)
    panel_width = min(text_width + padding * 2, 600)
    panel_height = 50

    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, panel_width, panel_height),
        0,  # NSBorderlessWindowMask
        NSBackingStoreBuffered,
        False,
    )
    panel.setLevel_(NSStatusWindowLevel + 1)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(_dynamic_bg_color())
    panel.setHasShadow_(True)
    panel.setIgnoresMouseEvents_(True)
    panel.setMovableByWindowBackground_(False)
    panel.setHidesOnDeactivate_(False)
    panel.setCollectionBehavior_(1 << 4)  # canJoinAllSpaces

    # Round corners
    panel.contentView().setWantsLayer_(True)
    panel.contentView().layer().setCornerRadius_(10.0)
    panel.contentView().layer().setMasksToBounds_(True)

    # Text label
    label = NSTextField.labelWithString_(text)
    label.setFrame_(NSMakeRect(padding, 8, panel_width - padding * 2, 34))
    label.setFont_(font)
    label.setTextColor_(_dynamic_text_color())
    label.setAlignment_(NSTextAlignmentCenter)
    label.setBackgroundColor_(NSColor.clearColor())
    label.setBezeled_(False)
    label.setEditable_(False)
    label.setSelectable_(False)
    panel.contentView().addSubview_(label)

    # Position: center-top of main screen
    screen = NSScreen.mainScreen()
    if screen:
        sf = screen.frame()
        x = sf.origin.x + (sf.size.width - panel_width) / 2
        y = sf.origin.y + sf.size.height - panel_height - 100
        panel.setFrameOrigin_((x, y))

    panel.orderFrontRegardless()
    _current_panel = panel

    # Auto-dismiss
    def _close():
        from PyObjCTools import AppHelper

        AppHelper.callAfter(_dismiss_alert, panel)

    _current_close_timer = threading.Timer(duration, _close)
    _current_close_timer.daemon = True
    _current_close_timer.start()


def _dismiss_alert(panel) -> None:
    """Close the alert panel. Must run on main thread."""
    global _current_panel, _current_close_timer

    try:
        panel.orderOut_(None)
    except Exception:
        pass
    if _current_panel is panel:
        _current_panel = None
    _current_close_timer = None
