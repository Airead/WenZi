# Window Manager

Vim-style window snapping, centering, and multi-monitor movement for macOS.

## Features

Manage your windows with keyboard shortcuts using familiar Vim-style keys (H/J/K/L).

### Keyboard Shortcuts

| Hotkey | Action | Description |
|--------|--------|-------------|
| `Ctrl+Cmd+H` | Snap Left | Snap window to the left edge, cycling 1/2 → 2/3 → 1/3 width |
| `Ctrl+Cmd+L` | Snap Right | Snap window to the right edge, cycling 1/2 → 2/3 → 1/3 width |
| `Ctrl+Cmd+K` | Snap Top | Snap window to the top edge, cycling 1/2 → 2/3 → 1/3 height |
| `Ctrl+Cmd+J` | Snap Bottom | Snap window to the bottom edge, cycling 1/2 → 2/3 → 1/3 height |
| `Ctrl+Cmd+R` | Full Screen | Maximize window to fill the entire screen |
| `Ctrl+Cmd+W` | Center | Center window on the current screen |
| `Ctrl+Cmd+E` | Next Screen | Move window to the next display |

### Snap Cycling

Pressing the same direction key repeatedly cycles the window through three sizes: **1/2 → 2/3 → 1/3** of the screen (similar to Hammerspoon's miro-windows-manager). Pressing a different direction resets to 1/2.

## Usage

Once the plugin is installed and enabled, all shortcuts are active immediately. Simply focus any window and press the corresponding hotkey.

### Multi-Monitor

Use `Ctrl+Cmd+E` to cycle the focused window through connected displays. Combine with snap shortcuts to position windows precisely across monitors.

## Requirements

- WenZi ≥ 0.1.14
