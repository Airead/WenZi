"""Tests for vt.keystroke API."""

import sys
from unittest.mock import MagicMock, patch


class TestKeystroke:
    def test_keystroke_basic(self):
        mock_quartz = MagicMock()
        mock_quartz.CGEventCreateKeyboardEvent.return_value = MagicMock()
        mock_quartz.kCGAnnotatedSessionEventTap = 1

        with patch.dict(sys.modules, {"Quartz": mock_quartz}):
            import importlib
            import voicetext.scripting.api.eventtap as eventtap_mod

            importlib.reload(eventtap_mod)
            eventtap_mod.keystroke("c")
            assert mock_quartz.CGEventCreateKeyboardEvent.call_count == 2
            assert mock_quartz.CGEventPost.call_count == 2

    def test_keystroke_with_modifiers(self):
        mock_quartz = MagicMock()
        mock_quartz.CGEventCreateKeyboardEvent.return_value = MagicMock()
        mock_quartz.kCGAnnotatedSessionEventTap = 1

        with patch.dict(sys.modules, {"Quartz": mock_quartz}):
            import importlib
            import voicetext.scripting.api.eventtap as eventtap_mod

            importlib.reload(eventtap_mod)
            eventtap_mod.keystroke("v", modifiers=["cmd"])
            assert mock_quartz.CGEventSetFlags.call_count == 2
