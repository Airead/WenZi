"""Input context capture for LLM enhancement.

Captures the user's current input environment (app, window, focused element)
to provide context-aware text enhancement.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import logging
import re
from typing import Any
from urllib.parse import urlparse

from wenzi.ui_helpers import get_frontmost_app

logger = logging.getLogger(__name__)

# Lazy singleton executor for AX timeout protection.
# Created on first "detailed" capture, avoids thread overhead when unused.
_ax_executor: concurrent.futures.ThreadPoolExecutor | None = None


def _get_ax_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Return the shared executor, creating it on first use."""
    global _ax_executor
    if _ax_executor is None:
        _ax_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    return _ax_executor


def shutdown_input_context() -> None:
    """Shut down the accessibility executor."""
    global _ax_executor
    if _ax_executor is not None:
        _ax_executor.shutdown(wait=False, cancel_futures=True)
        _ax_executor = None

# Pre-compiled regex patterns for domain extraction
_BROWSER_SUFFIX_RE = re.compile(
    r"\s*[-\u2014\u2013]+\s*"
    r"(Google Chrome|Mozilla Firefox|Safari|Microsoft Edge|Brave|Arc)$",
    re.IGNORECASE,
)
_DOMAIN_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}(\.[a-zA-Z]{2,})?$"
)


@dataclasses.dataclass
class InputContext:
    """Captured input environment at the time of voice recording."""

    app_name: str | None = None
    bundle_id: str | None = None
    window_title: str | None = None
    focused_role: str | None = None
    focused_description: str | None = None
    browser_domain: str | None = None

    def format_for_prompt(self, level: str) -> str | None:
        """Format context for LLM system prompt injection.

        Returns None if level is "off" or no useful info is available.
        ``bundle_id`` is never included in the prompt.
        """
        if level == "off" or not self.app_name:
            return None

        if level == "basic":
            return self.app_name

        # detailed
        parts = [self.app_name]
        if self.window_title:
            parts.append(f'"{self.window_title}"')
        if self.focused_role:
            parts.append(self.focused_role)
        if self.focused_description:
            parts.append(f'("{self.focused_description}")')
        if self.browser_domain:
            parts.append(self.browser_domain)
        return " \u2014 ".join(parts)

    def format_for_display(self) -> str:
        """Format context for the preview panel info view."""
        lines = []
        if self.app_name:
            lines.append(f"App:      {self.app_name}")
        if self.window_title:
            lines.append(f"Window:   {self.window_title}")
        if self.focused_role:
            lines.append(f"Element:  {self.focused_role}")
        if self.focused_description:
            lines.append(f"Desc:     {self.focused_description}")
        if self.browser_domain:
            lines.append(f"Domain:   {self.browser_domain}")
        return "\n".join(lines) if lines else "(no context captured)"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict, omitting None values."""
        return {
            k: v
            for k, v in dataclasses.asdict(self).items()
            if v is not None
        }

    @staticmethod
    def from_dict(d: dict[str, Any] | None) -> InputContext | None:
        """Deserialize from dict. Returns None if input is None."""
        if d is None:
            return None
        return InputContext(**{k: v for k, v in d.items() if k in _INPUT_CONTEXT_FIELDS})


# Cached field names for from_dict deserialization
_INPUT_CONTEXT_FIELDS = {f.name for f in dataclasses.fields(InputContext)}

_BROWSER_BUNDLE_IDS = {
    "com.apple.Safari",
    "com.google.Chrome",
    "org.mozilla.firefox",
    "company.thebrowser.Browser",  # Arc
    "com.microsoft.edgemac",
    "com.brave.Browser",
}


def capture_input_context(level: str = "basic") -> InputContext | None:
    """Capture current input environment.

    Args:
        level: Privacy level — "off", "basic", or "detailed".

    Returns:
        InputContext with fields populated according to level, or None
        if level is "off" or no frontmost app can be determined.
    """
    if level == "off":
        return None

    if level not in ("basic", "detailed"):
        logger.warning("Unknown input_context level %r, treating as basic", level)
        level = "basic"

    app = get_frontmost_app()
    if app is None:
        return None
    app_name = str(app.localizedName() or "")
    bundle_id = str(app.bundleIdentifier() or "")
    pid = app.processIdentifier()
    if not app_name:
        return None

    if level == "basic":
        return InputContext(app_name=app_name, bundle_id=bundle_id)

    # detailed — collect with timeout protection (500ms budget)
    # NOTE: AXUIElement calls ideally run on the main thread, but we
    # use a thread pool here for timeout protection. In practice,
    # AX calls use process-to-process IPC and work from any thread.
    window_title = None
    focused_role = None
    focused_desc = None
    browser_domain = None
    try:
        future = _get_ax_executor().submit(_collect_ax_fields, pid, bundle_id)
        window_title, focused_role, focused_desc, browser_domain = future.result(
            timeout=0.5
        )
    except concurrent.futures.TimeoutError:
        logger.debug("AX collection timed out for PID %s", pid)
    except Exception as e:
        logger.warning("AX collection failed: %s", e)

    return InputContext(
        app_name=app_name,
        bundle_id=bundle_id,
        window_title=window_title,
        focused_role=focused_role,
        focused_description=focused_desc,
        browser_domain=browser_domain,
    )


def _collect_ax_fields(pid: int, bundle_id: str | None) -> tuple:
    """Collect window title and AX-dependent fields. Called in a thread with timeout."""
    window_title, focused_role, focused_desc, win_ref = _get_ax_info(pid)
    browser_domain = None
    if bundle_id in _BROWSER_BUNDLE_IDS:
        browser_domain = _get_browser_domain(win_ref, window_title)
    return (window_title, focused_role, focused_desc, browser_domain)


def _get_ax_info(pid: int | None) -> tuple:
    """Get window title, focused element role, description, and window ref.

    Returns (window_title, role, description, win_ref) tuple. Any field
    may be None if Accessibility permission is not granted.
    """
    if pid is None:
        return (None, None, None, None)
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            kAXErrorSuccess,
        )
        app_ref = AXUIElementCreateApplication(pid)

        # Window title from AXFocusedWindow.AXTitle
        window_title = None
        win_ref = None
        err, win = AXUIElementCopyAttributeValue(app_ref, "AXFocusedWindow", None)
        if err == kAXErrorSuccess and win:
            win_ref = win
            err, val = AXUIElementCopyAttributeValue(win, "AXTitle", None)
            if err == kAXErrorSuccess and val:
                window_title = str(val)

        # Focused element role and description
        role = None
        desc = None
        err, focused = AXUIElementCopyAttributeValue(app_ref, "AXFocusedUIElement", None)
        if err == kAXErrorSuccess and focused:
            err, val = AXUIElementCopyAttributeValue(focused, "AXRole", None)
            if err == kAXErrorSuccess and val:
                role = str(val)

            for attr in ("AXDescription", "AXPlaceholderValue"):
                err, val = AXUIElementCopyAttributeValue(focused, attr, None)
                if err == kAXErrorSuccess and val:
                    desc = str(val)
                    break

        return (window_title, role, desc, win_ref)
    except Exception as e:
        logger.debug("Failed to get AX info: %s", e)
        return (None, None, None, None)


def _get_browser_domain(
    win_ref: Any, window_title: str | None
) -> str | None:
    """Extract browser domain. Tries AX first, falls back to window title."""
    if win_ref is not None:
        domain = _get_browser_domain_from_win(win_ref)
        if domain:
            return domain
    # Fallback: parse from window title
    return _parse_domain_from_title(window_title) if window_title else None


def _get_browser_domain_from_win(win_ref: Any) -> str | None:
    """Try to get URL from browser window AXDocument attribute."""
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            kAXErrorSuccess,
        )

        err, doc_url = AXUIElementCopyAttributeValue(win_ref, "AXDocument", None)
        if err == kAXErrorSuccess and doc_url:
            parsed = urlparse(str(doc_url))
            if parsed.hostname:
                return parsed.hostname

        return None
    except Exception as e:
        logger.debug("Failed to get browser domain from window: %s", e)
        return None


def _parse_domain_from_title(title: str) -> str | None:
    """Best-effort domain extraction from browser window title.

    Browser titles vary:
    - Chrome: "Page Title - Google Chrome"
    - Safari: "Page Title" or "domain.com"
    - Firefox: "Page Title -- Mozilla Firefox"

    This is best-effort and may return None.
    """
    # Strip known browser suffixes
    title = _BROWSER_SUFFIX_RE.sub("", title).strip()
    if not title:
        return None

    # Check if the remaining looks like a domain
    if _DOMAIN_RE.match(title):
        return title.lower()

    # Try urlparse for URLs
    parsed = urlparse(title)
    if parsed.hostname:
        return parsed.hostname

    return None
