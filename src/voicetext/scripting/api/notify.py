"""vt.notify — macOS notification API."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def notify(title: str, message: str = "") -> None:
    """Send a macOS user notification."""
    from voicetext.statusbar import send_notification

    send_notification(title, "", message)
    logger.debug("Notification sent: %s", title)
