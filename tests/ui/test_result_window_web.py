"""Tests for the web-based result preview panel."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.conftest import mock_panel_close_delegate
from tests.ui._shared_result_window_tests import (
    SharedConfirmCancelTests,
    SharedEnhanceLabelTests,
    SharedModeSwitchTests,
    SharedModelChangeTests,
    SharedPropertyTests,
    SharedReplayCachedTests,
    SharedShowTests,
    SharedStreamingTests,
    SharedThreadingTests,
    SharedToggleTests,
)


@pytest.fixture(autouse=True)
def mock_appkit(mock_appkit_modules, monkeypatch):
    """Provide mock AppKit, Foundation, WebKit modules for headless testing."""
    mock_webkit = MagicMock()
    monkeypatch.setitem(sys.modules, "WebKit", mock_webkit)

    import wenzi.ui.result_window_web as _rww

    _rww._PanelCloseDelegate = None
    mock_panel_close_delegate(monkeypatch, _rww)

    # Mock message handler class
    mock_handler_cls = MagicMock()
    mock_handler_instance = MagicMock()
    mock_handler_cls.alloc.return_value.init.return_value = mock_handler_instance
    monkeypatch.setattr(_rww, "_get_message_handler_class", lambda: mock_handler_cls)

    mock_appkit_modules.appkit.NSCommandKeyMask = 1 << 20
    mock_appkit_modules.appkit.NSDeviceIndependentModifierFlagsMask = 0xFFFF0000

    return mock_appkit_modules


def _build_panel(panel):
    """Helper to set up a panel with mocked internals for testing."""
    panel._build_panel = MagicMock()
    panel._panel = MagicMock()
    panel._webview = MagicMock()
    # Simulate WKWebView page load completion so _eval_js works immediately
    panel._page_loaded = True
    return panel


# ---------------------------------------------------------------------------
# Shared test fixture for web panel
# ---------------------------------------------------------------------------


@pytest.fixture
def panel_factory():
    """Factory that returns a ready-to-test web ResultPreviewPanel."""

    def _factory():
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())

        def trigger_confirm(text, user_edited=False, enhance_text="", copy=False):
            panel._handle_js_message({
                "type": "confirm",
                "text": text,
                "enhanceText": enhance_text,
                "userEdited": user_edited,
                "copyToClipboard": copy,
            })

        def trigger_cancel():
            panel._handle_js_message({"type": "cancel"})

        def trigger_mode_change(index):
            panel._handle_js_message({"type": "modeChange", "index": index})

        def trigger_stt_change(index):
            panel._handle_js_message({"type": "sttModelChange", "index": index})

        def trigger_llm_change(index):
            panel._handle_js_message({"type": "llmModelChange", "index": index})

        def trigger_punc_toggle(enabled):
            panel._handle_js_message({"type": "puncToggle", "enabled": enabled})

        def trigger_thinking_toggle(enabled):
            panel._handle_js_message({"type": "thinkingToggle", "enabled": enabled})

        return SimpleNamespace(
            panel=panel,
            trigger_confirm=trigger_confirm,
            trigger_cancel=trigger_cancel,
            trigger_mode_change=trigger_mode_change,
            trigger_stt_change=trigger_stt_change,
            trigger_llm_change=trigger_llm_change,
            trigger_punc_toggle=trigger_punc_toggle,
            trigger_thinking_toggle=trigger_thinking_toggle,
        )

    return _factory


# ---------------------------------------------------------------------------
# Shared behavioral tests (run against web panel)
# ---------------------------------------------------------------------------


class TestWebShow(SharedShowTests):
    pass


class TestWebConfirmCancel(SharedConfirmCancelTests):
    pass


class TestWebModeSwitch(SharedModeSwitchTests):
    pass


class TestWebModelChange(SharedModelChangeTests):
    pass


class TestWebToggle(SharedToggleTests):
    pass


class TestWebStreaming(SharedStreamingTests):
    pass


class TestWebProperty(SharedPropertyTests):
    pass


class TestWebThreading(SharedThreadingTests):
    pass


class TestWebEnhanceLabel(SharedEnhanceLabelTests):
    pass


class TestWebReplayCached(SharedReplayCachedTests):
    pass


# ---------------------------------------------------------------------------
# Web-specific tests below
# ---------------------------------------------------------------------------


class TestShowBasic:
    """Test show() initializes state correctly."""

    def test_show_stores_callbacks(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        on_confirm = MagicMock()
        on_cancel = MagicMock()

        panel.show(
            asr_text="hello",
            show_enhance=False,
            on_confirm=on_confirm,
            on_cancel=on_cancel,
        )

        assert panel._on_confirm is on_confirm
        assert panel._on_cancel is on_cancel
        assert panel._asr_text == "hello"

    def test_show_stores_modes(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        modes = [("off", "Off"), ("proofread", "纠错")]

        panel.show(
            asr_text="text",
            show_enhance=True,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            available_modes=modes,
            current_mode="proofread",
        )

        assert panel._available_modes == modes
        assert panel._current_mode == "proofread"

    def test_show_stores_model_lists(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())

        panel.show(
            asr_text="text",
            show_enhance=True,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            stt_models=["whisper", "funASR"],
            stt_current_index=1,
            llm_models=["gpt-4", "claude"],
            llm_current_index=0,
        )

        assert panel._stt_models == ["whisper", "funASR"]
        assert panel._stt_current_index == 1
        assert panel._llm_models == ["gpt-4", "claude"]
        assert panel._llm_current_index == 0


class TestConfirmCancel:
    """Test confirm and cancel via JS messages."""

    def test_confirm_calls_callback_with_text(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        confirmed = []
        panel.show(
            asr_text="raw",
            show_enhance=False,
            on_confirm=lambda t, info=None, clipboard=False: confirmed.append(t),
            on_cancel=MagicMock(),
        )

        panel._handle_js_message({
            "type": "confirm",
            "text": "final text",
            "userEdited": False,
            "copyToClipboard": False,
        })

        assert confirmed == ["final text"]

    def test_confirm_with_copy_to_clipboard(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        clipboard_flags = []
        panel.show(
            asr_text="raw",
            show_enhance=False,
            on_confirm=lambda t, info=None, clipboard=False: clipboard_flags.append(clipboard),
            on_cancel=MagicMock(),
        )

        panel._handle_js_message({
            "type": "confirm",
            "text": "text",
            "userEdited": False,
            "copyToClipboard": True,
        })

        assert clipboard_flags == [True]

    def test_confirm_with_user_edit_sends_correction_info(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        results = []
        panel.show(
            asr_text="raw asr",
            show_enhance=True,
            on_confirm=lambda t, info=None, clipboard=False: results.append(info),
            on_cancel=MagicMock(),
        )

        panel._handle_js_message({
            "type": "confirm",
            "text": "user modified",
            "enhanceText": "enhanced",
            "userEdited": True,
            "copyToClipboard": False,
        })

        assert results[0] == {
            "asr_text": "raw asr",
            "enhanced_text": "enhanced",
            "final_text": "user modified",
        }

    def test_cancel_calls_callback(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        cancelled = []
        panel.show(
            asr_text="text",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=lambda: cancelled.append(True),
        )

        panel._handle_js_message({"type": "cancel"})

        assert cancelled == [True]


class TestModeChange:
    """Test mode switching via JS messages."""

    def test_mode_change_triggers_callback(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        mode_changes = []
        modes = [("off", "Off"), ("proofread", "纠错"), ("format", "格式")]

        panel.show(
            asr_text="text",
            show_enhance=True,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            available_modes=modes,
            current_mode="off",
            on_mode_change=lambda m: mode_changes.append(m),
        )

        panel._handle_js_message({"type": "modeChange", "index": 1})
        assert mode_changes == ["proofread"]

    def test_same_mode_does_not_trigger_callback(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        mode_changes = []
        modes = [("off", "Off"), ("proofread", "纠错")]

        panel.show(
            asr_text="text",
            show_enhance=True,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            available_modes=modes,
            current_mode="off",
            on_mode_change=lambda m: mode_changes.append(m),
        )

        panel._handle_js_message({"type": "modeChange", "index": 0})
        assert mode_changes == []


class TestModelChange:
    """Test STT/LLM model switching via JS messages."""

    def test_stt_model_change(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        changes = []

        panel.show(
            asr_text="text",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            stt_models=["a", "b"],
            on_stt_model_change=lambda i: changes.append(i),
        )

        panel._handle_js_message({"type": "sttModelChange", "index": 1})
        assert changes == [1]

    def test_llm_model_change(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        changes = []

        panel.show(
            asr_text="text",
            show_enhance=True,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            llm_models=["gpt-4", "claude"],
            on_llm_model_change=lambda i: changes.append(i),
        )

        panel._handle_js_message({"type": "llmModelChange", "index": 0})
        assert changes == [0]


class TestToggleCallbacks:
    """Test punc and thinking toggle callbacks."""

    def test_punc_toggle(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        toggles = []

        panel.show(
            asr_text="text",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            on_punc_toggle=lambda v: toggles.append(v),
        )

        panel._handle_js_message({"type": "puncToggle", "enabled": False})
        assert toggles == [False]
        assert panel._punc_enabled is False

    def test_thinking_toggle(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        toggles = []

        panel.show(
            asr_text="text",
            show_enhance=True,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            on_thinking_toggle=lambda v: toggles.append(v),
        )

        panel._handle_js_message({"type": "thinkingToggle", "enabled": True})
        assert toggles == [True]
        assert panel._thinking_enabled is True


class TestEnhanceStreaming:
    """Test streaming enhancement text updates."""

    def test_append_enhance_text_evals_js(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel.append_enhance_text("hello ", request_id=0, completion_tokens=10)

        panel._webview.evaluateJavaScript_completionHandler_.assert_called()
        calls = [
            c[0][0]
            for c in panel._webview.evaluateJavaScript_completionHandler_.call_args_list
        ]
        assert any("appendEnhanceText" in c for c in calls)

    def test_append_thinking_text_accumulates(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel.append_thinking_text("think1 ")
        panel.append_thinking_text("think2")

        assert panel._thinking_text == "think1 think2"

    def test_clear_enhance_text(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel.clear_enhance_text()

        calls = [
            c[0][0]
            for c in panel._webview.evaluateJavaScript_completionHandler_.call_args_list
        ]
        assert any("clearEnhanceText" in c for c in calls)

    def test_stale_request_discarded(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        panel._enhance_request_id = 5

        # Reset mock to track only this call
        panel._webview.evaluateJavaScript_completionHandler_.reset_mock()

        # Request with wrong id should be discarded
        panel.append_enhance_text("stale", request_id=3)

        panel._webview.evaluateJavaScript_completionHandler_.assert_not_called()


class TestSetEnhanceComplete:
    """Test set_enhance_complete."""

    def test_complete_with_usage(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel.set_enhance_complete(
            usage={"total_tokens": 100, "prompt_tokens": 60, "completion_tokens": 40},
            system_prompt="You are helpful.",
        )

        assert panel._system_prompt == "You are helpful."
        calls = [
            c[0][0]
            for c in panel._webview.evaluateJavaScript_completionHandler_.call_args_list
        ]
        assert any("setEnhanceComplete" in c for c in calls)

    def test_complete_enables_thinking_button_when_thinking_text_exists(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        panel._thinking_text = "some thinking"

        panel.set_enhance_complete()

        calls = [
            c[0][0]
            for c in panel._webview.evaluateJavaScript_completionHandler_.call_args_list
        ]
        # Should pass hasThinking=true
        complete_calls = [c for c in calls if "setEnhanceComplete" in c]
        assert len(complete_calls) > 0
        assert "true" in complete_calls[0]


class TestReplayCachedResult:
    """Test replay_cached_result."""

    def test_replay_sets_state(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel.replay_cached_result(
            display_text="cached text",
            usage={"total_tokens": 50, "prompt_tokens": 30, "completion_tokens": 20},
            system_prompt="sys prompt",
            thinking_text="thought",
            final_text="final",
        )

        assert panel._system_prompt == "sys prompt"
        assert panel._thinking_text == "thought"
        calls = [
            c[0][0]
            for c in panel._webview.evaluateJavaScript_completionHandler_.call_args_list
        ]
        assert any("replayCachedResult" in c for c in calls)


class TestSetEnhanceOff:
    """Test set_enhance_off."""

    def test_off_calls_js(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel.show(
            asr_text="original", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel.set_enhance_off()

        calls = [
            c[0][0]
            for c in panel._webview.evaluateJavaScript_completionHandler_.call_args_list
        ]
        assert any("setEnhanceOff" in c for c in calls)
        assert panel._show_enhance is False


class TestSetAsrResult:
    """Test ASR result update."""

    def test_asr_result_updates_state(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel.show(
            asr_text="old", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel.set_asr_result("new text", "model info")

        assert panel._asr_text == "new text"
        assert panel._asr_info == "model info"

    def test_stale_asr_result_discarded(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel.show(
            asr_text="old", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )
        panel._asr_request_id = 3

        panel._webview.evaluateJavaScript_completionHandler_.reset_mock()
        panel.set_asr_result("stale", request_id=1)

        panel._webview.evaluateJavaScript_completionHandler_.assert_not_called()
        assert panel._asr_text == "old"


class TestProperties:
    """Test properties and visibility."""

    def test_is_visible(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        assert panel.is_visible is False

        panel._panel = MagicMock()
        panel._panel.isVisible.return_value = True
        assert panel.is_visible is True

    def test_enhance_request_id_property(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel.enhance_request_id = 42
        assert panel.enhance_request_id == 42

    def test_asr_request_id_increments_on_loading(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        initial_id = panel.asr_request_id
        panel.set_asr_loading()
        assert panel.asr_request_id == initial_id + 1


class TestClose:
    """Test panel close."""

    def test_close_clears_state(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel.close()

        assert panel._panel is None
        assert panel._webview is None
        assert panel._on_confirm is None
        assert panel._on_cancel is None

    def test_close_without_show_is_noop(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel.close()  # Should not raise


class TestHtmlTemplate:
    """Test HTML template generation."""

    def _template(self):
        from wenzi.ui.templates import load_template

        return load_template("result_window_web.html")

    def test_config_placeholder_present(self):
        assert "__CONFIG__" in self._template()

    def test_html_has_dark_mode_support(self):
        assert "prefers-color-scheme: dark" in self._template()

    def test_html_has_key_ui_elements(self):
        html = self._template()
        for element_id in ("asr-text", "enhance-text", "final-text",
                           "mode-segment", "confirm-btn", "cancel-btn"):
            assert element_id in html

    def test_html_has_keyboard_shortcuts(self):
        html = self._template()
        assert "Escape" in html
        assert "metaKey" in html


class TestEnhanceLabelText:
    """Test _enhance_label_text helper."""

    def test_with_llm_models_returns_suffix_only(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._llm_models = ["gpt-4"]
        assert panel._enhance_label_text("Tokens: 100") == "Tokens: 100"

    def test_without_llm_models_includes_ai_prefix(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_info = "openai/gpt-4"
        assert panel._enhance_label_text("ok") == "AI (openai/gpt-4)  ok"

    def test_empty_suffix(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        assert panel._enhance_label_text() == "AI"


class TestFormatTokenSuffix:
    """Test _format_token_suffix helper."""

    def test_none_usage(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        assert ResultPreviewPanel._format_token_suffix(None) == ""

    def test_empty_usage(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        assert ResultPreviewPanel._format_token_suffix({}) == ""

    def test_valid_usage(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        result = ResultPreviewPanel._format_token_suffix({
            "total_tokens": 1000,
            "prompt_tokens": 600,
            "completion_tokens": 400,
        })
        assert "1,000" in result
        assert "\u2191600" in result
        assert "\u2193400" in result

    def test_with_cache_tokens(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        result = ResultPreviewPanel._format_token_suffix({
            "total_tokens": 1000,
            "prompt_tokens": 600,
            "completion_tokens": 400,
            "cache_read_tokens": 200,
        })
        assert "1,000" in result
        assert "\u2191200+400" in result
        assert "\u2193400" in result

    def test_with_zero_cache_tokens(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        result = ResultPreviewPanel._format_token_suffix({
            "total_tokens": 1000,
            "prompt_tokens": 600,
            "completion_tokens": 400,
            "cache_read_tokens": 0,
        })
        assert "\u2191600" in result
        assert "+" not in result


class TestBrowseHistoryAndTranslate:
    """Test history and translate JS actions."""

    def test_browse_history(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        history_called = []
        panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
            on_select_history=lambda idx: history_called.append(idx),
        )

        panel._handle_js_message({"type": "selectHistory", "index": 2})
        assert history_called == [2]

    def test_google_translate(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        translate_called = []
        panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
            on_google_translate=lambda: translate_called.append(True),
        )

        # Pre-set a mock translate webview to avoid real import
        mock_twp = MagicMock()
        panel._translate_webview = mock_twp

        panel._handle_js_message({"type": "googleTranslate", "text": "hello"})

        mock_twp.show.assert_called_once_with("hello")
        assert translate_called == [True]


# ---------------------------------------------------------------------------
# JS call queue (page load race condition fix)
# ---------------------------------------------------------------------------


class TestJsCallQueue:
    """Tests for the JS call queue that prevents calls being dropped before
    WKWebView finishes loading."""

    def test_eval_js_queued_before_page_load(self):
        """JS calls made before page load should be queued, not executed."""
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._build_panel = MagicMock()
        panel._panel = MagicMock()
        panel._webview = MagicMock()
        # _page_loaded defaults to False
        panel._page_loaded = False

        panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel._eval_js("setAsrResult('hello','')")

        # Should NOT have been called on webview directly
        panel._webview.evaluateJavaScript_completionHandler_.assert_not_called()
        assert len(panel._pending_js) == 1
        assert panel._pending_js[0] == "setAsrResult('hello','')"

    def test_pending_js_flushed_on_page_load(self):
        """Queued JS calls should be flushed when page finishes loading."""
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._build_panel = MagicMock()
        panel._panel = MagicMock()
        panel._webview = MagicMock()
        panel._page_loaded = False

        panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel._eval_js("setAsrResult('a','')")
        panel._eval_js("setEnhanceResult('b')")

        assert len(panel._pending_js) == 2

        # Simulate page load completion
        panel._on_page_loaded()

        assert panel._page_loaded is True
        assert len(panel._pending_js) == 0
        # Pending JS is flushed as a single combined call to guarantee order
        panel._webview.evaluateJavaScript_completionHandler_.assert_called_once()
        combined_js = panel._webview.evaluateJavaScript_completionHandler_.call_args[0][0]
        assert "setAsrResult('a','')" in combined_js
        assert "setEnhanceResult('b')" in combined_js

    def test_eval_js_direct_after_page_load(self):
        """JS calls after page load should execute immediately."""
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())  # _page_loaded = True
        panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel._webview.reset_mock()
        panel._eval_js("someCall()")

        panel._webview.evaluateJavaScript_completionHandler_.assert_called_once()
        assert len(panel._pending_js) == 0

    def test_close_clears_pending_js(self):
        """Closing the panel should discard any pending JS calls."""
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._build_panel = MagicMock()
        panel._panel = MagicMock()
        panel._webview = MagicMock()
        panel._page_loaded = False

        panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel._eval_js("someCall()")
        assert len(panel._pending_js) == 1

        panel.close()

        assert panel._page_loaded is False
        assert len(panel._pending_js) == 0

    def test_flush_order_preserved(self):
        """Queued JS calls must be flushed in the order they were added."""
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._build_panel = MagicMock()
        panel._panel = MagicMock()
        panel._webview = MagicMock()
        panel._page_loaded = False

        panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel._eval_js("first()")
        panel._eval_js("second()")
        panel._eval_js("third()")

        panel._on_page_loaded()

        # All pending JS is combined into a single call to guarantee order
        panel._webview.evaluateJavaScript_completionHandler_.assert_called_once()
        combined = panel._webview.evaluateJavaScript_completionHandler_.call_args[0][0]
        assert combined == "first();second();third()"


# ---------------------------------------------------------------------------
# Playback toggle tests
# ---------------------------------------------------------------------------


class TestPlaybackToggle:
    """Tests for Play/Stop toggle button behaviour."""

    def test_toggle_audio_starts_playback_when_not_playing(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel._asr_wav_data = b"fake-wav"
        panel._play_wav = MagicMock()

        panel._handle_js_message({"type": "toggleAudio"})

        panel._play_wav.assert_called_once_with(b"fake-wav")

    def test_toggle_audio_stops_playback_when_playing(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel._asr_wav_data = b"fake-wav"
        mock_sound = MagicMock()
        mock_sound.isPlaying.return_value = True
        panel._asr_sound = mock_sound
        panel._stop_playback = MagicMock()

        panel._handle_js_message({"type": "toggleAudio"})

        panel._stop_playback.assert_called_once()

    def test_toggle_audio_noop_when_no_wav_data(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel._asr_wav_data = None

        # Should not raise
        panel._handle_js_message({"type": "toggleAudio"})

    def test_stop_playback_clears_sound_and_timer(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        mock_sound = MagicMock()
        mock_timer = MagicMock()
        panel._asr_sound = mock_sound
        panel._playback_timer = mock_timer

        panel._stop_playback()

        mock_sound.stop.assert_called_once()
        mock_timer.invalidate.assert_called_once()
        assert panel._asr_sound is None
        assert panel._playback_timer is None

    def test_stop_playback_handles_no_sound(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel._asr_sound = None
        panel._playback_timer = None

        # Should not raise
        panel._stop_playback()

    def test_tick_playback_timer_stops_when_finished(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        mock_sound = MagicMock()
        mock_sound.isPlaying.return_value = False
        panel._asr_sound = mock_sound
        mock_timer = MagicMock()
        panel._playback_timer = mock_timer

        panel.tickPlaybackTimer_(None)

        mock_timer.invalidate.assert_called_once()
        assert panel._asr_sound is None

    def test_tick_playback_timer_noop_when_still_playing(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        mock_sound = MagicMock()
        mock_sound.isPlaying.return_value = True
        panel._asr_sound = mock_sound
        mock_timer = MagicMock()
        panel._playback_timer = mock_timer

        panel.tickPlaybackTimer_(None)

        # Should not stop
        mock_timer.invalidate.assert_not_called()
        assert panel._asr_sound is mock_sound

    def test_close_stops_playback(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        mock_sound = MagicMock()
        mock_timer = MagicMock()
        panel._asr_sound = mock_sound
        panel._playback_timer = mock_timer

        panel.close()

        mock_sound.stop.assert_called_once()
        mock_timer.invalidate.assert_called_once()
        assert panel._asr_sound is None
        assert panel._playback_timer is None

    def test_show_stops_existing_playback(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        mock_sound = MagicMock()
        mock_timer = MagicMock()
        panel._asr_sound = mock_sound
        panel._playback_timer = mock_timer

        panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        mock_sound.stop.assert_called_once()
        mock_timer.invalidate.assert_called_once()

    def test_playback_timer_initialized_in_init(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        assert panel._playback_timer is None


# ---------------------------------------------------------------------------
# Hotwords display tests
# ---------------------------------------------------------------------------


class TestBuildHotwordsHtml:
    def _make_detail(self, **kwargs):
        from wenzi.enhance.vocabulary import HotwordDetail

        defaults = dict(
            term="API", variant="a p i", source="asr",
            hit_count=5, last_hit="", first_seen="",
        )
        defaults.update(kwargs)
        return HotwordDetail(**defaults)

    def test_returns_valid_html(self):
        from wenzi.ui.result_window_web import _build_hotwords_html

        details = [self._make_detail()]
        html = _build_hotwords_html(details)
        assert "<!DOCTYPE html>" in html
        assert "<table>" in html
        assert "API" in html

    def test_columns_present(self):
        from wenzi.ui.result_window_web import _build_hotwords_html

        details = [self._make_detail(
            term="Claude", variant="克劳德", source="llm", hit_count=3,
        )]
        html = _build_hotwords_html(details)
        assert "Claude" in html
        assert "克劳德" in html  # variant
        assert "llm" in html  # source
        assert ">3<" in html  # hit_count

    def test_html_escaping(self):
        from wenzi.ui.result_window_web import _build_hotwords_html

        details = [self._make_detail(term="<script>", variant="a&b")]
        html = _build_hotwords_html(details)
        # The term should be escaped in the table body
        assert "&lt;script&gt;" in html
        assert "a&amp;b" in html

    def test_multiple_rows(self):
        from wenzi.ui.result_window_web import _build_hotwords_html

        details = [
            self._make_detail(term="API"),
            self._make_detail(term="gRPC"),
        ]
        html = _build_hotwords_html(details)
        assert "API" in html
        assert "gRPC" in html

    def test_empty_list(self):
        from wenzi.ui.result_window_web import _build_hotwords_html

        html = _build_hotwords_html([])
        assert "<tbody>" in html
        assert "<tr" not in html.split("<tbody>")[1].split("</tbody>")[0] or \
               html.count("<tr") == 1  # only header row

    def test_time_rendered_via_js(self):
        from wenzi.ui.result_window_web import _build_hotwords_html

        details = [self._make_detail(
            last_hit="2024-06-01T12:00:00", first_seen="2024-01-01T00:00:00",
        )]
        html = _build_hotwords_html(details)
        # Times are rendered client-side via JS fmtDate
        assert "data-ts" in html
        assert "2024-06-01T12:00:00" in html
        assert "2024-01-01T00:00:00" in html
        assert "fmtDate" in html

    def test_dark_mode_css(self):
        from wenzi.ui.result_window_web import _build_hotwords_html

        html = _build_hotwords_html([self._make_detail()])
        assert "prefers-color-scheme: dark" in html

    def test_with_context_text(self):
        from wenzi.ui.result_window_web import _build_hotwords_html

        details = [self._make_detail()]
        html = _build_hotwords_html(details, context_text="App:  iTerm2")
        assert "iTerm2" in html
        assert "ctx-key" in html
        assert "<table>" in html
        # Context appears before the table
        assert html.index("iTerm2") < html.index("<table>")

    def test_without_context_no_context_rendered(self):
        from wenzi.ui.result_window_web import _build_hotwords_html

        html = _build_hotwords_html([self._make_detail()])
        assert '<div class="section">' not in html

    def test_context_html_escaping(self):
        from wenzi.ui.result_window_web import _build_hotwords_html

        html = _build_hotwords_html([], context_text="App:  <script>xss</script>")
        assert "&lt;script&gt;" in html


class TestBuildContextSectionHtml:
    def test_empty_returns_empty(self):
        from wenzi.ui.result_window_web import _build_context_section_html

        assert _build_context_section_html("") == ""

    def test_key_value_format(self):
        from wenzi.ui.result_window_web import _build_context_section_html

        result = _build_context_section_html("App:  iTerm2\nWindow:  ~/work")
        assert "ctx-key" in result
        assert "App" in result
        assert "iTerm2" in result
        assert "~/work" in result

    def test_plain_line(self):
        from wenzi.ui.result_window_web import _build_context_section_html

        result = _build_context_section_html("some plain text")
        assert "ctx-val" in result
        assert "some plain text" in result
        assert "ctx-key" not in result

    def test_html_escaping(self):
        from wenzi.ui.result_window_web import _build_context_section_html

        result = _build_context_section_html("App:  <b>bad</b>")
        assert "&lt;b&gt;" in result


class TestBuildContextPanelHtml:
    def _make_entry(self, **kwargs):
        from wenzi.enhance.manual_vocabulary import ManualVocabEntry

        defaults = dict(term="API", variant="a p i")
        defaults.update(kwargs)
        return ManualVocabEntry(**defaults)

    def test_context_only(self):
        from wenzi.ui.result_window_web import _build_context_panel_html

        html = _build_context_panel_html("App:  iTerm2\nWindow:  zsh", [])
        assert "iTerm2" in html
        assert "<!DOCTYPE html>" in html
        assert "<table>" not in html

    def test_context_key_value_format(self):
        from wenzi.ui.result_window_web import _build_context_panel_html

        ctx = "App:      iTerm2\nWindow:   ~/work\nElement:  AXTextArea"
        html = _build_context_panel_html(ctx, [])
        assert "ctx-key" in html
        assert "ctx-val" in html
        assert "App" in html
        assert "iTerm2" in html
        assert "~/work" in html
        assert "AXTextArea" in html

    def test_vocab_only(self):
        from wenzi.ui.result_window_web import _build_context_panel_html

        entries = [self._make_entry(term="Claude", variant="克劳德")]
        html = _build_context_panel_html("", entries)
        assert "Claude" in html
        assert "克劳德" in html
        assert "<table>" in html

    def test_both_sections(self):
        from wenzi.ui.result_window_web import _build_context_panel_html

        entries = [self._make_entry()]
        html = _build_context_panel_html("App:  iTerm2", entries)
        assert "iTerm2" in html
        assert "<table>" in html
        # Context should appear before vocab table
        ctx_pos = html.index("iTerm2")
        table_pos = html.index("<table>")
        assert ctx_pos < table_pos

    def test_html_escaping(self):
        from wenzi.ui.result_window_web import _build_context_panel_html

        entries = [self._make_entry(term="<b>bold</b>", variant="a&b")]
        html = _build_context_panel_html("App:  <script>alert(1)</script>", entries)
        assert "&lt;script&gt;" in html
        assert "&lt;b&gt;bold&lt;/b&gt;" in html
        assert "a&amp;b" in html

    def test_time_rendered_via_js(self):
        from wenzi.ui.result_window_web import _build_context_panel_html

        entries = [self._make_entry(last_hit="2024-06-01T00:00:00")]
        html = _build_context_panel_html("", entries)
        assert "data-ts" in html
        assert "fmtDate" in html

    def test_dark_mode_css(self):
        from wenzi.ui.result_window_web import _build_context_panel_html

        html = _build_context_panel_html("test", [])
        assert "prefers-color-scheme: dark" in html

    def test_vocab_count_in_title(self):
        from wenzi.ui.result_window_web import _build_context_panel_html

        entries = [self._make_entry(), self._make_entry(term="B", variant="b")]
        html = _build_context_panel_html("", entries)
        assert "(2)" in html


class TestSetLlmVocab:
    def test_caches_entries(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        entries = [MagicMock(term="API")]
        panel.set_llm_vocab(entries)
        assert len(panel._llm_vocab_detail) == 1

    def test_init_has_empty_llm_vocab(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        assert panel._llm_vocab_detail == []


class TestSetHotwords:
    def test_caches_details(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        details = [MagicMock(term="API")]
        panel.set_hotwords(details)
        assert panel._hotwords_detail == details

    def test_caches_multiple_details(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        details = [MagicMock(), MagicMock()]
        panel.set_hotwords(details)
        assert len(panel._hotwords_detail) == 2

    def test_init_has_empty_hotwords(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        assert panel._hotwords_detail == []


class TestShowHotwordsAction:
    def test_action_triggers_panel(self, panel_factory):
        ns = panel_factory()
        panel = ns.panel
        detail = MagicMock()
        detail.term = "API"
        panel._hotwords_detail = [detail]
        # Patch _show_hotwords_panel to verify it's called
        panel._show_hotwords_panel = MagicMock()
        panel._handle_js_message({"type": "showHotwords"})
        panel._show_hotwords_panel.assert_called_once_with([detail])

    def test_action_noop_when_empty(self, panel_factory):
        ns = panel_factory()
        panel = ns.panel
        panel._hotwords_detail = []
        panel._show_hotwords_panel = MagicMock()
        panel._handle_js_message({"type": "showHotwords"})
        panel._show_hotwords_panel.assert_not_called()


# ---------------------------------------------------------------------------
# Screen selection tests
# ---------------------------------------------------------------------------


def _make_mock_screen(x, y, w, h):
    """Create a mock NSScreen with the given frame geometry."""
    screen = MagicMock()
    frame = MagicMock()
    frame.origin.x = x
    frame.origin.y = y
    frame.size.width = w
    frame.size.height = h
    screen.frame.return_value = frame
    return screen


class TestScreenForMouse:
    """Test _screen_for_mouse returns the screen containing the cursor."""

    @pytest.fixture(autouse=True)
    def _patch_appkit(self, monkeypatch):
        self._mock_ns_screen = MagicMock()
        self._mock_ns_event = MagicMock()
        monkeypatch.setattr(sys.modules["AppKit"], "NSScreen", self._mock_ns_screen)
        monkeypatch.setattr(sys.modules["AppKit"], "NSEvent", self._mock_ns_event)
        monkeypatch.setattr(
            sys.modules["Foundation"], "NSPointInRect",
            lambda pt, frame: (
                frame.origin.x <= pt.x < frame.origin.x + frame.size.width
                and frame.origin.y <= pt.y < frame.origin.y + frame.size.height
            ),
        )

    def _set_mouse(self, x, y):
        pt = MagicMock()
        pt.x = x
        pt.y = y
        self._mock_ns_event.mouseLocation.return_value = pt

    def test_returns_screen_containing_mouse(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        left = _make_mock_screen(0, 0, 1920, 1080)
        right = _make_mock_screen(1920, 0, 1920, 1080)
        self._mock_ns_screen.screens.return_value = [left, right]
        self._set_mouse(2500, 500)

        assert ResultPreviewPanel._screen_for_mouse() is right

    def test_fallback_to_main_screen(self):
        from wenzi.ui.result_window_web import ResultPreviewPanel

        main = MagicMock()
        self._mock_ns_screen.screens.return_value = []
        self._mock_ns_screen.mainScreen.return_value = main
        self._set_mouse(100, 100)

        assert ResultPreviewPanel._screen_for_mouse() is main
