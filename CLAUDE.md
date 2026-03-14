# VoiceText - Claude Code Instructions

## Project Structure

Source code is organized into subpackages by responsibility:

```
src/voicetext/
├── app.py, config.py, statusbar.py, ...   # Root-level core modules
├── audio/           # Recording, sound feedback, recording indicator
├── transcription/   # ASR backends (FunASR, MLX Whisper, Apple Speech, Whisper API)
├── enhance/         # AI text enhancement, vocabulary, conversation history
├── ui/              # UI panels and windows (result, settings, log viewer, etc.)
└── controllers/     # Business logic controllers
```

Tests mirror this structure under `tests/`.

When adding new modules, place them in the appropriate subpackage. Subpackage `__init__.py` files re-export public APIs — update them when adding new public classes/functions.

Cross-package imports from controllers/ui should use absolute paths (`from voicetext.config import ...`), not relative imports to parent package.

## UI Dialogs

This is a macOS statusbar (accessory) app built with pure PyObjC (via `statusbar.py`). Standard modal dialogs will not appear on screen because the app has no foreground presence.

When you need to show a user-facing dialog (error, warning, confirmation), use `self._topmost_alert()` instead. It activates the app, sets `NSStatusWindowLevel`, and runs a modal `NSAlert` so the dialog is always visible. Call `self._restore_accessory()` afterward to return to statusbar-only mode.

```python
self._topmost_alert(title="...", message="...")
self._restore_accessory()
```

`send_notification()` (from `statusbar.py`) may fail with `Info.plist` / `CFBundleIdentifier` errors when running directly from the terminal (`uv run`) without app bundling. This is expected during development — the function catches exceptions internally and logs them. In a packaged app notifications work normally.

## Showing NSPanel / NSWindow from Menu Callbacks

When showing an NSPanel from a menu callback (e.g. clicking "Settings..."), the window must be created and displayed **synchronously within the callback**. Do NOT use `AppHelper.callAfter()` to defer the `show()` call.

**Why:** In a statusbar app (`NSApplicationActivationPolicyAccessory`), clicking a menu item briefly activates the app for menu tracking. When the menu callback returns, the app falls back to accessory mode. If `show()` is deferred via `callAfter`, it runs after the app has deactivated — the window is created but never appears on screen.

**Correct pattern** (used by `LogViewerPanel`, `SettingsPanel`):
```python
def _on_menu_click(self, _):
    # Call show() directly — it sets activation policy internally
    self._panel.show(...)
```

**Inside `show()`**, follow this order:
1. `NSApp.setActivationPolicy_(0)` — switch to Regular (foreground)
2. Build/configure the panel
3. `panel.makeKeyAndOrderFront_(None)` — display the window
4. `NSApp.activateIgnoringOtherApps_(True)` — bring app to front

**Inside `close()`**, restore:
1. `panel.orderOut_(None)`
2. `NSApp.setActivationPolicy_(1)` — back to Accessory (statusbar-only)

## PyObjC Class Name Uniqueness

Objective-C class names are **globally unique** across the entire process. When using PyObjC to define NSObject subclasses (e.g. panel close delegates), each module must use a **distinct class name**. If two modules both define `class PanelCloseDelegate(NSObject)`, the second one will crash with `objc.error: PanelCloseDelegate is overriding existing Objective-C class`.

**Convention:** Prefix with the module/component name: `SettingsPanelCloseDelegate`, `LogViewerPanelCloseDelegate`, etc.

## No Arbitrary Attributes on AppKit Objects

Real AppKit objects (`NSButton`, `NSTextField`, etc.) do **not** allow setting arbitrary Python attributes (e.g. `btn._my_data = "foo"` raises `AttributeError`). This works with `MagicMock` in tests but crashes at runtime.

**Solution:** Use a Python-side `dict` keyed by `id(button)` to store metadata:
```python
self._btn_meta: Dict[int, Dict] = {}

def _set_meta(self, btn, **kwargs):
    self._btn_meta[id(btn)] = kwargs

def _get_meta(self, btn) -> Dict:
    return self._btn_meta.get(id(btn), {})
```

See `ui/settings_window.py` for the reference implementation.

## Dark Mode Support

All UI must support macOS dark mode. Follow these rules when writing UI code:

- **Use system semantic colors** (`NSColor.labelColor()`, `NSColor.secondaryLabelColor()`, `NSColor.textBackgroundColor()`, `NSColor.windowBackgroundColor()`, etc.) instead of hardcoded RGB values. System colors adapt automatically to light/dark appearance.
- **Never use `NSColor.blackColor()` or `NSColor.whiteColor()`** for text or backgrounds — they do not adapt. Use `NSColor.labelColor()` and `NSColor.textBackgroundColor()` instead.
- **For custom colors that must differ between modes**, use a dynamic color provider:
  ```python
  def _dynamic_color(light_rgba, dark_rgba):
      def provider(appearance):
          name = appearance.bestMatchFromAppearancesWithNames_([
              NSAppearanceNameAqua, NSAppearanceNameDarkAqua
          ])
          return NSColor.colorWithSRGBRed_green_blue_alpha_(
              *(dark_rgba if name == NSAppearanceNameDarkAqua else light_rgba)
          )
      return NSColor.colorWithName_dynamicProvider_("custom", provider)
  ```
- **Avoid deprecated `colorWithCalibratedRed_green_blue_alpha_`** — use `colorWithSRGBRed_green_blue_alpha_` or system semantic colors.
- See `ui/result_window.py` for a good reference implementation of dark mode support.

## Usage Statistics

When adding new user-facing behaviors or interactions, always add corresponding tracking to `UsageStats` (`src/voicetext/usage_stats.py`):

1. Add counter(s) to `_empty_totals()`
2. Add a `record_*()` method in `UsageStats`
3. Call the method at the appropriate point in `app.py`
4. Update the stats display in `_on_show_usage_stats()`
5. Add tests in `tests/test_usage_stats.py`

## Release Process

1. Ensure all changes are committed and tests pass (`uv run pytest tests/`)
2. Update version in `pyproject.toml` (single source of truth — all other files read from it dynamically)
3. Commit: `git commit -m "chore: bump version to X.Y.Z"`
4. Tag: `git tag vX.Y.Z`
5. Push: `git push && git push --tags`
