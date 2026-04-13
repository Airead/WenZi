"""Global hotkey listener for press-and-hold interaction.

All key listening uses Quartz CGEventTap with thread-safe C APIs only.
NSEvent.eventWithCGEvent_ is NOT used anywhere — it creates AppKit objects
on background threads and races with the main thread's event dispatch,
causing intermittent crashes on Caps Lock / input method switching.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from wenzi.async_loop import TimerHandle, call_later

logger = logging.getLogger(__name__)

# Shared single-thread executor for dispatching hotkey callbacks off the
# CGEventTap thread.  Reuses one thread instead of spawning a new one per
# keypress.  max_workers=1 is safe because the callbacks are lightweight
# async dispatchers (submit to asyncio loop / put_nowait to queue).
_hotkey_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hotkey")


def shutdown_hotkey_executor() -> None:
    """Shut down the shared hotkey executor (call during app quit)."""
    _hotkey_executor.shutdown(wait=False)


def _submit_callback(fn: Callable, *args) -> None:
    """Submit a callback to the shared hotkey executor with error logging."""
    def _safe():
        try:
            fn(*args)
        except Exception as e:
            logger.error("Hotkey callback error (%s): %s", fn.__name__, e)
    _hotkey_executor.submit(_safe)


# --- Virtual keycode mappings ---

_KEYCODE_MAP = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32,
    "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
}

# Special key virtual keycodes (macOS)
_SPECIAL_VK = {
    "f1": 122, "f2": 120, "f3": 99, "f4": 118,
    "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    "f13": 105, "f14": 107, "f15": 113, "f16": 106,
    "f17": 64, "f18": 79, "f19": 80, "f20": 90,
    "fn": 63, "esc": 53, "space": 49,
    "return": 36, "delete": 51, "tab": 48,
    "up": 126, "down": 125, "left": 123, "right": 124,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "printscreen": 105,  # PC keyboards map PrintScreen to F13
    # Keypad (kp = keypad, distinguishes from main keyboard keys)
    "kp0": 82, "kp1": 83, "kp2": 84, "kp3": 85,
    "kp4": 86, "kp5": 87, "kp6": 88, "kp7": 89,
    "kp8": 91, "kp9": 92,
    "kp.": 65, "kp*": 67, "kp+": 69, "kp-": 78,
    "kp/": 75, "kp_enter": 76, "kp=": 81, "kp_clear": 71,
}

# Modifier key virtual keycodes and their CGEventFlags bitmask
_MOD_VK = {
    "cmd": (55, 0x100000), "cmd_r": (54, 0x100000),
    "ctrl": (59, 0x040000), "ctrl_r": (62, 0x040000),
    "alt": (58, 0x080000), "alt_r": (61, 0x080000),
    "shift": (56, 0x020000), "shift_r": (60, 0x020000),
}

# Reverse lookup: virtual keycode -> key name
_VK_TO_NAME: dict[int, str] = {}
_VK_TO_NAME.update({vk: name for name, vk in _KEYCODE_MAP.items()})
_VK_TO_NAME.update({vk: name for name, vk in _SPECIAL_VK.items()})
_VK_TO_NAME.update({vk: name for name, (vk, _flag) in _MOD_VK.items()})

# All known key names (for validation)
_ALL_KEY_NAMES = set(_KEYCODE_MAP) | set(_SPECIAL_VK) | set(_MOD_VK) | {"option", "command"}

def _normalize_key_name(name: str) -> str:
    """Normalize a key name: strip, lowercase, and map aliases."""
    n = name.strip().lower()
    if n == "option":
        n = "alt"
    elif n == "command":
        n = "cmd"
    return n


# Snapshot built-in maps so script-registered keys can be cleanly reverted
_BUILTIN_SPECIAL_VK = dict(_SPECIAL_VK)
_BUILTIN_VK_TO_NAME = dict(_VK_TO_NAME)
_BUILTIN_ALL_KEY_NAMES = set(_ALL_KEY_NAMES)

# Modifier key names (for combo hotkey recording)
MODIFIER_KEY_NAMES = set(_MOD_VK.keys())  # {"cmd", "cmd_r", "ctrl", ...}

# Modifier flag constants
_MOD_FLAGS = {
    "cmd": 0x100000, "command": 0x100000,
    "ctrl": 0x040000,
    "alt": 0x080000, "option": 0x080000,
    "shift": 0x020000,
}
_MOD_MASK = 0x100000 | 0x040000 | 0x080000 | 0x020000
_FN_FLAG = 0x800000  # NSEventModifierFlagFunction
_FN_KEYCODE = 63


def register_custom_key(name: str, keycode: int) -> None:
    """Register a custom key name → virtual keycode mapping (for user scripts)."""
    name = name.strip().lower()
    _SPECIAL_VK[name] = keycode
    _VK_TO_NAME[keycode] = name
    _ALL_KEY_NAMES.add(name)


def unregister_custom_keys() -> None:
    """Remove all script-registered keys and restore built-in maps."""
    _SPECIAL_VK.clear()
    _SPECIAL_VK.update(_BUILTIN_SPECIAL_VK)
    _VK_TO_NAME.clear()
    _VK_TO_NAME.update(_BUILTIN_VK_TO_NAME)
    _ALL_KEY_NAMES.clear()
    _ALL_KEY_NAMES.update(_BUILTIN_ALL_KEY_NAMES)


def _name_to_vk(name: str) -> int:
    """Convert a key name to its macOS virtual keycode."""
    name = _normalize_key_name(name)
    if name in _KEYCODE_MAP:
        return _KEYCODE_MAP[name]
    if name in _SPECIAL_VK:
        return _SPECIAL_VK[name]
    if name in _MOD_VK:
        return _MOD_VK[name][0]
    raise ValueError(f"Unknown key: {name}")


def _is_fn_key(name: str) -> bool:
    return name.strip().lower() == "fn"


_MODIFIER_VKS = frozenset(vk for vk, _flag in _MOD_VK.values())


def _is_modifier_like_vk(vk: int) -> bool:
    """Check if a virtual keycode is a modifier or fn (no character output)."""
    return vk in _MODIFIER_VKS or vk == _FN_KEYCODE


def _parse_hotkey_for_quartz(hotkey_str: str) -> tuple[int, int]:
    """Parse a hotkey string into (modifier_flags, keycode) for Quartz.

    Args:
        hotkey_str: Hotkey string like "ctrl+cmd+v".

    Returns:
        Tuple of (modifier_flags_bitmask, trigger_keycode).

    Raises:
        ValueError: If the hotkey string is invalid.
    """
    parts = [p.strip().lower() for p in hotkey_str.strip().split("+")]
    if not parts:
        raise ValueError(f"Empty hotkey string: {hotkey_str!r}")

    mod_flags = 0
    trigger_keys = []
    for part in parts:
        part = _normalize_key_name(part)
        if part in _MOD_FLAGS:
            mod_flags |= _MOD_FLAGS[part]
        elif part in _KEYCODE_MAP:
            trigger_keys.append(("letter", part))
        elif part in _SPECIAL_VK:
            trigger_keys.append(("special", part))
        else:
            raise ValueError(f"Unknown key in hotkey: {part!r}")

    if mod_flags == 0:
        raise ValueError(f"Hotkey must include at least one modifier: {hotkey_str!r}")
    if len(trigger_keys) != 1:
        raise ValueError(
            f"Hotkey must include exactly one trigger key, got {len(trigger_keys)}: {hotkey_str!r}"
        )

    kind, key = trigger_keys[0]
    vk = _KEYCODE_MAP[key] if kind == "letter" else _SPECIAL_VK[key]
    return mod_flags, vk


# ---------------------------------------------------------------------------
# Quartz-based key listener (thread-safe, no NSEvent)
# ---------------------------------------------------------------------------

class _QuartzAllKeysListener:
    """Listen for key press/release via Quartz CGEventTap using only C APIs.

    Monitors kCGEventKeyDown, kCGEventKeyUp, and kCGEventFlagsChanged.
    Callbacks receive the key name (str) and are called on a background thread.

    When ``listen_only=False`` (active tap), the ``on_press`` callback may
    return ``True`` to swallow the event (prevent it from reaching the
    focused application).
    """

    def __init__(
        self,
        on_press: Callable[[str], None],
        on_release: Callable[[str], None],
        listen_only: bool = True,
    ) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._listen_only = listen_only
        self._runner = None  # CGEventTapRunner | None
        # Track modifier key states to detect press vs release.
        # Snapshot current flags so a modifier held across listener
        # restart is not misinterpreted as a new press.
        try:
            from wenzi import _cgeventtap as _cg
            self._mod_flags_prev = _cg.CGEventSourceFlagsState(
                _cg.kCGEventSourceStateCombinedSessionState
            )
        except Exception:
            self._mod_flags_prev = 0

    def _callback(self, proxy, event_type, event, refcon):
        from wenzi import _cgeventtap as cg

        try:
            if event_type == cg.kCGEventTapDisabledByTimeout:
                logger.warning("CGEventTap disabled by timeout, re-enabling")
                if self._runner is not None and self._runner.tap is not None:
                    cg.CGEventTapEnable(self._runner.tap, True)
                # Re-sync modifier flags: if a modifier key was released
                # while the tap was disabled, the release event is lost
                # forever.  Poll the current system flags and fire synthetic
                # releases for any modifiers that went away.
                try:
                    new_flags = cg.CGEventSourceFlagsState(
                        cg.kCGEventSourceStateCombinedSessionState
                    )
                    seen_masks: set = set()
                    for _name, (_vk, _mask) in _MOD_VK.items():
                        if _mask in seen_masks:
                            continue
                        seen_masks.add(_mask)
                        if (self._mod_flags_prev & _mask) and not (new_flags & _mask):
                            self._on_release(_name)
                    if (self._mod_flags_prev & _FN_FLAG) and not (new_flags & _FN_FLAG):
                        self._on_release("fn")
                    self._mod_flags_prev = new_flags
                except Exception:
                    logger.warning(
                        "Failed to re-sync modifier flags", exc_info=True
                    )
                return event

            keycode = cg.CGEventGetIntegerValueField(
                event, cg.kCGKeyboardEventKeycode
            )
            flags = cg.CGEventGetFlags(event) if event_type == cg.kCGEventFlagsChanged else 0

            if event_type == cg.kCGEventKeyDown:
                name = _VK_TO_NAME.get(keycode)
                if name:
                    swallow = self._on_press(name)
                    if swallow and not self._listen_only:
                        return None

            elif event_type == cg.kCGEventKeyUp:
                name = _VK_TO_NAME.get(keycode)
                if name:
                    self._on_release(name)

            elif event_type == cg.kCGEventFlagsChanged:
                name = _VK_TO_NAME.get(keycode)
                if name and name in _MOD_VK:
                    _vk, mask = _MOD_VK[name]
                    was_down = bool(self._mod_flags_prev & mask)
                    is_down = bool(flags & mask)
                    self._mod_flags_prev = flags
                    if is_down and not was_down:
                        self._on_press(name)
                    elif was_down and not is_down:
                        self._on_release(name)
                elif keycode == _FN_KEYCODE:
                    was_down = bool(self._mod_flags_prev & _FN_FLAG)
                    is_down = bool(flags & _FN_FLAG)
                    self._mod_flags_prev = flags
                    if is_down and not was_down:
                        self._on_press("fn")
                    elif was_down and not is_down:
                        self._on_release("fn")
                else:
                    # Unknown modifier key; just update tracked flags
                    self._mod_flags_prev = flags

        except Exception:
            logger.warning("_QuartzAllKeysListener callback exception", exc_info=True)

        # Listen-only: return None (ignored by the system).
        # Active: return the original event to pass it through.
        return None if self._listen_only else event

    def start(self) -> None:
        from wenzi import _cgeventtap as cg

        mask = (
            cg.CGEventMaskBit(cg.kCGEventKeyDown)
            | cg.CGEventMaskBit(cg.kCGEventKeyUp)
            | cg.CGEventMaskBit(cg.kCGEventFlagsChanged)
        )
        option = cg.kCGEventTapOptionListenOnly if self._listen_only else cg.kCGEventTapOptionDefault
        self._runner = cg.CGEventTapRunner()
        self._runner.start(mask, self._callback, option=option)

    def stop(self) -> None:
        if self._runner is not None:
            self._runner.stop()
            self._runner = None
        logger.info("Quartz all-keys listener stopped")


# ---------------------------------------------------------------------------
# TapHotkeyListener — intercept and swallow a hotkey combination
# ---------------------------------------------------------------------------

class TapHotkeyListener:
    """Listen for a hotkey combination (single tap, not hold).

    Uses Quartz CGEventTap to intercept key combinations like "ctrl+cmd+v"
    and swallow the event so it does not reach the active application.
    """

    def __init__(self, hotkey_str: str, on_activate: Callable[[], None]) -> None:
        self._hotkey_str = hotkey_str
        self._on_activate = on_activate
        self._mod_flags, self._keycode = _parse_hotkey_for_quartz(hotkey_str)
        self._runner = None  # CGEventTapRunner | None

    def _callback(self, proxy, event_type, event, refcon):
        from wenzi import _cgeventtap as cg

        try:
            if event_type == cg.kCGEventTapDisabledByTimeout:
                logger.warning("CGEventTap disabled by timeout, re-enabling")
                if self._runner is not None and self._runner.tap is not None:
                    cg.CGEventTapEnable(self._runner.tap, True)
                return event

            if event_type != cg.kCGEventKeyDown:
                return event

            keycode = cg.CGEventGetIntegerValueField(
                event, cg.kCGKeyboardEventKeycode
            )
            flags = cg.CGEventGetFlags(event) & _MOD_MASK

            if keycode == self._keycode and flags == self._mod_flags:
                logger.debug("TapHotkeyListener matched: %s", self._hotkey_str)
                _submit_callback(self._on_activate)
                return None  # Swallow the event

            return event
        except Exception:
            logger.warning("[TapHotkey] _callback exception", exc_info=True)
            return event

    def start(self) -> None:
        from wenzi import _cgeventtap as cg

        mask = cg.CGEventMaskBit(cg.kCGEventKeyDown)
        self._runner = cg.CGEventTapRunner()
        self._runner.start(mask, self._callback)

    def stop(self) -> None:
        if self._runner is not None:
            self._runner.stop()
            self._runner = None
        logger.info("TapHotkeyListener stopped")


# ---------------------------------------------------------------------------
# SharedHotkeyTap — single CGEventTap for many hotkey combinations
# ---------------------------------------------------------------------------

class SharedHotkeyTap:
    """Single CGEventTap that multiplexes multiple hotkey combinations.

    Replaces per-hotkey TapHotkeyListener instances with one shared tap,
    reducing the number of CGEventTaps from N to 1.
    """

    def __init__(self) -> None:
        self._bindings: dict[tuple[int, int], dict[int, Callable[[], None]]] = {}
        self._tokens: dict[int, tuple[int, int]] = {}
        self._next_token = 0
        self._runner = None  # CGEventTapRunner | None
        self._cg = None  # cached _cgeventtap module
        self._lock = threading.Lock()

    def add(self, hotkey_str: str, on_activate: Callable[[], None]) -> int:
        """Register a hotkey. Returns a unique token for later removal.

        Starts the underlying tap automatically on the first add().
        """
        mod_flags, keycode = _parse_hotkey_for_quartz(hotkey_str)
        key = (mod_flags, keycode)
        with self._lock:
            token = self._next_token
            self._next_token += 1
            callbacks = self._bindings.setdefault(key, {})
            callbacks[token] = on_activate
            self._tokens[token] = key
            if self._runner is None:
                self._start_tap()
        logger.debug("SharedHotkeyTap: added %s (flags=0x%x kc=%d, token=%d)",
                     hotkey_str, mod_flags, keycode, token)
        return token

    def remove(self, token: int) -> None:
        """Unregister a hotkey by its registration token.

        When the last binding is removed the tap is auto-stopped.
        """
        with self._lock:
            key = self._tokens.pop(token, None)
            if key is None:
                return

            callbacks = self._bindings.get(key)
            if callbacks is None:
                return

            callbacks.pop(token, None)
            if not callbacks:
                self._bindings.pop(key, None)
            if not self._bindings:
                self._stop_tap_locked()

    def stop(self) -> None:
        """Stop the tap and remove all bindings."""
        with self._lock:
            self._bindings.clear()
            self._tokens.clear()
            self._stop_tap_locked()
        logger.info("SharedHotkeyTap stopped")

    def _stop_tap_locked(self) -> None:
        """Stop the underlying tap. Caller must hold *_lock*."""
        if self._runner is not None:
            self._runner.stop()
            self._runner = None

    def _start_tap(self) -> None:
        """Create and start the CGEventTap.  Caller must hold *_lock*."""
        from wenzi import _cgeventtap as cg

        self._cg = cg
        mask = cg.CGEventMaskBit(cg.kCGEventKeyDown)
        self._runner = cg.CGEventTapRunner()
        self._runner.start(mask, self._callback)
        logger.info("SharedHotkeyTap: CGEventTap created (bindings=%d)", len(self._bindings))

    def _callback(self, proxy, event_type, event, refcon):
        cg = self._cg

        try:
            if event_type == cg.kCGEventTapDisabledByTimeout:
                logger.warning("SharedHotkeyTap disabled by timeout, re-enabling")
                if self._runner is not None and self._runner.tap is not None:
                    cg.CGEventTapEnable(self._runner.tap, True)
                return event

            if event_type != cg.kCGEventKeyDown:
                return event

            keycode = cg.CGEventGetIntegerValueField(
                event, cg.kCGKeyboardEventKeycode
            )
            flags = cg.CGEventGetFlags(event) & _MOD_MASK
            key = (flags, keycode)

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("SharedHotkeyTap: keydown flags=0x%x kc=%d (%s)",
                             flags, keycode, _VK_TO_NAME.get(keycode, "?"))

            d = self._bindings.get(key)
            if d is None:
                return event

            with self._lock:
                callbacks = tuple(d.values())

            if callbacks:
                logger.info("SharedHotkeyTap matched: %s",
                            _format_hotkey(flags, keycode))
                for callback in callbacks:
                    _submit_callback(callback)
                return None  # Swallow the event

            return event
        except Exception:
            logger.warning("[SharedHotkeyTap] callback exception", exc_info=True)
            return event


# Ordered list for _format_hotkey — iterate instead of hard-coding each flag.
_MOD_DISPLAY_ORDER = ("cmd", "ctrl", "alt", "shift")


def _format_hotkey(flags: int, keycode: int) -> str:
    """Format modifier flags + keycode into a human-readable string."""
    parts = [name for name in _MOD_DISPLAY_ORDER if flags & _MOD_FLAGS[name]]
    parts.append(_VK_TO_NAME.get(keycode, f"vk{keycode}"))
    return "+".join(parts)


# ---------------------------------------------------------------------------
# KeyRemapListener — remap one key to another via CGEventTap
# ---------------------------------------------------------------------------

class KeyRemapListener:
    """Remap keys by intercepting events and synthesizing replacements.

    Supports modifier-to-key (e.g. Right Shift → F19) and key-to-key
    (e.g. Caps Lock → Escape) remappings.  A single CGEventTap handles
    all registered remaps.
    """

    def __init__(self) -> None:
        self._remaps: dict[int, tuple] = {}  # source_vk → (target_vk, is_modifier, mod_flag)
        self._runner = None  # CGEventTapRunner | None
        self._prev_flags: int = 0

    def add(self, source_vk: int, target_vk: int, is_modifier: bool, mod_flag: int) -> None:
        """Add a remap.  Can be called while running."""
        self._remaps[source_vk] = (target_vk, is_modifier, mod_flag)

    def remove(self, source_vk: int) -> None:
        """Remove a remap."""
        self._remaps.pop(source_vk, None)

    def is_running(self) -> bool:
        """Return True if the event tap is active."""
        return self._runner is not None and self._runner.running

    def _callback(self, proxy, event_type, event, refcon):
        from wenzi import _cgeventtap as cg

        try:
            if event_type == cg.kCGEventTapDisabledByTimeout:
                logger.warning("KeyRemapListener tap disabled by timeout, re-enabling")
                if self._runner is not None and self._runner.tap is not None:
                    cg.CGEventTapEnable(self._runner.tap, True)
                return event

            if event_type == cg.kCGEventFlagsChanged:
                keycode = cg.CGEventGetIntegerValueField(
                    event, cg.kCGKeyboardEventKeycode
                )
                remap = self._remaps.get(keycode)
                if remap and remap[1]:  # is_modifier source
                    target_vk, _, mod_flag = remap
                    flags = cg.CGEventGetFlags(event)
                    was_down = bool(self._prev_flags & mod_flag)
                    is_down = bool(flags & mod_flag)
                    self._prev_flags = flags
                    if is_down != was_down:
                        evt = cg.CGEventCreateKeyboardEvent(None, target_vk, is_down)
                        cg.CGEventPost(cg.kCGAnnotatedSessionEventTap, evt)
                        cg.CFRelease(evt)
                        return None  # Swallow the original modifier event
                return event

            if event_type in (cg.kCGEventKeyDown, cg.kCGEventKeyUp):
                keycode = cg.CGEventGetIntegerValueField(
                    event, cg.kCGKeyboardEventKeycode
                )
                remap = self._remaps.get(keycode)
                if remap and not remap[1]:  # non-modifier source
                    target_vk = remap[0]
                    is_down = event_type == cg.kCGEventKeyDown
                    evt_flags = cg.CGEventGetFlags(event)
                    evt = cg.CGEventCreateKeyboardEvent(None, target_vk, is_down)
                    cg.CGEventSetFlags(evt, evt_flags)
                    cg.CGEventPost(cg.kCGAnnotatedSessionEventTap, evt)
                    cg.CFRelease(evt)
                    return None  # Swallow the original key event
                return event

            return event
        except Exception:
            logger.warning("[KeyRemap] _callback exception", exc_info=True)
            return event

    def start(self) -> None:
        from wenzi import _cgeventtap as cg

        mask = (
            cg.CGEventMaskBit(cg.kCGEventFlagsChanged)
            | cg.CGEventMaskBit(cg.kCGEventKeyDown)
            | cg.CGEventMaskBit(cg.kCGEventKeyUp)
        )
        self._runner = cg.CGEventTapRunner()
        self._runner.start(mask, self._callback)

    def stop(self) -> None:
        if self._runner is not None:
            self._runner.stop()
            self._runner = None
        logger.info("KeyRemapListener stopped")


# ---------------------------------------------------------------------------
# HoldHotkeyListener — single key hold detection
# ---------------------------------------------------------------------------

class HoldHotkeyListener:
    """Listen for a hotkey: call on_press when pressed, on_release when released.

    Uses a Quartz CGEventTap with thread-safe C APIs only.
    """

    def __init__(
        self,
        key_name: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ) -> None:
        self._target_vk = _name_to_vk(key_name)
        self._on_press = on_press
        self._on_release = on_release
        self._held = False
        self._held_lock = threading.Lock()
        self._listener: _QuartzAllKeysListener | None = None

    def start(self) -> None:
        self._listener = _QuartzAllKeysListener(
            on_press=self._handle_press,
            on_release=self._handle_release,
        )
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None

    def _handle_press(self, name: str) -> None:
        vk = _name_to_vk(name) if name in _ALL_KEY_NAMES else -1
        with self._held_lock:
            if vk == self._target_vk and not self._held:
                self._held = True
            else:
                return
        try:
            self._on_press()
        except Exception as e:
            logger.error("on_press callback error: %s", e)

    def _handle_release(self, name: str) -> None:
        vk = _name_to_vk(name) if name in _ALL_KEY_NAMES else -1
        with self._held_lock:
            if vk == self._target_vk and self._held:
                self._held = False
            else:
                return
        try:
            self._on_release()
        except Exception as e:
            logger.error("on_release callback error: %s", e)


# ---------------------------------------------------------------------------
# MultiHotkeyListener — multiple keys + recording mode
# ---------------------------------------------------------------------------

class MultiHotkeyListener:
    """Listen for multiple hotkeys using a single Quartz CGEventTap.

    Uses only thread-safe Quartz C APIs (no pynput, no NSEvent).
    """

    def __init__(
        self,
        key_names: list[str],
        on_press: Callable[[str], None],
        on_release: Callable[[str], None],
        on_restart: Callable[[], None] | None = None,
        restart_key: str = "cmd",
        on_cancel: Callable[[], None] | None = None,
        cancel_key: str = "space",
        on_preview_history: Callable[[], None] | None = None,
        preview_history_key: str = "z",
        on_mode_prev: Callable[[], None] | None = None,
        on_mode_next: Callable[[], None] | None = None,
    ) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._on_restart = on_restart
        self._restart_key = restart_key.strip().lower()
        self._on_cancel = on_cancel
        self._cancel_key = cancel_key.strip().lower()
        self._cancel_requested = False
        self._on_preview_history = on_preview_history
        self._preview_history_key = preview_history_key.strip().lower()
        self._on_mode_prev = on_mode_prev
        self._on_mode_next = on_mode_next
        self._target_vks: dict[int, str] = {}  # vk -> name
        self._enabled_names: set = set()
        self._held: set = set()  # set of currently held key names
        self._held_lock = threading.Lock()
        self._listener: _QuartzAllKeysListener | None = None
        # Recording mode state
        self._record_done = threading.Event()
        self._record_cb: Callable[[str], None] | None = None
        self._record_unrecognized_cb: Callable[[str], None] | None = None
        self._record_timeout_cb: Callable[[], None] | None = None
        self._record_timer: TimerHandle | None = None

        self._has_non_modifier_trigger = False
        for name in key_names:
            n = _normalize_key_name(name)
            vk = _name_to_vk(n)
            self._target_vks[vk] = n
            self._enabled_names.add(n)
            if not _is_modifier_like_vk(vk):
                self._has_non_modifier_trigger = True

    def start(self) -> None:
        # Use active tap when callbacks need to swallow keys, or when
        # a trigger key is a non-modifier (e.g. numpad) that must be
        # intercepted to prevent it from reaching the focused app.
        listen_only = (
            not self._has_non_modifier_trigger
            and self._on_restart is None
            and self._on_cancel is None
            and self._on_preview_history is None
            and self._on_mode_prev is None
            and self._on_mode_next is None
        )
        self._listener = _QuartzAllKeysListener(
            on_press=self._handle_press,
            on_release=self._handle_release,
            listen_only=listen_only,
        )
        self._listener.start()
        logger.info(
            "Multi-hotkey listener started, keys=%s, listen_only=%s",
            list(self._enabled_names), listen_only,
        )

    def stop(self) -> None:
        self.cancel_record()
        if self._listener:
            self._listener.stop()
            self._listener = None
            logger.info("Multi-hotkey listener stopped")
        with self._held_lock:
            self._held.clear()

    # ------------------------------------------------------------------
    # Recording mode — capture the next key press (any key)
    # ------------------------------------------------------------------

    def record_next_key(
        self,
        on_recorded: Callable[[str], None],
        on_timeout: Callable[[], None],
        timeout: float = 10.0,
        on_unrecognized: Callable[[str], None] | None = None,
    ) -> None:
        """Enter recording mode: the next key press calls *on_recorded* instead of on_press."""
        self._record_done.clear()
        self._record_cb = on_recorded
        self._record_unrecognized_cb = on_unrecognized
        self._record_timeout_cb = on_timeout
        self._record_timer = call_later(timeout, self._on_record_timeout)
        logger.info("Recording mode started (timeout=%.1fs)", timeout)

    def cancel_record(self) -> None:
        """Cancel recording mode if active."""
        self._record_done.set()
        self._record_cb = None
        self._record_unrecognized_cb = None
        self._record_timeout_cb = None
        if self._record_timer:
            self._record_timer.cancel()
            self._record_timer = None

    def _on_record_timeout(self) -> None:
        if self._record_done.is_set():
            return
        self._record_done.set()
        cb = self._record_timeout_cb
        self._record_cb = None
        self._record_unrecognized_cb = None
        self._record_timeout_cb = None
        self._record_timer = None
        if cb:
            cb()
        logger.info("Recording mode timed out")

    def _try_record(self, key_name: str) -> bool:
        """If in recording mode, deliver the key and return True."""
        if self._record_done.is_set():
            return False
        self._record_done.set()
        cb = self._record_cb
        self._record_cb = None
        self._record_unrecognized_cb = None
        self._record_timeout_cb = None
        if self._record_timer:
            self._record_timer.cancel()
            self._record_timer = None
        if cb is None:
            return False
        logger.info("Recorded key: %s", key_name)
        cb(key_name)
        return True

    # ------------------------------------------------------------------

    def enable_key(self, key_name: str) -> None:
        """Enable a key dynamically."""
        n = _normalize_key_name(key_name)
        vk = _name_to_vk(n)
        self._target_vks[vk] = n
        self._enabled_names.add(n)
        logger.info("Hotkey %s enabled", n)

    def disable_key(self, key_name: str) -> None:
        """Disable a key dynamically."""
        n = _normalize_key_name(key_name)
        vk = _name_to_vk(n)
        self._target_vks.pop(vk, None)
        self._enabled_names.discard(n)
        with self._held_lock:
            self._held.discard(n)
        logger.info("Hotkey %s disabled", n)

    def set_restart_key(self, key_name: str) -> None:
        """Change the restart key at runtime."""
        n = _normalize_key_name(key_name)
        self._restart_key = n
        logger.info("Restart key set to: %s", n)

    def set_cancel_key(self, key_name: str) -> None:
        """Change the cancel key at runtime."""
        n = _normalize_key_name(key_name)
        self._cancel_key = n
        logger.info("Cancel key set to: %s", n)

    def _handle_press(self, name: str) -> bool:
        """Handle key press. Returns True if the event should be swallowed."""
        try:
            # Recording mode: capture any recognized key
            if self._record_cb is not None:
                if name in _ALL_KEY_NAMES:
                    self._try_record(name)
                elif self._record_unrecognized_cb is not None:
                    vk = -1
                    try:
                        vk = _name_to_vk(name)
                    except ValueError:
                        pass
                    debug = f"keyName={name!r} (vk={vk})"
                    logger.warning("Unrecognized key during recording: %s", debug)
                    try:
                        self._record_unrecognized_cb(debug)
                    except Exception as e:
                        logger.error("on_unrecognized callback error: %s", e)
                return False

            # Normal mode: check if this is a monitored key
            with self._held_lock:
                if name in self._enabled_names and name not in self._held:
                    self._held.add(name)
                    action = "press"
                elif name in self._held:
                    # Key repeat of already-held trigger — swallow to
                    # prevent characters leaking into the focused app.
                    return True
                elif self._on_restart and self._held and name == self._restart_key:
                    action = "restart"
                elif self._on_cancel and self._held and name == self._cancel_key:
                    action = "cancel"
                elif (
                    self._on_preview_history
                    and self._held
                    and name == self._preview_history_key
                ):
                    action = "preview_history"
                elif (
                    self._on_mode_prev
                    and self._held
                    and name in ("left", "up", "home", "pageup")
                ):
                    action = "mode_prev"
                elif (
                    self._on_mode_next
                    and self._held
                    and name in ("right", "down", "end", "pagedown")
                ):
                    action = "mode_next"
                else:
                    return False

            if action == "press":
                # Dispatch off the CGEventTap thread to avoid a tap
                # timeout (macOS disables the tap if the callback blocks
                # for too long).
                _submit_callback(self._on_press, name)
                # Swallow non-modifier trigger keys to prevent input
                # reaching the focused app (e.g. numpad keys).
                if self._has_non_modifier_trigger:
                    vk = _name_to_vk(name)
                    if not _is_modifier_like_vk(vk):
                        return True
            elif action == "restart":
                _submit_callback(self._on_restart)
                return True  # swallow the restart key event
            elif action == "cancel":
                self._cancel_requested = True
                _submit_callback(self._on_cancel)
                return True  # swallow the cancel key event
            elif action == "preview_history":
                self._cancel_requested = True
                _submit_callback(self._on_preview_history)
                return True  # swallow the key event
            elif action == "mode_prev":
                _submit_callback(self._on_mode_prev)
                return True  # swallow the arrow key event
            elif action == "mode_next":
                _submit_callback(self._on_mode_next)
                return True  # swallow the arrow key event
        except Exception:
            logger.warning("_handle_press exception", exc_info=True)
        return False

    def _handle_release(self, name: str) -> None:
        try:
            with self._held_lock:
                if name in self._held:
                    self._held.discard(name)
                else:
                    return
                cancel = self._cancel_requested
                self._cancel_requested = False
            if cancel:
                return
            try:
                self._on_release(name)
            except Exception as e:
                logger.error("on_release callback error: %s", e)
        except Exception:
            logger.warning("_handle_release exception", exc_info=True)
