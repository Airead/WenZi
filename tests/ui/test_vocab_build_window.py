"""Tests for the vocabulary build progress window."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import mock_panel_close_delegate


@pytest.fixture(autouse=True)
def _mock_appkit(mock_appkit_modules, monkeypatch):
    """Mock AppKit and Foundation modules for headless testing."""
    import voicetext.ui.vocab_build_window as _vbw

    mock_panel_close_delegate(monkeypatch, _vbw)
    return mock_appkit_modules


class TestVocabBuildProgressPanel:
    def _make_panel(self):
        from voicetext.ui.vocab_build_window import VocabBuildProgressPanel

        return VocabBuildProgressPanel()

    def test_show_creates_panel(self, _mock_appkit):
        panel = self._make_panel()
        on_cancel = MagicMock()
        panel.show(on_cancel=on_cancel)

        assert panel._panel is not None
        assert panel._status_label is not None
        assert panel._stream_text_view is not None

    def test_update_status(self, _mock_appkit):
        panel = self._make_panel()
        panel.show(on_cancel=MagicMock())

        panel.update_status("Batch 1/3 — extracting...")
        panel._status_label.setStringValue_.assert_called_with("Batch 1/3 — extracting...")

    def test_append_stream_text(self, _mock_appkit):
        panel = self._make_panel()
        panel.show(on_cancel=MagicMock())

        panel.append_stream_text('{"term": "Python"')
        # Verify text storage was appended to
        panel._stream_text_view.textStorage().appendAttributedString_.assert_called()

    def test_clear_stream_text(self, _mock_appkit):
        panel = self._make_panel()
        panel.show(on_cancel=MagicMock())

        panel.clear_stream_text()
        panel._stream_text_view.setString_.assert_called_with("")

    def test_close(self, _mock_appkit):
        panel = self._make_panel()
        panel.show(on_cancel=MagicMock())

        mock_ns_panel = panel._panel
        panel.close()
        mock_ns_panel.orderOut_.assert_called_with(None)
        assert panel._panel is None

    def test_cancel_button_calls_callback(self, _mock_appkit):
        panel = self._make_panel()
        on_cancel = MagicMock()
        panel.show(on_cancel=on_cancel)

        panel.cancelClicked_(None)
        on_cancel.assert_called_once()

    def test_close_when_already_closed(self, _mock_appkit):
        panel = self._make_panel()
        # Close without show - should not raise
        panel.close()
        assert panel._panel is None

    def test_update_status_when_no_label(self, _mock_appkit):
        panel = self._make_panel()
        # Should not raise when panel not shown
        panel.update_status("test")

    def test_append_stream_text_when_no_view(self, _mock_appkit):
        panel = self._make_panel()
        # Should not raise when panel not shown
        panel.append_stream_text("test")

    def test_clear_stream_text_when_no_view(self, _mock_appkit):
        panel = self._make_panel()
        # Should not raise when panel not shown
        panel.clear_stream_text()

    def test_close_button_triggers_cancel(self, _mock_appkit):
        """Clicking the X button should trigger the cancel callback."""
        panel = self._make_panel()
        cancelled = []
        panel.show(on_cancel=lambda: cancelled.append(True))

        # Simulate close button via cancelClicked_ (same as windowWillClose: delegate)
        panel.cancelClicked_(None)
        assert cancelled == [True]

    def test_cancel_via_close_only_fires_once(self, _mock_appkit):
        """Cancel callback should not fire twice on repeated calls."""
        panel = self._make_panel()
        cancel_count = []
        panel.show(on_cancel=lambda: cancel_count.append(1))

        panel.cancelClicked_(None)
        panel.cancelClicked_(None)
        assert len(cancel_count) == 1

    def test_close_clears_delegate(self, _mock_appkit):
        """close() should clear the delegate to prevent re-entry."""
        panel = self._make_panel()
        panel.show(on_cancel=MagicMock())

        assert panel._close_delegate is not None
        panel.close()
        assert panel._close_delegate is None
        assert panel._on_cancel is None
