# Input Context Awareness for LLM Enhancement

## Overview

Capture the user's current input environment (which app, which window) when voice recording starts, and inject this context into the LLM enhancement prompt. This allows the LLM to adapt its corrections to the user's current scenario — e.g., preserving technical terms in a code editor, using formal tone in an email client.

Additionally, store this context in both conversation history (JSONL) and preview history (in-memory), and provide a UI button in the preview panel to view the captured context.

## Data Model

New file: `src/wenzi/input_context.py`

```python
@dataclasses.dataclass
class InputContext:
    app_name: str | None = None           # e.g. "Visual Studio Code"
    bundle_id: str | None = None          # e.g. "com.microsoft.VSCode"
    window_title: str | None = None       # e.g. "enhancer.py - VoiceText"
    focused_role: str | None = None       # e.g. "AXTextArea", "AXTextField"
    focused_description: str | None = None  # e.g. "Message body", placeholder text
    browser_domain: str | None = None     # e.g. "github.com"
```

- All fields optional; `None` when unavailable or not collected at the current privacy level.
- `bundle_id` is for programmatic use only (e.g., detecting browsers); never injected into the LLM prompt.
- Provides `format_for_prompt(level: str) -> str | None` to format context for the LLM based on the configured level.
- Provides `format_for_display() -> str` to format context for the preview panel info view.
- Provides `format_for_history_tag(level: str) -> str | None` to format a short tag for conversation history entries.

## Configuration

New key in `DEFAULT_CONFIG["ai_enhance"]`:

```python
"input_context": "basic"   # "off" | "basic" | "detailed"
```

### Privacy levels

| Level | Collected fields | Injected to LLM |
|-------|-----------------|------------------|
| `off` | Nothing | Nothing |
| `basic` | `app_name`, `bundle_id` | `app_name` only |
| `detailed` | All fields (with AX graceful degradation) | All except `bundle_id` |

`capture_input_context(level)` accepts the configured level and only collects the fields allowed at that level. This ensures no extra data is gathered beyond what the user chose.

### Validation

`validate_config()` checks value is one of `{"off", "basic", "detailed"}`. Invalid values fall back to `"basic"`.

### Settings UI

In the Settings panel's AI tab, add a dropdown:

```
Input Context:  [Basic v]
                 Off / Basic / Detailed
```

Below the dropdown, a dynamic description label updates based on the selected option:

| Option | Description |
|--------|-------------|
| Off | *No app info is sent to the AI model.* |
| Basic | *App name is sent to help the AI adapt to your context.* |
| Detailed | *App name, window title, and other details are sent for better accuracy. May include sensitive info.* |

Follows the existing settings save pattern: dropdown change callback → `_call("on_input_context_change", value)` → app handler updates `config["ai_enhance"]["input_context"]` and calls `save_config()`. On panel init, reads current value from config to select the correct dropdown item.

## Collection Layer

### Module

New file: `src/wenzi/input_context.py`

Core function:

```python
def capture_input_context(level: str = "basic") -> InputContext | None:
    """Capture current input environment. Must be called from main thread."""
```

### Collection methods by field

| Field | API | Permission needed |
|-------|-----|-------------------|
| `app_name`, `bundle_id` | `NSWorkspace.sharedWorkspace().frontmostApplication()` | None |
| `window_title` | `CGWindowListCopyWindowInfo` filtered by frontmost app PID | None |
| `focused_role`, `focused_description` | AXUIElement API (focused element of frontmost app) | Accessibility |
| `browser_domain` | AXUIElement URL attribute, extract domain only; fallback: parse from `window_title` | Accessibility (fallback: none) |

### Graceful degradation

- If Accessibility permission is not granted, AX-dependent fields (`focused_role`, `focused_description`, `browser_domain` via AX) return `None`. The function does not raise or prompt — it silently degrades.
- Browser domain fallback: when AX is unavailable, attempt to extract domain from `window_title` for known browser `bundle_id`s. Heuristic: strip known browser suffixes (e.g., " - Google Chrome", " -- Mozilla Firefox") from the window title, then check if the remainder looks like a domain or URL. This is best-effort — returns `None` if no confident match.
- If `level="off"`, return `None` immediately.

### Known browser bundle IDs

```python
_BROWSER_BUNDLE_IDS = {
    "com.apple.Safari",
    "com.google.Chrome",
    "org.mozilla.firefox",
    "company.thebrowser.Browser",  # Arc
    "com.microsoft.edgemac",
    "com.brave.Browser",
}
```

### Timeout protection

AXUIElement calls may hang if the target application is unresponsive. The entire `capture_input_context()` call should complete within 500ms. If AX queries exceed this budget, abort them and return whatever fields were already collected.

### Call timing

Called in `recording_controller.on_hotkey_press()`, at the very beginning of the method body — before `_delayed_start`, sound playback, or any focus changes. At this point the user's target application is guaranteed to be in the foreground.

The hotkey callback runs on the main thread (PyObjC/AppKit event loop), which satisfies the AXUIElement main-thread requirement. If a future hotkey implementation dispatches on a background thread, `capture_input_context()` must be wrapped in `AppHelper.callAfter()` to ensure main-thread execution.

The result is stored as `self._input_context` on the controller.

## Injection Layer

### System prompt placement

Input context is injected at the **end** of the system prompt's context section, after conversation history and vocabulary — to maximize KV cache hit rate on the static prefix:

```
[mode prompt]              <- static, highest cache hit
[thinking hint]            <- static
---
对话记录：                  <- changes slowly (incremental append)
- [KC→k8s] (Terminal)
- [皮二→PR] (Chrome — github.com)

词库：                      <- changes slowly
- k8s: kubernetes [tech]

当前输入环境：Terminal — "zsh", AXTextArea
---
```

### Conversation history entry tags

Each history entry displayed to the LLM can include a short environment tag if the record has `input_context`:

- `basic` level: `(app_name)` — e.g., `(Terminal)`
- `detailed` level: `(app_name — window_title_or_domain)` — e.g., `(Chrome — github.com)`
- Old records without `input_context`: no tag, displayed as before.

The tag detail level follows the **current** configuration, not the level at which the record was originally stored.

`format_entry_line()` signature changes to:

```python
@staticmethod
def format_entry_line(entry: Dict[str, Any], context_level: str = "off") -> str:
```

The `context_level` parameter controls whether and how the environment tag is appended. The enhancer passes the current config level when calling this method.

**History cache interaction**: When the user changes `input_context` config level at runtime, the per-mode history cache (`_ModeHistoryCache`) must be invalidated to re-format entry lines with the new tag level. This can be handled by tracking the last-used `context_level` in the cache and triggering a full rebuild when it changes.

### Enhancer interface changes

Both `enhance_stream()` and `enhance()` gain a new optional parameter: `input_context: InputContext | None = None`.

The input context line is appended in `_build_system_content()` (not `_build_context_section()`), after the context section closing `---`. This keeps `_build_context_section()` focused on history + vocabulary, while `_build_system_content()` handles the final assembly including the per-request dynamic tail.

### EnhanceController pass-through

`EnhanceController.run()` gains `input_context: InputContext | None = None` and forwards it to both `_run_single()` and `_run_chain()`, which in turn pass it to `enhancer.enhance_stream()` / `enhancer.enhance()`.

## Storage Layer

### Conversation history (JSONL)

`conversation_history.log()` gains optional parameter `input_context: InputContext | None = None`.

When present, serialized via `dataclasses.asdict()` with `None` values omitted to save space:

```jsonl
{
  "timestamp": "...",
  "asr_text": "我们用 KC 来部署",
  "final_text": "我们用 k8s 来部署",
  "input_context": {"app_name": "Terminal", "bundle_id": "com.apple.Terminal", "window_title": "zsh", "focused_role": "AXTextArea"},
  ...
}
```

Old records without `input_context` are fully compatible — missing field treated as `None`.

### Preview history (in-memory)

`PreviewRecord` gains new field:

```python
input_context: InputContext | None = None
```

Set when the record is created in `preview_controller`.

## Preview Panel UI

### Context button

Add a **`📍`** button in the enhance section toolbar, alongside existing `Hotwords` / `🧠` / `Prompt ⓘ`:

```
[Hotwords] [🧠] [Prompt ⓘ] [📍]
```

- Only visible when `input_context` is not None.
- Clicking triggers `postAction('showContext')`.
- Handler calls existing `_show_info_panel("Input Context", formatted_text)`.

### Display format

```
App:      Terminal
Window:   zsh
Element:  AXTextArea
Domain:   —
```

Fields that are `None` are either omitted or shown as `—`.

Works for both live preview and history browsing (`_viewing_history_index`).

## Data Flow

```
User presses hotkey
  -> recording_controller.on_hotkey_press()
    -> capture_input_context(level=config["ai_enhance"]["input_context"])
    -> self._input_context = InputContext(...)
    -> Start recording...

Recording ends
  -> on_hotkey_release()
    -> preview mode: do_transcribe_with_preview(input_context=self._input_context)
    -> direct mode: do_transcribe_direct(input_context=self._input_context)
       -> with enhance: EnhanceController path (below)
       -> without enhance: conversation_history.log(..., input_context=...) directly

Enhancement phase
  -> EnhanceController.run(asr_text, input_context=...)
    -> _run_single() / _run_chain() forwards input_context
      -> enhancer.enhance_stream(text, input_context=...)
        -> _build_system_content(text, mode_def, input_context=...)
          -> _build_context_section(text)  # unchanged — history + vocabulary
          -> append "当前输入环境：..." at the tail

Storage phase
  -> conversation_history.log(..., input_context=input_context)
    -> JSONL record includes input_context dict

Preview history
  -> PreviewRecord(input_context=input_context)
    -> Stored in PreviewHistoryStore
    -> Viewable via 📍 button in preview panel
```

## Files to modify

| File | Change |
|------|--------|
| `src/wenzi/input_context.py` | **New** — `InputContext` dataclass + `capture_input_context()` |
| `src/wenzi/config.py` | Add `input_context` to `DEFAULT_CONFIG`, add validation |
| `src/wenzi/controllers/recording_controller.py` | Call `capture_input_context()` in `on_hotkey_press()` |
| `src/wenzi/controllers/preview_controller.py` | Pass `input_context` through the flow |
| `src/wenzi/controllers/enhance_controller.py` | Forward `input_context` to enhancer |
| `src/wenzi/enhance/enhancer.py` | Accept `input_context`, inject into prompt tail |
| `src/wenzi/enhance/conversation_history.py` | Accept and store `input_context` in `log()`, update `format_entry_line()` |
| `src/wenzi/enhance/preview_history.py` | Add `input_context` field to `PreviewRecord` |
| `src/wenzi/ui/result_window_web.py` | Add 📍 button, handle `showContext` message |
| `src/wenzi/ui/settings_window.py` | Add Input Context dropdown + dynamic description |
| `tests/test_input_context.py` | **New** — unit tests for `InputContext` and `capture_input_context()` |
| `tests/test_enhancer.py` | Test input context injection in prompt |
| `tests/test_conversation_history.py` | Test storing and reading `input_context` |
| `tests/test_preview_history.py` | Test `PreviewRecord` with `input_context` |

## Constraints

- `capture_input_context()` must be called on the main thread (AXUIElement requirement).
- `InputContext` is immutable after creation — passed as-is through the entire pipeline.
- `bundle_id` is never sent to the LLM.
- The configured level controls both collection scope and display scope.
- Old data without `input_context` is fully compatible — no migration needed.
- `input_context` field in `PreviewRecord` should not be mutated after creation.
