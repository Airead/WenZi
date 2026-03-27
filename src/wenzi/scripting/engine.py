"""Script engine — plugin loading and lifecycle management."""

from __future__ import annotations

import importlib
import logging
import os
import shutil
import sys
import traceback as _tb
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

import wenzi.config as _cfg
from wenzi.scripting.registry import ScriptingRegistry

if TYPE_CHECKING:
    from wenzi.scripting.plugin_meta import PluginMeta

logger = logging.getLogger(__name__)


class ScriptEngine:
    """Load user scripts and manage the scripting lifecycle."""

    def __init__(
        self,
        script_dir: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        plugins_dir: Optional[str] = None,
    ) -> None:
        self._script_dir = os.path.expanduser(
            script_dir or _cfg.DEFAULT_SCRIPTS_DIR
        )
        self._config = config or {}
        self._plugins_dir = os.path.expanduser(
            plugins_dir or _cfg.DEFAULT_PLUGINS_DIR
        )
        self._registry = ScriptingRegistry()
        self._clipboard_monitor = None
        self._usage_tracker = None
        self._query_history = None
        self._snippet_store = None
        self._snippet_source = None
        self._snippet_expander = None
        self._system_settings_source = None
        self._system_settings_open_cb: Optional[Callable[[], None]] = None
        self._reloading = False
        self._plugin_metas: Dict[str, PluginMeta] = {}
        self._plugin_load_errors: Dict[str, Dict[str, str]] = {}

        # Create wz namespace and install as module singleton
        from wenzi.scripting.api import _WZNamespace
        import wenzi.scripting.api as api_mod

        self._wz = _WZNamespace(self._registry)
        self._wz._reload_callback = self.reload
        api_mod.wz = self._wz

    @property
    def wz(self):
        """The wz namespace object."""
        return self._wz

    @staticmethod
    def _try_alert(message: str, duration: float = 2.0) -> None:
        """Show a floating alert, silently ignoring failures."""
        try:
            from wenzi.scripting.api.alert import alert

            alert(message, duration=duration)
        except Exception:
            logger.debug("Alert failed", exc_info=True)

    def start(self) -> None:
        """Load scripts, register built-in sources, and start all listeners."""
        self._register_builtin_sources()
        self._load_plugins()
        self._load_scripts()
        self._bind_chooser_hotkey()
        self._bind_source_hotkeys()
        self._bind_new_snippet_hotkey()
        # Start hotkey/leader listeners after scripts register their bindings
        self._wz.hotkey.start()
        logger.info("Script engine started (script_dir=%s)", self._script_dir)
        if self._plugin_load_errors:
            n = len(self._plugin_load_errors)
            self._try_alert(
                f"{n} plugin(s) failed to load. Check Settings → Plugins.",
                duration=3.0,
            )

    def stop(self) -> None:
        """Stop all listeners and clean up."""
        self._wz.hotkey.stop()
        if self._clipboard_monitor is not None:
            self._clipboard_monitor.stop()
            self._clipboard_monitor = None
        if self._snippet_expander is not None:
            self._snippet_expander.stop()
            self._snippet_expander = None
        if self._system_settings_source is not None:
            self._wz.chooser.unregister_source("system_settings")
            self._wz.chooser.unregister_source("system_settings_mixed")
            self._system_settings_source = None
        if self._query_history is not None:
            self._query_history.flush_sync()
        self._wz.pasteboard._set_monitor(None)
        self._wz.snippets._set_store(None)
        if self._wz._keychain_api is not None:
            self._wz._keychain_api.flush_sync()
        self._wz.store.flush_sync()
        self._registry.clear()
        logger.info("Script engine stopped")

    def reload(self) -> None:
        """Reload all scripts: stop, clear, re-load, start."""
        if self._reloading:
            logger.debug("Reload already in progress, skipping")
            return
        self._reloading = True
        try:
            logger.info("Reloading scripts...")
            # Cancel async tasks from the previous script cycle
            from wenzi.scripting.api._async_util import get_tracker

            get_tracker().cancel_all()
            self.stop()
            self._purge_user_modules()
            # Restore built-in key maps before scripts re-register custom keys
            from wenzi.hotkey import unregister_custom_keys

            unregister_custom_keys()
            # Reset APIs so they create fresh instances
            self._wz._hotkey_api = None
            self._wz._chooser_api = None
            self._wz._ui_api = None
            if self._wz._menubar_api is not None:
                self._wz._menubar_api.cleanup()
                self._wz._menubar_api = None
            self._register_builtin_sources()
            self._load_plugins()
            self._load_scripts()
            self._bind_chooser_hotkey()
            self._bind_source_hotkeys()
            self._bind_new_snippet_hotkey()
            self._wz.hotkey.start()
            logger.info("Scripts reloaded")
            if self._plugin_load_errors:
                n = len(self._plugin_load_errors)
                self._try_alert(
                    f"Scripts reloaded. {n} plugin(s) failed to load.",
                    duration=3.0,
                )
            else:
                self._try_alert("Scripts reloaded", duration=1.5)
        finally:
            self._reloading = False

    # ── Runtime chooser on/off ─────────────────────────────────────

    def enable_chooser(self) -> None:
        """Enable the chooser at runtime: register sources, bind hotkeys."""
        self._register_builtin_sources()
        # Re-register the built-in command source (cleared by disable_chooser)
        self._wz.chooser._ensure_command_source()
        self._bind_chooser_hotkey()
        self._bind_source_hotkeys()
        self._bind_new_snippet_hotkey()
        self._wz.hotkey.start()
        logger.info("Chooser enabled at runtime")

    def disable_chooser(self) -> None:
        """Disable the chooser at runtime: unbind hotkeys, stop monitors, clear sources."""
        chooser_config = self._config.get("chooser", {})

        # Unbind chooser hotkey
        hotkey_str = chooser_config.get("hotkey")
        if hotkey_str:
            self._wz.hotkey.unbind(hotkey_str)

        # Unbind source hotkeys
        source_hotkeys = chooser_config.get("source_hotkeys", {})
        for hotkey_str in source_hotkeys.values():
            if hotkey_str:
                self._wz.hotkey.unbind(hotkey_str)

        # Stop clipboard monitor
        if self._clipboard_monitor is not None:
            self._clipboard_monitor.stop()
            self._clipboard_monitor = None

        # Stop snippet expander
        if self._snippet_expander is not None:
            self._snippet_expander.stop()
            self._snippet_expander = None

        self._snippet_store = None
        self._system_settings_source = None
        self._usage_tracker = None
        self._query_history = None
        self._wz.pasteboard._set_monitor(None)
        self._wz.snippets._set_store(None)

        # Clear all registered sources and trackers
        panel = self._wz.chooser._get_panel()
        panel.reset()

        logger.info("Chooser disabled at runtime")

    # ── Runtime per-source on/off ────────────────────────────────

    def enable_clipboard(self) -> None:
        """Start the clipboard monitor and register its chooser source."""
        if self._clipboard_monitor is not None:
            return  # already running
        try:
            from wenzi.scripting.clipboard_monitor import ClipboardMonitor
            from wenzi.scripting.sources.clipboard_source import ClipboardSource

            chooser_config = self._config.get("chooser", {})
            max_days = chooser_config.get("clipboard_max_days", 7)
            ocr_enabled = chooser_config.get("clipboard_ocr", True)
            persist_path = os.path.expanduser(
                _cfg.DEFAULT_CLIPBOARD_HISTORY_PATH
            )
            prefixes = chooser_config.get("prefixes", {})

            self._clipboard_monitor = ClipboardMonitor(
                max_days=max_days,
                persist_path=persist_path,
                ocr_enabled=ocr_enabled,
            )
            self._clipboard_monitor.start()
            self._wz.pasteboard._set_monitor(self._clipboard_monitor)

            cb_source = ClipboardSource(self._clipboard_monitor)
            self._wz.chooser.register_source(
                cb_source.as_chooser_source(
                    prefix=prefixes.get("clipboard", "cb"),
                )
            )
            logger.info("Clipboard monitor enabled at runtime")
        except Exception:
            logger.exception("Failed to enable clipboard monitor")

    def disable_clipboard(self) -> None:
        """Stop the clipboard monitor and unregister its chooser source."""
        if self._clipboard_monitor is not None:
            self._clipboard_monitor.stop()
            self._clipboard_monitor = None
        self._wz.pasteboard._set_monitor(None)
        self._wz.chooser.unregister_source("clipboard")
        logger.info("Clipboard monitor disabled at runtime")

    def set_system_settings_open_callback(
        self, callback: Callable[[], None]
    ) -> None:
        """Set the callback invoked when a system setting is opened.

        The callback is stored and re-applied after reload().
        """
        self._system_settings_open_cb = callback
        if self._system_settings_source is not None:
            self._system_settings_source.set_on_open(callback)

    def enable_source(self, config_key: str) -> None:
        """Register a single source at runtime by config key."""
        chooser_config = self._config.get("chooser", {})
        prefixes = chooser_config.get("prefixes", {})
        source_map = {
            "app_search": ("apps", self._enable_app_source),
            "clipboard_history": ("clipboard", lambda p: self.enable_clipboard()),
            "file_search": ("files", self._enable_file_source),
            "folder_search": ("folders", self._enable_folder_source),
            "snippets": ("snippets", self._enable_snippet_source),
            "bookmarks": ("bookmarks", self._enable_bookmark_source),
            "calculator": ("calculator", self._enable_calculator_source),
            "system_settings": ("system_settings", self._enable_system_settings_source),
        }
        entry = source_map.get(config_key)
        if not entry:
            return
        _name, enabler = entry
        prefix = prefixes.get(_name, "") if _name != "apps" else ""
        enabler(prefix)

    def disable_source(self, config_key: str) -> None:
        """Unregister a single source at runtime by config key."""
        source_name_map = {
            "app_search": "apps",
            "clipboard_history": "clipboard",
            "file_search": "files",
            "folder_search": "folders",
            "snippets": "snippets",
            "bookmarks": "bookmarks",
            "calculator": "calculator",
            "system_settings": "system_settings",
        }
        source_name = source_name_map.get(config_key)
        if not source_name:
            return
        if config_key == "clipboard_history":
            self.disable_clipboard()
        elif config_key == "snippets":
            if self._snippet_expander is not None:
                self._snippet_expander.stop()
                self._snippet_expander = None
            chooser_config = self._config.get("chooser", {})
            snippet_hotkey = chooser_config.get("new_snippet_hotkey", "")
            if snippet_hotkey:
                self._wz.hotkey.unbind(snippet_hotkey)
            self._snippet_source = None
            self._snippet_store = None
            self._wz.snippets._set_store(None)
            self._wz.chooser.unregister_source(source_name)
        elif config_key == "system_settings":
            self._wz.chooser.unregister_source("system_settings")
            self._wz.chooser.unregister_source("system_settings_mixed")
            self._system_settings_source = None
        else:
            self._wz.chooser.unregister_source(source_name)
        logger.info("Source %s disabled at runtime", config_key)

    def _enable_app_source(self, _prefix: str) -> None:
        try:
            from wenzi.scripting.sources.app_source import AppSource

            app_source = AppSource()
            self._wz.chooser.register_source(app_source.as_chooser_source())
            logger.info("App source enabled at runtime")
        except Exception:
            logger.exception("Failed to enable app source")

    def _enable_file_source(self, prefix: str) -> None:
        try:
            from wenzi.scripting.sources.file_source import FileSource

            file_source = FileSource()
            self._wz.chooser.register_source(
                file_source.as_chooser_source(prefix=prefix)
            )
            logger.info("File source enabled at runtime")
        except Exception:
            logger.exception("Failed to enable file source")

    def _enable_folder_source(self, prefix: str) -> None:
        try:
            from wenzi.scripting.sources.file_source import FolderSource

            folder_source = FolderSource()
            self._wz.chooser.register_source(
                folder_source.as_chooser_source(prefix=prefix)
            )
            logger.info("Folder source enabled at runtime")
        except Exception:
            logger.exception("Failed to enable folder source")

    def _enable_snippet_source(self, prefix: str) -> None:
        try:
            from wenzi.scripting.sources.snippet_source import (
                SnippetSource,
                SnippetStore,
            )

            self._snippet_store = SnippetStore()
            self._wz.snippets._set_store(self._snippet_store)
            snippet_source = SnippetSource(self._snippet_store)
            self._snippet_source = snippet_source
            self._wz.chooser.register_source(
                snippet_source.as_chooser_source(prefix=prefix)
            )
            # Also start expander if configured
            chooser_config = self._config.get("chooser", {})
            if chooser_config.get("snippet_expansion", True):
                from wenzi.scripting.snippet_expander import SnippetExpander

                self._snippet_expander = SnippetExpander(self._snippet_store)
                self._snippet_expander.start()
                panel = self._wz.chooser._get_panel()
                panel._snippet_expander = self._snippet_expander
            logger.info("Snippet source enabled at runtime")
        except Exception:
            logger.exception("Failed to enable snippet source")

    def _enable_bookmark_source(self, prefix: str) -> None:
        try:
            from wenzi.scripting.sources.bookmark_source import BookmarkSource

            bookmark_source = BookmarkSource()
            self._wz.chooser.register_source(
                bookmark_source.as_chooser_source(prefix=prefix)
            )
            logger.info("Bookmark source enabled at runtime")
        except Exception:
            logger.exception("Failed to enable bookmark source")

    def _enable_calculator_source(self, _prefix: str) -> None:
        try:
            from wenzi.scripting.sources.calculator_source import CalculatorSource

            calc_source = CalculatorSource()
            self._wz.chooser.register_source(calc_source.as_chooser_source())
            logger.info("Calculator source enabled at runtime")
        except Exception:
            logger.exception("Failed to enable calculator source")

    def _enable_system_settings_source(self, prefix: str) -> None:
        try:
            from wenzi.scripting.sources.system_settings_source import (
                SystemSettingsSource,
            )

            ss_source = SystemSettingsSource(
                on_open=self._system_settings_open_cb,
            )
            self._system_settings_source = ss_source
            for cs in ss_source.as_chooser_source(prefix=prefix or "ss"):
                self._wz.chooser.register_source(cs)
            logger.info("System settings source enabled at runtime")
        except Exception:
            logger.exception("Failed to enable system settings source")

    def rebind_chooser_hotkey(self, old_hotkey: str, new_hotkey: str) -> None:
        """Unbind old chooser hotkey and bind the new one at runtime."""
        if old_hotkey:
            self._wz.hotkey.unbind(old_hotkey)
        if new_hotkey:
            self._wz.hotkey.bind(new_hotkey, lambda: self._wz.chooser.toggle())
            self._wz.hotkey.start()
        logger.info("Chooser hotkey rebound: %s -> %s", old_hotkey, new_hotkey)

    def set_usage_learning(self, enabled: bool) -> None:
        """Enable or disable the usage learning tracker at runtime."""
        panel = self._wz.chooser._get_panel()
        if enabled:
            if self._usage_tracker is None:
                try:
                    from wenzi.scripting.sources.usage_tracker import UsageTracker

                    self._usage_tracker = UsageTracker()
                except Exception:
                    logger.exception("Failed to create usage tracker")
                    return
            panel._usage_tracker = self._usage_tracker
            logger.info("Usage learning enabled at runtime")
        else:
            self._usage_tracker = None
            panel._usage_tracker = None
            logger.info("Usage learning disabled at runtime")

    def _register_builtin_sources(self) -> None:
        """Register built-in chooser sources."""
        chooser_config = self._config.get("chooser", {})
        if not chooser_config.get("enabled", True):
            logger.info("Chooser disabled via config, skipping source registration")
            return

        # Command source (always registered when chooser is enabled)
        self._wz.chooser._ensure_command_source()

        # Switch-to-English setting
        panel = self._wz.chooser._get_panel()
        panel._switch_english = chooser_config.get("switch_to_english", True)

        # Usage learning tracker
        if chooser_config.get("usage_learning", True):
            try:
                from wenzi.scripting.sources.usage_tracker import UsageTracker

                self._usage_tracker = UsageTracker()
                # Inject tracker into the chooser panel
                panel = self._wz.chooser._get_panel()
                panel._usage_tracker = self._usage_tracker
                logger.info("Usage learning tracker enabled")
            except Exception:
                logger.exception("Failed to set up usage tracker")

        # Query history
        try:
            from wenzi.scripting.sources.query_history import QueryHistory

            self._query_history = QueryHistory()
            panel = self._wz.chooser._get_panel()
            panel._query_history = self._query_history
            logger.info("Query history enabled")
        except Exception:
            logger.exception("Failed to set up query history")

        prefixes = chooser_config.get("prefixes", {})

        # Calculator source
        if chooser_config.get("calculator", True):
            try:
                from wenzi.scripting.sources.calculator_source import CalculatorSource

                calc_source = CalculatorSource()
                self._wz.chooser.register_source(calc_source.as_chooser_source())
                logger.info("Built-in calculator source registered")
            except Exception:
                logger.exception("Failed to register calculator source")

        # App search source
        if chooser_config.get("app_search", True):
            try:
                from wenzi.scripting.sources.app_source import AppSource

                app_source = AppSource()
                self._wz.chooser.register_source(app_source.as_chooser_source())
                logger.info("Built-in app search source registered")
            except Exception:
                logger.exception("Failed to register app search source")

        # Clipboard history source
        if chooser_config.get("clipboard_history", True):
            try:
                from wenzi.scripting.clipboard_monitor import ClipboardMonitor
                from wenzi.scripting.sources.clipboard_source import (
                    ClipboardSource,
                )

                max_days = chooser_config.get("clipboard_max_days", 7)
                ocr_enabled = chooser_config.get("clipboard_ocr", True)
                persist_path = os.path.expanduser(
                    _cfg.DEFAULT_CLIPBOARD_HISTORY_PATH
                )

                self._clipboard_monitor = ClipboardMonitor(
                    max_days=max_days,
                    persist_path=persist_path,
                    ocr_enabled=ocr_enabled,
                )
                self._clipboard_monitor.start()
                self._wz.pasteboard._set_monitor(self._clipboard_monitor)

                cb_source = ClipboardSource(self._clipboard_monitor)
                self._wz.chooser.register_source(
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
                from wenzi.scripting.sources.file_source import FileSource

                file_source = FileSource()
                self._wz.chooser.register_source(
                    file_source.as_chooser_source(
                        prefix=prefixes.get("files", "f"),
                    )
                )
                logger.info("Built-in file search source registered")
            except Exception:
                logger.exception("Failed to register file search source")

        # Folder search source
        if chooser_config.get("folder_search", True):
            try:
                from wenzi.scripting.sources.file_source import FolderSource

                folder_source = FolderSource()
                self._wz.chooser.register_source(
                    folder_source.as_chooser_source(
                        prefix=prefixes.get("folders", "fd"),
                    )
                )
                logger.info("Built-in folder search source registered")
            except Exception:
                logger.exception("Failed to register folder search source")

        # Snippet source
        if chooser_config.get("snippets", True):
            try:
                from wenzi.scripting.sources.snippet_source import (
                    SnippetSource,
                    SnippetStore,
                )

                self._snippet_store = SnippetStore()
                self._wz.snippets._set_store(self._snippet_store)
                snippet_source = SnippetSource(self._snippet_store)
                self._snippet_source = snippet_source
                self._wz.chooser.register_source(
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
                from wenzi.scripting.snippet_expander import SnippetExpander

                self._snippet_expander = SnippetExpander(self._snippet_store)
                self._snippet_expander.start()
                panel = self._wz.chooser._get_panel()
                panel._snippet_expander = self._snippet_expander
                logger.info("Snippet keyword expander started")
            except Exception:
                logger.exception("Failed to start snippet expander")

        # System Settings source
        if chooser_config.get("system_settings", True):
            try:
                from wenzi.scripting.sources.system_settings_source import (
                    SystemSettingsSource,
                )

                ss_source = SystemSettingsSource(
                    on_open=self._system_settings_open_cb,
                )
                self._system_settings_source = ss_source
                prefix = prefixes.get("system_settings", "ss")
                for cs in ss_source.as_chooser_source(prefix=prefix):
                    self._wz.chooser.register_source(cs)
                logger.info("Built-in system settings source registered")
            except Exception:
                logger.exception("Failed to register system settings source")

        # Bookmark search source
        if chooser_config.get("bookmarks", True):
            try:
                from wenzi.scripting.sources.bookmark_source import (
                    BookmarkSource,
                )

                bookmark_source = BookmarkSource()
                self._wz.chooser.register_source(
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
            self._wz.hotkey.bind(hotkey_str, lambda: self._wz.chooser.toggle())
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
                self._wz.hotkey.bind(
                    hotkey_str,
                    lambda p=prefix: self._wz.chooser.show_source(p),
                )
                logger.info(
                    "Source hotkey bound: %s -> %s", hotkey_str, source_key,
                )

    def _bind_new_snippet_hotkey(self) -> None:
        """Bind the 'New Snippet' hotkey from config."""
        chooser_config = self._config.get("chooser", {})
        hotkey_str = chooser_config.get("new_snippet_hotkey", "")
        if hotkey_str and self._snippet_source:
            self._wz.hotkey.bind(
                hotkey_str,
                lambda: self._snippet_source.create_snippet(""),
            )
            logger.info("New snippet hotkey bound: %s", hotkey_str)

    def rebind_new_snippet_hotkey(
        self, old_hotkey: str, new_hotkey: str,
    ) -> None:
        """Unbind old new-snippet hotkey and bind the new one at runtime."""
        if old_hotkey:
            self._wz.hotkey.unbind(old_hotkey)
        if new_hotkey and self._snippet_source:
            self._wz.hotkey.bind(
                new_hotkey,
                lambda: self._snippet_source.create_snippet(""),
            )
            self._wz.hotkey.start()

    def _purge_user_modules(self) -> None:
        """Remove cached user script and plugin modules so reload picks up changes."""
        purge_dirs = [os.path.normpath(self._script_dir) + os.sep]
        plugins_norm = os.path.normpath(self._plugins_dir) + os.sep
        if os.path.isdir(self._plugins_dir):
            purge_dirs.append(plugins_norm)

        for name, mod in list(sys.modules.items()):
            mod_file = getattr(mod, "__file__", None)
            if mod_file:
                norm_file = os.path.normpath(mod_file)
                for d in purge_dirs:
                    if norm_file.startswith(d):
                        self._remove_pyc(mod_file)
                        del sys.modules[name]
                        break
                continue
            mod_path = getattr(mod, "__path__", None)
            if mod_path:
                for p in mod_path:
                    norm_p = os.path.normpath(p)
                    for d in purge_dirs:
                        if norm_p.startswith(d):
                            del sys.modules[name]
                            break
                    else:
                        continue
                    break
        importlib.invalidate_caches()

    @staticmethod
    def _remove_pyc(source_path: str) -> None:
        """Delete the cached .pyc file for a source file."""
        try:
            pyc = importlib.util.cache_from_source(source_path)
            if os.path.isfile(pyc):
                os.remove(pyc)
        except (NotImplementedError, ValueError, OSError):
            pass

    def _load_scripts(self) -> None:
        """Execute init.py in the scripts directory."""
        # Ensure scripts dir is on sys.path so init.py can import sibling modules
        norm_dir = os.path.normpath(self._script_dir)
        if norm_dir not in sys.path:
            sys.path.append(norm_dir)

        init_path = os.path.join(self._script_dir, "init.py")

        if not os.path.isfile(init_path):
            logger.info("No init.py found at %s, skipping", init_path)
            return

        logger.info("Loading script: %s", init_path)
        script_globals = {
            "wz": self._wz,
            "__builtins__": __builtins__,
            "__file__": init_path,
            "__name__": "__wz_script__",
        }

        try:
            with open(init_path, "r", encoding="utf-8") as f:
                code = f.read()
            exec(compile(code, init_path, "exec"), script_globals)  # noqa: S102
            logger.info("Script loaded successfully: %s", init_path)
        except Exception as exc:
            logger.error("Failed to load script %s: %s", init_path, exc, exc_info=True)
            self._try_alert(f"Script error: {exc}", duration=5.0)

    def _load_plugins(self) -> None:
        """Auto-discover and load plugins from the plugins directory.

        Each subdirectory with an ``__init__.py`` defining a ``setup(wz)``
        function is treated as a plugin.  Plugins listed in the config key
        ``disabled_plugins`` are skipped.  Plugins whose
        ``min_wenzi_version`` exceeds the running version are skipped.

        Load errors are stored in ``_plugin_load_errors`` keyed by dir name.
        """
        self._plugin_metas.clear()
        errors: Dict[str, Dict[str, str]] = {}

        if not os.path.isdir(self._plugins_dir):
            self._plugin_load_errors = errors
            return

        from wenzi.scripting.plugin_installer import BACKUP_SUFFIX, TEMP_DIR_PREFIX

        # Clean up stale temp/backup dirs from interrupted installs
        for entry in os.listdir(self._plugins_dir):
            entry_path = os.path.join(self._plugins_dir, entry)
            if not os.path.isdir(entry_path):
                continue
            if entry.startswith(TEMP_DIR_PREFIX):
                shutil.rmtree(entry_path, ignore_errors=True)
                logger.info("Removed stale temp dir: %s", entry)
            elif entry.endswith(BACKUP_SUFFIX):
                target = entry_path.removesuffix(BACKUP_SUFFIX)
                if os.path.isdir(target):
                    shutil.rmtree(entry_path, ignore_errors=True)
                    logger.info("Removed stale backup dir: %s", entry)
                else:
                    os.rename(entry_path, target)
                    logger.info(
                        "Restored backup dir: %s -> %s",
                        entry,
                        os.path.basename(target),
                    )

        from wenzi.scripting.plugin_meta import load_plugin_meta

        norm_dir = os.path.normpath(self._plugins_dir)
        if norm_dir not in sys.path:
            sys.path.insert(0, norm_dir)

        disabled = set(self._config.get("disabled_plugins", []))

        # Re-scan after cleanup to see the post-cleanup state
        for entry in sorted(os.listdir(self._plugins_dir)):
            if entry.startswith((".", "_")):
                continue
            plugin_path = os.path.join(self._plugins_dir, entry)
            if not os.path.isdir(plugin_path):
                continue
            if not os.path.isfile(os.path.join(plugin_path, "__init__.py")):
                continue

            # Read metadata (always, even if plugin will be skipped)
            meta = load_plugin_meta(plugin_path)
            self._plugin_metas[entry] = meta

            # Check disabled by directory name OR bundle ID
            is_disabled = entry in disabled or (meta.id and meta.id in disabled)

            # Auto-migrate: if matched by dir name but plugin has bundle ID,
            # replace the dir name entry with the bundle ID in config
            if is_disabled and meta.id and entry in disabled and meta.id not in disabled:
                disabled_list = self._config.get("disabled_plugins", [])
                if entry in disabled_list:
                    idx = disabled_list.index(entry)
                    disabled_list[idx] = meta.id
                    logger.info(
                        "Migrated disabled_plugins entry: %s -> %s", entry, meta.id
                    )

            if is_disabled:
                logger.info("Plugin %s is disabled, skipping", entry)
                continue

            # Check version compatibility
            if meta.min_wenzi_version and not self._version_compatible(
                meta.min_wenzi_version
            ):
                logger.warning(
                    "Plugin %s (%s) requires WenZi >= %s, skipping",
                    meta.name,
                    entry,
                    meta.min_wenzi_version,
                )
                continue

            try:
                mod = importlib.import_module(entry)
                if hasattr(mod, "setup") and callable(mod.setup):
                    mod.setup(self._wz)
                    parts = [f"Plugin loaded: {meta.name}"]
                    if meta.version:
                        parts.append(f"v{meta.version}")
                    parts.append(f"({entry})")
                    if meta.description:
                        parts.append(f"— {meta.description}")
                    if meta.author:
                        parts.append(f"[by {meta.author}]")
                    logger.info(" ".join(parts))
                else:
                    logger.warning("Plugin %s has no setup() function, skipped", entry)
                    errors[entry] = {
                        "message": "no setup() function",
                        "traceback": "",
                    }
            except Exception as exc:
                logger.exception("Failed to load plugin: %s", entry)
                errors[entry] = {
                    "message": str(exc),
                    "traceback": _tb.format_exc(),
                }

        self._plugin_load_errors = errors

    def get_plugin_metas(self) -> Dict[str, "PluginMeta"]:
        """Return metadata for all discovered plugins (keyed by directory name)."""
        return dict(self._plugin_metas)

    def get_load_errors_by_id(self) -> Dict[str, Dict[str, str]]:
        """Return plugin load errors keyed by plugin bundle ID."""
        result: Dict[str, Dict[str, str]] = {}
        for dir_name, error in self._plugin_load_errors.items():
            meta = self._plugin_metas.get(dir_name)
            key = meta.id if meta and meta.id else dir_name
            result[key] = error
        return result

    @staticmethod
    def _version_compatible(min_version: str) -> bool:
        """Return True if the running WenZi version meets *min_version*."""
        from wenzi import is_version_compatible

        return is_version_compatible(min_version)
