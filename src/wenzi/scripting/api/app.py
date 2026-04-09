"""vt.app — application management API."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class AppAPI:
    """Launch, activate, query, hide, and quit running applications."""

    def launch(self, app_name: str) -> bool:
        """Launch or activate an application by name or path.

        Returns True if successful.
        """
        try:
            from AppKit import NSWorkspace

            ws = NSWorkspace.sharedWorkspace()
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

    def running(self) -> list[dict]:
        """Return a list of running applications with regular activation policy.

        Each entry is a dict with ``name``, ``bundle_id``, and ``pid``.
        """
        try:
            from AppKit import NSWorkspace

            ws = NSWorkspace.sharedWorkspace()
            result = []
            for app in ws.runningApplications():
                # 0 = NSApplicationActivationPolicyRegular
                if app.activationPolicy() == 0:
                    result.append({
                        "name": app.localizedName() or "",
                        "bundle_id": app.bundleIdentifier() or "",
                        "pid": app.processIdentifier(),
                    })
            return result
        except Exception as exc:
            logger.error("Error listing running apps: %s", exc)
            return []

    def hide(self, name: str) -> bool:
        """Hide a running application by name. Returns True if found."""
        app = self._find_running_app(name)
        if app is None:
            return False
        try:
            app.hide()
            logger.info("Hid app: %s", name)
            return True
        except Exception as exc:
            logger.error("Error hiding app %s: %s", name, exc)
            return False

    def quit(self, name: str) -> bool:
        """Terminate a running application by name. Returns True if found."""
        app = self._find_running_app(name)
        if app is None:
            return False
        try:
            app.terminate()
            logger.info("Terminated app: %s", name)
            return True
        except Exception as exc:
            logger.error("Error terminating app %s: %s", name, exc)
            return False

    @staticmethod
    def _find_running_app(name: str) -> object | None:
        """Find a running application by localized name."""
        try:
            from AppKit import NSWorkspace

            ws = NSWorkspace.sharedWorkspace()
            for app in ws.runningApplications():
                if app.localizedName() == name:
                    return app
        except Exception as exc:
            logger.debug("Error finding app %s: %s", name, exc)
        return None
