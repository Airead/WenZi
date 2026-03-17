"""Global hotkey listener for press-and-hold interaction.

All key listening uses Quartz CGEventTap with thread-safe C APIs only.
NSEvent.eventWithCGEvent_ is NOT used anywhere — it creates AppKit objects
on background threads and races with the main thread's event dispatch,
causing intermittent crashes on Caps Lock / input method switching.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, List, Optional


logger = logging.getLogger(__name__)

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
    "fn": 63, "esc": 53, "space": 49,
    "return": 36, "delete": 51, "tab": 48,
    "up": 126, "down": 125, "left": 123, "right": 124,
}

# Modifier key virtual keycodes and their CGEventFlags bitmask
_MOD_VK = {
    "cmd": (55, 0x100000), "cmd_r": (54, 0x100000),
    "ctrl": (59, 0x040000), "ctrl_r": (62, 0x040000),
    "alt": (58, 0x080000), "alt_r": (61, 0x080000),
    "shift": (56, 0x020000), "shift_r": (60, 0x020000),
}

# Reverse lookup: virtual keycode -> key name
_VK_TO_NAME: Dict[int, str] = {}
_VK_TO_NAME.update({vk: name for name, vk in _KEYCODE_MAP.items()})
_VK_TO_NAME.update({vk: name for name, vk in _SPECIAL_VK.items()})
_VK_TO_NAME.update({vk: name for name, (vk, _flag) in _MOD_VK.items()})

# All known key names (for validation)
_ALL_KEY_NAMES = set(_KEYCODE_MAP) | set(_SPECIAL_VK) | set(_MOD_VK) | {"option", "command"}

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


def _name_to_vk(name: str) -> int:
    """Convert a key name to its macOS virtual keycode."""
    name = name.strip().lower()
    if name == "option":
        name = "alt"
    elif name == "command":
        name = "cmd"
    if name in _KEYCODE_MAP:
        return _KEYCODE_MAP[name]
    if name in _SPECIAL_VK:
        return _SPECIAL_VK[name]
    if name in _MOD_VK:
        return _MOD_VK[name][0]
    raise ValueError(f"Unknown key: {name}")


def _is_fn_key(name: str) -> bool:
    return name.strip().lower() == "fn"


def _is_modifier_vk(vk: int) -> bool:
    """Check if a virtual keycode is a modifier key."""
    return any(vk == mod_vk for mod_vk, _flag in _MOD_VK.values())


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
        if part == "option":
            part = "alt"
        elif part == "command":
            part = "cmd"
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

def _pre_resolve_quartz():
    """Eagerly resolve Quartz symbols on the main thread."""
    import Quartz
    _ = Quartz.CGEventMaskBit
    _ = Quartz.kCGEventKeyDown
    _ = Quartz.kCGEventKeyUp
    _ = Quartz.kCGEventFlagsChanged
    _ = Quartz.CGEventTapCreate
    _ = Quartz.kCGSessionEventTap
    _ = Quartz.kCGHeadInsertEventTap
    _ = Quartz.kCGEventTapOptionListenOnly
    _ = Quartz.kCGEventTapOptionDefault
    _ = Quartz.CFMachPortCreateRunLoopSource
    _ = Quartz.CFRunLoopGetCurrent
    _ = Quartz.CFRunLoopAddSource
    _ = Quartz.kCFRunLoopDefaultMode
    _ = Quartz.CGEventTapEnable
    _ = Quartz.CFRunLoopRun
    _ = Quartz.CFRunLoopStop
    _ = Quartz.CGEventGetIntegerValueField
    _ = Quartz.CGEventGetFlags
    _ = Quartz.kCGKeyboardEventKeycode


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
        self._tap = None
        self._loop = None
        self._thread: Optional[threading.Thread] = None
        # Track modifier key states to detect press vs release.
        # Snapshot current flags so a modifier held across listener
        # restart is not misinterpreted as a new press.
        try:
            import Quartz as _Q
            self._mod_flags_prev = _Q.CGEventSourceFlagsState(
                _Q.kCGEventSourceStateCombinedSessionState
            )
        except Exception:
            self._mod_flags_prev = 0

    def _callback(self, proxy, event_type, event, refcon):
        try:
            import Quartz

            if event_type == Quartz.kCGEventTapDisabledByTimeout:
                logger.warning("CGEventTap disabled by timeout, re-enabling")
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event

            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )

            if event_type == Quartz.kCGEventKeyDown:
                name = _VK_TO_NAME.get(keycode)
                if name:
                    swallow = self._on_press(name)
                    if swallow and not self._listen_only:
                        return None

            elif event_type == Quartz.kCGEventKeyUp:
                name = _VK_TO_NAME.get(keycode)
                if name:
                    self._on_release(name)

            elif event_type == Quartz.kCGEventFlagsChanged:
                flags = Quartz.CGEventGetFlags(event)
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

        return event

    def start(self) -> None:
        import Quartz
        _pre_resolve_quartz()

        _listen_only = self._listen_only
        _CGEventMaskBit = Quartz.CGEventMaskBit
        _kCGEventKeyDown = Quartz.kCGEventKeyDown
        _kCGEventKeyUp = Quartz.kCGEventKeyUp
        _kCGEventFlagsChanged = Quartz.kCGEventFlagsChanged
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
            mask = (
                _CGEventMaskBit(_kCGEventKeyDown)
                | _CGEventMaskBit(_kCGEventKeyUp)
                | _CGEventMaskBit(_kCGEventFlagsChanged)
            )
            self._tap = _CGEventTapCreate(
                _kCGSessionEventTap,
                _kCGHeadInsertEventTap,
                _kCGEventTapOptionListenOnly if _listen_only else Quartz.kCGEventTapOptionDefault,
                mask,
                self._callback,
                None,
            )
            if self._tap is None:
                logger.error(
                    "Failed to create Quartz event tap. "
                    "Check accessibility permissions in System Settings."
                )
                return

            source = _CFMachPortCreateRunLoopSource(None, self._tap, 0)
            self._loop = _CFRunLoopGetCurrent()
            _CFRunLoopAddSource(self._loop, source, _kCFRunLoopDefaultMode)
            _CGEventTapEnable(self._tap, True)
            logger.info("Quartz all-keys listener started (listen_only=%s)", _listen_only)
            _CFRunLoopRun()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        import Quartz

        if self._tap is not None:
            Quartz.CGEventTapEnable(self._tap, False)
        if self._loop is not None:
            Quartz.CFRunLoopStop(self._loop)
            self._loop = None
        self._tap = None
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
        self._tap = None
        self._loop = None
        self._thread: Optional[threading.Thread] = None

    def _callback(self, proxy, event_type, event, refcon):
        try:
            import Quartz

            if event_type == Quartz.kCGEventTapDisabledByTimeout:
                logger.warning("CGEventTap disabled by timeout, re-enabling")
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event

            if event_type != Quartz.kCGEventKeyDown:
                return event

            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
            flags = Quartz.CGEventGetFlags(event) & _MOD_MASK

            if keycode == self._keycode and flags == self._mod_flags:
                logger.debug("TapHotkeyListener matched: %s", self._hotkey_str)
                try:
                    self._on_activate()
                except Exception as e:
                    logger.error("on_activate callback error: %s", e)
                return None  # Swallow the event

            return event
        except Exception:
            logger.warning("[TapHotkey] _callback exception", exc_info=True)
            return event

    def start(self) -> None:
        import Quartz
        _pre_resolve_quartz()

        _CGEventMaskBit = Quartz.CGEventMaskBit
        _kCGEventKeyDown = Quartz.kCGEventKeyDown
        _CGEventTapCreate = Quartz.CGEventTapCreate
        _kCGSessionEventTap = Quartz.kCGSessionEventTap
        _kCGHeadInsertEventTap = Quartz.kCGHeadInsertEventTap
        _kCGEventTapOptionDefault = Quartz.kCGEventTapOptionDefault
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
                _kCGEventTapOptionDefault,
                mask,
                self._callback,
                None,
            )
            if self._tap is None:
                logger.error(
                    "Failed to create Quartz event tap for hotkey. "
                    "Check accessibility permissions in System Settings."
                )
                return

            source = _CFMachPortCreateRunLoopSource(None, self._tap, 0)
            self._loop = _CFRunLoopGetCurrent()
            _CFRunLoopAddSource(self._loop, source, _kCFRunLoopDefaultMode)
            _CGEventTapEnable(self._tap, True)
            logger.info("TapHotkeyListener started: %s", self._hotkey_str)
            _CFRunLoopRun()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        import Quartz

        if self._tap is not None:
            Quartz.CGEventTapEnable(self._tap, False)
        if self._loop is not None:
            Quartz.CFRunLoopStop(self._loop)
            self._loop = None
        self._tap = None
        logger.info("TapHotkeyListener stopped")


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
        self._listener: Optional[_QuartzAllKeysListener] = None

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
        key_names: List[str],
        on_press: Callable[[str], None],
        on_release: Callable[[str], None],
        on_restart: Optional[Callable[[], None]] = None,
        restart_key: str = "cmd",
        on_cancel: Optional[Callable[[], None]] = None,
        cancel_key: str = "space",
        on_preview_history: Optional[Callable[[], None]] = None,
        preview_history_key: str = "z",
        on_mode_prev: Optional[Callable[[], None]] = None,
        on_mode_next: Optional[Callable[[], None]] = None,
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
        self._target_vks: Dict[int, str] = {}  # vk -> name
        self._enabled_names: set = set()
        self._held: set = set()  # set of currently held key names
        self._held_lock = threading.Lock()
        self._listener: Optional[_QuartzAllKeysListener] = None
        # Recording mode state
        self._record_done = threading.Event()
        self._record_cb: Optional[Callable[[str], None]] = None
        self._record_unrecognized_cb: Optional[Callable[[str], None]] = None
        self._record_timeout_cb: Optional[Callable[[], None]] = None
        self._record_timer: Optional[threading.Timer] = None

        for name in key_names:
            n = name.strip().lower()
            if n == "option":
                n = "alt"
            elif n == "command":
                n = "cmd"
            vk = _name_to_vk(n)
            self._target_vks[vk] = n
            self._enabled_names.add(n)

    def start(self) -> None:
        # Use active tap when callbacks need to swallow keys
        listen_only = (
            self._on_restart is None
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
        on_unrecognized: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Enter recording mode: the next key press calls *on_recorded* instead of on_press."""
        self._record_done.clear()
        self._record_cb = on_recorded
        self._record_unrecognized_cb = on_unrecognized
        self._record_timeout_cb = on_timeout
        self._record_timer = threading.Timer(timeout, self._on_record_timeout)
        self._record_timer.daemon = True
        self._record_timer.start()
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
        n = key_name.strip().lower()
        if n == "option":
            n = "alt"
        elif n == "command":
            n = "cmd"
        vk = _name_to_vk(n)
        self._target_vks[vk] = n
        self._enabled_names.add(n)
        logger.info("Hotkey %s enabled", n)

    def disable_key(self, key_name: str) -> None:
        """Disable a key dynamically."""
        n = key_name.strip().lower()
        if n == "option":
            n = "alt"
        elif n == "command":
            n = "cmd"
        vk = _name_to_vk(n)
        self._target_vks.pop(vk, None)
        self._enabled_names.discard(n)
        with self._held_lock:
            self._held.discard(n)
        logger.info("Hotkey %s disabled", n)

    def set_restart_key(self, key_name: str) -> None:
        """Change the restart key at runtime."""
        n = key_name.strip().lower()
        if n == "option":
            n = "alt"
        elif n == "command":
            n = "cmd"
        self._restart_key = n
        logger.info("Restart key set to: %s", n)

    def set_cancel_key(self, key_name: str) -> None:
        """Change the cancel key at runtime."""
        n = key_name.strip().lower()
        if n == "option":
            n = "alt"
        elif n == "command":
            n = "cmd"
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
                    and name in ("left", "up")
                ):
                    action = "mode_prev"
                elif (
                    self._on_mode_next
                    and self._held
                    and name in ("right", "down")
                ):
                    action = "mode_next"
                else:
                    return False

            if action == "press":
                try:
                    self._on_press(name)
                except Exception as e:
                    logger.error("on_press callback error: %s", e)
            elif action == "restart":
                try:
                    self._on_restart()
                except Exception as e:
                    logger.error("on_restart callback error: %s", e)
                return True  # swallow the restart key event
            elif action == "cancel":
                self._cancel_requested = True
                threading.Thread(
                    target=self._run_cancel, daemon=True
                ).start()
                return True  # swallow the cancel key event
            elif action == "preview_history":
                self._cancel_requested = True
                threading.Thread(
                    target=self._run_preview_history, daemon=True
                ).start()
                return True  # swallow the key event
            elif action == "mode_prev":
                try:
                    self._on_mode_prev()
                except Exception as e:
                    logger.error("on_mode_prev callback error: %s", e)
                return True  # swallow the arrow key event
            elif action == "mode_next":
                try:
                    self._on_mode_next()
                except Exception as e:
                    logger.error("on_mode_next callback error: %s", e)
                return True  # swallow the arrow key event
        except Exception:
            logger.warning("_handle_press exception", exc_info=True)
        return False

    def _run_cancel(self) -> None:
        """Run on_cancel callback in a background thread."""
        try:
            self._on_cancel()
        except Exception as e:
            logger.error("on_cancel callback error: %s", e)

    def _run_preview_history(self) -> None:
        """Run on_preview_history callback in a background thread."""
        try:
            self._on_preview_history()
        except Exception as e:
            logger.error("on_preview_history callback error: %s", e)

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
