"""Shared git utilities for cc_sessions plugin."""

from __future__ import annotations

import subprocess
from pathlib import Path

# Cache: cwd path -> resolved project name
_project_name_cache: dict[str, str] = {}
_MAX_PROJECT_CACHE = 512


def clear_project_name_cache() -> None:
    """Clear the in-memory project name cache."""
    _project_name_cache.clear()


def resolve_project_name(cwd: str, fallback: str = "") -> str:
    """Resolve the project name from *cwd*, with caching.

    Priority: git remote origin repo name > git root / cwd basename
    (before ".") > *fallback*.
    """
    if not cwd:
        return fallback
    cached = _project_name_cache.get(cwd)
    if cached is not None:
        return cached

    git_root = _find_git_root(cwd)
    effective = git_root or cwd
    name = _git_remote_name(effective) or _name_from_cwd(effective) or fallback
    _project_name_cache[cwd] = name
    if len(_project_name_cache) > _MAX_PROJECT_CACHE:
        for key in list(_project_name_cache.keys())[: _MAX_PROJECT_CACHE // 4]:
            del _project_name_cache[key]
    return name


def _find_git_root(cwd: str) -> str:
    """Walk up from *cwd* to filesystem root looking for ``.git``."""
    try:
        current = Path(cwd).resolve()
    except (OSError, ValueError):
        return ""
    if not current.exists():
        return ""
    while True:
        if (current / ".git").exists():
            return str(current)
        parent = current.parent
        if parent == current:
            return ""
        current = parent


def _git_remote_name(cwd: str) -> str:
    """Extract the repository name from git remote origin URL."""
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        url = result.stdout.strip()
        if not url:
            return ""
        base = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if base.endswith(".git"):
            base = base[:-4]
        return base
    except OSError:
        return ""


def _name_from_cwd(cwd: str) -> str:
    """Extract project name from cwd basename, handling worktree naming."""
    basename = Path(cwd).name
    if "." in basename:
        return basename.split(".", 1)[0]
    return basename
