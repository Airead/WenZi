"""Tests for wenzi.screenshot.annotation.

Exercises the pure-logic helpers and AnnotationLayer event handling
without PyObjC.  All AppKit / WebKit / Quartz APIs are mocked.
"""

from __future__ import annotations

import base64
from typing import Dict
from unittest.mock import MagicMock, call

from wenzi.screenshot.annotation import (
    AnnotationLayer,
    _TOOLBAR_HEIGHT,
    build_init_event_data,
    compute_panel_frame,
    compute_toolbar_position,
    decode_data_url,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_region(
    x: float = 100, y: float = 100, w: float = 400, h: float = 300
) -> Dict[str, float]:
    return {"x": x, "y": y, "width": w, "height": h}


def _make_data_url(png_bytes: bytes = b"\x89PNG\r\n\x1a\n") -> str:
    encoded = base64.b64encode(png_bytes).decode()
    return f"data:image/png;base64,{encoded}"


# ---------------------------------------------------------------------------
# compute_toolbar_position
# ---------------------------------------------------------------------------


class TestComputeToolbarPosition:
    """Toolbar position depends on available space below the selection."""

    def test_enough_space_below(self):
        """When there's room below the selection, toolbar goes to bottom."""
        region = _make_region(y=100, h=300)  # bottom at 400
        # Screen height 1080 → 680 px below → plenty of room
        assert compute_toolbar_position(region, 1080.0) == "bottom"

    def test_not_enough_space_below(self):
        """When selection is near the bottom, toolbar goes to top."""
        region = _make_region(y=950, h=100)  # bottom at 1050
        # Screen height 1080 → 30 px below → not enough for 80px toolbar
        assert compute_toolbar_position(region, 1080.0) == "top"

    def test_exact_fit_below(self):
        """Toolbar fits exactly at the bottom — bottom is chosen."""
        region = _make_region(y=100, h=300)  # bottom at 400
        screen_h = 400 + _TOOLBAR_HEIGHT  # exactly fits
        assert compute_toolbar_position(region, screen_h) == "bottom"

    def test_one_pixel_short(self):
        """One pixel short — toolbar goes to top."""
        region = _make_region(y=100, h=300)  # bottom at 400
        screen_h = 400 + _TOOLBAR_HEIGHT - 1  # 1 px short
        assert compute_toolbar_position(region, screen_h) == "top"

    def test_custom_toolbar_height(self):
        region = _make_region(y=100, h=300)
        assert compute_toolbar_position(region, 440.0, toolbar_height=40) == "bottom"
        assert compute_toolbar_position(region, 439.0, toolbar_height=40) == "top"

    def test_selection_at_top_of_screen(self):
        """Selection at the very top — always bottom."""
        region = _make_region(y=0, h=200)
        assert compute_toolbar_position(region, 1080.0) == "bottom"

    def test_selection_fills_screen(self):
        """Selection fills the whole screen — top (no room below)."""
        region = _make_region(y=0, h=1080)
        assert compute_toolbar_position(region, 1080.0) == "top"


# ---------------------------------------------------------------------------
# compute_panel_frame
# ---------------------------------------------------------------------------


class TestComputePanelFrame:
    """Panel frame calculation for both toolbar positions."""

    def test_toolbar_bottom(self):
        region = _make_region(x=100, y=200, w=400, h=300)
        frame = compute_panel_frame(region, "bottom")
        assert frame["x"] == 100
        assert frame["y"] == 200  # same as selection top
        assert frame["width"] == 400
        assert frame["height"] == 300 + _TOOLBAR_HEIGHT

    def test_toolbar_top(self):
        region = _make_region(x=100, y=200, w=400, h=300)
        frame = compute_panel_frame(region, "top")
        assert frame["x"] == 100
        assert frame["y"] == 200 - _TOOLBAR_HEIGHT  # starts above selection
        assert frame["width"] == 400
        assert frame["height"] == 300 + _TOOLBAR_HEIGHT

    def test_custom_toolbar_height(self):
        region = _make_region(x=50, y=50, w=200, h=100)
        frame = compute_panel_frame(region, "bottom", toolbar_height=60)
        assert frame["height"] == 160
        assert frame["y"] == 50


# ---------------------------------------------------------------------------
# build_init_event_data
# ---------------------------------------------------------------------------


class TestBuildInitEventData:
    def test_builds_correct_dict(self):
        region = _make_region(w=800, h=600)
        data = build_init_event_data("wz-file:///tmp/img.png", region, "bottom")
        assert data == {
            "imageUrl": "wz-file:///tmp/img.png",
            "width": 800,
            "height": 600,
            "toolbarPosition": "bottom",
        }

    def test_top_toolbar(self):
        region = _make_region(w=400, h=300)
        data = build_init_event_data("wz-file:///img.png", region, "top")
        assert data["toolbarPosition"] == "top"


# ---------------------------------------------------------------------------
# decode_data_url
# ---------------------------------------------------------------------------


class TestDecodeDataUrl:
    def test_valid_data_url(self):
        raw = b"\x89PNG\r\n\x1a\nfake_image_data"
        data_url = _make_data_url(raw)
        result = decode_data_url(data_url)
        assert result == raw

    def test_invalid_prefix(self):
        result = decode_data_url("data:image/jpeg;base64,abc")
        assert result is None

    def test_invalid_base64(self):
        result = decode_data_url("data:image/png;base64,!!not-valid!!")
        assert result is None

    def test_empty_data(self):
        data_url = "data:image/png;base64,"
        result = decode_data_url(data_url)
        assert result == b""


# ---------------------------------------------------------------------------
# save_cgimage_to_png
# ---------------------------------------------------------------------------


class TestSaveCgimageToPng:
    def test_saves_and_returns_true(self, tmp_path, monkeypatch):
        """Successful save returns True."""
        from wenzi.screenshot import annotation

        mock_quartz = MagicMock()
        mock_quartz.CGImageDestinationCreateWithURL.return_value = MagicMock()
        mock_quartz.CGImageDestinationFinalize.return_value = True
        monkeypatch.setattr(annotation, "Quartz", mock_quartz, raising=False)

        mock_nsurl_cls = MagicMock()
        monkeypatch.setattr(annotation, "NSURL", mock_nsurl_cls, raising=False)

        # Patch at the import level inside the function
        import sys
        monkeypatch.setitem(sys.modules, "Quartz", mock_quartz)

        mock_foundation = MagicMock()
        mock_foundation.NSURL = mock_nsurl_cls
        monkeypatch.setitem(sys.modules, "Foundation", mock_foundation)

        path = str(tmp_path / "test.png")
        cg_image = MagicMock()

        result = annotation.save_cgimage_to_png(cg_image, path)
        assert result is True
        mock_quartz.CGImageDestinationAddImage.assert_called_once()

    def test_returns_false_on_failure(self, tmp_path, monkeypatch):
        """Failed finalize returns False."""
        from wenzi.screenshot import annotation

        mock_quartz = MagicMock()
        mock_quartz.CGImageDestinationCreateWithURL.return_value = MagicMock()
        mock_quartz.CGImageDestinationFinalize.return_value = False

        mock_nsurl_cls = MagicMock()

        import sys
        monkeypatch.setitem(sys.modules, "Quartz", mock_quartz)

        mock_foundation = MagicMock()
        mock_foundation.NSURL = mock_nsurl_cls
        monkeypatch.setitem(sys.modules, "Foundation", mock_foundation)

        path = str(tmp_path / "test.png")
        result = annotation.save_cgimage_to_png(MagicMock(), path)
        assert result is False

    def test_creates_parent_directory(self, tmp_path, monkeypatch):
        """Parent directory is created if it doesn't exist."""
        from wenzi.screenshot import annotation

        mock_quartz = MagicMock()
        mock_quartz.CGImageDestinationCreateWithURL.return_value = MagicMock()
        mock_quartz.CGImageDestinationFinalize.return_value = True

        mock_nsurl_cls = MagicMock()

        import sys
        monkeypatch.setitem(sys.modules, "Quartz", mock_quartz)
        mock_foundation = MagicMock()
        mock_foundation.NSURL = mock_nsurl_cls
        monkeypatch.setitem(sys.modules, "Foundation", mock_foundation)

        nested = tmp_path / "a" / "b"
        path = str(nested / "test.png")
        annotation.save_cgimage_to_png(MagicMock(), path)
        assert nested.exists()


# ---------------------------------------------------------------------------
# AnnotationLayer — temp file cleanup
# ---------------------------------------------------------------------------


class TestAnnotationLayerCleanup:
    """Verify temp files are removed on close()."""

    def test_close_removes_temp_file(self, tmp_path):
        layer = AnnotationLayer()
        tmp_file = tmp_path / "screenshot.png"
        tmp_file.write_bytes(b"fake")
        layer._tmp_image_path = str(tmp_file)
        layer._open = True
        layer._webview = MagicMock()
        layer._panel = MagicMock()

        layer.close()

        assert not tmp_file.exists()
        assert layer._tmp_image_path is None

    def test_close_handles_missing_temp_file(self, tmp_path):
        """close() should not raise if the temp file is already gone."""
        layer = AnnotationLayer()
        layer._tmp_image_path = str(tmp_path / "nonexistent.png")
        layer._open = True
        layer._webview = MagicMock()
        layer._panel = MagicMock()

        layer.close()  # should not raise
        assert layer._tmp_image_path is None

    def test_close_idempotent(self):
        """Calling close() twice should be safe."""
        layer = AnnotationLayer()
        layer._open = False
        layer.close()
        layer.close()  # should not raise


# ---------------------------------------------------------------------------
# AnnotationLayer — clipboard write
# ---------------------------------------------------------------------------


class TestAnnotationLayerClipboard:
    """Verify clipboard writing with both PNG and TIFF."""

    def test_copy_to_clipboard_writes_png_and_tiff(self, monkeypatch):
        import sys

        # Build mock AppKit
        mock_appkit = MagicMock()
        mock_pb = MagicMock()
        mock_appkit.NSPasteboard.generalPasteboard.return_value = mock_pb
        mock_appkit.NSPasteboardTypePNG = "public.png"
        mock_appkit.NSPasteboardTypeTIFF = "public.tiff"

        # NSData
        mock_ns_data = MagicMock()
        mock_appkit.NSData.dataWithBytes_length_.return_value = mock_ns_data

        # NSImage with TIFF representation
        mock_image = MagicMock()
        mock_tiff_data = MagicMock()
        mock_image.TIFFRepresentation.return_value = mock_tiff_data
        mock_appkit.NSImage.alloc.return_value.initWithData_.return_value = mock_image

        monkeypatch.setitem(sys.modules, "AppKit", mock_appkit)

        layer = AnnotationLayer()
        png_bytes = b"\x89PNG\r\n\x1a\nfake"
        layer._copy_to_clipboard(png_bytes)

        # Pasteboard should be cleared
        mock_pb.clearContents.assert_called_once()

        # Both PNG and TIFF should be written
        calls = mock_pb.setData_forType_.call_args_list
        assert len(calls) == 2
        assert calls[0] == call(mock_ns_data, "public.png")
        assert calls[1] == call(mock_tiff_data, "public.tiff")

    def test_copy_to_clipboard_tiff_failure_still_writes_png(self, monkeypatch):
        """If TIFF conversion fails, PNG should still be written."""
        import sys

        mock_appkit = MagicMock()
        mock_pb = MagicMock()
        mock_appkit.NSPasteboard.generalPasteboard.return_value = mock_pb
        mock_appkit.NSPasteboardTypePNG = "public.png"
        mock_appkit.NSPasteboardTypeTIFF = "public.tiff"

        mock_ns_data = MagicMock()
        mock_appkit.NSData.dataWithBytes_length_.return_value = mock_ns_data

        # NSImage returns None (failure)
        mock_appkit.NSImage.alloc.return_value.initWithData_.return_value = None

        monkeypatch.setitem(sys.modules, "AppKit", mock_appkit)

        layer = AnnotationLayer()
        layer._copy_to_clipboard(b"\x89PNG")

        # PNG should still be written
        calls = mock_pb.setData_forType_.call_args_list
        assert len(calls) == 1
        assert calls[0] == call(mock_ns_data, "public.png")


# ---------------------------------------------------------------------------
# AnnotationLayer — JS event handling
# ---------------------------------------------------------------------------


class TestAnnotationLayerEvents:
    """Test the JS event routing without PyObjC."""

    def _make_layer(self) -> AnnotationLayer:
        layer = AnnotationLayer()
        layer._open = True
        layer._webview = MagicMock()
        layer._panel = MagicMock()
        return layer

    def test_confirm_triggers_export(self):
        layer = self._make_layer()
        layer._handle_event("confirm", None)
        assert layer._pending_action == "clipboard"
        # Should have sent "export" event to JS
        layer._webview.evaluateJavaScript_completionHandler_.assert_called_once()
        js_call = layer._webview.evaluateJavaScript_completionHandler_.call_args[0][0]
        assert '"export"' in js_call

    def test_save_triggers_export(self):
        layer = self._make_layer()
        layer._handle_event("save", None)
        assert layer._pending_action == "save"
        layer._webview.evaluateJavaScript_completionHandler_.assert_called_once()

    def test_cancel_calls_on_cancel_and_closes(self):
        layer = self._make_layer()
        cancel_cb = MagicMock()
        layer._on_cancel = cancel_cb

        layer._handle_event("cancel", None)

        cancel_cb.assert_called_once()
        assert not layer._open

    def test_exported_clipboard_action(self, monkeypatch):
        """exported event with pending clipboard action copies and calls on_done."""
        import sys
        mock_appkit = MagicMock()
        mock_pb = MagicMock()
        mock_appkit.NSPasteboard.generalPasteboard.return_value = mock_pb
        mock_appkit.NSPasteboardTypePNG = "public.png"
        mock_appkit.NSPasteboardTypeTIFF = "public.tiff"
        mock_appkit.NSImage.alloc.return_value.initWithData_.return_value = None
        mock_appkit.NSSound.soundNamed_.return_value = None
        monkeypatch.setitem(sys.modules, "AppKit", mock_appkit)

        layer = self._make_layer()
        done_cb = MagicMock()
        layer._on_done = done_cb
        layer._pending_action = "clipboard"

        raw = b"fake_png_data"
        data_url = _make_data_url(raw)
        layer._handle_exported({"dataUrl": data_url})

        done_cb.assert_called_once()
        assert not layer._open

    def test_exported_without_pending_action(self):
        """exported event without pending action is a no-op."""
        layer = self._make_layer()
        layer._pending_action = None
        layer._handle_exported({"dataUrl": _make_data_url(b"data")})
        # Layer should still be open (no action taken)
        assert layer._open

    def test_exported_with_no_data(self):
        """exported event with None data is handled gracefully."""
        layer = self._make_layer()
        layer._pending_action = "clipboard"
        layer._handle_exported(None)
        # Should not crash; pending action is NOT cleared since it failed
        assert layer._open

    def test_handle_js_message_routes_events(self):
        layer = self._make_layer()
        layer._handle_event = MagicMock()

        layer._handle_js_message({
            "type": "event",
            "name": "confirm",
            "data": None,
        })

        layer._handle_event.assert_called_once_with("confirm", None)

    def test_handle_js_message_routes_console(self):
        """Console messages are logged, not treated as events."""
        layer = self._make_layer()
        layer._handle_event = MagicMock()

        layer._handle_js_message({
            "type": "console",
            "level": "info",
            "message": "test log",
        })

        layer._handle_event.assert_not_called()

    def test_handle_js_message_unknown_type(self):
        """Unknown message types are logged as warnings."""
        layer = self._make_layer()
        # Should not raise
        layer._handle_js_message({"type": "unknown_type"})


# ---------------------------------------------------------------------------
# AnnotationLayer — init event data
# ---------------------------------------------------------------------------


class TestAnnotationLayerInitData:
    """Verify the init event data structure sent to JS."""

    def test_init_event_data(self):
        region = _make_region(w=800, h=600)
        data = build_init_event_data(
            "wz-file:///tmp/screenshot.png", region, "bottom"
        )
        assert data["imageUrl"] == "wz-file:///tmp/screenshot.png"
        assert data["width"] == 800
        assert data["height"] == 600
        assert data["toolbarPosition"] == "bottom"

    def test_init_event_top_toolbar(self):
        region = _make_region(w=400, h=200)
        data = build_init_event_data(
            "wz-file:///tmp/img.png", region, "top"
        )
        assert data["toolbarPosition"] == "top"


# ---------------------------------------------------------------------------
# AnnotationLayer — sound feedback
# ---------------------------------------------------------------------------


class TestAnnotationLayerSound:
    def test_play_sound_does_not_raise(self, monkeypatch):
        """Sound playback should never raise, even on failure."""
        import sys
        mock_appkit = MagicMock()
        mock_appkit.NSSound.soundNamed_.return_value = None
        monkeypatch.setitem(sys.modules, "AppKit", mock_appkit)

        AnnotationLayer._play_sound()  # should not raise

    def test_play_sound_with_available_sound(self, monkeypatch):
        import sys
        mock_appkit = MagicMock()
        mock_sound = MagicMock()
        mock_appkit.NSSound.soundNamed_.return_value = mock_sound
        monkeypatch.setitem(sys.modules, "AppKit", mock_appkit)

        AnnotationLayer._play_sound()

        mock_sound.setVolume_.assert_called_once_with(0.3)
        mock_sound.play.assert_called_once()
