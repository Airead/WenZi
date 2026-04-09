"""vt.execute — shell command execution API."""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


def execute(
    command: str,
    background: bool = True,
    timeout: int = 30,
    on_done: Callable[[dict], None] | None = None,
) -> dict | None:
    """Execute a shell command.

    Args:
        command: Shell command string.
        background: If True, run in a daemon thread and return None immediately.
                    If False, block and return a result dict.
        timeout: Maximum seconds to wait for the command.
        on_done: If provided (background mode only), called with the result
                 dict when the command completes.

    Returns:
        A dict with ``stdout``, ``stderr``, and ``returncode`` keys when
        *background* is False.  None when *background* is True.
    """
    if background:
        def _bg():
            result = _run(command, timeout=timeout)
            if on_done is not None:
                try:
                    on_done(result)
                except Exception:
                    logger.exception("on_done callback error")

        threading.Thread(target=_bg, daemon=True).start()
        return None
    return _run(command, timeout=timeout)


def _run(command: str, timeout: int = 30) -> dict:
    """Run command and return a structured result dict."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            logger.warning(
                "Command failed (rc=%d): %s\nstderr: %s",
                result.returncode,
                command,
                result.stderr.strip(),
            )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        logger.error("Command timed out: %s", command)
        return {"stdout": "", "stderr": "timeout", "returncode": -1}
    except Exception as exc:
        logger.error("Command error: %s — %s", command, exc)
        return {"stdout": "", "stderr": str(exc), "returncode": -1}
