# Menu Search

Search and trigger menu items of the frontmost app and WenZi itself (prefix: `m`).

## Features

### Dual Menu Search

Searches two menu sources simultaneously:
- **Frontmost app** — scans the active application's menu bar via macOS Accessibility API
- **WenZi** — scans WenZi's own statusbar menu

### Fuzzy Matching

Matches against both menu item titles and full menu paths (e.g., `File > Export > PDF`). Results are ranked by relevance.

### Rich Display

Each result shows:
- Menu item title
- Full menu path and keyboard shortcut (when available)
- WenZi items are labeled with a `WenZi` badge
- Checkmark indicator for toggled menu items

### Actions

| Key | Action |
|-----|--------|
| `Enter` | Trigger the selected menu item |

## Usage

1. Open the WenZi launcher
2. Type `m` followed by your search query (e.g., `m export`)
3. Browse results — app menus appear first, then WenZi menus
4. Press Enter to trigger the selected item

## Requirements

- WenZi ≥ 0.1.14
- Accessibility permission (System Settings → Privacy & Security → Accessibility)
