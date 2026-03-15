"""vt namespace — the public API for user scripts."""

from __future__ import annotations

import logging
import time
from typing import Callable, List, Optional

from voicetext.scripting.registry import LeaderMapping, ScriptingRegistry

from .alert import alert as _alert_fn
from .app import AppAPI
from .eventtap import keystroke as _keystroke_fn
from .execute import execute as _execute_fn
from .notify import notify as _notify_fn
from .pasteboard import PasteboardAPI
from .timer import TimerAPI

logger = logging.getLogger(__name__)


class _VTNamespace:
    """The 'vt' namespace object exposed to user scripts.

    Aggregates all API modules into a single convenient namespace.
    """

    def __init__(self, registry: ScriptingRegistry) -> None:
        self._registry = registry
        self.app = AppAPI()
        self.pasteboard = PasteboardAPI()
        self.timer = TimerAPI(registry)
        # HotkeyAPI is created lazily to avoid circular imports
        self._hotkey_api = None
        self._reload_callback: Optional[Callable] = None

    @property
    def hotkey(self):
        """Access the hotkey API (lazy init)."""
        if self._hotkey_api is None:
            from .hotkey import HotkeyAPI

            self._hotkey_api = HotkeyAPI(self._registry)
        return self._hotkey_api

    def leader(self, trigger_key: str, mappings: List[dict]) -> None:
        """Register a leader-key configuration.

        Args:
            trigger_key: The trigger key name (e.g. "cmd_r", "alt_r").
            mappings: List of dicts, each with "key" and one of
                      "app", "func", "exec", plus optional "desc".

        Example::

            vt.leader("cmd_r", [
                {"key": "w", "app": "WeChat"},
                {"key": "d", "desc": "date", "func": lambda: vt.notify("hi")},
                {"key": "i", "exec": "/usr/local/bin/code ~/work"},
            ])
        """
        parsed = []
        for m in mappings:
            parsed.append(
                LeaderMapping(
                    key=m["key"],
                    desc=m.get("desc", ""),
                    app=m.get("app"),
                    func=m.get("func"),
                    exec_cmd=m.get("exec"),
                )
            )
        self._registry.register_leader(trigger_key, parsed)

    def alert(self, text: str, duration: float = 2.0) -> None:
        """Show a brief floating alert message."""
        _alert_fn(text, duration)

    def notify(self, title: str, message: str = "") -> None:
        """Send a macOS notification."""
        _notify_fn(title, message)

    def keystroke(self, key: str, modifiers: list[str] | None = None) -> None:
        """Synthesize a keystroke."""
        _keystroke_fn(key, modifiers)

    def execute(self, command: str, background: bool = True) -> str | None:
        """Execute a shell command."""
        return _execute_fn(command, background)

    def date(self, fmt: str = "%Y-%m-%d") -> str:
        """Return formatted current date/time."""
        return time.strftime(fmt)

    def reload(self) -> None:
        """Reload all scripts."""
        if self._reload_callback:
            self._reload_callback()
        else:
            logger.warning("Reload not available (engine not set)")


# Module-level singleton — created and set by ScriptEngine
vt: Optional[_VTNamespace] = None
