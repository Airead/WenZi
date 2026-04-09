"""Tests for wenzi.screenshot.annotation.

Pure-logic helpers are tested directly. AnnotationLayer event handling
is tested by mocking the WebViewPanel dependency.
"""

from __future__ import annotations

import base64
import struct

from wenzi.screenshot.annotation import (
    AnnotationLayer,
    decode_data_url,
    get_image_dimensions,
)

# ---------------------------------------------------------------------------
# decode_data_url
# ---------------------------------------------------------------------------


class TestDecodeDataUrl:
    def test_valid_png_data_url(self):
        raw = b"\x89PNG\r\n\x1a\nfake"
        url = "data:image/png;base64," + base64.b64encode(raw).decode()
        assert decode_data_url(url) == raw

    def test_empty_payload(self):
        assert decode_data_url("data:image/png;base64,") == b""

    def test_wrong_prefix_returns_none(self):
        assert decode_data_url("data:image/jpeg;base64,abc") is None

    def test_invalid_base64_returns_none(self):
        assert decode_data_url("data:image/png;base64,!!!invalid!!!") is None


# ---------------------------------------------------------------------------
# get_image_dimensions
# ---------------------------------------------------------------------------


def _make_minimal_png(width: int, height: int) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    ihdr_chunk = struct.pack(">I", 13) + b"IHDR" + ihdr_data + b"\x00" * 4
    return sig + ihdr_chunk


class TestGetImageDimensions:
    def test_valid_png(self, tmp_path):
        p = tmp_path / "test.png"
        p.write_bytes(_make_minimal_png(1024, 768))
        assert get_image_dimensions(str(p)) == (1024, 768)

    def test_non_png_returns_fallback(self, tmp_path):
        p = tmp_path / "not.txt"
        p.write_text("hello")
        assert get_image_dimensions(str(p)) == (800, 600)

    def test_missing_file_returns_fallback(self):
        assert get_image_dimensions("/nonexistent") == (800, 600)


# ---------------------------------------------------------------------------
# AnnotationLayer
# ---------------------------------------------------------------------------


class TestAnnotationLayerInit:
    def test_initial_state(self):
        layer = AnnotationLayer()
        assert layer._panel is None
        assert layer._image_path is None

    def test_show_missing_image_calls_cancel(self, tmp_path):
        layer = AnnotationLayer()
        cancelled = []
        layer.show(
            image_path=str(tmp_path / "missing.png"),
            on_done=lambda: None,
            on_cancel=lambda: cancelled.append(True),
        )
        assert cancelled == [True]


class TestEventHandling:
    def _make_layer(self):
        layer = AnnotationLayer()
        # Simulate an active panel with mock send
        from unittest.mock import MagicMock
        layer._panel = MagicMock()
        return layer

    def test_request_export_clipboard(self):
        layer = self._make_layer()
        layer._request_export("clipboard")
        assert layer._pending_action == "clipboard"
        layer._panel.send.assert_called_once_with("export")

    def test_request_export_save(self):
        layer = self._make_layer()
        layer._request_export("save")
        assert layer._pending_action == "save"

    def test_cancel_calls_callback(self):
        layer = self._make_layer()
        cancelled = []
        layer._on_cancel = lambda: cancelled.append(True)
        layer._do_cancel()
        assert cancelled == [True]

    def test_exported_clipboard_action(self):
        layer = self._make_layer()
        layer._pending_action = "clipboard"
        done = []
        layer._on_done = lambda: done.append(True)
        layer._copy_to_clipboard = lambda png: None
        layer._play_sound = lambda: None

        raw = b"\x89PNG"
        data_url = "data:image/png;base64," + base64.b64encode(raw).decode()
        layer._handle_exported({"dataUrl": data_url})
        assert done == [True]

    def test_exported_none_data_ignored(self):
        layer = self._make_layer()
        layer._pending_action = "clipboard"
        layer._handle_exported(None)  # should not raise

    def test_close_idempotent(self):
        layer = AnnotationLayer()
        layer.close()
        layer.close()
