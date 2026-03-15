"""vt.execute — shell command execution API."""

from __future__ import annotations

import logging
import subprocess
import threading

logger = logging.getLogger(__name__)


def execute(command: str, background: bool = True) -> str | None:
    """Execute a shell command.

    Args:
        command: Shell command string.
        background: If True, run in a daemon thread and return None immediately.
                    If False, block and return stdout.
    """
    if background:
        threading.Thread(target=_run, args=(command,), daemon=True).start()
        return None
    return _run(command)


def _run(command: str) -> str:
    """Run command and return stdout."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            logger.warning(
                "Command failed (rc=%d): %s\nstderr: %s",
                result.returncode,
                command,
                result.stderr.strip(),
            )
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.error("Command timed out: %s", command)
        return ""
    except Exception as exc:
        logger.error("Command error: %s — %s", command, exc)
        return ""
