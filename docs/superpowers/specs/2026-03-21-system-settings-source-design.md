# System Settings Source Design

## Overview

Add a new built-in source to the Chooser (launcher) that allows searching macOS System Settings panes and sub-items, then opening them directly via URL scheme.

## Requirements

- Search granularity: panel-level AND sub-item level (e.g., "Night Shift", "Camera" permission)
- Activation: both prefix-based (default `ss`, configurable) and mixed into unprefixed search (low priority)
- Icons: real system icons for panels, sub-items inherit parent panel icon
- macOS 13 (Ventura)+ only — uses ExtensionKit architecture

## Architecture

### New File

`src/wenzi/scripting/sources/system_settings_source.py`

Follows the existing `XxxSource` class + `as_chooser_source()` pattern used by `AppSource`, `ClipboardSource`, etc.

### Class Structure

```python
class SystemSettingsSource:
    def __init__(self):
        # Scan system for panels, merge with static sub-item mapping
        ...

    def as_chooser_source(self, prefix: str = "ss") -> list[ChooserSource]:
        """Return two ChooserSource instances:
        1. Prefixed source (prefix='ss') for dedicated search
        2. Unprefixed source (prefix=None, priority=-5) for mixed search
        """
        ...
```

Returns a **list** of two `ChooserSource` instances because a single `ChooserSource` only supports one mode (prefixed or unprefixed). The engine registers both.

Update `src/wenzi/scripting/sources/__init__.py` to export `SystemSettingsSource`.

### Data Layer (Two-Tier)

**Tier 1 — Panel-level (auto-discovered at startup):**

- Scan `/System/Library/ExtensionKit/Extensions/*.appex`
- Read each `Info.plist`, filter by:
  - `EXExtensionPointIdentifier == "com.apple.Settings.extension.ui"`
  - `SettingsExtensionAttributes.allowsXAppleSystemPreferencesURLScheme == true`
- Extract: `CFBundleIdentifier` (pane ID), display name
- Scan `GeneralSettings.appex`'s `Info.plist` for the `SettingsExtensionAttributes.hosted_bundles` plist key — this is an array of dicts, each containing a `bundle_id` for General sub-panels (About, Storage, Date & Time, etc.)
- If the Extensions directory does not exist (pre-Ventura or sandboxed), gracefully return an empty list

**Tier 2 — Sub-item level (static mapping):**

A built-in dict covering:
- Privacy & Security anchors (~24 items): `Privacy_Camera`, `Privacy_Microphone`, `Privacy_ScreenCapture`, `FileVault`, `LockdownMode`, etc.
- Apple ID sub-panes: `icloud`
- Common search aliases/keywords for discoverability

Each sub-item entry:
```python
{
    "title": "Camera",
    "parent_pane_id": "com.apple.settings.PrivacySecurity.extension",
    "anchor": "Privacy_Camera",
    "keywords": ["camera", "webcam", "video", "privacy"],
}
```

**Deduplication:** If a Tier 2 sub-item's `parent_pane_id + anchor` matches a Tier 1 auto-discovered panel, the Tier 2 entry takes precedence (richer metadata).

### URL Construction

- Panel: `x-apple.systempreferences:<pane_id>`
- Sub-item with anchor (? syntax): `x-apple.systempreferences:<pane_id>?<anchor>`
- Sub-item with colon syntax: `x-apple.systempreferences:<pane_id>:<sub_id>`

**Error handling:** If `NSWorkspace.openURL_()` returns `False`, log a warning. No user-facing error — the setting pane may have been removed or renamed in a newer macOS version.

### Search Behavior

**With prefix `ss`:**
- Activates as dedicated source
- Returns all matching panels and sub-items, up to 50 results
- Uses `fuzzy_match_fields()` on title + keywords
- **Empty query** (user types `ss ` with no further text): returns all top-level panels sorted alphabetically for discoverability

**Without prefix (mixed into general search):**
- Low priority (`priority=-5`)
- Returns at most 5 high-relevance results to avoid cluttering
- Only returns items with strong match scores

### Actions

| Trigger | Behavior |
|---------|----------|
| Enter | Open setting via `NSWorkspace.sharedWorkspace().openURL_(url)` |
| Cmd+Enter | Copy the URL scheme string to clipboard |

**`action_hints`:**
```python
{"enter": "Open", "cmd_enter": "Copy URL"}
```

### Icon Handling

- **Panel icons**: Look for `.icns` or `.png` files in `.appex/Contents/Resources/`. Many modern extensions store icons in `Assets.car` (compiled asset catalogs), which cannot be trivially read with Python — these fall back to the gear icon. For extensions that do provide standalone icon files, extract and cache as PNG.
- **Sub-item icons**: Inherit from parent panel (looked up by `parent_pane_id`)
- **Fallback**: System gear icon (`NSImage.imageNamed_("NSPreferencesGeneral")`) if extraction fails or icon is in `Assets.car`

### Caching

- Panel scan results: cached in memory (immutable during app lifecycle)
- Icons: cached to disk (reused across launches, keyed by pane ID hash)
- No network requests — all data is local

### Configuration

Config key in `chooser` section:

```yaml
chooser:
  system_settings: true          # Enable/disable this source (default: true)
  prefixes:
    system_settings: "ss"        # Configurable prefix (default: "ss")
```

Follows the same pattern as other sources in `engine.py` `_register_builtin_sources()`.

### Registration

In `engine.py` `_register_builtin_sources()`:
```python
if chooser_config.get("system_settings", True):
    from wenzi.scripting.sources.system_settings_source import SystemSettingsSource
    ss_source = SystemSettingsSource()
    prefix = prefixes.get("system_settings", "ss")
    for cs in ss_source.as_chooser_source(prefix=prefix):
        self._chooser_panel.add_source(cs)
```

### ChooserItem Mapping

```python
ChooserItem(
    title="Camera",                          # Display name
    subtitle="Privacy & Security > Camera",  # Breadcrumb path
    icon=parent_panel_icon_url,              # Inherited from parent
    item_id="system_settings:Privacy_Camera", # For usage tracking
    action=lambda: open_system_settings_url(...),
    secondary_action=lambda: copy_url_to_clipboard(...),
)
```

### Usage Statistics

Per CLAUDE.md convention, add tracking to `UsageStats`:
- Add `system_settings_opened` counter to `_empty_totals()`
- Add `record_system_settings_open()` method
- Call it in the action handler when a setting is opened
- Update `_on_show_usage_stats()` display
- Add tests in `tests/test_usage_stats.py`

## Testing Strategy

**Normal paths:**
- Unit tests for pane discovery logic (mock filesystem with fake `.appex` bundles and `Info.plist`)
- Unit tests for URL construction (all three syntax variants)
- Unit tests for search/fuzzy matching (panels + sub-items)
- Unit tests for icon extraction fallback behavior
- Unit tests for `as_chooser_source()` returning two sources (prefixed + unprefixed)
- Unit tests for `action_hints` presence

**Edge cases:**
- Extensions directory does not exist → returns empty list, no crash
- Malformed `Info.plist` (missing keys, wrong types) → skipped gracefully
- Empty query with prefix → returns all top-level panels
- Duplicate pane IDs between Tier 1 and Tier 2 → Tier 2 takes precedence
- `openURL_()` returns `False` → logged, no user-facing error

## Out of Scope

- macOS 12 and earlier support
- Real-time detection of system changes (new extensions installed)
- Searching within settings values (e.g., current Wi-Fi network name)
- Localized setting names (English only in v1; system locale could be a future enhancement)
- Extracting icons from `Assets.car` compiled asset catalogs
