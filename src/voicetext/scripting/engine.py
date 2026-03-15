"""Script engine — plugin loading and lifecycle management."""

from __future__ import annotations

import logging
import os
from typing import Optional

from voicetext.scripting.registry import ScriptingRegistry

logger = logging.getLogger(__name__)


class ScriptEngine:
    """Load user scripts and manage the scripting lifecycle."""

    def __init__(self, script_dir: Optional[str] = None) -> None:
        self._script_dir = os.path.expanduser(
            script_dir or "~/.config/VoiceText/scripts"
        )
        self._registry = ScriptingRegistry()

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
        """Load scripts and start all listeners."""
        self._load_scripts()
        # Start hotkey/leader listeners after scripts register their bindings
        self._vt.hotkey.start()
        logger.info("Script engine started (script_dir=%s)", self._script_dir)

    def stop(self) -> None:
        """Stop all listeners and clean up."""
        self._vt.hotkey.stop()
        self._registry.clear()
        logger.info("Script engine stopped")

    def reload(self) -> None:
        """Reload all scripts: stop, clear, re-load, start."""
        logger.info("Reloading scripts...")
        self.stop()
        # Reset hotkey API so it creates a fresh listener
        self._vt._hotkey_api = None
        self._load_scripts()
        self._vt.hotkey.start()
        logger.info("Scripts reloaded")

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
