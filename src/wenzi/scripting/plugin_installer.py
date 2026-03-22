"""Plugin installer — download, install, update, uninstall plugins."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import tomllib
from datetime import datetime, timezone

from wenzi.scripting.plugin_meta import (
    INSTALL_TOML,
    find_plugin_dir,
    load_install_info,
    read_source,
)

logger = logging.getLogger(__name__)

TEMP_DIR_PREFIX = "_tmp_"
BACKUP_SUFFIX = ".bak"


class PluginInstaller:
    """Install, update, and uninstall plugins."""

    def __init__(self, plugins_dir: str):
        self._plugins_dir = plugins_dir

    def install(self, source_url: str) -> str:
        """Install a plugin from a plugin.toml URL (remote or local path).

        Returns the install directory path. Rolls back on failure.
        """
        raw, section = self._fetch_plugin_toml(source_url)
        plugin_id = section.get("id", "")
        if not plugin_id:
            raise ValueError("plugin.toml missing required 'id' field")

        version = str(section.get("version", ""))
        files = self._parse_files(section)
        install_dir = self._resolve_install_dir(plugin_id)
        base_url = source_url.rsplit("/", 1)[0]

        tempdir = self._download_to_temp(base_url, files, raw, source_url, version)
        try:
            self._atomic_replace(tempdir, install_dir)
        except BaseException:
            shutil.rmtree(tempdir, ignore_errors=True)
            raise
        return install_dir

    def update(self, plugin_id: str) -> str:
        """Update an installed plugin by re-downloading from its source URL."""
        plugin_dir = find_plugin_dir(self._plugins_dir, plugin_id)
        if plugin_dir is None:
            raise ValueError(f"Plugin {plugin_id!r} not found")
        info = load_install_info(plugin_dir)
        if info is None:
            raise ValueError(f"Plugin {plugin_id!r} has no install.toml (manually placed)")
        source_url = info.get("source_url", "")
        if not source_url:
            raise ValueError(f"Plugin {plugin_id!r} has no source_url in install.toml")

        raw, section = self._fetch_plugin_toml(source_url)
        version = str(section.get("version", ""))
        files = self._parse_files(section)
        base_url = source_url.rsplit("/", 1)[0]

        tempdir = self._download_to_temp(base_url, files, raw, source_url, version)
        try:
            self._atomic_replace(tempdir, plugin_dir)
        except BaseException:
            shutil.rmtree(tempdir, ignore_errors=True)
            raise
        return plugin_dir

    def uninstall(self, plugin_id: str) -> None:
        """Remove a plugin directory entirely."""
        plugin_dir = find_plugin_dir(self._plugins_dir, plugin_id)
        if plugin_dir is None:
            raise ValueError(f"Plugin {plugin_id!r} not found")
        shutil.rmtree(plugin_dir)

    def _download_to_temp(
        self, base_url: str, files: list[str], raw_toml: bytes,
        source_url: str, version: str,
    ) -> str:
        """Download plugin files to a temp directory inside plugins_dir.

        Returns the temp directory path. Cleans up on failure.
        """
        os.makedirs(self._plugins_dir, exist_ok=True)
        tempdir = tempfile.mkdtemp(dir=self._plugins_dir, prefix=TEMP_DIR_PREFIX)
        try:
            self._download_files(base_url, files, tempdir)
            with open(os.path.join(tempdir, "plugin.toml"), "wb") as f:
                f.write(raw_toml)
            self._write_install_toml(tempdir, source_url, version)
        except BaseException:
            shutil.rmtree(tempdir, ignore_errors=True)
            raise
        return tempdir

    @staticmethod
    def _atomic_replace(tempdir: str, target: str) -> None:
        """Atomically replace *target* with *tempdir*, backing up if needed."""
        backup = target + BACKUP_SUFFIX
        if os.path.isdir(target):
            shutil.rmtree(backup, ignore_errors=True)
            os.rename(target, backup)
            try:
                os.rename(tempdir, target)
            except BaseException:
                os.rename(backup, target)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        else:
            os.rename(tempdir, target)

    @staticmethod
    def _fetch_plugin_toml(source_url: str) -> tuple[bytes, dict]:
        """Fetch and parse plugin.toml. Returns (raw_bytes, plugin_section)."""
        raw = read_source(source_url)
        data = tomllib.loads(raw.decode("utf-8"))
        return raw, data.get("plugin", {})

    @staticmethod
    def _parse_files(section: dict) -> list[str]:
        files = section.get("files", [])
        if isinstance(files, str):
            files = [files]
        return files

    @staticmethod
    def _download_files(base_url: str, files: list[str], target_dir: str) -> None:
        abs_target = os.path.abspath(target_dir)
        for fname in files:
            file_path = os.path.normpath(os.path.join(target_dir, fname))
            if not os.path.abspath(file_path).startswith(abs_target + os.sep):
                raise ValueError(f"Path traversal in files list: {fname!r}")
            file_data = read_source(f"{base_url}/{fname}")
            parent = os.path.dirname(file_path)
            if parent != abs_target:
                os.makedirs(parent, exist_ok=True)
            with open(file_path, "wb") as f:
                f.write(file_data)

    def _resolve_install_dir(self, plugin_id: str) -> str:
        dir_name = plugin_id.replace(".", "_").replace("-", "_")
        return os.path.join(self._plugins_dir, dir_name)

    @staticmethod
    def _escape_toml_string(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    @staticmethod
    def _write_install_toml(plugin_dir: str, source_url: str, version: str) -> None:
        esc = PluginInstaller._escape_toml_string
        content = (
            "[install]\n"
            f'source_url = "{esc(source_url)}"\n'
            f'installed_version = "{esc(version)}"\n'
            f'installed_at = "{datetime.now(timezone.utc).isoformat()}"\n'
        )
        with open(os.path.join(plugin_dir, INSTALL_TOML), "w") as f:
            f.write(content)
