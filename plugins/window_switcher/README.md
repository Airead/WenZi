# Window Switcher

Switch between open windows across all applications via the launcher (prefix: `w`).

## Features

### Window List

Displays all visible windows with their titles, application names, and app icons (32×32, cached to disk).

### Fuzzy Search

Type to filter windows by title or application name with fuzzy matching.

### Actions

| Key | Action |
|-----|--------|
| `Enter` | Focus the selected window |
| `Cmd+Enter` | Close the selected window |

### Icon Caching

Application icons are extracted via AppKit, converted to PNG, and cached on disk with an in-memory layer for instant display on subsequent lookups.

## Usage

1. Open the WenZi launcher
2. Type `w` to see all open windows
3. Type further to filter by window title or app name
4. Press Enter to focus, or Cmd+Enter to close

## Requirements

- WenZi ≥ 0.1.14
