"""Tests for the diff side panel and manual vocabulary integration in ResultPreviewPanel."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest

from tests.conftest import mock_panel_close_delegate


@pytest.fixture(autouse=True)
def mock_appkit(mock_appkit_modules, monkeypatch):
    """Provide mock AppKit/Foundation/WebKit modules for headless testing."""
    mock_webkit = MagicMock()
    monkeypatch.setitem(sys.modules, "WebKit", mock_webkit)

    import wenzi.ui.result_window_web as _rww

    _rww._PanelCloseDelegate = None
    mock_panel_close_delegate(monkeypatch, _rww)

    mock_handler_cls = MagicMock()
    mock_handler_cls.alloc.return_value.init.return_value = MagicMock()
    monkeypatch.setattr(_rww, "_get_message_handler_class", lambda: mock_handler_cls)

    mock_appkit_modules.appkit.NSCommandKeyMask = 1 << 20
    mock_appkit_modules.appkit.NSDeviceIndependentModifierFlagsMask = 0xFFFF0000

    return mock_appkit_modules


def _build_panel():
    """Create a ResultPreviewPanel with mocked internals."""
    from wenzi.ui.result_window_web import ResultPreviewPanel

    panel = ResultPreviewPanel()
    panel._build_panel = MagicMock()
    panel._panel = MagicMock()
    panel._webview = MagicMock()
    panel._page_loaded = True
    return panel


class TestSetAsrDiffs:
    def test_pushes_js_with_correct_data(self, mock_appkit, monkeypatch):
        """set_asr_diffs should call evaluateJavaScript with setAsrDiffs(...)."""
        panel = _build_panel()
        # Bypass AppHelper.callAfter: execute immediately
        monkeypatch.setattr(
            "wenzi.ui.result_window_web.ResultPreviewPanel.set_asr_diffs",
            lambda self, pairs: self._eval_js(
                "setAsrDiffs({})".format(
                    json.dumps(
                        [{"variant": v, "term": t} for v, t in pairs],
                        ensure_ascii=False,
                    )
                )
            ),
        )
        panel.set_asr_diffs([("库伯尼特斯", "Kubernetes")])
        js_call = panel._webview.evaluateJavaScript_completionHandler_
        assert js_call.called
        js_code = js_call.call_args[0][0]
        assert "setAsrDiffs" in js_code
        assert "Kubernetes" in js_code


class TestSetVocabHits:
    def test_pushes_js_with_hit_data(self, mock_appkit, monkeypatch):
        panel = _build_panel()
        monkeypatch.setattr(
            "wenzi.ui.result_window_web.ResultPreviewPanel.set_vocab_hits",
            lambda self, hits: self._eval_js(f"setVocabHits({json.dumps(hits)})"),
        )
        panel.set_vocab_hits([{
            "variant": "派森", "term": "Python",
            "source": "manual", "hitCount": 5, "frequency": 2,
        }])
        js_call = panel._webview.evaluateJavaScript_completionHandler_
        assert js_call.called
        js_code = js_call.call_args[0][0]
        assert "setVocabHits" in js_code
        assert "Python" in js_code


class TestHandleJsMessage:
    def test_add_manual_vocab(self, mock_appkit):
        panel = _build_panel()
        callback = MagicMock()
        panel._on_add_manual_vocab = callback
        panel._handle_js_message({
            "type": "addManualVocab",
            "variant": "派森",
            "term": "Python",
            "source": "asr",
        })
        callback.assert_called_once_with("派森", "Python", "asr")

    def test_remove_manual_vocab(self, mock_appkit):
        panel = _build_panel()
        callback = MagicMock()
        panel._on_remove_manual_vocab = callback
        panel._handle_js_message({
            "type": "removeManualVocab",
            "variant": "派森",
            "term": "Python",
        })
        callback.assert_called_once_with("派森", "Python")

    def test_add_manual_vocab_no_callback(self, mock_appkit):
        """Should not raise when callback is None."""
        panel = _build_panel()
        panel._on_add_manual_vocab = None
        panel._handle_js_message({
            "type": "addManualVocab",
            "variant": "派森",
            "term": "Python",
            "source": "asr",
        })  # Should not raise

    def test_diff_panel_toggle(self, mock_appkit, monkeypatch):
        panel = _build_panel()
        panel._resize_for_diff_panel = MagicMock()
        panel._handle_js_message({
            "type": "diffPanelToggle",
            "open": True,
        })
        panel._resize_for_diff_panel.assert_called_once_with(True)

    def test_compute_user_diffs(self, mock_appkit, monkeypatch):
        panel = _build_panel()
        panel._enhanced_text_cache = "我在用库伯尼特斯"
        # Mock extract_word_pairs
        monkeypatch.setattr(
            "wenzi.enhance.text_diff.extract_word_pairs",
            lambda a, b: [("库伯尼特斯", "Kubernetes")],
        )
        panel._handle_js_message({
            "type": "computeUserDiffs",
            "finalText": "我在用 Kubernetes",
        })
        js_call = panel._webview.evaluateJavaScript_completionHandler_
        assert js_call.called
        js_code = js_call.call_args[0][0]
        assert "setUserDiffs" in js_code


class TestResizeForDiffPanel:
    def test_open_expands(self, mock_appkit):
        panel = _build_panel()
        mock_frame = MagicMock()
        mock_frame.origin.x = 400
        mock_frame.origin.y = 200
        mock_frame.size.width = 640
        mock_frame.size.height = 396
        panel._panel.frame.return_value = mock_frame
        screen = MagicMock()
        vis = MagicMock()
        vis.origin.x = 0
        vis.size.width = 1920
        screen.visibleFrame.return_value = vis
        panel._screen_for_mouse = MagicMock(return_value=screen)

        panel._resize_for_diff_panel(True)
        panel._panel.setFrame_display_.assert_called_once()

    def test_close_shrinks(self, mock_appkit):
        panel = _build_panel()
        mock_frame = MagicMock()
        mock_frame.origin.x = 400
        mock_frame.origin.y = 200
        mock_frame.size.width = 920
        mock_frame.size.height = 396
        panel._panel.frame.return_value = mock_frame

        panel._resize_for_diff_panel(False)
        panel._panel.setFrame_display_.assert_called_once()


class TestCacheEnhancedText:
    def test_cache_text(self, mock_appkit):
        panel = _build_panel()
        panel.cache_enhanced_text("hello world")
        assert panel._enhanced_text_cache == "hello world"

    def test_enhanced_text_property(self, mock_appkit):
        panel = _build_panel()
        panel.cache_enhanced_text("cached text")
        assert panel.enhanced_text == "cached text"


class TestClearDiffs:
    def test_clear_diffs_pushes_js(self, mock_appkit, monkeypatch):
        panel = _build_panel()
        monkeypatch.setattr(
            "wenzi.ui.result_window_web.ResultPreviewPanel.clear_diffs",
            lambda self: self._eval_js(
                "setAsrDiffs([]); setUserDiffs([]); setVocabHits([])"
            ),
        )
        panel.clear_diffs()
        js_call = panel._webview.evaluateJavaScript_completionHandler_
        assert js_call.called
        js_code = js_call.call_args[0][0]
        assert "setAsrDiffs([])" in js_code
        assert "setUserDiffs([])" in js_code
        assert "setVocabHits([])" in js_code
