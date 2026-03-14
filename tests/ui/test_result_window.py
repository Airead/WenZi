"""Tests for the result preview panel."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_appkit(mock_appkit_modules):
    """Provide mock AppKit and Foundation modules for headless testing."""
    # Set real integer values for keyboard event constants
    mock_appkit_modules.appkit.NSCommandKeyMask = 1 << 20
    mock_appkit_modules.appkit.NSShiftKeyMask = 1 << 17
    mock_appkit_modules.appkit.NSDeviceIndependentModifierFlagsMask = 0xFFFF0000
    mock_appkit_modules.appkit.NSKeyDownMask = 1 << 10


def _setup_panel_with_final_field(panel):
    """Set up a panel with mocked _final_text_field for testing."""
    panel._build_panel = MagicMock()
    panel._panel = MagicMock()
    panel._final_text_field = MagicMock()
    return panel


class TestResultPreviewPanelCallbacks:
    """Test confirm/cancel callback mechanism."""

    def test_confirm_triggers_callback_with_text(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        panel._final_text_field.stringValue.return_value = "final text"
        confirmed_text = []

        panel.show(
            asr_text="raw asr",
            show_enhance=False,
            on_confirm=lambda t, info=None, clipboard=False: confirmed_text.append(t),
            on_cancel=MagicMock(),
        )

        panel.confirmClicked_(None)

        assert confirmed_text == ["final text"]

    def test_confirm_with_cmd_held_passes_clipboard_flag(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        panel._final_text_field.stringValue.return_value = "clipboard text"
        results = []

        panel.show(
            asr_text="raw asr",
            show_enhance=False,
            on_confirm=lambda t, info=None, clipboard=False: results.append(
                (t, clipboard)
            ),
            on_cancel=MagicMock(),
        )

        # Simulate Command key held
        panel._cmd_held = True
        panel.confirmClicked_(None)

        assert results == [("clipboard text", True)]

    def test_confirm_without_cmd_passes_false(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        panel._final_text_field.stringValue.return_value = "normal text"
        results = []

        panel.show(
            asr_text="raw asr",
            show_enhance=False,
            on_confirm=lambda t, info=None, clipboard=False: results.append(
                (t, clipboard)
            ),
            on_cancel=MagicMock(),
        )

        panel.confirmClicked_(None)

        assert results == [("normal text", False)]

    def test_flags_changed_updates_button_title(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        panel._confirm_btn = MagicMock()
        panel._panel.isKeyWindow.return_value = True

        # Simulate Command key press
        event = MagicMock()
        event.modifierFlags.return_value = 1 << 20  # NSCommandKeyMask
        panel._handle_flags_changed(event)

        panel._confirm_btn.setTitle_.assert_called_with("Copy \u2318\u23ce")
        assert panel._cmd_held is True

        # Simulate Command key release
        event.modifierFlags.return_value = 0
        panel._handle_flags_changed(event)

        panel._confirm_btn.setTitle_.assert_called_with("Confirm \u23ce")
        assert panel._cmd_held is False

    def test_cancel_triggers_callback(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._build_panel = MagicMock()
        panel._panel = MagicMock()
        cancelled = []

        panel.show(
            asr_text="raw asr",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=lambda: cancelled.append(True),
        )

        panel.cancelClicked_(None)

        assert cancelled == [True]

    def test_confirm_closes_panel(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        panel._final_text_field.stringValue.return_value = "text"
        mock_panel = panel._panel

        panel.show(
            asr_text="asr",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
        )

        panel.confirmClicked_(None)

        mock_panel.orderOut_.assert_called_once_with(None)

    def test_cancel_closes_panel(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._build_panel = MagicMock()
        mock_panel = MagicMock()
        panel._panel = mock_panel

        panel.show(
            asr_text="asr",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
        )

        panel.cancelClicked_(None)

        mock_panel.orderOut_.assert_called_once_with(None)


class TestResultPreviewPanelCorrectionInfo:
    """Test correction_info passed via on_confirm callback."""

    def test_correction_info_when_user_edited_with_enhance(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        panel._final_text_field.stringValue.return_value = "user corrected"
        panel._enhance_text_view = MagicMock()
        panel._enhance_text_view.string.return_value = "ai enhanced"

        results = []

        panel.show(
            asr_text="raw asr",
            show_enhance=True,
            on_confirm=lambda text, info, clipboard=False: results.append((text, info)),
            on_cancel=MagicMock(),
        )

        # Simulate user editing
        panel._on_user_edit()
        panel.confirmClicked_(None)

        assert len(results) == 1
        text, info = results[0]
        assert text == "user corrected"
        assert info is not None
        assert info["asr_text"] == "raw asr"
        assert info["enhanced_text"] == "ai enhanced"
        assert info["final_text"] == "user corrected"

    def test_correction_info_none_when_not_edited(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        panel._final_text_field.stringValue.return_value = "text"
        panel._enhance_text_view = MagicMock()

        results = []

        panel.show(
            asr_text="raw asr",
            show_enhance=True,
            on_confirm=lambda text, info, clipboard=False: results.append((text, info)),
            on_cancel=MagicMock(),
        )

        # No user edit
        panel.confirmClicked_(None)

        assert results[0][1] is None

    def test_correction_info_none_when_no_enhance(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        panel._final_text_field.stringValue.return_value = "text"

        results = []

        panel.show(
            asr_text="raw asr",
            show_enhance=False,
            on_confirm=lambda text, info, clipboard=False: results.append((text, info)),
            on_cancel=MagicMock(),
        )

        panel._on_user_edit()
        panel.confirmClicked_(None)

        assert results[0][1] is None


class TestResultPreviewPanelEnhanceUpdate:
    """Test AI enhancement result update logic."""

    def test_set_enhance_result_updates_text_when_not_edited(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._user_edited = False
        panel._enhance_text_view = MagicMock()
        panel._enhance_label = MagicMock()
        panel._final_text_field = MagicMock()

        # Simulate callAfter executing immediately
        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_result("enhanced text")

        panel._enhance_text_view.setString_.assert_called_with("enhanced text")
        panel._final_text_field.setStringValue_.assert_called_with("enhanced text")

    def test_set_enhance_result_skips_final_when_user_edited(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._user_edited = True
        panel._enhance_text_view = MagicMock()
        panel._enhance_label = MagicMock()
        panel._final_text_field = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_result("enhanced text")

        panel._enhance_text_view.setString_.assert_called_with("enhanced text")
        panel._final_text_field.setStringValue_.assert_not_called()

    def test_set_enhance_result_updates_label(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._user_edited = False
        panel._enhance_text_view = MagicMock()
        panel._enhance_label = MagicMock()
        panel._final_text_field = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_result("done")

        panel._enhance_label.setStringValue_.assert_called_with("AI")

    def test_set_enhance_result_shows_token_usage(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._user_edited = False
        panel._enhance_text_view = MagicMock()
        panel._enhance_label = MagicMock()
        panel._final_text_field = MagicMock()
        panel._enhance_info = "ollama / qwen2.5:7b"

        usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_result("done", usage=usage)

        panel._enhance_label.setStringValue_.assert_called_with(
            "AI (ollama / qwen2.5:7b)  Tokens: 120 (\u2191100 \u219320)"
        )

    def test_set_enhance_result_no_usage_no_suffix(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._user_edited = False
        panel._enhance_text_view = MagicMock()
        panel._enhance_label = MagicMock()
        panel._final_text_field = MagicMock()
        panel._enhance_info = "ollama / qwen2.5:7b"

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_result("done", usage=None)

        panel._enhance_label.setStringValue_.assert_called_with(
            "AI (ollama / qwen2.5:7b)"
        )

    def test_set_enhance_result_noop_when_no_enhance_view(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_text_view = None

        # Should not raise
        panel.set_enhance_result("text")


class TestResultPreviewPanelKeyHandling:
    """Test that Enter confirms and the text field uses NSTextField behavior."""

    def test_enter_triggers_confirm_via_button_key_equivalent(self):
        """NSTextField does not consume Enter, so the confirm button's
        keyEquivalent (\\r) fires directly. Verify confirmClicked_ works."""
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        panel._final_text_field.stringValue.return_value = "text"

        confirmed = []
        panel.show(
            asr_text="asr",
            show_enhance=False,
            on_confirm=lambda t, info=None, clipboard=False: confirmed.append(t),
            on_cancel=MagicMock(),
        )

        panel.confirmClicked_(None)
        assert confirmed == ["text"]


class TestResultPreviewPanelUserEdit:
    """Test user edit tracking."""

    def test_user_edit_flag_set_on_edit(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        assert panel._user_edited is False

        panel._on_user_edit()

        assert panel._user_edited is True

    def test_user_edit_flag_reset_on_show(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._user_edited = True
        panel._build_panel = MagicMock()
        panel._panel = MagicMock()

        panel.show(
            asr_text="text",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
        )

        assert panel._user_edited is False


class TestResultPreviewPanelVisibility:
    """Test is_visible and bring_to_front."""

    def test_is_visible_true_when_panel_visible(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._panel = MagicMock()
        panel._panel.isVisible.return_value = True

        assert panel.is_visible is True

    def test_is_visible_false_when_panel_none(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        assert panel.is_visible is False

    def test_is_visible_false_when_panel_hidden(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._panel = MagicMock()
        panel._panel.isVisible.return_value = False

        assert panel.is_visible is False

    def test_bring_to_front_when_visible(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._panel = MagicMock()
        panel._panel.isVisible.return_value = True

        panel.bring_to_front()

        panel._panel.makeKeyAndOrderFront_.assert_called_once_with(None)

    def test_bring_to_front_noop_when_no_panel(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        # Should not raise
        panel.bring_to_front()

    def test_bring_to_front_noop_when_hidden(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._panel = MagicMock()
        panel._panel.isVisible.return_value = False

        panel.bring_to_front()

        panel._panel.makeKeyAndOrderFront_.assert_not_called()


class TestResultPreviewPanelLayout:
    """Test layout switching based on show_enhance."""

    def test_show_enhance_false_hides_enhance_section(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._build_panel = MagicMock()
        panel._panel = MagicMock()

        panel.show(
            asr_text="text",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
        )

        assert panel._show_enhance is False

    def test_show_enhance_true_shows_enhance_section(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._build_panel = MagicMock()
        panel._panel = MagicMock()

        panel.show(
            asr_text="text",
            show_enhance=True,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
        )

        assert panel._show_enhance is True


class TestResultPreviewPanelThreading:
    """Test that callbacks work correctly with threading.Event pattern."""

    def test_confirm_unblocks_waiting_thread(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        panel._final_text_field.stringValue.return_value = "result"

        event = threading.Event()
        result_holder = {"text": None}

        def on_confirm(text, correction_info=None, clipboard=False):
            result_holder["text"] = text
            event.set()

        panel.show(
            asr_text="asr",
            show_enhance=False,
            on_confirm=on_confirm,
            on_cancel=lambda: event.set(),
        )

        # Simulate confirm from another thread
        panel.confirmClicked_(None)

        assert event.wait(timeout=1)
        assert result_holder["text"] == "result"

    def test_cancel_unblocks_waiting_thread(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._build_panel = MagicMock()
        panel._panel = MagicMock()

        event = threading.Event()
        cancelled = []

        def on_cancel():
            cancelled.append(True)
            event.set()

        panel.show(
            asr_text="asr",
            show_enhance=False,
            on_confirm=lambda t, info=None, clipboard=False: event.set(),
            on_cancel=on_cancel,
        )

        panel.cancelClicked_(None)

        assert event.wait(timeout=1)
        assert cancelled == [True]


class TestResultPreviewPanelModeSwitch:
    """Test mode switcher (NSSegmentedControl) in preview panel."""

    def test_show_stores_available_modes(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        modes = [("off", "Off"), ("proofread", "Proofread"), ("format", "Format")]

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

    def test_mode_change_callback_invoked(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        modes = [("off", "Off"), ("proofread", "Proofread"), ("format", "Format")]
        changed_modes = []

        panel.show(
            asr_text="text",
            show_enhance=True,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            available_modes=modes,
            current_mode="off",
            on_mode_change=lambda m: changed_modes.append(m),
        )

        # Simulate selecting segment index 2 ("format")
        panel._on_segment_changed(2)

        assert changed_modes == ["format"]
        assert panel._current_mode == "format"

    def test_set_enhance_loading_resets_user_edited(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._user_edited = True
        panel._enhance_label = MagicMock()
        panel._enhance_text_view = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_loading()

        assert panel._user_edited is False

    def test_set_enhance_loading_shows_spinner_label(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_label = MagicMock()
        panel._enhance_text_view = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_loading()

        panel._enhance_label.setStringValue_.assert_called_with(
            "AI  \u23f3 Processing..."
        )
        panel._enhance_text_view.setString_.assert_called_with("")

    def test_set_enhance_off_clears_and_restores_asr(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._asr_text = "original asr"
        panel._user_edited = False
        panel._enhance_label = MagicMock()
        panel._enhance_text_view = MagicMock()
        panel._final_text_field = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_off()

        panel._enhance_label.setStringValue_.assert_called_with(
            "AI  Off"
        )
        panel._enhance_text_view.setString_.assert_called_with("")
        panel._final_text_field.setStringValue_.assert_called_with("original asr")
        assert panel._show_enhance is False

    def test_enhance_label_includes_provider_info(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_info = "zai / glm-5"
        panel._enhance_label = MagicMock()
        panel._enhance_text_view = MagicMock()
        panel._final_text_field = MagicMock()
        panel._user_edited = False
        panel._asr_text = "test"

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_loading()

        panel._enhance_label.setStringValue_.assert_called_with(
            "AI (zai / glm-5)  \u23f3 Processing..."
        )

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_off()

        panel._enhance_label.setStringValue_.assert_called_with(
            "AI (zai / glm-5)  Off"
        )

    def test_set_enhance_loading_starts_timer(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_label = MagicMock()
        panel._enhance_text_view = MagicMock()

        mock_timer = MagicMock()
        mock_ns_timer = MagicMock()
        mock_ns_timer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_.return_value = mock_timer

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            import sys
            sys.modules["Foundation"].NSTimer = mock_ns_timer
            panel.set_enhance_loading()

        assert panel._loading_timer is mock_timer
        assert panel._loading_seconds == 0
        mock_ns_timer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_.assert_called_once_with(
            1.0, panel, b"tickLoadingTimer:", None, True,
        )

    def test_tick_loading_timer_increments_seconds(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_label = MagicMock()
        panel._loading_seconds = 0

        panel.tickLoadingTimer_(None)
        assert panel._loading_seconds == 1
        panel._enhance_label.setStringValue_.assert_called_with(
            "AI  \u23f3 Processing... 1s"
        )

        panel.tickLoadingTimer_(None)
        assert panel._loading_seconds == 2
        panel._enhance_label.setStringValue_.assert_called_with(
            "AI  \u23f3 Processing... 2s"
        )

    def test_append_enhance_text_stops_loading_timer(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_text_view = MagicMock()
        panel._enhance_label = MagicMock()
        panel._enhance_request_id = 1
        mock_timer = MagicMock()
        panel._loading_timer = mock_timer

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.append_enhance_text("chunk", request_id=1, completion_tokens=1)

        mock_timer.invalidate.assert_called_once()
        assert panel._loading_timer is None

    def test_append_thinking_text_stops_loading_timer(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_text_view = MagicMock()
        panel._enhance_label = MagicMock()
        panel._enhance_request_id = 1
        mock_timer = MagicMock()
        panel._loading_timer = mock_timer

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.append_thinking_text("think", request_id=1, thinking_tokens=1)

        mock_timer.invalidate.assert_called_once()
        assert panel._loading_timer is None

    def test_set_enhance_off_stops_loading_timer(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_label = MagicMock()
        panel._enhance_text_view = MagicMock()
        panel._final_text_field = MagicMock()
        panel._asr_text = "test"
        mock_timer = MagicMock()
        panel._loading_timer = mock_timer

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_off()

        mock_timer.invalidate.assert_called_once()
        assert panel._loading_timer is None

    def test_set_enhance_complete_stops_loading_timer(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_text_view = MagicMock()
        panel._enhance_label = MagicMock()
        panel._final_text_field = MagicMock()
        panel._enhance_request_id = 1
        mock_timer = MagicMock()
        panel._loading_timer = mock_timer

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_complete(request_id=1)

        mock_timer.invalidate.assert_called_once()
        assert panel._loading_timer is None

    def test_set_enhance_result_ignores_stale_request_id(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_request_id = 3
        panel._enhance_text_view = MagicMock()
        panel._enhance_label = MagicMock()
        panel._final_text_field = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            # Send result with stale request_id=1
            panel.set_enhance_result("stale result", request_id=1)

        # Should not update anything because request_id is stale
        panel._enhance_text_view.setString_.assert_not_called()

    def test_set_enhance_result_accepts_current_request_id(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_request_id = 3
        panel._user_edited = False
        panel._enhance_text_view = MagicMock()
        panel._enhance_label = MagicMock()
        panel._final_text_field = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_result("current result", request_id=3)

        panel._enhance_text_view.setString_.assert_called_with("current result")
        panel._final_text_field.setStringValue_.assert_called_with("current result")

    def test_backward_compat_show_without_modes(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())

        # Call show() without the new parameters — should work as before
        panel.show(
            asr_text="text",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
        )

        assert panel._available_modes == []
        assert panel._current_mode == "off"
        assert panel._on_mode_change is None
        assert panel._mode_segment is None


def _make_key_event(char, command=True, shift=False):
    """Create a mock NSEvent for keyboard shortcut testing."""
    event = MagicMock()
    event.charactersIgnoringModifiers.return_value = char

    # Build modifier flags: NSCommandKeyMask = 1 << 20, NSShiftKeyMask = 1 << 17
    # NSDeviceIndependentModifierFlagsMask = 0xFFFF0000
    flags = 0
    if command:
        flags |= 1 << 20  # NSCommandKeyMask
    if shift:
        flags |= 1 << 17  # NSShiftKeyMask
    event.modifierFlags.return_value = flags
    return event


def _make_panel_with_modes(modes=None, current_mode="off"):
    """Create a ResultPreviewPanel set up with modes for keyboard shortcut testing."""
    from voicetext.ui.result_window import ResultPreviewPanel

    if modes is None:
        modes = [
            ("off", "Off"),
            ("proofread", "Proofread"),
            ("format", "Format"),
            ("complete", "Complete"),
            ("enhance", "Enhance"),
            ("translate_en", "Translate EN"),
        ]

    panel = _setup_panel_with_final_field(ResultPreviewPanel())
    panel._mode_segment = MagicMock()
    changed_modes = []

    panel.show(
        asr_text="text",
        show_enhance=True,
        on_confirm=MagicMock(),
        on_cancel=MagicMock(),
        available_modes=modes,
        current_mode=current_mode,
        on_mode_change=lambda m: changed_modes.append(m),
    )

    return panel, changed_modes


class TestResultPreviewPanelKeyboardShortcuts:
    """Test ⌘1~⌘N keyboard shortcuts for mode switching."""

    def test_cmd_number_switches_mode(self):
        """⌘2 should switch to index 1 (proofread), update segment and trigger callback."""
        panel, changed_modes = _make_panel_with_modes()
        panel._panel.isKeyWindow.return_value = True

        event = _make_key_event("2", command=True)
        result = panel._handle_key_event(event)

        assert result is None  # Event consumed
        panel._mode_segment.setSelectedSegment_.assert_called_with(1)
        assert changed_modes == ["proofread"]

    def test_cmd_number_out_of_range_ignored(self):
        """⌘9 with only 6 modes should pass through."""
        panel, changed_modes = _make_panel_with_modes()
        panel._panel.isKeyWindow.return_value = True

        event = _make_key_event("9", command=True)
        result = panel._handle_key_event(event)

        assert result is event  # Event not consumed
        panel._mode_segment.setSelectedSegment_.assert_not_called()
        assert changed_modes == []

    def test_plain_number_key_passthrough(self):
        """Number key without Command modifier should pass through."""
        panel, changed_modes = _make_panel_with_modes()
        panel._panel.isKeyWindow.return_value = True

        event = _make_key_event("2", command=False)
        result = panel._handle_key_event(event)

        assert result is event
        panel._mode_segment.setSelectedSegment_.assert_not_called()
        assert changed_modes == []

    def test_event_monitor_installed_on_show(self):
        """show() with available_modes should install event monitor."""
        panel, _ = _make_panel_with_modes()

        assert panel._event_monitor is not None

    def test_event_monitor_removed_on_close(self):
        """close() should remove event monitor."""
        panel, _ = _make_panel_with_modes()
        assert panel._event_monitor is not None

        panel.close()

        assert panel._event_monitor is None

    def test_event_ignored_when_panel_not_key_window(self):
        """When panel is not key window, events should pass through."""
        panel, changed_modes = _make_panel_with_modes()
        panel._panel.isKeyWindow.return_value = False

        event = _make_key_event("1", command=True)
        result = panel._handle_key_event(event)

        assert result is event
        assert changed_modes == []

    def test_monitor_installed_even_without_modes(self):
        """Event monitor should be installed even without modes (for ⌘Enter)."""
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())

        panel.show(
            asr_text="text",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
        )

        assert panel._event_monitor is not None

    def test_cmd_enter_triggers_clipboard_confirm(self):
        """⌘Enter should trigger confirmClicked_ with _cmd_held=True."""
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        panel._panel.isKeyWindow.return_value = True
        panel._final_text_field.stringValue.return_value = "test"
        results = []

        panel.show(
            asr_text="test",
            show_enhance=False,
            on_confirm=lambda t, info=None, clipboard=False: results.append(clipboard),
            on_cancel=MagicMock(),
        )

        event = _make_key_event("\r", command=True)
        result = panel._handle_key_event(event)

        assert result is None  # Event consumed
        assert results == [True]

    def test_cmd_shift_number_passthrough(self):
        """⌘+Shift+number should not be intercepted."""
        panel, changed_modes = _make_panel_with_modes()
        panel._panel.isKeyWindow.return_value = True

        event = _make_key_event("2", command=True, shift=True)
        result = panel._handle_key_event(event)

        assert result is event
        assert changed_modes == []

    def test_special_key_without_characters_passthrough(self):
        """Special keys (Caps Lock, input switch) that raise on charactersIgnoringModifiers should pass through."""
        panel, changed_modes = _make_panel_with_modes()
        panel._panel.isKeyWindow.return_value = True

        event = MagicMock()
        event.modifierFlags.return_value = 1 << 20  # NSCommandKeyMask
        event.charactersIgnoringModifiers.side_effect = Exception(
            "NSInternalInconsistencyException"
        )
        result = panel._handle_key_event(event)

        assert result is event
        assert changed_modes == []


class TestResultPreviewPanelSystemPrompt:
    """Test system prompt viewing functionality."""

    def test_system_prompt_stored_on_set_enhance_result(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._user_edited = False
        panel._enhance_text_view = MagicMock()
        panel._enhance_label = MagicMock()
        panel._final_text_field = MagicMock()
        panel._prompt_button = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_result("done", system_prompt="You are a proofreader.")

        assert panel._system_prompt == "You are a proofreader."

    def test_prompt_button_enabled_when_system_prompt_provided(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._user_edited = False
        panel._enhance_text_view = MagicMock()
        panel._enhance_label = MagicMock()
        panel._final_text_field = MagicMock()
        panel._prompt_button = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_result("done", system_prompt="prompt text")

        panel._prompt_button.setEnabled_.assert_called_with(True)

    def test_prompt_button_not_enabled_when_no_system_prompt(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._user_edited = False
        panel._enhance_text_view = MagicMock()
        panel._enhance_label = MagicMock()
        panel._final_text_field = MagicMock()
        panel._prompt_button = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_enhance_result("done")

        panel._prompt_button.setEnabled_.assert_not_called()

    def test_prompt_info_clicked_noop_when_empty(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._system_prompt = ""

        # Should not raise
        panel.promptInfoClicked_(None)

    def test_prompt_info_clicked_calls_show_panel(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._system_prompt = "You are a helpful assistant."
        panel._show_info_panel = MagicMock()

        panel.promptInfoClicked_(None)

        panel._show_info_panel.assert_called_once_with("System Prompt", "You are a helpful assistant.")

    def test_system_prompt_default_empty(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        assert panel._system_prompt == ""
        assert panel._prompt_button is None


class TestResultPreviewPanelASRInfo:
    """Test ASR model/duration info display in the preview panel."""

    def test_asr_info_stored_on_show(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())

        panel.show(
            asr_text="hello",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            asr_info="whisper-large-v3-turbo  2.5s",
        )

        assert panel._asr_info == "whisper-large-v3-turbo  2.5s"

    def test_asr_info_default_empty(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())

        panel.show(
            asr_text="hello",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
        )

        assert panel._asr_info == ""

    def test_backward_compat_show_without_asr_info(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())

        # Call show() without asr_info — should default to empty string
        panel.show(
            asr_text="text",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
        )

        assert panel._asr_info == ""


class TestResultPreviewPanelPlayback:
    """Test ASR audio playback button functionality."""

    def test_wav_data_stored_on_show(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        wav = b"RIFF....WAVEfmt "

        panel.show(
            asr_text="hello",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            asr_wav_data=wav,
        )

        assert panel._asr_wav_data == wav

    def test_wav_data_default_none(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())

        panel.show(
            asr_text="hello",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
        )

        assert panel._asr_wav_data is None

    def test_play_audio_noop_when_no_wav(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._asr_wav_data = None

        # Should not raise
        panel.playAudioClicked_(None)

    def test_play_audio_calls_play_wav(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._asr_wav_data = b"fake-wav-data"
        panel._play_wav = MagicMock()

        panel.playAudioClicked_(None)

        panel._play_wav.assert_called_once_with(b"fake-wav-data")

    def test_close_stops_playback(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._panel = MagicMock()
        mock_sound = MagicMock()
        panel._asr_sound = mock_sound

        panel.close()

        mock_sound.stop.assert_called_once()
        assert panel._asr_sound is None

    def test_stop_playback_handles_no_sound(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._asr_sound = None

        # Should not raise
        panel._stop_playback()

    def test_save_audio_noop_when_no_wav(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._asr_wav_data = None

        # Should not raise
        panel.saveAudioClicked_(None)

    def test_save_audio_calls_save_wav(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._asr_wav_data = b"fake-wav-data"
        panel._save_wav = MagicMock()

        panel.saveAudioClicked_(None)

        panel._save_wav.assert_called_once_with(b"fake-wav-data")

    def test_save_button_default_none(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        assert panel._asr_save_button is None


class TestResultPreviewPanelModelPopups:
    """Test STT/LLM model popup infrastructure."""

    def test_stt_popup_data_stored_on_show(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        stt_models = ["Whisper Large", "FunASR", "Groq / whisper-large-v3"]

        panel.show(
            asr_text="hello",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            stt_models=stt_models,
            stt_current_index=1,
        )

        assert panel._stt_models == stt_models
        assert panel._stt_current_index == 1

    def test_llm_popup_data_stored_on_show(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        llm_models = ["ollama / qwen2.5:7b", "openai / gpt-4o"]

        panel.show(
            asr_text="hello",
            show_enhance=True,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            llm_models=llm_models,
            llm_current_index=0,
        )

        assert panel._llm_models == llm_models
        assert panel._llm_current_index == 0

    def test_stt_popup_callback_invoked(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        changed = []

        panel.show(
            asr_text="hello",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            stt_models=["Model A", "Model B"],
            on_stt_model_change=lambda idx: changed.append(idx),
        )

        panel._on_stt_popup_changed(1)
        assert changed == [1]

    def test_llm_popup_callback_invoked(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        changed = []

        panel.show(
            asr_text="hello",
            show_enhance=True,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            llm_models=["ollama / qwen", "openai / gpt-4o"],
            on_llm_model_change=lambda idx: changed.append(idx),
        )

        panel._on_llm_popup_changed(0)
        assert changed == [0]

    def test_backward_compat_no_popups(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())

        panel.show(
            asr_text="text",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
        )

        assert panel._stt_models == []
        assert panel._llm_models == []
        assert panel._on_stt_model_change is None
        assert panel._on_llm_model_change is None

    def test_stt_popup_callback_noop_when_no_handler(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._on_stt_model_change = None

        # Should not raise
        panel._on_stt_popup_changed(0)

    def test_llm_popup_callback_noop_when_no_handler(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._on_llm_model_change = None

        # Should not raise
        panel._on_llm_popup_changed(0)

    def test_enhance_label_text_without_llm_popup(self):
        """Without LLM popup, label includes provider info."""
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_info = "ollama / qwen2.5:7b"

        result = panel._enhance_label_text("Tokens: 100")
        assert result == "AI (ollama / qwen2.5:7b)  Tokens: 100"

    def test_enhance_label_text_with_llm_popup(self):
        """With LLM popup, label only shows suffix (status/token info)."""
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._enhance_info = "ollama / qwen2.5:7b"
        panel._llm_models = ["ollama / qwen2.5:7b"]

        result = panel._enhance_label_text("Tokens: 100")
        assert result == "Tokens: 100"

    def test_enhance_label_text_with_llm_popup_empty_suffix(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._llm_models = ["model1"]

        result = panel._enhance_label_text("")
        assert result == ""


class TestASRLoadingAndResult:
    """Test ASR loading/result methods for STT re-transcription."""

    def test_set_asr_loading_increments_request_id(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._asr_text_view = MagicMock()
        panel._stt_popup = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_asr_loading()

        assert panel._asr_request_id == 1
        panel._asr_text_view.setString_.assert_called_with("\u23f3 Re-transcribing...")
        panel._stt_popup.setEnabled_.assert_called_with(False)

    def test_set_asr_loading_without_popup(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._asr_text_view = MagicMock()
        panel._stt_popup = None

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_asr_loading()

        assert panel._asr_request_id == 1
        panel._asr_text_view.setString_.assert_called_with("\u23f3 Re-transcribing...")

    def test_set_asr_result_updates_text_and_final(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._asr_request_id = 1
        panel._user_edited = False
        panel._asr_text_view = MagicMock()
        panel._final_text_field = MagicMock()
        panel._asr_info_label = MagicMock()
        panel._stt_popup = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_asr_result("new text", asr_info="2.5s", request_id=1)

        panel._asr_text_view.setString_.assert_called_with("new text")
        panel._final_text_field.setStringValue_.assert_called_with("new text")
        panel._asr_info_label.setStringValue_.assert_called_with("2.5s")
        assert panel._asr_text == "new text"
        panel._stt_popup.setEnabled_.assert_called_with(True)

    def test_set_asr_result_skips_final_when_user_edited(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._asr_request_id = 1
        panel._user_edited = True
        panel._asr_text_view = MagicMock()
        panel._final_text_field = MagicMock()
        panel._stt_popup = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_asr_result("new text", request_id=1)

        panel._asr_text_view.setString_.assert_called_with("new text")
        panel._final_text_field.setStringValue_.assert_not_called()

    def test_set_asr_result_discards_stale_request(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._asr_request_id = 3
        panel._asr_text_view = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_asr_result("stale text", request_id=1)

        panel._asr_text_view.setString_.assert_not_called()

    def test_set_asr_result_accepts_current_request(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._asr_request_id = 3
        panel._user_edited = False
        panel._asr_text_view = MagicMock()
        panel._final_text_field = MagicMock()
        panel._stt_popup = MagicMock()

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_asr_result("current text", request_id=3)

        panel._asr_text_view.setString_.assert_called_with("current text")

    def test_set_stt_popup_index_rollback(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._stt_popup = MagicMock()
        panel._stt_models = ["A", "B", "C"]

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_stt_popup_index(1)

        panel._stt_popup.selectItemAtIndex_.assert_called_with(1)
        panel._stt_popup.setEnabled_.assert_called_with(True)

    def test_set_stt_popup_index_out_of_range(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._stt_popup = MagicMock()
        panel._stt_models = ["A", "B"]

        with patch("PyObjCTools.AppHelper") as mock_helper:
            mock_helper.callAfter.side_effect = lambda fn: fn()
            panel.set_stt_popup_index(5)

        panel._stt_popup.selectItemAtIndex_.assert_not_called()
        # Should still re-enable
        panel._stt_popup.setEnabled_.assert_called_with(True)

    def test_asr_request_id_property(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        assert panel.asr_request_id == 0

        panel._asr_request_id = 5
        assert panel.asr_request_id == 5


class TestPuncCheckbox:
    """Test punctuation checkbox toggle behavior."""

    def test_punc_toggle_callback_fires(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        toggled = []

        panel._on_punc_toggle = lambda enabled: toggled.append(enabled)

        # Simulate toggling off
        panel._on_punc_toggled(False)
        assert toggled == [False]
        assert panel._punc_enabled is False

        # Simulate toggling on
        panel._on_punc_toggled(True)
        assert toggled == [False, True]
        assert panel._punc_enabled is True

    def test_punc_toggle_no_callback(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._on_punc_toggle = None

        # Should not raise
        panel._on_punc_toggled(False)
        assert panel._punc_enabled is False

    def test_punc_default_state(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        # Default punc_enabled should not be set until show() is called
        assert not hasattr(panel, "_punc_enabled") or panel._punc_enabled is not False


class TestPanelCloseDelegate:
    """Test that clicking the panel close button (X) triggers cancel."""

    def test_window_close_triggers_cancel(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        cancelled = []

        panel.show(
            asr_text="hello",
            show_enhance=False,
            on_confirm=lambda t, c, clipboard=False: None,
            on_cancel=lambda: cancelled.append(True),
        )

        # Simulate windowWillClose: (close button click)
        panel.cancelClicked_(None)
        assert cancelled == [True]

    def test_close_clears_delegate_to_prevent_reentry(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        cancel_count = []

        panel.show(
            asr_text="hello",
            show_enhance=False,
            on_confirm=lambda t, c, clipboard=False: None,
            on_cancel=lambda: cancel_count.append(1),
        )

        # close() should clear the delegate
        panel.close()
        assert panel._panel is None
        assert panel._close_delegate is None

    def test_cancel_via_close_button_only_fires_once(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = _setup_panel_with_final_field(ResultPreviewPanel())
        cancel_count = []

        panel.show(
            asr_text="hello",
            show_enhance=False,
            on_confirm=lambda t, c, clipboard=False: None,
            on_cancel=lambda: cancel_count.append(1),
        )

        # cancelClicked_ calls close() which clears delegate,
        # so a second call should not fire on_cancel again
        panel.cancelClicked_(None)
        panel.cancelClicked_(None)
        assert len(cancel_count) == 1
