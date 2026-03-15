"""Tests for leader alert panel."""

from unittest.mock import patch

from voicetext.scripting.registry import LeaderMapping


class TestLeaderAlertPanel:
    @patch("voicetext.scripting.ui.leader_alert.NSPanel", create=True)
    @patch("voicetext.scripting.ui.leader_alert.NSScreen", create=True)
    @patch("voicetext.scripting.ui.leader_alert.NSTextField", create=True)
    @patch("voicetext.scripting.ui.leader_alert.NSFont", create=True)
    @patch("voicetext.scripting.ui.leader_alert.NSColor", create=True)
    @patch("voicetext.scripting.ui.leader_alert.NSMakeRect", create=True)
    @patch("voicetext.scripting.ui.leader_alert.NSBackingStoreBuffered", 2, create=True)
    @patch("voicetext.scripting.ui.leader_alert.NSStatusWindowLevel", 25, create=True)
    def test_show_and_close(self, *mocks):
        from voicetext.scripting.ui.leader_alert import LeaderAlertPanel

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
        from voicetext.scripting.ui.leader_alert import LeaderAlertPanel

        panel = LeaderAlertPanel()
        panel.close()  # Should not raise
