"""Connection pool monitoring for AI enhancement LLM clients.

Provides dual-layer monitoring:
1. httpcore pool layer — ACTIVE / IDLE / CLOSED connection counts
2. OS socket layer — actual TCP connections (ESTABLISHED / TIME_WAIT / CLOSE_WAIT)

Usage::

    monitor = PoolMonitor(providers)
    monitor.log_stats("before stream")   # one-shot log
    monitor.start_periodic(interval=60)  # background periodic logging
    monitor.stop_periodic()
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Layer 1: httpcore connection pool stats
# ---------------------------------------------------------------------------

def _get_pool(client: Any) -> Any | None:
    """Extract the httpcore AsyncConnectionPool from an AsyncOpenAI client."""
    try:
        return client._client._transport._pool
    except AttributeError:
        return None


def get_pool_stats(client: Any) -> dict[str, int]:
    """Return connection counts from the httpcore pool.

    Keys: ``active``, ``idle``, ``closed``, ``total``.
    """
    pool = _get_pool(client)
    if pool is None:
        return {"active": 0, "idle": 0, "closed": 0, "total": 0}

    active = idle = closed = 0
    for conn in pool.connections:
        info = str(conn.info())
        if "IDLE" in info:
            idle += 1
        elif "CLOSED" in info:
            closed += 1
        else:
            # ACTIVE, CONNECTING, etc.
            active += 1
    return {"active": active, "idle": idle, "closed": closed, "total": active + idle + closed}


def get_pool_details(client: Any) -> list[str]:
    """Return per-connection detail strings from the httpcore pool."""
    pool = _get_pool(client)
    if pool is None:
        return []
    return [repr(conn) for conn in pool.connections]


# ---------------------------------------------------------------------------
# Layer 2: OS socket stats via lsof
# ---------------------------------------------------------------------------

def _parse_host_port(base_url: str) -> tuple[str, int] | None:
    """Extract (host, port) from a provider base_url."""
    try:
        parsed = urlparse(base_url)
        host = parsed.hostname
        port = parsed.port
        if host and port:
            return (host, port)
        if host and parsed.scheme == "https":
            return (host, 443)
        if host and parsed.scheme == "http":
            return (host, 80)
    except Exception:
        pass
    return None


def get_os_socket_stats(base_url: str) -> dict[str, int]:
    """Count OS-level TCP connections to the provider endpoint.

    Uses ``lsof`` on macOS to enumerate connections.
    Keys: ``ESTABLISHED``, ``TIME_WAIT``, ``CLOSE_WAIT``, ``total``.
    """
    result: dict[str, int] = {
        "ESTABLISHED": 0,
        "TIME_WAIT": 0,
        "CLOSE_WAIT": 0,
        "total": 0,
    }
    hp = _parse_host_port(base_url)
    if hp is None:
        return result

    host, port = hp
    try:
        import os

        out = subprocess.run(
            ["lsof", "-i", f"TCP@{host}:{port}", "-n", "-P",
             "-a", "-p", str(os.getpid())],
            capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.splitlines()[1:]:  # skip header
            parts = line.split()
            if not parts:
                continue
            # Last column contains state like "(ESTABLISHED)"
            state = parts[-1].strip("()")
            if state in result:
                result[state] += 1
            result["total"] += 1
    except Exception as e:
        logger.debug("OS socket stats unavailable: %s", e)
    return result


# ---------------------------------------------------------------------------
# PoolMonitor — aggregates both layers
# ---------------------------------------------------------------------------

class PoolMonitor:
    """Dual-layer connection pool monitor.

    Parameters
    ----------
    providers : dict
        ``{name: (AsyncOpenAI_client, models, extra_body)}`` — the same dict
        stored in ``TextEnhancer._providers``.
    providers_config : dict
        ``{name: {base_url, api_key, ...}}`` — provider config with base_url.
    """

    def __init__(
        self,
        providers: dict[str, tuple[Any, list[str], dict[str, Any]]],
        providers_config: dict[str, Any],
    ) -> None:
        self._providers = providers
        self._providers_config = providers_config
        self._periodic_task: asyncio.Task | None = None
        self._stop_flag = False

    # -- one-shot logging ---------------------------------------------------

    def log_stats(self, label: str, provider_name: str = "") -> None:
        """Log pool + OS socket stats for one or all providers."""
        names = [provider_name] if provider_name else list(self._providers.keys())
        for name in names:
            entry = self._providers.get(name)
            if entry is None:
                continue
            client = entry[0]
            pool = get_pool_stats(client)
            base_url = self._providers_config.get(name, {}).get(
                "base_url", ""
            )
            os_stats = get_os_socket_stats(base_url) if base_url else {}
            logger.info(
                "[ConnPool:%s] %s | pool: active=%d idle=%d closed=%d total=%d"
                " | os: ESTABLISHED=%d TIME_WAIT=%d CLOSE_WAIT=%d total=%d",
                name, label,
                pool["active"], pool["idle"], pool["closed"], pool["total"],
                os_stats.get("ESTABLISHED", 0),
                os_stats.get("TIME_WAIT", 0),
                os_stats.get("CLOSE_WAIT", 0),
                os_stats.get("total", 0),
            )
            details = get_pool_details(client)
            if details:
                for d in details:
                    logger.info("[ConnPool:%s]   %s", name, d)

    # -- periodic background logging ----------------------------------------

    def start_periodic(self, interval: float = 60.0) -> None:
        """Start a background coroutine that logs stats every *interval* secs."""
        if self._periodic_task is not None and not self._periodic_task.done():
            return  # already running

        self._stop_flag = False

        async def _loop() -> None:
            try:
                while not self._stop_flag:
                    await asyncio.sleep(interval)
                    if self._stop_flag:
                        break
                    try:
                        self.log_stats("periodic")
                    except Exception:
                        logger.debug("Periodic pool stats failed", exc_info=True)
            except asyncio.CancelledError:
                logger.debug("Periodic pool monitor cancelled")
                return

        try:
            from wenzi import async_loop
            future = async_loop.submit(_loop())
            self._periodic_task = future  # type: ignore[assignment]
        except Exception:
            logger.debug("Failed to start periodic pool monitor", exc_info=True)

    def stop_periodic(self) -> None:
        """Stop the periodic logging task."""
        self._stop_flag = True
        if self._periodic_task is not None:
            try:
                self._periodic_task.cancel()
            except Exception:
                pass
            self._periodic_task = None
