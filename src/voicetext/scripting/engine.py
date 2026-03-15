"""Script engine — plugin loading and lifecycle management."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from voicetext.scripting.registry import ScriptingRegistry

logger = logging.getLogger(__name__)


class ScriptEngine:
    """Load user scripts and manage the scripting lifecycle."""

    def __init__(
        self,
        script_dir: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._script_dir = os.path.expanduser(
            script_dir or "~/.config/VoiceText/scripts"
        )
        self._config = config or {}
        self._registry = ScriptingRegistry()
        self._clipboard_monitor = None

        # Create vt namespace and install as module singleton
        from voicetext.scripting.api import _VTNamespace
        import voicetext.scripting.api as api_mod

        self._vt = _VTNamespace(self._registry)
        self._vt._reload_callback = self.reload
        api_mod.vt = self._vt

    @property
    def vt(self):
        """The vt namespace object."""
        return self._vt

    def start(self) -> None:
        """Load scripts, register built-in sources, and start all listeners."""
        self._register_builtin_sources()
        self._load_scripts()
        self._bind_chooser_hotkey()
        # Start hotkey/leader listeners after scripts register their bindings
        self._vt.hotkey.start()
        logger.info("Script engine started (script_dir=%s)", self._script_dir)

    def stop(self) -> None:
        """Stop all listeners and clean up."""
        self._vt.hotkey.stop()
        if self._clipboard_monitor is not None:
            self._clipboard_monitor.stop()
            self._clipboard_monitor = None
        self._registry.clear()
        logger.info("Script engine stopped")

    def reload(self) -> None:
        """Reload all scripts: stop, clear, re-load, start."""
        logger.info("Reloading scripts...")
        self.stop()
        # Reset APIs so they create fresh instances
        self._vt._hotkey_api = None
        self._vt._chooser_api = None
        self._register_builtin_sources()
        self._load_scripts()
        self._bind_chooser_hotkey()
        self._vt.hotkey.start()
        logger.info("Scripts reloaded")

    def _register_builtin_sources(self) -> None:
        """Register built-in chooser sources (apps, clipboard)."""
        chooser_config = self._config.get("chooser", {})

        # App search source
        if chooser_config.get("app_search", True):
            try:
                from voicetext.scripting.sources.app_source import AppSource

                app_source = AppSource()
                self._vt.chooser.register_source(app_source.as_chooser_source())
                logger.info("Built-in app search source registered")
            except Exception:
                logger.exception("Failed to register app search source")

        # Clipboard history source
        if chooser_config.get("clipboard_history", True):
            try:
                from voicetext.scripting.clipboard_monitor import ClipboardMonitor
                from voicetext.scripting.sources.clipboard_source import ClipboardSource

                max_items = chooser_config.get("clipboard_max_items", 50)
                persist_path = os.path.expanduser(
                    "~/.config/VoiceText/clipboard_history.json"
                )

                self._clipboard_monitor = ClipboardMonitor(
                    max_items=max_items,
                    persist_path=persist_path,
                )
                self._clipboard_monitor.start()

                cb_source = ClipboardSource(self._clipboard_monitor)
                self._vt.chooser.register_source(cb_source.as_chooser_source())
                logger.info("Built-in clipboard source registered")
            except Exception:
                logger.exception("Failed to register clipboard source")

    def _bind_chooser_hotkey(self) -> None:
        """Bind the chooser toggle hotkey from config."""
        chooser_config = self._config.get("chooser", {})
        hotkey_str = chooser_config.get("hotkey")
        if hotkey_str:
            self._vt.hotkey.bind(hotkey_str, lambda: self._vt.chooser.toggle())
            logger.info("Chooser hotkey bound: %s", hotkey_str)

    def _load_scripts(self) -> None:
        """Execute init.py in the scripts directory."""
        init_path = os.path.join(self._script_dir, "init.py")

        if not os.path.isfile(init_path):
            logger.info("No init.py found at %s, skipping", init_path)
            return

        logger.info("Loading script: %s", init_path)
        script_globals = {
            "vt": self._vt,
            "__builtins__": __builtins__,
            "__file__": init_path,
            "__name__": "__vt_script__",
        }

        try:
            with open(init_path, "r", encoding="utf-8") as f:
                code = f.read()
            exec(compile(code, init_path, "exec"), script_globals)  # noqa: S102
            logger.info("Script loaded successfully: %s", init_path)
        except Exception as exc:
            logger.error("Failed to load script %s: %s", init_path, exc, exc_info=True)
            # Show alert to user
            try:
                from voicetext.scripting.api.alert import alert

                alert(f"Script error: {exc}", duration=5.0)
            except Exception:
                pass
