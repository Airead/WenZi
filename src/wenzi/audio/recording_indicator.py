"""Floating recording indicator panel with audio waveform visualization."""

from __future__ import annotations

import logging
import math
import time

logger = logging.getLogger(__name__)

# Asymmetric EMA smoothing: fast attack, slower release
_EMA_ATTACK = 0.6
_EMA_RELEASE = 0.25

# Panel dimensions
_PANEL_WIDTH = 226
_PANEL_HEIGHT = 36
_LABEL_HEIGHT = 17  # height added for subtitle row

# Animation refresh interval in seconds (~20Hz)
_REFRESH_INTERVAL = 0.05

# Waveform visual parameters
_WAVE_POINTS = 50  # sample points for smooth curve
_WAVE_WIDTH = 180.0  # horizontal extent
_WAVE_MAX_AMP = 9.6  # max amplitude from centre line
_WAVE_LINE_W = 2.4  # primary wave stroke width
_WAVE_LINE_W2 = 1.8  # secondary wave stroke width

# Status dot parameters
_DOT_RADIUS = 5.4

# Font
_FONT_WEIGHT_MEDIUM = 0.23  # NSFontWeightMedium

# Background
_BG_CORNER_RADIUS = 12


class RecordingIndicatorView:
    """Custom NSView that draws the recording indicator."""

    _view: object = None

    def __init__(self) -> None:
        self._level: float = 0.0
        self._start_time: float = 0.0
        self._view = None
        self._subtitle_attrs: dict | None = None  # cached for draw loop
        self._subtitle_ns_str = None  # cached NSString for subtitle
        # Whether the recorder is actually capturing audio
        self._recording_active: bool = False
        # Cached dynamic colors (created once, adapt to light/dark automatically)
        self._dot_color = None
        self._dot_color_inactive = None
        self._wave_color = None
        self._wave_color_inactive = None
        self._wave_strokes: dict = {}  # (active, wave_idx) -> cached NSColor
        # Subtitle text (combined mode + device, built by panel)
        self._subtitle: str | None = None

    def create_view(self, width: int, height: int) -> object:
        """Create and return the NSView instance."""
        from Foundation import NSMakeRect

        rect = NSMakeRect(0, 0, width, height)

        view = _IndicatorNSView.alloc().initWithFrame_(rect)
        view._indicator = self
        self._view = view
        if self._start_time == 0.0:
            self._start_time = time.monotonic()
        return view

    def set_level(self, level: float) -> None:
        self._level = level

    @staticmethod
    def _dynamic_color(light_rgba, dark_rgba):
        """Create a dynamic color that adapts to light/dark mode."""
        from AppKit import NSColor

        def _provider(appearance):
            name = appearance.bestMatchFromAppearancesWithNames_(["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"])
            rgba = dark_rgba if name and "Dark" in str(name) else light_rgba
            return NSColor.colorWithSRGBRed_green_blue_alpha_(*rgba)

        return NSColor.colorWithName_dynamicProvider_(None, _provider)

    def draw(self, rect: object) -> None:
        """Draw the indicator contents.

        Background is provided by NSGlassEffectView — this method only draws
        the pulsing dot, waveform, and optional subtitle label.
        """
        from AppKit import (
            NSBezierPath,
            NSColor,
            NSFont,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSMutableParagraphStyle,
            NSParagraphStyleAttributeName,
        )
        from Foundation import NSMakeRect, NSString

        # Subtitle sits at the bottom; animation area is above it.
        sub_height = _LABEL_HEIGHT if self._subtitle else 0
        anim_center_y = sub_height + _PANEL_HEIGHT / 2.0

        elapsed = time.monotonic() - self._start_time

        # ── Status dot (left) — red when recording, grey when waiting ───
        dot_x = 18.0
        dot_y = anim_center_y

        if self._recording_active:
            if self._dot_color is None:
                self._dot_color = NSColor.systemRedColor()
            self._dot_color.setFill()
        else:
            if self._dot_color_inactive is None:
                self._dot_color_inactive = self._dynamic_color(
                    light_rgba=(0.45, 0.45, 0.45, 1.0),
                    dark_rgba=(0.55, 0.55, 0.55, 1.0),
                )
            self._dot_color_inactive.setFill()

        dot_rect = NSMakeRect(
            dot_x - _DOT_RADIUS,
            dot_y - _DOT_RADIUS,
            _DOT_RADIUS * 2,
            _DOT_RADIUS * 2,
        )
        NSBezierPath.bezierPathWithOvalInRect_(dot_rect).fill()

        # ── Audio waveform (centre-right) ───────────────────────────────
        wave_cx = 123.6
        half_w = _WAVE_WIDTH / 2.0
        level = self._level

        for wave_idx in range(2):
            freq = 2.5 + wave_idx * 1.2
            phase = elapsed * 8.0 + wave_idx * 2.0

            if self._recording_active:
                level_amp = level * _WAVE_MAX_AMP * (1.0 - wave_idx * 0.3)
                idle_amp = 1.0 * (1.0 - wave_idx * 0.5)
            else:
                level_amp = 0.0
                idle_amp = 2.0 * (1.0 - wave_idx * 0.5)

            breath = 0.5 + 0.5 * math.sin(elapsed * 3.0 + wave_idx)
            total_amp = level_amp + idle_amp * breath

            path = NSBezierPath.alloc().init()
            path.setLineWidth_(_WAVE_LINE_W if wave_idx == 0 else _WAVE_LINE_W2)
            path.setLineCapStyle_(1)  # NSLineCapStyleRound
            path.setLineJoinStyle_(1)  # NSLineJoinStyleRound

            for i in range(_WAVE_POINTS + 1):
                t = i / _WAVE_POINTS
                x = wave_cx - half_w + t * _WAVE_WIDTH
                envelope = math.sin(t * math.pi)  # taper at edges
                y = anim_center_y + math.sin(t * freq * math.pi * 2 + phase) * total_amp * envelope
                if i == 0:
                    path.moveToPoint_((x, y))
                else:
                    path.lineToPoint_((x, y))

            stroke_key = (self._recording_active, wave_idx)
            stroke = self._wave_strokes.get(stroke_key)
            if stroke is None:
                if self._recording_active:
                    if self._wave_color is None:
                        self._wave_color = self._dynamic_color(
                            light_rgba=(0.15, 0.55, 0.95, 1.0),
                            dark_rgba=(0.35, 0.7, 1.0, 1.0),
                        )
                    w_alpha = 0.9 if wave_idx == 0 else 0.35
                    stroke = self._wave_color.colorWithAlphaComponent_(w_alpha)
                else:
                    if self._wave_color_inactive is None:
                        self._wave_color_inactive = self._dynamic_color(
                            light_rgba=(0.55, 0.55, 0.55, 0.9),
                            dark_rgba=(0.5, 0.5, 0.5, 0.9),
                        )
                    w_alpha = 0.7 if wave_idx == 0 else 0.25
                    stroke = self._wave_color_inactive.colorWithAlphaComponent_(w_alpha)
                self._wave_strokes[stroke_key] = stroke
            stroke.setStroke()

            path.stroke()

        # ── Subtitle label (bottom) ─────────────────────────────────────
        if self._subtitle:
            if self._subtitle_attrs is None:
                para = NSMutableParagraphStyle.alloc().init()
                para.setAlignment_(1)  # NSTextAlignmentCenter
                para.setLineBreakMode_(4)  # NSLineBreakByTruncatingTail
                self._subtitle_attrs = {
                    NSFontAttributeName: NSFont.systemFontOfSize_weight_(10.8, _FONT_WEIGHT_MEDIUM),
                    NSForegroundColorAttributeName: NSColor.labelColor(),
                    NSParagraphStyleAttributeName: para,
                }
            if self._subtitle_ns_str is None:
                self._subtitle_ns_str = NSString.stringWithString_(self._subtitle)
            label_rect = NSMakeRect(33.6, 6, 180, _LABEL_HEIGHT)
            self._subtitle_ns_str.drawInRect_withAttributes_(
                label_rect,
                self._subtitle_attrs,
            )


# PyObjC subclass for custom drawing
try:
    import objc
    from AppKit import NSView

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


def _build_subtitle(mode_name: str | None, device_name: str | None) -> str | None:
    """Combine mode and device into a single subtitle string."""
    if mode_name and device_name:
        return f"{mode_name} · {device_name}"
    return mode_name or device_name


class RecordingIndicatorPanel:
    """Manages a floating panel that shows recording status with audio visualization."""

    def __init__(self) -> None:
        self._panel: object = None
        self._timer: object = None
        self._indicator_view: RecordingIndicatorView | None = None
        self._smoothed_level: float = 0.0
        self._enabled: bool = True
        self._show_device_name: bool = False
        # Stored values for subtitle rebuilding
        self._mode_name: str | None = None
        self._device_name: str | None = None

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

    @staticmethod
    def _make_glass_view(width: int, height: int):
        """Create an NSGlassEffectView with Liquid Glass appearance."""
        from AppKit import NSGlassEffectView
        from Foundation import NSMakeRect

        from wenzi.ui_helpers import configure_glass_appearance

        glass = NSGlassEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        glass.setCornerRadius_(_BG_CORNER_RADIUS)
        glass.setWantsLayer_(True)
        glass.layer().setMasksToBounds_(True)
        configure_glass_appearance(glass)
        return glass

    @staticmethod
    def _decorate_glass(glass, width, height):
        """Add outline for Liquid Glass edge definition."""
        from AppKit import NSView
        from Foundation import NSMakeRect

        from wenzi.ui_helpers import dynamic_color

        # Crisp outline for edge definition
        ol_color = dynamic_color((1.0, 1.0, 1.0, 0.30), (1.0, 1.0, 1.0, 0.16))
        outline = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        outline.setWantsLayer_(True)
        outline.layer().setCornerRadius_(_BG_CORNER_RADIUS)
        outline.layer().setBorderWidth_(1.0)
        outline.layer().setBorderColor_(ol_color.CGColor())
        glass.addSubview_(outline)

    def _panel_height(self) -> int:
        """Compute current panel height based on whether a subtitle is shown."""
        has_sub = _build_subtitle(self._mode_name, self._device_name) is not None
        return _PANEL_HEIGHT + (_LABEL_HEIGHT if has_sub else 0)

    def show(self, device_name: str | None = None, mode_name: str | None = None) -> None:
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
            self._mode_name = mode_name
            self._device_name = device_name
            self._indicator_view = RecordingIndicatorView()
            self._update_subtitle()

            panel_height = self._panel_height()

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

            self._panel = panel

            # Position at centre of main screen
            screen = NSScreen.mainScreen()
            if screen:
                sf = screen.visibleFrame()
                x = sf.origin.x + (sf.size.width - _PANEL_WIDTH) / 2.0
                y = sf.origin.y + (sf.size.height - panel_height) / 2.0
                panel.setFrameOrigin_((x, y))

            # Liquid Glass background + indicator drawing view
            glass = self._make_glass_view(_PANEL_WIDTH, panel_height)
            indicator = self._indicator_view.create_view(_PANEL_WIDTH, panel_height)
            glass.setContentView_(indicator)
            self._decorate_glass(glass, _PANEL_WIDTH, panel_height)
            panel.setContentView_(glass)

            panel.orderFront_(None)

            # Start refresh timer targeting the indicator subview
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                _REFRESH_INTERVAL,
                indicator,
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
        if self._indicator_view and hasattr(self._indicator_view, "_view") and self._indicator_view._view:
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
                from wenzi.ui_helpers import release_panel_surfaces

                release_panel_surfaces(self._panel)
                self._panel.orderOut_(None)
                self._panel = None

            self._indicator_view = None
            self._smoothed_level = 0.0
            self._mode_name = None
            self._device_name = None
            logger.debug("Recording indicator hidden")
        except Exception as e:
            logger.warning("Failed to hide recording indicator: %s", e)

    def _update_subtitle(self) -> None:
        """Rebuild subtitle text on the indicator view from mode + device."""
        if self._indicator_view is None:
            return
        new_sub = _build_subtitle(self._mode_name, self._device_name)
        if self._indicator_view._subtitle == new_sub:
            return
        self._indicator_view._subtitle = new_sub
        self._indicator_view._subtitle_ns_str = None
        self._indicator_view._subtitle_attrs = None

    def _rebuild_panel(self) -> None:
        """Resize the panel, recreate content views, reposition, and restart timer."""
        if self._panel is None or self._indicator_view is None:
            return

        from Foundation import NSTimer

        self._clear_view_backref()

        new_height = self._panel_height()

        from wenzi.ui_helpers import release_panel_surfaces

        release_panel_surfaces(self._panel)
        glass = self._make_glass_view(_PANEL_WIDTH, new_height)
        indicator = self._indicator_view.create_view(_PANEL_WIDTH, new_height)
        glass.setContentView_(indicator)
        self._decorate_glass(glass, _PANEL_WIDTH, new_height)

        self._panel.setContentSize_((float(_PANEL_WIDTH), float(new_height)))
        self._panel.setContentView_(glass)

        # Re-centre on screen
        from AppKit import NSScreen

        screen = NSScreen.mainScreen()
        if screen:
            sf = screen.visibleFrame()
            x = sf.origin.x + (sf.size.width - _PANEL_WIDTH) / 2.0
            y = sf.origin.y + (sf.size.height - new_height) / 2.0
            self._panel.setFrameOrigin_((x, y))

        # Restart refresh timer with new indicator view
        if self._timer is not None:
            self._timer.invalidate()
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            _REFRESH_INTERVAL,
            indicator,
            b"refresh:",
            None,
            True,
        )

    def update_mode(self, name: str) -> None:
        """Update the mode label shown in the subtitle."""
        if self._indicator_view is None:
            return
        if self._mode_name == name:
            return

        self._mode_name = name
        old_sub = self._indicator_view._subtitle
        self._update_subtitle()
        new_sub = self._indicator_view._subtitle

        # Resize panel if subtitle appeared/disappeared
        if (old_sub is None) != (new_sub is None) and self._panel is not None:
            try:
                self._rebuild_panel()
            except Exception:
                logger.debug("Failed to resize panel for subtitle", exc_info=True)

    def clear_mode(self) -> None:
        """Remove the mode label from the subtitle."""
        if self._mode_name is None:
            return
        self._mode_name = None
        if self._indicator_view is not None:
            old_sub = self._indicator_view._subtitle
            self._update_subtitle()
            new_sub = self._indicator_view._subtitle
            if (old_sub is None) != (new_sub is None) and self._panel is not None:
                try:
                    self._rebuild_panel()
                except Exception:
                    logger.debug("Failed to resize panel after clear_mode", exc_info=True)

    def update_device_name(self, device_name: str) -> None:
        """Update the device name shown in the subtitle."""
        if self._panel is None or self._indicator_view is None:
            return
        if self._device_name == device_name:
            return

        self._device_name = device_name
        old_sub = self._indicator_view._subtitle
        self._update_subtitle()
        new_sub = self._indicator_view._subtitle

        if (old_sub is None) != (new_sub is None):
            try:
                self._rebuild_panel()
            except Exception:
                logger.debug("Failed to resize panel for subtitle", exc_info=True)

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
                self._mode_name = None
                self._device_name = None
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
        """Update the displayed audio level with asymmetric EMA smoothing.

        Uses a faster attack (rising) and slower release (falling) for snappy
        response without jitter.
        """
        alpha = _EMA_ATTACK if level > self._smoothed_level else _EMA_RELEASE
        self._smoothed_level = alpha * level + (1 - alpha) * self._smoothed_level
        if self._indicator_view is not None:
            self._indicator_view.set_level(self._smoothed_level)
