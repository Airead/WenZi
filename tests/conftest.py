"""Shared test fixtures for WenZi test suite."""

from __future__ import annotations

import builtins
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Global safety fixtures — prevent tests from touching real system resources
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_build_type_cache():
    """Reset the build type cache between tests to prevent leakage."""
    import wenzi.app

    wenzi.app._build_type_cache = None
    yield
    wenzi.app._build_type_cache = None


@pytest.fixture(autouse=True)
def _no_real_clipboard_polling():
    """Prevent ClipboardMonitor from polling the real system clipboard."""
    with patch(
        "wenzi.scripting.clipboard_monitor.ClipboardMonitor._check_clipboard",
    ):
        yield


@pytest.fixture(autouse=True)
def _no_real_snippet_tap():
    """Prevent SnippetExpander from creating a real Quartz CGEventTap."""
    with patch(
        "wenzi.scripting.snippet_expander.SnippetExpander.start",
    ):
        yield


@pytest.fixture(autouse=True)
def _safe_default_paths(tmp_path, monkeypatch):
    """Redirect all default data paths to tmp_path.

    Two layers of protection:

    1. **Patch layer** — overrides all known ``_DEFAULT_*`` path constants
       so classes instantiated without explicit paths use tmp_path.
    2. **Guard layer** — intercepts ``builtins.open``, ``os.remove``,
       ``os.makedirs``, ``os.rename``, ``os.replace``, ``os.unlink``,
       ``shutil.rmtree``, and ``shutil.copytree`` to raise immediately
       if a test tries to *write/delete* inside the real data directory.
       This catches any path that was missed by the patch layer.

    When adding a new ``_DEFAULT_*`` path constant, add it to the
    patch dicts below.  The guard layer does not need updating.
    """
    safe_config = str(tmp_path / "wenzi_config")
    safe_data = str(tmp_path / "wenzi_data")
    safe_cache = str(tmp_path / "wenzi_cache")

    # --- Patch layer: config.py central constants --------------------------
    config_patches = {
        # XDG base directories
        "DEFAULT_CONFIG_DIR": safe_config,
        "DEFAULT_DATA_DIR": safe_data,
        "DEFAULT_CACHE_DIR": safe_cache,
        # Config files
        "DEFAULT_CONFIG_PATH": os.path.join(safe_config, "config.json"),
        "DEFAULT_ENHANCE_MODES_DIR": os.path.join(safe_config, "enhance_modes"),
        "DEFAULT_SCRIPTS_DIR": os.path.join(safe_config, "scripts"),
        "DEFAULT_SNIPPETS_DIR": os.path.join(safe_config, "snippets"),
        # Data files
        "DEFAULT_CLIPBOARD_HISTORY_PATH": os.path.join(safe_data, "clipboard_history.json"),
        "DEFAULT_CLIPBOARD_IMAGES_DIR": os.path.join(safe_data, "clipboard_images"),
        "DEFAULT_CHOOSER_USAGE_PATH": os.path.join(safe_data, "chooser_usage.json"),
        "DEFAULT_SCRIPT_DATA_PATH": os.path.join(safe_data, "script_data.json"),
        # Cache files
        "DEFAULT_ICON_CACHE_DIR": os.path.join(safe_cache, "icon_cache"),
    }
    for attr, value in config_patches.items():
        monkeypatch.setattr(f"wenzi.config.{attr}", value)

    # --- Patch layer: consumer module copies (import-time snapshots) -------
    consumer_patches = {
        "wenzi.scripting.clipboard_monitor._DEFAULT_IMAGE_DIR":
            os.path.join(safe_data, "clipboard_images"),
        "wenzi.scripting.clipboard_monitor._DEFAULT_ICON_CACHE_DIR":
            os.path.join(safe_cache, "icon_cache"),
        "wenzi.scripting.sources.snippet_source._DEFAULT_SNIPPETS_DIR":
            os.path.join(safe_config, "snippets"),
        "wenzi.scripting.sources.app_source._DEFAULT_ICON_CACHE_DIR":
            os.path.join(safe_cache, "icon_cache"),
        "wenzi.scripting.sources.bookmark_source._DEFAULT_ICON_CACHE_DIR":
            os.path.join(safe_cache, "icon_cache"),
        "wenzi.scripting.sources.file_source._DEFAULT_ICON_CACHE_DIR":
            os.path.join(safe_cache, "icon_cache"),
        "wenzi.scripting.sources.usage_tracker._DEFAULT_PATH":
            os.path.join(safe_data, "chooser_usage.json"),
        "wenzi.scripting.api.store._DEFAULT_PATH":
            os.path.join(safe_data, "script_data.json"),
        "wenzi.enhance.mode_loader.DEFAULT_MODES_DIR":
            os.path.join(safe_config, "enhance_modes"),
    }
    for attr, value in consumer_patches.items():
        monkeypatch.setattr(attr, value)

    # --- Guard layer: block writes/deletes to real XDG directories ----------
    _real_guarded_dirs = (
        os.path.expanduser("~/.config/WenZi"),
        os.path.expanduser("~/.local/share/WenZi"),
        os.path.expanduser("~/.cache/WenZi"),
    )

    def _is_guarded(path):
        """Return True if *path* is inside any real WenZi directory."""
        if not isinstance(path, (str, bytes)):
            return False
        p = os.fsdecode(path) if isinstance(path, bytes) else path
        try:
            abspath = os.path.abspath(p)
            return any(abspath.startswith(d) for d in _real_guarded_dirs)
        except (ValueError, OSError):
            return False

    def _reject(op_name, path):
        raise RuntimeError(
            f"Test attempted {op_name}() on real user data: {path}\n"
            f"Ensure this path is redirected to tmp_path via _safe_default_paths."
        )

    # Guard builtins.open for write modes
    _original_open = builtins.open

    def _guarded_open(file, mode="r", *args, **kwargs):
        if _is_guarded(file) and any(c in mode for c in "wxa"):
            _reject("open", file)
        return _original_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _guarded_open)

    # Guard destructive os operations (single-path argument)
    _GUARDED_SINGLE = {
        "os.remove": os.remove,
        "os.unlink": os.unlink,
        "os.makedirs": os.makedirs,
        "os.mkdir": os.mkdir,
    }
    for dotpath, original_fn in _GUARDED_SINGLE.items():
        _mod, fn_name = dotpath.rsplit(".", 1)

        def _make_guard(orig, name):
            def _guard(path, *a, **kw):
                if _is_guarded(path):
                    _reject(name, path)
                return orig(path, *a, **kw)
            return _guard

        monkeypatch.setattr(dotpath, _make_guard(original_fn, fn_name))

    # Guard os operations with two path arguments (src, dst)
    _GUARDED_DUAL = {
        "os.rename": os.rename,
        "os.replace": os.replace,
    }
    for dotpath, original_fn in _GUARDED_DUAL.items():
        _mod, fn_name = dotpath.rsplit(".", 1)

        def _make_dual_guard(orig, name):
            def _guard(src, dst, *a, **kw):
                if _is_guarded(src):
                    _reject(name, src)
                if _is_guarded(dst):
                    _reject(name, dst)
                return orig(src, dst, *a, **kw)
            return _guard

        monkeypatch.setattr(dotpath, _make_dual_guard(original_fn, fn_name))

    # Guard shutil operations
    import shutil

    # rmtree: single path
    _orig_rmtree = shutil.rmtree

    def _guarded_rmtree(path, *a, **kw):
        if _is_guarded(path):
            _reject("rmtree", path)
        return _orig_rmtree(path, *a, **kw)

    monkeypatch.setattr("shutil.rmtree", _guarded_rmtree)

    # copytree: check both src and dst
    _orig_copytree = shutil.copytree

    def _guarded_copytree(src, dst, *a, **kw):
        if _is_guarded(dst):
            _reject("copytree", dst)
        return _orig_copytree(src, dst, *a, **kw)

    monkeypatch.setattr("shutil.copytree", _guarded_copytree)

    # Guard sqlite3.connect
    import sqlite3
    _orig_sqlite3_connect = sqlite3.connect

    def _guarded_sqlite3_connect(database, *a, **kw):
        if _is_guarded(database):
            _reject("sqlite3.connect", database)
        return _orig_sqlite3_connect(database, *a, **kw)

    monkeypatch.setattr("sqlite3.connect", _guarded_sqlite3_connect)


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
        import wenzi.ui.settings_window as _sw
        _sw._PanelCloseDelegate = None
        mock_panel_close_delegate(monkeypatch, _sw)
    """
    setattr(module, attr_name, None)
    mock_delegate_instance = MagicMock()
    mock_delegate_cls = MagicMock()
    mock_delegate_cls.alloc.return_value.init.return_value = mock_delegate_instance
    monkeypatch.setattr(module, "_get_panel_close_delegate_class", lambda: mock_delegate_cls)
    return mock_delegate_cls
