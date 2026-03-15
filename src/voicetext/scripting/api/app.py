"""vt.app — application management API."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class AppAPI:
    """Launch, activate, and query running applications."""

    def launch(self, app_name: str) -> bool:
        """Launch or activate an application by name or path.

        Returns True if successful.
        """
        try:
            from AppKit import NSWorkspace

            ws = NSWorkspace.sharedWorkspace()
            if app_name.endswith(".app"):
                # Full path to .app bundle
                ok = ws.launchApplication_(app_name)
            else:
                ok = ws.launchApplication_(app_name)
            if ok:
                logger.info("Launched app: %s", app_name)
            else:
                logger.warning("Failed to launch app: %s", app_name)
            return bool(ok)
        except Exception as exc:
            logger.error("Error launching app %s: %s", app_name, exc)
            return False

    def frontmost(self) -> str | None:
        """Return the localized name of the frontmost application."""
        try:
            from AppKit import NSWorkspace

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app:
                return app.localizedName()
            return None
        except Exception as exc:
            logger.debug("Failed to get frontmost app: %s", exc)
            return None
