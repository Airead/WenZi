"""wz namespace — the public API for user scripts."""

from __future__ import annotations

import logging
import time
from typing import Callable, List, Optional

from wenzi.scripting.registry import LeaderMapping, ScriptingRegistry

from .alert import alert as _alert_fn
from .app import AppAPI
from .eventtap import keystroke as _keystroke_fn
from .execute import execute as _execute_fn
from .notify import notify as _notify_fn
from .pasteboard import PasteboardAPI
from .snippets import SnippetsAPI
from .store import StoreAPI
from .timer import TimerAPI

logger = logging.getLogger(__name__)


class _WZNamespace:
    """The 'wz' namespace object exposed to user scripts.

    Aggregates all API modules into a single convenient namespace.
    """

    def __init__(self, registry: ScriptingRegistry) -> None:
        self._registry = registry
        self.app = AppAPI()
        self.pasteboard = PasteboardAPI()
        self.snippets = SnippetsAPI()
        self.timer = TimerAPI(registry)
        self.store = StoreAPI()
        # HotkeyAPI and ChooserAPI are created lazily to avoid circular imports
        self._hotkey_api = None
        self._chooser_api = None
        self._reload_callback: Optional[Callable] = None

    @property
    def hotkey(self):
        """Access the hotkey API (lazy init)."""
        if self._hotkey_api is None:
            from .hotkey import HotkeyAPI

            self._hotkey_api = HotkeyAPI(self._registry)
        return self._hotkey_api

    @property
    def chooser(self):
        """Access the chooser API (lazy init)."""
        if self._chooser_api is None:
            from .chooser import ChooserAPI

            self._chooser_api = ChooserAPI()
        return self._chooser_api

    def leader(
        self,
        trigger_key: str,
        mappings: List[dict],
        position: str | tuple = "center",
    ) -> None:
        """Register a leader-key configuration.

        Args:
            trigger_key: The trigger key name (e.g. "cmd_r", "alt_r").
            mappings: List of dicts, each with "key" and one of
                      "app", "func", "exec", plus optional "desc".
            position: Panel position — "center", "top", "bottom",
                      "mouse", or a tuple ``(x%, y%)`` in screen
                      percentage (origin bottom-left).

        Example::

            wz.leader("cmd_r", [
                {"key": "w", "app": "WeChat"},
                {"key": "d", "desc": "date", "func": lambda: wz.notify("hi")},
                {"key": "i", "exec": "/usr/local/bin/code ~/work"},
            ], position="mouse")
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
        self._registry.register_leader(trigger_key, parsed, position=position)

    def on(
        self, event_name: str, callback: Optional[Callable] = None
    ) -> Callable:
        """Register a global event listener.

        Supported events: ``recording_start``, ``recording_stop``,
        ``transcription_done``, ``enhancement_done``, ``output_text``.

        Can be used as a decorator::

            @wz.on("transcription_done")
            def on_transcribe(data):
                print(data["asr_text"])

        Or called directly::

            wz.on("recording_start", my_handler)
        """
        if callback is not None:
            self._registry.register_event(event_name, callback)
            return callback

        def decorator(func: Callable) -> Callable:
            self._registry.register_event(event_name, func)
            return func

        return decorator

    def alert(self, text: str, duration: float = 2.0) -> None:
        """Show a brief floating alert message."""
        _alert_fn(text, duration)

    def notify(self, title: str, message: str = "") -> None:
        """Send a macOS notification."""
        _notify_fn(title, message)

    def keystroke(self, key: str, modifiers: list[str] | None = None) -> None:
        """Synthesize a keystroke."""
        _keystroke_fn(key, modifiers)

    def execute(
        self,
        command: str,
        background: bool = True,
        timeout: int = 30,
        on_done: Optional[Callable] = None,
    ) -> dict | None:
        """Execute a shell command.

        Returns a dict with ``stdout``, ``stderr``, ``returncode`` when
        *background* is False.  Returns None when *background* is True.
        """
        return _execute_fn(
            command, background=background, timeout=timeout, on_done=on_done
        )

    def type_text(self, text: str, method: str = "auto") -> None:
        """Type text into the currently focused application.

        Args:
            text: The text to type.
            method: ``"auto"``, ``"paste"`` (clipboard), or ``"key"``
                    (AppleScript keystroke).
        """
        _METHOD_MAP = {"paste": "clipboard", "key": "applescript"}
        mapped = _METHOD_MAP.get(method, method)
        from wenzi.input import type_text as _type_text

        _type_text(text, method=mapped)

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
wz: Optional[_WZNamespace] = None
