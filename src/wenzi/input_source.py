"""macOS Text Input Source (TIS) utilities via HIToolbox.

Provides functions to query and switch the active keyboard input source.
All functions degrade gracefully — if HIToolbox is unavailable (e.g. on
Linux or in a test environment), they return None/False silently.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-loaded HIToolbox functions
# ---------------------------------------------------------------------------
_tis_loaded: Optional[bool] = None
_TISCopyCurrentKeyboardInputSource = None
_TISCreateInputSourceList = None
_TISSelectInputSource = None
_TISGetInputSourceProperty = None
_kTISPropertyInputSourceID = None


def _load_tis() -> bool:
    """Load TIS functions from HIToolbox. Returns True on success."""
    global _tis_loaded
    global _TISCopyCurrentKeyboardInputSource
    global _TISCreateInputSourceList
    global _TISSelectInputSource
    global _TISGetInputSourceProperty
    global _kTISPropertyInputSourceID

    if _tis_loaded is not None:
        return _tis_loaded

    try:
        import objc
        from Foundation import NSBundle

        bundle_path = (
            "/System/Library/Frameworks/Carbon.framework"
            "/Frameworks/HIToolbox.framework"
        )
        bundle = NSBundle.bundleWithPath_(bundle_path)
        if bundle is None:
            logger.debug("HIToolbox bundle not found")
            _tis_loaded = False
            return False

        # Load C functions — signatures are: return_type [arg_types...]
        # (NOT ObjC method signatures — no implicit self/_cmd)
        fn_list = [
            ("TISCopyCurrentKeyboardInputSource", b"@"),       # () -> TISInputSourceRef
            ("TISCreateInputSourceList", b"@@Z"),              # (CFDictRef, Boolean) -> CFArrayRef
            ("TISSelectInputSource", b"i@"),                   # (TISInputSourceRef) -> OSStatus
            ("TISGetInputSourceProperty", b"@@@"),             # (TISInputSourceRef, CFStringRef) -> CFTypeRef
        ]
        result = {}
        objc.loadBundleFunctions(bundle, result, fn_list)

        _TISCopyCurrentKeyboardInputSource = result.get(
            "TISCopyCurrentKeyboardInputSource"
        )
        _TISCreateInputSourceList = result.get("TISCreateInputSourceList")
        _TISSelectInputSource = result.get("TISSelectInputSource")
        _TISGetInputSourceProperty = result.get("TISGetInputSourceProperty")

        # Load the property key constant (CFStringRef)
        objc.loadBundleVariables(
            bundle,
            result,
            [("kTISPropertyInputSourceID", b"@")],
        )
        _kTISPropertyInputSourceID = result.get("kTISPropertyInputSourceID")

        _tis_loaded = all([
            _TISCopyCurrentKeyboardInputSource,
            _TISCreateInputSourceList,
            _TISSelectInputSource,
            _TISGetInputSourceProperty,
            _kTISPropertyInputSourceID,
        ])
        if _tis_loaded:
            logger.debug("HIToolbox TIS functions loaded successfully")
        else:
            logger.warning("Some TIS functions failed to load")
        return _tis_loaded

    except Exception:
        logger.warning("Failed to load HIToolbox", exc_info=True)
        _tis_loaded = False
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Common English keyboard layout source IDs (tried in order)
_ENGLISH_SOURCE_IDS = [
    "com.apple.keylayout.ABC",
    "com.apple.keylayout.US",
    "com.apple.keylayout.USInternational-PC",
]


def get_current_input_source() -> Optional[str]:
    """Return the current keyboard input source ID, or None on failure.

    Example return value: ``"com.apple.inputmethod.SCIM.ITABC"``
    """
    if not _load_tis():
        return None

    try:
        import objc

        with objc.autorelease_pool():
            source = _TISCopyCurrentKeyboardInputSource()
            if source is None:
                return None
            sid = _TISGetInputSourceProperty(source, _kTISPropertyInputSourceID)
            return str(sid) if sid else None
    except Exception:
        logger.warning("Failed to get current input source", exc_info=True)
        return None


def select_input_source(source_id: str) -> bool:
    """Switch to a specific input source by ID. Returns True on success."""
    if not _load_tis():
        return False

    try:
        import objc
        from Foundation import NSDictionary

        with objc.autorelease_pool():
            props = NSDictionary.dictionaryWithObject_forKey_(
                source_id, _kTISPropertyInputSourceID
            )
            source_list = _TISCreateInputSourceList(props, False)
            if not source_list or len(source_list) == 0:
                logger.debug("Input source not found: %s", source_id)
                return False

            source = source_list[0]
            status = _TISSelectInputSource(source)
            if status != 0:
                logger.warning(
                    "TISSelectInputSource returned %d for %s", status, source_id
                )
                return False
            return True
    except Exception:
        logger.warning(
            "Failed to select input source: %s", source_id, exc_info=True
        )
        return False


def is_english_input_source(source_id: Optional[str] = None) -> bool:
    """Check if the given (or current) input source is an English layout."""
    if source_id is None:
        source_id = get_current_input_source()
    if source_id is None:
        return False
    # English layouts typically start with "com.apple.keylayout."
    # and do NOT contain "inputmethod" (which indicates CJK IME)
    return (
        source_id.startswith("com.apple.keylayout.")
        and "inputmethod" not in source_id
    )


_cached_english_sid: Optional[str] = None


def select_english_input_source() -> bool:
    """Switch to an English keyboard layout.

    Tries common English layout IDs in order. Caches the first successful
    ID to avoid repeated TIS lookups on subsequent calls.
    """
    global _cached_english_sid

    # Try the cached source first
    if _cached_english_sid is not None:
        if select_input_source(_cached_english_sid):
            return True
        _cached_english_sid = None  # invalidated, fall through

    for sid in _ENGLISH_SOURCE_IDS:
        if select_input_source(sid):
            _cached_english_sid = sid
            logger.debug("Switched to English input source: %s", sid)
            return True
    logger.warning("No English input source found among: %s", _ENGLISH_SOURCE_IDS)
    return False
