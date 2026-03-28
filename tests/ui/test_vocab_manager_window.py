"""Tests for the WKWebView-based vocabulary manager panel."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from tests.conftest import mock_panel_close_delegate


@pytest.fixture(autouse=True)
def mock_appkit(mock_appkit_modules, monkeypatch):
    """Provide mock AppKit, Foundation, WebKit modules for headless testing."""
    mock_webkit = MagicMock()
    monkeypatch.setitem(sys.modules, "WebKit", mock_webkit)

    import wenzi.ui.vocab_manager_window as _vmw

    _vmw._VocabManagerCloseDelegate = None
    _vmw._VocabManagerNavigationDelegate = None
    _vmw._VocabManagerMessageHandler = None
    mock_panel_close_delegate(monkeypatch, _vmw, "_VocabManagerCloseDelegate")

    mock_nav_cls = MagicMock()
    mock_nav_instance = MagicMock()
    mock_nav_cls.alloc.return_value.init.return_value = mock_nav_instance
    monkeypatch.setattr(_vmw, "_get_navigation_delegate_class", lambda: mock_nav_cls)

    mock_handler_cls = MagicMock()
    mock_handler_instance = MagicMock()
    mock_handler_cls.alloc.return_value.init.return_value = mock_handler_instance
    monkeypatch.setattr(_vmw, "_get_message_handler_class", lambda: mock_handler_cls)

    return mock_appkit_modules


def _build_panel(panel):
    """Set up a panel with mocked internals for testing."""
    panel._build_panel = MagicMock()
    panel._panel = MagicMock()
    panel._webview = MagicMock()
    panel._page_loaded = True
    return panel


def _get_js_calls(panel):
    """Extract all JS code strings sent to evaluateJavaScript."""
    return [c[0][0] for c in panel._webview.evaluateJavaScript_completionHandler_.call_args_list]


# ---------------------------------------------------------------------------
# Init and lifecycle
# ---------------------------------------------------------------------------


class TestInit:
    def test_defaults(self):
        from wenzi.ui.vocab_manager_window import VocabManagerPanel

        panel = VocabManagerPanel()
        assert panel._panel is None
        assert panel._webview is None
        assert panel._page_loaded is False
        assert panel._pending_js == []
        assert panel._callbacks == {}

    def test_close_without_show_is_noop(self):
        from wenzi.ui.vocab_manager_window import VocabManagerPanel

        panel = VocabManagerPanel()
        panel.close()  # Should not raise

    def test_is_visible_false_by_default(self):
        from wenzi.ui.vocab_manager_window import VocabManagerPanel

        panel = VocabManagerPanel()
        assert panel.is_visible is False


class TestShow:
    def test_show_stores_callbacks(self):
        from wenzi.ui.vocab_manager_window import VocabManagerPanel

        panel = _build_panel(VocabManagerPanel())
        callbacks = {"on_page_ready": MagicMock(), "on_add": MagicMock()}
        panel.show(callbacks)
        assert panel._callbacks is callbacks


class TestClose:
    def test_close_clears_state(self):
        from wenzi.ui.vocab_manager_window import VocabManagerPanel

        panel = _build_panel(VocabManagerPanel())
        panel.show({"on_page_ready": MagicMock()})

        panel.close()

        assert panel._panel is None
        assert panel._webview is None
        assert panel._page_loaded is False
        assert panel._pending_js == []
        assert panel._callbacks == {}


# ---------------------------------------------------------------------------
# JS evaluation
# ---------------------------------------------------------------------------


class TestEvalJs:
    def test_queues_before_page_loaded(self):
        from wenzi.ui.vocab_manager_window import VocabManagerPanel

        panel = VocabManagerPanel()
        panel._webview = MagicMock()
        panel._page_loaded = False

        panel._eval_js("test1()")
        panel._eval_js("test2()")

        assert panel._pending_js == ["test1()", "test2()"]
        panel._webview.evaluateJavaScript_completionHandler_.assert_not_called()

    def test_executes_after_page_loaded(self):
        from wenzi.ui.vocab_manager_window import VocabManagerPanel

        panel = _build_panel(VocabManagerPanel())
        panel._eval_js("test()")

        calls = _get_js_calls(panel)
        assert "test()" in calls

    def test_flush_on_page_loaded(self):
        from wenzi.ui.vocab_manager_window import VocabManagerPanel

        panel = VocabManagerPanel()
        panel._webview = MagicMock()
        panel._page_loaded = False
        panel._eval_js("a()")
        panel._eval_js("b()")

        panel._on_page_loaded()

        assert panel._page_loaded is True
        assert panel._pending_js == []
        calls = _get_js_calls(panel)
        assert any("a();b()" in c for c in calls)

    def test_noop_without_webview(self):
        from wenzi.ui.vocab_manager_window import VocabManagerPanel

        panel = VocabManagerPanel()
        panel._eval_js("test()")  # Should not raise
        assert panel._pending_js == []


# ---------------------------------------------------------------------------
# Message handling
# ---------------------------------------------------------------------------


class TestMessageHandling:
    def _make_panel_with_callbacks(self):
        from wenzi.ui.vocab_manager_window import VocabManagerPanel

        panel = _build_panel(VocabManagerPanel())
        callbacks = {
            "on_page_ready": MagicMock(),
            "on_search": MagicMock(),
            "on_toggle_tags": MagicMock(),
            "on_change_page": MagicMock(),
            "on_sort": MagicMock(),
            "on_clear_filters": MagicMock(),
            "on_add": MagicMock(),
            "on_remove": MagicMock(),
            "on_batch_remove": MagicMock(),
            "on_edit": MagicMock(),
            "on_edit_field": MagicMock(),
            "on_export": MagicMock(),
            "on_import": MagicMock(),
        }
        panel._callbacks = callbacks
        return panel, callbacks

    def test_page_ready(self):
        panel, cbs = self._make_panel_with_callbacks()
        panel._handle_js_message({"type": "pageReady"})
        cbs["on_page_ready"].assert_called_once()

    def test_search(self):
        panel, cbs = self._make_panel_with_callbacks()
        panel._handle_js_message({"type": "search", "text": "test", "timeRange": "7d"})
        cbs["on_search"].assert_called_once_with("test", "7d")

    def test_toggle_tags(self):
        panel, cbs = self._make_panel_with_callbacks()
        panel._handle_js_message({"type": "toggleTags", "tags": ["asr", "user"]})
        cbs["on_toggle_tags"].assert_called_once_with(["asr", "user"])

    def test_change_page(self):
        panel, cbs = self._make_panel_with_callbacks()
        panel._handle_js_message({"type": "changePage", "page": 2})
        cbs["on_change_page"].assert_called_once_with(2)

    def test_sort(self):
        panel, cbs = self._make_panel_with_callbacks()
        panel._handle_js_message({"type": "sort", "column": "variant"})
        cbs["on_sort"].assert_called_once_with("variant")

    def test_clear_filters(self):
        panel, cbs = self._make_panel_with_callbacks()
        panel._handle_js_message({"type": "clearFilters"})
        cbs["on_clear_filters"].assert_called_once()

    def test_add_entry(self):
        panel, cbs = self._make_panel_with_callbacks()
        panel._handle_js_message({"type": "addEntry", "variant": "v", "term": "t", "source": "user"})
        cbs["on_add"].assert_called_once_with(
            "v", "t", "user",
            app_bundle_id="", asr_model="", llm_model="",
        )

    def test_add_entry_with_models(self):
        panel, cbs = self._make_panel_with_callbacks()
        panel._handle_js_message({
            "type": "addEntry", "variant": "v", "term": "t", "source": "asr",
            "app_bundle_id": "com.apple.dt.Xcode",
            "asr_model": "whisper-large-v3",
            "llm_model": "zai / glm-5",
        })
        cbs["on_add"].assert_called_once_with(
            "v", "t", "asr",
            app_bundle_id="com.apple.dt.Xcode",
            asr_model="whisper-large-v3",
            llm_model="zai / glm-5",
        )

    def test_edit_field(self):
        panel, cbs = self._make_panel_with_callbacks()
        panel._handle_js_message({
            "type": "editField", "variant": "v", "term": "t",
            "fields": {"source": "llm"},
        })
        cbs["on_edit_field"].assert_called_once_with("v", "t", {"source": "llm"})

    def test_edit_field_multiple(self):
        panel, cbs = self._make_panel_with_callbacks()
        panel._handle_js_message({
            "type": "editField", "variant": "v", "term": "t",
            "fields": {"asr_model": "whisper", "llm_model": "glm-5"},
        })
        cbs["on_edit_field"].assert_called_once_with(
            "v", "t", {"asr_model": "whisper", "llm_model": "glm-5"},
        )

    def test_remove_entry(self):
        panel, cbs = self._make_panel_with_callbacks()
        panel._handle_js_message({"type": "removeEntry", "variant": "v", "term": "t"})
        cbs["on_remove"].assert_called_once_with("v", "t")

    def test_batch_remove(self):
        panel, cbs = self._make_panel_with_callbacks()
        entries = [{"variant": "a", "term": "A"}]
        panel._handle_js_message({"type": "batchRemove", "entries": entries})
        cbs["on_batch_remove"].assert_called_once_with(entries)

    def test_edit_entry(self):
        panel, cbs = self._make_panel_with_callbacks()
        panel._handle_js_message({
            "type": "editEntry",
            "oldVariant": "ov", "oldTerm": "ot",
            "newVariant": "nv", "newTerm": "nt",
        })
        cbs["on_edit"].assert_called_once_with("ov", "ot", "nv", "nt")

    def test_export(self):
        panel, cbs = self._make_panel_with_callbacks()
        panel._handle_js_message({"type": "exportVocab"})
        cbs["on_export"].assert_called_once()

    def test_import(self):
        panel, cbs = self._make_panel_with_callbacks()
        panel._handle_js_message({"type": "importVocab"})
        cbs["on_import"].assert_called_once()

    def test_unknown_type_logs_warning(self, caplog):
        panel, _ = self._make_panel_with_callbacks()
        import logging
        with caplog.at_level(logging.WARNING):
            panel._handle_js_message({"type": "unknownType"})
        assert "Unknown vocab manager message type" in caplog.text

    def test_console_message(self):
        panel, _ = self._make_panel_with_callbacks()
        panel._handle_js_message({"type": "console", "level": "info", "message": "test log"})
        # Should not raise
