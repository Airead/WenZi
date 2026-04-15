"""Claude Code Sessions — launcher source and viewer integration."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _parse_query(query: str) -> tuple[str | None, str]:
    """Parse '@project rest' syntax. Returns (project_filter, remaining_query)."""
    query = query.strip()
    if query.startswith("@"):
        parts = query[1:].split(None, 1)
        project = parts[0] if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        return project, rest
    return None, query


def _time_ago(iso_timestamp: str) -> str:
    """Convert an ISO timestamp to a human-readable relative time."""
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} min ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = hours // 24
        if days < 30:
            return f"{days} day{'s' if days != 1 else ''} ago"
        months = days // 30
        return f"{months} month{'s' if months != 1 else ''} ago"
    except (ValueError, TypeError):
        return ""


def _get_subagents_dir(root_session_path: str) -> str:
    """Return the subagents directory for a given root session path."""
    root_dir = os.path.dirname(root_session_path)
    session_id = os.path.splitext(os.path.basename(root_session_path))[0]
    return os.path.join(root_dir, session_id, "subagents")


def _resolve_subagent_path(root_session_path: str, agent_id: str) -> str:
    """Resolve subagent JSONL path from root session path and agent ID."""
    return os.path.join(_get_subagents_dir(root_session_path), f"agent-{agent_id}.jsonl")


def _check_subagent_exists(
    root_session_path: str,
    agent_ids: list,
) -> dict:
    """Check which subagent JSONL files exist and extract their model.

    Returns ``{agent_id: {"exists": bool, "model": str}}``.
    """
    result = {}
    for aid in agent_ids:
        path = _resolve_subagent_path(root_session_path, aid)
        if os.path.isfile(path):
            meta = _parse_subagent_meta(path)
            result[aid] = {"exists": True, "model": meta.get("model", "")}
        else:
            result[aid] = {"exists": False, "model": ""}
    return result


def _list_subagents(root_session_path: str) -> list[dict]:
    """List all subagents for a session by reading subagent meta files.

    Returns a list of dicts with keys: agent_id, description, agent_type, model.
    """
    subagents_dir = _get_subagents_dir(root_session_path)
    if not os.path.isdir(subagents_dir):
        return []

    results = []
    for entry in os.listdir(subagents_dir):
        if not entry.startswith("agent-") or not entry.endswith(".meta.json"):
            continue
        agent_id = entry.removeprefix("agent-").removesuffix(".meta.json")
        meta_path = os.path.join(subagents_dir, entry)
        jsonl_path = os.path.join(subagents_dir, f"agent-{agent_id}.jsonl")
        if not os.path.isfile(jsonl_path):
            continue
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            meta = {}
        model = meta.get("model", "")
        if not model:
            model = _parse_subagent_meta(jsonl_path).get("model", "")
        results.append(
            {
                "agent_id": agent_id,
                "description": meta.get("description", ""),
                "agent_type": meta.get("agentType", ""),
                "model": model,
            }
        )
    return results


def _parse_subagent_meta(jsonl_path: str) -> dict:
    """Extract basic metadata from the first few lines of a subagent JSONL."""
    meta: dict = {
        "cwd": "",
        "version": "",
        "git_branch": "",
        "project": "",
        "model": "",
    }
    try:
        with open(jsonl_path) as f:
            for i, line in enumerate(f):
                if i >= 20:
                    break
                try:
                    msg = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not meta["cwd"] and msg.get("cwd"):
                    meta["cwd"] = msg["cwd"]
                if not meta["version"] and msg.get("version"):
                    meta["version"] = msg["version"]
                if not meta["git_branch"] and msg.get("git_branch"):
                    meta["git_branch"] = msg["git_branch"]
                if not meta["project"] and msg.get("project"):
                    meta["project"] = msg["project"]
                if not meta["model"]:
                    m = msg.get("message", {})
                    if isinstance(m, dict) and m.get("model"):
                        meta["model"] = m["model"]
    except OSError:
        pass
    return meta


def _filter_sessions(
    sessions: list[dict[str, Any]],
    project_filter: str | None,
    query: str,
) -> list[dict[str, Any]]:
    """Filter sessions by project name and/or title fuzzy match."""
    from wenzi.scripting.sources import fuzzy_match

    result = sessions

    if project_filter:
        filtered = []
        for s in result:
            matched, _ = fuzzy_match(project_filter, s["project"])
            if matched:
                filtered.append(s)
        result = filtered

    if query.strip():
        scored = []
        for s in result:
            search_text = f"{s['title']} {s['project']} {s.get('git_branch', '')} {s.get('summary', '')} {s.get('first_prompt', '')[:200]}"
            matched, score = fuzzy_match(query, search_text)
            if matched:
                scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        result = [s for _, s in scored]

    return result


_I18N = {
    "action.view": {"en": "View", "zh": "查看"},
    "action.path": {"en": "Path", "zh": "路径"},
}


def _t(key: str) -> str:
    """Translate a plugin-local i18n key."""
    from wenzi.i18n import get_locale

    locale = get_locale()
    entry = _I18N.get(key, {})
    return entry.get(locale, entry.get("en", key))


def register(wz) -> None:
    """Register the cc-sessions source with the chooser."""
    from .scanner import SessionScanner

    scanner = SessionScanner()

    def _clear_cache(_args: str) -> None:
        scanner.clear_cache()
        try:
            wz.alert("Session cache cleared")
        except Exception:
            logger.debug("Alert notification failed", exc_info=True)

    wz.chooser.register_command(
        name="cc-sessions:clear-cache",
        title="CC Sessions: Clear Cache",
        subtitle="Remove cached session metadata and rescan",
        action=_clear_cache,
    )

    from .identicon import generate as generate_identicon

    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    viewer_html_path = os.path.join(plugin_dir, "viewer.html")

    def _copy_text(text: str) -> None:
        from wenzi.scripting.sources import copy_to_clipboard

        copy_to_clipboard(text)

    def _ensure_opencode_jsonl(session: dict[str, Any], *, force: bool = False) -> Path:
        """Return a temp JSONL path for an OpenCode session, exporting if needed."""
        from wenzi.config import resolve_cache_dir

        from .opencode_store import export_opencode_session

        cache_dir = Path(resolve_cache_dir()) / "cc_sessions_opencode"
        temp_path = cache_dir / f"{session['session_id']}.jsonl"
        if force or not temp_path.exists():
            export_opencode_session(session["session_id"], temp_path)
        return temp_path

    def _start_auto_reload(panel, file_path: str) -> None:
        """Start auto-reload watcher that pushes file changes to the panel."""
        from .auto_reload import AutoReloadWatcher

        watcher = AutoReloadWatcher(
            file_path,
            on_new_lines=lambda lines: panel.send(
                "reload_update",
                {"lines": lines},
            ),
        )
        watcher.start()
        panel.on_close(watcher.request_stop)

    def _register_subagent_handlers(panel) -> None:
        """Register shared subagent bridge handlers on a viewer panel."""

        @panel.handle("check_subagent_exists")
        def check_subagent_exists(data):
            root_path = data.get("root_session_path", "")
            agent_ids = data.get("agent_ids", [])
            return _check_subagent_exists(root_path, agent_ids)

        @panel.handle("list_subagents")
        def list_subagents(data):
            root_path = data.get("root_session_path", "")
            return _list_subagents(root_path)

        @panel.handle("open_subagent")
        def open_subagent(data):
            _open_subagent_viewer(
                data.get("root_session_path", ""),
                data.get("parent_file_path", ""),
                data.get("agent_id", ""),
                data.get("description", ""),
                parent_panel=panel,
            )

    def _register_opencode_subagent_handlers(panel, session_id: str) -> None:
        """Register OpenCode subagent bridge handlers on a viewer panel."""
        from .opencode_store import (
            check_opencode_subagent_exists,
            list_opencode_subagents,
        )

        @panel.handle("check_subagent_exists")
        def check_subagent_exists(data):
            agent_ids = data.get("agent_ids", [])
            return check_opencode_subagent_exists(session_id, agent_ids)

        @panel.handle("list_subagents")
        def list_subagents(data):
            return list_opencode_subagents(session_id)

        @panel.handle("open_subagent")
        def open_subagent(data):
            _open_opencode_subagent_viewer(
                session_id,
                data.get("agent_id", ""),
                data.get("description", ""),
                parent_panel=panel,
            )

    def _open_viewer(session: dict[str, Any]) -> None:
        """Open the session viewer panel using pull model."""
        from .opencode_store import SOURCE_CC, SOURCE_OPENCODE

        source = session.get("source", SOURCE_CC)
        if source == SOURCE_OPENCODE:
            temp_path = _ensure_opencode_jsonl(session, force=True)
            display_session = dict(session)
            display_session["file_path"] = str(temp_path)
            read_paths = [str(temp_path.parent), os.path.expanduser("~/.claude/")]
        else:
            display_session = session
            temp_path = None
            read_paths = [os.path.expanduser("~/.claude/")]

        logger.info("Opening viewer for session: %s, file: %s", display_session["session_id"], display_session["file_path"])
        panel = wz.ui.webview_panel(
            title=display_session["title"],
            html_file=viewer_html_path,
            width=900,
            height=700,
            resizable=True,
            titlebar_hidden=True,
            floating=False,
            allowed_read_paths=read_paths,
        )

        @panel.handle("get_session_info")
        def get_session_info(_data):
            return {
                "file": display_session["file_path"],
                "project": display_session["project"],
                "cwd": display_session["cwd"],
                "session_id": display_session["session_id"],
                "title": display_session["title"],
                "git_branch": display_session.get("git_branch", ""),
                "version": display_session.get("version", ""),
                "root_session_path": display_session["file_path"],
                "is_subagent": False,
                "source": display_session.get("source", SOURCE_CC),
            }

        panel.on("copy_resume", lambda data: _copy_text(data.get("text", "")))

        if source == SOURCE_CC:
            _register_subagent_handlers(panel)
            _start_auto_reload(panel, display_session["file_path"])
        elif source == SOURCE_OPENCODE:
            _register_opencode_subagent_handlers(panel, display_session["session_id"])
            _start_auto_reload(panel, display_session["file_path"])
        panel.show()

    def _open_opencode_subagent_viewer(
        parent_session_id: str,
        agent_id: str,
        description: str,
        parent_panel=None,
    ) -> None:
        """Open a viewer panel for an OpenCode subagent session."""
        from wenzi.config import resolve_cache_dir

        from .opencode_store import SOURCE_OPENCODE, export_opencode_session

        cache_dir = Path(resolve_cache_dir()) / "cc_sessions_opencode" / "subagents"
        cache_dir.mkdir(parents=True, exist_ok=True)
        subagent_path = cache_dir / f"{agent_id}.jsonl"
        export_opencode_session(agent_id, subagent_path)
        meta = _parse_subagent_meta(str(subagent_path))

        panel = wz.ui.webview_panel(
            title=f"Subagent: {description}",
            html_file=viewer_html_path,
            width=900,
            height=700,
            resizable=True,
            titlebar_hidden=True,
            floating=False,
            allowed_read_paths=[
                str(subagent_path.parent),
                os.path.expanduser("~/.claude/"),
            ],
        )

        @panel.handle("get_session_info")
        def get_session_info(_data):
            return {
                "file": str(subagent_path),
                "project": meta.get("project", ""),
                "cwd": meta.get("cwd", ""),
                "session_id": agent_id,
                "title": f"Subagent: {description}",
                "git_branch": meta.get("git_branch", ""),
                "version": meta.get("version", ""),
                "root_session_path": parent_session_id,
                "parent_file_path": str(subagent_path),
                "is_subagent": True,
                "source": SOURCE_OPENCODE,
            }

        @panel.handle("open_parent_session")
        def open_parent(_data):
            panel.close()

        def _bring_parent_to_front():
            if parent_panel is not None:
                parent_panel.show()

        panel.on("copy_resume", lambda data: _copy_text(data.get("text", "")))
        panel.on_close(lambda: _bring_parent_to_front())
        _register_opencode_subagent_handlers(panel, agent_id)
        _start_auto_reload(panel, str(subagent_path))
        panel.show()

    def _open_subagent_viewer(
        root_session_path: str,
        parent_file_path: str,
        agent_id: str,
        description: str,
        parent_panel=None,
    ) -> None:
        """Open a viewer panel for a subagent session."""
        subagent_path = _resolve_subagent_path(root_session_path, agent_id)
        if not os.path.isfile(subagent_path):
            logger.warning("Subagent file not found: %s", subagent_path)
            return
        meta = _parse_subagent_meta(subagent_path)

        session_id = f"agent-{agent_id}"

        panel = wz.ui.webview_panel(
            title=f"Subagent: {description}",
            html_file=viewer_html_path,
            width=900,
            height=700,
            resizable=True,
            titlebar_hidden=True,
            floating=False,
            allowed_read_paths=[
                os.path.expanduser("~/.claude/"),
            ],
        )

        @panel.handle("get_session_info")
        def get_session_info(_data):
            return {
                "file": subagent_path,
                "project": meta.get("project", ""),
                "cwd": meta.get("cwd", ""),
                "session_id": session_id,
                "title": f"Subagent: {description}",
                "git_branch": meta.get("git_branch", ""),
                "version": meta.get("version", ""),
                "root_session_path": root_session_path,
                "parent_file_path": parent_file_path,
                "is_subagent": True,
            }

        @panel.handle("open_parent_session")
        def open_parent(_data):
            panel.close()

        def _bring_parent_to_front():
            if parent_panel is not None:
                parent_panel.show()

        panel.on("copy_resume", lambda data: _copy_text(data.get("text", "")))
        panel.on_close(lambda: _bring_parent_to_front())
        _register_subagent_handlers(panel)
        _start_auto_reload(panel, subagent_path)
        panel.show()

    def _delete_session(session: dict[str, Any]) -> None:
        """Move the session JSONL file to macOS Trash."""
        from .opencode_store import SOURCE_OPENCODE

        if session.get("source") == SOURCE_OPENCODE:
            try:
                wz.alert("OpenCode sessions cannot be deleted from this plugin.")
            except Exception:
                pass
            return
        file_path = session.get("file_path", "")
        if not file_path:
            return
        try:
            from Foundation import NSURL, NSFileManager

            url = NSURL.fileURLWithPath_(file_path)
            fm = NSFileManager.defaultManager()
            ok, _, err = fm.trashItemAtURL_resultingItemURL_error_(url, None, None)
            if not ok:
                raise OSError(str(err) if err else "trashItemAtURL failed")
        except ImportError:
            try:
                os.remove(file_path)
            except OSError:
                logger.warning("Failed to delete %s", file_path, exc_info=True)
                return

        try:
            home = os.path.expanduser("~")
            display = file_path.replace(home, "~")
            wz.alert(f"Trashed\n{display}")
        except Exception:
            logger.debug("Alert notification failed", exc_info=True)

    def _copy_full_path(session: dict[str, Any]) -> None:
        """Copy session JSONL file path to clipboard."""
        from wenzi.scripting.sources import copy_to_clipboard

        copy_to_clipboard(session["file_path"])

    def _make_preview(session: dict[str, Any]):
        """Return a lazy callable that builds HTML preview on demand."""

        def _load():
            from pathlib import Path

            from .opencode_store import SOURCE_OPENCODE
            from .preview import build_preview_html
            from .reader import read_session_detail

            file_path = session.get("file_path", "")
            if session.get("source") == SOURCE_OPENCODE and file_path:
                temp_path = _ensure_opencode_jsonl(session)
                file_path = str(temp_path)

            detail = (
                read_session_detail(Path(file_path))
                if file_path
                else {
                    "turns": [],
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                }
            )
            html = build_preview_html(session, detail)
            return {"type": "html", "content": html}

        return _load

    @wz.chooser.source(
        "cc-sessions",
        prefix="cc",
        priority=5,
        description="Browse Claude Code sessions",
        action_hints={
            "enter": _t("action.view"),
            "cmd_enter": _t("action.path"),
        },
        show_preview=True,
    )
    def search(query: str) -> list:
        sessions = scanner.scan_all()
        project_filter, text_query = _parse_query(query)
        filtered = _filter_sessions(sessions, project_filter, text_query)

        items = []
        for s in filtered[:50]:
            time_str = _time_ago(s.get("modified", ""))
            subtitle_parts = [s["project"]]
            if time_str:
                subtitle_parts.append(time_str)
            if s.get("git_branch"):
                subtitle_parts.append(s["git_branch"])

            msg_count = s.get("message_count", 0)
            prefix = "oc" if s.get("source") == "opencode" else "cc"
            items.append(
                {
                    "title": s["title"],
                    "subtitle": " · ".join(subtitle_parts),
                    "icon": generate_identicon(s["project"]),
                    "icon_badge": str(msg_count) if msg_count else "",
                    "item_id": f"{prefix}-{s['session_id']}",
                    "action": lambda sess=s: _open_viewer(sess),
                    "secondary_action": lambda sess=s: _copy_full_path(sess),
                    "preview": _make_preview(s),
                    "delete_action": lambda sess=s: _delete_session(sess),
                    "confirm_delete": True,
                }
            )
        return items
