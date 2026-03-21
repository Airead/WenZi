"""Scan Claude Code sessions from ~/.claude/projects/ directories."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _make_session(
    session_id: str,
    file_path: str,
    project: str,
    cwd: str = "",
    title: str = "",
    first_prompt: str = "",
    git_branch: str = "",
    created: str = "",
    modified: str = "",
    message_count: Optional[int] = None,
    version: str = "",
) -> dict[str, Any]:
    """Build a session metadata dict."""
    return {
        "session_id": session_id,
        "file_path": file_path,
        "project": project,
        "cwd": cwd,
        "title": title or _choose_title(None, None, first_prompt),
        "first_prompt": first_prompt,
        "git_branch": git_branch,
        "created": created,
        "modified": modified,
        "message_count": message_count,
        "version": version,
    }


def _project_name_from_dir(dirname: str) -> str:
    """Derive project name from directory name.

    The directory name is the project path with slashes replaced by dashes,
    e.g. ``-Users-fanrenhao-work-VoiceText`` -> ``VoiceText``.
    """
    # Strip leading/trailing dashes, take last segment
    stripped = dirname.strip("-")
    if not stripped:
        return dirname
    parts = stripped.split("-")
    return parts[-1] if parts else dirname


def _choose_title(
    custom_title: str | None,
    summary: str | None,
    first_prompt: str | None,
) -> str:
    """Return best available title, truncated to 80 chars.

    Priority: customTitle > summary > firstPrompt.
    """
    raw = custom_title or summary or first_prompt or ""
    if len(raw) > 80:
        return raw[:77] + "..."
    return raw


def _scan_project_with_index(
    proj_dir: Path,
    project_name: str,
) -> list[dict[str, Any]]:
    """Read sessions-index.json and return a list of session dicts."""
    index_path = proj_dir / "sessions-index.json"
    if not index_path.is_file():
        return []

    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    results: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        session_id = entry.get("sessionId", "")
        full_path = entry.get("fullPath", "")
        if not full_path:
            full_path = str(proj_dir / f"{session_id}.jsonl")

        title = _choose_title(
            entry.get("customTitle"),
            entry.get("summary"),
            entry.get("firstPrompt"),
        )

        results.append(_make_session(
            session_id=session_id,
            file_path=full_path,
            project=project_name,
            cwd=entry.get("projectPath", ""),
            title=title,
            first_prompt=entry.get("firstPrompt", ""),
            git_branch=entry.get("gitBranch", ""),
            created=entry.get("created", ""),
            modified=entry.get("modified", ""),
            message_count=entry.get("messageCount", 0),
            version=entry.get("version", ""),
        ))

    return results


def _scan_session_jsonl(
    jsonl_path: Path,
    project_name: str,
) -> dict[str, Any] | None:
    """Parse a JSONL session file header to build a session dict.

    Reads up to the first 30 lines to find metadata and the first user message.
    Returns None if the file is unreadable or contains no useful data.
    """
    try:
        lines = _read_head(jsonl_path, max_lines=30)
    except OSError:
        return None

    if not lines:
        return None

    session_id = jsonl_path.stem
    cwd = ""
    version = ""
    git_branch = ""
    first_user_message: str | None = None
    first_timestamp: str | None = None
    last_timestamp: str | None = None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(obj, dict):
            continue

        ts = obj.get("timestamp")
        if ts:
            if first_timestamp is None:
                first_timestamp = ts
            last_timestamp = ts

        if not cwd and obj.get("cwd"):
            cwd = obj["cwd"]
        if not version and obj.get("version"):
            version = obj["version"]
        if not git_branch and obj.get("gitBranch"):
            git_branch = obj["gitBranch"]

        if first_user_message is None and obj.get("type") == "user":
            msg = obj.get("message", {})
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            if isinstance(content, list):
                # content parts — extract text parts
                text_parts = [
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                ]
                content = " ".join(t for t in text_parts if t)
            if content:
                first_user_message = content

    if first_timestamp is None and first_user_message is None:
        return None

    # Use file stat for modified time as fallback
    try:
        stat = jsonl_path.stat()
        file_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        file_modified = last_timestamp or ""

    title = _choose_title(None, None, first_user_message)

    return _make_session(
        session_id=session_id,
        file_path=str(jsonl_path),
        project=project_name,
        cwd=cwd,
        title=title,
        first_prompt=first_user_message or "",
        git_branch=git_branch,
        created=first_timestamp or "",
        modified=last_timestamp or file_modified,
        message_count=0,
        version=version,
    )


def _read_head(path: Path, max_lines: int = 30) -> list[str]:
    """Read up to *max_lines* lines from a file."""
    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i >= max_lines:
                break
            lines.append(line)
    return lines


_UNSET = object()


class SessionScanner:
    """Discover and cache Claude Code sessions."""

    def __init__(
        self,
        base_dir: Path | None = None,
        cache_path: Path | None | object = _UNSET,
    ) -> None:
        if base_dir is None:
            base_dir = Path.home() / ".claude" / "projects"
        self._base_dir = base_dir

        if cache_path is _UNSET:
            # Default: use WenZi cache dir
            from wenzi.config import resolve_cache_dir
            cache_path = Path(resolve_cache_dir()) / "cc_sessions_cache.json"

        if cache_path is not None:
            from .cache import SessionCache
            self._cache: SessionCache | None = SessionCache(cache_path)
        else:
            self._cache = None

    def scan_all(self) -> list[dict[str, Any]]:
        """Return all sessions sorted by modified descending."""
        if not self._base_dir.is_dir():
            return []

        sessions: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        live_paths: set[str] = set()
        live_index_paths: set[str] = set()

        for proj_entry in self._base_dir.iterdir():
            if not proj_entry.is_dir():
                continue
            project_name = _project_name_from_dir(proj_entry.name)

            # Fast path: sessions-index.json
            index_path = proj_entry / "sessions-index.json"
            if index_path.exists():
                cache_key = str(index_path)
                live_index_paths.add(cache_key)
                try:
                    mtime = index_path.stat().st_mtime
                except OSError:
                    continue

                cached = self._cache.get_index(cache_key) if self._cache else None
                if cached and cached[0] == mtime:
                    index_sessions = cached[1]
                else:
                    index_sessions = _scan_project_with_index(proj_entry, project_name)
                    if self._cache:
                        self._cache.put_index(cache_key, mtime, index_sessions)

                if index_sessions:
                    for s in index_sessions:
                        seen_ids.add(s["session_id"])
                        sessions.append(s)
                    continue

            # Fallback: scan individual JSONL files
            for jsonl_file in proj_entry.glob("*.jsonl"):
                cache_key = str(jsonl_file)
                live_paths.add(cache_key)
                try:
                    mtime = jsonl_file.stat().st_mtime
                except OSError:
                    continue

                cached = self._cache.get(cache_key) if self._cache else None
                if cached and cached[0] == mtime:
                    session = cached[1]
                else:
                    result = _scan_session_jsonl(jsonl_file, project_name)
                    if result is None:
                        continue
                    session = result
                    if self._cache:
                        self._cache.put(cache_key, mtime, session)

                if session["session_id"] not in seen_ids:
                    seen_ids.add(session["session_id"])
                    sessions.append(session)

        # Prune deleted entries and save
        if self._cache:
            self._cache.prune(live_paths, live_index_paths)
            self._cache.save()

        # Sort by modified descending
        sessions.sort(key=lambda s: s.get("modified", ""), reverse=True)
        return sessions
