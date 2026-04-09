"""Thin ctypes wrapper around macOS MDQuery (Spotlight) C API.

Provides ``mdquery_search(query, max_results)`` which uses
``MDQuerySetMaxCount`` for server-side result limiting — dramatically
faster than shelling out to ``mdfind`` which returns *all* matches.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load frameworks
# ---------------------------------------------------------------------------

_cf_path = ctypes.util.find_library("CoreFoundation")
_cs_path = ctypes.util.find_library("CoreServices")

if not _cf_path or not _cs_path:
    raise ImportError("CoreFoundation or CoreServices not found")

_cf = ctypes.cdll.LoadLibrary(_cf_path)
_cs = ctypes.cdll.LoadLibrary(_cs_path)

# ---------------------------------------------------------------------------
# CoreFoundation helpers
# ---------------------------------------------------------------------------

CFIndex = ctypes.c_long
CFTypeRef = ctypes.c_void_p
CFStringRef = ctypes.c_void_p
CFAllocatorRef = ctypes.c_void_p

kCFAllocatorDefault = None

# CFStringCreateWithCString
_cf.CFStringCreateWithCString.restype = CFStringRef
_cf.CFStringCreateWithCString.argtypes = [
    CFAllocatorRef,
    ctypes.c_char_p,
    ctypes.c_uint32,
]
kCFStringEncodingUTF8 = 0x08000100


def _cfstr(s: str) -> CFStringRef:
    """Create a CFStringRef from a Python string.  Caller must CFRelease."""
    ref = _cf.CFStringCreateWithCString(
        kCFAllocatorDefault, s.encode("utf-8"), kCFStringEncodingUTF8
    )
    if not ref:
        raise RuntimeError(f"CFStringCreateWithCString failed for {s!r}")
    return ref


# CFStringGetCString
_cf.CFStringGetCString.restype = ctypes.c_bool
_cf.CFStringGetCString.argtypes = [
    CFStringRef,
    ctypes.c_char_p,
    CFIndex,
    ctypes.c_uint32,
]

_BUF_SIZE = 2048


def _cfstr_to_py(ref: CFStringRef) -> str | None:
    """Convert a CFStringRef to a Python str, or None on failure."""
    buf = ctypes.create_string_buffer(_BUF_SIZE)
    ok = _cf.CFStringGetCString(ref, buf, _BUF_SIZE, kCFStringEncodingUTF8)
    if not ok:
        return None
    return buf.value.decode("utf-8")


# CFRelease
_cf.CFRelease.restype = None
_cf.CFRelease.argtypes = [CFTypeRef]

# ---------------------------------------------------------------------------
# MDQuery API
# ---------------------------------------------------------------------------

MDQueryRef = ctypes.c_void_p
MDItemRef = ctypes.c_void_p

# MDQueryCreate(allocator, queryString, valueListAttrs, sortingAttrs)
_cs.MDQueryCreate.restype = MDQueryRef
_cs.MDQueryCreate.argtypes = [
    CFAllocatorRef,
    CFStringRef,
    CFTypeRef,
    CFTypeRef,
]

# MDQuerySetMaxCount(query, size)
_cs.MDQuerySetMaxCount.restype = None
_cs.MDQuerySetMaxCount.argtypes = [MDQueryRef, CFIndex]

# MDQueryExecute(query, optionFlags) -> Boolean
_cs.MDQueryExecute.restype = ctypes.c_bool
_cs.MDQueryExecute.argtypes = [MDQueryRef, CFIndex]
kMDQuerySynchronous = 1

# MDQueryGetResultCount(query) -> CFIndex
_cs.MDQueryGetResultCount.restype = CFIndex
_cs.MDQueryGetResultCount.argtypes = [MDQueryRef]

# MDQueryGetResultAtIndex(query, idx) -> MDItemRef (not owned)
_cs.MDQueryGetResultAtIndex.restype = MDItemRef
_cs.MDQueryGetResultAtIndex.argtypes = [MDQueryRef, CFIndex]

# MDQueryStop(query)
_cs.MDQueryStop.restype = None
_cs.MDQueryStop.argtypes = [MDQueryRef]

# MDItemCopyAttribute(item, name) -> CFTypeRef (owned)
_cs.MDItemCopyAttribute.restype = CFTypeRef
_cs.MDItemCopyAttribute.argtypes = [MDItemRef, CFStringRef]

# Pre-allocate the kMDItemPath attribute string (module-level)
_PATH_ATTR_PTR = _cfstr("kMDItemPath")

# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------


def _escape_query(s: str) -> str:
    r"""Escape special characters for MDQuery ``kMDItemFSName`` expressions.

    Characters ``\``, ``"``, and ``*`` are backslash-escaped.
    """
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("*", "\\*")
    return s


def _build_query_string(query: str, content_type: str | None = None) -> str:
    """Build an MDQuery query string for filename matching.

    Uses ``cd`` flags for case- and diacritic-insensitive matching.

    *content_type* optionally filters results:
      - ``"public.folder"`` — only folders
      - ``"!public.folder"`` — exclude folders (files only)
    """
    escaped = _escape_query(query)
    qs = f'kMDItemFSName == "*{escaped}*"cd'
    if content_type == "public.folder":
        qs += ' && kMDItemContentType == "public.folder"'
    elif content_type == "!public.folder":
        qs += ' && kMDItemContentType != "public.folder"'
    return qs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def mdquery_search(
    query: str, max_results: int = 30, content_type: str | None = None,
) -> list[str]:
    """Search Spotlight for files matching *query* by name.

    Returns up to *max_results* file paths.  Uses ``MDQuerySetMaxCount``
    for server-side limiting so we never transfer thousands of results.

    *content_type* is forwarded to ``_build_query_string`` for filtering.
    """
    if not query or not query.strip():
        return []

    qs = _build_query_string(query.strip(), content_type=content_type)
    qs_ref = _cfstr(qs)
    md_query = None

    try:
        md_query = _cs.MDQueryCreate(kCFAllocatorDefault, qs_ref, None, None)
        if not md_query:
            logger.debug("MDQueryCreate returned NULL for: %s", qs)
            return []

        _cs.MDQuerySetMaxCount(md_query, max_results)

        ok = _cs.MDQueryExecute(md_query, kMDQuerySynchronous)
        if not ok:
            logger.debug("MDQueryExecute failed for: %s", qs)
            return []

        count = _cs.MDQueryGetResultCount(md_query)
        paths: list[str] = []
        for i in range(count):
            item = _cs.MDQueryGetResultAtIndex(md_query, i)
            if not item:
                continue
            attr = _cs.MDItemCopyAttribute(item, _PATH_ATTR_PTR)
            if not attr:
                continue
            try:
                path = _cfstr_to_py(attr)
                if path:
                    paths.append(path)
            finally:
                _cf.CFRelease(attr)

        return paths

    except Exception:
        logger.debug("mdquery_search failed for: %s", query, exc_info=True)
        return []
    finally:
        if md_query:
            _cs.MDQueryStop(md_query)
            _cf.CFRelease(md_query)
        _cf.CFRelease(qs_ref)
