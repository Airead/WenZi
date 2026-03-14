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

    import voicetext.ui.result_window_web as _rww

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
    return panel


# ---------------------------------------------------------------------------
# Shared test fixture for web panel
# ---------------------------------------------------------------------------


@pytest.fixture
def panel_factory():
    """Factory that returns a ready-to-test web ResultPreviewPanel."""

    def _factory():
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel.show(
            asr_text="text", show_enhance=True,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel.append_thinking_text("think1 ")
        panel.append_thinking_text("think2")

        assert panel._thinking_text == "think1 think2"

    def test_clear_enhance_text(self):
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        panel.show(
            asr_text="old", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
        )

        panel.set_asr_result("new text", "model info")

        assert panel._asr_text == "new text"
        assert panel._asr_info == "model info"

    def test_stale_asr_result_discarded(self):
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        assert panel.is_visible is False

        panel._panel = MagicMock()
        panel._panel.isVisible.return_value = True
        assert panel.is_visible is True

    def test_enhance_request_id_property(self):
        from voicetext.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel.enhance_request_id = 42
        assert panel.enhance_request_id == 42

    def test_asr_request_id_increments_on_loading(self):
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
        from voicetext.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel.close()  # Should not raise


class TestHtmlTemplate:
    """Test HTML template generation."""

    def test_config_placeholder_replaced(self):
        from voicetext.ui.result_window_web import _HTML_TEMPLATE

        assert "__CONFIG__" in _HTML_TEMPLATE

    def test_html_has_dark_mode_support(self):
        from voicetext.ui.result_window_web import _HTML_TEMPLATE

        assert "prefers-color-scheme: dark" in _HTML_TEMPLATE

    def test_html_has_key_ui_elements(self):
        from voicetext.ui.result_window_web import _HTML_TEMPLATE

        for element_id in ("asr-text", "enhance-text", "final-text",
                           "mode-segment", "confirm-btn", "cancel-btn"):
            assert element_id in _HTML_TEMPLATE

    def test_html_has_keyboard_shortcuts(self):
        from voicetext.ui.result_window_web import _HTML_TEMPLATE

        assert "Escape" in _HTML_TEMPLATE
        assert "metaKey" in _HTML_TEMPLATE


class TestEnhanceLabelText:
    """Test _enhance_label_text helper."""

    def test_with_llm_models_returns_suffix_only(self):
        from voicetext.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._llm_models = ["gpt-4"]
        assert panel._enhance_label_text("Tokens: 100") == "Tokens: 100"

    def test_without_llm_models_includes_ai_prefix(self):
        from voicetext.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_info = "openai/gpt-4"
        assert panel._enhance_label_text("ok") == "AI (openai/gpt-4)  ok"

    def test_empty_suffix(self):
        from voicetext.ui.result_window_web import ResultPreviewPanel

        panel = ResultPreviewPanel()
        assert panel._enhance_label_text() == "AI"


class TestFormatTokenSuffix:
    """Test _format_token_suffix helper."""

    def test_none_usage(self):
        from voicetext.ui.result_window_web import ResultPreviewPanel

        assert ResultPreviewPanel._format_token_suffix(None) == ""

    def test_empty_usage(self):
        from voicetext.ui.result_window_web import ResultPreviewPanel

        assert ResultPreviewPanel._format_token_suffix({}) == ""

    def test_valid_usage(self):
        from voicetext.ui.result_window_web import ResultPreviewPanel

        result = ResultPreviewPanel._format_token_suffix({
            "total_tokens": 1000,
            "prompt_tokens": 600,
            "completion_tokens": 400,
        })
        assert "1,000" in result
        assert "600" in result
        assert "400" in result


class TestBrowseHistoryAndTranslate:
    """Test history and translate JS actions."""

    def test_browse_history(self):
        from voicetext.ui.result_window_web import ResultPreviewPanel

        panel = _build_panel(ResultPreviewPanel())
        history_called = []
        panel.show(
            asr_text="text", show_enhance=False,
            on_confirm=MagicMock(), on_cancel=MagicMock(),
            on_browse_history=lambda: history_called.append(True),
        )

        panel._handle_js_message({"type": "browseHistory"})
        assert history_called == [True]

    def test_google_translate(self):
        from voicetext.ui.result_window_web import ResultPreviewPanel

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
