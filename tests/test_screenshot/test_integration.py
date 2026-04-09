"""Integration tests for screenshot wiring in config.py and app.py."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


def test_default_config_has_screenshot_section():
    from wenzi.config import DEFAULT_CONFIG

    assert "screenshot" in DEFAULT_CONFIG


def test_default_config_screenshot_hotkey():
    from wenzi.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["screenshot"]["hotkey"] == "cmd+ctrl+a"


# ---------------------------------------------------------------------------
# App method tests
# ---------------------------------------------------------------------------


class _FakeApp:
    def __init__(self):
        self._screenshot_annotation = None


def _attach_methods(obj):
    import types

    from wenzi import app as app_module

    for name in ("_on_screenshot", "_show_annotation_ui",
                 "_on_screenshot_done", "_on_screenshot_cancel"):
        method = getattr(app_module.WenZiApp, name)
        setattr(obj, name, types.MethodType(method, obj))


@pytest.fixture()
def fake_app():
    obj = _FakeApp()
    _attach_methods(obj)
    return obj


@pytest.fixture()
def mock_screenshot_module():
    mock_annotation = MagicMock()
    mock_module = MagicMock()
    mock_module.AnnotationLayer.return_value = mock_annotation

    with patch.dict(sys.modules, {"wenzi.screenshot": mock_module}):
        yield {"module": mock_module, "annotation": mock_annotation}


def test_show_annotation_ui_creates_annotation(fake_app, mock_screenshot_module):
    mocks = mock_screenshot_module
    fake_app._show_annotation_ui("/tmp/test.png")
    mocks["module"].AnnotationLayer.assert_called_once()
    mocks["annotation"].show.assert_called_once()


def test_show_annotation_ui_stores_instance(fake_app, mock_screenshot_module):
    fake_app._show_annotation_ui("/tmp/test.png")
    assert fake_app._screenshot_annotation is mock_screenshot_module["annotation"]


def test_on_screenshot_done_clears_ref(fake_app):
    fake_app._screenshot_annotation = MagicMock()
    fake_app._on_screenshot_done()
    assert fake_app._screenshot_annotation is None


def test_on_screenshot_cancel_clears_ref(fake_app):
    fake_app._screenshot_annotation = MagicMock()
    fake_app._on_screenshot_cancel()
    assert fake_app._screenshot_annotation is None


def test_on_screenshot_done_noop_when_none(fake_app):
    fake_app._screenshot_annotation = None
    fake_app._on_screenshot_done()  # must not raise


def test_on_screenshot_cancel_noop_when_none(fake_app):
    fake_app._screenshot_annotation = None
    fake_app._on_screenshot_cancel()  # must not raise
