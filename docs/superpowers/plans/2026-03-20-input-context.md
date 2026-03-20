# Input Context Awareness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture the user's input environment (app, window, focused element) during voice recording and inject it as LLM context for better enhancement.

**Architecture:** New `input_context.py` module provides `InputContext` dataclass and `capture_input_context()`. Captured at hotkey press, passed through recording→enhance→storage chain. Injected at tail of system prompt. Three privacy levels (off/basic/detailed) controlled via config.

**Tech Stack:** PyObjC (NSWorkspace, CGWindowList, AXUIElement), AppKit, dataclasses

**Spec:** `docs/superpowers/specs/2026-03-20-input-context-design.md`

---

### Task 1: InputContext Data Model and Capture

**Files:**
- Create: `src/wenzi/input_context.py`
- Create: `tests/test_input_context.py`

- [ ] **Step 1: Write tests for InputContext dataclass**

```python
# tests/test_input_context.py
"""Tests for input_context module."""

import dataclasses

import pytest


class TestInputContext:
    """Tests for InputContext dataclass and formatting methods."""

    def test_default_all_none(self):
        from wenzi.input_context import InputContext
        ctx = InputContext()
        assert ctx.app_name is None
        assert ctx.bundle_id is None
        assert ctx.window_title is None
        assert ctx.focused_role is None
        assert ctx.focused_description is None
        assert ctx.browser_domain is None

    def test_format_for_prompt_off(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(app_name="Terminal", bundle_id="com.apple.Terminal")
        assert ctx.format_for_prompt("off") is None

    def test_format_for_prompt_basic(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(app_name="Terminal", bundle_id="com.apple.Terminal")
        result = ctx.format_for_prompt("basic")
        assert "Terminal" in result
        assert "com.apple.Terminal" not in result  # bundle_id never in prompt

    def test_format_for_prompt_basic_no_app_name(self):
        from wenzi.input_context import InputContext
        ctx = InputContext()
        assert ctx.format_for_prompt("basic") is None

    def test_format_for_prompt_detailed(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(
            app_name="Google Chrome",
            bundle_id="com.google.Chrome",
            window_title="GitHub - PR #42",
            focused_role="AXTextArea",
            focused_description="Comment body",
            browser_domain="github.com",
        )
        result = ctx.format_for_prompt("detailed")
        assert "Google Chrome" in result
        assert "GitHub - PR #42" in result
        assert "AXTextArea" in result
        assert "github.com" in result
        assert "com.google.Chrome" not in result

    def test_format_for_prompt_detailed_partial_fields(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(app_name="Terminal", bundle_id="com.apple.Terminal")
        result = ctx.format_for_prompt("detailed")
        assert "Terminal" in result
        # Should not crash with None fields

    def test_format_for_display(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(
            app_name="VS Code",
            window_title="main.py",
            focused_role="AXTextArea",
        )
        result = ctx.format_for_display()
        assert "VS Code" in result
        assert "main.py" in result
        assert "AXTextArea" in result

    def test_format_for_history_tag_off(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(app_name="Terminal")
        assert ctx.format_for_history_tag("off") is None

    def test_format_for_history_tag_basic(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(app_name="Terminal", bundle_id="com.apple.Terminal")
        result = ctx.format_for_history_tag("basic")
        assert result == "Terminal"

    def test_format_for_history_tag_detailed_with_domain(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(
            app_name="Chrome",
            browser_domain="github.com",
            window_title="GitHub - Some Page",
        )
        result = ctx.format_for_history_tag("detailed")
        assert "Chrome" in result
        assert "github.com" in result

    def test_format_for_history_tag_detailed_with_title(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(
            app_name="VS Code",
            window_title="main.py - MyProject",
        )
        result = ctx.format_for_history_tag("detailed")
        assert "VS Code" in result
        assert "main.py - MyProject" in result

    def test_to_dict_omits_none(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(app_name="Terminal", bundle_id="com.apple.Terminal")
        d = ctx.to_dict()
        assert d == {"app_name": "Terminal", "bundle_id": "com.apple.Terminal"}
        assert "window_title" not in d

    def test_from_dict(self):
        from wenzi.input_context import InputContext
        d = {"app_name": "Terminal", "bundle_id": "com.apple.Terminal", "focused_role": "AXTextArea"}
        ctx = InputContext.from_dict(d)
        assert ctx.app_name == "Terminal"
        assert ctx.bundle_id == "com.apple.Terminal"
        assert ctx.focused_role == "AXTextArea"
        assert ctx.window_title is None

    def test_from_dict_none(self):
        from wenzi.input_context import InputContext
        assert InputContext.from_dict(None) is None

    def test_from_dict_empty(self):
        from wenzi.input_context import InputContext
        ctx = InputContext.from_dict({})
        assert ctx is not None
        assert ctx.app_name is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_input_context.py -v`
Expected: ImportError — `wenzi.input_context` does not exist

- [ ] **Step 3: Implement InputContext dataclass**

```python
# src/wenzi/input_context.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_input_context.py -v`
Expected: All pass

- [ ] **Step 5: Write tests for capture_input_context**

Add to `tests/test_input_context.py`:

```python
from unittest.mock import MagicMock, patch


class TestCaptureInputContext:
    """Tests for capture_input_context() function."""

    def test_off_returns_none(self):
        from wenzi.input_context import capture_input_context
        assert capture_input_context("off") is None

    @patch("wenzi.input_context._get_frontmost_app_info")
    def test_basic_collects_app_only(self, mock_info):
        from wenzi.input_context import capture_input_context
        mock_info.return_value = ("Terminal", "com.apple.Terminal", 1234)
        ctx = capture_input_context("basic")
        assert ctx is not None
        assert ctx.app_name == "Terminal"
        assert ctx.bundle_id == "com.apple.Terminal"
        assert ctx.window_title is None
        assert ctx.focused_role is None

    @patch("wenzi.input_context._get_ax_focused_element")
    @patch("wenzi.input_context._get_window_title")
    @patch("wenzi.input_context._get_frontmost_app_info")
    def test_detailed_collects_all(self, mock_info, mock_title, mock_ax):
        from wenzi.input_context import capture_input_context
        mock_info.return_value = ("Terminal", "com.apple.Terminal", 1234)
        mock_title.return_value = "zsh"
        mock_ax.return_value = ("AXTextArea", None)
        ctx = capture_input_context("detailed")
        assert ctx is not None
        assert ctx.app_name == "Terminal"
        assert ctx.window_title == "zsh"
        assert ctx.focused_role == "AXTextArea"

    @patch("wenzi.input_context._get_frontmost_app_info")
    def test_returns_none_when_no_app(self, mock_info):
        from wenzi.input_context import capture_input_context
        mock_info.return_value = (None, None, None)
        assert capture_input_context("basic") is None

    @patch("wenzi.input_context._get_browser_domain")
    @patch("wenzi.input_context._get_ax_focused_element")
    @patch("wenzi.input_context._get_window_title")
    @patch("wenzi.input_context._get_frontmost_app_info")
    def test_detailed_browser_domain(self, mock_info, mock_title, mock_ax, mock_domain):
        from wenzi.input_context import capture_input_context
        mock_info.return_value = ("Google Chrome", "com.google.Chrome", 5678)
        mock_title.return_value = "GitHub - My Repo"
        mock_ax.return_value = ("AXTextField", "Search or type a URL")
        mock_domain.return_value = "github.com"
        ctx = capture_input_context("detailed")
        assert ctx.browser_domain == "github.com"
        assert ctx.focused_description == "Search or type a URL"

    @patch("wenzi.input_context._get_frontmost_app_info")
    def test_invalid_level_treated_as_basic(self, mock_info):
        from wenzi.input_context import capture_input_context
        mock_info.return_value = ("Terminal", "com.apple.Terminal", 1234)
        ctx = capture_input_context("invalid")
        assert ctx is not None
        assert ctx.app_name == "Terminal"
        assert ctx.window_title is None  # basic level
```

- [ ] **Step 6: Implement capture_input_context and helper functions**

Add to `src/wenzi/input_context.py`:

```python
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

    window_title = _get_window_title(pid) if pid else None

    # AX calls may hang if target app is unresponsive — run with timeout
    focused_role = None
    focused_desc = None
    browser_domain = None
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_collect_ax_fields, pid, bundle_id, window_title)
            focused_role, focused_desc, browser_domain = future.result(timeout=0.5)
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


def _collect_ax_fields(
    pid: int, bundle_id: Optional[str], window_title: Optional[str]
) -> tuple:
    """Collect AX-dependent fields. Called in a thread with timeout."""
    focused_role, focused_desc = _get_ax_focused_element(pid)
    browser_domain = None
    if bundle_id in _BROWSER_BUNDLE_IDS:
        browser_domain = _get_browser_domain(pid, window_title)
    return (focused_role, focused_desc, browser_domain)


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

    return None
```

- [ ] **Step 7: Run all tests**

Run: `uv run pytest tests/test_input_context.py -v`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add src/wenzi/input_context.py tests/test_input_context.py
git commit -m "feat: add InputContext data model and capture_input_context"
```

---

### Task 2: Configuration

**Files:**
- Modify: `src/wenzi/config.py:278-308` (DEFAULT_CONFIG ai_enhance section)
- Modify: `src/wenzi/config.py:494-536` (validate_config rules)

- [ ] **Step 1: Add `input_context` to DEFAULT_CONFIG**

In `src/wenzi/config.py`, add after line 307 (`}`  closing `conversation_history`):

```python
        "input_context": "basic",
```

So the `ai_enhance` section becomes:
```python
    "ai_enhance": {
        ...
        "conversation_history": {
            "enabled": False,
            "max_entries": 10,
        },
        "input_context": "basic",
    },
```

- [ ] **Step 2: Add validation rule**

In `validate_config()`, add to the `rules` list (after the `max_static_hotwords` rule at line 535):

```python
        ("ai_enhance.input_context", str,
         lambda v: v in {"off", "basic", "detailed"},
         DEFAULT_CONFIG["ai_enhance"]["input_context"]),
```

- [ ] **Step 3: Write validation test**

Add to the appropriate test file (e.g., `tests/test_config.py`):

```python
def test_validate_input_context_invalid_falls_back():
    from wenzi.config import validate_config
    config = {"ai_enhance": {"input_context": "invalid_value"}}
    result = validate_config(config)
    assert result["ai_enhance"]["input_context"] == "basic"

def test_validate_input_context_valid_values():
    from wenzi.config import validate_config
    for level in ("off", "basic", "detailed"):
        config = {"ai_enhance": {"input_context": level}}
        result = validate_config(config)
        assert result["ai_enhance"]["input_context"] == level
```

- [ ] **Step 4: Run config tests**

Run: `uv run pytest tests/test_config.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/wenzi/config.py
git commit -m "feat: add input_context config key with validation"
```

---

### Task 3: Conversation History Storage

**Files:**
- Modify: `src/wenzi/enhance/conversation_history.py:197-238` (log method)
- Modify: `src/wenzi/enhance/conversation_history.py:700-709` (format_entry_line)
- Test: `tests/enhance/test_conversation_history.py`

- [ ] **Step 1: Write tests for input_context in log() and format_entry_line()**

Add to `tests/enhance/test_conversation_history.py`:

```python
class TestInputContextStorage:
    """Tests for input_context field in conversation history."""

    def test_log_with_input_context(self, history, history_dir):
        from wenzi.input_context import InputContext
        ctx = InputContext(app_name="Terminal", bundle_id="com.apple.Terminal")
        history.log(
            asr_text="hello",
            enhanced_text="hello",
            final_text="hello",
            enhance_mode="proofread",
            preview_enabled=True,
            input_context=ctx,
        )
        records = history.get_all()
        assert len(records) == 1
        assert records[0]["input_context"] == {
            "app_name": "Terminal",
            "bundle_id": "com.apple.Terminal",
        }

    def test_log_without_input_context(self, history, history_dir):
        history.log(
            asr_text="hello",
            enhanced_text="hello",
            final_text="hello",
            enhance_mode="proofread",
            preview_enabled=True,
        )
        records = history.get_all()
        assert len(records) == 1
        assert "input_context" not in records[0]

    def test_format_entry_line_with_context_tag(self, history):
        entry = {
            "asr_text": "KC",
            "final_text": "k8s",
            "input_context": {"app_name": "Terminal"},
        }
        line = history.format_entry_line(entry, context_level="basic")
        assert "(Terminal)" in line
        assert "KC" in line or "k8s" in line

    def test_format_entry_line_no_context(self, history):
        entry = {"asr_text": "hello", "final_text": "hello"}
        line_with = history.format_entry_line(entry, context_level="basic")
        line_without = history.format_entry_line(entry)
        # No context → no tag, both should be the same
        assert "()" not in line_with
        assert line_with == line_without

    def test_format_entry_line_detailed_tag(self, history):
        entry = {
            "asr_text": "hello",
            "final_text": "hello",
            "input_context": {
                "app_name": "Chrome",
                "browser_domain": "github.com",
            },
        }
        line = history.format_entry_line(entry, context_level="detailed")
        assert "(Chrome" in line
        assert "github.com" in line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/enhance/test_conversation_history.py::TestInputContextStorage -v`
Expected: FAIL

- [ ] **Step 3: Update log() method**

In `src/wenzi/enhance/conversation_history.py`, modify the `log()` signature (line 197-208) to add `input_context` parameter:

```python
    def log(
        self,
        asr_text: str,
        enhanced_text: Optional[str],
        final_text: str,
        enhance_mode: str,
        preview_enabled: bool,
        stt_model: str = "",
        llm_model: str = "",
        user_corrected: bool = False,
        audio_duration: float = 0.0,
        input_context: Any = None,
    ) -> str:
```

After the record dict creation (line 228), add:

```python
        if input_context is not None:
            record["input_context"] = input_context.to_dict()
```

- [ ] **Step 4: Update format_entry_line()**

Replace the existing `format_entry_line` static method (lines 700-709) with:

```python
    @staticmethod
    def format_entry_line(entry: Dict[str, Any], context_level: str = "off") -> str:
        """Format one history record as a prompt line.

        Uses inline-diff notation ``[old→new]`` for corrections.

        Args:
            entry: A conversation history record dict.
            context_level: Current input_context config level. When not "off"
                and the entry has ``input_context``, a short environment tag
                is appended.
        """
        asr = entry.get("asr_text", "").replace("\n", "\u23ce")
        final = entry.get("final_text", "").replace("\n", "\u23ce")
        line = f"- {inline_diff(asr, final)}"

        if context_level != "off":
            ic_data = entry.get("input_context")
            if ic_data:
                from wenzi.input_context import InputContext
                ic = InputContext.from_dict(ic_data)
                if ic:
                    tag = ic.format_for_history_tag(context_level)
                    if tag:
                        line = f"{line} ({tag})"
        return line
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/enhance/test_conversation_history.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/wenzi/enhance/conversation_history.py tests/test_conversation_history.py
git commit -m "feat: add input_context to conversation history storage and formatting"
```

---

### Task 4: Preview History Storage

**Files:**
- Modify: `src/wenzi/enhance/preview_history.py:13-31` (PreviewRecord)
- Test: `tests/enhance/test_preview_history.py`

- [ ] **Step 1: Write test for PreviewRecord with input_context**

Add to `tests/enhance/test_preview_history.py`:

```python
class TestPreviewRecordInputContext:
    """Tests for input_context field on PreviewRecord."""

    def test_default_none(self):
        r = _make_record()
        assert r.input_context is None

    def test_with_input_context(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(app_name="Terminal", bundle_id="com.apple.Terminal")
        r = _make_record(input_context=ctx)
        assert r.input_context is ctx
        assert r.input_context.app_name == "Terminal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/enhance/test_preview_history.py::TestPreviewRecordInputContext -v`
Expected: FAIL — `input_context` not a field of `PreviewRecord`

- [ ] **Step 3: Add input_context field to PreviewRecord**

In `src/wenzi/enhance/preview_history.py`, add import and field after `hotwords_detail` (line 31):

```python
    input_context: "InputContext | None" = None  # from wenzi.input_context
```

Add at the top of the file (after existing imports):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wenzi.input_context import InputContext
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/enhance/test_preview_history.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/wenzi/enhance/preview_history.py tests/test_preview_history.py
git commit -m "feat: add input_context field to PreviewRecord"
```

---

### Task 5: Enhancer Integration

**Files:**
- Modify: `src/wenzi/enhance/enhancer.py:26-32` (_ModeHistoryCache)
- Modify: `src/wenzi/enhance/enhancer.py:610-633` (_build_system_content)
- Modify: `src/wenzi/enhance/enhancer.py:775` (format_entry_line calls)
- Modify: `src/wenzi/enhance/enhancer.py:816` (format_entry_line calls)
- Modify: `src/wenzi/enhance/enhancer.py:905` (enhance)
- Modify: `src/wenzi/enhance/enhancer.py:967` (enhance_stream)
- Test: `tests/test_enhancer.py`

- [ ] **Step 1: Write tests for input_context injection**

Add to `tests/test_enhancer.py`:

```python
class TestInputContextInjection:
    """Tests for input_context in system prompt building."""

    def test_build_system_content_with_input_context(self):
        """Input context should appear at the tail of system content."""
        from wenzi.input_context import InputContext
        from wenzi.enhance.enhancer import TextEnhancer

        config = {
            "enabled": True,
            "default_provider": "test",
            "default_model": "test",
            "providers": {"test": {"base_url": "http://localhost", "api_key": "k", "models": ["m"]}},
            "input_context": "basic",
        }
        enhancer = TextEnhancer(config)
        ctx = InputContext(app_name="Terminal", bundle_id="com.apple.Terminal")

        mode_def = enhancer.get_mode_definition(enhancer.mode)
        if mode_def is None:
            pytest.skip("No default mode available")

        content = enhancer._build_system_content("test text", mode_def, input_context=ctx)
        assert "\u5f53\u524d\u8f93\u5165\u73af\u5883\uff1aTerminal" in content

    def test_build_system_content_without_input_context(self):
        """Without input_context, system content should not contain environment line."""
        from wenzi.enhance.enhancer import TextEnhancer

        config = {
            "enabled": True,
            "default_provider": "test",
            "default_model": "test",
            "providers": {"test": {"base_url": "http://localhost", "api_key": "k", "models": ["m"]}},
        }
        enhancer = TextEnhancer(config)
        mode_def = enhancer.get_mode_definition(enhancer.mode)
        if mode_def is None:
            pytest.skip("No default mode available")

        content = enhancer._build_system_content("test text", mode_def)
        assert "\u5f53\u524d\u8f93\u5165\u73af\u5883" not in content

    def test_input_context_at_tail(self):
        """Input context must be at the end, after context section."""
        from wenzi.input_context import InputContext
        from wenzi.enhance.enhancer import TextEnhancer

        config = {
            "enabled": True,
            "default_provider": "test",
            "default_model": "test",
            "providers": {"test": {"base_url": "http://localhost", "api_key": "k", "models": ["m"]}},
            "input_context": "detailed",
            "conversation_history": {"enabled": True, "max_entries": 5},
        }
        enhancer = TextEnhancer(config)
        ctx = InputContext(
            app_name="Chrome", bundle_id="com.google.Chrome",
            window_title="GitHub", browser_domain="github.com",
        )

        mode_def = enhancer.get_mode_definition(enhancer.mode)
        if mode_def is None:
            pytest.skip("No default mode available")

        content = enhancer._build_system_content("test", mode_def, input_context=ctx)
        env_line = "\u5f53\u524d\u8f93\u5165\u73af\u5883"
        if env_line in content:
            # Should be after the last --- or at the very end
            idx = content.index(env_line)
            assert idx > len(content) // 2  # roughly at the tail
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_enhancer.py::TestInputContextInjection -v`
Expected: FAIL — `_build_system_content` doesn't accept `input_context`

- [ ] **Step 3: Add input_context_level to _ModeHistoryCache**

In `src/wenzi/enhance/enhancer.py`, update `_ModeHistoryCache` (line 26-32) to track the context level:

```python
@dataclass
class _ModeHistoryCache:
    entry_lines: List[str] = field(default_factory=list)
    last_ts: str = ""
    total_chars: int = 0
    last_log_count: int = 0
    last_context_level: str = "off"
```

- [ ] **Step 4: Store input_context_level in enhancer __init__**

In `TextEnhancer.__init__`, after the existing `conversation_history` config reading, add:

```python
        self._input_context_level: str = config.get("input_context", "basic")
```

- [ ] **Step 5: Update _build_system_content to accept input_context**

Replace `_build_system_content` (lines 610-633):

```python
    def _build_system_content(
        self, text: str, mode_def: "ModeDefinition",
        input_context: "InputContext | None" = None,
    ) -> str:
        """Build system prompt with vocabulary, history, and input context.

        Components are ordered by stability (most stable first) so that
        LLM API-level prompt caching can match the longest possible prefix:

        1. mode prompt  — static per mode
        2. thinking hint — static within a session
        3. combined context section — merged history & vocab
        4. input context — dynamic per request (at the tail)
        """
        system_content = mode_def.prompt

        # 1. Static: thinking brevity hint
        if self._thinking:
            system_content = f"{system_content}\n\n{THINKING_BREVITY_HINT}"

        # 2. Combined context section (history + vocab)
        context_section = self._build_context_section(text)
        if context_section:
            system_content = f"{system_content}\n\n{context_section}"

        # 3. Input context at the tail (dynamic per request)
        if input_context is not None:
            env_line = input_context.format_for_prompt(self._input_context_level)
            if env_line:
                system_content = f"{system_content}\n\n{env_line}"

        return system_content
```

- [ ] **Step 6: Update format_entry_line calls to pass context_level**

In `_build_history_context` (line 775), update the `format_entry_line` call:

```python
        new_lines = [ch.format_entry_line(e, context_level=self._input_context_level) for e in new_entries]
```

In `_full_rebuild_history` (line 816), update:

```python
        mc.entry_lines = [ch.format_entry_line(e, context_level=self._input_context_level) for e in base]
```

- [ ] **Step 7: Add cache invalidation on context_level change**

In `_build_history_context`, after the fast-path check (line 724), add a check for context level change:

```python
        # Invalidate cache if context level changed
        if mc.last_context_level != self._input_context_level:
            mc.entry_lines = []  # Force full rebuild
            mc.last_context_level = self._input_context_level
```

- [ ] **Step 8: Update enhance() and enhance_stream() signatures**

In `enhance()` (line 905), add `input_context` parameter:

```python
    async def enhance(
        self, text: str, input_context: "InputContext | None" = None,
    ) -> Tuple[str, Optional[Dict[str, int]]]:
```

And update the `_build_system_content` call inside (line 924):

```python
            system_content = self._build_system_content(text, mode_def, input_context=input_context)
```

In `enhance_stream()` (line 967), add `input_context` parameter:

```python
    async def enhance_stream(
        self, text: str, input_context: "InputContext | None" = None,
    ) -> AsyncIterator[Tuple[str, Optional[Dict[str, int]], bool]]:
```

And update the `_build_system_content` call inside (line 992):

```python
            system_content = self._build_system_content(text, mode_def, input_context=input_context)
```

- [ ] **Step 9: Add TYPE_CHECKING import**

At the top of `enhancer.py`, add:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wenzi.input_context import InputContext
```

- [ ] **Step 10: Run tests**

Run: `uv run pytest tests/test_enhancer.py -v`
Expected: All pass

- [ ] **Step 11: Commit**

```bash
git add src/wenzi/enhance/enhancer.py tests/test_enhancer.py
git commit -m "feat: inject input_context into LLM system prompt"
```

---

### Task 6: EnhanceController Pass-Through

**Files:**
- Modify: `src/wenzi/controllers/enhance_controller.py:97-141` (run)
- Modify: `src/wenzi/controllers/enhance_controller.py:143-244` (_run_single)
- Modify: `src/wenzi/controllers/enhance_controller.py:245-403` (_run_chain)

- [ ] **Step 1: Update run() to accept and forward input_context**

In `enhance_controller.py`, update `run()` signature (line 97):

```python
    def run(
        self,
        asr_text: str,
        request_id: int,
        result_holder: dict | None = None,
        input_context: "InputContext | None" = None,
    ) -> None:
```

Update the `_enhance` closure (lines 124-141) to pass `input_context`:

```python
        def _enhance():
            try:
                if chain_steps:
                    self._run_chain(
                        asr_text, request_id, result_holder, cancel_event,
                        chain_steps, current_mode_def.mode_id,
                        input_context=input_context,
                    )
                else:
                    self._run_single(
                        asr_text, request_id, result_holder, cancel_event,
                        input_context=input_context,
                    )
            except Exception as e:
                logger.error("AI enhancement failed: %s", e)
                self._preview_panel.set_enhance_result(
                    f"(error: {e})", request_id=request_id
                )
```

- [ ] **Step 2: Update _run_single() to forward input_context**

Update signature (line 143):

```python
    def _run_single(
        self, asr_text: str, request_id: int,
        result_holder: dict | None, cancel_event: threading.Event,
        input_context: "InputContext | None" = None,
    ) -> None:
```

Update the `enhance_stream` call inside `_stream()` (line 155):

```python
            gen = self._enhancer.enhance_stream(asr_text, input_context=input_context)
```

- [ ] **Step 3: Update _run_chain() to forward input_context**

Update signature (line 245):

```python
    def _run_chain(
        self, asr_text: str, request_id: int,
        result_holder: dict | None, cancel_event: threading.Event,
        chain_steps: list[str], original_mode_id: str,
        input_context: "InputContext | None" = None,
    ) -> None:
```

Find the `enhance_stream` call inside `_run_chain` and update it to pass `input_context`:

```python
                gen = self._enhancer.enhance_stream(input_text, input_context=input_context)
```

- [ ] **Step 4: Add TYPE_CHECKING import**

At the top of `enhance_controller.py`:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wenzi.input_context import InputContext
```

- [ ] **Step 5: Run existing tests**

Run: `uv run pytest tests/ -v -k enhance`
Expected: All pass (optional param doesn't break callers)

- [ ] **Step 6: Commit**

```bash
git add src/wenzi/controllers/enhance_controller.py
git commit -m "feat: forward input_context through EnhanceController"
```

---

### Task 7: Recording Controller — Capture and Pass

**Files:**
- Modify: `src/wenzi/controllers/recording_controller.py:126-185` (on_hotkey_press)
- Modify: `src/wenzi/controllers/recording_controller.py:743-878` (do_transcribe_direct)
- Modify: `src/wenzi/controllers/recording_controller.py:879-901` (_run_direct_single_stream)

- [ ] **Step 1: Add input_context capture to on_hotkey_press()**

In `recording_controller.py`, at the top of `on_hotkey_press()` (after the guard checks at lines 129-137, before line 139), add:

```python
        # Capture input context while the user's target app is still frontmost
        from wenzi.input_context import capture_input_context
        ic_level = app._config.get("ai_enhance", {}).get("input_context", "basic")
        self._input_context = capture_input_context(ic_level)
```

- [ ] **Step 2: Initialize _input_context in __init__**

In `RecordingController.__init__`, add:

```python
        self._input_context = None
```

- [ ] **Step 3: Pass input_context in do_transcribe_direct()**

Update `do_transcribe_direct` to use `self._input_context`. In the `enhance_stream` call at line 890:

```python
            gen = app._enhancer.enhance_stream(asr_text, input_context=self._input_context)
```

And in the `conversation_history.log()` call (lines 866-875), add:

```python
            app._conversation_history.log(
                asr_text=asr_text,
                enhanced_text=enhanced_text,
                final_text=text.strip(),
                enhance_mode=app._enhance_mode,
                preview_enabled=False,
                stt_model=app._current_stt_model(),
                llm_model=app._current_llm_model(),
                audio_duration=getattr(app, "_last_audio_duration", 0.0),
                input_context=self._input_context,
            )
```

- [ ] **Step 4: Update _run_direct_single_stream**

In `_run_direct_single_stream` (line 890), update the `enhance_stream` call:

```python
            gen = app._enhancer.enhance_stream(asr_text, input_context=self._input_context)
```

- [ ] **Step 5: Update _run_direct_chain_stream**

In `_run_direct_chain_stream` (find the `enhance_stream` call, around line 979), update:

```python
                gen = app._enhancer.enhance_stream(input_text, input_context=self._input_context)
```

Note: Both of these go directly to `enhancer.enhance_stream()`, not through `EnhanceController`. Clipboard enhance calls in `preview_controller.py` should pass `input_context=None` since there's no hotkey-press capture for clipboard enhance.

- [ ] **Step 6: Run existing tests**

Run: `uv run pytest tests/ -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/wenzi/controllers/recording_controller.py
git commit -m "feat: capture input_context on hotkey press and pass through direct mode"
```

---

### Task 8: Preview Controller — Pass input_context

**Files:**
- Modify: `src/wenzi/controllers/preview_controller.py` (multiple enhance_controller.run calls)
- Modify: `src/wenzi/controllers/preview_controller.py:83-133` (_log_with_chain_steps)
- Modify: `src/wenzi/controllers/preview_controller.py:135-179` (_save_to_preview_history)

- [ ] **Step 1: Store input_context reference on preview_controller**

In `PreviewController.__init__` (around line 48), add:

```python
        self._input_context = None
```

- [ ] **Step 2: Update do_transcribe_with_preview() to accept and store input_context**

At the top of `do_transcribe_with_preview()`, after `previous_app = get_frontmost_app()` (line 354), add a way to read the recording controller's input_context:

```python
        self._input_context = app._recording_controller._input_context
```

- [ ] **Step 3: Pass input_context to all enhance_controller.run() calls**

Find all `app._enhance_controller.run(` calls in `preview_controller.py` and add `input_context=self._input_context`. There are multiple call sites (lines 526, 575, 863, 1031, 1155, 1229, 1267, 1325). Each needs:

```python
                app._enhance_controller.run(
                    asr_text, app._preview_panel.enhance_request_id, result_holder,
                    input_context=self._input_context,
                )
```

- [ ] **Step 4: Pass input_context to _log_with_chain_steps**

Update `_log_with_chain_steps` signature to accept and forward `input_context`:

```python
    def _log_with_chain_steps(
        self,
        app: WenZiApp,
        *,
        result_holder: dict,
        asr_text: str,
        final_text: str,
        audio_duration: float = 0.0,
    ) -> str | None:
```

In the `app._conversation_history.log()` call (line 123), add:

```python
        return app._conversation_history.log(
            asr_text=asr_text,
            enhanced_text=result_holder.get("enhanced_text"),
            final_text=final_text,
            enhance_mode=app._enhance_mode,
            preview_enabled=True,
            stt_model=app._current_stt_model(),
            llm_model=app._current_llm_model(),
            user_corrected=bool(result_holder.get("user_corrected")),
            audio_duration=audio_duration,
            input_context=self._input_context,
        )
```

- [ ] **Step 5: Pass input_context to _save_to_preview_history**

In the `PreviewRecord(...)` creation inside `_save_to_preview_history` (line 161), add:

```python
            input_context=self._input_context,
```

- [ ] **Step 6: Pass input_context when viewing history records**

In `on_select_history()` (around line 227), also sync the input_context:

```python
        # Store the record's input_context for display
        self._input_context = record.input_context
```

- [ ] **Step 7: Run existing tests**

Run: `uv run pytest tests/ -v`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add src/wenzi/controllers/preview_controller.py
git commit -m "feat: pass input_context through preview controller flow"
```

---

### Task 9: Preview Panel UI — Context Button

**Files:**
- Modify: `src/wenzi/ui/result_window_web.py` (HTML, JS, message handler)

- [ ] **Step 1: Add context button to HTML**

In the enhance section header (line 412), after the `prompt-btn` button, add:

```html
            <button class="btn disabled" id="context-btn" onclick="postAction('showContext')"
                style="opacity:0.3;">📍</button>
```

- [ ] **Step 2: Add JS functions for context button**

After `enablePromptButton()` (line 759), add:

```javascript
function enableContextButton() {
    const cb = document.getElementById('context-btn');
    cb.classList.remove('disabled'); cb.style.opacity = '1';
}

function disableContextButton() {
    const cb = document.getElementById('context-btn');
    cb.classList.add('disabled'); cb.style.opacity = '0.3';
}
```

In `setEnhanceLoading()` (around line 727), add to reset context button:

```javascript
    const _cb = document.getElementById('context-btn');
    _cb.classList.add('disabled'); _cb.style.opacity = '0.3';
```

In `setEnhanceOff()` (around line 736), also reset:

```javascript
    const _cb = document.getElementById('context-btn');
    _cb.classList.add('disabled'); _cb.style.opacity = '0.3';
```

In the `loadHistoryRecord` JS function, add context button handling alongside the existing `hasPrompt`/`hasThinking` logic:

```javascript
    const cb = document.getElementById('context-btn');
    if (data.hasContext) {
        cb.classList.remove('disabled'); cb.style.opacity = '1';
    } else {
        cb.classList.add('disabled'); cb.style.opacity = '0.3';
    }
```

- [ ] **Step 3: Store input_context on the panel and handle messages**

Add `_input_context_text` attribute in `__init__`:

```python
        self._input_context_text: str = ""
```

Add a method to set the context:

```python
    def set_input_context(self, text: str) -> None:
        """Cache input context display text and enable the button."""
        self._input_context_text = text
        if text:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(lambda: self._eval_js("enableContextButton()") if self._webview else None)
```

In the message handler (around line 1673), add:

```python
        elif msg_type == "showContext":
            if self._input_context_text:
                self._show_info_panel("Input Context", self._input_context_text)
```

- [ ] **Step 4: Update load_history_record to pass hasContext**

In `load_history_record()` method, add `hasContext` to the data dict:

```python
            "hasContext": bool(self._input_context_text),
```

- [ ] **Step 5: Wire up in preview_controller**

In `preview_controller.py`, after enhancement starts (or when panel opens), set the context:

After `self._input_context = app._recording_controller._input_context` in `do_transcribe_with_preview()`, add:

```python
        if self._input_context:
            app._preview_panel.set_input_context(self._input_context.format_for_display())
        else:
            app._preview_panel.set_input_context("")
```

In `on_select_history()`, after loading the record, add:

```python
        if record.input_context:
            app._preview_panel.set_input_context(record.input_context.format_for_display())
        else:
            app._preview_panel.set_input_context("")
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/ -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/wenzi/ui/result_window_web.py src/wenzi/controllers/preview_controller.py
git commit -m "feat: add context button to preview panel for viewing input environment"
```

---

### Task 10: Settings UI

**Files:**
- Modify: `src/wenzi/ui/settings_window.py` (AI tab)
- Modify: `src/wenzi/controllers/settings_controller.py` (state + callback)

- [ ] **Step 1: Add input_context to settings state dict**

In `settings_controller.py`, in the `on_open_settings` method's state dict (around line 132), add:

```python
            "input_context_level": app._config.get("ai_enhance", {}).get("input_context", "basic"),
```

- [ ] **Step 2: Add callback**

In the callbacks dict (around line 166), add:

```python
            "on_input_context_change": self.input_context_change,
```

Add the handler method in `SettingsController`:

```python
    def input_context_change(self, level: str) -> None:
        """Handle input context level change from Settings panel."""
        app = self._app
        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"]["input_context"] = level
        if app._enhancer:
            app._enhancer._input_context_level = level
        save_config(app._config, app._config_dir)
```

- [ ] **Step 3: Add dropdown and hint in settings_window.py**

In `_build_ai_tab()`, after the Thinking section (line 1005) and before the Vocabulary section (line 1007), add:

```python
        # Input Context level
        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        ic_label = self._make_label(
            "Input Context", pad + 12, y, 110, small_font,
        )
        doc_view.addSubview_(ic_label)

        ic_items = [
            ("off", "Off"),
            ("basic", "Basic"),
            ("detailed", "Detailed"),
        ]
        current_ic = state.get("input_context_level", "basic")
        self._input_context_popup = self._make_popup(
            ic_items, current_ic,
            pad + 12 + 110, y, 120, small_font,
            b"inputContextChanged:", doc_view,
        )

        _IC_HINTS = {
            "off": "No app info is sent to the AI model.",
            "basic": "App name is sent to help the AI adapt to your context.",
            "detailed": "App name, window title, and other details are sent for better accuracy. May include sensitive info.",
        }
        y = self._add_hint(
            _IC_HINTS.get(current_ic, _IC_HINTS["basic"]),
            pad + 12, y, content_w - 24, doc_view,
        )
        # Store reference to the hint label for dynamic update
        # _add_hint returns new y, the label is the last subview added
        self._input_context_hint_label = doc_view.subviews()[-1]
```

Store the hint labels dict as a class constant at the top of the class:

```python
_IC_HINTS = {
    "off": "No app info is sent to the AI model.",
    "basic": "App name is sent to help the AI adapt to your context.",
    "detailed": "App name, window title, and other details are sent for better accuracy. May include sensitive info.",
}
```

- [ ] **Step 4: Add callback method in settings_window.py**

In the callbacks section (around line 1749):

```python
    def inputContextChanged_(self, sender):
        value = sender.selectedItem().representedObject()
        if value is not None:
            self._call("on_input_context_change", str(value))
            # Update dynamic hint label
            hint = self._IC_HINTS.get(str(value), self._IC_HINTS["basic"])
            if hasattr(self, "_input_context_hint_label") and self._input_context_hint_label:
                self._input_context_hint_label.setStringValue_(hint)
```

- [ ] **Step 5: Add callbacks to show() docstring**

In `show()` docstring (around line 163), add:

```python
                - on_input_context_change: (level) -> None
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/ -v`
Expected: All pass

- [ ] **Step 7: Run lint**

Run: `uv run ruff check`
Expected: 0 errors

- [ ] **Step 8: Commit**

```bash
git add src/wenzi/ui/settings_window.py src/wenzi/controllers/settings_controller.py
git commit -m "feat: add Input Context dropdown to Settings AI tab"
```

---

### Task 11: Final Integration Test and Lint

**Files:**
- All modified files

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v --cov=wenzi`
Expected: All pass, no regressions

- [ ] **Step 2: Run linter**

Run: `uv run ruff check`
Expected: 0 errors

- [ ] **Step 3: Fix any issues found**

If tests fail or lint errors exist, fix them.

- [ ] **Step 4: Final commit if needed**

```bash
git add -A
git commit -m "fix: address lint and test issues for input context feature"
```
