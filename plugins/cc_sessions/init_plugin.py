"""Claude Code Sessions — launcher source and viewer integration."""

from __future__ import annotations

import logging
import os
import shlex
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


def _parse_query(query: str) -> Tuple[str | None, str]:
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
    sessions: list[Dict[str, Any]],
    project_filter: str | None,
    query: str,
) -> list[Dict[str, Any]]:
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
            search_text = (
                f"{s['title']} {s['project']} {s.get('git_branch', '')}"
                f" {s.get('summary', '')} {s.get('first_prompt', '')[:200]}"
            )
            matched, score = fuzzy_match(query, search_text)
            if matched:
                scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        result = [s for _, s in scored]

    return result


def register(wz) -> None:
    """Register the cc-sessions source with the chooser."""
    from .scanner import SessionScanner

    scanner = SessionScanner()
    from .identicon import generate as generate_identicon

    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    viewer_html_path = os.path.join(plugin_dir, "viewer.html")

    def _open_viewer(session: Dict[str, Any]) -> None:
        """Open the session viewer panel using pull model."""
        logger.info("Opening viewer for session: %s, file: %s",
                     session["session_id"], session["file_path"])
        panel = wz.ui.webview_panel(
            title=session["title"],
            html_file=viewer_html_path,
            width=900,
            height=700,
            resizable=True,
            allowed_read_paths=[
                os.path.expanduser("~/.claude/"),
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

        def _copy_text(text: str) -> None:
            from wenzi.scripting.sources import copy_to_clipboard

            copy_to_clipboard(text)

        panel.on("copy_resume", lambda data: _copy_text(data.get("text", "")))

        panel.show()

    def _delete_session(session: Dict[str, Any]) -> None:
        """Move the session JSONL file to macOS Trash."""
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
            from PyObjCTools import AppHelper

            home = os.path.expanduser("~")
            display = file_path.replace(home, "~")

            def _hud():
                from wenzi.ui.hud import show_hud
                show_hud(f"Trashed\n{display}")

            AppHelper.callAfter(_hud)
        except Exception:
            logger.debug("HUD notification failed", exc_info=True)

    def _copy_resume_command(session: Dict[str, Any]) -> None:
        """Copy cd + claude --resume command to clipboard."""
        from wenzi.scripting.sources import copy_to_clipboard

        cwd = shlex.quote(session["cwd"])
        cmd = f"cd {cwd} && claude --resume {session['session_id']}"
        copy_to_clipboard(cmd)

    def _make_preview(session: Dict[str, Any]):
        """Return a lazy callable that builds HTML preview on demand."""
        def _load():
            from pathlib import Path
            from .reader import read_session_detail
            from .preview import build_preview_html

            file_path = session.get("file_path", "")
            detail = read_session_detail(Path(file_path)) if file_path else {
                "turns": [], "total_input_tokens": 0, "total_output_tokens": 0,
            }
            html = build_preview_html(session, detail)
            return {"type": "html", "content": html}
        return _load

    @wz.chooser.source(
        "cc-sessions",
        prefix="cc",
        priority=5,
        description="Browse Claude Code sessions",
        action_hints={
            "enter": "View",
            "cmd_enter": "Copy resume command",
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
            items.append({
                "title": s["title"],
                "subtitle": " \u00b7 ".join(subtitle_parts),
                "icon": generate_identicon(s["project"]),
                "icon_badge": str(msg_count) if msg_count else "",
                "item_id": f"cc-{s['session_id']}",
                "action": lambda sess=s: _open_viewer(sess),
                "secondary_action": lambda sess=s: _copy_resume_command(sess),
                "preview": _make_preview(s),
                "delete_action": lambda sess=s: _delete_session(sess),
                "confirm_delete": True,
            })
        return items
