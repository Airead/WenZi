"""Window Manager — vim-style window snapping with cycling.

Binds Ctrl+Cmd + h/j/k/l/r/w/e to snap, center, and move
the focused window across screens.

Pressing the same direction key cycles through 1/2 → 2/3 → 1/3 of
the screen, mimicking Hammerspoon's miro-windows-manager behavior.
"""

_CYCLE = [1 / 2, 2 / 3, 1 / 3]
_TOLERANCE = 10  # pixels


def _current_screen(wz):
    """Return (frame, screen) for the focused window."""
    frame = wz.window.focused_frame()
    if frame is None:
        return None, None
    screens = wz.window.screens()
    if not screens:
        return frame, None
    cx = frame["x"] + frame["w"] / 2
    cy = frame["y"] + frame["h"] / 2
    for s in screens:
        if s["x"] <= cx <= s["x"] + s["w"] and s["y"] <= cy <= s["y"] + s["h"]:
            return frame, s
    return frame, screens[0]


def _close(a, b):
    return abs(a - b) < _TOLERANCE


def _next_fraction(current_frac):
    for i, f in enumerate(_CYCLE):
        if abs(current_frac - f) < 0.05:
            return _CYCLE[(i + 1) % len(_CYCLE)]
    return _CYCLE[0]


def _snap_left(wz):
    frame, screen = _current_screen(wz)
    if frame is None or screen is None:
        return
    sx, sy, sw, sh = screen["x"], screen["y"], screen["w"], screen["h"]
    if _close(frame["x"], sx):
        frac = _next_fraction(frame["w"] / sw)
    else:
        frac = _CYCLE[0]
    wz.window.set_frame(sx, sy, sw * frac, sh)


def _snap_right(wz):
    frame, screen = _current_screen(wz)
    if frame is None or screen is None:
        return
    sx, sy, sw, sh = screen["x"], screen["y"], screen["w"], screen["h"]
    right = sx + sw
    if _close(frame["x"] + frame["w"], right):
        frac = _next_fraction(frame["w"] / sw)
    else:
        frac = _CYCLE[0]
    wz.window.set_frame(right - sw * frac, sy, sw * frac, sh)


def _snap_top(wz):
    frame, screen = _current_screen(wz)
    if frame is None or screen is None:
        return
    sx, sy, sw, sh = screen["x"], screen["y"], screen["w"], screen["h"]
    if _close(frame["y"], sy):
        frac = _next_fraction(frame["h"] / sh)
    else:
        frac = _CYCLE[0]
    wz.window.set_frame(sx, sy, sw, sh * frac)


def _snap_bottom(wz):
    frame, screen = _current_screen(wz)
    if frame is None or screen is None:
        return
    sx, sy, sw, sh = screen["x"], screen["y"], screen["w"], screen["h"]
    bottom = sy + sh
    if _close(frame["y"] + frame["h"], bottom):
        frac = _next_fraction(frame["h"] / sh)
    else:
        frac = _CYCLE[0]
    wz.window.set_frame(sx, bottom - sh * frac, sw, sh * frac)


def _snap_full(wz):
    frame, screen = _current_screen(wz)
    if frame is None or screen is None:
        return
    sx, sy, sw, sh = screen["x"], screen["y"], screen["w"], screen["h"]
    wz.window.set_frame(sx, sy, sw, sh)


def setup(wz):
    """Entry point called by the ScriptEngine plugin loader."""
    wz.hotkey.bind("ctrl+cmd+h", lambda: _snap_left(wz))
    wz.hotkey.bind("ctrl+cmd+l", lambda: _snap_right(wz))
    wz.hotkey.bind("ctrl+cmd+k", lambda: _snap_top(wz))
    wz.hotkey.bind("ctrl+cmd+j", lambda: _snap_bottom(wz))
    wz.hotkey.bind("ctrl+cmd+r", lambda: _snap_full(wz))
    wz.hotkey.bind("ctrl+cmd+w", lambda: wz.window.center())
    wz.hotkey.bind("ctrl+cmd+e", lambda: wz.window.move_to_screen("next"))
