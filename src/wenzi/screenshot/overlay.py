"""Full-screen region selection overlay for screenshot capture.

Creates a borderless NSWindow covering the screen, draws a dark mask over a
pre-captured screenshot, and lets the user select a region by:

1. **Window auto-detection** -- moving the mouse highlights the smallest
   window whose bounds contain the cursor.
2. **Manual drag** -- click and drag to draw a custom rectangle.

After selection the region can be adjusted (resize via handles, move by
dragging inside). Pressing Enter / double-clicking confirms; Esc cancels.

All PyObjC / Quartz imports are deferred so the module can be imported
(and its pure-logic helpers tested) without a running AppKit environment.
"""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Overlay mask opacity (rgba alpha for the darkening layer)
_MASK_ALPHA = 0.3

# Highlight / selection border
_HIGHLIGHT_COLOR_HEX = (0x18 / 255, 0x90 / 255, 0xFF / 255, 1.0)  # #1890ff
_HIGHLIGHT_BORDER_WIDTH = 1.0
_SELECTION_BORDER_WIDTH = 1.5

# Resize handles
_HANDLE_SIZE = 8.0  # side length of the square handle

# Dimension label
_LABEL_FONT_SIZE = 11.0
_LABEL_PADDING_H = 6.0
_LABEL_PADDING_V = 3.0
_LABEL_CORNER_RADIUS = 4.0
_LABEL_OFFSET_Y = 6.0  # gap between label and selection top edge


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class OverlayState(Enum):
    """Interaction states for the overlay."""
    IDLE = auto()
    DETECTING = auto()   # mouse moving, highlighting windows
    DRAGGING = auto()    # mouse down + drag for manual selection
    SELECTED = auto()    # selection made (can still adjust)
    ADJUSTING = auto()   # dragging a handle or moving selection


# ---------------------------------------------------------------------------
# Handle positions
# ---------------------------------------------------------------------------

class HandlePosition(Enum):
    """Eight possible resize handle positions."""
    TOP_LEFT = auto()
    TOP = auto()
    TOP_RIGHT = auto()
    RIGHT = auto()
    BOTTOM_RIGHT = auto()
    BOTTOM = auto()
    BOTTOM_LEFT = auto()
    LEFT = auto()


# ---------------------------------------------------------------------------
# Pure-logic helpers (testable without PyObjC)
# ---------------------------------------------------------------------------

def find_window_at_point(
    windows: List[Dict[str, Any]],
    px: float,
    py: float,
) -> Optional[Dict[str, Any]]:
    """Return the best-matching window containing the point *(px, py)*.

    *windows* is expected to be pre-sorted by ``capture.py``: higher layer
    first, smaller area first within the same layer.  The first window whose
    bounds contain the point is therefore the "smallest on-top" match.
    """
    for win in windows:
        b = win["bounds"]
        if (b["x"] <= px <= b["x"] + b["width"]
                and b["y"] <= py <= b["y"] + b["height"]):
            return win
    return None


def normalize_rect(
    x1: float, y1: float, x2: float, y2: float,
) -> Dict[str, float]:
    """Normalise two arbitrary corner points into a rect dict.

    Drag can go in any direction so *(x1, y1)* may be bottom-right.
    Returns ``{"x": ..., "y": ..., "width": ..., "height": ...}`` with
    positive width/height and (x, y) at the top-left (in screen coords
    where Y increases downward -- standard macOS CG coordinate space).
    """
    x = min(x1, x2)
    y = min(y1, y2)
    w = abs(x2 - x1)
    h = abs(y2 - y1)
    return {"x": x, "y": y, "width": w, "height": h}


def crop_rect_for_scale(
    rect: Dict[str, float],
    scale: float,
) -> Tuple[float, float, float, float]:
    """Return (x, y, w, h) in pixel coordinates for a given backing scale.

    On Retina displays the CGImage is 2x the screen-point dimensions, so we
    must multiply by the backing scale factor.
    """
    return (
        rect["x"] * scale,
        rect["y"] * scale,
        rect["width"] * scale,
        rect["height"] * scale,
    )


def handle_rects(
    rect: Dict[str, float],
) -> Dict[HandlePosition, Dict[str, float]]:
    """Return handle rectangles (centred on each edge / corner) for a selection.

    Each handle is an ``_HANDLE_SIZE x _HANDLE_SIZE`` square.
    """
    x, y, w, h = rect["x"], rect["y"], rect["width"], rect["height"]
    hs = _HANDLE_SIZE
    half = hs / 2

    cx = x + w / 2
    cy = y + h / 2

    return {
        HandlePosition.TOP_LEFT:     {"x": x - half,     "y": y - half,     "width": hs, "height": hs},
        HandlePosition.TOP:          {"x": cx - half,    "y": y - half,     "width": hs, "height": hs},
        HandlePosition.TOP_RIGHT:    {"x": x + w - half, "y": y - half,     "width": hs, "height": hs},
        HandlePosition.RIGHT:        {"x": x + w - half, "y": cy - half,    "width": hs, "height": hs},
        HandlePosition.BOTTOM_RIGHT: {"x": x + w - half, "y": y + h - half, "width": hs, "height": hs},
        HandlePosition.BOTTOM:       {"x": cx - half,    "y": y + h - half, "width": hs, "height": hs},
        HandlePosition.BOTTOM_LEFT:  {"x": x - half,     "y": y + h - half, "width": hs, "height": hs},
        HandlePosition.LEFT:         {"x": x - half,     "y": cy - half,    "width": hs, "height": hs},
    }


def hit_test_handles(
    rect: Dict[str, float],
    px: float,
    py: float,
) -> Optional[HandlePosition]:
    """Return the handle at *(px, py)* or ``None``."""
    for pos, hr in handle_rects(rect).items():
        if (hr["x"] <= px <= hr["x"] + hr["width"]
                and hr["y"] <= py <= hr["y"] + hr["height"]):
            return pos
    return None


def point_in_rect(rect: Dict[str, float], px: float, py: float) -> bool:
    """Return True if *(px, py)* lies inside *rect*."""
    return (rect["x"] <= px <= rect["x"] + rect["width"]
            and rect["y"] <= py <= rect["y"] + rect["height"])


def apply_handle_drag(
    rect: Dict[str, float],
    handle: HandlePosition,
    dx: float,
    dy: float,
) -> Dict[str, float]:
    """Return a new rect after dragging *handle* by *(dx, dy)*.

    Ensures width/height stay positive by swapping edges when necessary.
    """
    x, y, w, h = rect["x"], rect["y"], rect["width"], rect["height"]

    # Corners
    if handle in (HandlePosition.TOP_LEFT, HandlePosition.LEFT, HandlePosition.BOTTOM_LEFT):
        x += dx
        w -= dx
    if handle in (HandlePosition.TOP_RIGHT, HandlePosition.RIGHT, HandlePosition.BOTTOM_RIGHT):
        w += dx
    if handle in (HandlePosition.TOP_LEFT, HandlePosition.TOP, HandlePosition.TOP_RIGHT):
        y += dy
        h -= dy
    if handle in (HandlePosition.BOTTOM_LEFT, HandlePosition.BOTTOM, HandlePosition.BOTTOM_RIGHT):
        h += dy

    # Ensure positive dimensions
    if w < 0:
        x += w
        w = -w
    if h < 0:
        y += h
        h = -h

    return {"x": x, "y": y, "width": w, "height": h}


def move_rect(
    rect: Dict[str, float],
    dx: float,
    dy: float,
) -> Dict[str, float]:
    """Return a new rect shifted by *(dx, dy)*."""
    return {
        "x": rect["x"] + dx,
        "y": rect["y"] + dy,
        "width": rect["width"],
        "height": rect["height"],
    }


# ---------------------------------------------------------------------------
# ScreenshotOverlay — public API
# ---------------------------------------------------------------------------

class ScreenshotOverlay:
    """Full-screen overlay for interactive region selection.

    Parameters
    ----------
    screen_data:
        Dict from :func:`wenzi.screenshot.capture.capture_screen` containing
        ``"displays"`` (mapping display-id -> CGImage) and ``"windows"`` (list
        of window metadata dicts).
    """

    def __init__(self, screen_data: Dict[str, Any]) -> None:
        self._screen_data = screen_data
        self._displays: Dict[int, Any] = screen_data.get("displays", {})
        self._windows: List[Dict[str, Any]] = screen_data.get("windows", [])

        self._on_complete: Optional[Callable] = None
        self._on_cancel: Optional[Callable] = None

        # NSWindow / NSView references (created lazily on show())
        self._overlay_window: Any = None
        self._overlay_view: Any = None

        # Interaction state
        self._state: OverlayState = OverlayState.IDLE

        # Currently highlighted window (during DETECTING)
        self._highlighted_window: Optional[Dict[str, Any]] = None

        # Selection rectangle (screen coordinates)
        self._selection: Optional[Dict[str, float]] = None

        # Drag start point (screen coordinates)
        self._drag_start: Optional[Tuple[float, float]] = None

        # For ADJUSTING state
        self._active_handle: Optional[HandlePosition] = None
        self._adjust_start: Optional[Tuple[float, float]] = None
        self._adjust_original_rect: Optional[Dict[str, float]] = None

        # The full-screen CGImage for the main display
        self._screenshot_image: Any = None

        # Screen info
        self._screen_frame: Optional[Dict[str, float]] = None
        self._backing_scale: float = 2.0

    def show(self, on_complete: Callable, on_cancel: Callable) -> None:
        """Show the overlay.

        Parameters
        ----------
        on_complete:
            ``on_complete(region_rect, cropped_image)`` called with the
            selected region and the cropped CGImage.
        on_cancel:
            ``on_cancel()`` called when the user presses Esc.
        """
        self._on_complete = on_complete
        self._on_cancel = on_cancel
        self._state = OverlayState.DETECTING

        try:
            self._create_overlay_window()
        except Exception:
            logger.error("Failed to create screenshot overlay", exc_info=True)
            if self._on_cancel:
                self._on_cancel()

    def close(self) -> None:
        """Tear down all overlay windows and clean up."""
        try:
            if self._overlay_window is not None:
                self._overlay_window.orderOut_(None)
                self._overlay_window = None
            self._overlay_view = None
            self._screenshot_image = None
            self._state = OverlayState.IDLE
            logger.debug("Screenshot overlay closed")
        except Exception:
            logger.warning("Failed to close screenshot overlay", exc_info=True)

    # ------------------------------------------------------------------
    # Private — window creation
    # ------------------------------------------------------------------

    def _create_overlay_window(self) -> None:
        """Build the full-screen borderless overlay window."""
        from AppKit import (
            NSScreen,
            NSWindow,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
        )
        from Foundation import NSMakeRect
        import Quartz

        screen = NSScreen.mainScreen()
        frame = screen.frame()
        self._backing_scale = screen.backingScaleFactor()
        self._screen_frame = {
            "x": frame.origin.x,
            "y": frame.origin.y,
            "width": frame.size.width,
            "height": frame.size.height,
        }

        # Pick the CGImage for the main display
        if self._displays:
            # Use the first available display image
            self._screenshot_image = next(iter(self._displays.values()))

        # Create NSWindow
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(frame.origin.x, frame.origin.y,
                       frame.size.width, frame.size.height),
            0,  # NSBorderlessWindowMask
            2,  # NSBackingStoreBuffered
            False,
        )
        # CGShieldingWindowLevel() is a function, not a constant
        shielding_level = Quartz.CGShieldingWindowLevel()
        window.setLevel_(shielding_level)
        window.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
        window.setOpaque_(False)
        window.setHasShadow_(False)

        # Create the custom view
        view = _get_overlay_view_class().alloc().initWithFrame_(
            NSMakeRect(0, 0, frame.size.width, frame.size.height)
        )
        view._overlay = self  # back-reference for event handling

        window.setContentView_(view)
        window.makeKeyAndOrderFront_(None)
        window.makeFirstResponder_(view)

        self._overlay_window = window
        self._overlay_view = view

        logger.debug("Screenshot overlay shown (%.0fx%.0f, scale=%.1f)",
                     frame.size.width, frame.size.height, self._backing_scale)

    # ------------------------------------------------------------------
    # Event handling (called from the NSView subclass)
    # ------------------------------------------------------------------

    def _handle_mouse_moved(self, screen_x: float, screen_y: float) -> None:
        """Mouse move during DETECTING state -- highlight window under cursor."""
        if self._state not in (OverlayState.DETECTING, OverlayState.IDLE):
            return

        self._state = OverlayState.DETECTING
        matched = find_window_at_point(self._windows, screen_x, screen_y)
        if matched != self._highlighted_window:
            self._highlighted_window = matched
            self._request_redraw()

    def _handle_mouse_down(self, screen_x: float, screen_y: float) -> None:
        """Mouse button pressed."""
        if self._state == OverlayState.SELECTED:
            # Check if clicking a handle
            handle = hit_test_handles(self._selection, screen_x, screen_y)
            if handle is not None:
                self._state = OverlayState.ADJUSTING
                self._active_handle = handle
                self._adjust_start = (screen_x, screen_y)
                self._adjust_original_rect = dict(self._selection)
                return

            # Check if clicking inside the selection (to move)
            if point_in_rect(self._selection, screen_x, screen_y):
                self._state = OverlayState.ADJUSTING
                self._active_handle = None  # None means "moving"
                self._adjust_start = (screen_x, screen_y)
                self._adjust_original_rect = dict(self._selection)
                return

            # Clicking outside the selection starts a new drag
            self._selection = None

        if self._state == OverlayState.DETECTING:
            # If a window is highlighted, select it immediately
            if self._highlighted_window is not None:
                self._selection = dict(self._highlighted_window["bounds"])
                self._highlighted_window = None
                self._state = OverlayState.SELECTED
                self._request_redraw()
                return

        # Start a new drag selection
        self._state = OverlayState.DRAGGING
        self._drag_start = (screen_x, screen_y)
        self._selection = normalize_rect(screen_x, screen_y, screen_x, screen_y)
        self._request_redraw()

    def _handle_mouse_dragged(self, screen_x: float, screen_y: float) -> None:
        """Mouse moved while button is held."""
        if self._state == OverlayState.DRAGGING and self._drag_start is not None:
            self._selection = normalize_rect(
                self._drag_start[0], self._drag_start[1],
                screen_x, screen_y,
            )
            self._request_redraw()

        elif self._state == OverlayState.ADJUSTING and self._adjust_start is not None:
            dx = screen_x - self._adjust_start[0]
            dy = screen_y - self._adjust_start[1]
            if self._active_handle is not None:
                self._selection = apply_handle_drag(
                    self._adjust_original_rect, self._active_handle, dx, dy,
                )
            else:
                self._selection = move_rect(self._adjust_original_rect, dx, dy)
            self._request_redraw()

    def _handle_mouse_up(self, screen_x: float, screen_y: float) -> None:
        """Mouse button released."""
        if self._state == OverlayState.DRAGGING:
            if self._selection and self._selection["width"] > 2 and self._selection["height"] > 2:
                self._state = OverlayState.SELECTED
            else:
                # Too small, treat as a click — go back to detecting
                self._selection = None
                self._state = OverlayState.DETECTING
            self._drag_start = None
            self._request_redraw()

        elif self._state == OverlayState.ADJUSTING:
            self._state = OverlayState.SELECTED
            self._active_handle = None
            self._adjust_start = None
            self._adjust_original_rect = None
            self._request_redraw()

    def _handle_double_click(self, screen_x: float, screen_y: float) -> None:
        """Double-click inside selection confirms it."""
        if (self._state == OverlayState.SELECTED
                and self._selection
                and point_in_rect(self._selection, screen_x, screen_y)):
            self._confirm_selection()

    def _handle_key_down(self, keycode: int) -> None:
        """Handle keyboard events."""
        # Esc = keycode 53
        if keycode == 53:
            self._cancel()
        # Enter/Return = keycode 36, numpad enter = keycode 76
        elif keycode in (36, 76):
            if self._state == OverlayState.SELECTED and self._selection:
                self._confirm_selection()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _confirm_selection(self) -> None:
        """Crop the screenshot and call on_complete."""
        if not self._selection or not self._screenshot_image:
            return

        import Quartz

        rect = self._selection
        scale = self._backing_scale
        cx, cy, cw, ch = crop_rect_for_scale(rect, scale)
        crop_cg_rect = Quartz.CGRectMake(cx, cy, cw, ch)
        cropped = Quartz.CGImageCreateWithImageInRect(
            self._screenshot_image, crop_cg_rect,
        )

        region_rect = dict(rect)
        callback = self._on_complete
        self.close()
        if callback:
            callback(region_rect, cropped)

    def _cancel(self) -> None:
        """Cancel and close the overlay."""
        callback = self._on_cancel
        self.close()
        if callback:
            callback()

    def _request_redraw(self) -> None:
        """Ask the overlay view to redraw."""
        if self._overlay_view is not None:
            self._overlay_view.setNeedsDisplay_(True)


# ---------------------------------------------------------------------------
# NSView subclass (lazily defined to avoid PyObjC import at module level)
# ---------------------------------------------------------------------------

_OverlayViewClass: Any = None


def _get_overlay_view_class() -> Any:
    """Return the ScreenshotOverlayView class, creating it on first call."""
    global _OverlayViewClass
    if _OverlayViewClass is not None:
        return _OverlayViewClass

    from AppKit import NSColor, NSGraphicsContext, NSTrackingArea, NSView
    import Quartz

    # Tracking area options: mouseMoved + mouseEnteredAndExited + activeAlways
    _TRACKING_OPTIONS = 0x02 | 0x01 | 0x80  # NSTrackingMouseMoved | MouseEnteredAndExited | ActiveAlways

    class ScreenshotOverlayView(NSView):
        """Custom NSView that draws the overlay and handles all mouse/key events."""

        # The back-reference to ScreenshotOverlay is set as _overlay after init.

        def acceptsFirstResponder(self):
            return True

        def canBecomeKeyView(self):
            return True

        def updateTrackingAreas(self):
            """Install / refresh the tracking area for mouse-moved events."""
            NSView.updateTrackingAreas(self)
            # Remove old tracking areas
            for ta in self.trackingAreas():
                self.removeTrackingArea_(ta)
            ta = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(), _TRACKING_OPTIONS, self, None,
            )
            self.addTrackingArea_(ta)

        # -- Drawing --------------------------------------------------

        def drawRect_(self, dirty_rect):
            overlay = getattr(self, "_overlay", None)
            if overlay is None:
                return

            ctx = NSGraphicsContext.currentContext()
            if ctx is None:
                return
            cg = ctx.CGContext()

            bounds = self.bounds()
            bw = bounds.size.width
            bh = bounds.size.height

            # 1. Draw the screenshot image (if available)
            screenshot = overlay._screenshot_image
            if screenshot is not None:
                img_rect = Quartz.CGRectMake(0, 0, bw, bh)
                Quartz.CGContextDrawImage(cg, img_rect, screenshot)

            # 2. Draw the dark mask over everything
            Quartz.CGContextSetRGBFillColor(cg, 0, 0, 0, _MASK_ALPHA)
            Quartz.CGContextFillRect(cg, Quartz.CGRectMake(0, 0, bw, bh))

            # 3. Draw highlight / selection (punch through the mask)
            highlight_rect = None
            state = overlay._state

            if state == OverlayState.DETECTING and overlay._highlighted_window:
                highlight_rect = overlay._highlighted_window["bounds"]
            elif state in (OverlayState.DRAGGING, OverlayState.SELECTED,
                           OverlayState.ADJUSTING) and overlay._selection:
                highlight_rect = overlay._selection

            if highlight_rect is not None:
                hr = highlight_rect
                # Convert from screen-top-left coords to NSView coords
                # NSView has origin at bottom-left
                ns_rect = self._screen_to_view_rect(hr, bh)

                # Punch through: redraw the screenshot in this region
                if screenshot is not None:
                    Quartz.CGContextSaveGState(cg)
                    Quartz.CGContextClipToRect(cg, ns_rect)
                    # Redraw full image (clipped to selection)
                    img_rect = Quartz.CGRectMake(0, 0, bw, bh)
                    Quartz.CGContextDrawImage(cg, img_rect, screenshot)
                    Quartz.CGContextRestoreGState(cg)

                # Border
                r, g, b, a = _HIGHLIGHT_COLOR_HEX
                Quartz.CGContextSetRGBStrokeColor(cg, r, g, b, a)
                if state == OverlayState.DETECTING:
                    Quartz.CGContextSetLineWidth(cg, _HIGHLIGHT_BORDER_WIDTH)
                else:
                    Quartz.CGContextSetLineWidth(cg, _SELECTION_BORDER_WIDTH)
                Quartz.CGContextStrokeRect(cg, ns_rect)

                # Dimension label (during drag or selected)
                if state in (OverlayState.DRAGGING, OverlayState.SELECTED,
                             OverlayState.ADJUSTING):
                    self._draw_dimension_label(cg, hr, ns_rect, bh)

                # Resize handles (when selected or adjusting)
                if state in (OverlayState.SELECTED, OverlayState.ADJUSTING):
                    self._draw_handles(cg, hr, bh)

        def _screen_to_view_rect(self, rect, view_height):
            """Convert screen-coordinate rect (Y-down) to NSView rect (Y-up)."""
            x = rect["x"]
            y = view_height - rect["y"] - rect["height"]
            return Quartz.CGRectMake(x, y, rect["width"], rect["height"])

        def _draw_dimension_label(self, cg, screen_rect, ns_rect, view_height):
            """Draw a 'W x H' label above the selection."""
            w = int(round(screen_rect["width"]))
            h = int(round(screen_rect["height"]))
            label_text = f"{w} \u00d7 {h}"

            # Position the label above the selection (in view coords)
            label_x = ns_rect.origin.x
            label_y = ns_rect.origin.y + ns_rect.size.height + _LABEL_OFFSET_Y

            # Draw background pill
            from AppKit import NSFont, NSString
            from Foundation import NSDictionary

            font = NSFont.monospacedDigitSystemFontOfSize_weight_(_LABEL_FONT_SIZE, 0.0)
            attrs = NSDictionary.dictionaryWithObjectsAndKeys_(
                font, "NSFont",
                NSColor.whiteColor(), "NSColor",
                None,
            )
            ns_str = NSString.stringWithString_(label_text)
            text_size = ns_str.sizeWithAttributes_(attrs)

            pill_w = text_size.width + 2 * _LABEL_PADDING_H
            pill_h = text_size.height + 2 * _LABEL_PADDING_V
            pill_rect = Quartz.CGRectMake(label_x, label_y, pill_w, pill_h)

            # Rounded rect background
            Quartz.CGContextSaveGState(cg)
            Quartz.CGContextSetRGBFillColor(cg, 0, 0, 0, 0.7)
            _cg_add_rounded_rect(cg, pill_rect, _LABEL_CORNER_RADIUS)
            Quartz.CGContextFillPath(cg)
            Quartz.CGContextRestoreGState(cg)

            # Draw text
            text_point = (label_x + _LABEL_PADDING_H,
                          label_y + _LABEL_PADDING_V)
            ns_str.drawAtPoint_withAttributes_(text_point, attrs)

        def _draw_handles(self, cg, screen_rect, view_height):
            """Draw the 8 resize handles."""
            handles = handle_rects(screen_rect)
            r, g, b, a = _HIGHLIGHT_COLOR_HEX
            Quartz.CGContextSetRGBFillColor(cg, r, g, b, a)
            for _pos, hr in handles.items():
                ns_r = self._screen_to_view_rect(hr, view_height)
                Quartz.CGContextFillRect(cg, ns_r)

        # -- Mouse events ---------------------------------------------

        def mouseDown_(self, event):
            p = self._event_to_screen(event)
            overlay = getattr(self, "_overlay", None)
            if overlay:
                # Check for double-click
                if event.clickCount() >= 2:
                    overlay._handle_double_click(p[0], p[1])
                else:
                    overlay._handle_mouse_down(p[0], p[1])

        def mouseDragged_(self, event):
            p = self._event_to_screen(event)
            overlay = getattr(self, "_overlay", None)
            if overlay:
                overlay._handle_mouse_dragged(p[0], p[1])

        def mouseUp_(self, event):
            p = self._event_to_screen(event)
            overlay = getattr(self, "_overlay", None)
            if overlay:
                overlay._handle_mouse_up(p[0], p[1])

        def mouseMoved_(self, event):
            p = self._event_to_screen(event)
            overlay = getattr(self, "_overlay", None)
            if overlay:
                overlay._handle_mouse_moved(p[0], p[1])

        def keyDown_(self, event):
            overlay = getattr(self, "_overlay", None)
            if overlay:
                overlay._handle_key_down(event.keyCode())

        # -- Coordinate conversion ------------------------------------

        def _event_to_screen(self, event):
            """Convert an NSEvent location to screen coordinates (Y-down).

            NSEvent locationInWindow is in window coordinates (Y-up from
            bottom-left).  We convert to screen coordinates where Y increases
            downward (matching CGWindowList coordinate space).
            """
            loc = event.locationInWindow()
            view_height = self.bounds().size.height
            sx = loc.x
            sy = view_height - loc.y  # flip Y
            return (sx, sy)

    _OverlayViewClass = ScreenshotOverlayView
    return _OverlayViewClass


def _cg_add_rounded_rect(cg_context: Any, rect: Any, radius: float) -> None:
    """Add a rounded rectangle path to a CG context."""
    import Quartz
    x = rect.origin.x
    y = rect.origin.y
    w = rect.size.width
    h = rect.size.height
    r = min(radius, w / 2, h / 2)

    Quartz.CGContextMoveToPoint(cg_context, x + r, y)
    Quartz.CGContextAddLineToPoint(cg_context, x + w - r, y)
    Quartz.CGContextAddArcToPoint(cg_context, x + w, y, x + w, y + r, r)
    Quartz.CGContextAddLineToPoint(cg_context, x + w, y + h - r)
    Quartz.CGContextAddArcToPoint(cg_context, x + w, y + h, x + w - r, y + h, r)
    Quartz.CGContextAddLineToPoint(cg_context, x + r, y + h)
    Quartz.CGContextAddArcToPoint(cg_context, x, y + h, x, y + h - r, r)
    Quartz.CGContextAddLineToPoint(cg_context, x, y + r)
    Quartz.CGContextAddArcToPoint(cg_context, x, y, x + r, y, r)
    Quartz.CGContextClosePath(cg_context)
