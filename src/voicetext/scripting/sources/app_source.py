"""App search data source for the Chooser.

Scans /Applications and ~/Applications for .app bundles, checks running
status via NSWorkspace, and provides substring matching with running apps
ranked first.
"""

from __future__ import annotations

import logging
import os
from typing import List

from voicetext.scripting.sources import ChooserItem, ChooserSource

logger = logging.getLogger(__name__)

# Directories to scan for applications
_APP_DIRS = ["/Applications", os.path.expanduser("~/Applications")]


def _scan_apps() -> list[dict]:
    """Scan application directories and return a list of app info dicts.

    Each dict has keys: name (str), path (str).
    """
    apps = []
    seen = set()

    for app_dir in _APP_DIRS:
        if not os.path.isdir(app_dir):
            continue
        try:
            entries = os.listdir(app_dir)
        except OSError:
            continue

        for entry in entries:
            if not entry.endswith(".app"):
                continue
            full_path = os.path.join(app_dir, entry)
            name = entry[:-4]  # Strip ".app"
            if name in seen:
                continue
            seen.add(name)
            apps.append({"name": name, "path": full_path})

    logger.info("Scanned %d apps from %s", len(apps), _APP_DIRS)
    return apps


def _get_running_app_names() -> set[str]:
    """Return a set of currently running application names."""
    try:
        from AppKit import NSWorkspace

        workspace = NSWorkspace.sharedWorkspace()
        running = workspace.runningApplications()
        return {
            str(app.localizedName())
            for app in running
            if app.localizedName()
        }
    except Exception:
        logger.debug("Failed to get running apps", exc_info=True)
        return set()


def _launch_app(path: str) -> None:
    """Launch or activate an application by path."""
    try:
        from AppKit import NSWorkspace

        workspace = NSWorkspace.sharedWorkspace()
        workspace.launchApplication_(path)
    except Exception:
        logger.exception("Failed to launch app: %s", path)


class AppSource:
    """Application search data source.

    Scans app directories once on init and caches the list.
    Running status is checked on every search for fresh results.
    """

    def __init__(self) -> None:
        self._apps: list[dict] = []
        self._scanned = False

    def _ensure_scanned(self) -> None:
        if not self._scanned:
            self._apps = _scan_apps()
            self._scanned = True

    def rescan(self) -> None:
        """Force a rescan of application directories."""
        self._apps = _scan_apps()
        self._scanned = True

    def search(self, query: str) -> List[ChooserItem]:
        """Search apps by substring matching, running apps first."""
        self._ensure_scanned()

        if not query.strip():
            return []

        q = query.lower()
        running = _get_running_app_names()

        matches = []
        for app in self._apps:
            if q not in app["name"].lower():
                continue
            is_running = app["name"] in running
            path = app["path"]
            matches.append((is_running, app["name"], path))

        # Sort: running apps first, then alphabetical
        matches.sort(key=lambda x: (not x[0], x[1].lower()))

        return [
            ChooserItem(
                title=name,
                subtitle="Running" if is_running else "Application",
                action=lambda p=path: _launch_app(p),
                reveal_path=path,
            )
            for is_running, name, path in matches
        ]

    def as_chooser_source(self) -> ChooserSource:
        """Return a ChooserSource wrapping this AppSource."""
        return ChooserSource(
            name="apps",
            prefix=None,
            search=self.search,
            priority=10,
        )
