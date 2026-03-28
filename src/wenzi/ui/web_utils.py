"""Shared utilities for WKWebView-based panels."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


def time_range_cutoff(time_range: str) -> Optional[str]:
    """Return ISO timestamp cutoff for a time range value, or None for 'all'."""
    now = datetime.now(timezone.utc)
    if time_range == "today":
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif time_range == "7d":
        cutoff = now - timedelta(days=7)
    elif time_range == "30d":
        cutoff = now - timedelta(days=30)
    else:
        return None
    return cutoff.isoformat()


def cleanup_webview_handler(webview, handler_name: str = "action") -> None:
    """Remove a script message handler from a WKWebView, ignoring errors.

    Must be called before releasing the webview to prevent delegate leaks.
    """
    if webview is None:
        return
    try:
        webview.configuration().userContentController().removeScriptMessageHandlerForName_(
            handler_name
        )
    except Exception:
        pass
