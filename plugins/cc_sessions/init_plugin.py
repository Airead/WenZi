"""Claude Code Sessions — launcher source and viewer integration."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _parse_query(query: str) -> Tuple[Optional[str], str]:
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
        now = datetime.now(timezone.utc)
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


def _filter_sessions(
    sessions: List[Dict[str, Any]],
    project_filter: Optional[str],
    query: str,
) -> List[Dict[str, Any]]:
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
            matched, score = fuzzy_match(query, s["title"])
            if matched:
                scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        result = [s for _, s in scored]

    return result


def register(wz) -> None:
    """Register the cc-sessions source with the chooser."""
    from .scanner import SessionScanner

    scanner = SessionScanner()
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    viewer_html_path = os.path.join(plugin_dir, "viewer.html")

    def _open_viewer(session: Dict[str, Any]) -> None:
        """Open the session viewer panel using pull model."""
        try:
            with open(viewer_html_path, encoding="utf-8") as f:
                html = f.read()
        except OSError:
            logger.exception("Failed to read viewer.html")
            return

        panel = wz.ui.webview_panel(
            title=session["title"],
            html=html,
            width=900,
            height=700,
            resizable=True,
            allowed_read_paths=[
                os.path.expanduser("~/.claude/"),
                plugin_dir,
            ],
        )

        @panel.handle("get_session_info")
        def get_session_info(_data):
            return {
                "file": session["file_path"],
                "project": session["project"],
                "cwd": session["cwd"],
                "session_id": session["session_id"],
                "git_branch": session.get("git_branch", ""),
                "version": session.get("version", ""),
            }

        from PyObjCTools import AppHelper

        AppHelper.callAfter(panel.show)

    def _copy_resume_command(session: Dict[str, Any]) -> None:
        """Copy cd + claude --resume command to clipboard."""
        from wenzi.scripting.sources import copy_to_clipboard

        cmd = f"cd {session['cwd']} && claude --resume {session['session_id']}"
        copy_to_clipboard(cmd)

    def _make_preview(session: Dict[str, Any]) -> dict:
        """Build a text preview for the launcher right panel."""
        lines = [f"Project: {session['project']}"]
        if session.get("git_branch"):
            lines.append(f"Branch: {session['git_branch']}")
        if session.get("version"):
            lines.append(f"Claude: {session['version']}")
        if session.get("message_count"):
            lines.append(f"Messages: {session['message_count']}")
        lines.append("")
        lines.append(session.get("first_prompt", ""))
        return {"type": "text", "content": "\n".join(lines)}

    @wz.chooser.source(
        "cc-sessions",
        prefix="cc",
        priority=5,
        description="Browse Claude Code sessions",
        action_hints={
            "enter": "View",
            "cmd_enter": "Copy resume command",
        },
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

            items.append({
                "title": s["title"],
                "subtitle": " \u00b7 ".join(subtitle_parts),
                "icon": "\U0001f916",
                "item_id": f"cc-{s['session_id']}",
                "action": lambda sess=s: _open_viewer(sess),
                "secondary_action": lambda sess=s: _copy_resume_command(sess),
                "preview": _make_preview(s),
            })
        return items
