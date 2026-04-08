# Writing Efficient Tests

Lessons learned from optimizing the test suite from 76s → 29s.

## Core Principle

Tests should validate logic, not wait for I/O, timeouts, or heavy initialization. Every second spent loading models, reading 8MB files, or sleeping through debounce timers is wasted.

## Techniques

### 1. Poll, Don't Sleep

**Problem:** `time.sleep(0.3)` blocks for the full duration even when the condition is met in 5ms.

**Solution:** Poll on the actual condition with a tight interval and a generous timeout.

```python
_POLL_INTERVAL = 0.005
_POLL_TIMEOUT = 0.5

def _wait_for(predicate, timeout=_POLL_TIMEOUT):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL)
    assert predicate(), "timed out"
```

**Usage:**

```python
# Bad
ctrl.trigger_debounce()
time.sleep(0.3)
assert ctrl.result == expected

# Good
ctrl.trigger_debounce()
_wait_for(lambda: ctrl._debounce_timer is None)
assert ctrl.result == expected
```

**When to use:** Any test that waits for async/threaded behavior — debounce timers, background tasks, streaming callbacks.

### 2. Extract Constants, Monkeypatch in Tests

**Problem:** Production code uses reasonable timeouts (1.0s, 2.0s) that make tests slow.

**Solution:** Extract magic numbers into class-level constants, then monkeypatch them to small values in tests.

```python
# Production code
class RecordingController:
    _RELEASE_WAIT_TIMEOUT = 1.0  # Extracted from inline value
    _DELAYED_START_SECS = 0.15

    def on_hotkey_release(self):
        self._event.wait(self._RELEASE_WAIT_TIMEOUT)
```

```python
# Test code
def test_timeout(self, ctrl, monkeypatch):
    monkeypatch.setattr(RecordingController, "_RELEASE_WAIT_TIMEOUT", 0.05)
    ctrl.on_hotkey_release()  # Completes in 50ms, not 1000ms
```

**When to use:** Any class with hardcoded delay/timeout values that slow down tests.

### 3. Mock Heavyweight Imports via `sys.modules`

**Problem:** Importing `funasr_onnx`, `mlx`, `sherpa_onnx` loads native C extensions and allocates hundreds of MB.

**Solution:** Inject `MagicMock` into `sys.modules` before the import happens.

```python
with patch.dict("sys.modules", {
    "funasr_onnx": MagicMock(),
    "funasr_onnx.paraformer_bin": MagicMock(Paraformer=mock_cls),
}):
    result = transcriber._load_asr()
```

For Apple frameworks in headless CI:

```python
@pytest.fixture(autouse=True)
def _mock_apple_frameworks(monkeypatch):
    monkeypatch.setitem(sys.modules, "Speech", MagicMock())
    monkeypatch.setitem(sys.modules, "AVFoundation", MagicMock())
```

**When to use:** Tests that exercise logic around optional/heavy native libraries. If the test isn't validating the library itself, don't load it.

### 4. Shared `conftest.py` Fixtures with `autouse=True`

**Problem:** Multiple test files repeat the same expensive setup (loading 8MB word lists, initializing registries).

**Solution:** Create a package-level `conftest.py` with `autouse=True` fixtures.

```python
# tests/enhance/conftest.py
@pytest.fixture(autouse=True)
def _no_heavy_init(monkeypatch):
    """Skip expensive initialization not needed for enhance test logic."""
    monkeypatch.setattr(
        "wenzi.enhance.enhancer.TextEnhancer._load_vocabulary",
        lambda self: None,
    )
```

**When to use:** When 3+ test files in the same package share identical mock setup. One conftest fixture eliminates duplication and ensures no test accidentally loads the real resource.

### 5. Lazy Module Imports

**Problem:** `from wenzi.ui import *` loads all PyObjC panel classes during test collection, even if only one test needs one panel.

**Solution:** Use `__getattr__` for deferred imports in `__init__.py`.

```python
_LAZY_IMPORTS = {
    "SettingsPanel": (".settings_window", "SettingsPanel"),
    "LogViewerPanel": (".log_viewer_window", "LogViewerPanel"),
    # ...
}

def __getattr__(name):
    if name in _LAZY_IMPORTS:
        module_path, attr = _LAZY_IMPORTS[name]
        mod = importlib.import_module(module_path, __package__)
        value = getattr(mod, attr)
        globals()[name] = value  # Cache after first access
        return value
    raise AttributeError(name)
```

**When to use:** Packages that re-export many classes but most consumers only need one or two. Especially valuable when the submodules pull in heavy frameworks (PyObjC, ML libraries).

### 6. Tiered Fixtures for Optional Subsystems

### 7. Mock System Calls at the Boundary

**Problem:** AppKit calls like icon extraction and display name lookup hit the filesystem and IPC.

**Solution:** Mock at the function boundary, not deep inside AppKit.

```python
@pytest.fixture(autouse=True)
def _no_real_appkit(self, monkeypatch):
    monkeypatch.setattr(
        "wenzi.scripting.sources.app_source._get_app_icon_png",
        lambda path: None,
    )
    monkeypatch.setattr(
        "wenzi.scripting.sources.app_source._get_display_name",
        lambda path, fallback: fallback,
    )
```

Override selectively when a specific test needs controlled behavior:

```python
def test_localized_name(self, tmp_path):
    with patch("..._get_display_name", side_effect=lambda p, fb: "Notes"):
        # Test with controlled localization
```

**When to use:** Any code that calls into system frameworks (AppKit, CoreFoundation, NSFileManager). Mock the wrapper function, not the framework.

## Design Checklist for New Code

When writing production code that will be tested:

- [ ] **Timeouts/delays** are class-level constants (not inline literals)
- [ ] **Heavy I/O** (file loading, model init) happens in a dedicated method that can be mocked
- [ ] **Optional subsystem init** can be bypassed or deferred
- [ ] **System calls** are wrapped in thin functions at the module boundary
- [ ] **Package `__init__.py`** uses lazy imports if submodules are heavy

## Impact Reference

| Technique | Typical savings | Commit |
|-----------|----------------|--------|
| Lazy UI imports | 5-10s collection | `556e602` |
| `sys.modules` mock for ML libs | 10-20s | `556e602` |
| conftest `autouse` for word lists | 5-8s | `216152c` |
| Polling instead of sleep | 0.5-1s per test | `f6b9725` |
| Monkeypatch timeout constants | 0.5-1s per test | `33af398` |
| Tiered fixtures | 0.1-0.5s per test | `33af398` |

Total: **76s → 29s** (62% reduction) across 4 commits.
