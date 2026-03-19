"""vt.timer — delayed and repeating execution API."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from wenzi.scripting.registry import ScriptingRegistry

logger = logging.getLogger(__name__)


class TimerAPI:
    """Schedule delayed or repeating callbacks."""

    def __init__(self, registry: ScriptingRegistry) -> None:
        self._registry = registry

    def after(self, seconds: float, callback: Callable) -> str:
        """Execute callback once after a delay. Returns timer_id."""
        entry = self._registry.register_timer(seconds, callback, repeating=False)
        t = threading.Timer(seconds, self._fire_once, args=(entry.timer_id,))
        t.daemon = True
        entry._timer = t
        t.start()
        return entry.timer_id

    def every(self, seconds: float, callback: Callable) -> str:
        """Execute callback repeatedly at interval. Returns timer_id."""
        entry = self._registry.register_timer(seconds, callback, repeating=True)
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
        try:
            entry.callback()
        except Exception as exc:
            logger.error("Timer callback error: %s", exc)

    def _schedule_repeat(self, timer_id: str) -> None:
        """Schedule the next tick of a repeating timer."""
        entry = self._registry.get_timer(timer_id)
        if entry is None:
            return
        t = threading.Timer(entry.interval, self._fire_repeat, args=(timer_id,))
        t.daemon = True
        entry._timer = t
        t.start()

    def _fire_repeat(self, timer_id: str) -> None:
        """Fire a repeating timer and reschedule."""
        entry = self._registry.get_timer(timer_id)
        if entry is None:
            return
        try:
            entry.callback()
        except Exception as exc:
            logger.error("Repeating timer callback error: %s", exc)
        # Reschedule
        self._schedule_repeat(timer_id)
