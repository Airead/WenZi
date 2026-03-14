"""Tests for clipboard AI enhancement feature."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# Mock AppKit/Foundation before importing modules that use them
@pytest.fixture(autouse=True)
def mock_appkit(monkeypatch):
    """Provide mock AppKit and Foundation modules for headless testing."""
    mock_appkit_mod = MagicMock()
    mock_appkit_mod.NSCommandKeyMask = 1 << 20
    mock_appkit_mod.NSShiftKeyMask = 1 << 17
    mock_appkit_mod.NSDeviceIndependentModifierFlagsMask = 0xFFFF0000
    mock_appkit_mod.NSKeyDownMask = 1 << 10

    modules = {
        "AppKit": mock_appkit_mod,
        "Foundation": MagicMock(),
        "objc": MagicMock(),
        "PyObjCTools": MagicMock(),
        "PyObjCTools.AppHelper": MagicMock(),
    }

    for name, mod in modules.items():
        monkeypatch.setitem(__import__("sys").modules, name, mod)


class TestClipboardPublicFunctions:
    """Test public clipboard read/write functions in input.py."""

    @patch("voicetext.input.NSPasteboard")
    def test_get_clipboard_text(self, mock_pb_cls):
        from voicetext.input import get_clipboard_text

        mock_pb = MagicMock()
        mock_pb_cls.generalPasteboard.return_value = mock_pb
        mock_pb.stringForType_.return_value = "hello clipboard"

        result = get_clipboard_text()
        assert result == "hello clipboard"

    @patch("voicetext.input.NSPasteboard")
    def test_get_clipboard_text_empty(self, mock_pb_cls):
        from voicetext.input import get_clipboard_text

        mock_pb = MagicMock()
        mock_pb_cls.generalPasteboard.return_value = mock_pb
        mock_pb.stringForType_.return_value = None

        result = get_clipboard_text()
        assert result is None

    @patch("voicetext.input.NSString")
    @patch("voicetext.input.NSPasteboard")
    def test_set_clipboard_text(self, mock_pb_cls, mock_nsstr):
        from voicetext.input import set_clipboard_text

        mock_pb = MagicMock()
        mock_pb_cls.generalPasteboard.return_value = mock_pb
        mock_nsstr.stringWithString_.return_value = "enhanced text"

        set_clipboard_text("enhanced text")

        mock_pb.clearContents.assert_called_once()
        # Should set string without concealed markers
        assert mock_pb.setString_forType_.call_count == 1

    @patch("voicetext.input.NSPasteboardTypeString", "public.utf8-plain-text")
    @patch("voicetext.input.NSPasteboard")
    def test_has_clipboard_text_true(self, mock_pb_cls):
        from voicetext.input import has_clipboard_text

        mock_pb = MagicMock()
        mock_pb_cls.generalPasteboard.return_value = mock_pb
        mock_pb.availableTypeFromArray_.return_value = "public.utf8-plain-text"

        assert has_clipboard_text() is True

    @patch("voicetext.input.NSPasteboardTypeString", "public.utf8-plain-text")
    @patch("voicetext.input.NSPasteboard")
    def test_has_clipboard_text_false_for_image(self, mock_pb_cls):
        from voicetext.input import has_clipboard_text

        mock_pb = MagicMock()
        mock_pb_cls.generalPasteboard.return_value = mock_pb
        mock_pb.availableTypeFromArray_.return_value = None

        assert has_clipboard_text() is False


class TestCopySelectionToClipboard:
    """Test copy_selection_to_clipboard() function."""

    @patch("voicetext.input._has_text_selection", return_value=True)
    @patch("voicetext.input.time.sleep")
    @patch("voicetext.input._send_cmd_c")
    @patch("voicetext.input.get_clipboard_text")
    def test_selection_copied_successfully(self, mock_get, mock_send, mock_sleep, _):
        from voicetext.input import copy_selection_to_clipboard

        # Clipboard changes after Cmd+C
        mock_get.side_effect = ["old text", "new selected text"]

        result = copy_selection_to_clipboard()

        assert result is True
        mock_send.assert_called_once()
        # Two sleeps: 0.05 before Cmd+C and 0.15 after
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(0.05)
        mock_sleep.assert_any_call(0.15)

    @patch("voicetext.input._has_text_selection", return_value=False)
    def test_no_selection_skips_cmd_c(self, _):
        from voicetext.input import copy_selection_to_clipboard

        result = copy_selection_to_clipboard()

        assert result is False

    @patch("voicetext.input._has_text_selection", return_value=True)
    @patch("voicetext.input.time.sleep")
    @patch("voicetext.input._send_cmd_c")
    @patch("voicetext.input.get_clipboard_text")
    def test_clipboard_unchanged_returns_false(self, mock_get, mock_send, mock_sleep, _):
        from voicetext.input import copy_selection_to_clipboard

        # Clipboard stays the same (nothing selected per clipboard check)
        mock_get.side_effect = ["same text", "same text"]

        result = copy_selection_to_clipboard()

        assert result is False

    @patch("voicetext.input._has_text_selection", return_value=True)
    @patch("voicetext.input.time.sleep")
    @patch("voicetext.input._send_cmd_c")
    @patch("voicetext.input.get_clipboard_text")
    def test_send_cmd_c_failure_returns_false(self, mock_get, mock_send, mock_sleep, _):
        from voicetext.input import copy_selection_to_clipboard

        mock_get.return_value = "old text"
        mock_send.side_effect = OSError("failed")

        result = copy_selection_to_clipboard()

        assert result is False


class TestClipboardEnhanceValidation:
    """Test clipboard content validation before enhancement."""

    @pytest.fixture(autouse=True)
    def mock_appkit(self):
        """Override the module-level autouse fixture — not needed here."""
        pass

    def _make_app_and_ctrl(self):
        """Create a minimal mock app and a real PreviewController."""
        from voicetext.controllers.preview_controller import PreviewController

        app = MagicMock(spec=[])
        app._busy = False
        ctrl = PreviewController(app)
        return app, ctrl

    def test_non_text_clipboard_shows_alert(self):
        with patch("voicetext.controllers.preview_controller.copy_selection_to_clipboard"), \
             patch("voicetext.controllers.preview_controller.has_clipboard_text", return_value=False), \
             patch("voicetext.controllers.preview_controller.topmost_alert") as mock_alert, \
             patch("voicetext.controllers.preview_controller.restore_accessory") as mock_restore:
            app, ctrl = self._make_app_and_ctrl()

            mock_helper = MagicMock()
            mock_helper.callAfter = lambda fn, *a: fn(*a)
            with patch.dict("sys.modules", {
                "PyObjCTools": MagicMock(AppHelper=mock_helper),
                "PyObjCTools.AppHelper": mock_helper,
            }):
                ctrl._on_clipboard_enhance_worker()

            mock_alert.assert_called_once()
            assert "Not Supported" in mock_alert.call_args[1]["title"]
            mock_restore.assert_called_once()

    def test_empty_text_clipboard_shows_alert(self):
        with patch("voicetext.controllers.preview_controller.copy_selection_to_clipboard"), \
             patch("voicetext.controllers.preview_controller.has_clipboard_text", return_value=True), \
             patch("voicetext.controllers.preview_controller.get_clipboard_text", return_value=""), \
             patch("voicetext.controllers.preview_controller.topmost_alert") as mock_alert, \
             patch("voicetext.controllers.preview_controller.restore_accessory") as mock_restore:
            app, ctrl = self._make_app_and_ctrl()

            mock_helper = MagicMock()
            mock_helper.callAfter = lambda fn, *a: fn(*a)
            with patch.dict("sys.modules", {
                "PyObjCTools": MagicMock(AppHelper=mock_helper),
                "PyObjCTools.AppHelper": mock_helper,
            }):
                ctrl._on_clipboard_enhance_worker()

            mock_alert.assert_called_once()
            assert "Empty" in mock_alert.call_args[1]["title"]
            mock_restore.assert_called_once()

    def test_long_text_shows_alert_and_aborts(self):
        with patch("voicetext.controllers.preview_controller.copy_selection_to_clipboard"), \
             patch("voicetext.controllers.preview_controller.has_clipboard_text", return_value=True), \
             patch("voicetext.controllers.preview_controller.get_clipboard_text", return_value="x" * 2001), \
             patch("voicetext.controllers.preview_controller.topmost_alert") as mock_alert, \
             patch("voicetext.controllers.preview_controller.restore_accessory") as mock_restore:
            app, ctrl = self._make_app_and_ctrl()

            mock_helper = MagicMock()
            mock_helper.callAfter = lambda fn, *a: fn(*a)
            with patch.dict("sys.modules", {
                "PyObjCTools": MagicMock(AppHelper=mock_helper),
                "PyObjCTools.AppHelper": mock_helper,
            }):
                ctrl._on_clipboard_enhance_worker()

            mock_alert.assert_called_once()
            assert "2001" in mock_alert.call_args[1]["message"]
            mock_restore.assert_called_once()
            assert not app._busy

    def test_normal_text_proceeds_without_alert(self):
        with patch("voicetext.controllers.preview_controller.copy_selection_to_clipboard"), \
             patch("voicetext.controllers.preview_controller.has_clipboard_text", return_value=True), \
             patch("voicetext.controllers.preview_controller.get_clipboard_text", return_value="short text"):
            app, ctrl = self._make_app_and_ctrl()
            app._set_status = MagicMock()
            ctrl._do_clipboard_with_preview = MagicMock()

            mock_helper = MagicMock()
            mock_helper.callAfter = lambda fn, *a: fn(*a)
            with patch.dict("sys.modules", {
                "PyObjCTools": MagicMock(AppHelper=mock_helper),
                "PyObjCTools.AppHelper": mock_helper,
            }):
                ctrl._on_clipboard_enhance_worker()

            ctrl._do_clipboard_with_preview.assert_called_once_with("short text")
            assert app._busy is False  # busy is reset in finally block

    def test_busy_skips(self):
        app, ctrl = self._make_app_and_ctrl()
        app._busy = True

        mock_helper = MagicMock()
        with patch.dict("sys.modules", {
            "PyObjCTools": MagicMock(AppHelper=mock_helper),
            "PyObjCTools.AppHelper": mock_helper,
        }):
            ctrl._on_clipboard_enhance_worker()

    def test_dispatches_to_worker_thread(self):
        """Verify on_clipboard_enhance starts a worker thread."""
        from voicetext.controllers.preview_controller import PreviewController

        app = MagicMock()
        ctrl = PreviewController(app)

        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            ctrl.on_clipboard_enhance()
            mock_thread.assert_called_once()
            assert mock_thread.call_args[1]["target"] == ctrl._on_clipboard_enhance_worker


class TestPreviewPanelClipboardSource:
    """Test Preview panel behavior with source='clipboard'."""

    def _setup_panel(self):
        from voicetext.ui.result_window import ResultPreviewPanel

        panel = ResultPreviewPanel()
        panel._build_panel = MagicMock()
        panel._panel = MagicMock()
        panel._final_text_field = MagicMock()
        return panel

    def test_source_defaults_to_voice(self):
        panel = self._setup_panel()

        panel.show(
            asr_text="hello",
            show_enhance=False,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
        )

        assert panel._source == "voice"

    def test_source_clipboard_stored(self):
        panel = self._setup_panel()

        panel.show(
            asr_text="clipboard text",
            show_enhance=True,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            source="clipboard",
        )

        assert panel._source == "clipboard"

    def test_clipboard_source_no_wav_data(self):
        panel = self._setup_panel()

        panel.show(
            asr_text="clipboard text",
            show_enhance=True,
            on_confirm=MagicMock(),
            on_cancel=MagicMock(),
            source="clipboard",
            asr_wav_data=None,
        )

        assert panel._asr_wav_data is None
        assert panel._source == "clipboard"


class TestClipboardEnhanceConfig:
    """Test clipboard_enhance config defaults."""

    def test_default_config_has_clipboard_enhance(self):
        from voicetext.config import DEFAULT_CONFIG

        assert "clipboard_enhance" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["clipboard_enhance"]["hotkey"] == "ctrl+cmd+v"
        assert "output" not in DEFAULT_CONFIG["clipboard_enhance"]

    def test_config_merge_preserves_clipboard_enhance(self):
        from voicetext.config import _merge_dict, DEFAULT_CONFIG

        overrides = {
            "clipboard_enhance": {
                "hotkey": "ctrl+shift+v",
            }
        }
        result = _merge_dict(DEFAULT_CONFIG, overrides)
        assert result["clipboard_enhance"]["hotkey"] == "ctrl+shift+v"
