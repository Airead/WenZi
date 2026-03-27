"""Youdao dictionary API client."""

from __future__ import annotations

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

_SUGGEST_URL = (
    "https://dict.youdao.com/suggest"
    "?num=20&ver=3.0&doctype=json&cache=false&le=en&q={query}"
)

_SUGGEST_TIMEOUT = 3


def suggest(query: str) -> list[dict]:
    """Return word suggestions from Youdao suggest API.

    Returns list of ``{"word": str, "explain": str}``.
    Returns empty list on any error.
    """
    url = _SUGGEST_URL.format(query=urllib.request.quote(query))
    try:
        with urllib.request.urlopen(url, timeout=_SUGGEST_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except Exception:
        logger.warning("Youdao suggest failed for %r", query, exc_info=True)
        return []

    entries = data.get("data", {}).get("entries")
    if not entries:
        return []
    return [
        {"word": e.get("entry", ""), "explain": e.get("explain", "")}
        for e in entries
    ]
