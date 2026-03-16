"""wz.hotkey — hotkey binding and leader-key system."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from wenzi.scripting.registry import LeaderConfig, LeaderMapping, ScriptingRegistry
from wenzi.scripting.ui.leader_alert import LeaderAlertPanel

logger = logging.getLogger(__name__)


class HotkeyAPI:
    """Hotkey binding and leader-key management."""

    def __init__(self, registry: ScriptingRegistry) -> None:
        self._registry = registry
        self._leader_alert = LeaderAlertPanel()
        self._listener = None  # _QuartzAllKeysListener
        self._active_leader: Optional[LeaderConfig] = None
        self._leader_triggered: bool = False
        self._lock = threading.Lock()

    def bind(self, hotkey_str: str, callback: Callable) -> None:
        """Bind a hotkey combination (e.g. "ctrl+cmd+v")."""
        self._registry.register_hotkey(hotkey_str, callback)

    def unbind(self, hotkey_str: str) -> None:
        """Remove and stop a hotkey binding."""
        self._registry.unregister_hotkey(hotkey_str)

    def start(self) -> None:
        """Start all hotkey and leader-key listeners."""
        self._start_leader_listener()
        self._start_hotkey_listeners()

    def stop(self) -> None:
        """Stop all listeners."""
        if self._listener:
            self._listener.stop()
            self._listener = None
        for binding in self._registry.hotkeys:
            if binding.listener:
                binding.listener.stop()
                binding.listener = None
        with self._lock:
            self._active_leader = None
            self._leader_triggered = False
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
        """Start individual TapHotkeyListener for each registered hotkey."""
        from wenzi.hotkey import TapHotkeyListener

        for binding in self._registry.hotkeys:
            if binding.listener is not None:
                continue
            try:
                listener = TapHotkeyListener(
                    hotkey_str=binding.hotkey_str,
                    on_activate=binding.callback,
                )
                listener.start()
                binding.listener = listener
            except Exception as exc:
                logger.error("Failed to start hotkey %s: %s", binding.hotkey_str, exc)

    def _on_press(self, name: str) -> bool:
        """Handle key press. Returns True to swallow the event."""
        with self._lock:
            if self._active_leader is not None:
                # Leader mode active — check for sub-key match
                leader = self._active_leader
                for m in leader.mappings:
                    if m.key.lower() == name.lower():
                        self._leader_triggered = True
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
                return False  # Don't swallow the modifier FlagsChanged

        return False

    def _on_release(self, name: str) -> None:
        """Handle key release."""
        with self._lock:
            if self._active_leader and name == self._active_leader.trigger_key:
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
