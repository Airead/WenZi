"""Tests for wz.window API."""

from unittest.mock import MagicMock

import pytest

from wenzi.scripting.api.window import (
    WindowAPI,
    _get_window_title,
    _get_windows_for_pid,
    _unzoom_if_needed,
    _visible_frame_ax,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_ax(monkeypatch, *, pos=(100, 200), size=(800, 600)):
    """Patch AX helpers to return fixed values without real Accessibility."""
    win = MagicMock(name="AXWindow")
    monkeypatch.setattr(
        "wenzi.scripting.api.window._get_focused_window", lambda: win
    )
    monkeypatch.setattr(
        "wenzi.scripting.api.window._get_position",
        lambda w: pos if w is win else None,
    )
    monkeypatch.setattr(
        "wenzi.scripting.api.window._get_size",
        lambda w: size if w is win else None,
    )
    set_pos_calls = []
    set_size_calls = []
    unzoom_calls = []
    monkeypatch.setattr(
        "wenzi.scripting.api.window._set_position",
        lambda w, x, y: set_pos_calls.append((x, y)),
    )
    monkeypatch.setattr(
        "wenzi.scripting.api.window._set_size",
        lambda w, x, y: set_size_calls.append((x, y)),
    )
    monkeypatch.setattr(
        "wenzi.scripting.api.window._unzoom_if_needed",
        lambda w: unzoom_calls.append(w),
    )
    return win, set_pos_calls, set_size_calls, unzoom_calls


def _mock_screen(x=0, y=0, w=1920, h=1080, vis_x=0, vis_y=0, vis_w=1920, vis_h=1055, name="Built-in"):
    """Create a fake NSScreen-like object."""
    screen = MagicMock()
    frame = MagicMock()
    frame.origin.x = x
    frame.origin.y = y
    frame.size.width = w
    frame.size.height = h
    screen.frame.return_value = frame
    vf = MagicMock()
    vf.origin.x = vis_x
    vf.origin.y = vis_y
    vf.size.width = vis_w
    vf.size.height = vis_h
    screen.visibleFrame.return_value = vf
    screen.localizedName.return_value = name
    return screen


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFocusedFrame:
    def test_returns_dict(self, monkeypatch):
        _mock_ax(monkeypatch, pos=(50, 100), size=(640, 480))  # unzoom_calls unused
        api = WindowAPI()
        frame = api.focused_frame()
        assert frame == {"x": 50, "y": 100, "w": 640, "h": 480}

    def test_returns_none_when_no_window(self, monkeypatch):
        monkeypatch.setattr(
            "wenzi.scripting.api.window._get_focused_window", lambda: None
        )
        assert WindowAPI().focused_frame() is None


class TestSetFrame:
    def test_sets_position_and_size(self, monkeypatch):
        _, pos_calls, size_calls, _ = _mock_ax(monkeypatch)
        WindowAPI().set_frame(10, 20, 300, 400)
        # Position set twice (before and after resize)
        assert pos_calls == [(10, 20), (10, 20)]
        assert size_calls == [(300, 400)]

    def test_noop_when_no_window(self, monkeypatch):
        monkeypatch.setattr(
            "wenzi.scripting.api.window._get_focused_window", lambda: None
        )
        WindowAPI().set_frame(0, 0, 100, 100)  # should not raise


class TestSnap:
    @pytest.mark.parametrize(
        "position, expected_frac",
        [
            ("left", (0, 0, 0.5, 1)),
            ("right", (0.5, 0, 0.5, 1)),
            ("top", (0, 0, 1, 0.5)),
            ("bottom", (0, 0.5, 1, 0.5)),
            ("full", (0, 0, 1, 1)),
            ("top-left", (0, 0, 0.5, 0.5)),
            ("bottom-right", (0.5, 0.5, 0.5, 0.5)),
        ],
    )
    def test_snap_positions(self, monkeypatch, position, expected_frac):
        win, pos_calls, size_calls, _ = _mock_ax(monkeypatch, pos=(100, 50), size=(800, 600))
        # Mock screen: visible area 1920x1055 starting at (0, 25) in AX coords
        screen = _mock_screen()
        monkeypatch.setattr(
            "wenzi.scripting.api.window._screen_for_window", lambda w: screen
        )
        monkeypatch.setattr(
            "wenzi.scripting.api.window._visible_frame_ax",
            lambda s: (0, 25, 1920, 1055),
        )

        WindowAPI().snap(position)

        fx, fy, fw, fh = expected_frac
        expected_x = 0 + fx * 1920
        expected_y = 25 + fy * 1055
        expected_w = fw * 1920
        expected_h = fh * 1055

        assert size_calls == [(expected_w, expected_h)]
        assert pos_calls == [(expected_x, expected_y), (expected_x, expected_y)]

    def test_unknown_position_warns(self, monkeypatch, caplog):
        _mock_ax(monkeypatch)
        import logging
        with caplog.at_level(logging.WARNING):
            WindowAPI().snap("diagonal")
        assert "Unknown snap position" in caplog.text


class TestCenter:
    def test_centers_on_screen(self, monkeypatch):
        win, pos_calls, _, _ = _mock_ax(monkeypatch, pos=(0, 0), size=(400, 300))
        monkeypatch.setattr(
            "wenzi.scripting.api.window._screen_for_window", lambda w: MagicMock()
        )
        monkeypatch.setattr(
            "wenzi.scripting.api.window._visible_frame_ax",
            lambda s: (0, 25, 1920, 1055),
        )

        WindowAPI().center()

        # Centered: x = (1920-400)/2, y = 25 + (1055-300)/2
        assert pos_calls == [(760.0, 402.5)]


def _patch_nsscreen(monkeypatch, screens):
    """Patch AppKit.NSScreen so lazy imports inside window.py see the mock."""
    import AppKit as _appkit

    mock_ns = MagicMock()
    mock_ns.mainScreen.return_value = screens[0]
    mock_ns.screens.return_value = screens
    monkeypatch.setattr(_appkit, "NSScreen", mock_ns)
    return mock_ns


class TestScreens:
    def test_returns_screen_info(self, monkeypatch):
        screen = _mock_screen(vis_x=0, vis_y=0, vis_w=1920, vis_h=1055)
        _patch_nsscreen(monkeypatch, [screen])
        monkeypatch.setattr(
            "wenzi.scripting.api.window._visible_frame_ax",
            lambda s: (0, 25, 1920, 1055),
        )

        result = WindowAPI().screens()

        assert len(result) == 1
        assert result[0]["w"] == 1920
        assert result[0]["name"] == "Built-in"


class TestMoveToScreen:
    def test_moves_to_next_screen(self, monkeypatch):
        win, pos_calls, size_calls, _ = _mock_ax(
            monkeypatch, pos=(100, 125), size=(960, 1055)
        )
        s1 = _mock_screen(name="Primary")
        s2 = _mock_screen(name="Secondary")
        _patch_nsscreen(monkeypatch, [s1, s2])

        monkeypatch.setattr(
            "wenzi.scripting.api.window._screen_for_window", lambda w: s1
        )

        def fake_visible(s):
            if s is s1:
                return (0, 25, 1920, 1055)
            return (1920, 25, 1920, 1055)

        monkeypatch.setattr(
            "wenzi.scripting.api.window._visible_frame_ax", fake_visible
        )

        WindowAPI().move_to_screen("next")

        assert len(pos_calls) == 2
        assert len(size_calls) == 1

    def test_noop_single_screen(self, monkeypatch):
        _mock_ax(monkeypatch)
        s1 = _mock_screen()
        _patch_nsscreen(monkeypatch, [s1])
        monkeypatch.setattr(
            "wenzi.scripting.api.window._screen_for_window", lambda w: s1
        )
        WindowAPI().move_to_screen("next")  # should not raise


class TestUnzoom:
    def test_unzooms_when_zoomed(self, monkeypatch):
        """_unzoom_if_needed sets AXIsZoomed to False when window is zoomed."""
        import ApplicationServices as _as

        set_calls = []
        monkeypatch.setattr(
            _as, "AXUIElementCopyAttributeValue",
            lambda w, attr, _: (_as.kAXErrorSuccess, True),
        )
        monkeypatch.setattr(
            _as, "AXUIElementSetAttributeValue",
            lambda w, attr, val: set_calls.append((attr, val)),
        )
        win = MagicMock()
        _unzoom_if_needed(win)
        assert set_calls == [("AXIsZoomed", False)]

    def test_noop_when_not_zoomed(self, monkeypatch):
        import ApplicationServices as _as

        set_calls = []
        monkeypatch.setattr(
            _as, "AXUIElementCopyAttributeValue",
            lambda w, attr, _: (_as.kAXErrorSuccess, False),
        )
        monkeypatch.setattr(
            _as, "AXUIElementSetAttributeValue",
            lambda w, attr, val: set_calls.append((attr, val)),
        )
        win = MagicMock()
        _unzoom_if_needed(win)
        assert set_calls == []

    def test_snap_calls_unzoom(self, monkeypatch):
        """snap() must call _unzoom_if_needed before repositioning."""
        win, pos_calls, size_calls, unzoom_calls = _mock_ax(
            monkeypatch, pos=(100, 50), size=(800, 600)
        )
        monkeypatch.setattr(
            "wenzi.scripting.api.window._screen_for_window", lambda w: MagicMock()
        )
        monkeypatch.setattr(
            "wenzi.scripting.api.window._visible_frame_ax",
            lambda s: (0, 25, 1920, 1055),
        )
        WindowAPI().snap("left")
        assert len(unzoom_calls) == 1
        assert unzoom_calls[0] is win

    def test_set_frame_calls_unzoom(self, monkeypatch):
        win, _, _, unzoom_calls = _mock_ax(monkeypatch)
        WindowAPI().set_frame(10, 20, 300, 400)
        assert len(unzoom_calls) == 1
        assert unzoom_calls[0] is win

    def test_center_calls_unzoom(self, monkeypatch):
        win, _, _, unzoom_calls = _mock_ax(monkeypatch, pos=(0, 0), size=(400, 300))
        monkeypatch.setattr(
            "wenzi.scripting.api.window._screen_for_window", lambda w: MagicMock()
        )
        monkeypatch.setattr(
            "wenzi.scripting.api.window._visible_frame_ax",
            lambda s: (0, 25, 1920, 1055),
        )
        WindowAPI().center()
        assert len(unzoom_calls) == 1
        assert unzoom_calls[0] is win

    def test_move_to_screen_calls_unzoom(self, monkeypatch):
        win, _, _, unzoom_calls = _mock_ax(
            monkeypatch, pos=(100, 125), size=(960, 1055)
        )
        s1 = _mock_screen(name="Primary")
        s2 = _mock_screen(name="Secondary")
        _patch_nsscreen(monkeypatch, [s1, s2])
        monkeypatch.setattr(
            "wenzi.scripting.api.window._screen_for_window", lambda w: s1
        )
        monkeypatch.setattr(
            "wenzi.scripting.api.window._visible_frame_ax",
            lambda s: (0, 25, 1920, 1055) if s is s1 else (1920, 25, 1920, 1055),
        )
        WindowAPI().move_to_screen("next")
        assert len(unzoom_calls) == 1
        assert unzoom_calls[0] is win

    def test_noop_when_ax_error(self, monkeypatch):
        """_unzoom_if_needed does nothing when AXIsZoomed is unsupported."""
        import ApplicationServices as _as

        set_calls = []
        monkeypatch.setattr(
            _as, "AXUIElementCopyAttributeValue",
            lambda w, attr, _: (-25200, None),  # kAXErrorAttributeUnsupported
        )
        monkeypatch.setattr(
            _as, "AXUIElementSetAttributeValue",
            lambda w, attr, val: set_calls.append((attr, val)),
        )
        _unzoom_if_needed(MagicMock())
        assert set_calls == []


class TestVisibleFrameAx:
    def test_coordinate_conversion(self, monkeypatch):
        """Cocoa bottom-left origin → AX top-left origin."""
        screen = _mock_screen(
            w=1920, h=1080,
            vis_x=0, vis_y=25, vis_w=1920, vis_h=1055,
        )
        main_screen = _mock_screen(w=1920, h=1080)
        _patch_nsscreen(monkeypatch, [main_screen])

        x, y, w, h = _visible_frame_ax(screen)

        assert x == 0
        # AX y = main_h - cocoa_y - vis_h = 1080 - 25 - 1055 = 0
        assert y == 0
        assert w == 1920
        assert h == 1055


# ---------------------------------------------------------------------------
# Helpers for list / focus / close
# ---------------------------------------------------------------------------

def _make_running_app(pid, name="TestApp", policy=0, bundle_path="/Applications/Test.app"):
    """Create a fake NSRunningApplication."""
    app = MagicMock()
    app.processIdentifier.return_value = pid
    app.localizedName.return_value = name
    app.activationPolicy.return_value = policy
    bundle_url = MagicMock()
    bundle_url.path.return_value = bundle_path
    app.bundleURL.return_value = bundle_url
    return app


def _patch_workspace_apps(monkeypatch, apps):
    """Patch NSWorkspace.sharedWorkspace().runningApplications()."""
    import AppKit as _appkit

    workspace = MagicMock()
    workspace.runningApplications.return_value = apps
    ns_workspace = MagicMock()
    ns_workspace.sharedWorkspace.return_value = workspace
    monkeypatch.setattr(_appkit, "NSWorkspace", ns_workspace)
    return workspace


# ---------------------------------------------------------------------------
# Tests: list / focus / close
# ---------------------------------------------------------------------------

class TestList:
    def test_returns_windows(self, monkeypatch):
        win1 = MagicMock(name="win1")
        win2 = MagicMock(name="win2")
        app = _make_running_app(100, "Safari", bundle_path="/Applications/Safari.app")
        _patch_workspace_apps(monkeypatch, [app])

        monkeypatch.setattr(
            "wenzi.scripting.api.window._get_windows_for_pid",
            lambda pid: [win1, win2] if pid == 100 else None,
        )
        titles = {"win1": "Google", "win2": "GitHub"}
        monkeypatch.setattr(
            "wenzi.scripting.api.window._get_window_title",
            lambda w: titles.get(w._mock_name, ""),
        )
        monkeypatch.setattr("wenzi.scripting.api.window.os.getpid", lambda: 999)

        result = WindowAPI().list()

        assert len(result) == 2
        assert result[0] == {
            "app_name": "Safari",
            "title": "Google",
            "pid": 100,
            "window_index": 0,
            "app_path": "/Applications/Safari.app",
        }
        assert result[1]["title"] == "GitHub"
        assert result[1]["window_index"] == 1

    def test_skips_own_process(self, monkeypatch):
        app = _make_running_app(42, "WenZi")
        _patch_workspace_apps(monkeypatch, [app])
        monkeypatch.setattr("wenzi.scripting.api.window.os.getpid", lambda: 42)

        result = WindowAPI().list()
        assert result == []

    def test_skips_non_regular_apps(self, monkeypatch):
        # policy=1 → NSApplicationActivationPolicyAccessory
        app = _make_running_app(100, "Daemon", policy=1)
        _patch_workspace_apps(monkeypatch, [app])
        monkeypatch.setattr("wenzi.scripting.api.window.os.getpid", lambda: 999)

        result = WindowAPI().list()
        assert result == []

    def test_skips_windows_without_title(self, monkeypatch):
        win = MagicMock(name="untitled")
        app = _make_running_app(100, "Foo")
        _patch_workspace_apps(monkeypatch, [app])
        monkeypatch.setattr(
            "wenzi.scripting.api.window._get_windows_for_pid",
            lambda pid: [win],
        )
        monkeypatch.setattr(
            "wenzi.scripting.api.window._get_window_title",
            lambda w: "",
        )
        monkeypatch.setattr("wenzi.scripting.api.window.os.getpid", lambda: 999)

        result = WindowAPI().list()
        assert result == []

    def test_skips_apps_with_no_windows(self, monkeypatch):
        app = _make_running_app(100, "Empty")
        _patch_workspace_apps(monkeypatch, [app])
        monkeypatch.setattr(
            "wenzi.scripting.api.window._get_windows_for_pid",
            lambda pid: None,
        )
        monkeypatch.setattr("wenzi.scripting.api.window.os.getpid", lambda: 999)

        result = WindowAPI().list()
        assert result == []


class TestFocus:
    def test_raises_and_activates(self, monkeypatch):
        import ApplicationServices as _as

        win = MagicMock(name="target")
        monkeypatch.setattr(
            "wenzi.scripting.api.window._get_windows_for_pid",
            lambda pid: [win] if pid == 100 else None,
        )
        perform_calls = []
        monkeypatch.setattr(
            _as, "AXUIElementPerformAction",
            lambda w, action: perform_calls.append((w, action)),
        )
        app = _make_running_app(100, "Safari")
        _patch_workspace_apps(monkeypatch, [app])

        result = WindowAPI().focus(100, 0)

        assert result is True
        assert perform_calls == [(win, "AXRaise")]
        app.activateWithOptions_.assert_called_once_with(2)

    def test_returns_false_no_windows(self, monkeypatch):
        monkeypatch.setattr(
            "wenzi.scripting.api.window._get_windows_for_pid",
            lambda pid: None,
        )
        assert WindowAPI().focus(100) is False

    def test_returns_false_bad_index(self, monkeypatch):
        monkeypatch.setattr(
            "wenzi.scripting.api.window._get_windows_for_pid",
            lambda pid: [MagicMock()],
        )
        assert WindowAPI().focus(100, window_index=5) is False


class TestClose:
    def test_presses_close_button(self, monkeypatch):
        import ApplicationServices as _as

        win = MagicMock(name="win")
        close_btn = MagicMock(name="close_btn")

        monkeypatch.setattr(
            "wenzi.scripting.api.window._get_windows_for_pid",
            lambda pid: [win] if pid == 100 else None,
        )

        def fake_copy(element, attr, _):
            if element is win and attr == "AXCloseButton":
                return (_as.kAXErrorSuccess, close_btn)
            return (-1, None)

        monkeypatch.setattr(_as, "AXUIElementCopyAttributeValue", fake_copy)

        perform_calls = []
        monkeypatch.setattr(
            _as, "AXUIElementPerformAction",
            lambda w, action: perform_calls.append((w, action)),
        )

        result = WindowAPI().close(100, 0)

        assert result is True
        assert perform_calls == [(close_btn, "AXPress")]

    def test_returns_false_no_close_button(self, monkeypatch):
        import ApplicationServices as _as

        win = MagicMock()
        monkeypatch.setattr(
            "wenzi.scripting.api.window._get_windows_for_pid",
            lambda pid: [win],
        )
        monkeypatch.setattr(
            _as, "AXUIElementCopyAttributeValue",
            lambda w, attr, _: (-1, None),
        )

        assert WindowAPI().close(100, 0) is False

    def test_returns_false_no_windows(self, monkeypatch):
        monkeypatch.setattr(
            "wenzi.scripting.api.window._get_windows_for_pid",
            lambda pid: None,
        )
        assert WindowAPI().close(100) is False


class TestGetWindowsForPid:
    def test_returns_windows(self, monkeypatch):
        import ApplicationServices as _as

        win_list = [MagicMock(), MagicMock()]
        monkeypatch.setattr(
            _as, "AXUIElementCreateApplication",
            lambda pid: MagicMock(),
        )
        monkeypatch.setattr(
            _as, "AXUIElementCopyAttributeValue",
            lambda app, attr, _: (_as.kAXErrorSuccess, win_list),
        )

        result = _get_windows_for_pid(42)
        assert result is win_list

    def test_returns_none_on_error(self, monkeypatch):
        import ApplicationServices as _as

        monkeypatch.setattr(
            _as, "AXUIElementCreateApplication",
            lambda pid: MagicMock(),
        )
        monkeypatch.setattr(
            _as, "AXUIElementCopyAttributeValue",
            lambda app, attr, _: (-1, None),
        )

        assert _get_windows_for_pid(42) is None


class TestGetWindowTitle:
    def test_returns_title(self, monkeypatch):
        import ApplicationServices as _as

        monkeypatch.setattr(
            _as, "AXUIElementCopyAttributeValue",
            lambda w, attr, _: (_as.kAXErrorSuccess, "My Window"),
        )
        assert _get_window_title(MagicMock()) == "My Window"

    def test_returns_empty_on_error(self, monkeypatch):
        import ApplicationServices as _as

        monkeypatch.setattr(
            _as, "AXUIElementCopyAttributeValue",
            lambda w, attr, _: (-1, None),
        )
        assert _get_window_title(MagicMock()) == ""
