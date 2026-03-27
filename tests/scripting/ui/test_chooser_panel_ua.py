"""Tests for ChooserPanel Universal Action mode."""

from wenzi.scripting.ui.chooser_panel import ChooserPanel


class TestChooserPanelUAState:
    def test_context_text_initially_none(self):
        panel = ChooserPanel()
        assert panel._context_text is None

    def test_context_text_set(self):
        panel = ChooserPanel()
        panel._context_text = "test text"
        assert panel._context_text == "test text"

    def test_context_text_cleared(self):
        panel = ChooserPanel()
        panel._context_text = "test text"
        panel._context_text = None
        assert panel._context_text is None
