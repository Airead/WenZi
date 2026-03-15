# Scripting

VoiceText includes a Python-based scripting system that lets you automate macOS tasks — launch apps with leader keys, bind global hotkeys, show alerts, and more.

## Quick Start

1. **Enable scripting** in Settings → General → Scripting, or set it directly in `config.json`:

   ```json
   {
     "scripting": {
       "enabled": true
     }
   }
   ```

2. **Create your script** at `~/.config/VoiceText/scripts/init.py`:

   ```python
   vt.leader("cmd_r", [
       {"key": "w", "app": "WeChat"},
       {"key": "s", "app": "Slack"},
       {"key": "t", "app": "iTerm"},
   ])
   ```

3. **Restart VoiceText**. Hold right Command, see the mapping panel, press a letter key to launch the app.

## Leader Keys

Leader keys let you hold a trigger key (like right Command) and then press a second key to perform an action. A floating panel shows available mappings while the trigger key is held.

```python
vt.leader("cmd_r", [
    {"key": "w", "app": "WeChat"},
    {"key": "f", "app": "Safari"},
    {"key": "g", "app": "/Users/me/Applications/Google Chrome.app"},
    {"key": "i", "exec": "/usr/local/bin/code ~/work/projects", "desc": "projects"},
    {"key": "d", "desc": "date", "func": lambda: (
        vt.pasteboard.set(vt.date("%Y-%m-%d")),
        vt.notify("Date copied", vt.date("%Y-%m-%d")),
    )},
    {"key": "r", "desc": "reload", "func": lambda: vt.reload()},
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
vt.leader("cmd_r", [...])   # Right Command for apps
vt.leader("alt_r", [...])   # Right Alt for utilities
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

## API Reference

### `vt.leader(trigger_key, mappings)`

Register a leader-key configuration.

```python
vt.leader("cmd_r", [
    {"key": "w", "app": "WeChat"},
])
```

### `vt.app.launch(name)`

Launch or focus an application. Accepts app name or full path.

```python
vt.app.launch("Safari")
vt.app.launch("/Applications/Visual Studio Code.app")
```

### `vt.app.frontmost()`

Return the localized name of the frontmost application.

```python
name = vt.app.frontmost()  # e.g. "Finder"
```

### `vt.alert(text, duration=2.0)`

Show a brief floating message on screen. Auto-dismisses after `duration` seconds.

```python
vt.alert("Hello!", duration=3.0)
```

### `vt.notify(title, message="")`

Send a macOS notification.

```python
vt.notify("Build complete", "All tests passed")
```

### `vt.pasteboard.get()`

Return the current clipboard text, or `None`.

```python
text = vt.pasteboard.get()
```

### `vt.pasteboard.set(text)`

Set the clipboard text.

```python
vt.pasteboard.set("Hello, world!")
```

### `vt.keystroke(key, modifiers=None)`

Synthesize a keystroke via Quartz CGEvent.

```python
vt.keystroke("c", modifiers=["cmd"])       # Cmd+C
vt.keystroke("v", modifiers=["cmd"])       # Cmd+V
vt.keystroke("space")                       # Space
vt.keystroke("a", modifiers=["cmd", "shift"])  # Cmd+Shift+A
```

### `vt.execute(command, background=True)`

Execute a shell command.

```python
vt.execute("open ~/Downloads")             # Background (returns None)
output = vt.execute("date", background=False)  # Foreground (returns stdout)
```

### `vt.timer.after(seconds, callback)`

Execute a function once after a delay. Returns a `timer_id`.

```python
tid = vt.timer.after(5.0, lambda: vt.alert("5 seconds passed"))
```

### `vt.timer.every(seconds, callback)`

Execute a function repeatedly at an interval. Returns a `timer_id`.

```python
tid = vt.timer.every(60.0, lambda: vt.notify("Reminder", "Take a break"))
```

### `vt.timer.cancel(timer_id)`

Cancel a timer.

```python
tid = vt.timer.every(10.0, my_func)
vt.timer.cancel(tid)
```

### `vt.date(format="%Y-%m-%d")`

Return the current date/time as a formatted string.

```python
vt.date()              # "2025-03-15"
vt.date("%H:%M:%S")   # "14:30:00"
vt.date("%Y-%m-%d %H:%M")  # "2025-03-15 14:30"
```

### `vt.reload()`

Reload all scripts. Stops current listeners, re-reads `init.py`, and restarts.

```python
vt.reload()
```

## Examples

### App Launcher

```python
vt.leader("cmd_r", [
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
vt.leader("alt_r", [
    {"key": "d", "desc": "date → clipboard", "func": lambda: (
        vt.pasteboard.set(vt.date("%Y-%m-%d")),
        vt.notify("Date copied", vt.date("%Y-%m-%d")),
    )},
    {"key": "t", "desc": "timestamp", "func": lambda: (
        vt.pasteboard.set(vt.date("%Y-%m-%d %H:%M:%S")),
        vt.alert("Timestamp copied"),
    )},
    {"key": "r", "desc": "reload scripts", "func": lambda: vt.reload()},
])
```

### Timed Reminders

```python
# Remind to take a break every 30 minutes
vt.timer.every(1800, lambda: vt.notify("Break", "Stand up and stretch!"))
```

### Quick Actions with Hotkeys

```python
# Ctrl+Cmd+N to open a new note
vt.hotkey.bind("ctrl+cmd+n", lambda: vt.execute("open -a Notes"))
```

## Script Environment

- Scripts run as standard Python with full access to `import`
- The `vt` object is available as a global variable — no import needed
- Errors in scripts are caught and displayed as alerts
- Scripts are loaded once at startup; use `vt.reload()` to re-read changes
- Script path: `~/.config/VoiceText/scripts/init.py`
- Custom script directory can be set via `"scripting": {"script_dir": "/path/to/scripts"}` in config

## Security

Scripts run as **unsandboxed Python** with the same permissions as VoiceText itself. This means a script can:

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
- Restart VoiceText after enabling
- Check logs at `~/Library/Logs/VoiceText/voicetext.log` for errors

**Leader key not responding?**
- Ensure VoiceText has Accessibility permission (System Settings → Privacy & Security → Accessibility)
- Verify the trigger key name is correct (e.g. `cmd_r` not `right_cmd`)

**Alert panel not visible?**
- The panel requires Accessibility permission to display over other apps

**Script errors?**
- Syntax errors and exceptions are logged and shown as alerts
- Check `~/Library/Logs/VoiceText/voicetext.log` for stack traces
