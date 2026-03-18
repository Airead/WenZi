"""Snippet keyword auto-expansion via Quartz CGEventTap.

Monitors typed characters and automatically expands snippet keywords
(e.g. typing "/lsof/" gets replaced with the snippet content).

Uses CGEventKeyboardGetUnicodeString to read the actual character
from each keyDown event, maintaining a rolling buffer that is checked
against all known snippet keywords after every keystroke.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import threading
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from wenzi.scripting.sources.snippet_source import SnippetStore

logger = logging.getLogger(__name__)

# Backspace virtual keycode on macOS
_VK_DELETE = 51

# Maximum buffer length — keywords longer than this are not supported
_MAX_BUFFER = 128

# Keycodes that should clear the character buffer (navigation / control)
_CLEAR_KEYCODES = {
    36,  # return
    48,  # tab
    51,  # delete (backspace)
    53,  # escape
    76,  # enter (numpad)
    117,  # forward delete
    123,  # left arrow
    124,  # right arrow
    125,  # down arrow
    126,  # up arrow
}


_carbon = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Carbon"))


def _get_unicode_string(event) -> str:
    """Extract the Unicode string from a Quartz CGEvent using ctypes."""
    import objc

    length = ctypes.c_uint32(0)
    buf = (ctypes.c_uint16 * 4)()
    _carbon.CGEventKeyboardGetUnicodeString(
        ctypes.c_void_p(objc.pyobjc_id(event)),
        ctypes.c_uint32(4),
        ctypes.byref(length),
        buf,
    )
    if length.value == 0:
        return ""
    return "".join(chr(buf[i]) for i in range(length.value))


class SnippetExpander:
    """Watch typed characters and auto-expand snippet keywords.

    When the user types a sequence that matches a snippet keyword,
    the keyword text is deleted (via synthetic backspace events) and
    replaced with the snippet content (via clipboard paste).
    """

    def __init__(self, store: "SnippetStore") -> None:
        self._store = store
        self._buffer = ""
        self._lock = threading.Lock()
        self._tap = None
        self._loop = None
        self._thread: Optional[threading.Thread] = None
        self._expanding = False  # Guard against re-entrance during expansion
        self._suppressed = False  # True while our own panels are key

    # -- public API ----------------------------------------------------------

    def suppress(self) -> None:
        """Temporarily suppress expansion (e.g. while launcher is open)."""
        self._suppressed = True
        with self._lock:
            self._buffer = ""

    def resume(self) -> None:
        """Resume expansion after suppression."""
        self._suppressed = False
        with self._lock:
            self._buffer = ""

    def start(self) -> None:
        """Start listening for keystrokes."""
        import Quartz
        from wenzi.hotkey import _pre_resolve_quartz

        _pre_resolve_quartz()

        _CGEventMaskBit = Quartz.CGEventMaskBit
        _kCGEventKeyDown = Quartz.kCGEventKeyDown
        _CGEventTapCreate = Quartz.CGEventTapCreate
        _kCGSessionEventTap = Quartz.kCGSessionEventTap
        _kCGHeadInsertEventTap = Quartz.kCGHeadInsertEventTap
        _kCGEventTapOptionListenOnly = Quartz.kCGEventTapOptionListenOnly
        _CFMachPortCreateRunLoopSource = Quartz.CFMachPortCreateRunLoopSource
        _CFRunLoopGetCurrent = Quartz.CFRunLoopGetCurrent
        _CFRunLoopAddSource = Quartz.CFRunLoopAddSource
        _kCFRunLoopDefaultMode = Quartz.kCFRunLoopDefaultMode
        _CGEventTapEnable = Quartz.CGEventTapEnable
        _CFRunLoopRun = Quartz.CFRunLoopRun

        def _run():
            mask = _CGEventMaskBit(_kCGEventKeyDown)
            self._tap = _CGEventTapCreate(
                _kCGSessionEventTap,
                _kCGHeadInsertEventTap,
                _kCGEventTapOptionListenOnly,
                mask,
                self._callback,
                None,
            )
            if self._tap is None:
                logger.error(
                    "SnippetExpander: failed to create event tap. "
                    "Check accessibility permissions."
                )
                return

            source = _CFMachPortCreateRunLoopSource(None, self._tap, 0)
            self._loop = _CFRunLoopGetCurrent()
            _CFRunLoopAddSource(self._loop, source, _kCFRunLoopDefaultMode)
            _CGEventTapEnable(self._tap, True)
            logger.info("SnippetExpander started")
            _CFRunLoopRun()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop listening."""
        import Quartz

        if self._loop is not None:
            Quartz.CFRunLoopStop(self._loop)
            self._loop = None
        self._tap = None
        with self._lock:
            self._buffer = ""
        logger.info("SnippetExpander stopped")

    # -- internals -----------------------------------------------------------

    def _callback(self, proxy, event_type, event, refcon):
        """CGEventTap callback — runs on the tap's background thread."""
        try:
            import Quartz

            if event_type == Quartz.kCGEventTapDisabledByTimeout:
                logger.warning("SnippetExpander tap disabled by timeout, re-enabling")
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event

            if self._expanding or self._suppressed:
                return event

            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode,
            )
            flags = Quartz.CGEventGetFlags(event)

            # Ignore events with Cmd/Ctrl/Alt modifiers (shortcuts, not text)
            mod_mask = (
                Quartz.kCGEventFlagMaskCommand
                | Quartz.kCGEventFlagMaskControl
                | Quartz.kCGEventFlagMaskAlternate
            )
            if flags & mod_mask:
                with self._lock:
                    self._buffer = ""
                return event

            # Navigation / control keys clear the buffer
            if keycode in _CLEAR_KEYCODES:
                with self._lock:
                    self._buffer = ""
                return event

            # Extract the actual character typed
            char = _get_unicode_string(event)
            if not char or not char.isprintable():
                return event

            # Append to buffer
            with self._lock:
                self._buffer += char
                if len(self._buffer) > _MAX_BUFFER:
                    self._buffer = self._buffer[-_MAX_BUFFER:]
                buf = self._buffer

            # Check for keyword match at the end of buffer
            self._check_expansion(buf)

        except Exception:
            logger.warning("SnippetExpander callback exception", exc_info=True)

        return event

    def _check_expansion(self, buf: str) -> None:
        """Check if the buffer ends with a snippet keyword and trigger expansion."""
        import random as _random

        snippets = self._store.snippets
        for s in snippets:
            keyword = s.get("keyword", "")
            if not keyword:
                continue
            if not s.get("auto_expand", True):
                continue
            if buf.endswith(keyword):
                # Pick a random variant when available, otherwise use content
                variants = s.get("variants")
                if s.get("random", False) and variants:
                    content = _random.choice(variants)
                else:
                    content = s.get("content", "")
                raw = s.get("raw", False)
                logger.info(
                    "Snippet keyword matched: %r -> %r",
                    keyword, content[:50],
                )
                # Clear the buffer before expanding
                with self._lock:
                    self._buffer = ""
                # Run expansion in a separate thread to avoid blocking the tap
                threading.Thread(
                    target=self._expand,
                    args=(keyword, content, raw),
                    daemon=True,
                ).start()
                return

    def _expand(self, keyword: str, content: str, raw: bool = False) -> None:
        """Delete the keyword text and paste the snippet content."""
        self._expanding = True
        try:
            if raw:
                expanded = content
            else:
                from wenzi.scripting.sources.snippet_source import (
                    _expand_placeholders,
                )

                expanded = _expand_placeholders(content)

            # Send backspace keys to delete the keyword
            self._send_backspaces(len(keyword))
            time.sleep(0.05)

            # Paste the snippet content
            from wenzi.input import _set_pasteboard_concealed

            _set_pasteboard_concealed(expanded)
            time.sleep(0.05)

            import subprocess

            subprocess.run(
                [
                    "osascript", "-e",
                    'tell application "System Events" to keystroke "v" '
                    "using command down",
                ],
                capture_output=True, timeout=5,
            )
        except Exception:
            logger.exception("Failed to expand snippet %r", keyword)
        finally:
            self._expanding = False

    @staticmethod
    def _send_backspaces(count: int) -> None:
        """Send *count* backspace keystrokes via Quartz CGEvent."""
        import Quartz

        for _ in range(count):
            down = Quartz.CGEventCreateKeyboardEvent(None, _VK_DELETE, True)
            Quartz.CGEventPost(Quartz.kCGAnnotatedSessionEventTap, down)
            up = Quartz.CGEventCreateKeyboardEvent(None, _VK_DELETE, False)
            Quartz.CGEventPost(Quartz.kCGAnnotatedSessionEventTap, up)
            time.sleep(0.01)
