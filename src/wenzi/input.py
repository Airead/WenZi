"""Text injection into the active macOS application."""

from __future__ import annotations

import logging
import subprocess
import threading
import time

import objc
from AppKit import NSPasteboard, NSPasteboardTypeString, NSString

logger = logging.getLogger(__name__)


def get_clipboard_text() -> str | None:
    """Read the current plain-text content from the system clipboard."""
    return _get_pasteboard_string()


def has_clipboard_text() -> bool:
    """Check whether the clipboard contains plain-text content."""
    pb = NSPasteboard.generalPasteboard()
    return pb.availableTypeFromArray_([NSPasteboardTypeString]) is not None


def set_clipboard_text(text: str) -> None:
    """Write text to the system clipboard (visible in clipboard history)."""
    _set_pasteboard_string(text)


def copy_selection_to_clipboard() -> bool:
    """Simulate Cmd+C to copy the current selection to the clipboard.

    Uses CGEvent directly so that only the Command modifier is set,
    regardless of which physical modifier keys the user is still holding.
    Checks for a text selection via Accessibility API first to avoid
    triggering a system beep when nothing is selected.

    Returns True if the clipboard content changed (selection was copied),
    False otherwise.
    """
    if not _has_text_selection():
        return False

    old = get_clipboard_text()

    # Brief pause so the system finishes processing the trigger hotkey
    time.sleep(0.05)

    try:
        _send_cmd_c()
    except Exception as exc:
        logger.warning("Simulate Cmd+C failed: %s", exc)
        return False

    time.sleep(0.15)
    new = get_clipboard_text()
    return old != new


def _send_cmd_c() -> None:
    """Post a synthetic Cmd+C keystroke via Quartz CGEvent.

    Unlike osascript ``keystroke``, CGEvent lets us set modifier flags
    explicitly so physical keys (e.g. ctrl held from the trigger hotkey)
    do not leak into the synthesised event.
    """
    import Quartz

    _C_KEYCODE = 8  # virtual keycode for 'c'

    # Key down
    event_down = Quartz.CGEventCreateKeyboardEvent(None, _C_KEYCODE, True)
    Quartz.CGEventSetFlags(event_down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGAnnotatedSessionEventTap, event_down)

    # Key up
    event_up = Quartz.CGEventCreateKeyboardEvent(None, _C_KEYCODE, False)
    Quartz.CGEventSetFlags(event_up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGAnnotatedSessionEventTap, event_up)


def get_selected_text() -> str | None:
    """Get the currently selected text from the frontmost application.

    Fallback chain:
      1. AXSelectedText via Accessibility API (fast, no clipboard pollution)
      2. Simulate Cmd+C and read clipboard (broader compatibility)
      3. Current clipboard content (last resort)

    Returns the selected text, or None if no text could be obtained.
    """
    # 1. Try Accessibility API
    text = _get_ax_selected_text()
    if text:
        return text

    # 2. Try Cmd+C simulation
    text = _get_text_via_cmd_c()
    if text:
        return text

    # 3. Fall back to current clipboard
    text = get_clipboard_text()
    return text if text else None


def _get_ax_selected_text() -> str | None:
    """Read AXSelectedText from the focused UI element.

    Returns the selected text string, or None on error/empty.
    """
    try:
        from ApplicationServices import (
            AXUIElementCreateSystemWide,
            AXUIElementCopyAttributeValue,
        )

        system = AXUIElementCreateSystemWide()
        err, focused_app = AXUIElementCopyAttributeValue(
            system, "AXFocusedApplication", None
        )
        if err != 0 or focused_app is None:
            return None

        err, focused_elem = AXUIElementCopyAttributeValue(
            focused_app, "AXFocusedUIElement", None
        )
        if err != 0 or focused_elem is None:
            return None

        err, selected_text = AXUIElementCopyAttributeValue(
            focused_elem, "AXSelectedText", None
        )
        if err != 0 or selected_text is None:
            return None

        text = str(selected_text)
        return text if text else None
    except Exception as exc:
        logger.debug("AX selected text read failed: %s", exc)
        return None


def _get_text_via_cmd_c() -> str | None:
    """Simulate Cmd+C to capture the selection, then read clipboard.

    Bypasses the AX pre-check in ``copy_selection_to_clipboard()`` because
    this function is only called when AX already failed — the guard would
    reject the same apps we're trying to support via Cmd+C.

    Saves and restores the original clipboard content.
    Returns the captured text, or None if nothing was copied.
    """
    old_clip = get_clipboard_text()

    time.sleep(0.05)
    try:
        _send_cmd_c()
    except Exception as exc:
        logger.debug("Cmd+C simulation failed: %s", exc)
        return None

    time.sleep(0.15)
    new_clip = get_clipboard_text()
    if new_clip == old_clip:
        return None  # Cmd+C didn't change clipboard — no selection

    # Restore original clipboard in background
    if old_clip is not None:
        def _restore():
            time.sleep(0.5)
            try:
                _set_pasteboard_concealed(old_clip)
            except Exception:
                pass
        threading.Thread(target=_restore, daemon=True).start()

    return new_clip


def _has_text_selection() -> bool:
    """Check if the frontmost application has a non-empty text selection.

    Uses the macOS Accessibility API (AXUIElement) to query AXSelectedText
    on the focused UI element. Returns False if there is no selection,
    accessibility is unavailable, or the focused element has no text.
    """
    try:
        from ApplicationServices import (
            AXUIElementCreateSystemWide,
            AXUIElementCopyAttributeValue,
        )

        system = AXUIElementCreateSystemWide()
        err, focused_app = AXUIElementCopyAttributeValue(
            system, "AXFocusedApplication", None
        )
        if err != 0 or focused_app is None:
            return False

        err, focused_elem = AXUIElementCopyAttributeValue(
            focused_app, "AXFocusedUIElement", None
        )
        if err != 0 or focused_elem is None:
            return False

        err, selected_text = AXUIElementCopyAttributeValue(
            focused_elem, "AXSelectedText", None
        )
        if err != 0 or selected_text is None:
            return False

        return len(str(selected_text)) > 0
    except Exception as exc:
        logger.debug("Accessibility selection check failed: %s", exc)
        return False


def type_text(text: str, append_newline: bool = False, method: str = "auto") -> None:
    """Type text into the currently focused text field on macOS.

    Methods:
        clipboard: pbcopy + Cmd+V (fast, reliable)
        applescript: AppleScript keystroke (good Unicode support)
        auto: try clipboard first, fall back to applescript
    """
    if not text:
        return

    payload = text + ("\n" if append_newline else "")

    method = (method or "auto").lower()
    if method == "clipboard":
        order = ["clipboard"]
    elif method == "applescript":
        order = ["applescript"]
    else:
        order = ["clipboard", "applescript"]

    for mode in order:
        if mode == "clipboard" and _type_via_clipboard(payload):
            logger.info("Text injected via clipboard: %s", payload[:50])
            return
        if mode == "applescript" and _type_via_applescript(payload):
            logger.info("Text injected via applescript: %s", payload[:50])
            return

    logger.error("All text injection methods failed")


def _get_pasteboard_string() -> str | None:
    """Read the current plain-text content from the system pasteboard."""
    pb = NSPasteboard.generalPasteboard()
    return pb.stringForType_(NSPasteboardTypeString)


def _set_pasteboard_concealed(text: str) -> bool:
    """Write *text* to the pasteboard with concealed/transient markers.

    Clipboard history managers (Paste, Maccy, Raycast, etc.) honour
    ``org.nspasteboard.ConcealedType`` and ``com.nspasteboard.TransientType``
    and will skip entries that carry these types.
    """
    with objc.autorelease_pool():
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        ns_str = NSString.stringWithString_(text)
        ok = pb.setString_forType_(ns_str, NSPasteboardTypeString)
        if not ok:
            return False
        # Marker types – the value is irrelevant; their presence is the signal.
        pb.setString_forType_("", "org.nspasteboard.ConcealedType")
        pb.setString_forType_("", "com.nspasteboard.TransientType")
        return True


def _set_pasteboard_string(text: str) -> None:
    """Write *text* to the pasteboard without concealed markers (for restore)."""
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    ns_str = NSString.stringWithString_(text)
    pb.setString_forType_(ns_str, NSPasteboardTypeString)


def _type_via_clipboard(payload: str) -> bool:
    """Copy to clipboard then simulate Cmd+V."""
    old_clip = _get_pasteboard_string()

    try:
        if not _set_pasteboard_concealed(payload):
            logger.warning("NSPasteboard setString failed")
            return False

        # Small delay to ensure clipboard is ready
        time.sleep(0.05)

        result = subprocess.run(
            [
                "osascript", "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ],
            capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            logger.warning("Cmd+V osascript failed: %s",
                           result.stderr.decode(errors="replace"))
            return False
        return True
    except Exception as exc:
        logger.warning("Clipboard injection failed: %s", exc)
        return False
    finally:
        if old_clip is not None:
            def _restore():
                time.sleep(1.0)
                try:
                    # Use concealed markers so clipboard monitors skip
                    # this restore and don't record a ghost entry.
                    _set_pasteboard_concealed(old_clip)
                except Exception:
                    pass
            threading.Thread(target=_restore, daemon=True).start()


def _type_via_applescript(payload: str) -> bool:
    """Use AppleScript keystroke to type text.

    The script is passed via stdin to avoid command-line injection through
    user-controlled text.
    """
    try:
        escaped = payload.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "System Events" to keystroke "{escaped}"'
        result = subprocess.run(
            ["osascript"],
            input=script,
            capture_output=True, timeout=10,
            text=True,
        )
        if result.returncode != 0:
            logger.warning("AppleScript keystroke failed: %s", result.stderr)
            return False
        return True
    except Exception as exc:
        logger.warning("AppleScript injection failed: %s", exc)
        return False
