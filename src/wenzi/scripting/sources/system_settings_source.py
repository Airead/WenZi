"""macOS System Settings source for the Chooser."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, Sequence

from wenzi.config import DEFAULT_ICON_CACHE_DIR as _CFG_ICON_CACHE_DIR

if TYPE_CHECKING:
    from wenzi.scripting.sources import ChooserItem

logger = logging.getLogger(__name__)

_URL_SCHEME = "x-apple.systempreferences"
_DEFAULT_EXTENSIONS_DIR = "/System/Library/ExtensionKit/Extensions"
_DEFAULT_ICON_CACHE_DIR = os.path.expanduser(_CFG_ICON_CACHE_DIR)
_ICON_SIZE = 32


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
    appex_name: str = ""  # e.g. "SecurityPrivacyExtension" for icon lookup

    @property
    def url(self) -> str:
        return build_url(self.pane_id, anchor=self.anchor, sub_id=self.sub_id)

    @property
    def breadcrumb(self) -> str:
        if self.parent_title:
            return f"{self.parent_title} \u203a {self.title}"
        return self.title

    @property
    def item_id(self) -> str:
        suffix = self.anchor or self.sub_id or self.pane_id
        return f"system_settings:{suffix}"


# ---------------------------------------------------------------------------
# Static entries: top-level panels + sub-items
# ---------------------------------------------------------------------------

_PRIVACY_PANE = "com.apple.settings.PrivacySecurity.extension"
_APPLEID_PANE = "com.apple.systempreferences.AppleIDSettings"

# (title, pane_id, appex_name, keywords)
_TOP_LEVEL_PANELS: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("Wi-Fi", "com.apple.wifi-settings-extension", "WiFiSettings",
     ("wifi", "wireless", "network", "internet")),
    ("Bluetooth", "com.apple.BluetoothSettings", "BluetoothSettings",
     ("bluetooth", "bt", "wireless")),
    ("Network", "com.apple.Network-Settings.extension", "NetworkSettingsExtension",
     ("network", "ethernet", "vpn", "proxy", "dns")),
    ("Notifications", "com.apple.Notifications-Settings.extension", "NotificationsSettingsExtension",
     ("notifications", "alerts", "banners")),
    ("Sound", "com.apple.Sound-Settings.extension", "SoundSettings",
     ("sound", "audio", "volume", "output", "input")),
    ("Focus", "com.apple.Focus-Settings.extension", "FocusSettingsExtension",
     ("focus", "do not disturb", "dnd")),
    ("Screen Time", "com.apple.Screen-Time-Settings.extension", "ScreenTimeSettingsExtension",
     ("screen time", "parental", "limits")),
    ("General", "com.apple.systempreferences.GeneralSettings", "GeneralSettings",
     ("general",)),
    ("Appearance", "com.apple.Appearance-Settings.extension", "AppearanceSettings",
     ("appearance", "dark mode", "light mode", "accent color")),
    ("Accessibility", "com.apple.Accessibility-Settings.extension", "AccessibilitySettingsExtension",
     ("accessibility", "a11y", "voiceover", "zoom")),
    ("Control Center", "com.apple.ControlCenter-Settings.extension", "ControlCenterSettings",
     ("control center", "menu bar")),
    ("Siri & Spotlight", "com.apple.Siri-Settings.extension", "AssistantSettingsControlsExtension",
     ("siri", "spotlight", "apple intelligence")),
    ("Privacy & Security", _PRIVACY_PANE, "SecurityPrivacyExtension",
     ("privacy", "security", "permissions")),
    ("Desktop & Dock", "com.apple.Desktop-Settings.extension", "DesktopSettings",
     ("desktop", "dock", "mission control", "hot corners", "stage manager")),
    ("Displays", "com.apple.Displays-Settings.extension", "DisplaysSettings",
     ("displays", "monitor", "resolution", "night shift", "true tone")),
    ("Wallpaper", "com.apple.Wallpaper-Settings.extension", "WallpaperSettings",
     ("wallpaper", "background", "desktop picture")),
    ("Battery", "com.apple.Battery-Settings.extension", "BatterySettings",
     ("battery", "energy", "power")),
    ("Lock Screen", "com.apple.Lock-Screen-Settings.extension", "LockScreenSettings",
     ("lock screen", "screen saver", "login window")),
    ("Touch ID & Password", "com.apple.Touch-ID-Settings.extension", "BiometricsAndPasswordSettingsExtension",
     ("touch id", "password", "fingerprint")),
    ("Users & Groups", "com.apple.Users-Groups-Settings.extension", "UsersGroupsSettings",
     ("users", "groups", "accounts", "login")),
    ("Passwords", "com.apple.Passwords-Settings.extension", "PasswordSettings",
     ("passwords", "passkeys", "keychain")),
    ("Internet Accounts", "com.apple.Internet-Accounts-Settings.extension", "InternetAccountsSettingsExtension",
     ("internet accounts", "email", "mail accounts")),
    ("Keyboard", "com.apple.Keyboard-Settings.extension", "KeyboardSettings",
     ("keyboard", "shortcuts", "text replacement", "dictation", "input sources")),
    ("Mouse", "com.apple.Mouse-Settings.extension", "MouseSettings",
     ("mouse", "scroll", "tracking")),
    ("Trackpad", "com.apple.Trackpad-Settings.extension", "TrackpadSettings",
     ("trackpad", "gesture", "tap", "click")),
    ("Printers & Scanners", "com.apple.Print-Scan-Settings.extension", "PrintScanSettingsExtension",
     ("printers", "scanners", "print")),
    ("Apple ID", _APPLEID_PANE, "AppleIDSettings",
     ("apple id", "icloud", "account")),
]

_PRIVACY_ANCHORS: list[tuple[str, str, tuple[str, ...]]] = [
    ("Accessibility", "Privacy_Accessibility", ("accessibility", "a11y", "assistive")),
    ("Camera", "Privacy_Camera", ("camera", "webcam", "video")),
    ("Microphone", "Privacy_Microphone", ("microphone", "mic", "audio", "recording")),
    ("Screen Recording", "Privacy_ScreenCapture", ("screen recording", "screen capture")),
    ("Location Services", "Privacy_LocationServices", ("location", "gps")),
    ("Photos", "Privacy_Photos", ("photos", "photo library")),
    ("Files and Folders", "Privacy_FilesAndFolders", ("files", "folders", "file access")),
    ("Full Disk Access", "Privacy_AllFiles", ("full disk", "disk access")),
    ("Automation", "Privacy_Automation", ("automation", "applescript", "scripting")),
    ("Developer Tools", "Privacy_DevTools", ("developer", "dev tools")),
    ("Input Monitoring", "Privacy_ListenEvent", ("input monitoring", "keyboard")),
    ("Calendars", "Privacy_Calendars", ()),
    ("Contacts", "Privacy_Contacts", ()),
    ("Reminders", "Privacy_Reminders", ()),
    ("Bluetooth", "Privacy_Bluetooth", ()),
    ("Analytics & Improvements", "Privacy_Analytics", ("analytics", "diagnostics", "telemetry")),
    ("Apple Advertising", "Privacy_Advertising", ("advertising", "ads")),
    ("Pasteboard", "Privacy_Pasteboard", ("pasteboard", "clipboard")),
    ("Media & Apple Music", "Privacy_Media", ()),
    ("Desktop Folder", "Privacy_DesktopFolder", ()),
    ("Documents Folder", "Privacy_DocumentsFolder", ()),
    ("Downloads Folder", "Privacy_DownloadsFolder", ()),
    ("FileVault", "FileVault", ("filevault", "encryption", "disk encryption")),
    ("Lockdown Mode", "LockdownMode", ()),
]

# (title, pane_id, appex_name, keywords)
_GENERAL_SUBPANELS: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("About", "com.apple.SystemProfiler.AboutExtension", "AboutSettingsExtension",
     ("about", "system info", "serial number")),
    ("Software Update", "com.apple.Software-Update-Settings.extension", "SoftwareUpdateSettingsExtension",
     ("software update", "update", "upgrade", "macos update")),
    ("Storage", "com.apple.settings.Storage", "StorageSettings",
     ("storage", "disk space")),
    ("AirDrop & Handoff", "com.apple.AirDrop-Handoff-Settings.extension", "AirDropHandoffSettings",
     ("airdrop", "handoff")),
    ("Login Items", "com.apple.LoginItems-Settings.extension", "LoginItemsSettingsExtension",
     ("login items", "startup", "launch at login")),
    ("Language & Region", "com.apple.Localization-Settings.extension", "InternationalSettingsExtension",
     ("language", "region", "locale")),
    ("Date & Time", "com.apple.Date-Time-Settings.extension", "DateTimeSettings",
     ("date", "time", "timezone")),
    ("Sharing", "com.apple.Sharing-Settings.extension", "SharingSettingsExtension",
     ("sharing", "file sharing", "screen sharing")),
    ("Time Machine", "com.apple.Time-Machine-Settings.extension", "TimeMachineSettings",
     ("time machine", "backup")),
    ("Transfer or Reset", "com.apple.Transfer-Reset-Settings.extension", "TransferResetSettingsExtension",
     ("transfer", "reset", "erase")),
    ("Startup Disk", "com.apple.Startup-Disk-Settings.extension", "StartupDiskSettings",
     ("startup disk", "boot")),
]


def get_static_entries() -> list[SettingsEntry]:
    """Return all statically-defined System Settings entries."""
    entries: list[SettingsEntry] = []

    # Top-level panels
    for title, pane_id, appex_name, keywords in _TOP_LEVEL_PANELS:
        entries.append(
            SettingsEntry(
                title=title,
                pane_id=pane_id,
                keywords=keywords,
                appex_name=appex_name,
            )
        )

    # Privacy & Security sub-items (anchors)
    privacy_appex = next(
        (a for t, _, a, _ in _TOP_LEVEL_PANELS if t == "Privacy & Security"), ""
    )
    for title, anchor, keywords in _PRIVACY_ANCHORS:
        entries.append(
            SettingsEntry(
                title=title,
                pane_id=_PRIVACY_PANE,
                anchor=anchor,
                parent_title="Privacy & Security",
                keywords=keywords,
                appex_name=privacy_appex,
            )
        )

    # General sub-panels
    for title, pane_id, appex_name, keywords in _GENERAL_SUBPANELS:
        entries.append(
            SettingsEntry(
                title=title,
                pane_id=pane_id,
                parent_title="General",
                keywords=keywords,
                appex_name=appex_name,
            )
        )

    # Apple ID sub-panes
    appleid_appex = next(
        (a for t, _, a, _ in _TOP_LEVEL_PANELS if t == "Apple ID"), ""
    )
    entries.append(
        SettingsEntry(
            title="iCloud",
            pane_id=_APPLEID_PANE,
            sub_id="icloud",
            parent_title="Apple ID",
            keywords=("icloud", "cloud", "sync", "icloud drive"),
            appex_name=appleid_appex,
        )
    )

    return entries


# ---------------------------------------------------------------------------
# Icon helpers
# ---------------------------------------------------------------------------


def _get_icon_png(appex_path: str) -> Optional[bytes]:
    """Return 32x32 PNG bytes for an .appex icon via NSWorkspace, or None."""
    try:
        from AppKit import (
            NSBitmapImageRep,
            NSCompositingOperationCopy,
            NSImage,
            NSPNGFileType,
            NSWorkspace,
        )
        from Foundation import NSMakeRect, NSSize

        ws = NSWorkspace.sharedWorkspace()
        icon = ws.iconForFile_(appex_path)
        if icon is None:
            return None

        size = NSSize(_ICON_SIZE, _ICON_SIZE)
        target = NSImage.alloc().initWithSize_(size)
        target.lockFocus()
        icon.drawInRect_fromRect_operation_fraction_(
            NSMakeRect(0, 0, _ICON_SIZE, _ICON_SIZE),
            NSMakeRect(0, 0, icon.size().width, icon.size().height),
            NSCompositingOperationCopy,
            1.0,
        )
        target.unlockFocus()

        rep = NSBitmapImageRep.imageRepWithData_(target.TIFFRepresentation())
        png_data = rep.representationUsingType_properties_(NSPNGFileType, None)
        return bytes(png_data) if png_data else None
    except Exception:
        logger.debug("Failed to get icon for %s", appex_path, exc_info=True)
        return None


def _cache_key(appex_name: str) -> str:
    """Stable cache filename for an appex name."""
    return hashlib.md5(appex_name.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# SystemSettingsSource — Chooser source for System Settings
# ---------------------------------------------------------------------------


class SystemSettingsSource:
    """Searches macOS System Settings panes and sub-items."""

    _MAX_RESULTS = 50

    def __init__(
        self,
        extensions_dir: str = _DEFAULT_EXTENSIONS_DIR,
        icon_cache_dir: str = _DEFAULT_ICON_CACHE_DIR,
        on_open: Optional[Callable[[], None]] = None,
    ) -> None:
        from wenzi.scripting.sources import (
            ChooserItem as _ChooserItem,
            ChooserSource as _ChooserSource,
            copy_to_clipboard,
            fuzzy_match,
            fuzzy_match_fields,
        )

        self._on_open = on_open
        self._copy_to_clipboard = copy_to_clipboard
        self._fuzzy_match = fuzzy_match
        self._fuzzy_match_fields = fuzzy_match_fields
        self._ChooserItem = _ChooserItem
        self._ChooserSource = _ChooserSource
        self._extensions_dir = extensions_dir
        self._icon_cache_dir = icon_cache_dir
        self._icon_cache: dict[str, str] = {}  # appex_name → file:// URL
        self._icon_lock = threading.Lock()

        self._entries = get_static_entries()

        # Pre-compute sorted panel list for empty-query fast path
        self._panel_entries = sorted(
            (e for e in self._entries if not e.parent_title),
            key=lambda e: e.title.lower(),
        )

        # Pre-warm icon cache in background
        appex_names = {e.appex_name for e in self._entries if e.appex_name}
        threading.Thread(
            target=self._prewarm_icons,
            args=(appex_names,),
            daemon=True,
        ).start()

        logger.info(
            "SystemSettingsSource loaded: %d entries", len(self._entries),
        )

    def set_on_open(self, callback: Optional[Callable[[], None]]) -> None:
        """Set the callback invoked when a system setting is opened."""
        self._on_open = callback

    def _prewarm_icons(self, appex_names: set[str]) -> None:
        """Pre-warm icon cache for all known appex names (runs in background)."""
        for name in appex_names:
            self._get_icon(name)

    def _get_icon(self, appex_name: str) -> str:
        """Return a file:// URL for the appex icon, with disk caching."""
        if not appex_name:
            return ""

        with self._icon_lock:
            cached = self._icon_cache.get(appex_name)
            if cached is not None:
                return cached
            # Mark as in-progress to prevent duplicate work
            self._icon_cache[appex_name] = ""

        key = _cache_key(appex_name)
        png_path = os.path.join(self._icon_cache_dir, f"ss_{key}.png")

        # Try disk cache
        try:
            if os.path.getsize(png_path) > 0:
                file_url = f"file://{png_path}"
                with self._icon_lock:
                    self._icon_cache[appex_name] = file_url
                return file_url
        except OSError:
            pass

        # Generate from NSWorkspace (only if appex exists on disk)
        appex_path = os.path.join(
            self._extensions_dir, f"{appex_name}.appex"
        )
        png_data = (
            _get_icon_png(appex_path) if os.path.isdir(appex_path) else None
        )

        if png_data:
            try:
                os.makedirs(self._icon_cache_dir, exist_ok=True)
                with open(png_path, "wb") as f:
                    f.write(png_data)
                file_url = f"file://{png_path}"
            except Exception:
                logger.debug("Failed to cache icon for %s", appex_name)
                file_url = ""
        else:
            file_url = ""

        with self._icon_lock:
            self._icon_cache[appex_name] = file_url
        return file_url

    def search(self, query: str) -> list["ChooserItem"]:
        """Search all entries. Empty query returns top-level panels."""
        q = query.strip()
        if not q:
            return [self._to_item(e) for e in self._panel_entries]

        scored: list[tuple[int, SettingsEntry]] = []
        fuzzy_match = self._fuzzy_match
        fuzzy_match_fields = self._fuzzy_match_fields
        for entry in self._entries:
            fields = (entry.title, entry.breadcrumb, *entry.keywords)
            matched, score = fuzzy_match_fields(q, fields)
            if matched:
                # Boost entries whose title directly matches the query
                title_matched, _ = fuzzy_match(q, entry.title)
                if title_matched:
                    score += 15
                scored.append((score, entry))

        scored.sort(key=lambda x: (-x[0], x[1].title.lower()))
        return [self._to_item(e) for _, e in scored[: self._MAX_RESULTS]]

    def as_chooser_source(self, prefix: str = "ss") -> list:
        """Return two ChooserSource instances: prefixed + unprefixed."""
        ChooserSource = self._ChooserSource
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

    def _search_mixed(self, query: str) -> list["ChooserItem"]:
        """Search for unprefixed mode: no results on empty, limited count."""
        if not query.strip():
            return []
        return self.search(query)[:5]

    def _to_item(self, entry: SettingsEntry) -> "ChooserItem":
        """Convert a SettingsEntry to a ChooserItem."""
        url = entry.url
        icon = self._get_icon(entry.appex_name)

        def _action(u=url, s=self):
            _open_url(u)
            if s._on_open:
                s._on_open()

        return self._ChooserItem(
            title=entry.title,
            subtitle=(
                entry.breadcrumb if entry.parent_title else "System Settings"
            ),
            icon=icon,
            item_id=entry.item_id,
            action=_action,
            secondary_action=lambda u=url, s=self: s._copy_to_clipboard(u),
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
