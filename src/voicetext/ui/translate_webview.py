"""Lightweight WebView panel for Google Translate verification."""

from __future__ import annotations

import logging
import re
import urllib.parse

logger = logging.getLogger(__name__)

# CJK Unified Ideographs range for Chinese detection
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def build_google_translate_url(text: str) -> str:
    """Build a Google Translate URL with auto-detected language direction.

    If text contains Chinese characters: zh-CN -> en
    Otherwise: en -> zh-CN (back-translation verification)
    """
    has_chinese = bool(_CJK_RE.search(text))
    sl, tl = ("zh-CN", "en") if has_chinese else ("en", "zh-CN")
    encoded = urllib.parse.quote(text, safe="")
    return f"https://translate.google.com/?sl={sl}&tl={tl}&text={encoded}&op=translate"


def _create_resign_delegate_class():
    """Create an NSObject subclass that closes the panel when it loses focus."""
    from Foundation import NSObject

    class _TranslateResignDelegate(NSObject):
        """NSWindowDelegate that closes the panel on resignKey."""

        _owner = None  # TranslateWebViewPanel instance

        def windowDidResignKey_(self, notification):
            owner = self._owner
            if owner is not None:
                owner._on_panel_resigned()

    return _TranslateResignDelegate


_ResignDelegate = _create_resign_delegate_class()


class TranslateWebViewPanel:
    """Floating NSPanel with WKWebView that auto-closes on focus loss."""

    _WIDTH = 750
    _HEIGHT = 550

    def __init__(self) -> None:
        self._panel = None
        self._delegate = None

    def show(self, text: str) -> None:
        """Open the WebView panel with Google Translate for the given text."""
        if not text or not text.strip():
            return

        from AppKit import (
            NSApp,
            NSBackingStoreBuffered,
            NSClosableWindowMask,
            NSPanel,
            NSResizableWindowMask,
            NSStatusWindowLevel,
            NSTitledWindowMask,
        )
        from Foundation import NSMakeRect, NSURL, NSURLRequest
        from WebKit import WKWebView

        # Close existing panel if any
        self._cleanup()

        url_str = build_google_translate_url(text.strip())
        logger.info("Opening Google Translate: %s", url_str[:120])

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._WIDTH, self._HEIGHT),
            NSTitledWindowMask | NSClosableWindowMask | NSResizableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_("Google Translate")
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.center()

        # WKWebView fills the panel
        webview = WKWebView.alloc().initWithFrame_(
            NSMakeRect(0, 0, self._WIDTH, self._HEIGHT)
        )
        webview.setAutoresizingMask_(0x12)  # NSViewWidthSizable | NSViewHeightSizable

        url = NSURL.URLWithString_(url_str)
        request = NSURLRequest.requestWithURL_(url)
        webview.loadRequest_(request)

        panel.contentView().addSubview_(webview)

        # Delegate to auto-close on focus loss
        delegate = _ResignDelegate.alloc().init()
        delegate._owner = self
        panel.setDelegate_(delegate)
        self._delegate = delegate

        panel.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)
        self._panel = panel

    def _on_panel_resigned(self) -> None:
        """Called by delegate when the panel loses focus."""
        self._cleanup()

    def _cleanup(self) -> None:
        """Close the panel and clear references."""
        if self._panel is not None:
            try:
                self._panel.close()
            except Exception:
                pass
            self._panel = None
        if self._delegate is not None:
            self._delegate._owner = None
            self._delegate = None

    def close(self) -> None:
        """Close the panel if open."""
        self._cleanup()
