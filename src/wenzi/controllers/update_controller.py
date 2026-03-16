"""Background update checker — queries GitHub Releases API periodically."""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
import webbrowser
from typing import TYPE_CHECKING, Any, Optional, Tuple

if TYPE_CHECKING:
    from wenzi.app import WenZiApp

from wenzi.statusbar import StatusMenuItem

logger = logging.getLogger(__name__)

GITHUB_REPO = "Airead/WenZi"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_REQUEST_TIMEOUT = 10  # seconds

# Menu item title prefix — used to identify the update item in the menu.
_MENU_TITLE_PREFIX = "Update available"


def _parse_version(version_str: str) -> Optional[Tuple[int, ...]]:
    """Parse 'v0.1.2' or '0.1.2' into (0, 1, 2). Returns None on failure."""
    cleaned = version_str.strip().lstrip("v")
    if not cleaned:
        return None
    try:
        return tuple(int(x) for x in cleaned.split("."))
    except (ValueError, AttributeError):
        return None


def _is_newer(latest: str, current: str) -> bool:
    """Return True if *latest* is a higher version than *current*."""
    l_ver = _parse_version(latest)
    c_ver = _parse_version(current)
    if l_ver is None or c_ver is None:
        return False
    return l_ver > c_ver


def _fetch_latest_release() -> Optional[dict[str, Any]]:
    """Fetch the latest release info from GitHub API.

    Returns the parsed JSON dict, or None on any failure.
    """
    try:
        req = urllib.request.Request(
            GITHUB_API_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "WenZi-UpdateChecker",
            },
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.debug("Update check failed: %s", exc)
        return None


class UpdateController:
    """Periodically checks GitHub for new releases and updates the app menu."""

    _DEFAULT_INTERVAL_HOURS = 6

    def __init__(self, app: "WenZiApp") -> None:
        self._app = app
        cfg = app._config.get("update_check", {})
        self._enabled = cfg.get("enabled", True)
        interval_hours = cfg.get("interval_hours", self._DEFAULT_INTERVAL_HOURS)
        self._interval = max(interval_hours, 1) * 3600
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._update_menu_item: Optional[StatusMenuItem] = None
        self._latest_version: Optional[str] = None
        self._release_url: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        """Start the periodic update check (first check runs immediately)."""
        if not self._enabled:
            return
        threading.Thread(target=self._check_update, daemon=True).start()

    def stop(self) -> None:
        """Cancel any pending timer."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _schedule_next_check(self) -> None:
        """Schedule the next update check after the configured interval."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._interval, self._check_update)
            self._timer.daemon = True
            self._timer.start()

    def _check_update(self) -> None:
        """Perform the update check (runs in a background thread)."""
        try:
            from wenzi import __version__

            current = os.environ.get("WENZI_DEV_VERSION") or __version__
            if current == "dev":
                logger.debug("Skipping update check in dev mode")
                return

            data = _fetch_latest_release()
            if data is None:
                return

            tag = data.get("tag_name", "")
            html_url = data.get("html_url", "")

            if _is_newer(tag, current):
                logger.info("New version available: %s (current: %s)", tag, current)
                self._latest_version = tag
                self._release_url = html_url
                from PyObjCTools import AppHelper

                AppHelper.callAfter(self._apply_update_menu, tag, html_url)
            else:
                logger.debug("Already up to date: %s", current)
                # Remove stale menu item if version was updated
                if self._update_menu_item is not None:
                    from PyObjCTools import AppHelper

                    AppHelper.callAfter(self._remove_update_menu)
        except Exception as exc:
            logger.debug("Update check error: %s", exc)
        finally:
            self._schedule_next_check()

    def _apply_update_menu(self, version: str, url: str) -> None:
        """Insert or update the 'Update available' menu item (main thread)."""
        title = f"{_MENU_TITLE_PREFIX}: {version}"

        # Already showing the same version
        if (
            self._update_menu_item is not None
            and self._update_menu_item._menuitem.title() == title
        ):
            return

        # Remove old item if present
        self._remove_update_menu()

        self._release_url = url
        item = StatusMenuItem(title, callback=self._on_update_click)
        try:
            self._app._menu.insert_before("About WenZi", item)
            self._update_menu_item = item
        except KeyError:
            logger.debug("Could not insert update menu item: 'About WenZi' not found")

    def _remove_update_menu(self) -> None:
        """Remove the update menu item if present (main thread)."""
        if self._update_menu_item is not None:
            title = self._update_menu_item._menuitem.title()
            try:
                del self._app._menu[title]
            except KeyError:
                pass
            self._update_menu_item = None

    def _on_update_click(self, _: Any) -> None:
        """Open the GitHub release page in the default browser."""
        if self._release_url:
            webbrowser.open(self._release_url)
