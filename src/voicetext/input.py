"""Text injection into the active macOS application."""

from __future__ import annotations

import logging
import subprocess
import threading
import time


logger = logging.getLogger(__name__)


def type_text(text: str, append_newline: bool = False, method: str = "auto") -> None:
    """Type text into the currently focused text field on macOS.

    Methods:
        clipboard: pbcopy + Cmd+V (fast, reliable)
        applescript: AppleScript keystroke (good Unicode support)
        auto: try clipboard first, fall back to applescript
    """
    if not text:
        return

    payload = text + ("\n" if append_newline else "")

    method = (method or "auto").lower()
    if method == "clipboard":
        order = ["clipboard"]
    elif method == "applescript":
        order = ["applescript"]
    else:
        order = ["clipboard", "applescript"]

    for mode in order:
        if mode == "clipboard" and _type_via_clipboard(payload):
            logger.info("Text injected via clipboard: %s", payload[:50])
            return
        if mode == "applescript" and _type_via_applescript(payload):
            logger.info("Text injected via applescript: %s", payload[:50])
            return

    logger.error("All text injection methods failed")


def _type_via_clipboard(payload: str) -> bool:
    """Copy to clipboard then simulate Cmd+V."""
    try:
        old_clip = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=2
        ).stdout
    except Exception:
        old_clip = None

    try:
        proc = subprocess.run(["pbcopy"], input=payload, text=True, timeout=2)
        if proc.returncode != 0:
            logger.warning("pbcopy failed with returncode %d", proc.returncode)
            return False

        # Small delay to ensure clipboard is ready
        time.sleep(0.05)

        result = subprocess.run(
            [
                "osascript", "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ],
            capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            logger.warning("Cmd+V osascript failed: %s",
                           result.stderr.decode(errors="replace"))
            return False
        return True
    except Exception as exc:
        logger.warning("Clipboard injection failed: %s", exc)
        return False
    finally:
        if old_clip is not None:
            def _restore():
                time.sleep(1.0)
                try:
                    subprocess.run(["pbcopy"], input=old_clip, text=True, timeout=2)
                except Exception:
                    pass
            threading.Thread(target=_restore, daemon=True).start()


def _type_via_applescript(payload: str) -> bool:
    """Use AppleScript keystroke to type text."""
    try:
        escaped = payload.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "System Events" to keystroke "{escaped}"'
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            logger.warning("AppleScript keystroke failed: %s",
                           result.stderr.decode(errors="replace"))
            return False
        return True
    except Exception as exc:
        logger.warning("AppleScript injection failed: %s", exc)
        return False
