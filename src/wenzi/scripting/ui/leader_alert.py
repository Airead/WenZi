"""Leader-key floating alert panel (HUD-style).

Displays available sub-key mappings when a leader trigger key is held.
Uses NSVisualEffectView for native macOS vibrancy with fade animations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from wenzi.i18n import t

if TYPE_CHECKING:
    from wenzi.scripting.registry import LeaderMapping

logger = logging.getLogger(__name__)

# Layout constants
_PANEL_WIDTH = 360
_PADDING = 20
_TITLE_HEIGHT = 26
_ROW_HEIGHT = 28
_BADGE_SIZE = 22
_BADGE_CORNER = 6
_GAP_AFTER_TITLE = 10
_GAP_AFTER_SEP = 8
_CORNER_RADIUS = 14
_FADE_IN = 0.15
_FADE_OUT = 0.2


class LeaderAlertPanel:
    """Floating panel showing leader-key mappings."""

    def __init__(self) -> None:
        self._panel: object = None

    @property
    def is_visible(self) -> bool:
        return self._panel is not None

    def show(
        self,
        trigger_key: str,
        mappings: list[LeaderMapping],
        position: str | tuple = "center",
    ) -> None:
        """Create and display the leader alert. Must run on main thread."""
        from AppKit import (
            NSAnimationContext,
            NSBackingStoreBuffered,
            NSColor,
            NSEvent,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSPanel,
            NSScreen,
            NSStatusWindowLevel,
            NSTextAlignmentCenter,
            NSTextField,
            NSView,
            NSVisualEffectMaterialHUDWindow,
            NSVisualEffectView,
        )

        if self._panel is not None:
            self.close()

        num_rows = len(mappings)
        panel_height = (
            _PADDING
            + _TITLE_HEIGHT
            + _GAP_AFTER_TITLE
            + 1  # separator
            + _GAP_AFTER_SEP
            + num_rows * _ROW_HEIGHT
            + _PADDING
        )

        # --- Panel (borderless, transparent) ---
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, _PANEL_WIDTH, panel_height),
            0,
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

        # --- Vibrancy background ---
        vibrancy = NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, _PANEL_WIDTH, panel_height)
        )
        vibrancy.setMaterial_(NSVisualEffectMaterialHUDWindow)
        vibrancy.setState_(1)  # NSVisualEffectStateActive
        vibrancy.setWantsLayer_(True)
        vibrancy.layer().setCornerRadius_(_CORNER_RADIUS)
        vibrancy.layer().setMasksToBounds_(True)
        panel.contentView().addSubview_(vibrancy)

        # --- Title ---
        title_font = NSFont.systemFontOfSize_weight_(15.0, NSFontWeightSemibold)
        y_cursor = panel_height - _PADDING - _TITLE_HEIGHT
        title = NSTextField.labelWithString_(t("leader_alert.title", key=trigger_key))
        title.setFrame_(
            NSMakeRect(_PADDING, y_cursor, _PANEL_WIDTH - _PADDING * 2, _TITLE_HEIGHT)
        )
        title.setFont_(title_font)
        title.setTextColor_(NSColor.labelColor())
        title.setBackgroundColor_(NSColor.clearColor())
        title.setBezeled_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        vibrancy.addSubview_(title)

        # --- Separator ---
        y_cursor -= _GAP_AFTER_TITLE
        sep = NSView.alloc().initWithFrame_(
            NSMakeRect(_PADDING, y_cursor, _PANEL_WIDTH - _PADDING * 2, 1)
        )
        sep.setWantsLayer_(True)
        sep.layer().setBackgroundColor_(NSColor.separatorColor().CGColor())
        vibrancy.addSubview_(sep)
        y_cursor -= 1 + _GAP_AFTER_SEP

        # --- Mapping rows ---
        key_font = NSFont.monospacedSystemFontOfSize_weight_(12.0, NSFontWeightMedium)
        desc_font = NSFont.systemFontOfSize_weight_(14.0, 0.0)
        badge_bg_cg = NSColor.colorWithSRGBRed_green_blue_alpha_(
            0.5, 0.5, 0.5, 0.15
        ).CGColor()
        badge_x = _PADDING
        desc_x = _PADDING + _BADGE_SIZE + 12
        desc_width = _PANEL_WIDTH - desc_x - _PADDING

        for m in mappings:
            row_y = y_cursor - _ROW_HEIGHT
            badge_y = row_y + (_ROW_HEIGHT - _BADGE_SIZE) / 2

            # Badge background (rounded rect)
            badge = NSView.alloc().initWithFrame_(
                NSMakeRect(badge_x, badge_y, _BADGE_SIZE, _BADGE_SIZE)
            )
            badge.setWantsLayer_(True)
            badge.layer().setBackgroundColor_(badge_bg_cg)
            badge.layer().setCornerRadius_(_BADGE_CORNER)
            vibrancy.addSubview_(badge)

            # Badge key letter
            key_label = NSTextField.labelWithString_(m.key.upper())
            key_label.setFrame_(NSMakeRect(0, 0, _BADGE_SIZE, _BADGE_SIZE))
            key_label.setFont_(key_font)
            key_label.setAlignment_(NSTextAlignmentCenter)
            key_label.setTextColor_(NSColor.labelColor())
            key_label.setBackgroundColor_(NSColor.clearColor())
            key_label.setBezeled_(False)
            key_label.setEditable_(False)
            key_label.setSelectable_(False)
            badge.addSubview_(key_label)

            # Description
            desc_text = m.desc or m.app or m.exec_cmd or t("leader_alert.default_action")
            desc_label = NSTextField.labelWithString_(desc_text)
            desc_label.setFrame_(NSMakeRect(desc_x, row_y, desc_width, _ROW_HEIGHT))
            desc_label.setFont_(desc_font)
            desc_label.setTextColor_(NSColor.secondaryLabelColor())
            desc_label.setBackgroundColor_(NSColor.clearColor())
            desc_label.setBezeled_(False)
            desc_label.setEditable_(False)
            desc_label.setSelectable_(False)
            vibrancy.addSubview_(desc_label)

            y_cursor = row_y

        # --- Position ---
        screen = NSScreen.mainScreen()
        if screen:
            sf = screen.frame()
            x, y = self._calculate_origin(
                position, _PANEL_WIDTH, panel_height, sf, NSEvent,
            )
            panel.setFrameOrigin_((x, y))

        # --- Fade in ---
        panel.setAlphaValue_(0.0)
        panel.orderFrontRegardless()
        self._panel = panel

        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(_FADE_IN)
        panel.animator().setAlphaValue_(1.0)
        NSAnimationContext.endGrouping()

        logger.debug("Leader alert shown for %s", trigger_key)

    @staticmethod
    def _calculate_origin(position, pw, ph, sf, ns_event_cls):
        """Return ``(x, y)`` for the panel, clamped to screen bounds.

        Args:
            position: "center", "top", "bottom", "mouse", or (x%, y%).
            pw / ph: panel width / height.
            sf: screen frame (NSRect).
            ns_event_cls: ``NSEvent`` class (for mouseLocation).
        """
        sx, sy = sf.origin.x, sf.origin.y
        sw, sh = sf.size.width, sf.size.height

        if position == "top":
            x = sx + (sw - pw) / 2
            y = sy + sh - ph - 100
        elif position == "bottom":
            x = sx + (sw - pw) / 2
            y = sy + 100
        elif position == "mouse":
            loc = ns_event_cls.mouseLocation()
            x = loc.x - pw / 2
            y = loc.y - ph / 2
        elif isinstance(position, (tuple, list)) and len(position) == 2:
            px, py = float(position[0]), float(position[1])
            x = sx + sw * px - pw / 2
            y = sy + sh * py - ph / 2
        else:  # "center" or unknown
            x = sx + (sw - pw) / 2
            y = sy + (sh - ph) / 2

        # Clamp to screen bounds
        x = max(sx, min(x, sx + sw - pw))
        y = max(sy, min(y, sy + sh - ph))
        return x, y

    def close(self) -> None:
        """Fade out and close the panel. Must run on main thread."""
        if self._panel is None:
            return
        panel = self._panel
        self._panel = None  # mark closed immediately for is_visible

        from AppKit import NSAnimationContext

        def _on_fade_done():
            try:
                panel.orderOut_(None)
            except Exception:
                pass

        NSAnimationContext.beginGrouping()
        ctx = NSAnimationContext.currentContext()
        ctx.setDuration_(_FADE_OUT)
        ctx.setCompletionHandler_(_on_fade_done)
        panel.animator().setAlphaValue_(0.0)
        NSAnimationContext.endGrouping()

        logger.debug("Leader alert closed")
