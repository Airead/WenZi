"""wz.timer — delayed and repeating execution API.

Scheduling uses :func:`async_loop.call_later` (single shared asyncio
event-loop timer) instead of ``threading.Timer`` to avoid spawning a
new thread for every timer tick.  User callbacks are offloaded to the
asyncio default thread-pool executor so they cannot block the loop.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from wenzi import async_loop
from wenzi.scripting.api._async_util import wrap_async
from wenzi.scripting.registry import ScriptingRegistry

logger = logging.getLogger(__name__)


class TimerAPI:
    """Schedule delayed or repeating callbacks."""

    def __init__(self, registry: ScriptingRegistry) -> None:
        self._registry = registry

    def after(self, seconds: float, callback: Callable) -> str:
        """Execute callback once after a delay. Returns timer_id.

        *callback* may be a regular function or an ``async def``.
        """
        entry = self._registry.register_timer(
            seconds, wrap_async(callback), repeating=False,
        )
        entry._timer = async_loop.call_later(
            seconds, self._fire_once, entry.timer_id,
        )
        return entry.timer_id

    def every(self, seconds: float, callback: Callable) -> str:
        """Execute callback repeatedly at interval. Returns timer_id.

        *callback* may be a regular function or an ``async def``.
        """
        entry = self._registry.register_timer(
            seconds, wrap_async(callback), repeating=True,
        )
        self._schedule_repeat(entry.timer_id)
        return entry.timer_id

    def cancel(self, timer_id: str) -> None:
        """Cancel a timer."""
        self._registry.cancel_timer(timer_id)

    def _fire_once(self, timer_id: str) -> None:
        """Fire a one-shot timer and remove it."""
        entry = self._registry.pop_timer(timer_id)
        if entry is None:
            return
        # Offload to executor so sync user callbacks don't block the loop.
        async_loop.get_loop().run_in_executor(
            None, self._safe_callback, entry.callback,
        )

    def _schedule_repeat(self, timer_id: str) -> None:
        """Schedule the next tick of a repeating timer."""
        entry = self._registry.get_timer(timer_id)
        if entry is None:
            return
        entry._timer = async_loop.call_later(
            entry.interval, self._fire_repeat, timer_id,
        )

    def _fire_repeat(self, timer_id: str) -> None:
        """Fire a repeating timer and reschedule."""
        entry = self._registry.get_timer(timer_id)
        if entry is None:
            return
        cb = entry.callback

        def _run() -> None:
            self._safe_callback(cb)
            # call_later uses call_soon_threadsafe, safe from executor.
            self._schedule_repeat(timer_id)

        async_loop.get_loop().run_in_executor(None, _run)

    @staticmethod
    def _safe_callback(callback: Callable) -> None:
        """Execute a user callback with error handling."""
        try:
            callback()
        except Exception as exc:
            logger.error("Timer callback error: %s", exc)
