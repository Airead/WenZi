"""Build HTML preview for session chooser panel."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any


def build_preview_html(session: dict[str, Any], detail: dict[str, Any]) -> str:
    """Build an HTML string for the chooser preview panel."""
    parts: list[str] = []

    # Title section
    title = session.get("custom_title") or session.get("summary") or session.get("first_prompt", "")
    if title:
        parts.append(f'<div style="font-weight:600;font-size:13px;margin-bottom:8px">{escape(title)}</div>')

    # Metadata tags
    tags = _build_tags(session, detail)
    if tags:
        parts.append(f'<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px">{tags}</div>')

    # Time info
    time_info = _build_time_info(session)
    if time_info:
        parts.append(f'<div style="font-size:11px;color:var(--secondary);margin-bottom:10px">{time_info}</div>')

    # Conversation turns
    turns = detail.get("turns", [])
    from .opencode_store import SOURCE_OPENCODE

    assistant_label = "OPENCODE" if session.get("source") == SOURCE_OPENCODE else "CLAUDE"
    if turns:
        parts.append('<div style="border-top:1px solid var(--border);padding-top:8px">')
        for turn in turns:
            role = turn["role"]
            text = escape(turn["text"])
            truncate = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
            if role == "user":
                parts.append(
                    f'<div style="margin-bottom:6px">'
                    f'<span style="font-size:10px;font-weight:600;color:var(--accent)">'
                    f"USER</span>"
                    f'<div style="font-size:12px;margin-top:2px;{truncate}">{text}</div>'
                    f"</div>"
                )
            else:
                parts.append(
                    f'<div style="margin-bottom:6px">'
                    f'<span style="font-size:10px;font-weight:600;color:var(--secondary)">'
                    f"{assistant_label}</span>"
                    f'<div style="font-size:12px;margin-top:2px;color:var(--secondary);{truncate}">'
                    f"{text}</div>"
                    f"</div>"
                )
        parts.append("</div>")

    return "\n".join(parts)


def _build_tags(session: dict[str, Any], detail: dict[str, Any]) -> str:
    """Build pill-shaped metadata tags."""
    tag_style = "display:inline-block;font-size:10px;padding:2px 6px;border-radius:4px;background:var(--item-hover);color:var(--text)"
    tags: list[str] = []

    project = session.get("project", "")
    if project:
        tags.append(f'<span style="{tag_style}">{escape(project)}</span>')

    branch = session.get("git_branch", "")
    if branch:
        tags.append(f'<span style="{tag_style}">{escape(branch)}</span>')

    version = session.get("version", "")
    if version:
        from .opencode_store import SOURCE_OPENCODE

        label = "OpenCode" if session.get("source") == SOURCE_OPENCODE else "Claude"
        tags.append(f'<span style="{tag_style}">{label} {escape(version)}</span>')

    count = session.get("message_count", 0)
    if count:
        tags.append(f'<span style="{tag_style}">{count} msgs</span>')

    total_in = detail.get("total_input_tokens", 0)
    total_out = detail.get("total_output_tokens", 0)
    if total_in:
        tags.append(f'<span style="{tag_style}">{total_in:,} in</span>')
    if total_out:
        tags.append(f'<span style="{tag_style}">{total_out:,} out</span>')

    return "".join(tags)


def _build_time_info(session: dict[str, Any]) -> str:
    """Build time info line: created, modified, duration."""
    parts: list[str] = []
    created = session.get("created", "")
    modified = session.get("modified", "")

    if created:
        parts.append(f"Created: {_format_time(created)}")
    if modified:
        parts.append(f"Modified: {_format_time(modified)}")

    duration = _calc_duration(created, modified)
    if duration:
        parts.append(f"Duration: {duration}")

    return " \u00b7 ".join(parts)


def _format_time(iso_str: str) -> str:
    """Format ISO timestamp to readable local time."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local = dt.astimezone()
        return local.strftime("%m/%d %H:%M")
    except (ValueError, TypeError):
        return iso_str


def _calc_duration(created: str, modified: str) -> str:
    """Calculate duration between created and modified timestamps."""
    if not created or not modified:
        return ""
    try:
        c = datetime.fromisoformat(created.replace("Z", "+00:00"))
        m = datetime.fromisoformat(modified.replace("Z", "+00:00"))
        delta = m - c
        total_minutes = int(delta.total_seconds()) // 60
        if total_minutes < 1:
            return "<1m"
        hours, minutes = divmod(total_minutes, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except (ValueError, TypeError):
        return ""
