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
    position: Any = "center"  # "center", "top", "bottom", "mouse", or (x%, y%)


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

    Stores leaders, hotkeys, timers, and chooser sources registered by
    user scripts.
    """

    def __init__(self) -> None:
        self._leaders: Dict[str, LeaderConfig] = {}
        self._hotkeys: List[HotkeyBinding] = []
        self._timers: Dict[str, TimerEntry] = {}
        self._chooser_sources: Dict[str, Any] = {}  # name → ChooserSource
        self._event_listeners: Dict[str, List[Callable]] = {}
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

    @property
    def chooser_sources(self) -> Dict[str, Any]:
        return self._chooser_sources

    def register_leader(
        self,
        trigger_key: str,
        mappings: List[LeaderMapping],
        position: Any = "center",
    ) -> None:
        """Register a leader-key configuration."""
        self._leaders[trigger_key] = LeaderConfig(
            trigger_key=trigger_key, mappings=mappings, position=position,
        )
        logger.info(
            "Registered leader: %s with %d mappings", trigger_key, len(mappings)
        )

    def register_hotkey(self, hotkey_str: str, callback: Callable) -> None:
        """Register a hotkey binding."""
        self._hotkeys.append(HotkeyBinding(hotkey_str=hotkey_str, callback=callback))
        logger.info("Registered hotkey: %s", hotkey_str)

    def unregister_hotkey(self, hotkey_str: str) -> None:
        """Remove and stop a hotkey binding by its hotkey string."""
        to_remove = [b for b in self._hotkeys if b.hotkey_str == hotkey_str]
        for binding in to_remove:
            if binding.listener:
                try:
                    binding.listener.stop()
                except Exception:
                    pass
            self._hotkeys.remove(binding)
        if to_remove:
            logger.info("Unregistered hotkey: %s", hotkey_str)

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

    def register_event(self, event_name: str, callback: Callable) -> None:
        """Register a listener for a global event."""
        self._event_listeners.setdefault(event_name, []).append(callback)
        logger.info("Registered event listener: %s", event_name)

    def unregister_event(self, event_name: str, callback: Callable) -> None:
        """Remove a specific listener for a global event."""
        handlers = self._event_listeners.get(event_name, [])
        if callback in handlers:
            handlers.remove(callback)
            logger.info("Unregistered event listener: %s", event_name)

    def fire_event(self, event_name: str, **kwargs) -> None:
        """Invoke all handlers for *event_name* in a background thread."""
        handlers = list(self._event_listeners.get(event_name, []))
        if not handlers:
            return

        def _run():
            for handler in handlers:
                try:
                    handler(kwargs)
                except Exception:
                    logger.exception(
                        "Event handler error for %s", event_name
                    )

        threading.Thread(target=_run, daemon=True).start()

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
        self._chooser_sources.clear()
        self._event_listeners.clear()
        logger.info("Registry cleared")
