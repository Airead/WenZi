"""Custom search engines plugin for WenZi."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import tomllib
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_NAME = "engines.toml"
_DEFAULT_PRIORITY = 5
_ICON_CACHE_TTL = 7 * 24 * 60 * 60
_ICON_TIMEOUT = 5.0
_ICON_REFRESHING: set[str] = set()
_ICON_LOCK = threading.Lock()


@dataclass(frozen=True)
class SearchEngine:
    """A single search engine definition."""

    engine_id: str
    name: str
    prefix: str
    url: str
    homepage: str = ""
    subtitle: str = ""
    icon_url: str = ""


_DEFAULT_ENGINES = (
    SearchEngine(
        engine_id="google",
        name="Google",
        prefix="g",
        url="https://www.google.com/search?q={query}",
        homepage="https://www.google.com",
        subtitle="General web search",
        icon_url="https://www.google.com/favicon.ico",
    ),
    SearchEngine(
        engine_id="github",
        name="GitHub",
        prefix="gh",
        url="https://github.com/search?q={query}&type=repositories",
        homepage="https://github.com",
        subtitle="Repository and code search",
        icon_url="https://github.com/favicon.ico",
    ),
    SearchEngine(
        engine_id="etherscan",
        name="Etherscan",
        prefix="eth",
        url="https://etherscan.io/search?f=0&q={query}",
        homepage="https://etherscan.io",
        subtitle="Address, token, and transaction lookup",
        icon_url="https://etherscan.io/favicon.ico",
    ),
)


def _config_path() -> str:
    return os.path.join(os.path.dirname(__file__), _CONFIG_NAME)


def _parse_engine(raw: dict) -> SearchEngine | None:
    if not isinstance(raw, dict):
        return None

    name = str(raw.get("name", "")).strip()
    engine_id = str(raw.get("id", name.lower().replace(" ", "-"))).strip()
    prefix = str(raw.get("prefix", "")).strip().lower()
    if not prefix:
        raw_aliases = raw.get("aliases", [])
        if isinstance(raw_aliases, str):
            raw_aliases = [raw_aliases]
        if isinstance(raw_aliases, list):
            for alias in raw_aliases:
                text = str(alias).strip().lower()
                if text:
                    prefix = text
                    break
    if not prefix:
        prefix = engine_id.lower()
    url = str(raw.get("url", "")).strip()
    if not name or not engine_id or not prefix or not url:
        return None

    return SearchEngine(
        engine_id=engine_id,
        name=name,
        prefix=prefix,
        url=url,
        homepage=str(raw.get("homepage", "")).strip(),
        subtitle=str(raw.get("subtitle", "")).strip(),
        icon_url=str(raw.get("icon_url", "")).strip(),
    )


def _load_engines() -> list[SearchEngine]:
    engines = list(_DEFAULT_ENGINES)
    path = _config_path()

    if not os.path.isfile(path):
        return engines

    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except Exception:
        logger.exception("Failed to parse %s", path)
        return engines

    parsed_engines: list[SearchEngine] = []
    for raw in data.get("engines", []):
        engine = _parse_engine(raw)
        if engine is not None:
            parsed_engines.append(engine)

    if parsed_engines:
        engines = parsed_engines

    return engines


def _build_url(template: str, query: str) -> str:
    return template.format(
        raw=query,
        query=urllib.parse.quote(query, safe=""),
        query_plus=urllib.parse.quote_plus(query),
    )


def _copy_text(wz, text: str, message: str) -> None:
    wz.pasteboard.set(text)
    wz.alert(message, duration=1.5)


def _engine_badge(engine: SearchEngine) -> str:
    if engine.prefix:
        return engine.prefix.upper()[:4]
    return engine.name.upper()[:4]


def _engine_icon_url(engine: SearchEngine) -> str:
    if engine.icon_url:
        return engine.icon_url
    base = engine.homepage or engine.url
    parsed = urllib.parse.urlparse(base)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, "/favicon.ico", "", "", ""),
    )


def _icon_cache_dir() -> Path | None:
    try:
        from wenzi.config import resolve_cache_dir

        path = Path(resolve_cache_dir()) / "search_engines"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except Exception:
        logger.debug("Failed to resolve cache dir for search engine icons", exc_info=True)
        return None


def _icon_cache_path(engine: SearchEngine, icon_url: str) -> Path | None:
    cache_dir = _icon_cache_dir()
    if cache_dir is None:
        return None

    parsed = urllib.parse.urlparse(icon_url)
    ext = os.path.splitext(parsed.path)[1].lower()
    if ext not in {".ico", ".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp"}:
        ext = ".ico"

    digest = hashlib.sha256(icon_url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{engine.engine_id}-{digest}{ext}"


def _icon_is_fresh(cache_path: Path) -> bool:
    try:
        age = time.time() - cache_path.stat().st_mtime
    except OSError:
        return False
    return age <= _ICON_CACHE_TTL


def _download_icon(icon_url: str, cache_path: Path) -> None:
    request = urllib.request.Request(
        icon_url,
        headers={"User-Agent": "WenZi-SearchEngines/0.1"},
    )
    with urllib.request.urlopen(request, timeout=_ICON_TIMEOUT) as response:
        data = response.read()

    if not data:
        return

    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_path, "wb") as fh:
        fh.write(data)
    os.replace(tmp_path, cache_path)


def _schedule_icon_download(icon_url: str, cache_path: Path) -> None:
    key = str(cache_path)
    with _ICON_LOCK:
        if key in _ICON_REFRESHING:
            return
        _ICON_REFRESHING.add(key)

    def _worker() -> None:
        try:
            _download_icon(icon_url, cache_path)
        except Exception:
            logger.debug("Failed to cache favicon: %s", icon_url, exc_info=True)
        finally:
            with _ICON_LOCK:
                _ICON_REFRESHING.discard(key)

    threading.Thread(target=_worker, daemon=True).start()


def _engine_icon(engine: SearchEngine) -> str:
    icon_url = _engine_icon_url(engine)
    if not icon_url:
        return ""

    cache_path = _icon_cache_path(engine, icon_url)
    if cache_path is None:
        return icon_url

    if cache_path.is_file():
        if not _icon_is_fresh(cache_path):
            _schedule_icon_download(icon_url, cache_path)
        return cache_path.as_uri()

    _schedule_icon_download(icon_url, cache_path)
    return icon_url


def _engine_help_item(wz, engine: SearchEngine) -> dict:
    homepage = engine.homepage or engine.url
    subtitle = engine.subtitle or f'Use "{engine.prefix} <query>" to search'
    return {
        "title": engine.name,
        "subtitle": f'{subtitle}  |  Prefix: {engine.prefix}',
        "item_id": f"search-engine:{engine.engine_id}",
        "icon": _engine_icon(engine),
        "action": lambda url=homepage: webbrowser.open(url),
        "modifiers": {
            "alt": {
                "subtitle": "Copy prefix",
                "action": lambda text=engine.prefix: _copy_text(wz, text, "Prefix copied"),
            }
        },
    }


def _engine_search_item(wz, engine: SearchEngine, query: str) -> dict:
    url = _build_url(engine.url, query)
    subtitle = engine.subtitle or "Open search in browser"
    return {
        "title": f'{engine.name}: {query}',
        "subtitle": subtitle,
        "item_id": f"search-engine:{engine.engine_id}:{query.lower()}",
        "icon": _engine_icon(engine),
        "action": lambda target=url: webbrowser.open(target),
        "modifiers": {
            "alt": {
                "subtitle": "Copy search URL",
                "action": lambda text=url: _copy_text(wz, text, "Search URL copied"),
            }
        },
    }


def _register_engine_source(wz, engine: SearchEngine) -> None:
    source_name = f"search_engine_{engine.engine_id}"
    description = engine.subtitle or f"Search with {engine.name}"

    @wz.chooser.source(
        source_name,
        prefix=engine.prefix,
        priority=_DEFAULT_PRIORITY,
        description=description,
        action_hints={
            "enter": "Open search",
            "alt_enter": "Copy URL",
        },
        show_preview=False,
        universal_action=True,
    )
    def search(query: str, engine: SearchEngine = engine) -> list[dict]:
        stripped = query.strip()
        if not stripped:
            return [_engine_help_item(wz, engine)]
        return [_engine_search_item(wz, engine, stripped)]


def setup(wz) -> None:
    """Register one chooser source per search engine."""
    seen_prefixes: set[str] = set()
    for engine in _load_engines():
        if engine.prefix in seen_prefixes:
            logger.warning(
                "Duplicate search engine prefix %s for %s, skipping",
                engine.prefix,
                engine.engine_id,
            )
            continue
        seen_prefixes.add(engine.prefix)
        _register_engine_source(wz, engine)
