"""Shared utilities for WKWebView-based panels."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

# ---------------------------------------------------------------------------
# Panel close delegate factory
# ---------------------------------------------------------------------------
_close_delegate_cache: dict[str, type] = {}


def make_panel_close_delegate_class(
    class_name: str, *, close_method: str = "close"
) -> type:
    """Create (or return cached) NSObject subclass for panel close delegation.

    Each ObjC class name must be unique across the process, so callers pass a
    distinct *class_name*.  The generated ``windowWillClose_`` calls
    ``getattr(self._panel_ref, close_method)()`` — most panels use ``"close"``
    but some use ``"cancelClicked_"`` or ``"_on_close_button"``.
    """
    key = class_name
    if key in _close_delegate_cache:
        return _close_delegate_cache[key]

    from Foundation import NSObject

    # Build the method dynamically so *close_method* is captured in closure.
    def _window_will_close(self, notification):  # noqa: ARG001
        if self._panel_ref is not None:
            method = getattr(self._panel_ref, close_method)
            if close_method.endswith("_"):
                method(None)
            else:
                method()

    attrs = {
        "_panel_ref": None,
        "windowWillClose_": _window_will_close,
    }
    cls = type(class_name, (NSObject,), attrs)
    _close_delegate_cache[key] = cls
    return cls


def time_range_cutoff(time_range: str) -> str | None:
    """Return ISO timestamp cutoff for a time range value, or None for 'all'."""
    now = datetime.now(UTC)
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


# ---------------------------------------------------------------------------
# Lightweight WKWebView configuration
# ---------------------------------------------------------------------------
_nonpersistent_store = None


def _reset_shared_state() -> None:
    """Reset shared singletons (for testing only)."""
    global _nonpersistent_store
    _nonpersistent_store = None


def _shared_nonpersistent_store():
    """Return a cached non-persistent WKWebsiteDataStore.

    All WebViews that share the same data store instance will share a
    single Web Content process.  This is the actual isolation boundary
    in modern WebKit — ``WKProcessPool`` is deprecated and ignored.
    """
    global _nonpersistent_store
    if _nonpersistent_store is None:
        from WebKit import WKWebsiteDataStore

        _nonpersistent_store = WKWebsiteDataStore.nonPersistentDataStore()
    return _nonpersistent_store


def lightweight_webview_config(
    *, network: bool = False, shared: bool = True
):  # -> WKWebViewConfiguration
    """Return a WKWebViewConfiguration optimised for low memory usage.

    WebViews sharing the same ``WKWebsiteDataStore`` instance share a
    single Web Content process.  ``WKProcessPool`` is deprecated on
    modern macOS and has no effect — the data store is the real
    isolation boundary.

    *network* — keep the default persistent data store (needed when the
    WebView loads real URLs, e.g. Google Translate).  When ``False`` a
    non-persistent ``WKWebsiteDataStore`` is used, which avoids
    persistent cookie/cache storage and reduces Networking process
    overhead.

    *shared* — when ``True`` (default), all WebViews use a single shared
    non-persistent data store and therefore share one Web Content process.
    When ``False``, a dedicated non-persistent data store is created so
    the WebView gets its own Web Content process that exits automatically
    when the WebView is deallocated.  Prefer ``False`` for most panels —
    WebKit's process-level decoded image cache grows unboundedly within a
    shared process and can only be freed by terminating it.  ``True`` is
    kept for backward compatibility but is no longer recommended.
    """
    from WebKit import WKWebViewConfiguration

    config = WKWebViewConfiguration.alloc().init()

    if not network:
        if shared:
            config.setWebsiteDataStore_(_shared_nonpersistent_store())
        else:
            from WebKit import WKWebsiteDataStore

            config.setWebsiteDataStore_(
                WKWebsiteDataStore.nonPersistentDataStore()
            )

    return config
