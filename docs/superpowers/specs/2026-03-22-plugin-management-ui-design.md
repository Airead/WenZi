# Plugin Management UI Design

## Overview

Add a "Plugins" tab to the Settings WebPanel for managing plugins: install, update, uninstall, enable/disable, with support for version-specific installation via plugin metadata.

## Data Model

### plugin.toml Extension

Extend the existing `plugin.toml` with `id` and `files` fields:

```toml
[plugin]
id = "com.airead.wenzi.cc-sessions"
name = "CC Sessions"
description = "Browse Claude Code sessions"
version = "1.0.0"
author = "Airead"
url = "https://github.com/Airead/WenZi"
icon = "icon.png"
min_wenzi_version = "0.1.7"
files = ["__init__.py", "scanner.py", "reader.py", "cache.py", "preview.py", "viewer.html"]
```

- `id`: Bundle ID in reverse domain format. Globally unique identifier for conflict detection, update matching, and enable/disable tracking. Plugins without `id` fall back to directory name (backward compatibility).
- `files`: Explicit list of all files in the plugin. Required for remote installation (cannot list directory contents from raw GitHub URLs). Local path sources can auto-scan but `files` is still recommended.

### install.toml

Generated in the plugin directory after UI-based installation:

```toml
[install]
source_url = "https://raw.githubusercontent.com/.../plugin.toml"
installed_version = "1.0.0"
installed_at = "2026-03-22T10:00:00"
```

Purpose:
- Distinguish UI-installed plugins from manually placed ones
- Record source URL for update checking
- Does not pollute upstream plugin.toml

### registry.toml

Central index file in the Git repository listing available plugins with inline metadata:

```toml
name = "WenZi Official"

[[plugins]]
id = "com.airead.wenzi.cc-sessions"
name = "CC Sessions"
description = "Browse Claude Code sessions"
version = "1.0.0"
author = "Airead"
min_wenzi_version = "0.1.7"
source = "https://raw.githubusercontent.com/Airead/WenZi/refs/heads/main/plugins/cc_sessions/plugin.toml"
```

Inline metadata eliminates the need to fetch individual plugin.toml files when browsing — one request per registry is enough to display the full plugin list.

### Registry Configuration

```toml
# ~/.config/WenZi/config.toml
[plugins]
extra_registries = [
    "https://raw.githubusercontent.com/someone/wenzi-plugins/main/registry.toml",
]
```

- One built-in official registry URL (hardcoded, not removable)
- Users can add/remove third-party registry URLs in Settings

### Plugin Uniqueness & Priority

- `id` (bundle ID) is the unique identifier
- Official registry has highest priority: if official and third-party registries contain the same `id`, the third-party entry is ignored (not displayed)
- Installing a third-party plugin with the same `id` as an existing official plugin is rejected with a conflict message
- Third-party plugins show an **"Unverified"** warning label; official plugins show **"Official"** label
- Signature verification is not implemented in v1; `signature` field reserved for future use

### disabled_plugins Migration

`config.disabled_plugins` changes from directory names to bundle IDs.

Migration strategy:
- `_load_plugins()` checks both directory name and bundle ID when matching against `disabled_plugins` — this ensures backward compatibility during transition
- Loading order adjustment: read plugin metadata (including `id`) first, then check disabled status
- On first encounter of a plugin with `id` field, auto-migrate the `disabled_plugins` entry from directory name to bundle ID in config
- Plugins without `id` continue using directory name as identifier

## Registry Sync

### scripts/sync_registry.py

A script that scans `plugins/*/plugin.toml`, extracts metadata, and generates `plugins/registry.toml`.

### Makefile Target

```makefile
sync-registry:
	python scripts/sync_registry.py
```

Developers run `make sync-registry` after modifying any plugin.toml.

### CI Validation

CI checks that `plugins/registry.toml` is in sync with `plugins/*/plugin.toml`. If they diverge, CI fails with a message to run `make sync-registry`.

## UI Design

### Plugins Tab Layout

Added to Settings WebPanel alongside General/Speech/LLM/AI/Launcher tabs.

**Top: Registry Management**
- List of registry sources (Official built-in + user-added)
- "Add Registry" button → URL input
- Third-party sources can be removed; Official source cannot

**Middle: Plugin List (card-style)**

Each card displays:
- Plugin icon, name, version, author, description
- Source label (Official / third-party registry name)
- Status-dependent action buttons:

| Status | Actions |
|--------|---------|
| Not installed | `Install` |
| Installed, up to date | Enable/Disable toggle + `Uninstall` |
| Installed, update available | `Update (v1.0→v1.1)` + Enable/Disable toggle + `Uninstall` |
| Manually placed (no install.toml) | Enable/Disable toggle only |

**Bottom: Manual Install**
- "Install from URL" input field + Install button
- User pastes a plugin.toml URL, system fetches metadata and confirms installation

### Interaction Flows

**Install**: Click Install → show progress → download files per `files` list → write install.toml → refresh list → prompt to reload scripts

**Update**: Click Update → download new version files (overwrite) → update install.toml → prompt to reload

**Uninstall**: Click Uninstall → confirmation dialog → delete plugin directory → refresh list

**Update Check**: Opening Plugins tab triggers background fetch of all registry URLs + comparison of remote version vs local install.toml `installed_version`

**Reload after changes**: After install/update/uninstall, show a "Reload Now" button in the Plugins tab. User clicks to trigger `ScriptEngine.reload()`. Multiple changes can be batched before reload.

### Error Handling

- **Registry fetch failure**: Show error inline (e.g. "Failed to load registry: <name>"). Still display locally installed plugins and any successfully loaded registries.
- **Plugin download failure**: Show error alert with details. Roll back partial downloads (delete incomplete plugin directory).
- **Single file download failure**: Abort the entire install, roll back, show which file failed.
- **Network timeout**: 30s timeout per request. Show "Request timed out" error.
- **Incompatible plugin**: If `min_wenzi_version` > current version, show Install button as disabled with tooltip "Requires WenZi >= X.Y.Z".

### Icon Display

- Uninstalled plugins: show default placeholder icon (plugin emoji or generic icon)
- Installed plugins: load icon from local plugin directory if `icon` field is set
- registry.toml does not include icon to keep it lightweight

### Version Comparison

Versions follow `MAJOR.MINOR.PATCH` format. Comparison uses numeric tuple parsing (split by `.`), consistent with existing `_version_compatible()` in engine.py.

## Backend Modules

### New Modules

```
src/wenzi/scripting/
├── plugin_meta.py          # Existing — extend PluginMeta fields
├── plugin_registry.py      # New — registry fetch, parse, merge, status calculation
├── plugin_installer.py     # New — download, install, update, uninstall
```

### plugin_registry.py

Responsibilities:
- Fetch registry.toml (remote URL or local path)
- Merge multiple registries with official-first deduplication by `id`
- Compare with locally installed plugins to compute status (not installed / installed / update available)

```python
class PluginStatus(Enum):
    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"
    UPDATE_AVAILABLE = "update_available"
    MANUALLY_PLACED = "manually_placed"       # no install.toml
    INCOMPATIBLE = "incompatible"             # min_wenzi_version not met

@dataclass
class PluginInfo:
    meta: PluginMeta                          # inline metadata from registry
    source_url: str                           # plugin.toml URL
    registry_name: str                        # e.g. "WenZi Official"
    status: PluginStatus
    installed_version: str | None = None      # from local install.toml
    is_official: bool = False

class PluginRegistry:
    def fetch_available(self) -> list[PluginInfo]
```

Network operations run in `threading.Thread` with `AppHelper.callAfter()` callback to the main thread, consistent with existing patterns (e.g. model download in settings_controller.py). No async/await.

### plugin_installer.py

Responsibilities:
- Fetch plugin.toml from source URL to get `files` list
- Download files relative to plugin.toml URL into `~/.config/WenZi/plugins/<dir>/`
- Write install.toml with source URL and timestamp
- Update: overwrite files + update install.toml
- Uninstall: delete plugin directory
- Pre-install validation: id conflict check, min_wenzi_version compatibility

```python
class PluginInstaller:
    def install(self, source_url: str) -> None      # runs in thread
    def update(self, plugin_id: str) -> None         # runs in thread
    def uninstall(self, plugin_id: str) -> None      # synchronous
```

Install directory naming: derived from the last segment of `id` (e.g. `com.airead.wenzi.cc-sessions` → `cc-sessions`). If a directory name collision occurs with a different `id`, append a numeric suffix (`cc-sessions-2`).

### settings_controller.py Extension

New callbacks for Plugins tab:
- `_on_plugin_install(url)` / `_on_plugin_update(id)` / `_on_plugin_uninstall(id)`
- `_on_plugin_toggle(id, enabled)`
- `_on_registry_add(url)` / `_on_registry_remove(url)`
- `_on_plugins_tab_open()` → trigger background update check

## Loading Strategy

1. Open Plugins tab → fetch each registry.toml (one request per registry) → display full plugin list from inline metadata
2. User clicks Install → fetch specific plugin.toml for `files` list → download files
3. Update check: compare registry `version` vs local `installed_version` — no extra requests needed
