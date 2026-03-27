"""Dictionary plugin — EN↔ZH lookup via Youdao."""

from __future__ import annotations

import asyncio
import re
from html import escape as _esc

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _detect_direction(query: str) -> str:
    """Return Youdao direction string based on query characters."""
    if _CJK_RE.search(query):
        return "zh2en"
    return "en2zh-CHS"


def _wrap_html(content: str) -> str:
    """Wrap dictionary HTML with CSS variables for standalone window."""
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><style>"
        ":root{--bg:#f5f5f7;--text:#1d1d1f;--secondary:#86868b;--border:#d2d2d7}"
        "@media(prefers-color-scheme:dark)"
        "{:root{--bg:#2c2c2e;--text:#e5e5e7;--secondary:#98989d;--border:#48484a}}"
        "body{font-family:-apple-system,sans-serif;background:var(--bg);"
        "color:var(--text);padding:16px;}"
        "</style></head><body>" + content + "</body></html>"
    )


def setup(wz) -> None:
    """Register the dictionary chooser source."""
    from .render import render_definition
    from .youdao import lookup, suggest

    _panel_ref = [None]

    def _make_preview_and_action(word: str, direction: str):
        """Return (preview_callable, action_callable) sharing a lookup cache."""
        _cache = {}

        def _load_html():
            if "html" not in _cache:
                data = lookup(word, direction)
                if data:
                    _cache["html"] = render_definition(data, word)
                else:
                    _cache["html"] = (
                        f'<div style="color:var(--secondary);'
                        f'text-align:center;padding:40px">'
                        f"Failed to load definition for <b>{_esc(word)}</b></div>"
                    )
            return _cache["html"]

        def _preview():
            return {"type": "html", "content": _load_html()}

        def _action():
            html = _load_html()
            if not html:
                return
            if _panel_ref[0] is not None:
                try:
                    _panel_ref[0].close()
                except Exception:
                    pass
            panel = wz.ui.webview_panel(
                title=word,
                html=_wrap_html(html),
                width=500,
                height=600,
                floating=True,
            )
            panel.show()
            _panel_ref[0] = panel

        return _preview, _action

    @wz.chooser.source(
        "dictionary",
        prefix="d",
        priority=5,
        description="Youdao Dictionary (EN↔ZH)",
        show_preview=True,
        action_hints={"enter": "Open"},
        search_timeout=5.0,
        debounce_delay=0.2,
    )
    async def search(query: str) -> list:
        if not query.strip():
            return []

        direction = _detect_direction(query)
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, suggest, query)

        return [
            {
                "title": entry["word"],
                "subtitle": entry.get("explain", ""),
                "preview": preview,
                "action": action,
            }
            for entry in results
            for preview, action in [_make_preview_and_action(entry["word"], direction)]
        ]
