"""Floating recording indicator panel with audio level visualization."""

from __future__ import annotations

import logging
import math
import time
from typing import Optional

logger = logging.getLogger(__name__)

# EMA smoothing factor for audio level updates
_EMA_ALPHA = 0.3

# Panel dimensions
_PANEL_WIDTH = 120
_PANEL_WIDTH_WITH_MODE = 220
_PANEL_HEIGHT = 50
_PANEL_HEIGHT_WITH_LABEL = 68

# Animation refresh interval in seconds (~20Hz)
_REFRESH_INTERVAL = 0.05

# Number of audio level bars
_NUM_BARS = 5

# Bar visual parameters
_BAR_WIDTH = 6
_BAR_GAP = 4
_BAR_MAX_HEIGHT = 30
_BAR_MIN_HEIGHT = 3
_BAR_CORNER_RADIUS = 2

# Pulse dot parameters
_DOT_BASE_RADIUS = 5.0
_DOT_PULSE_AMPLITUDE = 1.5
_DOT_PULSE_SPEED = 3.0

# Background
_BG_CORNER_RADIUS = 12


class RecordingIndicatorView:
    """Custom NSView that draws the recording indicator."""

    _view: object = None

    def __init__(self, device_name: Optional[str] = None) -> None:
        self._level: float = 0.0
        self._start_time: float = 0.0
        self._view = None
        self._device_name: Optional[str] = device_name
        self._label_attrs: Optional[dict] = None  # cached for draw loop
        # Whether the recorder is actually capturing audio
        self._recording_active: bool = False
        # Cached dynamic colors (created once, adapt to light/dark automatically)
        self._bg_color = None
        self._dot_color = None
        self._dot_color_inactive = None
        self._bar_color = None
        self._bar_color_inactive = None
        # Mode display fields
        self._mode_name: Optional[str] = None
        self._mode_nav: tuple = (False, False)  # (can_prev, can_next)
        self._mode_attrs: Optional[dict] = None  # cached for draw loop
        self._arrow_attrs: Optional[dict] = None  # cached for draw loop
        self._mode_ns_str = None  # cached NSString for mode name
        self._left_arrow_ns_str = None  # cached NSString for ◁
        self._right_arrow_ns_str = None  # cached NSString for ▷

    def create_view(self, width: int, height: int) -> object:
        """Create and return the NSView instance."""
        from Foundation import NSMakeRect

        rect = NSMakeRect(0, 0, width, height)

        # Use a block-based approach with a wrapper view
        view = _IndicatorNSView.alloc().initWithFrame_(rect)
        view._indicator = self
        self._view = view
        self._start_time = time.monotonic()
        return view

    def set_level(self, level: float) -> None:
        self._level = level

    @staticmethod
    def _dynamic_color(light_rgba, dark_rgba):
        """Create a dynamic color that adapts to light/dark mode."""
        from AppKit import NSColor

        def _provider(appearance):
            name = appearance.bestMatchFromAppearancesWithNames_(
                ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]
            )
            rgba = dark_rgba if name and "Dark" in str(name) else light_rgba
            return NSColor.colorWithSRGBRed_green_blue_alpha_(*rgba)

        return NSColor.colorWithName_dynamicProvider_(None, _provider)

    def draw(self, rect: object) -> None:
        """Draw the indicator contents."""
        from AppKit import (
            NSBezierPath,
            NSFont,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSParagraphStyleAttributeName,
            NSMutableParagraphStyle,
        )
        from Foundation import NSMakeRect, NSString

        width = rect.size.width

        # When a device label is shown, the animation area is the top
        # portion and the label sits at the bottom.
        label_height = (_PANEL_HEIGHT_WITH_LABEL - _PANEL_HEIGHT) if self._device_name else 0
        anim_center_y = label_height + _PANEL_HEIGHT / 2.0

        # Semi-transparent rounded background (adapts to light/dark mode)
        if self._bg_color is None:
            self._bg_color = self._dynamic_color(
                light_rgba=(0.95, 0.95, 0.95, 0.85),
                dark_rgba=(0.15, 0.15, 0.15, 0.85),
            )
        self._bg_color.setFill()
        bg_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, _BG_CORNER_RADIUS, _BG_CORNER_RADIUS
        )
        bg_path.fill()

        elapsed = time.monotonic() - self._start_time

        # Pulsing dot on the left (gray when waiting, red when recording)
        dot_x = 18.0
        dot_y = anim_center_y
        pulse = math.sin(elapsed * _DOT_PULSE_SPEED) * _DOT_PULSE_AMPLITUDE
        dot_radius = _DOT_BASE_RADIUS + pulse

        if self._recording_active:
            if self._dot_color is None:
                self._dot_color = self._dynamic_color(
                    light_rgba=(0.85, 0.15, 0.15, 1.0),
                    dark_rgba=(0.95, 0.25, 0.25, 1.0),
                )
            self._dot_color.setFill()
        else:
            if self._dot_color_inactive is None:
                self._dot_color_inactive = self._dynamic_color(
                    light_rgba=(0.45, 0.45, 0.45, 1.0),
                    dark_rgba=(0.55, 0.55, 0.55, 1.0),
                )
            self._dot_color_inactive.setFill()
        dot_rect = NSMakeRect(
            dot_x - dot_radius, dot_y - dot_radius,
            dot_radius * 2, dot_radius * 2,
        )
        dot_path = NSBezierPath.bezierPathWithOvalInRect_(dot_rect)
        dot_path.fill()

        # Audio level bars on the right
        bars_start_x = 38.0
        bar_y_base = anim_center_y - _BAR_MAX_HEIGHT / 2.0

        if self._recording_active:
            if self._bar_color is None:
                self._bar_color = self._dynamic_color(
                    light_rgba=(0.2, 0.65, 0.2, 0.9),
                    dark_rgba=(0.4, 0.9, 0.4, 0.9),
                )
            self._bar_color.setFill()
        else:
            if self._bar_color_inactive is None:
                self._bar_color_inactive = self._dynamic_color(
                    light_rgba=(0.55, 0.55, 0.55, 0.9),
                    dark_rgba=(0.5, 0.5, 0.5, 0.9),
                )
            self._bar_color_inactive.setFill()

        level = self._level
        for i in range(_NUM_BARS):
            # Each bar has a slightly different phase for visual variety
            phase = i * 0.6
            wave = (math.sin(elapsed * 5.0 + phase) + 1.0) / 2.0
            # Base idle animation + level-driven height
            idle = wave * 0.15
            bar_factor = idle + level * (0.85 + wave * 0.15)
            bar_height = max(_BAR_MIN_HEIGHT, bar_factor * _BAR_MAX_HEIGHT)

            bar_x = bars_start_x + i * (_BAR_WIDTH + _BAR_GAP)
            bar_y = bar_y_base + (_BAR_MAX_HEIGHT - bar_height) / 2.0

            bar_rect = NSMakeRect(bar_x, bar_y, _BAR_WIDTH, bar_height)
            bar_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar_rect, _BAR_CORNER_RADIUS, _BAR_CORNER_RADIUS
            )
            bar_path.fill()

        # Device name label at the bottom (single line, truncate tail)
        if self._device_name:
            if self._label_attrs is None:
                label_color = self._dynamic_color(
                    light_rgba=(0.3, 0.3, 0.3, 0.8),
                    dark_rgba=(0.7, 0.7, 0.7, 0.8),
                )
                para = NSMutableParagraphStyle.alloc().init()
                para.setAlignment_(1)  # NSTextAlignmentCenter
                para.setLineBreakMode_(4)  # NSLineBreakByTruncatingTail
                self._label_attrs = {
                    NSFontAttributeName: NSFont.systemFontOfSize_(9),
                    NSForegroundColorAttributeName: label_color,
                    NSParagraphStyleAttributeName: para,
                }
                self._label_ns_str = NSString.stringWithString_(self._device_name)
            label_rect = NSMakeRect(4, 4, width - 8, label_height)
            self._label_ns_str.drawInRect_withAttributes_(
                label_rect, self._label_attrs,
            )

        # Mode name with navigation arrows (right of audio bars)
        if self._mode_name:
            if self._mode_attrs is None:
                mode_color = self._dynamic_color(
                    light_rgba=(0.1, 0.1, 0.1, 0.9),
                    dark_rgba=(0.9, 0.9, 0.9, 0.9),
                )
                mode_para = NSMutableParagraphStyle.alloc().init()
                mode_para.setAlignment_(1)  # NSTextAlignmentCenter
                mode_para.setLineBreakMode_(4)  # NSLineBreakByTruncatingTail
                self._mode_attrs = {
                    NSFontAttributeName: NSFont.systemFontOfSize_(12),
                    NSForegroundColorAttributeName: mode_color,
                    NSParagraphStyleAttributeName: mode_para,
                }
                arrow_color = self._dynamic_color(
                    light_rgba=(0.4, 0.4, 0.4, 0.7),
                    dark_rgba=(0.6, 0.6, 0.6, 0.7),
                )
                arrow_para = NSMutableParagraphStyle.alloc().init()
                arrow_para.setAlignment_(1)
                self._arrow_attrs = {
                    NSFontAttributeName: NSFont.systemFontOfSize_(11),
                    NSForegroundColorAttributeName: arrow_color,
                    NSParagraphStyleAttributeName: arrow_para,
                }

            can_prev, can_next = self._mode_nav
            mode_x = bars_start_x + _NUM_BARS * (_BAR_WIDTH + _BAR_GAP) + 4
            mode_area_w = width - mode_x - 6

            # Draw left arrow
            if can_prev:
                if self._left_arrow_ns_str is None:
                    self._left_arrow_ns_str = NSString.stringWithString_("\u25C1")
                arrow_rect = NSMakeRect(mode_x, anim_center_y - 8, 14, 16)
                self._left_arrow_ns_str.drawInRect_withAttributes_(
                    arrow_rect, self._arrow_attrs
                )

            # Draw mode name (centered in the remaining space)
            name_x = mode_x + (16 if can_prev else 2)
            name_w = mode_area_w - (16 if can_prev else 2) - (16 if can_next else 2)
            if self._mode_ns_str is None:
                self._mode_ns_str = NSString.stringWithString_(self._mode_name)
            name_rect = NSMakeRect(name_x, anim_center_y - 9, name_w, 18)
            self._mode_ns_str.drawInRect_withAttributes_(name_rect, self._mode_attrs)

            # Draw right arrow
            if can_next:
                if self._right_arrow_ns_str is None:
                    self._right_arrow_ns_str = NSString.stringWithString_("\u25B7")
                arrow_rect = NSMakeRect(
                    mode_x + mode_area_w - 14, anim_center_y - 8, 14, 16
                )
                self._right_arrow_ns_str.drawInRect_withAttributes_(
                    arrow_rect, self._arrow_attrs
                )


# PyObjC subclass for custom drawing
try:
    from AppKit import NSView
    import objc

    class _IndicatorNSView(NSView):
        _indicator = objc.ivar()

        def drawRect_(self, rect):
            if self._indicator:
                self._indicator.draw(rect)

        def isOpaque(self):
            return False

        def refresh_(self, timer):
            """Called by NSTimer to trigger a redraw."""
            self.setNeedsDisplay_(True)

except Exception:
    _IndicatorNSView = None


class RecordingIndicatorPanel:
    """Manages a floating panel that shows recording status with audio visualization."""

    def __init__(self) -> None:
        self._panel: object = None
        self._timer: object = None
        self._indicator_view: Optional[RecordingIndicatorView] = None
        self._smoothed_level: float = 0.0
        self._enabled: bool = True
        self._show_device_name: bool = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
        if not value:
            self.hide()

    @property
    def show_device_name(self) -> bool:
        return self._show_device_name

    @show_device_name.setter
    def show_device_name(self, value: bool) -> None:
        self._show_device_name = value

    def show(self, device_name: Optional[str] = None) -> None:
        """Create and show the floating indicator panel."""
        if not self._enabled:
            return

        try:
            from AppKit import (
                NSColor,
                NSPanel,
                NSScreen,
                NSStatusWindowLevel,
            )
            from Foundation import NSMakeRect, NSTimer

            if self._panel is not None:
                self.hide()

            self._smoothed_level = 0.0
            self._indicator_view = RecordingIndicatorView(device_name=device_name)

            panel_height = _PANEL_HEIGHT_WITH_LABEL if device_name else _PANEL_HEIGHT

            # Create borderless panel
            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, _PANEL_WIDTH, panel_height),
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

            # Position at center of main screen
            screen = NSScreen.mainScreen()
            if screen:
                screen_frame = screen.visibleFrame()
                x = screen_frame.origin.x + (screen_frame.size.width - _PANEL_WIDTH) / 2
                y = screen_frame.origin.y + (screen_frame.size.height - panel_height) / 2
                panel.setFrameOrigin_((x, y))

            # Set content view
            content_view = self._indicator_view.create_view(_PANEL_WIDTH, panel_height)
            panel.setContentView_(content_view)

            panel.orderFront_(None)
            self._panel = panel

            # Start refresh timer
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                _REFRESH_INTERVAL,
                content_view,
                b"refresh:",
                None,
                True,
            )

            logger.debug("Recording indicator shown")
        except Exception:
            logger.error("Failed to show recording indicator", exc_info=True)

    def set_recording_active(self) -> None:
        """Mark that the recorder is actually capturing audio.

        Switches the indicator from grayscale (waiting) to color (recording).
        """
        if self._indicator_view is not None:
            self._indicator_view._recording_active = True

    def _clear_view_backref(self) -> None:
        """Clear the _indicator back-reference on the NSView to break the cycle."""
        if (
            self._indicator_view
            and hasattr(self._indicator_view, "_view")
            and self._indicator_view._view
        ):
            try:
                self._indicator_view._view._indicator = None
            except Exception:
                pass

    def hide(self) -> None:
        """Hide and clean up the indicator panel."""
        try:
            if self._timer is not None:
                self._timer.invalidate()
                self._timer = None

            self._clear_view_backref()

            if self._panel is not None:
                self._panel.orderOut_(None)
                self._panel = None

            self._indicator_view = None
            self._smoothed_level = 0.0
            logger.debug("Recording indicator hidden")
        except Exception as e:
            logger.warning("Failed to hide recording indicator: %s", e)

    def _rebuild_content_view(self, new_width: int, new_height: int) -> None:
        """Resize the panel, recreate the content view, re-center, and restart the timer.

        Shared helper for update_mode() and update_device_name().
        """
        from AppKit import NSScreen
        from Foundation import NSTimer

        self._clear_view_backref()
        content_view = self._indicator_view.create_view(new_width, new_height)
        self._panel.setContentSize_((float(new_width), float(new_height)))
        self._panel.setContentView_(content_view)

        # Re-center on screen
        screen = NSScreen.mainScreen()
        if screen:
            sf = screen.visibleFrame()
            x = sf.origin.x + (sf.size.width - new_width) / 2
            y = sf.origin.y + (sf.size.height - new_height) / 2
            self._panel.setFrameOrigin_((x, y))

        # Restart refresh timer with new content view
        if self._timer is not None:
            self._timer.invalidate()
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            _REFRESH_INTERVAL,
            content_view,
            b"refresh:",
            None,
            True,
        )

    def update_mode(self, name: str, can_prev: bool, can_next: bool) -> None:
        """Update the mode label and nav arrow state on the indicator.

        Widens the panel to _PANEL_WIDTH_WITH_MODE if not already wide.
        """
        if self._indicator_view is None:
            return

        view = self._indicator_view
        nav = (can_prev, can_next)
        if view._mode_name == name and view._mode_nav == nav:
            return  # nothing changed

        # Invalidate cached NSString when mode name changes
        if view._mode_name != name:
            view._mode_ns_str = None
        view._mode_name = name
        view._mode_nav = nav

        if self._panel is not None:
            try:
                current_width = self._panel.frame().size.width
                if current_width < _PANEL_WIDTH_WITH_MODE:
                    current_height = int(self._panel.frame().size.height)
                    self._rebuild_content_view(
                        _PANEL_WIDTH_WITH_MODE, current_height
                    )
            except Exception:
                logger.debug("Failed to resize panel for mode display", exc_info=True)

    def clear_mode(self) -> None:
        """Remove the mode label and shrink back to default width."""
        if self._indicator_view is not None:
            self._indicator_view._mode_name = None
            self._indicator_view._mode_nav = (False, False)
            self._indicator_view._mode_ns_str = None

    def update_device_name(self, device_name: str) -> None:
        """Update the device name label after the panel is already shown.

        Resizes the panel from _PANEL_HEIGHT to _PANEL_HEIGHT_WITH_LABEL
        and recreates the content view so the label area is included.
        """
        if self._panel is None or self._indicator_view is None:
            return
        if self._indicator_view._device_name == device_name:
            return

        try:
            self._indicator_view._device_name = device_name
            # Reset cached label attrs so they are rebuilt on next draw
            self._indicator_view._label_attrs = None

            current_width = int(self._panel.frame().size.width)
            self._rebuild_content_view(current_width, _PANEL_HEIGHT_WITH_LABEL)

            logger.debug("Recording indicator updated with device: %s", device_name)
        except Exception:
            logger.debug("Failed to update recording indicator device name", exc_info=True)

    @property
    def current_frame(self) -> object | None:
        """Return the current panel frame rect, or None if not visible."""
        if self._panel is not None:
            return self._panel.frame()
        return None

    def animate_out(self, completion: callable = None) -> None:
        """Fade out the indicator panel, then clean up and call completion.

        Stops the refresh timer immediately, animates alpha to 0 over ~200ms,
        then calls orderOut and completion callback.
        """
        if self._panel is None:
            if completion:
                completion()
            return

        # Stop refresh timer immediately
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

        try:
            from AppKit import NSAnimationContext

            panel = self._panel

            def _on_complete():
                panel.orderOut_(None)
                self._clear_view_backref()
                self._panel = None
                self._indicator_view = None
                self._smoothed_level = 0.0
                logger.debug("Recording indicator animated out")
                if completion:
                    completion()

            NSAnimationContext.beginGrouping()
            ctx = NSAnimationContext.currentContext()
            ctx.setDuration_(0.2)
            ctx.setCompletionHandler_(_on_complete)
            panel.animator().setAlphaValue_(0.0)
            NSAnimationContext.endGrouping()
        except Exception:
            logger.error("Failed to animate indicator out", exc_info=True)
            self.hide()
            if completion:
                completion()

    def update_level(self, level: float) -> None:
        """Update the displayed audio level with EMA smoothing."""
        self._smoothed_level = (
            _EMA_ALPHA * level + (1 - _EMA_ALPHA) * self._smoothed_level
        )
        if self._indicator_view is not None:
            self._indicator_view.set_level(self._smoothed_level)
