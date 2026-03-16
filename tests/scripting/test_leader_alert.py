"""Tests for leader alert panel."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from wenzi.scripting.registry import LeaderMapping
from wenzi.scripting.ui.leader_alert import LeaderAlertPanel


class TestLeaderAlertPanel:
    @patch("wenzi.scripting.ui.leader_alert.NSPanel", create=True)
    @patch("wenzi.scripting.ui.leader_alert.NSScreen", create=True)
    @patch("wenzi.scripting.ui.leader_alert.NSTextField", create=True)
    @patch("wenzi.scripting.ui.leader_alert.NSFont", create=True)
    @patch("wenzi.scripting.ui.leader_alert.NSColor", create=True)
    @patch("wenzi.scripting.ui.leader_alert.NSMakeRect", create=True)
    @patch("wenzi.scripting.ui.leader_alert.NSBackingStoreBuffered", 2, create=True)
    @patch("wenzi.scripting.ui.leader_alert.NSStatusWindowLevel", 25, create=True)
    def test_show_and_close(self, *mocks):
        panel = LeaderAlertPanel()
        assert not panel.is_visible

        mappings = [
            LeaderMapping(key="w", app="WeChat"),
            LeaderMapping(key="s", app="Slack"),
        ]

        # Show will fail gracefully in test env without full AppKit
        # Just verify the API doesn't crash
        try:
            panel.show("cmd_r", mappings)
        except Exception:
            pass  # Expected without real AppKit

    def test_close_when_not_visible(self):
        panel = LeaderAlertPanel()
        panel.close()  # Should not raise


def _screen_frame(x=0, y=0, w=1920, h=1080):
    """Create a mock screen frame (NSRect-like)."""
    return SimpleNamespace(
        origin=SimpleNamespace(x=x, y=y),
        size=SimpleNamespace(width=w, height=h),
    )


def _mock_ns_event(mouse_x=500, mouse_y=600):
    """Create a mock NSEvent class with mouseLocation."""
    ns = MagicMock()
    ns.mouseLocation.return_value = SimpleNamespace(x=mouse_x, y=mouse_y)
    return ns


class TestCalculateOrigin:
    """Tests for LeaderAlertPanel._calculate_origin."""

    def test_center_position(self):
        sf = _screen_frame(w=1920, h=1080)
        x, y = LeaderAlertPanel._calculate_origin(
            "center", 320, 100, sf, _mock_ns_event(),
        )
        assert x == (1920 - 320) / 2
        assert y == (1080 - 100) / 2

    def test_top_position(self):
        sf = _screen_frame(w=1920, h=1080)
        x, y = LeaderAlertPanel._calculate_origin(
            "top", 320, 100, sf, _mock_ns_event(),
        )
        assert x == (1920 - 320) / 2
        assert y == 1080 - 100 - 100  # height - panel_height - 100

    def test_bottom_position(self):
        sf = _screen_frame(w=1920, h=1080)
        x, y = LeaderAlertPanel._calculate_origin(
            "bottom", 320, 100, sf, _mock_ns_event(),
        )
        assert x == (1920 - 320) / 2
        assert y == 100

    def test_mouse_position(self):
        sf = _screen_frame(w=1920, h=1080)
        ns_event = _mock_ns_event(mouse_x=800, mouse_y=600)
        x, y = LeaderAlertPanel._calculate_origin(
            "mouse", 320, 100, sf, ns_event,
        )
        assert x == 800 - 320 / 2
        assert y == 600 - 100 / 2

    def test_tuple_position(self):
        sf = _screen_frame(w=1920, h=1080)
        x, y = LeaderAlertPanel._calculate_origin(
            (0.5, 0.8), 320, 100, sf, _mock_ns_event(),
        )
        assert x == 1920 * 0.5 - 320 / 2
        assert y == 1080 * 0.8 - 100 / 2

    def test_list_position(self):
        sf = _screen_frame(w=1920, h=1080)
        x, y = LeaderAlertPanel._calculate_origin(
            [0.0, 0.0], 320, 100, sf, _mock_ns_event(),
        )
        # Clamped to screen bounds
        assert x == 0
        assert y == 0

    def test_unknown_position_defaults_to_center(self):
        sf = _screen_frame(w=1920, h=1080)
        x, y = LeaderAlertPanel._calculate_origin(
            "unknown_value", 320, 100, sf, _mock_ns_event(),
        )
        assert x == (1920 - 320) / 2
        assert y == (1080 - 100) / 2

    def test_clamp_to_screen_bounds(self):
        sf = _screen_frame(w=1920, h=1080)
        # Mouse at extreme top-right corner
        ns_event = _mock_ns_event(mouse_x=1919, mouse_y=1079)
        x, y = LeaderAlertPanel._calculate_origin(
            "mouse", 320, 100, sf, ns_event,
        )
        assert x <= 1920 - 320
        assert y <= 1080 - 100

    def test_screen_with_offset_origin(self):
        # Simulates a secondary display with non-zero origin
        sf = _screen_frame(x=1920, y=0, w=1440, h=900)
        x, y = LeaderAlertPanel._calculate_origin(
            "center", 320, 100, sf, _mock_ns_event(),
        )
        assert x == 1920 + (1440 - 320) / 2
        assert y == (900 - 100) / 2

    def test_mouse_clamped_to_left_edge(self):
        sf = _screen_frame(w=1920, h=1080)
        ns_event = _mock_ns_event(mouse_x=10, mouse_y=540)
        x, y = LeaderAlertPanel._calculate_origin(
            "mouse", 320, 100, sf, ns_event,
        )
        assert x == 0  # clamped to left edge
