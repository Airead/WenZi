"""Background update checker — queries GitHub Releases API periodically."""

from __future__ import annotations

import collections
import json
import logging
import os
import sys
import urllib.request
import webbrowser
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wenzi.app import WenZiApp
    from wenzi.updater import AppUpdater

from wenzi import async_loop, get_version
from wenzi.i18n import t
from wenzi.statusbar import StatusMenuItem

logger = logging.getLogger(__name__)

GITHUB_REPO = "Airead/WenZi"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_REQUEST_TIMEOUT = 10  # seconds

# Menu item title prefixes.
_MENU_TITLE_PREFIX = "Update available"
_RESTART_TITLE_PREFIX = "Restart to update"


def _is_frozen() -> bool:
    """Check if running as a frozen (packaged) app.

    Also returns True when WENZI_FORCE_AUTO_UPDATE=1 is set,
    allowing dev-mode testing of the full auto-update flow.
    """
    return getattr(sys, "frozen", False) or os.environ.get(
        "WENZI_FORCE_AUTO_UPDATE"
    ) == "1"


def _parse_version(version_str: str) -> tuple[int, ...] | None:
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


def _fetch_latest_release() -> dict[str, Any] | None:
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


def _find_dmg_url(release_data: dict) -> str | None:
    """Find the .dmg asset download URL matching the current build type.

    Lite builds match DMG names containing 'Lite';
    Standard builds match DMG names not containing 'Lite'.
    """
    from wenzi.app import get_build_type

    is_lite = get_build_type() == "lite"
    assets = release_data.get("assets", [])
    for asset in assets:
        name = asset.get("name", "")
        if not name.endswith(".dmg"):
            continue
        if is_lite and "Lite" in name:
            return asset.get("browser_download_url")
        if not is_lite and "Lite" not in name:
            return asset.get("browser_download_url")
    return None


class UpdateController:
    """Periodically checks GitHub for new releases and updates the app menu."""

    _DEFAULT_INTERVAL_HOURS = 6

    def __init__(self, app: WenZiApp) -> None:
        self._app = app
        cfg = app._config.get("update_check", {})
        self._enabled = cfg.get("enabled", True)
        interval_hours = cfg.get("interval_hours", self._DEFAULT_INTERVAL_HOURS)
        self._interval = max(interval_hours, 1) * 3600
        self._timer: async_loop.TimerHandle | None = None
        self._update_menu_item: StatusMenuItem | None = None
        self._latest_version: str | None = None
        self._release_url: str | None = None
        self._release_data: dict | None = None
        self._updater: AppUpdater | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        """Start the periodic update check (first check runs immediately)."""
        if not self._enabled:
            return
        # Apply previously staged update, or clean up if invalid
        if _is_frozen():
            if self._try_apply_staged_update():
                return  # app is quitting to apply the update
        async_loop.get_loop().run_in_executor(None, self._check_update)

    def _try_apply_staged_update(self) -> bool:
        """Check for a staged update and apply it if valid.

        Returns True if the app should quit (swap script spawned),
        False if no valid staged update was found (cleaned up or absent).
        """
        from wenzi.updater import AppUpdater

        staged = AppUpdater.get_staged_app_path()
        if staged is None:
            return False

        # Verify code signature
        try:
            AppUpdater._verify_app(staged)
        except Exception:
            logger.warning("Staged update failed verification, removing")
            AppUpdater.cleanup_staged_app()
            return False

        # Compare versions — don't downgrade
        staged_version = AppUpdater.get_app_version(staged)
        if staged_version is None:
            logger.warning("Cannot read staged app version, removing")
            AppUpdater.cleanup_staged_app()
            return False

        current = get_version()
        if not _is_newer(staged_version, current):
            logger.info(
                "Staged app %s is not newer than current %s, removing",
                staged_version,
                current,
            )
            AppUpdater.cleanup_staged_app()
            return False

        # Show brief confirmation and apply
        from wenzi.ui_helpers import restore_accessory, topmost_alert

        result = topmost_alert(
            title=t("alert.update.staged_title", version=staged_version),
            message=t("alert.update.staged_message"),
            ok=t("alert.update.restart_now"),
            cancel=t("common.later"),
        )
        restore_accessory()

        if result != 1:
            return False

        if not AppUpdater.perform_swap_and_relaunch():
            logger.error("Failed to spawn swap script, cleaning up")
            AppUpdater.cleanup_staged_app()
            return False

        # Quit the app — swap script will handle the rest
        self._app._on_quit_click(None)
        return True

    def stop(self) -> None:
        """Cancel any pending timer and running updater."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._updater is not None:
            self._updater.cancel()

    def _schedule_next_check(self) -> None:
        """Schedule the next update check after the configured interval."""
        if self._timer is not None:
            self._timer.cancel()
        self._timer = async_loop.call_later(
            self._interval, self._dispatch_check,
        )

    def _dispatch_check(self) -> None:
        """Submit the blocking update check to the asyncio default executor.

        Called on the asyncio event loop by ``call_later``; the actual
        HTTP request runs in the thread-pool executor to avoid blocking.
        """
        async_loop.get_loop().run_in_executor(None, self._check_update)

    def _check_update(self) -> None:
        """Perform the update check (runs in the asyncio thread-pool executor)."""
        try:
            current = get_version()
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
                self._release_data = data
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
        menu = self._app._menu
        ns_menu = menu._ensure_submenu()
        ns_menu.insertItem_atIndex_(item._menuitem, 0)
        # Keep OrderedDict in sync — prepend to front
        menu._items = collections.OrderedDict(
            [(title, item)] + list(menu._items.items())
        )
        self._update_menu_item = item

    def _remove_update_menu(self) -> None:
        """Remove the update menu item if present (main thread)."""
        if self._update_menu_item is not None:
            title = self._update_menu_item._menuitem.title()
            try:
                del self._app._menu[title]
            except KeyError:
                pass
            self._update_menu_item = None

    def _open_release_in_browser(self) -> None:
        """Open the GitHub release page in the default browser."""
        if self._release_url:
            webbrowser.open(self._release_url)

    def _on_update_click(self, _: Any) -> None:
        """Handle update menu item click."""
        if not self._release_url:
            return

        # In frozen mode, try auto-update if DMG asset is available
        if _is_frozen() and self._release_data is not None:
            dmg_url = _find_dmg_url(self._release_data)
            if dmg_url is not None:
                self._try_auto_update(dmg_url)
                return

        self._open_release_in_browser()

    def _try_auto_update(self, dmg_url: str) -> None:
        """Attempt in-app auto-update with user confirmation."""
        from wenzi.ui_helpers import restore_accessory, topmost_alert
        from wenzi.updater import AppUpdater

        app_path = AppUpdater.get_app_bundle_path()

        if not AppUpdater.is_writable(app_path):
            topmost_alert(
                title=t("alert.update.cannot_title"),
                message=t("alert.update.cannot_message",
                           app=AppUpdater._app_name(), path=str(app_path.parent)),
            )
            restore_accessory()
            self._open_release_in_browser()
            return

        # Confirm with user
        version = self._latest_version or "new version"
        result = topmost_alert(
            title=t("alert.update.title", version=version),
            message=t("alert.update.message"),
            ok=t("alert.update.install"),
            cancel=t("common.cancel"),
        )
        restore_accessory()

        if result != 1:
            return

        self._start_auto_update(dmg_url)

    def _start_auto_update(self, dmg_url: str) -> None:
        """Create AppUpdater and start the download."""
        if self._updater is not None:
            return  # already in progress

        from wenzi.updater import AppUpdater

        self._updater = AppUpdater(
            dmg_url=dmg_url,
            version=self._latest_version or "",
            on_progress=self._on_update_progress,
            on_error=self._on_update_error,
            on_ready=self._on_update_ready,
        )
        self._updater.start()

    def _on_update_progress(self, msg: str) -> None:
        """Update menu title with download progress (called from background)."""
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._set_menu_title, msg)

    def _set_menu_title(self, title: str) -> None:
        """Set the update menu item title (main thread)."""
        if self._update_menu_item is not None:
            self._update_menu_item.title = title

    def _on_update_error(self, msg: str) -> None:
        """Show error alert and restore menu (called from background)."""
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._show_update_error, msg)

    def _show_update_error(self, msg: str) -> None:
        """Show error alert with browser fallback (main thread)."""
        from wenzi.ui_helpers import restore_accessory, topmost_alert

        self._updater = None

        # Restore menu title
        if self._update_menu_item is not None and self._latest_version:
            self._update_menu_item.title = (
                f"{_MENU_TITLE_PREFIX}: {self._latest_version}"
            )
            self._update_menu_item.set_callback(self._on_update_click)

        result = topmost_alert(
            title=t("alert.update.failed.title"),
            message=t("alert.update.failed.message", error=msg),
            ok=t("alert.update.open_browser"),
            cancel=t("common.cancel"),
        )
        restore_accessory()

        if result == 1:
            self._open_release_in_browser()

    def _on_update_ready(self) -> None:
        """Change menu to 'Restart to update' (called from background)."""
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._apply_restart_menu)

    def _apply_restart_menu(self) -> None:
        """Update menu to show restart option (main thread)."""
        version = self._latest_version or ""
        if self._update_menu_item is not None:
            self._update_menu_item.title = f"{_RESTART_TITLE_PREFIX} {version}"
            self._update_menu_item.set_callback(self._on_restart_to_update)

    def _on_restart_to_update(self, _: Any) -> None:
        """Confirm restart, swap app, and quit (main thread)."""
        from wenzi.ui_helpers import restore_accessory, topmost_alert
        from wenzi.updater import AppUpdater

        version = self._latest_version or "new version"
        result = topmost_alert(
            title=t("alert.update.restart_title", version=version),
            message=t("alert.update.restart_message"),
            ok=t("alert.update.restart_now"),
            cancel=t("common.later"),
        )
        restore_accessory()

        if result != 1:
            return

        if not AppUpdater.perform_swap_and_relaunch():
            topmost_alert(
                title=t("alert.update.failed.title"),
                message=t("alert.update.swap_failed_message"),
            )
            restore_accessory()
            self._open_release_in_browser()
            return

        # Quit the app using existing quit flow
        self._app._on_quit_click(None)
