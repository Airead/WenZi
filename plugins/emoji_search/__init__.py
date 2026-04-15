"""Emoji search plugin for WenZi launcher."""

from __future__ import annotations

import base64
import html
import json
import logging
import os
from typing import Any

from wenzi.scripting.sources import fuzzy_match_fields

logger = logging.getLogger(__name__)

_MAX_RESULTS = 30
_MAX_GROUP_RESULTS = 200
_DATA_FILE = "emoji-tree.json"


def _load_emoji_data() -> tuple[
    list[dict[str, str]],
    dict[str, list[dict[str, str]]],
    list[dict[str, Any]],
]:
    """Load emoji-tree.json and return (records, group_map, groups).

    *records* is a flat list of all emoji dicts.
    *group_map* maps lowercase group/subgroup names (en/zh) to the emoji list.
    *groups* is a list of top-level group metadata dicts.
    """
    path = os.path.join(os.path.dirname(__file__), _DATA_FILE)
    if not os.path.isfile(path):
        logger.error("Emoji data file not found: %s", path)
        return [], {}, []

    try:
        with open(path, encoding="utf-8") as fh:
            tree = json.load(fh)
    except Exception:
        logger.exception("Failed to parse %s", path)
        return [], {}, []

    records: list[dict[str, str]] = []
    group_map: dict[str, list[dict[str, str]]] = {}
    groups: list[dict[str, Any]] = []
    group_seen: set[str] = set()

    for group in tree:
        group_en = group.get("name", "")
        group_zh = group.get("name_i18n", {}).get("zh_CN", "")
        group_emojis: list[dict[str, str]] = []

        group_chars: list[str] = []
        for subgroup in group.get("list", []):
            for entry in subgroup.get("list", []):
                char = entry.get("char", "")
                if char and char not in group_chars:
                    group_chars.append(char)

        first_char = group_chars[0] if group_chars else ""

        if group_en and group_en.lower() not in group_seen:
            group_seen.add(group_en.lower())
            groups.append(
                {
                    "name_en": group_en,
                    "name_zh": group_zh,
                    "char": first_char,
                    "chars": group_chars,
                }
            )

        for subgroup in group.get("list", []):
            subgroup_en = subgroup.get("name", "")
            subgroup_zh = subgroup.get("name_i18n", {}).get("zh_CN", "")
            subgroup_emojis: list[dict[str, str]] = []

            for entry in subgroup.get("list", []):
                name_i18n = entry.get("name_i18n", {})
                rec = {
                    "char": entry.get("char", ""),
                    "name_en": entry.get("name", ""),
                    "name_zh": name_i18n.get("zh_CN", ""),
                    "group_en": group_en,
                    "group_zh": group_zh,
                    "subgroup_en": subgroup_en,
                    "subgroup_zh": subgroup_zh,
                }
                records.append(rec)
                group_emojis.append(rec)
                subgroup_emojis.append(rec)

            if subgroup_en:
                group_map.setdefault(subgroup_en.lower(), []).extend(subgroup_emojis)
            if subgroup_zh:
                group_map.setdefault(subgroup_zh.lower(), []).extend(subgroup_emojis)

        if group_en:
            group_map.setdefault(group_en.lower(), []).extend(group_emojis)
        if group_zh:
            group_map.setdefault(group_zh.lower(), []).extend(group_emojis)

    return records, group_map, groups


def _parse_query(
    query: str,
    group_map: dict[str, list[dict[str, str]]],
) -> tuple[str, str | None]:
    """Split *query* into (keyword, group_filter).

    Supports '@groupname' syntax. The text after '@' is consumed word-by-word
    from the end until a known group/subgroup name is matched (exact or fuzzy).
    This allows both 'cat @动物与自然' and '@face eye' (where 'face' is the
    group filter and 'eye' becomes the keyword).
    """
    from wenzi.scripting.sources import fuzzy_match as _fuzzy_match

    text = query.strip()
    if "@" not in text:
        return text, None

    at_index = text.rfind("@")
    before = text[:at_index].strip()
    after = text[at_index + 1 :].strip().lower()
    if not after:
        return before, ""

    parts = after.split()

    # 1. Exact match (prefer longer candidates)
    for i in range(len(parts), 0, -1):
        candidate = " ".join(parts[:i])
        if candidate in group_map:
            keyword = " ".join([before] + parts[i:]).strip()
            return keyword, candidate

    # 2. Fuzzy match: collect all matches and pick the highest score.
    best_score = -1
    best_i = 0
    for i in range(len(parts), 0, -1):
        candidate = " ".join(parts[:i])
        for key in group_map:
            matched, score = _fuzzy_match(candidate, key)
            if matched and score > best_score:
                best_score = score
                best_i = i

    if best_score >= 0:
        keyword = " ".join([before] + parts[best_i:]).strip()
        return keyword, " ".join(parts[:best_i])

    # Fallback: treat the whole after-@ text as the group filter.
    return before, after


def _search_emojis(
    query: str,
    records: list[dict[str, str]],
    group_map: dict[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    """Return emoji records matching *query*.

    If the query contains '@groupname', the group name is fuzzy-matched
    against group/subgroup names (en/zh) to restrict the search pool.
    If no group matches, the search falls back to all records.
    Otherwise the keyword is fuzzy-matched across names and groups.
    """
    from wenzi.scripting.sources import fuzzy_match as _fuzzy_match

    q, group_filter = _parse_query(query, group_map)
    q = q.lower()
    if not q and group_filter is None:
        return []

    # Determine the pool of records to search within.
    pool = records
    if group_filter:
        matched_groups: list[list[dict[str, str]]] = []
        for key, emojis in group_map.items():
            matched, _score = _fuzzy_match(group_filter, key)
            if matched:
                matched_groups.append(emojis)
        if matched_groups:
            seen: set[str] = set()
            pool = []
            for group in matched_groups:
                for rec in group:
                    if rec["char"] not in seen:
                        seen.add(rec["char"])
                        pool.append(rec)

    # 1. Group-only query: return the pooled group emojis.
    if not q:
        return pool[:_MAX_GROUP_RESULTS]

    # 2. Fuzzy match within the pool.
    # When a group filter is active, only match against emoji names to avoid
    # spurious hits on group metadata (e.g. "eye" matching "Smileys & Emotion").
    scored: list[tuple[int, dict[str, str]]] = []
    for rec in pool:
        if not rec["char"]:
            continue
        fields = (
            [rec["name_en"], rec["name_zh"]]
            if group_filter
            else [
                rec["name_en"],
                rec["name_zh"],
                rec["group_en"],
                rec["group_zh"],
                rec["subgroup_en"],
                rec["subgroup_zh"],
            ]
        )
        matched, score = fuzzy_match_fields(q, fields)
        if matched:
            scored.append((score, rec))

    scored.sort(key=lambda x: x[0], reverse=True)
    max_results = _MAX_GROUP_RESULTS if group_filter else _MAX_RESULTS
    return [rec for _, rec in scored[:max_results]]


def _copy_and_alert(wz, char: str) -> None:
    wz.pasteboard.set(char)
    wz.alert("Emoji copied", duration=1.2)


def _emoji_icon(char: str) -> str:
    """Return a data URI SVG for the given emoji character."""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" '
        f'viewBox="0 0 32 32"><text y="26" font-size="24" x="16" '
        f'text-anchor="middle">{html.escape(char)}</text></svg>'
    )
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def _wrap_preview_html(content: str) -> str:
    """Wrap preview HTML with CSS variables for dark mode support."""
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
        ":root{--text:#1d1d1f;--secondary:#86868b}"
        "@media(prefers-color-scheme:dark)"
        "{:root{--text:#e5e5e7;--secondary:#98989d}}"
        "html,body{height:100%;margin:0;}"
        "body{font-family:-apple-system,sans-serif;color:var(--text);}"
        "</style></head><body>" + content + "</body></html>"
    )


def _group_preview_html(g: dict[str, Any]) -> str:
    """Build preview HTML for a group item showing all its emoji."""
    chars = g.get("chars", [])
    emoji_cells = " ".join(f"<span style='font-size:28px;line-height:1.2;'>{html.escape(c)}</span>" for c in chars)
    inner = (
        f"<div style='display:flex;flex-direction:column;align-items:center;"
        f"justify-content:flex-start;min-height:100%;padding:16px;"
        f"box-sizing:border-box;'>"
        f"<div style='display:flex;flex-wrap:wrap;justify-content:center;"
        f"gap:4px 6px;max-width:100%;overflow-y:auto;padding:4px;'>"
        f"{emoji_cells}"
        f"</div>"
        f"</div>"
    )
    return _wrap_preview_html(inner)


def _build_preview_html(rec: dict[str, str]) -> dict[str, Any]:
    """Build preview HTML dict for an emoji record."""
    char = rec["char"]
    inner_html = (
        "<div style='display:flex;flex-direction:column;align-items:center;"
        "justify-content:center;min-height:100%;padding:16px 0;"
        "box-sizing:border-box;'>"
        "<div style='font-size:110px;line-height:1;text-align:center;'>"
        f"{html.escape(char)}"
        "</div>"
        "<div style='text-align:center;color:var(--secondary);"
        "margin-top:12px;font-size:13px;line-height:1.5;max-width:100%;'>"
        f"<div style='font-weight:600;color:var(--text);font-size:16px;"
        f"margin-bottom:4px;'>{html.escape(rec['name_zh'])}</div>"
        f"{html.escape(rec['name_en'])}<br>"
        f"{html.escape(rec['group_zh'])} / {html.escape(rec['subgroup_zh'])}"
        "</div>"
        "</div>"
    )
    return {"type": "html", "content": _wrap_preview_html(inner_html)}


def _emoji_item(wz, rec: dict[str, str]) -> dict[str, Any]:
    char = rec["char"]
    subtitle = f"{rec['name_zh']} | {rec['name_en']} · {rec['group_zh']}"
    return {
        "title": char,
        "subtitle": subtitle,
        "item_id": f"emoji:{char}",
        "action": lambda c=char: wz.type_text(c, method="paste"),
        "secondary_action": lambda c=char: _copy_and_alert(wz, c),
        "preview": _build_preview_html(rec),
    }


def setup(wz) -> None:
    """Register the emoji chooser source."""
    from wenzi.scripting.sources import ChooserItem, ChooserSource

    records, group_map, groups = _load_emoji_data()

    def _search(query: str) -> list[ChooserItem]:
        if query.strip() == "@":
            return [
                ChooserItem(
                    title=g["name_zh"] or g["name_en"],
                    subtitle=g["name_en"] or "",
                    item_id=f"group:{g['name_en'] or g['name_zh']}",
                    icon=_emoji_icon(g["char"]),
                    action=lambda c=g["char"]: wz.type_text(c, method="paste"),
                    preview={
                        "type": "html",
                        "content": _group_preview_html(g),
                    },
                )
                for g in groups
                if g["char"]
            ]
        results = _search_emojis(query, records, group_map)
        return [
            ChooserItem(
                title=rec["char"],
                subtitle=f"{rec['name_zh']} | {rec['name_en']} · {rec['group_zh']}",
                item_id=f"emoji:{rec['char']}",
                action=lambda c=rec["char"]: wz.type_text(c, method="paste"),
                secondary_action=lambda c=rec["char"]: _copy_and_alert(wz, c),
                preview=_build_preview_html(rec),
            )
            for rec in results
        ]

    def _tab_complete(query: str, item: Any) -> str | None:
        if query.strip() == "@":
            return f"@{item.title} "
        return None

    src = ChooserSource(
        name="emoji_search",
        prefix="e",
        priority=5,
        description="Search and paste emoji (prefix: e)",
        search=_search,
        show_preview=True,
        action_hints={"enter": "Paste emoji", "cmd_enter": "Copy to clipboard"},
        complete=_tab_complete,
    )
    wz.chooser.register_source(src)
