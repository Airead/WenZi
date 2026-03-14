"""Tests for the text input module."""

from unittest.mock import patch, MagicMock, call


from voicetext.input import type_text


class TestTypeText:
    def test_empty_text_does_nothing(self):
        with patch("voicetext.input._type_via_clipboard") as mock_clip:
            type_text("")
            mock_clip.assert_not_called()

    @patch("voicetext.input._type_via_clipboard", return_value=True)
    def test_auto_tries_clipboard_first(self, mock_clip):
        type_text("hello", method="auto")
        mock_clip.assert_called_once_with("hello")

    @patch("voicetext.input._type_via_clipboard", return_value=False)
    @patch("voicetext.input._type_via_applescript", return_value=True)
    def test_auto_falls_back_to_applescript(self, mock_apple, mock_clip):
        type_text("hello", method="auto")
        mock_clip.assert_called_once()
        mock_apple.assert_called_once()

    @patch("voicetext.input._type_via_clipboard", return_value=True)
    def test_append_newline(self, mock_clip):
        type_text("hello", append_newline=True)
        mock_clip.assert_called_once_with("hello\n")


class TestPasteboardHelpers:
    """Tests for NSPasteboard-based clipboard helpers."""

    @patch("voicetext.input.NSString")
    @patch("voicetext.input.NSPasteboard")
    def test_set_pasteboard_concealed_sets_markers(self, mock_pb_cls, mock_nsstr):
        from voicetext.input import _set_pasteboard_concealed

        mock_pb = MagicMock()
        mock_pb_cls.generalPasteboard.return_value = mock_pb
        mock_pb.setString_forType_.return_value = True
        mock_nsstr.stringWithString_.return_value = "hello"

        result = _set_pasteboard_concealed("hello")

        assert result is True
        mock_pb.clearContents.assert_called_once()
        # Should set the main string + two marker types
        assert mock_pb.setString_forType_.call_count == 3
        calls = mock_pb.setString_forType_.call_args_list
        assert calls[0] == call("hello", "public.utf8-plain-text")
        assert calls[1] == call("", "org.nspasteboard.ConcealedType")
        assert calls[2] == call("", "com.nspasteboard.TransientType")

    @patch("voicetext.input.NSPasteboard")
    def test_get_pasteboard_string(self, mock_pb_cls):
        from voicetext.input import _get_pasteboard_string

        mock_pb = MagicMock()
        mock_pb_cls.generalPasteboard.return_value = mock_pb
        mock_pb.stringForType_.return_value = "existing text"

        result = _get_pasteboard_string()

        assert result == "existing text"
        mock_pb.stringForType_.assert_called_once()

    @patch("voicetext.input.NSString")
    @patch("voicetext.input.NSPasteboard")
    def test_set_pasteboard_string_no_markers(self, mock_pb_cls, mock_nsstr):
        from voicetext.input import _set_pasteboard_string

        mock_pb = MagicMock()
        mock_pb_cls.generalPasteboard.return_value = mock_pb
        mock_nsstr.stringWithString_.return_value = "restored"

        _set_pasteboard_string("restored")

        mock_pb.clearContents.assert_called_once()
        # Only one setString call (no concealed/transient markers)
        assert mock_pb.setString_forType_.call_count == 1
        assert mock_pb.setString_forType_.call_args == call(
            "restored", "public.utf8-plain-text"
        )

    @patch("voicetext.input.NSString")
    @patch("voicetext.input.NSPasteboard")
    def test_concealed_write_failure_returns_false(self, mock_pb_cls, mock_nsstr):
        from voicetext.input import _set_pasteboard_concealed

        mock_pb = MagicMock()
        mock_pb_cls.generalPasteboard.return_value = mock_pb
        mock_pb.setString_forType_.return_value = False
        mock_nsstr.stringWithString_.return_value = "fail"

        result = _set_pasteboard_concealed("fail")

        assert result is False

    @patch("voicetext.input.time")
    @patch("voicetext.input.threading")
    @patch("voicetext.input.subprocess")
    @patch("voicetext.input._set_pasteboard_concealed", return_value=True)
    @patch("voicetext.input._get_pasteboard_string", return_value="old content")
    def test_clipboard_restore_after_paste(
        self, mock_get, mock_set_concealed, mock_subprocess, mock_threading, mock_time
    ):
        from voicetext.input import _type_via_clipboard

        mock_subprocess.run.return_value = MagicMock(returncode=0)

        result = _type_via_clipboard("new text")

        assert result is True
        mock_get.assert_called_once()
        mock_set_concealed.assert_called_once_with("new text")
        # A restore thread should be started
        mock_threading.Thread.assert_called_once()
        mock_threading.Thread.return_value.start.assert_called_once()


class TestAppleScriptSafety:
    """Tests for AppleScript command injection prevention."""

    @patch("voicetext.input.subprocess")
    def test_applescript_uses_stdin(self, mock_subprocess):
        """AppleScript should pass script via stdin, not -e argument."""
        from voicetext.input import _type_via_applescript

        mock_subprocess.run.return_value = MagicMock(returncode=0)

        _type_via_applescript("hello world")

        args, kwargs = mock_subprocess.run.call_args
        # Should use stdin (input= kwarg) instead of -e
        assert kwargs.get("input") is not None
        assert kwargs.get("text") is True
        # Command should be just ["osascript"] without -e
        assert args[0] == ["osascript"]

    @patch("voicetext.input.subprocess")
    def test_applescript_special_chars(self, mock_subprocess):
        """Text with special characters should be safely passed."""
        from voicetext.input import _type_via_applescript

        mock_subprocess.run.return_value = MagicMock(returncode=0)

        payload = 'hello "world" & $(dangerous) `backtick`'
        _type_via_applescript(payload)

        args, kwargs = mock_subprocess.run.call_args
        script = kwargs["input"]
        # The script should contain escaped quotes
        assert '\\"world\\"' in script
        # Should NOT be passed as -e argument
        assert "-e" not in args[0]
