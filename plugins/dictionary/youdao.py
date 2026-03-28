"""Youdao dictionary API client."""

from __future__ import annotations

import json
import logging
import urllib.request
from urllib.parse import quote as _urlquote

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
    url = _SUGGEST_URL.format(query=_urlquote(query, safe=""))
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


_LOOKUP_URL = (
    "https://dict.youdao.com/jsonapi_s"
    "?doctype=json&jsonversion=4&le={le}&t={direction}&q={query}"
    "&dicts={dicts}"
)

_LOOKUP_TIMEOUT = 5

# Sections to request — keeps response small and fast
_DICTS_FILTER = (
    '%7B%22count%22%3A99%2C%22dicts%22%3A%5B%5B'
    '%22ec%22%2C%22simple%22%2C%22phrs%22%2C%22syno%22%2C'
    '%22ee%22%2C%22blng_sents_part%22%2C%22collins%22%2C'
    '%22etym%22%2C%22rel_word%22%2C%22web_trans%22'
    '%5D%5D%7D'
)


def lookup(word: str, direction: str) -> dict:
    """Fetch full dictionary entry from Youdao jsonapi_s.

    Args:
        word: The word to look up.
        direction: ``"en2zh-CHS"`` or ``"zh2en"``.

    Returns parsed JSON dict, or empty dict on error.
    """
    le = "zh" if direction == "zh2en" else "en"
    url = _LOOKUP_URL.format(
        le=le,
        direction=_urlquote(direction, safe=""),
        query=_urlquote(word, safe=""),
        dicts=_DICTS_FILTER,
    )
    try:
        with urllib.request.urlopen(url, timeout=_LOOKUP_TIMEOUT) as resp:
            return json.loads(resp.read())
    except Exception:
        logger.warning("Youdao lookup failed for %r", word, exc_info=True)
        return {}
