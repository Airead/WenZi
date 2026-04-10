# 闻字 (WenZi) - Claude Code Instructions

## Project Structure

Source code is organized into subpackages by responsibility:

```
src/wenzi/
├── app.py, config.py, statusbar.py, ...   # Root-level core modules
├── audio/           # Recording, sound feedback, recording indicator
├── transcription/   # ASR backends (FunASR, MLX Whisper, Apple Speech, Whisper API)
├── enhance/         # AI text enhancement, vocabulary, conversation history
├── ui/              # UI panels and windows (result, settings, log viewer, etc.)
├── controllers/     # Business logic controllers
├── screenshot/      # Screenshot capture + annotation (picture editor)
├── scripting/       # Plugin/scripting engine, APIs (wz namespace), launcher, sources
└── locales/         # Internationalization locale data
```

Tests mirror this structure under `tests/`.

When adding new modules, place them in the appropriate subpackage. Subpackage `__init__.py` files re-export public APIs — update them when adding new public classes/functions.

Cross-package imports from controllers/ui should use absolute paths (`from wenzi.config import ...`), not relative imports to parent package.

## Test Safety — Never Use Real User Data Paths

When writing tests that instantiate classes with default paths pointing to real user directories (e.g. `~/.config/WenZi/`), **always override those paths with `tmp_path`** to prevent tests from reading, modifying, or deleting real user data.

Known dangerous defaults:
- `ClipboardMonitor()` → `image_dir` defaults to `~/.config/WenZi/clipboard_images`. Calling `clear()` will delete all real images.
- `ClipboardMonitor(persist_path=...)` → connects to real SQLite database.
- `SnippetStore()` → `path` defaults to `~/.config/WenZi/snippets`.
- `KeychainAPI()` / `Vault()` → `vault_path` defaults to `~/.local/share/WenZi/keychain.json` and reads the real macOS Keychain master key. Always pass `vault_path=str(tmp_path / "vault.json")` and mock `wenzi.vault._keychain_get`/`_keychain_set`.

**Rule:** Always check what default paths a class uses before instantiating it in tests. Pass `tmp_path`-based paths for any file/directory parameters. Follow existing test patterns in the same file.

## Writing Efficient Tests

When writing or optimizing tests, refer to [`dev/writing-efficient-tests.md`](dev/writing-efficient-tests.md) for patterns on polling instead of sleep, mocking heavyweight imports, monkeypatching constants, shared fixtures, and other techniques that keep the test suite fast.

## UI Dialogs

This is a macOS statusbar (accessory) app built with pure PyObjC (via `statusbar.py`). Standard modal dialogs will not appear on screen because the app has no foreground presence.

When you need to show a user-facing dialog (error, warning, confirmation), use `self._topmost_alert()` instead. It activates the app, sets `NSStatusWindowLevel`, and runs a modal `NSAlert` so the dialog is always visible. Call `self._restore_accessory()` afterward to return to statusbar-only mode.

```python
self._topmost_alert(title="...", message="...")
self._restore_accessory()
```

`send_notification()` (from `statusbar.py`) may fail with `Info.plist` / `CFBundleIdentifier` errors when running directly from the terminal (`uv run`) without app bundling. This is expected during development — the function catches exceptions internally and logs them. In a packaged app notifications work normally.

## WKWebView Development Reference

When developing or modifying WKWebView-based panels, read `dev/wkwebview-pitfalls.md` first. It documents critical pitfalls including event handling, page load races, and state management.

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

This pattern was used in the former native AppKit settings panel. Now that UI has migrated to WKWebView (where state lives in JS/DOM), it is less common — but still applies to any code that attaches metadata to raw AppKit objects (e.g. NSObject delegates).

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
- See `ui/result_window_web.py` for a good reference implementation of dark mode support.

## NSVisualEffectView Memory Management

`NSVisualEffectView` with `NSVisualEffectBlendingModeBehindWindow` causes macOS to allocate a **full-screen IOSurface** (~72 MB at retina) for desktop content capture used by the blur effect. This memory is **not released** by `orderOut_` alone.

When hiding or closing any panel that contains an `NSVisualEffectView`, call the shared helper **before or after** `orderOut_`:

```python
from wenzi.ui_helpers import release_panel_surfaces

release_panel_surfaces(self._panel)   # deactivate VFX + shrink to 1×1
self._panel.orderOut_(None)
self._panel = None
```

`release_panel_surfaces(panel)` handles both steps automatically:
1. Walks the content view and its direct subviews, deactivates any `NSVisualEffectView` (`setState_(0)`)
2. Shrinks the panel to 1×1 to force Core Animation to release the backing store

When re-showing, re-activate the effect view (`setState_(1)`) **just before** `orderFront_`/`makeKeyAndOrderFront_`, so the IOSurface is only allocated while the panel is visible.

**Note:** The chooser panel (`ChooserPanel`) does **not** use `NSVisualEffectView`. It uses CSS `backdrop-filter` for frosted glass — see "CSS backdrop-filter in Transparent WKWebView" below.

## CSS backdrop-filter in Transparent WKWebView

The chooser panel uses CSS `backdrop-filter: blur() saturate()` instead of `NSVisualEffectView` for frosted glass. This is inherently fragile in transparent WKWebViews ([WebKit #275919](https://bugs.webkit.org/show_bug.cgi?id=275919)). System events (audio playback, compositing changes) can invalidate the backdrop-filter texture, causing the panel to appear transparent.

**Required architecture** (see `chooser.html`):

1. **Separate compositing layer** — `backdrop-filter` must be on `body::before` (pseudo-element), NOT on `body` itself. DOM changes in the content layer must not invalidate the blur layer.
2. **Keepalive animation** — A perpetual micro-animation (`opacity: 0.999→1`) on `body::before` forces WebKit to re-composite every frame, preventing texture reclamation.
3. **GPU hint** — `-webkit-backface-visibility: hidden` on `body::before` to force a separate compositing layer.
4. **Matching border-radius** — `body` must have the same `border-radius` and `overflow: hidden` as the native panel's corner radius to prevent edge artifacts.
5. **Transparent html** — `html { background: transparent }`, only `body::before` carries the `--bg` tinted overlay.

**When adding a new panel with frosted glass**, prefer `NSVisualEffectView` (stable, native). Only use CSS `backdrop-filter` if you need fine-grained control over blur radius/saturation and can tolerate the fragility.

## LLM max_tokens Guard

All `chat.completions.create` calls **must** include `max_tokens` to prevent runaway repetition (models sometimes loop the same tokens indefinitely). Current call sites and their limits:

| Call site | `max_tokens` | Rationale |
|-----------|-------------|-----------|
| `enhancer.verify_provider` | `1` | Connectivity check only |
| `enhancer._build_request_kwargs` | config `max_output_tokens` (default 4096) | Text enhancement — output ≈ input length |

When adding a new LLM call, always set `max_tokens` to a reasonable upper bound for the expected output.

## Audio in WebViews — Use `wz.playAudio()`, Not `new Audio()`

Playing audio via HTML5 `new Audio(url).play()` in WKWebView triggers macOS Now Playing, showing an unwanted playback icon in the menu bar. Use the built-in `wz.playAudio(url)` JS bridge method instead — it routes audio through native playback, which does not register with `MPNowPlayingInfoCenter`.

- **WebView panels** (`wz.ui.webview_panel`): use `wz.playAudio(url)` in JavaScript
- **Chooser preview HTML** (no `wz` bridge): use `webkit.messageHandlers.chooser.postMessage({type: 'playAudio', url: url})`
- The built-in handler is registered in `WebViewPanel._builtin_play_audio` and `ChooserPanel._play_audio_url`

**ChooserPanel uses `AVAudioPlayer`** (AVFoundation) instead of `NSSound` (AppKit). `NSSound` operations interfere with AppKit window server compositing, which breaks CSS `backdrop-filter` in the chooser's transparent WKWebView. Download happens on a background thread; `play()` is dispatched to the main thread via `AppHelper.callAfter`.

## CGEventTap — Use ctypes, Not PyObjC

CGEventTap callbacks **must** use the ctypes bindings in `wenzi/_cgeventtap.py`, NOT PyObjC's `Quartz.CGEventTapCreate`. PyObjC's callback bridge internally retains `CGEventRef` wrappers (CF types freed by `CFRelease`, not autorelease), accumulating ~42 MB over 7 hours. The ctypes path bypasses the bridge entirely — callbacks receive raw `c_void_p` pointers with no `CFRetain`.

**Rule:** When adding a new CGEventTap:

```python
from wenzi import _cgeventtap as cg

def _callback(self, proxy, event_type, event, refcon):
    # event is a raw c_void_p integer — no PyObjC wrapper
    keycode = cg.CGEventGetIntegerValueField(event, cg.kCGKeyboardEventKeycode)
    flags = cg.CGEventGetFlags(event)
    # ... process ...
    return event  # pass through (active tap) or None/0 (swallow / listen-only)

def start(self):
    def _raw_cb(proxy, event_type, event, refcon):
        return self._callback(proxy, event_type, event, refcon) or 0
    self._ctypes_cb = cg.CGEventTapCallBack(_raw_cb)  # MUST store to prevent GC!
    self._tap = cg.CGEventTapCreate(
        cg.kCGSessionEventTap, cg.kCGHeadInsertEventTap,
        cg.kCGEventTapOptionListenOnly, mask, self._ctypes_cb, None,
    )
    # ... CFRunLoop setup via cg.CFMachPortCreateRunLoopSource, etc.
```

**Key rules:**
- Store `self._ctypes_cb` — if the ctypes callback is garbage collected, the tap segfaults
- Synthetic events from `cg.CGEventCreateKeyboardEvent` must be `cg.CFRelease`'d after posting
- The `_raw_cb` trampoline converts `None` returns to `0` (ctypes `c_void_p` cannot handle `None`)
- Set `self._ctypes_cb = None` in `stop()` only after the tap is disabled and the run loop is stopped

See `hotkey.py` (`_QuartzAllKeysListener`, `TapHotkeyListener`, `KeyRemapListener`) and `snippet_expander.py` (`SnippetExpander`) for reference implementations.

## Launcher (Chooser) Panel Lifecycle

The chooser panel (`ChooserPanel`) uses a **hide/reuse** strategy for performance. WKWebView creation + HTML loading is expensive (~200-500ms), so the panel is kept alive across open/close cycles:

- **`close()`** — hides the panel (`orderOut_`), breaks `_panel_ref` back-references to prevent retain cycles, then loads empty HTML to release IOSurface compositing layer buffers. Sets `_page_loaded = False`.
- **`destroy()`** — fully tears down the panel and webview. Only called during `engine.reload()` when HTML/i18n may have changed.
- **`show()`** has three paths:
  1. **Hot path** (`_page_loaded` is True) — reconnects refs and resets UI via JS. Fastest, no HTML reload.
  2. **Warm path** (panel + webview alive, `_page_loaded` is False) — reconnects refs and reloads the cached `_chooser.html` from disk. The panel is hidden (alpha=0) until `_on_page_loaded` reveals it (alpha=1).
  3. **Cold path** (no panel) — builds everything from scratch.

When modifying `close()`, do NOT set `self._panel = None` or `self._webview = None` — this would break the reuse path and force a cold start on every open. To prevent retain cycles while hidden, nil out `_panel_ref` on the message handler, navigation delegate, and panel delegate instead.

## Screenshot & Picture Editor

### Architecture

Screenshot uses macOS `screencapture -i` for region selection (supports drag selection + Space to switch to window capture), then opens an annotation editor built with WKWebView + Fabric.js.

```
src/wenzi/screenshot/
├── __init__.py          # Exports AnnotationLayer
├── annotation.py        # WKWebView annotation layer (composes WebViewPanel)
└── templates/
    ├── annotation.html  # Canvas annotation UI
    ├── annotation.css   # Toolbar and tool styles
    ├── annotation.js    # 8 annotation tools + undo/redo + export
    └── fabric.min.js    # Fabric.js v6.6.1 (bundled locally)
```

### Flow

1. User presses hotkey (`Cmd+Ctrl+A` by default, configurable in Settings → Screenshot)
2. `screencapture -i` runs in a background thread — macOS shows native selection UI
3. On success, `AnnotationLayer` opens the image in a titled WKWebView panel
4. User annotates with tools (rect, ellipse, arrow, line, pen, mosaic, text, numbered markers)
5. Double-click / Enter / ✓ → exports Canvas as PNG → clipboard; save icon → NSSavePanel

### Configuration

```json
{
  "screenshot": {
    "enabled": false,
    "hotkey": "cmd+ctrl+a"
  }
}
```

Screenshot is **disabled by default**. Enable it in Settings → Screenshot. The hotkey listener only starts when `enabled` is true.

### AnnotationLayer API

`AnnotationLayer` composes `WebViewPanel` — it does NOT duplicate bridge JS, message handlers, or file scheme handlers. All WKWebView infrastructure is inherited.

Key parameter: `delete_on_close` — set to `True` for screenshot temp files (auto-cleaned), `False` for plugin-provided user files (preserved).

### Plugin API — `wz.ui.picture_editor`

Plugins can open the annotation editor for any image file:

```python
wz.ui.picture_editor(
    image_path="/path/to/image.jpg",
    on_done=lambda: wz.log("Copied to clipboard"),
    on_cancel=lambda: wz.log("Cancelled"),
)
```

- Supports all macOS image formats: PNG, JPG, GIF, BMP, WebP, TIFF, etc.
- The original image file is **never** modified or deleted.
- `on_done` and `on_cancel` are optional.

### PyInstaller Packaging

The `templates/` directory must be included in both spec files:
```python
datas=[
    ('src/wenzi/screenshot/templates', 'wenzi/screenshot/templates'),
]
```

## Plugin Secret Storage — `wz.keychain`

Plugins must use `wz.keychain` (not `wz.store`) for sensitive data like API tokens and credentials. `wz.store` writes plaintext JSON; `wz.keychain` encrypts with AES-256-GCM.

```python
wz.keychain.set("raindrop.token", token)   # encrypt + store → returns bool
token = wz.keychain.get("raindrop.token")   # decrypt + return → str or None
wz.keychain.delete("raindrop.token")        # remove entry
wz.keychain.keys()                          # list all keys
```

**Architecture:** A single AES-256-GCM master key is stored in macOS Keychain (account `scripting.vault.master_key`). Encrypted secrets are stored in `~/.local/share/WenZi/keychain.json`. The master key is auto-generated on first access.

**Graceful degradation:** When macOS Keychain is unavailable (e.g. headless environments), `get()` returns None, `set()` returns False, `delete()` is a no-op. Plugins should handle None returns.

**Note:** This is separate from the core app's `wenzi.keychain` module (`keychain_get`/`keychain_set`) which stores provider API keys directly in macOS Keychain. `wz.keychain` is for the plugin/scripting layer only.

## Plugin Menu API — `wz.menu`

Plugins can enumerate and trigger both WenZi's own statusbar menu and the frontmost application's menu bar.

### WenZi Menu

```python
items = wz.menu.list()              # nested tree: [{title, key, state, has_action, children}, ...]
items = wz.menu.list(flat=True)     # flat list with "path" field (e.g. "Parent > Child")
wz.menu.trigger("Settings...")      # trigger by title
wz.menu.trigger("Parent > Child")   # trigger nested item by path
```

### Frontmost App Menu (Accessibility API)

```python
items = wz.menu.app_menu()          # flat list from the app active before chooser opened
items = wz.menu.app_menu(pid=1234)  # explicit pid
wz.menu.app_menu_trigger(item)      # activate app, re-find by path, AXPress
```

Each app menu item dict contains: `title`, `path`, `enabled`, `shortcut`, `_ax_element`.

**Requirements:** Accessibility permission (System Settings → Privacy → Accessibility). The system Apple menu is automatically excluded from `app_menu()` results.

**Trigger behavior:** `app_menu_trigger()` activates the target app first, waits briefly, then re-locates the menu item by path in the AX tree before pressing. The stored `_ax_element` is NOT reused because it becomes stale when the app loses focus.

**Architecture:** `MenuAPI` is injected with the app's root `StatusMenuItem` (for WenZi menus) and the wz namespace (for dynamic chooser access to get the previous-app pid). The wz namespace reference ensures the current `ChooserAPI` instance is always used, even after script reloads.

## Usage Statistics

`UsageStats` (`src/wenzi/usage_stats.py`) buffers data in memory and flushes to disk periodically (every 30s) or on `shutdown()`. This avoids disk I/O on every event.

When adding new user-facing behaviors or interactions, always add corresponding tracking:

1. Add counter(s) to `_empty_totals()`
2. Add a `record_*()` method in `UsageStats`
3. Call the method at the appropriate point in `app.py`
4. Update the stats display in `_on_show_usage_stats()`
5. Add tests in `tests/test_usage_stats.py` — call `flush()` or `shutdown()` before asserting on-disk state

**Important:** `shutdown()` must be called on app quit (already wired in `app.py:_on_quit_click`). In tests, always call `shutdown()` in teardown to cancel the flush timer.

## Worktree Management

Do NOT use Claude Code's built-in `EnterWorktree`. All worktrees are managed by Worktrunk. When an isolated environment is needed, prompt the user to create one with Worktrunk.

## Pre-PR Rebase

**MANDATORY before creating a pull request.** First check if the branch needs rebasing (`git fetch origin main && git merge-base --is-ancestor origin/main HEAD`). If the branch is already up-to-date with `main`, skip the rebase. Otherwise:

1. Back up the current branch: `git branch <branch>-backup`
2. Rebase: `git rebase origin/main`
3. If conflicts arise, resolve them, then `git rebase --continue`
4. Only proceed to push/PR after a clean rebase

## Pre-PR Local Verification

**MANDATORY gate before creating a pull request.** You MUST run both checks below and ensure they pass with zero errors BEFORE pushing or creating the PR. Do NOT proceed to `git push` or `gh pr create` until both pass:

```bash
uv run ruff check              # Lint — must have 0 errors
uv run pytest tests/ -v --cov=wenzi  # Tests — must all pass
```

If either check fails, fix all errors first, commit the fixes, then re-run until both are clean. Do not create a PR with known failures — GitHub Actions branch protection requires CI to be green before merging.

**Test warnings must also be addressed.** Pytest warnings (e.g. `DeprecationWarning`, `RuntimeWarning`, `ResourceWarning`) indicate potential issues and should be investigated and fixed, not ignored.

This mirrors the CI pipeline in `.github/workflows/test.yml`.

## Post-Merge Cleanup

After a PR is merged, delete both the local and remote feature branches:

```bash
git checkout main && git pull origin main
git branch -d <branch>
git push origin --delete <branch>   # skip if GitHub auto-deleted it
```

## Release Process

1. Ensure all changes are committed and tests pass (`uv run pytest tests/`)
2. Review and update `WenZi.spec` and `WenZi-Lite.spec` — this step is critical to avoid runtime errors in the packaged app. Both spec files must be kept in sync (Lite excludes local ASR packages like mlx/sherpa_onnx/funasr but shares all other modules):
   - **`hiddenimports`**: sync with all current wenzi modules (scan `src/wenzi/` for new `.py` files) and any lazily/conditionally imported third-party packages
   - **`datas`**: ensure non-Python resource files referenced via `os.path.dirname(__file__)` are included (e.g. `src/wenzi/audio/sounds` → `wenzi/audio/sounds`). PyInstaller does NOT auto-bundle data files from source directories
   - **`collect_all`**: use for third-party packages with native extensions or bundled data (e.g. `mlx`, `sherpa_onnx`, `librosa`). Without this, native `.so/.dylib` or data files will be missing at runtime
   - **Removed modules**: delete entries for modules that no longer exist in the codebase
3. Sync plugins: verify each `plugin.toml` `files` list matches disk (`ls plugins/*/`), then `make sync-registry`
4. Update version in `pyproject.toml` (single source of truth — all other files read from it dynamically)
5. Run `uv lock` to sync `uv.lock` with the new version — this is **required** because `uv.lock` records the package version and won't update until `uv lock` is explicitly run
6. Commit the version bump together with `uv.lock`: `git add pyproject.toml uv.lock && git commit -m "chore: bump version to X.Y.Z"`
7. Tag: `git tag vX.Y.Z`
8. Push: `git push && git push --tags`
