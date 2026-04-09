"""Low-level ctypes bindings for CGEventTap — no PyObjC bridge.

Using ctypes instead of PyObjC for CGEventTapCreate avoids the PyObjC
callback bridge retaining CGEventRef wrappers indefinitely.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging as _logging
import threading as _threading
from ctypes import CFUNCTYPE, c_bool, c_int32, c_int64, c_uint32, c_uint64, c_void_p

# ---------------------------------------------------------------------------
# Load frameworks
# ---------------------------------------------------------------------------
_cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
_cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))

# ---------------------------------------------------------------------------
# Callback type
# ---------------------------------------------------------------------------
CGEventTapCallBack = CFUNCTYPE(c_void_p, c_void_p, c_uint32, c_void_p, c_void_p)

# ---------------------------------------------------------------------------
# Constants (hardcoded — no Quartz import)
# ---------------------------------------------------------------------------
kCGSessionEventTap = 1
kCGHeadInsertEventTap = 0

kCGEventTapOptionDefault = 0
kCGEventTapOptionListenOnly = 1

kCGEventKeyDown = 10
kCGEventKeyUp = 11
kCGEventFlagsChanged = 12

kCGEventTapDisabledByTimeout = 0xFFFFFFFE

kCGKeyboardEventKeycode = 9

kCGAnnotatedSessionEventTap = 2

kCGEventSourceStateCombinedSessionState = 0

kCGEventFlagMaskCommand = 1 << 20
kCGEventFlagMaskControl = 1 << 18
kCGEventFlagMaskAlternate = 1 << 19
kCGEventFlagMaskShift = 1 << 17

kCFRunLoopDefaultMode = c_void_p.in_dll(_cf, "kCFRunLoopDefaultMode")

# ---------------------------------------------------------------------------
# Function signatures
# ---------------------------------------------------------------------------

# CGEventTapCreate(tap, place, options, eventsOfInterest, callback, userInfo)
_cg.CGEventTapCreate.restype = c_void_p
_cg.CGEventTapCreate.argtypes = [
    c_uint32,           # CGEventTapLocation
    c_uint32,           # CGEventTapPlacement
    c_uint32,           # CGEventTapOptions
    c_uint64,           # CGEventMask
    CGEventTapCallBack, # callback
    c_void_p,           # userInfo
]

# CGEventTapEnable(tap, enable)
_cg.CGEventTapEnable.restype = None
_cg.CGEventTapEnable.argtypes = [c_void_p, c_bool]

# CGEventGetIntegerValueField(event, field) -> int64
_cg.CGEventGetIntegerValueField.restype = c_int64
_cg.CGEventGetIntegerValueField.argtypes = [c_void_p, c_uint32]

# CGEventGetFlags(event) -> uint64
_cg.CGEventGetFlags.restype = c_uint64
_cg.CGEventGetFlags.argtypes = [c_void_p]

# CGEventSetFlags(event, flags)
_cg.CGEventSetFlags.restype = None
_cg.CGEventSetFlags.argtypes = [c_void_p, c_uint64]

# CGEventSourceFlagsState(stateID) -> uint64
_cg.CGEventSourceFlagsState.restype = c_uint64
_cg.CGEventSourceFlagsState.argtypes = [c_int32]

# CGEventCreateKeyboardEvent(source, virtualKey, keyDown) -> CGEventRef
_cg.CGEventCreateKeyboardEvent.restype = c_void_p
_cg.CGEventCreateKeyboardEvent.argtypes = [c_void_p, c_uint32, c_bool]

# CGEventPost(tap, event)
_cg.CGEventPost.restype = None
_cg.CGEventPost.argtypes = [c_uint32, c_void_p]

# CFMachPortCreateRunLoopSource(allocator, port, order) -> CFRunLoopSourceRef
_cf.CFMachPortCreateRunLoopSource.restype = c_void_p
_cf.CFMachPortCreateRunLoopSource.argtypes = [c_void_p, c_void_p, c_int64]

# CFRunLoopGetCurrent() -> CFRunLoopRef
_cf.CFRunLoopGetCurrent.restype = c_void_p
_cf.CFRunLoopGetCurrent.argtypes = []

# CFRunLoopAddSource(rl, source, mode)
_cf.CFRunLoopAddSource.restype = None
_cf.CFRunLoopAddSource.argtypes = [c_void_p, c_void_p, c_void_p]

# CFRunLoopRun()
_cf.CFRunLoopRun.restype = None
_cf.CFRunLoopRun.argtypes = []

# CFRunLoopStop(rl)
_cf.CFRunLoopStop.restype = None
_cf.CFRunLoopStop.argtypes = [c_void_p]

# CFRelease(cf)
_cf.CFRelease.restype = None
_cf.CFRelease.argtypes = [c_void_p]

# ---------------------------------------------------------------------------
# Module-level Python functions
# ---------------------------------------------------------------------------


def CGEventTapCreate(tap, place, options, events_of_interest, callback, user_info):
    return _cg.CGEventTapCreate(tap, place, options, events_of_interest, callback, user_info)


def CGEventTapEnable(tap, enable):
    _cg.CGEventTapEnable(tap, enable)


def CGEventGetIntegerValueField(event, field):
    return _cg.CGEventGetIntegerValueField(event, field)


def CGEventGetFlags(event):
    return _cg.CGEventGetFlags(event)


def CGEventSetFlags(event, flags):
    _cg.CGEventSetFlags(event, flags)


def CGEventSourceFlagsState(state_id):
    return _cg.CGEventSourceFlagsState(state_id)


def CGEventCreateKeyboardEvent(source, virtual_key, key_down):
    return _cg.CGEventCreateKeyboardEvent(source, virtual_key, key_down)


def CGEventPost(tap, event):
    _cg.CGEventPost(tap, event)


def CFMachPortCreateRunLoopSource(allocator, port, order):
    return _cf.CFMachPortCreateRunLoopSource(allocator, port, order)


def CFRunLoopGetCurrent():
    return _cf.CFRunLoopGetCurrent()


def CFRunLoopAddSource(rl, source, mode):
    _cf.CFRunLoopAddSource(rl, source, mode)


def CFRunLoopRun():
    _cf.CFRunLoopRun()


def CFRunLoopStop(rl):
    _cf.CFRunLoopStop(rl)


def CFRelease(cf):
    _cf.CFRelease(cf)


def CGEventMaskBit(event_type):
    """Pure Python implementation of CGEventMaskBit."""
    return 1 << event_type


# ---------------------------------------------------------------------------
# CGEventTapRunner — shared lifecycle helper
# ---------------------------------------------------------------------------

_runner_logger = _logging.getLogger(__name__)


class CGEventTapRunner:
    """Manages a CGEventTap on a background thread with proper cleanup.

    Handles the boilerplate: create tap, create run-loop source, run the
    CFRunLoop on a daemon thread, and tear everything down with CFRelease
    on stop().  Consumers only supply a callback and event mask.
    """

    def __init__(self) -> None:
        self.tap = None
        self._source = None
        self._loop = None
        self._thread: _threading.Thread | None = None
        self._ctypes_cb = None
        self._ready = _threading.Event()

    @property
    def running(self) -> bool:
        return self.tap is not None

    def start(
        self,
        mask: int,
        callback,
        *,
        option: int = kCGEventTapOptionDefault,
        on_create_failed=None,
    ) -> None:
        """Start the tap on a background thread.

        *callback* receives ``(proxy, event_type, event, refcon)`` and must
        return the event (pass-through) or ``None`` (swallow / listen-only).

        *on_create_failed* is called (on the bg thread) if
        ``CGEventTapCreate`` returns NULL.
        """
        def _raw_cb(proxy, event_type, event, refcon):
            return callback(proxy, event_type, event, refcon) or 0
        self._ctypes_cb = CGEventTapCallBack(_raw_cb)

        def _run():
            tap = CGEventTapCreate(
                kCGSessionEventTap, kCGHeadInsertEventTap,
                option, mask, self._ctypes_cb, None,
            )
            if not tap:
                _runner_logger.warning(
                    "CGEventTapCreate failed — check Accessibility permissions "
                    "in System Settings > Privacy & Security > Accessibility"
                )
                self._ready.set()
                if on_create_failed is not None:
                    on_create_failed()
                return
            source = CFMachPortCreateRunLoopSource(None, tap, 0)
            self.tap = tap
            self._source = source
            self._loop = CFRunLoopGetCurrent()
            CFRunLoopAddSource(self._loop, source, kCFRunLoopDefaultMode.value)
            CGEventTapEnable(tap, True)
            _runner_logger.debug("CGEventTap started")
            self._ready.set()
            CFRunLoopRun()

        self._ready.clear()
        self._thread = _threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def wait_ready(self, timeout: float = 2.0) -> None:
        """Block until the background thread has created the tap (or failed)."""
        self._ready.wait(timeout)

    def stop(self) -> None:
        """Disable the tap, stop the run loop, release CF objects."""
        if self.tap is None and self._thread is None:
            return
        try:
            if self.tap is not None:
                CGEventTapEnable(self.tap, False)
            if self._loop is not None:
                CFRunLoopStop(self._loop)
        except Exception:
            _runner_logger.debug("CGEventTapRunner: error during disable", exc_info=True)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._loop = None
        if self._source is not None:
            try:
                CFRelease(self._source)
            except Exception:
                _runner_logger.debug("CFRelease(source) failed", exc_info=True)
            self._source = None
        if self.tap is not None:
            try:
                CFRelease(self.tap)
            except Exception:
                _runner_logger.debug("CFRelease(tap) failed", exc_info=True)
            self.tap = None
        self._ctypes_cb = None
