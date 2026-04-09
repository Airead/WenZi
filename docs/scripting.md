# Scripting

闻字 includes a Python-based scripting system that lets you automate macOS tasks — launch apps with leader keys, bind global hotkeys, show alerts, and more.

## Quick Start

1. **Enable scripting** in Settings → General → Scripting, or set it directly in `config.json`:

   ```json
   {
     "scripting": {
       "enabled": true
     }
   }
   ```

2. **Create your script** at `~/.config/WenZi/scripts/init.py`:

   ```python
   wz.leader("cmd_r", [
       {"key": "w", "app": "WeChat"},
       {"key": "s", "app": "Slack"},
       {"key": "t", "app": "iTerm"},
   ])
   ```

3. **Restart 闻字**. Hold right Command, see the mapping panel, press a letter key to launch the app.

## Leader Keys

Leader keys let you hold a trigger key (like right Command) and then press a second key to perform an action. A floating panel shows available mappings while the trigger key is held.

```python
wz.leader("cmd_r", [
    {"key": "w", "app": "WeChat"},
    {"key": "f", "app": "Safari"},
    {"key": "g", "app": "/Users/me/Applications/Google Chrome.app"},
    {"key": "i", "exec": "/usr/local/bin/code ~/work/projects", "desc": "projects"},
    {"key": "d", "desc": "date", "func": lambda: (
        wz.pasteboard.set(wz.date("%Y-%m-%d")),
        wz.notify("Date copied", wz.date("%Y-%m-%d")),
    )},
    {"key": "r", "desc": "reload", "func": lambda: wz.reload()},
])
```

### Trigger Keys

Any modifier key can be a trigger. Available names:

| Key | Name |
|-----|------|
| Right Command | `cmd_r` |
| Right Alt/Option | `alt_r` |
| Right Shift | `shift_r` |
| Right Control | `ctrl_r` |
| Left Command | `cmd` |
| Left Alt/Option | `alt` |
| Left Shift | `shift` |
| Left Control | `ctrl` |

You can register multiple leaders with different trigger keys:

```python
wz.leader("cmd_r", [...])   # Right Command for apps
wz.leader("alt_r", [...])   # Right Alt for utilities
```

### Mapping Actions

Each mapping dict requires `"key"` and one action:

| Field | Type | Description |
|-------|------|-------------|
| `key` | `str` | The sub-key to press (e.g. `"w"`, `"1"`, `"f"`) |
| `app` | `str` | App name or full `.app` path to launch/focus |
| `func` | `callable` | Python function to call |
| `exec` | `str` | Shell command to execute |
| `desc` | `str` | Optional description shown in the panel |

If `desc` is omitted, the panel displays the app name or command.

## Launcher

The Launcher is a keyboard-driven search panel (similar to Alfred or Raycast) that lets you quickly find and open apps, files, bookmarks, clipboard history, and code snippets. It is built into the scripting system and activated via a configurable hotkey.

### Activation

- **Default hotkey:** `Cmd+Space` (configurable via `scripting.chooser.hotkey` in config)
- **Per-source hotkey:** Each source can have its own direct hotkey (see Configuration below)
- **Scripting API:** `wz.chooser.show()` / `wz.chooser.toggle()`

### Search Modes

The Launcher supports two search modes:

- **Global search:** Type normally to search across all non-prefix sources (e.g. apps). Results are ranked by priority and fuzzy match score.
- **Prefix search:** Type a prefix followed by a space to activate a specific source. For example, `f readme` searches files for "readme".

### Built-in Data Sources

| Source | Prefix | Description |
|--------|--------|-------------|
| **Apps** | *(none)* | Search installed applications. Always active in global search. |
| **Calculator** | *(none)* | Math evaluation and unit conversion. Always active in global search. |
| **Commands** | `>` | Command palette — scripts register named commands here. |
| **Files** | `f` | Search files by name via macOS Spotlight. |
| **Folders** | `fd` | Search folders via macOS Spotlight. |
| **Clipboard** | `cb` | Browse clipboard history (text and images). |
| **Snippets** | `sn` | Search text snippets with keyword expansion. |
| **Bookmarks** | `bm` | Search browser bookmarks (Chrome, Safari, Arc, Edge, Brave, Firefox). |
| **System Settings** | *(none)* | Search macOS System Settings panes and sub-items. Always active in global search. |

Prefixes are configurable via `scripting.chooser.prefixes` in config. The `>` prefix is reserved for commands and cannot be changed.

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `↑` `↓` | Navigate results |
| `Enter` | Open / execute selected item |
| `⌘+Enter` | Reveal in Finder (for file-based items) |
| `⌘1` – `⌘9` | Quick select by position |
| `Tab` | Auto-complete (e.g. complete command name in `>` mode) |
| `Esc` | Close the Launcher |
| `Alt` / `Ctrl` / `Shift` (hold) | Show alternative action for selected item |

### Custom Sources

You can register your own data sources via the `@wz.chooser.source` decorator:

```python
@wz.chooser.source("todos", prefix="td", priority=5)
def search_todos(query):
    return [
        {"title": "Fix bug #123", "subtitle": "backend", "action": lambda: ...},
        {"title": "Write docs", "subtitle": "frontend", "action": lambda: ...},
    ]
```

### Commands

Scripts can register named commands that appear in the command palette (activated by typing `> ` in the Launcher). Commands support argument passing, modifier keys, and Tab completion.

```python
# Decorator style
@wz.chooser.command("greet", title="Greet", subtitle="Say hello")
def greet(args):
    name = args.strip() or "World"
    wz.notify("Hello", f"Hello, {name}!")

# Direct registration
wz.chooser.register_command(
    name="open-url",
    title="Open URL",
    subtitle="Open a URL in browser",
    action=lambda args: wz.execute(f"open {args.strip()}"),
)
```

**Argument passing:** Type the full command name followed by a space to enter args mode. For example, `> greet Alice` passes `"Alice"` as the `args` parameter. Tab-completing a command name also enters args mode.

**Modifier keys:** Commands can define alternative actions for modifier keys:

```python
wz.chooser.register_command(
    name="deploy",
    title="Deploy",
    action=lambda args: deploy(args),
    modifiers={
        "alt": {"subtitle": "Force deploy", "action": lambda args: force_deploy(args)},
    },
)
```

**Promoted commands:** By default, commands only appear when the `>` prefix is active. Set `promoted=True` to also show them in the main (unprefixed) search alongside apps and other results:

```python
@wz.chooser.command("reload", title="Reload Scripts", promoted=True)
def reload(args):
    wz.reload()
```

**Built-in help:** A promoted `help` command is always available. Type "help" in the Launcher and press Enter to see all available prefixes and their descriptions.

### Usage Learning

When enabled (default), the Launcher tracks which items you select for each query and boosts frequently used items in future results. Data is stored locally at `~/.config/WenZi/chooser_usage.json`.

## API Reference

### `wz.leader(trigger_key, mappings)`

Register a leader-key configuration.

```python
wz.leader("cmd_r", [
    {"key": "w", "app": "WeChat"},
])
```

### `wz.app.launch(name)`

Launch or focus an application. Accepts app name or full path.

```python
wz.app.launch("Safari")
wz.app.launch("/Applications/Visual Studio Code.app")
```

### `wz.app.frontmost()`

Return the localized name of the frontmost application.

```python
name = wz.app.frontmost()  # e.g. "Finder"
```

### `wz.alert(text, duration=2.0)`

Show a brief floating message on screen. Auto-dismisses after `duration` seconds.

```python
wz.alert("Hello!", duration=3.0)
```

### `wz.notify(title, message="")`

Send a macOS notification.

```python
wz.notify("Build complete", "All tests passed")
```

### `wz.ui.webview_panel(title, html, ...)`

Create a WebView panel for custom UI. Returns a `WebViewPanel` with `show()`, `close()`, `eval_js()`, `send()`, `on()`, and `handle()` methods.

```python
panel = wz.ui.webview_panel(
    title="My Panel",
    html="<h1>Hello</h1>",
    width=400, height=300,
    floating=True,
)
panel.show()
```

#### JavaScript Bridge

Inside the WebView, a `wz` object is injected at document start, providing bidirectional communication with Python:

| JS API | Description |
|--------|-------------|
| `wz.send(event, data)` | Fire-and-forget event to Python |
| `wz.call(method, data)` | Call Python handler, returns Promise |
| `wz.on(event, callback)` | Listen for events from Python |
| `wz.playAudio(url)` | Play audio via native NSSound (see below) |

#### `wz.playAudio(url)` — Audio without Now Playing

Play an audio URL using macOS native `NSSound`. This avoids the "Now Playing" icon that appears in the menu bar when using HTML5 `new Audio(url).play()` inside WKWebView.

```javascript
// In WebView JS — no Now Playing icon
wz.playAudio('https://example.com/sound.mp3');

// Standard HTML5 Audio — shows Now Playing icon (use when needed)
new Audio('https://example.com/sound.mp3').play();
```

`wz.playAudio()` is available in all `wz.ui.webview_panel()` panels automatically. For HTML rendered inside the Launcher preview, use `webkit.messageHandlers.chooser.postMessage({type: 'playAudio', url: '...'})` instead (the Launcher does not inject the `wz` JS bridge).

### `wz.pasteboard.get()`

Return the current clipboard text, or `None`.

```python
text = wz.pasteboard.get()
```

### `wz.pasteboard.set(text)`

Set the clipboard text.

```python
wz.pasteboard.set("Hello, world!")
```

### `wz.keystroke(key, modifiers=None)`

Synthesize a keystroke via Quartz CGEvent.

```python
wz.keystroke("c", modifiers=["cmd"])       # Cmd+C
wz.keystroke("v", modifiers=["cmd"])       # Cmd+V
wz.keystroke("space")                       # Space
wz.keystroke("a", modifiers=["cmd", "shift"])  # Cmd+Shift+A
```

### `wz.execute(command, background=True)`

Execute a shell command.

```python
wz.execute("open ~/Downloads")             # Background (returns None)
output = wz.execute("date", background=False)  # Foreground (returns stdout)
```

### `wz.timer.after(seconds, callback)`

Execute a function once after a delay. Returns a `timer_id`.

```python
tid = wz.timer.after(5.0, lambda: wz.alert("5 seconds passed"))
```

### `wz.timer.every(seconds, callback)`

Execute a function repeatedly at an interval. Returns a `timer_id`.

```python
tid = wz.timer.every(60.0, lambda: wz.notify("Reminder", "Take a break"))
```

### `wz.timer.cancel(timer_id)`

Cancel a timer.

```python
tid = wz.timer.every(10.0, my_func)
wz.timer.cancel(tid)
```

### `wz.date(format="%Y-%m-%d")`

Return the current date/time as a formatted string.

```python
wz.date()              # "2025-03-15"
wz.date("%H:%M:%S")   # "14:30:00"
wz.date("%Y-%m-%d %H:%M")  # "2025-03-15 14:30"
```

### `wz.reload()`

Reload all scripts. Stops current listeners, purges cached modules from the scripts directory, re-reads `init.py` (and any imported sub-modules), and restarts. All file changes are picked up on reload.

```python
wz.reload()
```

### `wz.chooser.show(initial_query=None)`

Show the Launcher panel. Optionally pre-fill the search input.

```python
wz.chooser.show()
wz.chooser.show(initial_query="f readme")
```

### `wz.chooser.close()`

Close the Launcher panel.

### `wz.chooser.toggle()`

Toggle the Launcher panel visibility.

### `wz.chooser.show_source(prefix)`

Show the Launcher with a specific source activated.

```python
wz.chooser.show_source("cb")  # Opens with clipboard history
```

### `wz.chooser.register_source(source)`

Register a `ChooserSource` object as a data source.

### `wz.chooser.unregister_source(name)`

Remove a registered data source by name.

### `wz.chooser.pick(items, callback, placeholder="Choose...")`

Use the Launcher as a generic selection UI. Shows a fixed list of items; calls `callback(item_dict)` when the user picks one, or `callback(None)` if dismissed.

```python
wz.chooser.pick(
    [{"title": "Option A"}, {"title": "Option B"}],
    callback=lambda item: print(item),
    placeholder="Pick one...",
)
```

### `@wz.chooser.on(event)`

Decorator to register a Launcher event handler.

Supported events: `open`, `close`, `select`, `delete`.

```python
@wz.chooser.on("select")
def on_select(item_info):
    print(f"Selected: {item_info['title']}")
```

### `wz.chooser.register_command(name, title, action, ...)`

Register a named command in the command palette (`>` prefix).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | *(required)* | Unique command name (single token, e.g. `"reload-scripts"`) |
| `title` | `str` | *(required)* | Human-readable title shown in the Launcher |
| `action` | `callable` | *(required)* | Callback receiving args: `action(args_str)` |
| `subtitle` | `str` | `""` | Description shown below the title |
| `icon` | `str` | `""` | Icon URL (`file://` or `data:` URI) |
| `modifiers` | `dict` | `None` | Modifier actions (see Commands section above) |
| `promoted` | `bool` | `False` | Also appear in the unprefixed main search |

```python
wz.chooser.register_command(
    name="greet",
    title="Greet",
    action=lambda args: wz.notify("Hello", args.strip() or "World"),
    promoted=True,
)
```

### `wz.chooser.unregister_command(name)`

Remove a registered command by name.

### `@wz.chooser.command(name, title, ...)`

Decorator to register a function as a Launcher command. Same parameters as `register_command` except `action` (the decorated function is the action).

```python
@wz.chooser.command("greet", title="Greet", promoted=True)
def greet(args):
    wz.notify("Hello", args.strip() or "World")
```

### `@wz.chooser.source(name, prefix=None, priority=0, description="")`

Decorator to register a search function as a Launcher data source. Set `description` so the source appears in the built-in `help` command output.

```python
@wz.chooser.source("notes", prefix="n", priority=5, description="Search notes")
def search_notes(query):
    return [{"title": "...", "action": lambda: ...}]
```

## Examples

### App Launcher

```python
wz.leader("cmd_r", [
    {"key": "1", "app": "1Password"},
    {"key": "b", "app": "Obsidian"},
    {"key": "c", "app": "Calendar"},
    {"key": "f", "app": "Safari"},
    {"key": "g", "app": "/Users/me/Applications/Google Chrome.app"},
    {"key": "n", "app": "Notes"},
    {"key": "s", "app": "Slack"},
    {"key": "t", "app": "iTerm"},
    {"key": "v", "app": "Visual Studio Code"},
    {"key": "w", "app": "WeChat"},
    {"key": "z", "app": "zoom.us"},
])
```

### Utility Keys

```python
wz.leader("alt_r", [
    {"key": "d", "desc": "date → clipboard", "func": lambda: (
        wz.pasteboard.set(wz.date("%Y-%m-%d")),
        wz.notify("Date copied", wz.date("%Y-%m-%d")),
    )},
    {"key": "t", "desc": "timestamp", "func": lambda: (
        wz.pasteboard.set(wz.date("%Y-%m-%d %H:%M:%S")),
        wz.alert("Timestamp copied"),
    )},
    {"key": "r", "desc": "reload scripts", "func": lambda: wz.reload()},
])
```

### Timed Reminders

```python
# Remind to take a break every 30 minutes
wz.timer.every(1800, lambda: wz.notify("Break", "Stand up and stretch!"))
```

### Quick Actions with Hotkeys

```python
# Ctrl+Cmd+N to open a new note
wz.hotkey.bind("ctrl+cmd+n", lambda: wz.execute("open -a Notes"))
```

## Launcher Configuration

The Launcher is configured under `scripting.chooser` in `config.json`:

```json
{
  "scripting": {
    "chooser": {
      "enabled": true,
      "hotkey": "cmd+space",
      "app_search": true,
      "file_search": true,
      "clipboard_history": false,
      "snippets": false,
      "bookmarks": true,
      "usage_learning": true,
      "switch_to_english": true,
      "recycle_mode": "preload_html",
      "prefixes": {
        "clipboard": "cb",
        "files": "f",
        "snippets": "sn",
        "bookmarks": "bm"
      },
      "source_hotkeys": {
        "clipboard": "",
        "files": "",
        "snippets": "",
        "bookmarks": ""
      }
    }
  }
}
```

| Option | Default | Description |
|--------|---------|-------------|
| `enabled` | `false` | Master switch for the Launcher |
| `hotkey` | `"cmd+space"` | Global hotkey to toggle the Launcher |
| `app_search` | `true` | Enable application search |
| `file_search` | `true` | Enable Spotlight file search |
| `clipboard_history` | `false` | Enable clipboard history tracking |
| `snippets` | `false` | Enable snippet search and expansion |
| `bookmarks` | `true` | Enable browser bookmark search |
| `usage_learning` | `true` | Track selection frequency for smarter ranking |
| `switch_to_english` | `true` | Temporarily switch to an English input source while the Launcher is open |
| `recycle_mode` | `"preload_html"` | Idle WebView strategy after the Launcher has been hidden for 60 seconds |
| `prefixes` | *(see above)* | Prefix strings to activate each source |
| `source_hotkeys` | *(empty)* | Direct hotkeys to open Launcher with a source pre-selected |

### `recycle_mode`

After the Launcher is closed, WenZi keeps the hidden chooser alive for a short
period so reopening stays fast. Once the chooser has been hidden for 60
seconds, `recycle_mode` controls what happens next:

- `destroy`: fully tear down the hidden `WKWebView`; lowest idle memory, slowest next open
- `prebuild`: destroy the old WebView, then prebuild a fresh empty panel/WebView shell; lower idle memory than `preload_html`
- `preload_html`: destroy the old WebView, then prebuild and preload `chooser.html` in the background; uses more idle memory for a faster next open
- `keep_alive`: skip idle recycle entirely and keep reusing the hidden WebView; fastest reopen, highest long-lived memory

## Script Environment

- Scripts run as standard Python with full access to `import`
- The `wz` object is available as a global variable in `init.py` — no import needed
- In sub-modules, access `wz` via `from wenzi.scripting.api import wz`
- Errors in scripts are caught and displayed as alerts
- Scripts are loaded once at startup; use `wz.reload()` to re-read changes
- Script path: `~/.config/WenZi/scripts/init.py`
- Custom script directory can be set via `"scripting": {"script_dir": "/path/to/scripts"}` in config

### Multi-file Scripts

You can split your scripts into multiple `.py` files. `init.py` is the entry point; other files in the same directory can be imported using standard `import` statements:

```
~/.config/WenZi/scripts/
├── init.py          # Entry point
├── my_sources.py    # Custom launcher sources
└── utils/
    ├── __init__.py
    └── formatting.py
```

```python
# init.py
import my_sources
from utils.formatting import fmt_date

wz.chooser.register_source(my_sources.build_source())
wz.hotkey.bind("cmd+shift+d", lambda: wz.type_text(fmt_date()))
```

```python
# my_sources.py
from wenzi.scripting.api import wz   # import wz in sub-modules

def build_source():
    @wz.chooser.source("todos", prefix="td")
    def search_todos(query):
        return [{"title": "Example todo", "action": lambda: wz.alert("Done!")}]
```

When you call `wz.reload()`, **all files** in the scripts directory are reloaded — not just `init.py`.

> **Note:** Only absolute imports are supported (`import helper`, `from utils import foo`). Relative imports (`from . import foo`) do not work because `init.py` is not loaded as a Python package.
>
> **Note:** Do not define PyObjC `NSObject` subclasses in user scripts. The Objective-C runtime does not support re-registering a class with the same name, which causes a crash on reload.

## Async / Await

All callback-based APIs (`wz.hotkey.bind`, `wz.timer.after`, `wz.timer.every`, `wz.leader`, `wz.on`, `wz.chooser.command`, `wz.chooser.register_command`, `wz.chooser.pick`, `wz.chooser.on`) transparently support `async def` callbacks. You can write asynchronous code without any extra setup:

```python
import asyncio

async def slow_task():
    await asyncio.sleep(2)
    wz.notify("Done", "Task finished!")

wz.hotkey.bind("ctrl+cmd+a", slow_task)
```

Lambdas that return coroutines also work:

```python
wz.hotkey.bind("ctrl+cmd+b", lambda: slow_task())
```

### `wz.run(coroutine)`

For ad-hoc async execution outside of callbacks, use `wz.run()`:

```python
async def fetch_and_copy(url):
    import urllib.request
    resp = await asyncio.to_thread(urllib.request.urlopen, url)
    wz.pasteboard.set(resp.read().decode())
    wz.notify("Copied", f"Content from {url}")

wz.run(fetch_and_copy("https://example.com"))
```

### How It Works

Async callbacks are automatically submitted to a background asyncio event loop (shared across the app). Unhandled exceptions are logged to `~/Library/Logs/WenZi/wenzi.log` — they are never silently swallowed.

When scripts are reloaded (`wz.reload()`), all pending async tasks from the previous script cycle are cancelled with a 2-second grace period for cleanup.

### Limitations

- **No top-level `await`** — `init.py` is executed synchronously. Use `async def` functions and register them as callbacks, or submit them via `wz.run()`.
- **Chooser sources must be synchronous** — `@wz.chooser.source()` search functions must return results immediately. Async sources are not supported because the UI needs synchronous results to render.
- **Don't call `wz.run(coro).result()` inside an async callback** — this will deadlock because you're already on the async event loop thread. Inside async functions, use `await` directly.
- **No built-in concurrency control** — pressing a hotkey rapidly can launch multiple concurrent async tasks. Use a flag or `asyncio.Lock` if you need mutual exclusion.

## Security

Scripts run as **unsandboxed Python** with the same permissions as 闻字 itself. This means a script can:

- Read and write any file your user account can access
- Execute arbitrary shell commands
- Access the network
- Read the clipboard
- Simulate keystrokes and interact with other applications

**Only run scripts you wrote yourself or have reviewed.** Do not copy-paste scripts from untrusted sources without reading them first. A malicious script could silently exfiltrate data, install software, or modify files.

Scripting is disabled by default for this reason.

## Troubleshooting

**Scripts not loading?**
- Check that `"scripting": {"enabled": true}` is set in `config.json`
- Restart 闻字 after enabling
- Check logs at `~/Library/Logs/WenZi/wenzi.log` for errors

**Leader key not responding?**
- Ensure 闻字 has Accessibility permission (System Settings → Privacy & Security → Accessibility)
- Verify the trigger key name is correct (e.g. `cmd_r` not `right_cmd`)

**Alert panel not visible?**
- The panel requires Accessibility permission to display over other apps

**Script errors?**
- Syntax errors and exceptions are logged and shown as alerts
- Check `~/Library/Logs/WenZi/wenzi.log` for stack traces
