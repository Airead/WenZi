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
        self._usage_tracker = None
        self._snippet_store = None
        self._snippet_expander = None

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
        self._bind_source_hotkeys()
        # Start hotkey/leader listeners after scripts register their bindings
        self._vt.hotkey.start()
        logger.info("Script engine started (script_dir=%s)", self._script_dir)

    def stop(self) -> None:
        """Stop all listeners and clean up."""
        self._vt.hotkey.stop()
        if self._clipboard_monitor is not None:
            self._clipboard_monitor.stop()
            self._clipboard_monitor = None
        if self._snippet_expander is not None:
            self._snippet_expander.stop()
            self._snippet_expander = None
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
        self._bind_source_hotkeys()
        self._vt.hotkey.start()
        logger.info("Scripts reloaded")

    def _register_builtin_sources(self) -> None:
        """Register built-in chooser sources."""
        chooser_config = self._config.get("chooser", {})
        if not chooser_config.get("enabled", True):
            logger.info("Chooser disabled via config, skipping source registration")
            return

        # Usage learning tracker
        if chooser_config.get("usage_learning", True):
            try:
                from voicetext.scripting.sources.usage_tracker import UsageTracker

                self._usage_tracker = UsageTracker()
                # Inject tracker into the chooser panel
                panel = self._vt.chooser._get_panel()
                panel._usage_tracker = self._usage_tracker
                logger.info("Usage learning tracker enabled")
            except Exception:
                logger.exception("Failed to set up usage tracker")

        prefixes = chooser_config.get("prefixes", {})

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
                from voicetext.scripting.sources.clipboard_source import (
                    ClipboardSource,
                )

                max_days = chooser_config.get("clipboard_max_days", 7)
                persist_path = os.path.expanduser(
                    "~/.config/VoiceText/clipboard_history.json"
                )

                self._clipboard_monitor = ClipboardMonitor(
                    max_days=max_days,
                    persist_path=persist_path,
                )
                self._clipboard_monitor.start()

                cb_source = ClipboardSource(self._clipboard_monitor)
                self._vt.chooser.register_source(
                    cb_source.as_chooser_source(
                        prefix=prefixes.get("clipboard", "cb"),
                    )
                )
                logger.info("Built-in clipboard source registered")
            except Exception:
                logger.exception("Failed to register clipboard source")

        # File search source
        if chooser_config.get("file_search", True):
            try:
                from voicetext.scripting.sources.file_source import FileSource

                file_source = FileSource()
                self._vt.chooser.register_source(
                    file_source.as_chooser_source(
                        prefix=prefixes.get("files", "f"),
                    )
                )
                logger.info("Built-in file search source registered")
            except Exception:
                logger.exception("Failed to register file search source")

        # Snippet source
        if chooser_config.get("snippets", True):
            try:
                from voicetext.scripting.sources.snippet_source import (
                    SnippetSource,
                    SnippetStore,
                )

                self._snippet_store = SnippetStore()
                snippet_source = SnippetSource(self._snippet_store)
                self._vt.chooser.register_source(
                    snippet_source.as_chooser_source(
                        prefix=prefixes.get("snippets", "sn"),
                    )
                )
                logger.info("Built-in snippet source registered")
            except Exception:
                logger.exception("Failed to register snippet source")

        # Snippet keyword auto-expansion
        if chooser_config.get("snippet_expansion", True) and self._snippet_store:
            try:
                from voicetext.scripting.snippet_expander import SnippetExpander

                self._snippet_expander = SnippetExpander(self._snippet_store)
                self._snippet_expander.start()
                logger.info("Snippet keyword expander started")
            except Exception:
                logger.exception("Failed to start snippet expander")

        # Bookmark search source
        if chooser_config.get("bookmarks", True):
            try:
                from voicetext.scripting.sources.bookmark_source import (
                    BookmarkSource,
                )

                bookmark_source = BookmarkSource()
                self._vt.chooser.register_source(
                    bookmark_source.as_chooser_source(
                        prefix=prefixes.get("bookmarks", "bm"),
                    )
                )
                logger.info("Built-in bookmark source registered")
            except Exception:
                logger.exception("Failed to register bookmark source")

    def _bind_chooser_hotkey(self) -> None:
        """Bind the chooser toggle hotkey from config."""
        chooser_config = self._config.get("chooser", {})
        if not chooser_config.get("enabled", True):
            return
        hotkey_str = chooser_config.get("hotkey")
        if hotkey_str:
            self._vt.hotkey.bind(hotkey_str, lambda: self._vt.chooser.toggle())
            logger.info("Chooser hotkey bound: %s", hotkey_str)

    def _bind_source_hotkeys(self) -> None:
        """Bind per-source direct hotkeys from config."""
        chooser_config = self._config.get("chooser", {})
        if not chooser_config.get("enabled", True):
            return
        source_hotkeys = chooser_config.get("source_hotkeys", {})
        prefixes = chooser_config.get("prefixes", {})
        for source_key, hotkey_str in source_hotkeys.items():
            if hotkey_str:
                prefix = prefixes.get(source_key, "")
                if not prefix:
                    continue
                self._vt.hotkey.bind(
                    hotkey_str,
                    lambda p=prefix: self._vt.chooser.show_source(p),
                )
                logger.info(
                    "Source hotkey bound: %s -> %s", hotkey_str, source_key,
                )

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
