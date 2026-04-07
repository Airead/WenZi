"""vt.keystroke — synthesize keyboard events via Quartz."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def keystroke(key: str, modifiers: list[str] | None = None) -> None:
    """Synthesize a keystroke using CGEvent.

    Args:
        key: Key name (e.g. "c", "v", "space", "return").
        modifiers: Optional modifier names (e.g. ["cmd"], ["cmd", "shift"]).
    """
    import Quartz

    from wenzi.hotkey import _MOD_FLAGS, _name_to_vk

    vk = _name_to_vk(key)

    # Build modifier flags
    flags = 0
    for mod in (modifiers or []):
        mod_lower = mod.strip().lower()
        if mod_lower in _MOD_FLAGS:
            flags |= _MOD_FLAGS[mod_lower]
        else:
            logger.warning("Unknown modifier: %s", mod)

    # Key down
    event_down = Quartz.CGEventCreateKeyboardEvent(None, vk, True)
    if flags:
        Quartz.CGEventSetFlags(event_down, flags)
    Quartz.CGEventPost(Quartz.kCGAnnotatedSessionEventTap, event_down)
    Quartz.CFRelease(event_down)

    # Key up
    event_up = Quartz.CGEventCreateKeyboardEvent(None, vk, False)
    if flags:
        Quartz.CGEventSetFlags(event_up, flags)
    Quartz.CGEventPost(Quartz.kCGAnnotatedSessionEventTap, event_up)
    Quartz.CFRelease(event_up)

    logger.debug("Keystroke: %s (modifiers=%s)", key, modifiers)
