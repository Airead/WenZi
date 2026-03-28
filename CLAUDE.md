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
└── controllers/     # Business logic controllers
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

## LLM max_tokens Guard

All `chat.completions.create` calls **must** include `max_tokens` to prevent runaway repetition (models sometimes loop the same tokens indefinitely). Current call sites and their limits:

| Call site | `max_tokens` | Rationale |
|-----------|-------------|-----------|
| `enhancer.verify_provider` | `1` | Connectivity check only |
| `enhancer._build_request_kwargs` | config `max_output_tokens` (default 4096) | Text enhancement — output ≈ input length |
| `vocabulary_builder._extract_batch` | config `vocabulary.max_output_tokens` (default 4096) | Vocab extraction — ≤60 pipe-delimited lines |

When adding a new LLM call, always set `max_tokens` to a reasonable upper bound for the expected output.

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

## Usage Statistics

When adding new user-facing behaviors or interactions, always add corresponding tracking to `UsageStats` (`src/wenzi/usage_stats.py`):

1. Add counter(s) to `_empty_totals()`
2. Add a `record_*()` method in `UsageStats`
3. Call the method at the appropriate point in `app.py`
4. Update the stats display in `_on_show_usage_stats()`
5. Add tests in `tests/test_usage_stats.py`

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
