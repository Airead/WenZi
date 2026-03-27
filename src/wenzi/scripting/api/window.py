"""Window management API — move, resize, snap, list, focus, and close windows."""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _unzoom_if_needed(win):
    """Un-zoom (un-maximize) the window if it is currently zoomed.

    Some apps (e.g. Chrome) ignore AXPosition/AXSize changes while the
    window is in the zoomed state.  Clearing AXIsZoomed first allows the
    subsequent position/size calls to take effect.
    """
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXUIElementSetAttributeValue,
        kAXErrorSuccess,
    )

    err, zoomed = AXUIElementCopyAttributeValue(win, "AXIsZoomed", None)
    logger.debug("_unzoom_if_needed: err=%s zoomed=%s", err, zoomed)
    if err == kAXErrorSuccess and zoomed:
        ret = AXUIElementSetAttributeValue(win, "AXIsZoomed", False)
        logger.debug("_unzoom_if_needed: set AXIsZoomed=False ret=%s", ret)


def _get_focused_window():
    """Return the AXUIElement for the focused window, or None.

    Uses NSWorkspace to find the frontmost application (more reliable than
    the AX system-wide ``AXFocusedApplication`` query, which can return
    ``kAXErrorCannotComplete`` for Electron apps like Chrome and Slack).
    """
    from AppKit import NSWorkspace
    from ApplicationServices import (
        AXUIElementCreateApplication,
        AXUIElementCopyAttributeValue,
        kAXErrorSuccess,
    )

    front_app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if front_app is None:
        logger.debug("_get_focused_window: no frontmost app")
        return None
    pid = front_app.processIdentifier()
    app = AXUIElementCreateApplication(pid)
    err, win = AXUIElementCopyAttributeValue(app, "AXFocusedWindow", None)
    if err != kAXErrorSuccess or win is None:
        logger.debug(
            "_get_focused_window: no focused window for %s pid=%s (err=%s)",
            front_app.localizedName(), pid, err,
        )
        return None
    logger.debug(
        "_get_focused_window: %s pid=%s win=%s",
        front_app.localizedName(), pid, win,
    )
    return win


def _get_windows_for_pid(pid: int):
    """Return the AXWindows array for a given PID, or None."""
    from ApplicationServices import (
        AXUIElementCreateApplication,
        AXUIElementCopyAttributeValue,
        kAXErrorSuccess,
    )

    ax_app = AXUIElementCreateApplication(pid)
    err, windows = AXUIElementCopyAttributeValue(ax_app, "AXWindows", None)
    if err != kAXErrorSuccess or not windows:
        return None
    return windows


def _get_window_title(win) -> str:
    """Return the AXTitle of a window, or empty string."""
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        kAXErrorSuccess,
    )

    err, title = AXUIElementCopyAttributeValue(win, "AXTitle", None)
    if err != kAXErrorSuccess or title is None:
        return ""
    return str(title)


def _get_position(win) -> Optional[tuple]:
    """Return (x, y) of the window in AX (top-left origin) coords."""
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXValueGetValue,
        kAXErrorSuccess,
        kAXValueCGPointType,
    )

    err, val = AXUIElementCopyAttributeValue(win, "AXPosition", None)
    if err != kAXErrorSuccess or val is None:
        return None
    ok, point = AXValueGetValue(val, kAXValueCGPointType, None)
    if ok:
        return (point.x, point.y)
    return None


def _set_position(win, x: float, y: float):
    """Set the window position in AX coords."""
    from ApplicationServices import (
        AXUIElementSetAttributeValue,
        AXValueCreate,
        kAXValueCGPointType,
    )
    import Quartz

    point = Quartz.CGPoint(x=x, y=y)
    val = AXValueCreate(kAXValueCGPointType, point)
    AXUIElementSetAttributeValue(win, "AXPosition", val)


def _get_size(win) -> Optional[tuple]:
    """Return (width, height) of the window."""
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXValueGetValue,
        kAXErrorSuccess,
        kAXValueCGSizeType,
    )

    err, val = AXUIElementCopyAttributeValue(win, "AXSize", None)
    if err != kAXErrorSuccess or val is None:
        return None
    ok, size = AXValueGetValue(val, kAXValueCGSizeType, None)
    if ok:
        return (size.width, size.height)
    return None


def _set_size(win, w: float, h: float):
    """Set the window size."""
    from ApplicationServices import (
        AXUIElementSetAttributeValue,
        AXValueCreate,
        kAXValueCGSizeType,
    )
    import Quartz

    size = Quartz.CGSize(width=w, height=h)
    val = AXValueCreate(kAXValueCGSizeType, size)
    AXUIElementSetAttributeValue(win, "AXSize", val)


def _apply_frame(win, x: float, y: float, w: float, h: float):
    """Un-zoom, then set position → size → position.

    The double position call corrects for apps that reposition the window
    on resize (e.g. some Electron apps).
    """
    _unzoom_if_needed(win)
    _set_position(win, x, y)
    _set_size(win, w, h)
    _set_position(win, x, y)


def _visible_frame_ax(screen) -> tuple:
    """Return (x, y, w, h) of screen's visible area in AX coords.

    Converts from Cocoa (bottom-left origin) to AX (top-left origin).
    """
    from AppKit import NSScreen

    main_h = NSScreen.mainScreen().frame().size.height
    vf = screen.visibleFrame()
    ax_x = vf.origin.x
    ax_y = main_h - vf.origin.y - vf.size.height
    return (ax_x, ax_y, vf.size.width, vf.size.height)


def _screen_for_window(win):
    """Find the NSScreen whose visible area contains the window center."""
    from AppKit import NSScreen

    pos = _get_position(win)
    sz = _get_size(win)
    if pos is None or sz is None:
        return NSScreen.mainScreen()

    cx = pos[0] + sz[0] / 2
    cy = pos[1] + sz[1] / 2

    for s in NSScreen.screens():
        sx, sy, sw, sh = _visible_frame_ax(s)
        if sx <= cx <= sx + sw and sy <= cy <= sy + sh:
            return s
    return NSScreen.mainScreen()


class WindowAPI:
    """Window management — ``wz.window``."""

    def focused_frame(self) -> Optional[dict]:
        """Return ``{"x", "y", "w", "h"}`` of the focused window, or None."""
        win = _get_focused_window()
        if win is None:
            return None
        pos = _get_position(win)
        sz = _get_size(win)
        if pos is None or sz is None:
            return None
        return {"x": pos[0], "y": pos[1], "w": sz[0], "h": sz[1]}

    def set_frame(self, x: float, y: float, w: float, h: float) -> None:
        """Move and resize the focused window."""
        win = _get_focused_window()
        if win is None:
            return
        _apply_frame(win, x, y, w, h)

    def screens(self) -> list[dict]:
        """Return visible area of each screen in AX coords.

        Each dict has ``x``, ``y``, ``w``, ``h``, and ``name``.
        """
        from AppKit import NSScreen

        result = []
        for s in NSScreen.screens():
            sx, sy, sw, sh = _visible_frame_ax(s)
            name = (
                s.localizedName()
                if hasattr(s, "localizedName")
                else str(s)
            )
            result.append(
                {"x": sx, "y": sy, "w": sw, "h": sh, "name": name}
            )
        return result

    _SNAP_POSITIONS = {
        "left": (0, 0, 0.5, 1),
        "right": (0.5, 0, 0.5, 1),
        "top": (0, 0, 1, 0.5),
        "bottom": (0, 0.5, 1, 0.5),
        "full": (0, 0, 1, 1),
        "top-left": (0, 0, 0.5, 0.5),
        "top-right": (0.5, 0, 0.5, 0.5),
        "bottom-left": (0, 0.5, 0.5, 0.5),
        "bottom-right": (0.5, 0.5, 0.5, 0.5),
    }

    def snap(self, position: str) -> None:
        """Snap the focused window.

        Positions: ``left``, ``right``, ``top``, ``bottom``, ``full``,
        ``top-left``, ``top-right``, ``bottom-left``, ``bottom-right``.
        """
        fracs = self._SNAP_POSITIONS.get(position)
        if fracs is None:
            logger.warning("Unknown snap position: %s", position)
            return
        win = _get_focused_window()
        if win is None:
            logger.debug("snap(%s): no focused window", position)
            return

        sx, sy, sw, sh = _visible_frame_ax(_screen_for_window(win))
        fx, fy, fw, fh = fracs
        x = sx + fx * sw
        y = sy + fy * sh
        w = fw * sw
        h = fh * sh

        _apply_frame(win, x, y, w, h)

    def center(self) -> None:
        """Center the focused window on its current screen."""
        win = _get_focused_window()
        if win is None:
            return
        sz = _get_size(win)
        if sz is None:
            return
        _unzoom_if_needed(win)
        sx, sy, sw, sh = _visible_frame_ax(_screen_for_window(win))
        _set_position(win, sx + (sw - sz[0]) / 2, sy + (sh - sz[1]) / 2)

    def move_to_screen(self, direction: str = "next") -> None:
        """Move the focused window to the next or previous screen.

        Preserves relative position and size within the visible area.
        """
        from AppKit import NSScreen

        win = _get_focused_window()
        if win is None:
            return
        all_screens = NSScreen.screens()
        if len(all_screens) < 2:
            return

        current = _screen_for_window(win)
        idx = 0
        for i, s in enumerate(all_screens):
            if s == current:
                idx = i
                break

        if direction == "prev":
            new_idx = (idx - 1) % len(all_screens)
        else:
            new_idx = (idx + 1) % len(all_screens)

        pos = _get_position(win)
        sz = _get_size(win)
        if pos is None or sz is None:
            return

        osx, osy, osw, osh = _visible_frame_ax(current)
        nsx, nsy, nsw, nsh = _visible_frame_ax(all_screens[new_idx])

        # Map relative position from old screen to new screen
        rel_x = (pos[0] - osx) / osw if osw else 0
        rel_y = (pos[1] - osy) / osh if osh else 0
        rel_w = sz[0] / osw if osw else 0.5
        rel_h = sz[1] / osh if osh else 0.5

        new_x = nsx + rel_x * nsw
        new_y = nsy + rel_y * nsh
        new_w = rel_w * nsw
        new_h = rel_h * nsh

        _apply_frame(win, new_x, new_y, new_w, new_h)

    def list(self) -> list[dict]:
        """Return info about all open windows across all applications.

        Each dict has ``app_name``, ``title``, ``pid``, ``window_index``,
        and ``app_path``.  Windows without a title are skipped (utility
        windows, hidden panels, etc.).
        """
        from AppKit import (
            NSApplicationActivationPolicyRegular,
            NSWorkspace,
        )

        result = []
        own_pid = os.getpid()
        workspace = NSWorkspace.sharedWorkspace()

        for app in workspace.runningApplications():
            if app.activationPolicy() != NSApplicationActivationPolicyRegular:
                continue
            pid = app.processIdentifier()
            if pid == own_pid:
                continue
            app_name = app.localizedName() or ""
            bundle_url = app.bundleURL()
            app_path = str(bundle_url.path()) if bundle_url else ""

            windows = _get_windows_for_pid(pid)
            if not windows:
                continue

            for idx, win in enumerate(windows):
                title = _get_window_title(win)
                if not title:
                    continue
                result.append({
                    "app_name": app_name,
                    "title": title,
                    "pid": pid,
                    "window_index": idx,
                    "app_path": app_path,
                })
        return result

    def focus(self, pid: int, window_index: int = 0) -> bool:
        """Bring the specified window to the front.

        Args:
            pid: Process ID of the application.
            window_index: Index in the app's ``AXWindows`` array.

        Returns:
            True if the window was raised successfully.
        """
        from AppKit import NSWorkspace
        from ApplicationServices import AXUIElementPerformAction

        windows = _get_windows_for_pid(pid)
        if not windows or window_index >= len(windows):
            logger.debug("focus: no window for pid=%s idx=%s", pid, window_index)
            return False

        AXUIElementPerformAction(windows[window_index], "AXRaise")

        # Activate the owning application
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            if app.processIdentifier() == pid:
                # NSApplicationActivateIgnoringOtherApps = 1 << 1 = 2
                app.activateWithOptions_(2)
                break
        return True

    def close(self, pid: int, window_index: int = 0) -> bool:
        """Close the specified window via its AXCloseButton.

        Args:
            pid: Process ID of the application.
            window_index: Index in the app's ``AXWindows`` array.

        Returns:
            True if the close button was pressed successfully.
        """
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementPerformAction,
            kAXErrorSuccess,
        )

        windows = _get_windows_for_pid(pid)
        if not windows or window_index >= len(windows):
            logger.debug("close: no window for pid=%s idx=%s", pid, window_index)
            return False

        win = windows[window_index]
        err, close_btn = AXUIElementCopyAttributeValue(
            win, "AXCloseButton", None,
        )
        if err != kAXErrorSuccess or close_btn is None:
            logger.debug("close: no AXCloseButton for pid=%s idx=%s", pid, window_index)
            return False

        AXUIElementPerformAction(close_btn, "AXPress")
        return True
