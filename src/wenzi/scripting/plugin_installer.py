"""Plugin installer — download, install, update, uninstall plugins."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
import time
import tomllib
from collections.abc import Callable
from datetime import datetime, timezone

from wenzi.scripting.plugin_meta import (
    INSTALL_TOML,
    find_plugin_dir,
    load_install_info,
    read_source,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]  # (current, total)

TEMP_DIR_PREFIX = "_tmp_"
BACKUP_SUFFIX = ".bak"


def resolve_ref(ref: str) -> str:
    """Classify a user-provided ref string into a GitHub-compatible ref path.

    - 40-char hex -> commit SHA (lowercased)
    - vX.Y.Z or X.Y.Z -> refs/tags/vX.Y.Z
    - everything else -> refs/heads/{ref}

    Raises ValueError for empty input or short SHA-like strings (7-39 hex chars).
    """
    if not ref:
        raise ValueError("ref must not be empty")
    lower = ref.lower()
    if re.fullmatch(r"[0-9a-f]{40}", lower):
        return lower
    if re.fullmatch(r"[0-9a-f]{7,39}", lower):
        raise ValueError(
            f"Looks like an abbreviated commit SHA ({ref!r}). "
            "Please provide the full 40-character SHA."
        )
    if re.fullmatch(r"v?\d+\.\d+(?:\.\d+)*", ref):
        tag = ref if ref.startswith("v") else f"v{ref}"
        return f"refs/tags/{tag}"
    return f"refs/heads/{ref}"


_GITHUB_RAW_PREFIX = "https://raw.githubusercontent.com/"
_DEFAULT_GITHUB_REF = "refs/heads/main"


def replace_github_ref(
    source_url: str,
    new_ref: str,
    current_ref: str = _DEFAULT_GITHUB_REF,
) -> str:
    """Replace the git ref segment in a raw.githubusercontent.com URL.

    Raises ValueError if the URL is not a GitHub raw URL or if *current_ref*
    is not found in the URL.
    """
    if not source_url.startswith(_GITHUB_RAW_PREFIX):
        raise ValueError(
            "Version-specific install is only supported for GitHub raw URLs, "
            f"got: {source_url!r}"
        )
    marker = f"/{current_ref}/"
    idx = source_url.find(marker)
    if idx == -1:
        raise ValueError(
            f"Current ref {current_ref!r} not found in URL: {source_url!r}"
        )
    return source_url[:idx] + f"/{new_ref}/" + source_url[idx + len(marker):]


class PluginInstaller:
    """Install, update, and uninstall plugins."""

    def __init__(self, plugins_dir: str):
        self._plugins_dir = plugins_dir

    def install(
        self,
        source_url: str,
        pinned_ref: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> str:
        """Install a plugin from a plugin.toml URL (remote or local path).

        Returns the install directory path. Rolls back on failure.
        If *pinned_ref* is given, it is persisted in install.toml so that
        future updates can preserve the pin.
        If *progress* is given, it is called with (current, total) after each
        file download.
        """
        logger.info("Installing plugin from %s", source_url)
        raw, section = self._fetch_plugin_toml(source_url)
        plugin_id = section.get("id", "")
        if not plugin_id:
            raise ValueError("plugin.toml missing required 'id' field")

        install_dir = self._resolve_install_dir(plugin_id)
        self._fetch_and_replace(
            source_url, raw, section, install_dir, plugin_id,
            pinned_ref=pinned_ref, progress=progress,
        )
        logger.info("Plugin %s installed to %s", plugin_id, install_dir)
        return install_dir

    def update(
        self, plugin_id: str, progress: ProgressCallback | None = None,
    ) -> str:
        """Update an installed plugin by re-downloading from its source URL."""
        logger.info("Updating plugin %s", plugin_id)
        plugin_dir = find_plugin_dir(self._plugins_dir, plugin_id)
        if plugin_dir is None:
            raise ValueError(f"Plugin {plugin_id!r} not found")
        info = load_install_info(plugin_dir)
        if info is None:
            raise ValueError(f"Plugin {plugin_id!r} has no install.toml (manually placed)")
        source_url = info.get("source_url", "")
        if not source_url:
            raise ValueError(f"Plugin {plugin_id!r} has no source_url in install.toml")

        pinned_ref = info.get("pinned_ref") or None

        raw, section = self._fetch_plugin_toml(source_url)
        self._fetch_and_replace(
            source_url, raw, section, plugin_dir, plugin_id,
            pinned_ref=pinned_ref, progress=progress,
        )
        logger.info("Plugin %s updated", plugin_id)
        return plugin_dir

    def uninstall(self, plugin_id: str) -> None:
        """Remove a plugin directory entirely."""
        logger.info("Uninstalling plugin %s", plugin_id)
        plugin_dir = find_plugin_dir(self._plugins_dir, plugin_id)
        if plugin_dir is None:
            raise ValueError(f"Plugin {plugin_id!r} not found")
        shutil.rmtree(plugin_dir)
        logger.info("Plugin %s removed", plugin_id)

    def _fetch_and_replace(
        self,
        source_url: str,
        raw_toml: bytes,
        section: dict,
        target_dir: str,
        plugin_id: str,
        *,
        pinned_ref: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        """Download files listed in *section* and atomically replace *target_dir*."""
        version = str(section.get("version", ""))
        files = self._parse_files(section)
        base_url = source_url.rsplit("/", 1)[0]
        logger.info("Plugin %s v%s — downloading %d files", plugin_id, version, len(files))

        tempdir = self._download_to_temp(
            base_url, files, raw_toml, source_url, version,
            pinned_ref=pinned_ref, progress=progress,
        )
        try:
            self._atomic_replace(tempdir, target_dir)
        except BaseException:
            shutil.rmtree(tempdir, ignore_errors=True)
            raise

    def _download_to_temp(
        self, base_url: str, files: list[str], raw_toml: bytes,
        source_url: str, version: str,
        *, pinned_ref: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> str:
        """Download plugin files to a temp directory inside plugins_dir.

        Returns the temp directory path. Cleans up on failure.
        """
        os.makedirs(self._plugins_dir, exist_ok=True)
        tempdir = tempfile.mkdtemp(dir=self._plugins_dir, prefix=TEMP_DIR_PREFIX)
        try:
            self._download_files(base_url, files, tempdir, progress=progress)
            with open(os.path.join(tempdir, "plugin.toml"), "wb") as f:
                f.write(raw_toml)
            self._write_install_toml(
                tempdir, source_url, version, pinned_ref=pinned_ref,
            )
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

    _MAX_DOWNLOAD_RETRIES = 2
    _MAX_DOWNLOAD_WORKERS = 4
    _RETRY_DELAY: float = 1.0

    @staticmethod
    def _download_files(
        base_url: str, files: list[str], target_dir: str,
        *, progress: ProgressCallback | None = None,
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        abs_target = os.path.abspath(target_dir)
        total = len(files)
        if not total:
            return

        # Validate paths before downloading
        file_paths: dict[str, str] = {}
        for fname in files:
            file_path = os.path.normpath(os.path.join(target_dir, fname))
            if not os.path.abspath(file_path).startswith(abs_target + os.sep):
                raise ValueError(f"Path traversal in files list: {fname!r}")
            file_paths[fname] = file_path

        completed = 0
        lock = threading.Lock()
        cancel = threading.Event()

        def _download_one(fname: str) -> None:
            nonlocal completed
            if cancel.is_set():
                return
            url = f"{base_url}/{fname}"
            for attempt in range(PluginInstaller._MAX_DOWNLOAD_RETRIES + 1):
                try:
                    logger.debug("Downloading %s from %s", fname, url)
                    data = read_source(url)
                    break
                except Exception:
                    if cancel.is_set() or attempt >= PluginInstaller._MAX_DOWNLOAD_RETRIES:
                        raise
                    logger.warning(
                        "Download %s failed (attempt %d), retrying in %.1fs...",
                        fname, attempt + 1, PluginInstaller._RETRY_DELAY,
                    )
                    time.sleep(PluginInstaller._RETRY_DELAY)

            if cancel.is_set():
                return
            fp = file_paths[fname]
            parent = os.path.dirname(fp)
            if parent != abs_target:
                os.makedirs(parent, exist_ok=True)
            with open(fp, "wb") as f:
                f.write(data)

            with lock:
                completed += 1
                current = completed
            if progress:
                progress(current, total)

        with ThreadPoolExecutor(
            max_workers=PluginInstaller._MAX_DOWNLOAD_WORKERS,
        ) as pool:
            futures = {pool.submit(_download_one, fname): fname for fname in files}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    cancel.set()
                    raise

    def _resolve_install_dir(self, plugin_id: str) -> str:
        dir_name = plugin_id.replace(".", "_").replace("-", "_")
        return os.path.join(self._plugins_dir, dir_name)

    @staticmethod
    def _escape_toml_string(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    @staticmethod
    def _write_install_toml(
        plugin_dir: str, source_url: str, version: str,
        *, pinned_ref: str | None = None,
    ) -> None:
        esc = PluginInstaller._escape_toml_string
        content = (
            "[install]\n"
            f'source_url = "{esc(source_url)}"\n'
            f'installed_version = "{esc(version)}"\n'
            f'installed_at = "{datetime.now(timezone.utc).isoformat()}"\n'
        )
        if pinned_ref is not None:
            content += f'pinned_ref = "{esc(pinned_ref)}"\n'
        with open(os.path.join(plugin_dir, INSTALL_TOML), "w") as f:
            f.write(content)
