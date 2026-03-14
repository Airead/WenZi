"""Shared test fixtures for VoiceText test suite."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


class MockAppKitModules:
    """Container for mocked AppKit/Foundation/PyObjC modules."""

    def __init__(self, appkit, foundation, apphelper, pyobjctools, objc):
        self.appkit = appkit
        self.foundation = foundation
        self.apphelper = apphelper
        self.pyobjctools = pyobjctools
        self.objc = objc


@pytest.fixture
def mock_appkit_modules(monkeypatch):
    """Mock AppKit, Foundation, and related PyObjC modules for headless testing.

    Returns a MockAppKitModules with attributes: appkit, foundation, apphelper,
    pyobjctools, objc.  callAfter is wired to execute immediately.
    """
    mock_appkit = MagicMock()
    mock_foundation = MagicMock()
    mock_pyobjctools = MagicMock()
    mock_apphelper = MagicMock()
    mock_objc = MagicMock()

    # Make callAfter execute the callback immediately
    mock_apphelper.callAfter = lambda fn, *a, **kw: fn(*a, **kw)
    mock_pyobjctools.AppHelper = mock_apphelper

    monkeypatch.setitem(sys.modules, "AppKit", mock_appkit)
    monkeypatch.setitem(sys.modules, "Foundation", mock_foundation)
    monkeypatch.setitem(sys.modules, "PyObjCTools", mock_pyobjctools)
    monkeypatch.setitem(sys.modules, "PyObjCTools.AppHelper", mock_apphelper)
    monkeypatch.setitem(sys.modules, "objc", mock_objc)

    # NSMakeRect returns a mock with .size attribute
    def make_rect(x, y, w, h):
        r = MagicMock()
        r.size = MagicMock()
        r.size.width = w
        r.size.height = h
        return r

    mock_foundation.NSMakeRect = make_rect
    mock_foundation.NSMakeSize = MagicMock()
    mock_foundation.NSAttributedString = MagicMock()
    mock_foundation.NSMutableAttributedString = MagicMock()
    mock_foundation.NSDictionary = MagicMock()

    return MockAppKitModules(
        appkit=mock_appkit,
        foundation=mock_foundation,
        apphelper=mock_apphelper,
        pyobjctools=mock_pyobjctools,
        objc=mock_objc,
    )


def mock_panel_close_delegate(monkeypatch, module, attr_name="_PanelCloseDelegate"):
    """Reset cached delegate class and provide a mock for panel window modules.

    Usage in per-file fixture:
        from tests.conftest import mock_panel_close_delegate
        import voicetext.ui.settings_window as _sw
        _sw._PanelCloseDelegate = None
        mock_panel_close_delegate(monkeypatch, _sw)
    """
    setattr(module, attr_name, None)
    mock_delegate_instance = MagicMock()
    mock_delegate_cls = MagicMock()
    mock_delegate_cls.alloc.return_value.init.return_value = mock_delegate_instance
    monkeypatch.setattr(module, "_get_panel_close_delegate_class", lambda: mock_delegate_cls)
    return mock_delegate_cls
