# 闻字 (WenZi) - Claude Code Instructions

## Project Structure

```
src/wenzi/
├── app.py, config.py, statusbar.py, ...   # Core modules
├── audio/           # Recording, sound feedback, recording indicator
├── transcription/   # ASR backends (FunASR, MLX Whisper, Apple Speech, Whisper API)
├── enhance/         # AI text enhancement, vocabulary, conversation history
├── ui/              # UI panels and windows
├── controllers/     # Business logic controllers
├── screenshot/      # Screenshot capture + annotation
├── scripting/       # Plugin/scripting engine, APIs (wz namespace), launcher
└── locales/         # i18n locale data
```

Tests mirror under `tests/`. New modules go in the appropriate subpackage; update `__init__.py` re-exports. Use absolute imports (`from wenzi.config import ...`).

## Minimum Deployment Target — macOS 26

No backwards-compatibility code. APIs like `NSGlassEffectView` used unconditionally.

## Key Rules

**Glass / UI:**
- `NSGlassEffectView` + `configure_glass_appearance()`, never `NSVisualEffectView` → [liquid-glass.md](dev/liquid-glass.md)
- `release_panel_surfaces()` before `orderOut_()`; ChooserPanel uses `removeFromSuperview()` → [memory-leak-debug.md](dev/memory-leak-debug.md)
- Semantic colors for dark mode, never `blackColor`/`whiteColor` → [ui-patterns.md](dev/ui-patterns.md)
- `_topmost_alert()` for dialogs; `wz.alert()` for notifications → [ui-patterns.md](dev/ui-patterns.md)

**WKWebView:** Read [wkwebview-pitfalls.md](dev/wkwebview-pitfalls.md) before modifying any panel. Key points: JS queue + atomic flush, show synchronously (no `callAfter`), ObjC class names globally unique, event delegation for innerHTML, `wz.playAudio()` not `new Audio()`.

**System:**
- CGEventTap: ctypes (`_cgeventtap.py`), never PyObjC → [cgeventtap.md](dev/cgeventtap.md)
- Chooser: hide/reuse, never `_panel=None` in `close()` → [chooser-lifecycle.md](dev/chooser-lifecycle.md)

**Testing:** Override real paths with `tmp_path` → [writing-efficient-tests.md](dev/writing-efficient-tests.md)

**Plugins:** `wz.keychain` for secrets (not `wz.store`), `wz.menu` for menus → [plugin-apis.md](dev/plugin-apis.md)

**LLM:** All `chat.completions.create` calls must set `max_tokens`:

| Call site | `max_tokens` |
|-----------|-------------|
| `enhancer.verify_provider` | `1` |
| `enhancer._build_request_kwargs` | config `max_output_tokens` (default 4096) |

**Usage Stats:** `UsageStats` buffers in memory, flushes every 30s / on `shutdown()`. New tracking: (1) `_empty_totals()` (2) `record_*()` method (3) call from `app.py` (4) update display (5) add tests. Always `shutdown()` in test teardown.

## Workflow

**Worktrees:** Use Worktrunk (`wt`), not Claude Code's `EnterWorktree`.

**Pre-PR rebase (mandatory):** `git fetch origin main && git merge-base --is-ancestor origin/main HEAD` — if not up-to-date: backup branch, `git rebase origin/main`, resolve conflicts.

**Pre-PR verification (mandatory):**
```bash
uv run ruff check
uv run pytest tests/ -v --cov=wenzi
```
> If `rtk` fails repeatedly (even for different commands), stop using it and switch to `uv` or direct equivalents without investigating the cause.

**Post-merge cleanup:** `git checkout main && git pull`, delete local + remote branch.

**Release:** (1) tests pass (2) sync spec files (3) sync plugins (4) bump `pyproject.toml` (5) `uv lock` (6) commit version + lock (7) tag + push.

## Dev Documentation Index

| Document | Description |
|----------|-------------|
| [liquid-glass.md](dev/liquid-glass.md) | NSGlassEffectView API, adaptive appearance, visual techniques |
| [memory-leak-debug.md](dev/memory-leak-debug.md) | IOSurface leak investigation history and fixes |
| [ui-patterns.md](dev/ui-patterns.md) | Dialogs, notifications, dark mode |
| [wkwebview-pitfalls.md](dev/wkwebview-pitfalls.md) | WKWebView pitfalls and new-panel checklist |
| [writing-efficient-tests.md](dev/writing-efficient-tests.md) | Test optimization, test safety (dangerous defaults) |
| [cgeventtap.md](dev/cgeventtap.md) | CGEventTap ctypes bindings |
| [chooser-lifecycle.md](dev/chooser-lifecycle.md) | Chooser panel hide/reuse, recycle mechanism |
| [screenshot.md](dev/screenshot.md) | Screenshot capture + annotation editor |
| [plugin-apis.md](dev/plugin-apis.md) | Plugin secrets (`wz.keychain`) and menus (`wz.menu`) |
| [vocab-hit-tracking.md](dev/vocab-hit-tracking.md) | Vocabulary four-dimension hit tracking |
| [asyncio-migration-plan.md](dev/asyncio-migration-plan.md) | Asyncio migration plan (in progress) |
| [minimax-api-behavior.md](dev/minimax-api-behavior.md) | MiniMax API behavior notes |
| [recording-shortcuts.md](dev/recording-shortcuts.md) | Recording keyboard shortcuts |
