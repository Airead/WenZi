"""wz.hotkey — hotkey binding and leader-key system."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from wenzi.scripting.api._async_util import wrap_async
from wenzi.scripting.registry import LeaderConfig, LeaderMapping, RemapEntry, ScriptingRegistry
from wenzi.scripting.ui.leader_alert import LeaderAlertPanel

logger = logging.getLogger(__name__)


class HotkeyAPI:
    """Hotkey binding and leader-key management."""

    def __init__(self, registry: ScriptingRegistry) -> None:
        self._registry = registry
        self._leader_alert = LeaderAlertPanel()
        self._listener = None  # _QuartzAllKeysListener
        self._shared_tap = None  # SharedHotkeyTap
        self._active_leader: LeaderConfig | None = None
        self._leader_triggered: bool = False
        self._sticky_leader: bool = False
        self._click_monitor = None
        self._lock = threading.Lock()
        self._started = False

    def define_key(self, name: str, keycode: int) -> None:
        """Define a custom key mapping for use in hotkeys and leader keys."""
        from wenzi.hotkey import register_custom_key

        register_custom_key(name, keycode)

    def define_keys(self, mapping: dict[str, int]) -> None:
        """Define multiple custom key mappings at once."""
        for name, keycode in mapping.items():
            self.define_key(name, keycode)

    def bind(self, hotkey_str: str, callback: Callable) -> None:
        """Bind a hotkey combination (e.g. "ctrl+cmd+v").

        *callback* may be a regular function or an ``async def``.
        Async callbacks are automatically submitted to the background
        event loop.
        """
        self._registry.register_hotkey(hotkey_str, wrap_async(callback))
        if self._started:
            self._start_hotkey_listeners()

    def unbind(self, hotkey_str: str) -> None:
        """Remove and stop a hotkey binding."""
        removed = self._registry.unregister_hotkey(hotkey_str)
        if self._shared_tap:
            for binding in removed:
                if binding.tap_key is not None:
                    self._shared_tap.remove(binding.tap_key)

    def remap(self, source: str, target: str) -> None:
        """Remap one key to another.

        Supports modifier keys (e.g. ``"shift_r"``) and regular keys.
        The original key is swallowed and the target key is synthesized.

        Example::

            wz.hotkey.remap("shift_r", "f19")
        """
        from wenzi.hotkey import _MOD_VK, _name_to_vk

        source_lower = source.strip().lower()
        target_lower = target.strip().lower()
        source_vk = _name_to_vk(source_lower)
        target_vk = _name_to_vk(target_lower)
        is_modifier = source_lower in _MOD_VK
        mod_flag = _MOD_VK[source_lower][1] if is_modifier else 0

        entry = RemapEntry(
            source_name=source_lower,
            target_name=target_lower,
            source_vk=source_vk,
            target_vk=target_vk,
            is_modifier=is_modifier,
            mod_flag=mod_flag,
        )
        self._registry.register_remap(entry)
        if self._started:
            self._start_remap_listener(entry)

    def unremap(self, source: str) -> None:
        """Remove a key remap."""
        from wenzi.hotkey import _name_to_vk

        source_vk = _name_to_vk(source.strip().lower())
        entry = self._registry.unregister_remap(source_vk)
        if entry and self._registry.remap_listener:
            self._registry.remap_listener.remove(source_vk)

    def toggle_leader(self, trigger_key: str) -> None:
        """Toggle leader mode for *trigger_key* (sticky — stays open until dismissed).

        If the same leader is already active in sticky mode, close it.
        Otherwise, show the leader panel and enter sticky mode where the
        panel remains visible until a sub-key, Esc, or mouse click dismisses it.
        """
        with self._lock:
            if (
                self._active_leader is not None
                and self._sticky_leader
                and self._active_leader.trigger_key == trigger_key
            ):
                self._active_leader = None
                self._sticky_leader = False
                self._leader_triggered = False
                try:
                    from PyObjCTools import AppHelper

                    AppHelper.callAfter(self._close_leader_ui)
                except Exception:
                    pass
                return

            if trigger_key not in self._registry.leaders:
                logger.warning("toggle_leader: no leader registered for %s", trigger_key)
                return

            self._active_leader = self._registry.leaders[trigger_key]
            self._leader_triggered = False
            self._sticky_leader = True

        try:
            from PyObjCTools import AppHelper

            leader = self._active_leader
            AppHelper.callAfter(self._show_sticky_leader, leader)
        except Exception:
            pass

    def start(self) -> None:
        """Start all hotkey and leader-key listeners."""
        self._started = True
        self._start_leader_listener()
        self._start_hotkey_listeners()
        self._start_remap_listener()

    def stop(self) -> None:
        """Stop all listeners."""
        self._started = False
        if self._listener:
            self._listener.stop()
            self._listener = None
        if self._shared_tap:
            self._shared_tap.stop()
            self._shared_tap = None
        for binding in self._registry.hotkeys:
            binding.tap_key = None
        # Stop remap listener
        if self._registry.remap_listener:
            self._registry.remap_listener.stop()
            self._registry.remap_listener = None
        with self._lock:
            self._active_leader = None
            self._leader_triggered = False
            self._sticky_leader = False
        # Close leader alert panel and stop click monitor
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self._close_leader_ui)
        except Exception:
            pass
        logger.info("Hotkey API stopped")

    def _start_leader_listener(self) -> None:
        """Start the CGEventTap for leader-key detection."""
        if not self._registry.leaders:
            return

        from wenzi.hotkey import _QuartzAllKeysListener

        self._listener = _QuartzAllKeysListener(
            on_press=self._on_press,
            on_release=self._on_release,
            listen_only=False,
        )
        self._listener.start()
        logger.info(
            "Leader listener started for keys: %s",
            list(self._registry.leaders.keys()),
        )

    def _start_hotkey_listeners(self) -> None:
        """Register all hotkeys on a single shared CGEventTap."""
        if not self._registry.hotkeys:
            return

        from wenzi.hotkey import SharedHotkeyTap

        if self._shared_tap is None:
            self._shared_tap = SharedHotkeyTap()

        for binding in self._registry.hotkeys:
            if binding.tap_key is not None:
                continue
            try:
                binding.tap_key = self._shared_tap.add(
                    binding.hotkey_str, binding.callback,
                )
            except Exception as exc:
                logger.error("Failed to add hotkey %s: %s", binding.hotkey_str, exc)

    def _start_remap_listener(self, new_entry: RemapEntry | None = None) -> None:
        """Start (or update) the shared KeyRemapListener for registered remaps.

        When *new_entry* is given only that single remap is added to the
        listener.  On initial startup (``new_entry is None``) all registered
        remaps are synced.
        """
        if not self._registry.remaps:
            return

        from wenzi.hotkey import KeyRemapListener

        listener = self._registry.remap_listener
        if listener is None:
            listener = KeyRemapListener()
            self._registry.remap_listener = listener

        if new_entry is not None:
            listener.add(new_entry.source_vk, new_entry.target_vk,
                         new_entry.is_modifier, new_entry.mod_flag)
        else:
            for entry in self._registry.remaps.values():
                listener.add(entry.source_vk, entry.target_vk,
                             entry.is_modifier, entry.mod_flag)

        # Start if not already running
        if not listener.is_running():
            listener.start()

    # ------------------------------------------------------------------
    # Sticky leader helpers (main-thread only)
    # ------------------------------------------------------------------

    def _show_sticky_leader(self, leader: LeaderConfig) -> None:
        self._leader_alert.show(leader.trigger_key, leader.mappings, leader.position)
        self._start_click_monitor()

    def _close_leader_ui(self) -> None:
        self._leader_alert.close()
        self._stop_click_monitor()

    def _start_click_monitor(self) -> None:
        self._stop_click_monitor()
        from AppKit import NSEvent

        self._click_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            (1 << 1) | (1 << 3),  # NSLeftMouseDownMask | NSRightMouseDownMask
            lambda _event: self._on_global_click(),
        )

    def _stop_click_monitor(self) -> None:
        if self._click_monitor is not None:
            from AppKit import NSEvent

            NSEvent.removeMonitor_(self._click_monitor)
            self._click_monitor = None

    def _on_global_click(self) -> None:
        with self._lock:
            if not self._sticky_leader:
                return
            self._active_leader = None
            self._sticky_leader = False
            self._leader_triggered = False
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._close_leader_ui)

    def _on_press(self, name: str) -> bool:
        """Handle key press. Returns True to swallow the event."""
        with self._lock:
            if self._active_leader is not None:
                # --- Sticky mode special handling ---
                if self._sticky_leader:
                    # Esc or Ctrl+C closes the leader
                    _dismiss = name.lower() == "esc"
                    if not _dismiss and name.lower() == "c":
                        try:
                            import Quartz

                            flags = Quartz.CGEventSourceFlagsState(
                                Quartz.kCGEventSourceStateCombinedSessionState
                            )
                            _dismiss = bool(flags & (1 << 18))  # Ctrl
                        except Exception:
                            pass
                    if _dismiss:
                        self._active_leader = None
                        self._sticky_leader = False
                        self._leader_triggered = False
                        try:
                            from PyObjCTools import AppHelper

                            AppHelper.callAfter(self._close_leader_ui)
                        except Exception:
                            pass
                        return True

                    # Don't match sub-keys when modifier keys are held
                    # (e.g. user pressing Cmd+D again to toggle-close)
                    try:
                        import Quartz

                        flags = Quartz.CGEventSourceFlagsState(
                            Quartz.kCGEventSourceStateCombinedSessionState
                        )
                        _MOD_MASK = (1 << 20) | (1 << 18) | (1 << 19)  # Cmd|Ctrl|Alt
                        if flags & _MOD_MASK:
                            return True
                    except Exception:
                        pass

                # Leader mode active — check for sub-key match
                leader = self._active_leader
                for m in leader.mappings:
                    if m.key.lower() == name.lower():
                        self._leader_triggered = True
                        # Sticky mode: close panel on sub-key press
                        if self._sticky_leader:
                            self._active_leader = None
                            self._sticky_leader = False
                            try:
                                from PyObjCTools import AppHelper

                                AppHelper.callAfter(self._close_leader_ui)
                            except Exception:
                                pass
                        threading.Thread(
                            target=self._execute_mapping, args=(m,), daemon=True
                        ).start()
                        return True  # Swallow the sub-key
                # Non-matching key during leader mode — still swallow
                return True

            # Check if this is a leader trigger key
            if name in self._registry.leaders:
                self._active_leader = self._registry.leaders[name]
                self._leader_triggered = False
                # Show alert on main thread
                try:
                    from PyObjCTools import AppHelper

                    leader = self._active_leader
                    AppHelper.callAfter(
                        self._leader_alert.show,
                        leader.trigger_key,
                        leader.mappings,
                        leader.position,
                    )
                except Exception:
                    pass
                # Swallow non-modifier trigger keys to prevent input;
                # modifier keys (FlagsChanged) should not be swallowed.
                from wenzi.hotkey import _is_modifier_like_vk, _name_to_vk

                return not _is_modifier_like_vk(_name_to_vk(name))

        return False

    def _on_release(self, name: str) -> None:
        """Handle key release."""
        with self._lock:
            if self._active_leader and name == self._active_leader.trigger_key:
                # Sticky mode: don't close on trigger key release
                if self._sticky_leader:
                    return
                self._active_leader = None
                self._leader_triggered = False
                # Always close alert when trigger key is released
                try:
                    from PyObjCTools import AppHelper

                    AppHelper.callAfter(self._leader_alert.close)
                except Exception:
                    pass

    def _execute_mapping(self, mapping: LeaderMapping) -> None:
        """Execute a leader mapping action in a background thread."""
        try:
            if mapping.app:
                from wenzi.scripting.api.app import AppAPI

                api = AppAPI()
                if not api.launch(mapping.app):
                    logger.warning("Failed to launch: %s", mapping.app)
            elif mapping.func:
                mapping.func()
            elif mapping.exec_cmd:
                from wenzi.scripting.api.execute import execute

                execute(mapping.exec_cmd, background=False)
        except Exception as exc:
            logger.error(
                "Leader mapping execution error (key=%s): %s", mapping.key, exc
            )
