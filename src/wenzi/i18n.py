"""Internationalization support for WenZi."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_LOCALES_DIR = os.path.join(os.path.dirname(__file__), "locales")

# Module-level state
_current_locale: str = "en"
_strings: dict[str, str] = {}
_fallback_strings: dict[str, str] = {}
_active_locales_dir: str = _LOCALES_DIR

# Lazy import - may not be available in test environments
NSLocale: Any = None


def _load_nslocale() -> Any:
    global NSLocale
    if NSLocale is None:
        try:
            from Foundation import NSLocale as _NSLocale

            NSLocale = _NSLocale
        except ImportError:
            NSLocale = type(
                "FallbackNSLocale",
                (),
                {"preferredLanguages": staticmethod(lambda: ["en"])},
            )
    return NSLocale


def _load_json(path: str) -> dict[str, str]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return {k: str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to load locale file %s: %s", path, e)
        return {}


def _detect_system_locale() -> str:
    ns = _load_nslocale()
    try:
        langs = ns.preferredLanguages()
        if langs and langs[0].startswith("zh"):
            return "zh"
    except Exception:
        logger.debug("Failed to detect system locale", exc_info=True)
    return "en"


def init_i18n(
    locale: str | None = None,
    locales_dir: str | None = None,
) -> None:
    """Initialize i18n. Call once at app startup.

    Args:
        locale: "en", "zh", "auto", or None.
                None and "auto" both detect from system.
        locales_dir: Override locales directory (for testing).
    """
    global _current_locale, _strings, _fallback_strings, _active_locales_dir

    base = locales_dir or _LOCALES_DIR
    _active_locales_dir = base

    # Always load English as fallback
    _fallback_strings = _load_json(os.path.join(base, "en.json"))

    # Resolve locale
    if locale is None or locale == "auto":
        resolved = _detect_system_locale()
    else:
        resolved = locale

    _current_locale = resolved

    if resolved == "en":
        _strings = _fallback_strings
    else:
        _strings = _load_json(os.path.join(base, f"{resolved}.json"))

    logger.info("i18n initialized: locale=%s, keys=%d", resolved, len(_strings))


def t(_key: str, **kwargs: Any) -> str:
    """Get translated string for key.

    Lookup order: current locale -> English fallback -> key itself.
    Supports parameter interpolation via str.format_map().
    """
    value = _strings.get(_key)
    if value is None:
        value = _fallback_strings.get(_key)
    if value is None:
        value = _key
    if kwargs:
        try:
            return value.format_map(kwargs)
        except (KeyError, AttributeError, ValueError):
            logger.warning("Missing i18n params for key %r: %s", _key, kwargs)
            return value
    return value


def get_locale() -> str:
    """Return the current locale code."""
    return _current_locale


_DOCS_BASE_URL = "https://airead.github.io/WenZi"


def build_doc_url(path: str) -> str:
    """Build a full documentation URL with locale-aware prefix.

    *path* should be relative, e.g. ``"enhance-modes.html#how-it-works"``.
    """
    locale = get_locale()
    if locale.startswith("zh"):
        return f"{_DOCS_BASE_URL}/zh/docs/{path}"
    return f"{_DOCS_BASE_URL}/docs/{path}"


def inject_i18n_into_webview(
    webview: Any, prefix: str, call_init: bool = True
) -> None:
    """Inject translations into a WKWebView as window._i18n.

    If call_init is True, also calls _initI18nLabels() in JS.
    """
    import json as _json

    translations = get_translations_for_prefix(prefix)
    init_call = "_initI18nLabels();" if call_init else ""
    script = (
        f"window._i18n = {_json.dumps(translations, ensure_ascii=False)};"
        f"{init_call}"
    )
    if webview is not None:
        webview.evaluateJavaScript_completionHandler_(script, None)


def get_translations_for_prefix(prefix: str) -> dict[str, str]:
    """Return translations matching prefix, with prefix stripped from keys.

    Used for injecting translations into WKWebView JS context.
    Falls back to English for missing keys.
    """
    result: dict[str, str] = {}
    all_keys = set(_strings.keys()) | set(_fallback_strings.keys())
    for key in all_keys:
        if key.startswith(prefix):
            short_key = key[len(prefix) :]
            value = _strings.get(key)
            if value is None:
                value = _fallback_strings.get(key)
            if value is None:
                value = key
            result[short_key] = value
    return result
