"""Tests for wenzi.screenshot.overlay.

Exercises the pure-logic helpers and state machine without PyObjC.  All
AppKit / Quartz APIs are mocked.
"""

from __future__ import annotations

import sys
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from wenzi.screenshot.overlay import (
    HandlePosition,
    OverlayState,
    apply_handle_drag,
    crop_rect_for_scale,
    find_window_at_point,
    handle_rects,
    hit_test_handles,
    move_rect,
    normalize_rect,
    point_in_rect,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rect(x: float = 0, y: float = 0, w: float = 100, h: float = 100) -> Dict[str, float]:
    return {"x": x, "y": y, "width": w, "height": h}


def _win(
    x: float = 0,
    y: float = 0,
    w: float = 200,
    h: float = 150,
    layer: int = 0,
    title: str = "Win",
) -> Dict[str, Any]:
    return {
        "bounds": {"x": x, "y": y, "width": w, "height": h},
        "title": title,
        "app": "App",
        "layer": layer,
        "window_id": 1,
    }


# ---------------------------------------------------------------------------
# find_window_at_point
# ---------------------------------------------------------------------------

class TestFindWindowAtPoint:
    """Window hit-testing logic."""

    def test_point_inside_single_window(self):
        wins = [_win(x=10, y=10, w=100, h=100)]
        result = find_window_at_point(wins, 50, 50)
        assert result is wins[0]

    def test_point_outside_all_windows(self):
        wins = [_win(x=10, y=10, w=100, h=100)]
        assert find_window_at_point(wins, 200, 200) is None

    def test_first_match_wins_already_sorted(self):
        """The list is pre-sorted (higher layer first, smaller area first).
        The first containing window should be returned."""
        small = _win(x=50, y=50, w=50, h=50, layer=0, title="Small")
        big = _win(x=0, y=0, w=200, h=200, layer=0, title="Big")
        # small comes first because it has smaller area (matches capture.py sort)
        wins = [small, big]
        result = find_window_at_point(wins, 60, 60)
        assert result["title"] == "Small"

    def test_point_on_window_edge_included(self):
        wins = [_win(x=10, y=10, w=100, h=100)]
        # Point on the right edge (10 + 100 = 110)
        assert find_window_at_point(wins, 110, 50) is not None
        # Point on the bottom edge
        assert find_window_at_point(wins, 50, 110) is not None
        # Point on the top-left corner
        assert find_window_at_point(wins, 10, 10) is not None

    def test_empty_window_list(self):
        assert find_window_at_point([], 50, 50) is None

    def test_higher_layer_matched_first(self):
        """Windows on a higher layer should appear first in the list and be matched."""
        foreground = _win(x=0, y=0, w=200, h=200, layer=5, title="Front")
        background = _win(x=0, y=0, w=200, h=200, layer=0, title="Back")
        wins = [foreground, background]  # sorted: higher layer first
        result = find_window_at_point(wins, 100, 100)
        assert result["title"] == "Front"

    def test_point_just_outside_window(self):
        wins = [_win(x=10, y=10, w=100, h=100)]
        # Just outside right edge (111 > 110)
        assert find_window_at_point(wins, 111, 50) is None
        # Just outside bottom edge
        assert find_window_at_point(wins, 50, 111) is None


# ---------------------------------------------------------------------------
# normalize_rect
# ---------------------------------------------------------------------------

class TestNormalizeRect:
    """Drag rectangle normalisation (handles any drag direction)."""

    def test_top_left_to_bottom_right(self):
        r = normalize_rect(10, 20, 100, 200)
        assert r == {"x": 10, "y": 20, "width": 90, "height": 180}

    def test_bottom_right_to_top_left(self):
        r = normalize_rect(100, 200, 10, 20)
        assert r == {"x": 10, "y": 20, "width": 90, "height": 180}

    def test_top_right_to_bottom_left(self):
        r = normalize_rect(100, 20, 10, 200)
        assert r == {"x": 10, "y": 20, "width": 90, "height": 180}

    def test_bottom_left_to_top_right(self):
        r = normalize_rect(10, 200, 100, 20)
        assert r == {"x": 10, "y": 20, "width": 90, "height": 180}

    def test_zero_size_drag(self):
        r = normalize_rect(50, 50, 50, 50)
        assert r == {"x": 50, "y": 50, "width": 0, "height": 0}

    def test_negative_coordinates(self):
        r = normalize_rect(-10, -20, 10, 20)
        assert r == {"x": -10, "y": -20, "width": 20, "height": 40}


# ---------------------------------------------------------------------------
# crop_rect_for_scale
# ---------------------------------------------------------------------------

class TestCropRectForScale:
    """Retina backing-scale multiplication."""

    def test_scale_1x(self):
        r = _make_rect(10, 20, 100, 200)
        assert crop_rect_for_scale(r, 1.0) == (10.0, 20.0, 100.0, 200.0)

    def test_scale_2x(self):
        r = _make_rect(10, 20, 100, 200)
        assert crop_rect_for_scale(r, 2.0) == (20.0, 40.0, 200.0, 400.0)

    def test_scale_3x(self):
        r = _make_rect(10, 20, 100, 200)
        result = crop_rect_for_scale(r, 3.0)
        assert result == pytest.approx((30.0, 60.0, 300.0, 600.0))

    def test_fractional_scale(self):
        r = _make_rect(10, 20, 100, 200)
        result = crop_rect_for_scale(r, 1.5)
        assert result == pytest.approx((15.0, 30.0, 150.0, 300.0))


# ---------------------------------------------------------------------------
# handle_rects
# ---------------------------------------------------------------------------

class TestHandleRects:
    """Resize handle placement."""

    def test_returns_eight_handles(self):
        r = _make_rect(100, 100, 200, 150)
        handles = handle_rects(r)
        assert len(handles) == 8
        assert set(handles.keys()) == set(HandlePosition)

    def test_corner_handles_centred_on_corners(self):
        r = _make_rect(100, 100, 200, 150)
        handles = handle_rects(r)
        hs = 8.0  # _HANDLE_SIZE
        half = hs / 2

        tl = handles[HandlePosition.TOP_LEFT]
        assert tl["x"] == pytest.approx(100 - half)
        assert tl["y"] == pytest.approx(100 - half)

        br = handles[HandlePosition.BOTTOM_RIGHT]
        assert br["x"] == pytest.approx(300 - half)
        assert br["y"] == pytest.approx(250 - half)

    def test_edge_handles_centred_on_edges(self):
        r = _make_rect(100, 100, 200, 150)
        handles = handle_rects(r)
        half = 8.0 / 2

        top = handles[HandlePosition.TOP]
        assert top["x"] == pytest.approx(200 - half)  # cx = 100+100
        assert top["y"] == pytest.approx(100 - half)

        right = handles[HandlePosition.RIGHT]
        assert right["x"] == pytest.approx(300 - half)
        assert right["y"] == pytest.approx(175 - half)  # cy = 100+75


# ---------------------------------------------------------------------------
# hit_test_handles
# ---------------------------------------------------------------------------

class TestHitTestHandles:
    """Handle hit detection."""

    def test_hit_corner_handle(self):
        r = _make_rect(100, 100, 200, 150)
        # Top-left handle centre is at (100, 100)
        result = hit_test_handles(r, 100, 100)
        assert result == HandlePosition.TOP_LEFT

    def test_miss_all_handles(self):
        r = _make_rect(100, 100, 200, 150)
        # Centre of rect — far from any handle
        assert hit_test_handles(r, 200, 175) is None

    def test_hit_edge_handle(self):
        r = _make_rect(100, 100, 200, 150)
        # Bottom centre handle is at (200, 250)
        result = hit_test_handles(r, 200, 250)
        assert result == HandlePosition.BOTTOM


# ---------------------------------------------------------------------------
# point_in_rect
# ---------------------------------------------------------------------------

class TestPointInRect:
    def test_inside(self):
        assert point_in_rect(_make_rect(10, 20, 100, 100), 50, 50) is True

    def test_outside(self):
        assert point_in_rect(_make_rect(10, 20, 100, 100), 200, 200) is False

    def test_on_edge(self):
        assert point_in_rect(_make_rect(10, 20, 100, 100), 110, 120) is True

    def test_on_corner(self):
        assert point_in_rect(_make_rect(10, 20, 100, 100), 10, 20) is True


# ---------------------------------------------------------------------------
# apply_handle_drag
# ---------------------------------------------------------------------------

class TestApplyHandleDrag:
    """Resize via handle dragging."""

    def test_drag_right_edge(self):
        r = _make_rect(100, 100, 200, 150)
        result = apply_handle_drag(r, HandlePosition.RIGHT, 50, 0)
        assert result["width"] == 250
        assert result["x"] == 100  # left unchanged

    def test_drag_left_edge(self):
        r = _make_rect(100, 100, 200, 150)
        result = apply_handle_drag(r, HandlePosition.LEFT, -30, 0)
        assert result["x"] == 70
        assert result["width"] == 230

    def test_drag_top_edge(self):
        r = _make_rect(100, 100, 200, 150)
        result = apply_handle_drag(r, HandlePosition.TOP, 0, -20)
        assert result["y"] == 80
        assert result["height"] == 170

    def test_drag_bottom_edge(self):
        r = _make_rect(100, 100, 200, 150)
        result = apply_handle_drag(r, HandlePosition.BOTTOM, 0, 40)
        assert result["height"] == 190

    def test_drag_corner(self):
        r = _make_rect(100, 100, 200, 150)
        result = apply_handle_drag(r, HandlePosition.BOTTOM_RIGHT, 30, 20)
        assert result["width"] == 230
        assert result["height"] == 170
        assert result["x"] == 100
        assert result["y"] == 100

    def test_drag_past_opposite_edge_flips(self):
        """Dragging left edge past right edge should flip (positive width)."""
        r = _make_rect(100, 100, 50, 50)
        result = apply_handle_drag(r, HandlePosition.LEFT, 100, 0)
        # Left moves by +100 -> x=200, w=-50 -> flipped to x=150, w=50
        assert result["width"] >= 0
        assert result["height"] >= 0

    def test_drag_preserves_unaffected_edges(self):
        r = _make_rect(100, 100, 200, 150)
        result = apply_handle_drag(r, HandlePosition.TOP_LEFT, -10, -10)
        # Bottom-right corner should stay put
        assert result["x"] + result["width"] == 300
        assert result["y"] + result["height"] == 250


# ---------------------------------------------------------------------------
# move_rect
# ---------------------------------------------------------------------------

class TestMoveRect:
    def test_positive_offset(self):
        r = _make_rect(100, 100, 200, 150)
        result = move_rect(r, 30, 20)
        assert result == {"x": 130, "y": 120, "width": 200, "height": 150}

    def test_negative_offset(self):
        r = _make_rect(100, 100, 200, 150)
        result = move_rect(r, -50, -30)
        assert result == {"x": 50, "y": 70, "width": 200, "height": 150}

    def test_zero_offset(self):
        r = _make_rect(100, 100, 200, 150)
        result = move_rect(r, 0, 0)
        assert result == r

    def test_does_not_mutate_original(self):
        r = _make_rect(100, 100, 200, 150)
        original = dict(r)
        move_rect(r, 50, 50)
        assert r == original


# ---------------------------------------------------------------------------
# State machine transitions (ScreenshotOverlay)
# ---------------------------------------------------------------------------

class TestScreenshotOverlayStateMachine:
    """Test state transitions without PyObjC by calling event handlers directly."""

    def _make_overlay(self):
        from wenzi.screenshot.overlay import ScreenshotOverlay
        screen_data = {
            "displays": {1: MagicMock()},
            "windows": [
                _win(x=50, y=50, w=200, h=150, layer=0, title="Win1"),
                _win(x=0, y=0, w=800, h=600, layer=0, title="Win2"),
            ],
        }
        overlay = ScreenshotOverlay(screen_data)
        # Set up internals without calling show() (which needs PyObjC)
        overlay._state = OverlayState.DETECTING
        overlay._screenshot_image = screen_data["displays"][1]
        overlay._backing_scale = 2.0
        overlay._overlay_view = MagicMock()  # mock view for redraw requests
        return overlay

    def test_initial_state_is_idle(self):
        from wenzi.screenshot.overlay import ScreenshotOverlay
        overlay = ScreenshotOverlay({"displays": {}, "windows": []})
        assert overlay._state == OverlayState.IDLE

    def test_mouse_move_transitions_to_detecting(self):
        overlay = self._make_overlay()
        overlay._handle_mouse_moved(100, 100)
        assert overlay._state == OverlayState.DETECTING
        assert overlay._highlighted_window is not None
        assert overlay._highlighted_window["title"] == "Win1"

    def test_mouse_move_no_window_clears_highlight(self):
        overlay = self._make_overlay()
        overlay._handle_mouse_moved(900, 900)  # outside all windows
        assert overlay._highlighted_window is None

    def test_click_on_highlighted_window_selects(self):
        overlay = self._make_overlay()
        overlay._handle_mouse_moved(100, 100)  # highlight Win1
        overlay._handle_mouse_down(100, 100)
        assert overlay._state == OverlayState.SELECTED
        assert overlay._selection is not None
        assert overlay._selection["x"] == 50  # Win1 bounds

    def test_drag_starts_manual_selection(self):
        overlay = self._make_overlay()
        # Move to an area with no small window match — but the big window
        # covers it, so clicking will select it. Let's move to no-window area.
        overlay._highlighted_window = None
        overlay._handle_mouse_down(10, 10)
        assert overlay._state == OverlayState.DRAGGING

    def test_drag_updates_selection(self):
        overlay = self._make_overlay()
        overlay._highlighted_window = None
        overlay._handle_mouse_down(10, 10)
        overlay._handle_mouse_dragged(100, 100)
        assert overlay._selection == {"x": 10, "y": 10, "width": 90, "height": 90}

    def test_drag_release_transitions_to_selected(self):
        overlay = self._make_overlay()
        overlay._highlighted_window = None
        overlay._handle_mouse_down(10, 10)
        overlay._handle_mouse_dragged(100, 100)
        overlay._handle_mouse_up(100, 100)
        assert overlay._state == OverlayState.SELECTED

    def test_tiny_drag_returns_to_detecting(self):
        """A drag of 1-2 pixels should be treated as a click, not a selection."""
        overlay = self._make_overlay()
        overlay._highlighted_window = None
        overlay._handle_mouse_down(10, 10)
        overlay._handle_mouse_dragged(11, 11)
        overlay._handle_mouse_up(11, 11)
        assert overlay._state == OverlayState.DETECTING
        assert overlay._selection is None

    def test_esc_cancels(self):
        overlay = self._make_overlay()
        callback = MagicMock()
        overlay._on_cancel = callback
        overlay._on_complete = MagicMock()
        # Stub close to avoid PyObjC
        overlay._overlay_window = None
        overlay._handle_key_down(53)  # Esc
        callback.assert_called_once()

    def test_enter_confirms_selection(self, monkeypatch):
        overlay = self._make_overlay()
        overlay._state = OverlayState.SELECTED
        overlay._selection = _make_rect(50, 50, 200, 150)

        callback = MagicMock()
        overlay._on_complete = callback
        overlay._overlay_window = None

        mock_quartz = MagicMock()
        mock_quartz.CGImageCreateWithImageInRect.return_value = MagicMock()
        monkeypatch.setitem(sys.modules, "Quartz", mock_quartz)
        overlay._handle_key_down(36)  # Enter

        callback.assert_called_once()
        args = callback.call_args[0]
        assert args[0] == {"x": 50, "y": 50, "width": 200, "height": 150}

    def test_double_click_confirms(self, monkeypatch):
        overlay = self._make_overlay()
        overlay._state = OverlayState.SELECTED
        overlay._selection = _make_rect(50, 50, 200, 150)

        callback = MagicMock()
        overlay._on_complete = callback
        overlay._overlay_window = None

        mock_quartz = MagicMock()
        mock_quartz.CGImageCreateWithImageInRect.return_value = MagicMock()
        monkeypatch.setitem(sys.modules, "Quartz", mock_quartz)
        overlay._handle_double_click(100, 100)  # inside selection

        callback.assert_called_once()

    def test_double_click_outside_does_not_confirm(self):
        overlay = self._make_overlay()
        overlay._state = OverlayState.SELECTED
        overlay._selection = _make_rect(50, 50, 200, 150)

        callback = MagicMock()
        overlay._on_complete = callback
        overlay._handle_double_click(900, 900)  # outside selection
        callback.assert_not_called()

    def test_handle_drag_transitions_to_adjusting(self):
        overlay = self._make_overlay()
        overlay._state = OverlayState.SELECTED
        overlay._selection = _make_rect(100, 100, 200, 150)

        # Click on top-left handle (at 100, 100)
        overlay._handle_mouse_down(100, 100)
        assert overlay._state == OverlayState.ADJUSTING
        assert overlay._active_handle == HandlePosition.TOP_LEFT

    def test_move_selection_by_dragging_inside(self):
        overlay = self._make_overlay()
        overlay._state = OverlayState.SELECTED
        overlay._selection = _make_rect(100, 100, 200, 150)

        # Click inside selection
        overlay._handle_mouse_down(200, 175)
        assert overlay._state == OverlayState.ADJUSTING
        assert overlay._active_handle is None  # None = moving

        # Drag to move
        overlay._handle_mouse_dragged(230, 195)
        assert overlay._selection["x"] == 130
        assert overlay._selection["y"] == 120

    def test_adjusting_mouse_up_returns_to_selected(self):
        overlay = self._make_overlay()
        overlay._state = OverlayState.SELECTED
        overlay._selection = _make_rect(100, 100, 200, 150)

        overlay._handle_mouse_down(200, 175)  # inside -> adjusting
        overlay._handle_mouse_up(230, 195)
        assert overlay._state == OverlayState.SELECTED

    def test_click_outside_selection_starts_new_drag(self):
        overlay = self._make_overlay()
        overlay._state = OverlayState.SELECTED
        overlay._selection = _make_rect(100, 100, 200, 150)

        # Click outside the selection and outside any handle
        overlay._handle_mouse_down(500, 500)
        assert overlay._state == OverlayState.DRAGGING
        assert overlay._drag_start == (500, 500)

    def test_numpad_enter_confirms(self, monkeypatch):
        overlay = self._make_overlay()
        overlay._state = OverlayState.SELECTED
        overlay._selection = _make_rect(50, 50, 200, 150)
        callback = MagicMock()
        overlay._on_complete = callback
        overlay._overlay_window = None

        mock_quartz = MagicMock()
        mock_quartz.CGImageCreateWithImageInRect.return_value = MagicMock()
        monkeypatch.setitem(sys.modules, "Quartz", mock_quartz)
        overlay._handle_key_down(76)  # numpad Enter

        callback.assert_called_once()

    def test_enter_without_selection_does_nothing(self):
        overlay = self._make_overlay()
        overlay._state = OverlayState.DETECTING
        overlay._selection = None
        callback = MagicMock()
        overlay._on_complete = callback
        overlay._handle_key_down(36)
        callback.assert_not_called()

    def test_mouse_moved_during_dragging_ignored(self):
        """Mouse move events during DRAGGING state should not change highlight."""
        overlay = self._make_overlay()
        overlay._highlighted_window = None
        overlay._handle_mouse_down(10, 10)
        assert overlay._state == OverlayState.DRAGGING
        overlay._handle_mouse_moved(100, 100)
        # State should stay DRAGGING, not switch to DETECTING
        assert overlay._state == OverlayState.DRAGGING

    def test_mouse_moved_during_selected_ignored(self):
        """Mouse move events during SELECTED state should not change state."""
        overlay = self._make_overlay()
        overlay._state = OverlayState.SELECTED
        overlay._selection = _make_rect(100, 100, 200, 150)
        overlay._handle_mouse_moved(200, 200)
        assert overlay._state == OverlayState.SELECTED


# ---------------------------------------------------------------------------
# Crop rect calculation with scale
# ---------------------------------------------------------------------------

class TestCropIntegration:
    """Integration test: selection -> crop rect with scale factor."""

    def test_retina_crop(self):
        """A 100x100 selection at (50,50) on a 2x Retina display
        should produce a crop rect of (100,100, 200,200) in pixels."""
        selection = _make_rect(50, 50, 100, 100)
        scale = 2.0
        cx, cy, cw, ch = crop_rect_for_scale(selection, scale)
        assert (cx, cy, cw, ch) == (100, 100, 200, 200)

    def test_non_retina_crop(self):
        selection = _make_rect(50, 50, 100, 100)
        scale = 1.0
        cx, cy, cw, ch = crop_rect_for_scale(selection, scale)
        assert (cx, cy, cw, ch) == (50, 50, 100, 100)
