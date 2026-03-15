"""Leader-key floating alert panel.

Displays available sub-key mappings when a leader trigger key is held.
Uses native NSPanel + NSTextField for a lightweight, dark-mode-aware overlay.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicetext.scripting.registry import LeaderMapping

logger = logging.getLogger(__name__)


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


def _dynamic_title_color():
    """Title text color that adapts to light/dark mode."""
    from AppKit import NSColor

    def _provider(appearance):
        name = appearance.bestMatchFromAppearancesWithNames_(
            ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]
        )
        if name and "Dark" in str(name):
            return NSColor.colorWithSRGBRed_green_blue_alpha_(0.95, 0.95, 0.95, 1.0)
        return NSColor.colorWithSRGBRed_green_blue_alpha_(0.1, 0.1, 0.1, 1.0)

    return NSColor.colorWithName_dynamicProvider_(None, _provider)


def _dynamic_item_color():
    """Mapping item text color that adapts to light/dark mode."""
    from AppKit import NSColor

    def _provider(appearance):
        name = appearance.bestMatchFromAppearancesWithNames_(
            ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]
        )
        if name and "Dark" in str(name):
            return NSColor.colorWithSRGBRed_green_blue_alpha_(0.75, 0.75, 0.75, 1.0)
        return NSColor.colorWithSRGBRed_green_blue_alpha_(0.35, 0.35, 0.35, 1.0)

    return NSColor.colorWithName_dynamicProvider_(None, _provider)


class LeaderAlertPanel:
    """Floating panel showing leader-key mappings."""

    def __init__(self) -> None:
        self._panel: object = None

    @property
    def is_visible(self) -> bool:
        return self._panel is not None

    def show(self, trigger_key: str, mappings: list[LeaderMapping]) -> None:
        """Create and display the leader alert. Must run on main thread."""
        from AppKit import (
            NSBackingStoreBuffered,
            NSColor,
            NSFont,
            NSMakeRect,
            NSPanel,
            NSScreen,
            NSStatusWindowLevel,
            NSTextField,
        )

        if self._panel is not None:
            self.close()

        padding = 16
        line_height = 24
        title_height = 28
        num_lines = len(mappings)
        panel_width = 320
        panel_height = padding + title_height + num_lines * line_height + padding

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

        content = panel.contentView()

        # Title
        y = panel_height - padding - title_height
        title_font = NSFont.boldSystemFontOfSize_(15.0)
        title = NSTextField.labelWithString_(f"Leader: {trigger_key}")
        title.setFrame_(NSMakeRect(padding, y, panel_width - padding * 2, title_height))
        title.setFont_(title_font)
        title.setTextColor_(_dynamic_title_color())
        title.setBackgroundColor_(NSColor.clearColor())
        title.setBezeled_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        content.addSubview_(title)

        # Mapping lines (bottom-up layout)
        item_font = NSFont.monospacedSystemFontOfSize_weight_(14.0, 0.0)
        item_color = _dynamic_item_color()

        for i, m in enumerate(mappings):
            y = panel_height - padding - title_height - (i + 1) * line_height
            desc = m.desc or m.app or m.exec_cmd or "action"
            line_text = f"  [{m.key}]  {desc}"

            label = NSTextField.labelWithString_(line_text)
            label.setFrame_(
                NSMakeRect(padding, y, panel_width - padding * 2, line_height)
            )
            label.setFont_(item_font)
            label.setTextColor_(item_color)
            label.setBackgroundColor_(NSColor.clearColor())
            label.setBezeled_(False)
            label.setEditable_(False)
            label.setSelectable_(False)
            content.addSubview_(label)

        # Position: center-top of main screen
        screen = NSScreen.mainScreen()
        if screen:
            sf = screen.frame()
            x = sf.origin.x + (sf.size.width - panel_width) / 2
            y = sf.origin.y + sf.size.height - panel_height - 100
            panel.setFrameOrigin_((x, y))

        panel.orderFrontRegardless()
        self._panel = panel
        logger.debug("Leader alert shown for %s", trigger_key)

    def close(self) -> None:
        """Close the panel. Must run on main thread."""
        if self._panel is not None:
            try:
                self._panel.orderOut_(None)
            except Exception:
                pass
            self._panel = None
            logger.debug("Leader alert closed")
