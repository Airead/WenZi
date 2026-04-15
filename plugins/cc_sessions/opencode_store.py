"""Read and export OpenCode sessions from SQLite storage."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .git_utils import clear_project_name_cache, resolve_project_name

logger = logging.getLogger(__name__)

SOURCE_OPENCODE = "opencode"
SOURCE_CC = "cc"

# Disk cache instance (lazily created)
_opencode_cache: Any = None

# In-memory TTL cache for list_opencode_sessions results
_LIST_OC_TTL = 5.0
_list_oc_cached_at: float = 0.0
_list_oc_cached_result: list[dict[str, Any]] = []


def _get_cache():
    """Return the disk cache for OpenCode sessions, creating it if needed."""
    global _opencode_cache
    if _opencode_cache is None:
        from wenzi.config import resolve_cache_dir

        from .cache import SessionCache

        cache_path = Path(resolve_cache_dir()) / "cc_sessions_opencode_cache.json"
        _opencode_cache = SessionCache(cache_path)
    return _opencode_cache


def clear_cache() -> None:
    """Clear the OpenCode session disk cache and in-memory caches."""
    global _opencode_cache, _list_oc_cached_at, _list_oc_cached_result
    clear_project_name_cache()
    if _opencode_cache is not None:
        _opencode_cache.clear()
        _opencode_cache = None
    _list_oc_cached_at = 0.0
    _list_oc_cached_result = []


def _db_path() -> Path:
    return Path.home() / ".local/share/opencode/opencode.db"


def _ms_to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=UTC).isoformat()


def _batch_counts_and_prompts(
    conn: sqlite3.Connection,
    session_ids: list[str],
) -> tuple[dict[str, int], dict[str, str]]:
    """Return (user_message_counts, first_prompts) for all *session_ids* in bulk."""
    counts: dict[str, int] = {}
    prompts: dict[str, str] = {}
    if not session_ids:
        return counts, prompts

    placeholders = ",".join("?" * len(session_ids))
    cur = conn.cursor()

    cur.execute(
        f"""
        SELECT session_id, COUNT(*) AS cnt
        FROM message
        WHERE session_id IN ({placeholders})
          AND json_extract(data, '$.role') = 'user'
        GROUP BY session_id
        """,
        session_ids,
    )
    for sid, cnt in cur:
        counts[sid] = cnt

    cur.execute(
        f"""
        SELECT m.session_id, m.id
        FROM message m
        WHERE m.session_id IN ({placeholders})
          AND json_extract(m.data, '$.role') = 'user'
          AND m.time_created = (
              SELECT MIN(time_created)
              FROM message m2
              WHERE m2.session_id = m.session_id
                AND json_extract(m2.data, '$.role') = 'user'
          )
        """,
        session_ids,
    )
    first_msg_map = {sid: msg_id for sid, msg_id in cur}

    if first_msg_map:
        msg_placeholders = ",".join("?" * len(first_msg_map))
        cur.execute(
            f"""
            SELECT p.message_id, p.data
            FROM part p
            WHERE p.message_id IN ({msg_placeholders})
              AND json_extract(p.data, '$.type') = 'text'
              AND p.time_created = (
                  SELECT MIN(time_created)
                  FROM part p2
                  WHERE p2.message_id = p.message_id
                    AND json_extract(p2.data, '$.type') = 'text'
              )
            """,
            list(first_msg_map.values()),
        )
        part_map: dict[str, str] = {}
        for msg_id, data in cur:
            if msg_id not in part_map:
                try:
                    part_data = json.loads(data)
                    part_map[msg_id] = part_data.get("text", "")[:200]
                except Exception:
                    part_map[msg_id] = ""
        for sid, msg_id in first_msg_map.items():
            prompts[sid] = part_map.get(msg_id, "")

    return counts, prompts


def list_opencode_sessions() -> list[dict[str, Any]]:
    """Return OpenCode sessions formatted for the chooser (cached on disk + memory TTL)."""
    from time import time

    global _list_oc_cached_at, _list_oc_cached_result

    now = time()
    if now - _list_oc_cached_at < _LIST_OC_TTL:
        return _list_oc_cached_result

    db = _db_path()
    if not db.exists():
        return []

    cache = _get_cache()
    sessions: list[dict[str, Any]] = []
    live_keys: set[str] = set()

    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, project_id, parent_id, slug, directory, title, version,
                   time_created, time_updated
            FROM session
            WHERE parent_id IS NULL
            ORDER BY time_updated DESC
            """
        )
        rows = cur.fetchall()

        # Separate cache hits from misses so we only run heavy queries for misses
        missing_rows = []
        for row in rows:
            sid = row["id"]
            mtime = row["time_updated"] / 1000.0
            cache_key = f"opencode://{sid}"
            live_keys.add(cache_key)
            cached = cache.get(cache_key)
            if not cached or cached[0] != mtime:
                missing_rows.append(row)

        missing_ids = [row["id"] for row in missing_rows]
        counts, prompts = _batch_counts_and_prompts(conn, missing_ids)

        for row in rows:
            sid = row["id"]
            cache_key = f"opencode://{sid}"
            mtime = row["time_updated"] / 1000.0

            cached = cache.get(cache_key)
            if cached and cached[0] == mtime:
                session = cached[1]
            else:
                cwd = row["directory"] or ""
                project = resolve_project_name(cwd, "") or Path(cwd).name or row["slug"]
                session = {
                    "session_id": sid,
                    "file_path": cache_key,
                    "project": project,
                    "cwd": cwd,
                    "title": row["title"] or row["slug"] or "Untitled",
                    "first_prompt": prompts.get(sid, ""),
                    "git_branch": "",
                    "created": _ms_to_iso(row["time_created"]),
                    "modified": _ms_to_iso(row["time_updated"]),
                    "message_count": counts.get(sid, 0),
                    "version": row["version"] or "",
                    "summary": "",
                    "custom_title": "",
                    "source": SOURCE_OPENCODE,
                }
                cache.put(cache_key, mtime, session)

            sessions.append(session)

    cache.prune(live_keys)
    cache.save()
    _list_oc_cached_at = now
    _list_oc_cached_result = sessions
    return sessions


def _get_session_parts(
    conn: sqlite3.Connection,
    session_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Return {message_id: [part_data, ...]} for all parts in a session."""
    cur = conn.cursor()
    cur.execute(
        "SELECT message_id, data FROM part WHERE session_id = ? ORDER BY time_created",
        (session_id,),
    )
    parts_by_msg: dict[str, list[dict[str, Any]]] = {}
    for msg_id, data in cur:
        parts_by_msg.setdefault(msg_id, []).append(json.loads(data))
    return parts_by_msg


_SUBAGENT_TITLE_RE = re.compile(r"^(.*?)\s+\(@(\w+)\s+subagent\)$")


def _parse_subagent_title(title: str) -> tuple[str, str]:
    """Extract description and agent_type from an OpenCode subagent title."""
    m = _SUBAGENT_TITLE_RE.match(title)
    if m:
        return m.group(1), m.group(2)
    return title, ""


def _get_first_assistant_model(conn: sqlite3.Connection, session_id: str) -> str:
    """Return modelID from the first assistant message in a session."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT json_extract(data, '$.modelID')
        FROM message
        WHERE session_id = ? AND json_extract(data, '$.role') = 'assistant'
        LIMIT 1
        """,
        (session_id,),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] else ""


def list_opencode_subagents(parent_session_id: str) -> list[dict[str, Any]]:
    """Return subagents for a given OpenCode parent session."""
    db = _db_path()
    if not db.exists():
        return []

    results: list[dict[str, Any]] = []
    with sqlite3.connect(str(db)) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, version FROM session WHERE parent_id = ?",
            (parent_session_id,),
        )
        for sid, title, version in cur:
            description, agent_type = _parse_subagent_title(title or "")
            model = _get_first_assistant_model(conn, sid)
            results.append(
                {
                    "agent_id": sid,
                    "description": description,
                    "agent_type": agent_type,
                    "model": model,
                    "version": version or "",
                }
            )
    return results


def check_opencode_subagent_exists(parent_session_id: str, agent_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Check which OpenCode subagent sessions exist and extract their model."""
    result: dict[str, dict[str, Any]] = {}
    if not agent_ids:
        return result

    db = _db_path()
    if not db.exists():
        for aid in agent_ids:
            result[aid] = {"exists": False, "model": ""}
        return result

    placeholders = ",".join("?" * len(agent_ids))
    with sqlite3.connect(str(db)) as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT id FROM session WHERE parent_id = ? AND id IN ({placeholders})",
            [parent_session_id] + agent_ids,
        )
        existing = {row[0] for row in cur.fetchall()}
        for aid in agent_ids:
            model = ""
            if aid in existing:
                model = _get_first_assistant_model(conn, aid)
            result[aid] = {"exists": aid in existing, "model": model}
    return result


def export_opencode_session(session_id: str, out_path: Path) -> None:
    """Export an OpenCode session to a CC-compatible JSONL file."""
    db = _db_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, data, time_created FROM message
            WHERE session_id = ?
            ORDER BY time_created
            """,
            (session_id,),
        )
        messages = cur.fetchall()
        parts_by_msg = _get_session_parts(conn, session_id)

        with out_path.open("w", encoding="utf-8") as fh:
            for msg in messages:
                msg_data = json.loads(msg["data"])
                line = _convert_message(msg_data, msg["time_created"], parts_by_msg.get(msg["id"], []))
                if line:
                    fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    logger.info("Exported OpenCode session %s to %s", session_id, out_path)


def _make_call_id() -> str:
    """Return a deterministic pseudo-unique call id."""
    import uuid

    return "oc_" + uuid.uuid4().hex[:16]


_TOOL_NAME_MAP: dict[str, str] = {
    "bash": "Bash",
    "read": "Read",
    "glob": "Glob",
    "grep": "Grep",
    "edit": "Edit",
    "write": "Write",
    "webfetch": "WebFetch",
    "websearch": "WebSearch",
    "question": "Question",
    "todowrite": "TodoWrite",
    "codesearch": "CodeSearch",
    "skill": "Skill",
    "task": "Agent",
}


def _map_tool_name(raw: str) -> str:
    """Map OpenCode tool name to CC-compatible tool name."""
    return _TOOL_NAME_MAP.get(raw, raw.capitalize())


def _build_tool_input(tool_name: str, raw_input: dict[str, Any], title: str) -> dict[str, Any]:
    """Build tool_use input dict compatible with the viewer."""
    inp = dict(raw_input) if isinstance(raw_input, dict) else {}
    if tool_name == "Agent" and "subagent_type" not in inp:
        # task tool uses 'subagent_type' key; promote it if missing
        inp.setdefault("subagent_type", inp.get("subagent_type", "general-purpose"))
    if title and "description" not in inp:
        inp["description"] = title
    return inp


def _convert_message(
    msg_data: dict[str, Any],
    ts_ms: int,
    parts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Convert a single OpenCode message (+ parts) to CC JSONL format."""
    role = msg_data.get("role")
    if role not in ("user", "assistant"):
        return None

    timestamp = _ms_to_iso(ts_ms)
    path_info = msg_data.get("path", {})
    cwd = path_info.get("cwd", "")

    content_parts: list[dict[str, Any]] = []
    i = 0
    while i < len(parts):
        p = parts[i]
        pt = p.get("type")
        if pt == "text":
            text = p.get("text", "")
            # Skip synthetic placeholder text injected by OpenCode after tool execution
            if text and not p.get("synthetic"):
                content_parts.append({"type": "text", "text": text})
        elif pt == "reasoning":
            text = p.get("text", "")
            if text:
                content_parts.append({"type": "thinking", "thinking": text})
        elif pt == "tool":
            tool_name = _map_tool_name(p.get("tool", "tool"))
            call_id = p.get("callID") or _make_call_id()
            state = p.get("state", {})
            inp = _build_tool_input(tool_name, state.get("input", {}), state.get("title", ""))
            out = state.get("output", "")
            content_parts.append(
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": tool_name,
                    "input": inp,
                }
            )
            content_parts.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": out,
                }
            )
        elif pt == "file":
            filename = p.get("filename", "")
            if filename:
                content_parts.append({"type": "text", "text": f"@{filename}"})
        elif pt == "patch":
            files = p.get("files", [])
            if files:
                content_parts.append({"type": "text", "text": f"[Patch] {', '.join(files)}"})
        elif pt in ("step-start", "step-finish", "subtask", "compaction"):
            pass
        else:
            text = p.get("text")
            if text:
                content_parts.append({"type": "text", "text": text})
        i += 1

    if role == "user":
        user_text = " ".join(p["text"] for p in content_parts if p.get("text"))
        if not user_text:
            return None
        return {
            "type": "user",
            "timestamp": timestamp,
            "cwd": cwd,
            "message": {"content": user_text},
        }

    usage = msg_data.get("tokens", {})
    model = msg_data.get("modelID", "")
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "cwd": cwd,
        "version": msg_data.get("version", ""),
        "message": {
            "content": content_parts if content_parts else [{"type": "text", "text": ""}],
            "usage": {
                "input_tokens": usage.get("input", 0) or 0,
                "output_tokens": usage.get("output", 0) or 0,
            },
            "model": model,
        },
    }
