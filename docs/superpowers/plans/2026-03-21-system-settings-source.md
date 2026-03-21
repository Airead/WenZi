# System Settings Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Chooser source that searches macOS System Settings panes and sub-items, opening them directly via `x-apple.systempreferences:` URL scheme.

**Architecture:** Two-tier data model — Tier 1 auto-discovers panes from ExtensionKit `.appex` bundles at startup, Tier 2 provides static deep-link mappings for sub-items (Privacy anchors, General sub-panels). Two `ChooserSource` instances: one prefixed (`ss`), one unprefixed (low priority). Uses `NSWorkspace.openURL_()` to launch settings.

**Tech Stack:** Python, PyObjC (AppKit/Foundation for plist reading, NSWorkspace, NSImage), existing `fuzzy_match_fields()` from sources package.

**Spec:** `docs/superpowers/specs/2026-03-21-system-settings-source-design.md`

---

## File Structure

| File | Role |
|------|------|
| Create: `src/wenzi/scripting/sources/system_settings_source.py` | `SystemSettingsSource` class — pane discovery, static mapping, search, icon extraction |
| Create: `tests/scripting/test_system_settings_source.py` | All tests for the source |
| Modify: `src/wenzi/scripting/sources/__init__.py` | Export `SystemSettingsSource` |
| Modify: `src/wenzi/scripting/engine.py` | Register source in `_register_builtin_sources()` |
| Modify: `src/wenzi/usage_stats.py` | Add `system_settings_opened` counter |
| Modify: `tests/test_usage_stats.py` | Test new counter |

---

## Task 1: Static Data Model and URL Construction

**Files:**
- Create: `tests/scripting/test_system_settings_source.py`
- Create: `src/wenzi/scripting/sources/system_settings_source.py`

- [ ] **Step 1: Write failing tests for data model and URL building**

```python
"""Tests for system_settings_source."""

from __future__ import annotations

import os

import pytest

from wenzi.scripting.sources.system_settings_source import (
    SettingsEntry,
    build_url,
)


class TestBuildUrl:
    def test_panel_url(self):
        url = build_url("com.apple.BluetoothSettings")
        assert url == "x-apple.systempreferences:com.apple.BluetoothSettings"

    def test_anchor_url(self):
        url = build_url(
            "com.apple.settings.PrivacySecurity.extension",
            anchor="Privacy_Camera",
        )
        assert (
            url
            == "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Camera"
        )

    def test_colon_url(self):
        url = build_url(
            "com.apple.systempreferences.AppleIDSettings",
            sub_id="icloud",
        )
        assert (
            url
            == "x-apple.systempreferences:com.apple.systempreferences.AppleIDSettings:icloud"
        )


class TestSettingsEntry:
    def test_panel_entry(self):
        entry = SettingsEntry(
            title="Bluetooth",
            pane_id="com.apple.BluetoothSettings",
        )
        assert entry.url == "x-apple.systempreferences:com.apple.BluetoothSettings"
        assert entry.breadcrumb == "Bluetooth"

    def test_subitem_entry_with_anchor(self):
        entry = SettingsEntry(
            title="Camera",
            pane_id="com.apple.settings.PrivacySecurity.extension",
            anchor="Privacy_Camera",
            parent_title="Privacy & Security",
            keywords=("camera", "webcam"),
        )
        assert "?Privacy_Camera" in entry.url
        assert entry.breadcrumb == "Privacy & Security › Camera"

    def test_subitem_entry_with_sub_id(self):
        entry = SettingsEntry(
            title="iCloud",
            pane_id="com.apple.systempreferences.AppleIDSettings",
            sub_id="icloud",
            parent_title="Apple ID",
        )
        assert ":icloud" in entry.url
        assert entry.breadcrumb == "Apple ID › iCloud"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scripting/test_system_settings_source.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `SettingsEntry` and `build_url`**

```python
"""macOS System Settings source for the Chooser."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

logger = logging.getLogger(__name__)

_URL_SCHEME = "x-apple.systempreferences"


def build_url(
    pane_id: str,
    anchor: Optional[str] = None,
    sub_id: Optional[str] = None,
) -> str:
    """Build a System Settings URL.

    Three variants:
      - Panel:  x-apple.systempreferences:<pane_id>
      - Anchor: x-apple.systempreferences:<pane_id>?<anchor>
      - Sub-ID: x-apple.systempreferences:<pane_id>:<sub_id>
    """
    url = f"{_URL_SCHEME}:{pane_id}"
    if anchor:
        url += f"?{anchor}"
    elif sub_id:
        url += f":{sub_id}"
    return url


@dataclass
class SettingsEntry:
    """A single searchable System Settings item (panel or sub-item)."""

    title: str
    pane_id: str
    anchor: Optional[str] = None
    sub_id: Optional[str] = None
    parent_title: str = ""
    keywords: Sequence[str] = field(default_factory=tuple)
    icon_path: str = ""  # file:// URL or empty

    @property
    def url(self) -> str:
        return build_url(self.pane_id, anchor=self.anchor, sub_id=self.sub_id)

    @property
    def breadcrumb(self) -> str:
        if self.parent_title:
            return f"{self.parent_title} › {self.title}"
        return self.title

    @property
    def item_id(self) -> str:
        suffix = self.anchor or self.sub_id or self.pane_id
        return f"system_settings:{suffix}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scripting/test_system_settings_source.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wenzi/scripting/sources/system_settings_source.py tests/scripting/test_system_settings_source.py
git commit -m "feat(chooser): add SettingsEntry data model and URL builder"
```

---

## Task 2: Static Sub-Item Mapping (Tier 2)

**Files:**
- Modify: `src/wenzi/scripting/sources/system_settings_source.py`
- Modify: `tests/scripting/test_system_settings_source.py`

- [ ] **Step 1: Write failing test for static entries**

```python
from wenzi.scripting.sources.system_settings_source import get_static_entries


class TestStaticEntries:
    def test_returns_list_of_settings_entries(self):
        entries = get_static_entries()
        assert len(entries) > 20  # At least Privacy items + General sub-panels

    def test_privacy_camera_present(self):
        entries = get_static_entries()
        camera = [e for e in entries if e.anchor == "Privacy_Camera"]
        assert len(camera) == 1
        assert camera[0].parent_title == "Privacy & Security"
        assert camera[0].keywords  # Should have search keywords

    def test_general_subpanels_present(self):
        entries = get_static_entries()
        about = [e for e in entries if "About" in e.title and e.parent_title == "General"]
        assert len(about) == 1

    def test_all_entries_have_pane_id(self):
        for entry in get_static_entries():
            assert entry.pane_id, f"{entry.title} missing pane_id"

    def test_all_entries_have_url(self):
        for entry in get_static_entries():
            assert entry.url.startswith("x-apple.systempreferences:")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scripting/test_system_settings_source.py::TestStaticEntries -v`
Expected: FAIL — `get_static_entries` not found

- [ ] **Step 3: Implement static mapping**

Add to `system_settings_source.py`:

```python
_PRIVACY_PANE = "com.apple.settings.PrivacySecurity.extension"
_GENERAL_PANE = "com.apple.systempreferences.GeneralSettings"
_APPLEID_PANE = "com.apple.systempreferences.AppleIDSettings"

_STATIC_ENTRIES: list[dict] = [
    # --- Privacy & Security anchors ---
    {"title": "Accessibility", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_Accessibility",
     "parent_title": "Privacy & Security", "keywords": ("accessibility", "a11y", "assistive")},
    {"title": "Camera", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_Camera",
     "parent_title": "Privacy & Security", "keywords": ("camera", "webcam", "video")},
    {"title": "Microphone", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_Microphone",
     "parent_title": "Privacy & Security", "keywords": ("microphone", "mic", "audio", "recording")},
    {"title": "Screen Recording", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_ScreenCapture",
     "parent_title": "Privacy & Security", "keywords": ("screen recording", "screen capture", "screencapture")},
    {"title": "Location Services", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_LocationServices",
     "parent_title": "Privacy & Security", "keywords": ("location", "gps", "geolocation")},
    {"title": "Photos", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_Photos",
     "parent_title": "Privacy & Security", "keywords": ("photos", "photo library")},
    {"title": "Files and Folders", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_FilesAndFolders",
     "parent_title": "Privacy & Security", "keywords": ("files", "folders", "file access")},
    {"title": "Full Disk Access", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_AllFiles",
     "parent_title": "Privacy & Security", "keywords": ("full disk", "disk access")},
    {"title": "Automation", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_Automation",
     "parent_title": "Privacy & Security", "keywords": ("automation", "applescript", "scripting")},
    {"title": "Developer Tools", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_DevTools",
     "parent_title": "Privacy & Security", "keywords": ("developer", "dev tools", "xcode")},
    {"title": "Input Monitoring", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_ListenEvent",
     "parent_title": "Privacy & Security", "keywords": ("input monitoring", "keyboard", "mouse")},
    {"title": "Calendars", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_Calendars",
     "parent_title": "Privacy & Security", "keywords": ("calendars", "calendar access")},
    {"title": "Contacts", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_Contacts",
     "parent_title": "Privacy & Security", "keywords": ("contacts", "address book")},
    {"title": "Reminders", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_Reminders",
     "parent_title": "Privacy & Security", "keywords": ("reminders",)},
    {"title": "Bluetooth", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_Bluetooth",
     "parent_title": "Privacy & Security", "keywords": ("bluetooth privacy",)},
    {"title": "Analytics & Improvements", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_Analytics",
     "parent_title": "Privacy & Security", "keywords": ("analytics", "diagnostics", "telemetry")},
    {"title": "Apple Advertising", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_Advertising",
     "parent_title": "Privacy & Security", "keywords": ("advertising", "ads", "personalized")},
    {"title": "Pasteboard", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_Pasteboard",
     "parent_title": "Privacy & Security", "keywords": ("pasteboard", "clipboard", "paste")},
    {"title": "Media & Apple Music", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_Media",
     "parent_title": "Privacy & Security", "keywords": ("media", "apple music")},
    {"title": "Desktop Folder", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_DesktopFolder",
     "parent_title": "Privacy & Security", "keywords": ("desktop folder",)},
    {"title": "Documents Folder", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_DocumentsFolder",
     "parent_title": "Privacy & Security", "keywords": ("documents folder",)},
    {"title": "Downloads Folder", "pane_id": _PRIVACY_PANE, "anchor": "Privacy_DownloadsFolder",
     "parent_title": "Privacy & Security", "keywords": ("downloads folder",)},
    {"title": "FileVault", "pane_id": _PRIVACY_PANE, "anchor": "FileVault",
     "parent_title": "Privacy & Security", "keywords": ("filevault", "encryption", "disk encryption")},
    {"title": "Lockdown Mode", "pane_id": _PRIVACY_PANE, "anchor": "LockdownMode",
     "parent_title": "Privacy & Security", "keywords": ("lockdown", "lockdown mode")},
    # --- General sub-panels ---
    {"title": "About", "pane_id": "com.apple.SystemProfiler.AboutExtension",
     "parent_title": "General", "keywords": ("about", "system info", "serial number", "mac info")},
    {"title": "Software Update", "pane_id": "com.apple.Software-Update-Settings.extension",
     "parent_title": "General", "keywords": ("software update", "update", "upgrade", "macos update")},
    {"title": "Storage", "pane_id": "com.apple.settings.Storage",
     "parent_title": "General", "keywords": ("storage", "disk space", "free space")},
    {"title": "AirDrop & Handoff", "pane_id": "com.apple.AirDrop-Handoff-Settings.extension",
     "parent_title": "General", "keywords": ("airdrop", "handoff", "continuity")},
    {"title": "Login Items", "pane_id": "com.apple.LoginItems-Settings.extension",
     "parent_title": "General", "keywords": ("login items", "startup", "launch at login", "open at login")},
    {"title": "Language & Region", "pane_id": "com.apple.Localization-Settings.extension",
     "parent_title": "General", "keywords": ("language", "region", "locale", "format")},
    {"title": "Date & Time", "pane_id": "com.apple.Date-Time-Settings.extension",
     "parent_title": "General", "keywords": ("date", "time", "timezone", "clock")},
    {"title": "Sharing", "pane_id": "com.apple.Sharing-Settings.extension",
     "parent_title": "General", "keywords": ("sharing", "file sharing", "screen sharing", "remote login")},
    {"title": "Time Machine", "pane_id": "com.apple.Time-Machine-Settings.extension",
     "parent_title": "General", "keywords": ("time machine", "backup", "backups")},
    {"title": "Transfer or Reset", "pane_id": "com.apple.Transfer-Reset-Settings.extension",
     "parent_title": "General", "keywords": ("transfer", "reset", "erase", "factory reset")},
    {"title": "Startup Disk", "pane_id": "com.apple.Startup-Disk-Settings.extension",
     "parent_title": "General", "keywords": ("startup disk", "boot")},
    # --- Apple ID sub-panes ---
    {"title": "iCloud", "pane_id": _APPLEID_PANE, "sub_id": "icloud",
     "parent_title": "Apple ID", "keywords": ("icloud", "cloud", "sync", "icloud drive")},
]


def get_static_entries() -> list[SettingsEntry]:
    """Return the built-in sub-item entries."""
    return [SettingsEntry(**e) for e in _STATIC_ENTRIES]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scripting/test_system_settings_source.py::TestStaticEntries -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wenzi/scripting/sources/system_settings_source.py tests/scripting/test_system_settings_source.py
git commit -m "feat(chooser): add static sub-item mapping for system settings"
```

---

## Task 3: Pane Discovery from ExtensionKit (Tier 1)

**Files:**
- Modify: `src/wenzi/scripting/sources/system_settings_source.py`
- Modify: `tests/scripting/test_system_settings_source.py`

- [ ] **Step 1: Write failing tests for pane discovery**

```python
import os
import plistlib
from unittest.mock import patch

from wenzi.scripting.sources.system_settings_source import discover_panels


def _make_appex(tmp_path, bundle_id, display_name, *, allows_url=True, ext_point="com.apple.Settings.extension.ui"):
    """Create a fake .appex bundle for testing."""
    appex = tmp_path / f"{display_name}.appex" / "Contents"
    appex.mkdir(parents=True)
    info = {
        "CFBundleIdentifier": bundle_id,
        "CFBundleDisplayName": display_name,
        "EXExtensionPointIdentifier": ext_point,
        "SettingsExtensionAttributes": {
            "allowsXAppleSystemPreferencesURLScheme": allows_url,
        },
    }
    (appex / "Info.plist").write_bytes(plistlib.dumps(info))
    res = appex / "Resources"
    res.mkdir()
    return appex.parent


class TestDiscoverPanels:
    def test_discovers_valid_appex(self, tmp_path):
        _make_appex(tmp_path, "com.apple.BluetoothSettings", "Bluetooth")
        entries = discover_panels(extensions_dir=str(tmp_path))
        assert len(entries) == 1
        assert entries[0].title == "Bluetooth"
        assert entries[0].pane_id == "com.apple.BluetoothSettings"

    def test_skips_non_settings_extension(self, tmp_path):
        _make_appex(tmp_path, "com.apple.Other", "Other", ext_point="com.apple.Other.extension")
        entries = discover_panels(extensions_dir=str(tmp_path))
        assert len(entries) == 0

    def test_skips_url_scheme_disabled(self, tmp_path):
        _make_appex(tmp_path, "com.apple.Foo", "Foo", allows_url=False)
        entries = discover_panels(extensions_dir=str(tmp_path))
        assert len(entries) == 0

    def test_missing_dir_returns_empty(self):
        entries = discover_panels(extensions_dir="/nonexistent/path")
        assert entries == []

    def test_malformed_plist_skipped(self, tmp_path):
        appex = tmp_path / "Bad.appex" / "Contents"
        appex.mkdir(parents=True)
        (appex / "Info.plist").write_text("not a plist")
        _make_appex(tmp_path, "com.apple.Good", "Good")
        entries = discover_panels(extensions_dir=str(tmp_path))
        assert len(entries) == 1  # Only the good one

    def test_uses_bundle_name_fallback(self, tmp_path):
        """If CFBundleDisplayName is missing, use CFBundleName."""
        appex = tmp_path / "Test.appex" / "Contents"
        appex.mkdir(parents=True)
        info = {
            "CFBundleIdentifier": "com.apple.Test",
            "CFBundleName": "TestPane",
            "EXExtensionPointIdentifier": "com.apple.Settings.extension.ui",
            "SettingsExtensionAttributes": {
                "allowsXAppleSystemPreferencesURLScheme": True,
            },
        }
        (appex / "Info.plist").write_bytes(plistlib.dumps(info))
        (appex / "Resources").mkdir()
        entries = discover_panels(extensions_dir=str(tmp_path))
        assert entries[0].title == "TestPane"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scripting/test_system_settings_source.py::TestDiscoverPanels -v`
Expected: FAIL — `discover_panels` not found

- [ ] **Step 3: Implement `discover_panels()`**

Add to `system_settings_source.py`:

```python
import os
import plistlib

_DEFAULT_EXTENSIONS_DIR = "/System/Library/ExtensionKit/Extensions"
_SETTINGS_EXTENSION_POINT = "com.apple.Settings.extension.ui"


def discover_panels(
    extensions_dir: str = _DEFAULT_EXTENSIONS_DIR,
) -> list[SettingsEntry]:
    """Scan ExtensionKit extensions for System Settings panes.

    Returns SettingsEntry for each pane that supports the URL scheme.
    Gracefully returns [] if the directory does not exist.
    """
    if not os.path.isdir(extensions_dir):
        logger.debug("Extensions directory not found: %s", extensions_dir)
        return []

    entries: list[SettingsEntry] = []
    for name in os.listdir(extensions_dir):
        if not name.endswith(".appex"):
            continue
        plist_path = os.path.join(extensions_dir, name, "Contents", "Info.plist")
        if not os.path.isfile(plist_path):
            continue
        try:
            with open(plist_path, "rb") as f:
                info = plistlib.load(f)
        except Exception:
            logger.debug("Failed to read plist: %s", plist_path)
            continue

        ext_point = info.get("EXExtensionPointIdentifier", "")
        if ext_point != _SETTINGS_EXTENSION_POINT:
            continue

        attrs = info.get("SettingsExtensionAttributes", {})
        if not attrs.get("allowsXAppleSystemPreferencesURLScheme", False):
            continue

        bundle_id = info.get("CFBundleIdentifier", "")
        if not bundle_id:
            continue

        display_name = info.get("CFBundleDisplayName") or info.get("CFBundleName") or name.replace(".appex", "")

        entries.append(SettingsEntry(
            title=display_name,
            pane_id=bundle_id,
        ))

    entries.sort(key=lambda e: e.title.lower())
    return entries
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scripting/test_system_settings_source.py::TestDiscoverPanels -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wenzi/scripting/sources/system_settings_source.py tests/scripting/test_system_settings_source.py
git commit -m "feat(chooser): add ExtensionKit pane discovery for system settings"
```

---

## Task 4: Icon Extraction

**Files:**
- Modify: `src/wenzi/scripting/sources/system_settings_source.py`
- Modify: `tests/scripting/test_system_settings_source.py`

- [ ] **Step 1: Write failing tests for icon extraction**

```python
from wenzi.scripting.sources.system_settings_source import extract_icon


class TestExtractIcon:
    def test_extracts_icns_file(self, tmp_path):
        appex = tmp_path / "Test.appex" / "Contents" / "Resources"
        appex.mkdir(parents=True)
        # Create a fake .icns file (just needs to exist for the path test)
        icon_file = appex / "AppIcon.icns"
        icon_file.write_bytes(b"fake-icns-data")
        result = extract_icon(str(tmp_path / "Test.appex"))
        assert result.endswith(".icns")
        assert os.path.isfile(result)

    def test_extracts_png_file(self, tmp_path):
        appex = tmp_path / "Test.appex" / "Contents" / "Resources"
        appex.mkdir(parents=True)
        icon_file = appex / "AppIcon.png"
        icon_file.write_bytes(b"fake-png-data")
        result = extract_icon(str(tmp_path / "Test.appex"))
        assert result.endswith(".png")

    def test_returns_empty_when_no_icon(self, tmp_path):
        appex = tmp_path / "Test.appex" / "Contents" / "Resources"
        appex.mkdir(parents=True)
        result = extract_icon(str(tmp_path / "Test.appex"))
        assert result == ""

    def test_returns_empty_when_no_resources_dir(self, tmp_path):
        appex = tmp_path / "Test.appex" / "Contents"
        appex.mkdir(parents=True)
        result = extract_icon(str(tmp_path / "Test.appex"))
        assert result == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scripting/test_system_settings_source.py::TestExtractIcon -v`
Expected: FAIL — `extract_icon` not found

- [ ] **Step 3: Implement `extract_icon()`**

Add to `system_settings_source.py`:

```python
_ICON_EXTENSIONS = (".icns", ".png", ".tiff")


def extract_icon(appex_path: str) -> str:
    """Extract icon file path from an .appex bundle.

    Looks for icon files in Contents/Resources/.
    Returns the path to the first found icon file, or "" if none found.
    Assets.car (compiled asset catalogs) are not supported — returns "".
    """
    resources = os.path.join(appex_path, "Contents", "Resources")
    if not os.path.isdir(resources):
        return ""

    # Look for common icon file patterns
    for name in os.listdir(resources):
        lower = name.lower()
        if any(lower.endswith(ext) for ext in _ICON_EXTENSIONS):
            if "icon" in lower or "appicon" in lower:
                return os.path.join(resources, name)

    # Fallback: any image file
    for name in os.listdir(resources):
        lower = name.lower()
        if any(lower.endswith(ext) for ext in _ICON_EXTENSIONS):
            return os.path.join(resources, name)

    return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scripting/test_system_settings_source.py::TestExtractIcon -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wenzi/scripting/sources/system_settings_source.py tests/scripting/test_system_settings_source.py
git commit -m "feat(chooser): add icon extraction from appex bundles"
```

---

## Task 5: SystemSettingsSource Class and Search

**Files:**
- Modify: `src/wenzi/scripting/sources/system_settings_source.py`
- Modify: `tests/scripting/test_system_settings_source.py`

- [ ] **Step 1: Write failing tests for the source class**

```python
from wenzi.scripting.sources.system_settings_source import SystemSettingsSource


class TestSystemSettingsSource:
    def _make_source(self, tmp_path):
        """Create a source with a fake extensions dir."""
        _make_appex(tmp_path, "com.apple.BluetoothSettings", "Bluetooth")
        _make_appex(tmp_path, "com.apple.wifi-settings-extension", "Wi-Fi")
        _make_appex(tmp_path, "com.apple.Sound-Settings.extension", "Sound")
        return SystemSettingsSource(extensions_dir=str(tmp_path))

    def test_search_panel_by_name(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("bluetooth")
        assert len(results) >= 1
        assert results[0].title == "Bluetooth"

    def test_search_subitem(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("camera")
        assert len(results) >= 1
        assert any("Camera" in r.title for r in results)

    def test_search_empty_returns_panels(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("")
        # Empty query returns all top-level panels
        assert len(results) >= 3  # At least the 3 we created

    def test_search_no_match(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("xyznonexistent")
        assert len(results) == 0

    def test_search_by_keyword(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("webcam")  # Keyword for Camera
        assert any("Camera" in r.title for r in results)

    def test_items_have_action(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("bluetooth")
        assert results[0].action is not None

    def test_items_have_item_id(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("bluetooth")
        assert results[0].item_id.startswith("system_settings:")

    def test_subitem_subtitle_shows_breadcrumb(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("camera")
        camera = [r for r in results if r.title == "Camera"]
        assert camera
        assert "Privacy & Security" in camera[0].subtitle

    def test_search_limited(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search_limited("bluetooth", max_results=2)
        assert len(results) <= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scripting/test_system_settings_source.py::TestSystemSettingsSource -v`
Expected: FAIL — `SystemSettingsSource` not found

- [ ] **Step 3: Implement `SystemSettingsSource`**

Add to `system_settings_source.py`:

```python
from wenzi.scripting.sources import ChooserItem, ChooserSource, fuzzy_match_fields


class SystemSettingsSource:
    """Searches macOS System Settings panes and sub-items."""

    _MAX_RESULTS = 50

    def __init__(self, extensions_dir: str = _DEFAULT_EXTENSIONS_DIR) -> None:
        panels = discover_panels(extensions_dir)
        static = get_static_entries()

        # Merge: static entries override panels with same pane_id
        panel_map: dict[str, SettingsEntry] = {e.pane_id: e for e in panels}
        self._entries: list[SettingsEntry] = []

        # Add panels first (those not overridden by static)
        static_pane_ids = {e.pane_id for e in static if not e.anchor and not e.sub_id}
        for entry in panels:
            if entry.pane_id not in static_pane_ids:
                self._entries.append(entry)

        # Add all static entries, inheriting icon from discovered panels
        for entry in static:
            if not entry.icon_path and entry.pane_id in panel_map:
                # Sub-items inherit icon from parent panel
                parent = panel_map.get(entry.pane_id)
                if parent and parent.icon_path:
                    entry = SettingsEntry(
                        title=entry.title,
                        pane_id=entry.pane_id,
                        anchor=entry.anchor,
                        sub_id=entry.sub_id,
                        parent_title=entry.parent_title,
                        keywords=entry.keywords,
                        icon_path=parent.icon_path,
                    )
            self._entries.append(entry)

        logger.info(
            "SystemSettingsSource loaded: %d panels, %d static, %d total",
            len(panels), len(static), len(self._entries),
        )

    def search(self, query: str) -> list[ChooserItem]:
        """Search all entries. Empty query returns top-level panels."""
        q = query.strip()
        if not q:
            # Return top-level panels (entries without parent_title)
            panels = [e for e in self._entries if not e.parent_title]
            panels.sort(key=lambda e: e.title.lower())
            return [self._to_item(e) for e in panels]

        scored: list[tuple[int, SettingsEntry]] = []
        for entry in self._entries:
            fields = (entry.title, entry.breadcrumb, *entry.keywords)
            matched, score = fuzzy_match_fields(q, fields)
            if matched:
                scored.append((score, entry))

        scored.sort(key=lambda x: (-x[0], x[1].title.lower()))
        return [self._to_item(e) for _, e in scored[: self._MAX_RESULTS]]

    def search_limited(self, query: str, max_results: int = 5) -> list[ChooserItem]:
        """Search with a result cap — for unprefixed mixed mode."""
        results = self.search(query)
        return results[:max_results]

    def _to_item(self, entry: SettingsEntry) -> ChooserItem:
        """Convert a SettingsEntry to a ChooserItem."""
        url = entry.url
        return ChooserItem(
            title=entry.title,
            subtitle=entry.breadcrumb if entry.parent_title else "System Settings",
            icon=f"file://{entry.icon_path}" if entry.icon_path else "",
            item_id=entry.item_id,
            action=lambda u=url: _open_url(u),
            secondary_action=lambda u=url: _copy_url(u),
        )


def _open_url(url: str) -> None:
    """Open a System Settings URL."""
    try:
        from AppKit import NSWorkspace
        from Foundation import NSURL

        ns_url = NSURL.URLWithString_(url)
        ok = NSWorkspace.sharedWorkspace().openURL_(ns_url)
        if not ok:
            logger.warning("Failed to open URL: %s", url)
    except Exception:
        logger.exception("Error opening system settings URL: %s", url)


def _copy_url(url: str) -> None:
    """Copy a System Settings URL to clipboard."""
    from wenzi.scripting.sources import copy_to_clipboard

    copy_to_clipboard(url)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scripting/test_system_settings_source.py::TestSystemSettingsSource -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wenzi/scripting/sources/system_settings_source.py tests/scripting/test_system_settings_source.py
git commit -m "feat(chooser): add SystemSettingsSource class with search"
```

---

## Task 6: ChooserSource Integration (Dual Registration)

**Files:**
- Modify: `src/wenzi/scripting/sources/system_settings_source.py`
- Modify: `tests/scripting/test_system_settings_source.py`

Note: Use `as_chooser_source()` (singular) to match the codebase convention. It returns a `list` instead of a single `ChooserSource` — this is a minor deviation but keeps the method name consistent with all other sources.

- [ ] **Step 1: Write failing tests for `as_chooser_source()`**

```python
class TestAsChooserSource:
    def test_returns_two_sources(self, tmp_path):
        src = SystemSettingsSource(extensions_dir=str(tmp_path))
        sources = src.as_chooser_source()
        assert len(sources) == 2

    def test_prefixed_source(self, tmp_path):
        src = SystemSettingsSource(extensions_dir=str(tmp_path))
        sources = src.as_chooser_source(prefix="ss")
        prefixed = [s for s in sources if s.prefix is not None]
        assert len(prefixed) == 1
        assert prefixed[0].prefix == "ss"
        assert prefixed[0].name == "system_settings"
        assert prefixed[0].search is not None
        assert prefixed[0].action_hints == {"enter": "Open", "cmd_enter": "Copy URL"}
        assert prefixed[0].description

    def test_unprefixed_source(self, tmp_path):
        src = SystemSettingsSource(extensions_dir=str(tmp_path))
        sources = src.as_chooser_source()
        unprefixed = [s for s in sources if s.prefix is None]
        assert len(unprefixed) == 1
        assert unprefixed[0].name == "system_settings_mixed"
        assert unprefixed[0].priority == -5
        assert unprefixed[0].search is not None

    def test_unprefixed_limits_results(self, tmp_path):
        _make_appex(tmp_path, "com.apple.BluetoothSettings", "Bluetooth")
        src = SystemSettingsSource(extensions_dir=str(tmp_path))
        sources = src.as_chooser_source()
        unprefixed = [s for s in sources if s.prefix is None][0]
        # Empty query should return nothing in unprefixed mode
        results = unprefixed.search("")
        assert len(results) == 0

    def test_custom_prefix(self, tmp_path):
        src = SystemSettingsSource(extensions_dir=str(tmp_path))
        sources = src.as_chooser_source(prefix="set")
        prefixed = [s for s in sources if s.prefix is not None]
        assert prefixed[0].prefix == "set"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scripting/test_system_settings_source.py::TestAsChooserSource -v`
Expected: FAIL — `as_chooser_source` not found

- [ ] **Step 3: Implement `as_chooser_source()`**

Add to `SystemSettingsSource`:

```python
    def as_chooser_source(self, prefix: str = "ss") -> list[ChooserSource]:
        """Return two ChooserSource instances: prefixed + unprefixed."""
        return [
            ChooserSource(
                name="system_settings",
                prefix=prefix,
                search=self.search,
                priority=5,
                description="Search macOS System Settings",
                action_hints={"enter": "Open", "cmd_enter": "Copy URL"},
            ),
            ChooserSource(
                name="system_settings_mixed",
                prefix=None,
                search=self._search_mixed,
                priority=-5,
                description="System Settings (mixed)",
            ),
        ]

    def _search_mixed(self, query: str) -> list[ChooserItem]:
        """Search for unprefixed mode: no results on empty, limited count."""
        if not query.strip():
            return []
        return self.search_limited(query, max_results=5)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scripting/test_system_settings_source.py::TestAsChooserSource -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wenzi/scripting/sources/system_settings_source.py tests/scripting/test_system_settings_source.py
git commit -m "feat(chooser): add dual ChooserSource registration for system settings"
```

---

## Task 7: Engine Registration and `__init__.py` Export

**Files:**
- Modify: `src/wenzi/scripting/sources/__init__.py`
- Modify: `src/wenzi/scripting/engine.py`

- [ ] **Step 1: Update `__init__.py` exports**

At the bottom of `src/wenzi/scripting/sources/__init__.py`, add (following the existing import style — check if there are existing imports at bottom or if the file just has the dataclasses):

Note: This file currently only defines dataclasses and functions (no re-exports of source classes). Check the file first. If other source classes are not exported from `__init__.py`, just ensure the module is importable. If they are, add `SystemSettingsSource`.

- [ ] **Step 2: Add registration in `engine.py`**

In `_register_builtin_sources()`, add after the last source registration block:

```python
        # System Settings
        if chooser_config.get("system_settings", True):
            try:
                from wenzi.scripting.sources.system_settings_source import (
                    SystemSettingsSource,
                )

                ss_source = SystemSettingsSource()
                prefix = prefixes.get("system_settings", "ss")
                for cs in ss_source.as_chooser_source(prefix=prefix):
                    self._wz.chooser.register_source(cs)
                logger.info("Built-in system settings source registered")
            except Exception:
                logger.exception("Failed to register system settings source")
```

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v --cov=wenzi`
Expected: All existing tests pass, no regressions

- [ ] **Step 4: Commit**

```bash
git add src/wenzi/scripting/sources/__init__.py src/wenzi/scripting/engine.py
git commit -m "feat(chooser): register SystemSettingsSource in engine"
```

---

## Task 8: Usage Statistics

**Files:**
- Modify: `src/wenzi/usage_stats.py`
- Modify: `tests/test_usage_stats.py`

- [ ] **Step 1: Write failing test for new counter**

Add to `tests/test_usage_stats.py`:

```python
def test_record_system_settings_open(tmp_path):
    stats = UsageStats(data_dir=str(tmp_path))
    stats.record_system_settings_open()
    data = stats.summary()
    assert data["totals"]["system_settings_opened"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_usage_stats.py::test_record_system_settings_open -v`
Expected: FAIL — key not found or method not found

- [ ] **Step 3: Implement counter**

In `src/wenzi/usage_stats.py`:

1. Add `"system_settings_opened": 0` to `_empty_totals()`
2. Add method:
```python
    def record_system_settings_open(self) -> None:
        """Record a system settings pane opened from the chooser."""
        def _update(data: dict[str, Any]) -> None:
            data["totals"]["system_settings_opened"] += 1
        self._record(_update)
```
3. Update `_on_show_usage_stats()` display (if this method exists in `app.py`, add the line there)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_usage_stats.py::test_record_system_settings_open -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wenzi/usage_stats.py tests/test_usage_stats.py
git commit -m "feat: add system_settings_opened usage counter"
```

---

## Task 9: Wire Up Usage Tracking in Action

**Files:**
- Modify: `src/wenzi/scripting/sources/system_settings_source.py`

- [ ] **Step 1: Update `_open_url()` to accept optional stats callback**

Modify `SystemSettingsSource.__init__()` to accept an optional `on_open` callback, and pass it through to the action lambda:

```python
class SystemSettingsSource:
    def __init__(
        self,
        extensions_dir: str = _DEFAULT_EXTENSIONS_DIR,
        on_open: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_open = on_open
        # ... rest of init

    def _to_item(self, entry: SettingsEntry) -> ChooserItem:
        url = entry.url
        on_open = self._on_open
        def _action(u=url):
            _open_url(u)
            if on_open:
                on_open()
        return ChooserItem(
            # ...
            action=_action,
            # ...
        )
```

- [ ] **Step 2: Wire in engine.py**

Usage stats live in `app.py` (`app._usage_stats`), not in the engine. The engine doesn't have access to `UsageStats`. Two options:

**Option A (simpler):** Pass a callback from `app.py` when setting up the engine. Check if the engine's `__init__` or `_register_builtin_sources` already receives an `app` reference or callbacks. If it does, use it.

**Option B (self-contained):** The `on_open` callback can be wired later from `app.py` after engine setup, or the engine can expose the `SystemSettingsSource` instance for `app.py` to set the callback.

During implementation, check how the engine accesses app-level services and follow the same pattern. The key is to call `app._usage_stats.record_system_settings_open()` when a setting is opened.

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v --cov=wenzi`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add src/wenzi/scripting/sources/system_settings_source.py src/wenzi/scripting/engine.py
git commit -m "feat(chooser): wire usage stats tracking for system settings"
```

---

## Task 10: Final Lint and Verification

- [ ] **Step 1: Run linter**

Run: `uv run ruff check`
Expected: 0 errors

- [ ] **Step 2: Run full test suite with coverage**

Run: `uv run pytest tests/ -v --cov=wenzi`
Expected: All tests pass, no warnings to address

- [ ] **Step 3: Fix any issues found**

If lint or test issues arise, fix and commit.

- [ ] **Step 4: Final commit if needed**

```bash
git add -A
git commit -m "chore: fix lint and test issues for system settings source"
```
