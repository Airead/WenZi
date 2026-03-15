"""Registration center for all scripting resources."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LeaderMapping:
    """Single leader-key mapping entry."""

    key: str
    desc: str = ""
    app: Optional[str] = None
    func: Optional[Callable] = None
    exec_cmd: Optional[str] = None


@dataclass
class LeaderConfig:
    """A complete leader-key configuration."""

    trigger_key: str
    mappings: List[LeaderMapping] = field(default_factory=list)


@dataclass
class HotkeyBinding:
    """A hotkey combination binding."""

    hotkey_str: str
    callback: Callable
    listener: Any = None  # TapHotkeyListener instance, set at start time


@dataclass
class TimerEntry:
    """A registered timer."""

    timer_id: str
    interval: float
    callback: Callable
    repeating: bool
    _timer: Optional[threading.Timer] = field(default=None, repr=False)


class ScriptingRegistry:
    """Registration center for all scripting resources.

    Stores leaders, hotkeys, and timers registered by user scripts.
    """

    def __init__(self) -> None:
        self._leaders: Dict[str, LeaderConfig] = {}
        self._hotkeys: List[HotkeyBinding] = []
        self._timers: Dict[str, TimerEntry] = {}
        self._lock = threading.Lock()

    @property
    def leaders(self) -> Dict[str, LeaderConfig]:
        return self._leaders

    @property
    def hotkeys(self) -> List[HotkeyBinding]:
        return self._hotkeys

    @property
    def timers(self) -> Dict[str, TimerEntry]:
        return self._timers

    def register_leader(self, trigger_key: str, mappings: List[LeaderMapping]) -> None:
        """Register a leader-key configuration."""
        self._leaders[trigger_key] = LeaderConfig(
            trigger_key=trigger_key, mappings=mappings
        )
        logger.info(
            "Registered leader: %s with %d mappings", trigger_key, len(mappings)
        )

    def register_hotkey(self, hotkey_str: str, callback: Callable) -> None:
        """Register a hotkey binding."""
        self._hotkeys.append(HotkeyBinding(hotkey_str=hotkey_str, callback=callback))
        logger.info("Registered hotkey: %s", hotkey_str)

    def register_timer(
        self, interval: float, callback: Callable, repeating: bool = False
    ) -> str:
        """Register a timer. Returns a unique timer_id."""
        timer_id = str(uuid.uuid4())
        entry = TimerEntry(
            timer_id=timer_id,
            interval=interval,
            callback=callback,
            repeating=repeating,
        )
        with self._lock:
            self._timers[timer_id] = entry
        logger.info(
            "Registered timer %s (interval=%.1fs, repeating=%s)",
            timer_id[:8],
            interval,
            repeating,
        )
        return timer_id

    def cancel_timer(self, timer_id: str) -> None:
        """Cancel and remove a timer."""
        with self._lock:
            entry = self._timers.pop(timer_id, None)
        if entry and entry._timer:
            entry._timer.cancel()
            logger.info("Cancelled timer %s", timer_id[:8])

    def clear(self) -> None:
        """Stop all timers and clear all registrations."""
        with self._lock:
            for entry in self._timers.values():
                if entry._timer:
                    entry._timer.cancel()
            self._timers.clear()
        # Stop hotkey listeners
        for binding in self._hotkeys:
            if binding.listener:
                try:
                    binding.listener.stop()
                except Exception:
                    pass
        self._hotkeys.clear()
        self._leaders.clear()
        logger.info("Registry cleared")
