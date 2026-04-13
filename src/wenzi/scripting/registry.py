"""Registration center for all scripting resources."""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LeaderMapping:
    """Single leader-key mapping entry."""

    key: str
    desc: str = ""
    app: str | None = None
    func: Callable | None = None
    exec_cmd: str | None = None


@dataclass
class LeaderConfig:
    """A complete leader-key configuration."""

    trigger_key: str
    mappings: list[LeaderMapping] = field(default_factory=list)
    position: Any = "center"  # "center", "top", "bottom", "mouse", or (x%, y%)


@dataclass
class HotkeyBinding:
    """A hotkey combination binding."""

    hotkey_str: str
    callback: Callable
    tap_key: int | None = None  # SharedHotkeyTap registration token, set at start time


@dataclass
class RemapEntry:
    """A key remap binding (source keycode → target keycode)."""

    source_name: str
    target_name: str
    source_vk: int
    target_vk: int
    is_modifier: bool  # True if source is a modifier key (FlagsChanged)
    mod_flag: int = 0  # CGEventFlags bitmask for modifier sources


@dataclass
class TimerEntry:
    """A registered timer."""

    timer_id: str
    interval: float
    callback: Callable
    repeating: bool
    _timer: Any = field(default=None, repr=False)  # async_loop.TimerHandle


class ScriptingRegistry:
    """Registration center for all scripting resources.

    Stores leaders, hotkeys, timers, and chooser sources registered by
    user scripts.
    """

    def __init__(self) -> None:
        self._leaders: dict[str, LeaderConfig] = {}
        self._hotkeys: list[HotkeyBinding] = []
        self._timers: dict[str, TimerEntry] = {}
        self._remaps: dict[int, RemapEntry] = {}  # source_vk → RemapEntry
        self._remap_listener: Any = None  # KeyRemapListener instance
        self._chooser_sources: dict[str, Any] = {}  # name → ChooserSource
        self._event_listeners: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()
        self._event_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="event-dispatch",
        )

    @property
    def leaders(self) -> dict[str, LeaderConfig]:
        return self._leaders

    @property
    def hotkeys(self) -> list[HotkeyBinding]:
        return self._hotkeys

    @property
    def timers(self) -> dict[str, TimerEntry]:
        return self._timers

    @property
    def remaps(self) -> dict[int, RemapEntry]:
        return self._remaps

    @property
    def remap_listener(self):
        return self._remap_listener

    @remap_listener.setter
    def remap_listener(self, value):
        self._remap_listener = value

    @property
    def chooser_sources(self) -> dict[str, Any]:
        return self._chooser_sources

    def register_leader(
        self,
        trigger_key: str,
        mappings: list[LeaderMapping],
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

    def unregister_hotkey(self, hotkey_str: str) -> list[HotkeyBinding]:
        """Remove a hotkey binding by its hotkey string.

        Returns the removed bindings so the caller can clean up the
        shared tap.
        """
        to_remove = [b for b in self._hotkeys if b.hotkey_str == hotkey_str]
        for binding in to_remove:
            self._hotkeys.remove(binding)
        if to_remove:
            logger.info("Unregistered hotkey: %s", hotkey_str)
        return to_remove

    def register_remap(self, entry: RemapEntry) -> None:
        """Register a key remap."""
        self._remaps[entry.source_vk] = entry
        logger.info("Registered remap: %s → %s", entry.source_name, entry.target_name)

    def unregister_remap(self, source_vk: int) -> RemapEntry | None:
        """Remove a key remap. Returns the removed entry or None."""
        entry = self._remaps.pop(source_vk, None)
        if entry:
            logger.info("Unregistered remap: %s → %s", entry.source_name, entry.target_name)
        return entry

    def register_timer(
        self, interval: float, callback: Callable, repeating: bool = False
    ) -> TimerEntry:
        """Register a timer. Returns the TimerEntry."""
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
        return entry

    def get_timer(self, timer_id: str) -> TimerEntry | None:
        """Thread-safe lookup of a timer entry."""
        with self._lock:
            return self._timers.get(timer_id)

    def pop_timer(self, timer_id: str) -> TimerEntry | None:
        """Atomically remove and return a timer entry without cancelling."""
        with self._lock:
            return self._timers.pop(timer_id, None)

    def cancel_timer(self, timer_id: str) -> None:
        """Cancel and remove a timer."""
        entry = self.pop_timer(timer_id)
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
        """Invoke all handlers for *event_name* via the thread pool."""
        handlers = list(self._event_listeners.get(event_name, []))
        if not handlers:
            return

        def _run_handler(handler):
            try:
                handler(kwargs)
            except Exception:
                logger.exception(
                    "Event handler error for %s", event_name
                )

        for handler in handlers:
            self._event_executor.submit(_run_handler, handler)

    def clear(self) -> None:
        """Stop all timers and clear all registrations."""
        with self._lock:
            for entry in self._timers.values():
                if entry._timer:
                    entry._timer.cancel()
            self._timers.clear()
        # Hotkey bindings only — HotkeyAPI owns the tap lifecycle.
        self._hotkeys.clear()
        self._leaders.clear()
        # Stop remap listener
        if self._remap_listener:
            try:
                self._remap_listener.stop()
            except Exception:
                pass
            self._remap_listener = None
        self._remaps.clear()
        self._chooser_sources.clear()
        self._event_listeners.clear()
        self._event_executor.shutdown(wait=False, cancel_futures=True)
        self._event_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="event-dispatch",
        )
        logger.info("Registry cleared")
