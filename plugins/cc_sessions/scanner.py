"""Scan Claude Code sessions from ~/.claude/projects/ directories."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from time import time
from typing import Any

from .git_utils import clear_project_name_cache, resolve_project_name

# XML-like tags injected by Claude Code for system messages and commands
_NOISE_PATTERN = re.compile(
    r"^<(local-command-caveat|command-name|command-message|"
    r"command-args|system-reminder|user-prompt-submit-hook)"
)


def is_noise_message(text: str) -> bool:
    """Return True if *text* is a system-injected noise message."""
    return bool(_NOISE_PATTERN.match(text.strip()))


def _strip_xml_tags(text: str) -> str:
    """Remove XML-like tags and return the inner text content."""
    return re.sub(r"<[^>]+>", "", text).strip()


_PLAN_PREFIX = "Implement the following plan:"


def _extract_plan_title(plan_content: str) -> str:
    """Extract the first markdown heading from plan content."""
    for line in plan_content.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _clean_custom_title(raw: str) -> str:
    """Clean a custom title: extract plan title if it starts with plan prefix."""
    if raw.startswith(_PLAN_PREFIX):
        remainder = raw[len(_PLAN_PREFIX) :].strip()
        # custom-title has plan on one line; extract heading then truncate at next ##
        title = _extract_plan_title(remainder)
        if title:
            for sep in [" ## ", " # "]:
                idx = title.find(sep)
                if idx != -1:
                    title = title[:idx]
            return title.strip()
    return raw


def _clean_first_prompt(raw: str) -> str:
    """Clean a firstPrompt value: strip noise, extract useful text."""
    if not raw or not is_noise_message(raw):
        return raw
    cleaned = _strip_xml_tags(raw)
    return cleaned if cleaned else ""


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
    message_count: int | None = None,
    version: str = "",
    summary: str = "",
    custom_title: str = "",
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
        "summary": summary,
        "custom_title": custom_title,
    }


def _project_name_from_dir(dirname: str) -> str:
    """Derive project name from directory name (fallback only).

    The directory name is the project path with slashes replaced by dashes,
    e.g. ``-Users-fanrenhao-work-VoiceText`` -> ``VoiceText``.
    """
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

    Priority: customTitle > summary > cleaned firstPrompt.
    """
    raw = custom_title or summary or _clean_first_prompt(first_prompt or "") or ""
    if len(raw) > 80:
        return raw[:77] + "..."
    return raw


def _scan_session_jsonl(
    jsonl_path: Path,
    project_name: str,
) -> dict[str, Any] | None:
    """Parse a JSONL session file to build a session dict.

    Reads the entire file in a single pass: extracts metadata from early lines
    and counts real user messages throughout.
    Returns None if the file is unreadable or contains no useful data.
    """
    try:
        fh = jsonl_path.open("rb")
    except OSError:
        return None

    session_id = jsonl_path.stem
    cwd = ""
    version = ""
    git_branch = ""
    first_user_message: str | None = None
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    custom_title = ""
    summary = ""
    user_msg_count = 0
    metadata_lines = 30

    with fh:
        for i, raw_line in enumerate(fh):
            line = raw_line.strip()
            if not line:
                continue

            # Count real user messages via string matching (entire file) — bytes for speed
            if b'"type":"user"' in line:
                if (
                    b"tool_result" not in line
                    and b"toolUseResult" not in line
                    and b"<local-command-caveat>" not in line
                    and b"<command-name>" not in line
                ):
                    user_msg_count += 1

            # Detect custom-title entries (can appear anywhere in the file)
            if b'"type":"custom-title"' in line:
                try:
                    ct_obj = json.loads(line)
                    custom_title = ct_obj.get("customTitle", "")
                except json.JSONDecodeError:
                    pass

            # Extract plan title as summary via string matching (avoid full JSON parse)
            if not summary and b'"planContent"' in line and b'"# ' in line:
                pc_idx = line.find(b'"planContent"')
                heading_idx = line.find(b'"# ', pc_idx)
                if heading_idx != -1:
                    start = heading_idx + 3  # skip '"# '
                    end = len(line)
                    for stop in [b"\\n", b'"']:
                        pos = line.find(stop, start)
                        if pos != -1 and pos < end:
                            end = pos
                    summary = line[start:end].strip().decode("utf-8", errors="replace")

            # Extract metadata from early lines only
            if i >= metadata_lines:
                continue

            try:
                obj = json.loads(raw_line.decode("utf-8", errors="replace"))
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
                from .reader import _extract_user_text

                msg = obj.get("message", {})
                content = _extract_user_text(msg.get("content", "") if isinstance(msg, dict) else "")
                if content and not is_noise_message(content):
                    first_user_message = content

    if first_timestamp is None and first_user_message is None:
        return None

    try:
        stat = jsonl_path.stat()
        file_modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
    except OSError:
        file_modified = last_timestamp or ""

    if custom_title:
        custom_title = _clean_custom_title(custom_title)

    project = resolve_project_name(cwd, project_name)
    title = _choose_title(custom_title or None, summary or None, first_user_message)

    return _make_session(
        session_id=session_id,
        file_path=str(jsonl_path),
        project=project,
        cwd=cwd,
        title=title,
        first_prompt=first_user_message or "",
        git_branch=git_branch,
        created=first_timestamp or "",
        modified=file_modified,
        message_count=user_msg_count,
        version=version,
        summary=summary,
        custom_title=custom_title,
    )


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

        # In-memory cache for index supplements: {index_path: (mtime, {sid: {summary, customTitle}})}
        self._index_supplements: dict[str, tuple[float, dict[str, dict[str, str]]]] = {}

        # In-memory TTL cache for scan_all results
        self._scan_all_ttl = 30.0
        self._scan_all_cached_at: float = 0.0
        self._scan_all_cached_result: list[dict[str, Any]] = []

    def clear_cache(self) -> None:
        """Clear all caches (disk, in-memory) so sessions are rescanned fresh."""
        if self._cache:
            self._cache.clear()
        self._index_supplements.clear()
        clear_project_name_cache()
        self._scan_all_cached_at = 0.0
        self._scan_all_cached_result = []
        from .opencode_store import clear_cache as _clear_opencode_cache

        _clear_opencode_cache()

    def scan_all(self) -> list[dict[str, Any]]:
        """Return all sessions sorted by modified descending (cached in-memory with TTL)."""
        now = time()
        if now - self._scan_all_cached_at < self._scan_all_ttl:
            return self._scan_all_cached_result

        if not self._base_dir.is_dir():
            return []

        sessions: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        live_paths: set[str] = set()

        for proj_entry in self._base_dir.iterdir():
            if not proj_entry.is_dir():
                continue
            dir_fallback = _project_name_from_dir(proj_entry.name)

            # Load index supplements (summary/customTitle) with mtime caching
            index_lookup = self._load_index_supplements(proj_entry)

            # Scan all JSONL files
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
                    result = _scan_session_jsonl(jsonl_file, dir_fallback)
                    if result is None:
                        continue
                    session = result
                    if self._cache:
                        self._cache.put(cache_key, mtime, session)

                # Merge index supplements (summary/customTitle)
                if index_lookup:
                    sid = session["session_id"]
                    supplement = index_lookup.get(sid)
                    if supplement:
                        summary = supplement.get("summary", "")
                        custom_title = supplement.get("customTitle", "")
                        if summary != session.get("summary", "") or custom_title != session.get("custom_title", ""):
                            session = dict(session)
                            session["summary"] = summary
                            session["custom_title"] = custom_title
                            session["title"] = _choose_title(
                                custom_title or None,
                                summary or None,
                                session.get("first_prompt"),
                            )
                            if self._cache:
                                self._cache.put(cache_key, mtime, session)

                if session["session_id"] not in seen_ids:
                    seen_ids.add(session["session_id"])
                    sessions.append(session)

        # Merge OpenCode sessions
        from .opencode_store import list_opencode_sessions

        for oc_session in list_opencode_sessions():
            cache_key = oc_session["file_path"]
            live_paths.add(cache_key)
            try:
                mtime = datetime.fromisoformat(oc_session["modified"].replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                mtime = 0.0

            cached = self._cache.get(cache_key) if self._cache else None
            if cached and cached[0] == mtime:
                session = cached[1]
            else:
                session = oc_session
                if self._cache:
                    self._cache.put(cache_key, mtime, session)

            if session["session_id"] not in seen_ids:
                seen_ids.add(session["session_id"])
                sessions.append(session)

        # Prune deleted entries and save
        if self._cache:
            self._cache.prune(live_paths)
            self._cache.save()

        # Sort by modified descending
        sessions.sort(key=lambda s: s.get("modified", ""), reverse=True)
        self._scan_all_cached_at = now
        self._scan_all_cached_result = sessions
        return sessions

    def _load_index_supplements(
        self,
        proj_dir: Path,
    ) -> dict[str, dict[str, str]]:
        """Load summary/customTitle from sessions-index.json with mtime caching."""
        index_path = proj_dir / "sessions-index.json"
        try:
            mtime = index_path.stat().st_mtime
        except OSError:
            return {}

        cache_key = str(index_path)
        cached = self._index_supplements.get(cache_key)
        if cached and cached[0] == mtime:
            return cached[1]

        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

        entries = data.get("entries", data) if isinstance(data, dict) else data
        if not isinstance(entries, list):
            return {}

        lookup: dict[str, dict[str, str]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            sid = entry.get("sessionId", "")
            if sid:
                lookup[sid] = {
                    "summary": entry.get("summary", ""),
                    "customTitle": entry.get("customTitle", ""),
                }

        self._index_supplements[cache_key] = (mtime, lookup)
        return lookup
