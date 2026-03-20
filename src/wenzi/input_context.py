"""Input context capture for LLM enhancement.

Captures the user's current input environment (app, window, focused element)
to provide context-aware text enhancement.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class InputContext:
    """Captured input environment at the time of voice recording."""

    app_name: Optional[str] = None
    bundle_id: Optional[str] = None
    window_title: Optional[str] = None
    focused_role: Optional[str] = None
    focused_description: Optional[str] = None
    browser_domain: Optional[str] = None

    def format_for_prompt(self, level: str) -> Optional[str]:
        """Format context for LLM system prompt injection.

        Returns None if level is "off" or no useful info is available.
        ``bundle_id`` is never included in the prompt.
        """
        if level == "off" or not self.app_name:
            return None

        if level == "basic":
            return f"\u5f53\u524d\u8f93\u5165\u73af\u5883\uff1a{self.app_name}"

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
        return f"\u5f53\u524d\u8f93\u5165\u73af\u5883\uff1a{' \u2014 '.join(parts)}"

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

    def format_for_history_tag(self, level: str) -> Optional[str]:
        """Format a short tag for conversation history entries.

        Returns None if level is "off" or no useful info.
        """
        if level == "off" or not self.app_name:
            return None

        if level == "basic":
            return self.app_name

        # detailed: prefer domain for browsers, else window_title
        suffix = self.browser_domain or self.window_title
        if suffix:
            return f"{self.app_name} \u2014 {suffix}"
        return self.app_name

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict, omitting None values."""
        return {
            k: v
            for k, v in dataclasses.asdict(self).items()
            if v is not None
        }

    @staticmethod
    def from_dict(d: Optional[Dict[str, Any]]) -> Optional["InputContext"]:
        """Deserialize from dict. Returns None if input is None."""
        if d is None:
            return None
        fields = {f.name for f in dataclasses.fields(InputContext)}
        return InputContext(**{k: v for k, v in d.items() if k in fields})


_BROWSER_BUNDLE_IDS = {
    "com.apple.Safari",
    "com.google.Chrome",
    "org.mozilla.firefox",
    "company.thebrowser.Browser",  # Arc
    "com.microsoft.edgemac",
    "com.brave.Browser",
}


def capture_input_context(level: str = "basic") -> Optional[InputContext]:
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

    app_name, bundle_id, pid = _get_frontmost_app_info()
    if not app_name:
        return None

    if level == "basic":
        return InputContext(app_name=app_name, bundle_id=bundle_id)

    # detailed — collect with timeout protection (500ms budget)
    import concurrent.futures

    # NOTE: AXUIElement calls ideally run on the main thread, but we
    # use a thread pool here for timeout protection. In practice,
    # AX calls use process-to-process IPC and work from any thread.
    window_title = None
    focused_role = None
    focused_desc = None
    browser_domain = None
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_collect_ax_fields, pid, bundle_id)
            window_title, focused_role, focused_desc, browser_domain = future.result(
                timeout=0.5
            )
    except (concurrent.futures.TimeoutError, Exception) as e:
        logger.debug("AX collection timed out or failed: %s", e)

    return InputContext(
        app_name=app_name,
        bundle_id=bundle_id,
        window_title=window_title,
        focused_role=focused_role,
        focused_description=focused_desc,
        browser_domain=browser_domain,
    )


def _collect_ax_fields(pid: int, bundle_id: Optional[str]) -> tuple:
    """Collect window title and AX-dependent fields. Called in a thread with timeout."""
    window_title = _get_window_title(pid)
    focused_role, focused_desc = _get_ax_focused_element(pid)
    browser_domain = None
    if bundle_id in _BROWSER_BUNDLE_IDS:
        browser_domain = _get_browser_domain(pid, window_title)
    return (window_title, focused_role, focused_desc, browser_domain)


def _get_frontmost_app_info() -> tuple:
    """Return (app_name, bundle_id, pid) of the frontmost application."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return (None, None, None)
        return (
            str(app.localizedName() or ""),
            str(app.bundleIdentifier() or ""),
            app.processIdentifier(),
        )
    except Exception as e:
        logger.debug("Failed to get frontmost app info: %s", e)
        return (None, None, None)


def _get_window_title(pid: int) -> Optional[str]:
    """Get the key window title via CGWindowListCopyWindowInfo."""
    try:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListExcludeDesktopElements,
            kCGWindowListOptionOnScreenOnly,
        )
        options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
        window_list = CGWindowListCopyWindowInfo(options, kCGNullWindowID)
        if not window_list:
            return None
        for win in window_list:
            if win.get("kCGWindowOwnerPID") == pid and win.get("kCGWindowLayer", 99) == 0:
                name = win.get("kCGWindowName")
                if name:
                    return str(name)
        return None
    except Exception as e:
        logger.debug("Failed to get window title: %s", e)
        return None


def _get_ax_focused_element(pid: Optional[int]) -> tuple:
    """Get focused element role and description via AXUIElement API.

    Returns (role, description) tuple. Both may be None if Accessibility
    permission is not granted or the element cannot be determined.
    """
    if pid is None:
        return (None, None)
    try:
        from ApplicationServices import (
            AXUIElementCreateApplication,
            AXUIElementCopyAttributeValue,
        )
        from ApplicationServices import kAXErrorSuccess
        app_ref = AXUIElementCreateApplication(pid)

        err, focused = AXUIElementCopyAttributeValue(app_ref, "AXFocusedUIElement", None)
        if err != kAXErrorSuccess or focused is None:
            return (None, None)

        role = None
        err, val = AXUIElementCopyAttributeValue(focused, "AXRole", None)
        if err == kAXErrorSuccess and val:
            role = str(val)

        desc = None
        # Try AXDescription first, then AXPlaceholderValue
        for attr in ("AXDescription", "AXPlaceholderValue"):
            err, val = AXUIElementCopyAttributeValue(focused, attr, None)
            if err == kAXErrorSuccess and val:
                desc = str(val)
                break

        return (role, desc)
    except Exception as e:
        logger.debug("Failed to get AX focused element: %s", e)
        return (None, None)


def _get_browser_domain(
    pid: Optional[int], window_title: Optional[str]
) -> Optional[str]:
    """Extract browser domain. Tries AX first, falls back to window title."""
    if pid is not None:
        domain = _get_browser_domain_via_ax(pid)
        if domain:
            return domain
    # Fallback: parse from window title
    return _parse_domain_from_title(window_title) if window_title else None


def _get_browser_domain_via_ax(pid: int) -> Optional[str]:
    """Try to get URL from browser via AX and extract domain."""
    try:
        from ApplicationServices import (
            AXUIElementCreateApplication,
            AXUIElementCopyAttributeValue,
        )
        from ApplicationServices import kAXErrorSuccess
        from urllib.parse import urlparse

        app_ref = AXUIElementCreateApplication(pid)

        # Try AXFocusedWindow → AXDocument (Safari) or address bar value
        err, win = AXUIElementCopyAttributeValue(app_ref, "AXFocusedWindow", None)
        if err != kAXErrorSuccess or win is None:
            return None

        # Safari: AXDocument attribute on the window
        err, doc_url = AXUIElementCopyAttributeValue(win, "AXDocument", None)
        if err == kAXErrorSuccess and doc_url:
            parsed = urlparse(str(doc_url))
            if parsed.hostname:
                return parsed.hostname

        return None
    except Exception as e:
        logger.debug("Failed to get browser domain via AX: %s", e)
        return None


def _parse_domain_from_title(title: str) -> Optional[str]:
    """Best-effort domain extraction from browser window title.

    Browser titles vary:
    - Chrome: "Page Title - Google Chrome"
    - Safari: "Page Title" or "domain.com"
    - Firefox: "Page Title -- Mozilla Firefox"

    This is best-effort and may return None.
    """
    import re

    # Strip known browser suffixes
    title = re.sub(
        r"\s*[-\u2014\u2013]+\s*(Google Chrome|Mozilla Firefox|Safari|Microsoft Edge|Brave|Arc)$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = title.strip()
    if not title:
        return None

    # Check if the remaining looks like a domain
    domain_pattern = re.compile(
        r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}(\.[a-zA-Z]{2,})?$"
    )
    if domain_pattern.match(title):
        return title.lower()

    # Try urlparse for URLs
    from urllib.parse import urlparse

    parsed = urlparse(title)
    if parsed.hostname:
        return parsed.hostname

    return None
