"""macOS Keychain CRUD helpers for WenZi sensitive configuration.

All Security.framework calls are isolated in ``_sec_item_*`` functions so
that the public API (``_keychain_get``, ``_keychain_set``, ``_keychain_delete``,
``_keychain_list``) can be tested without touching the real Keychain.

Low-level Security and Foundation symbols are imported lazily inside each
``_sec_item_*`` function to keep module-level imports free of PyObjC so that
the module can be imported in headless test environments.
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# Service name shared across all Keychain items written by this app.
SERVICE = "io.github.airead.wenzi"

# macOS Security.framework status code returned when an item is not found.
_ERR_SEC_ITEM_NOT_FOUND = -25300


# ---------------------------------------------------------------------------
# Low-level Security.framework wrappers
# ---------------------------------------------------------------------------


def _sec_item_copy_matching(service: str, account: str) -> Optional[str]:
    """Return the password string for *account* in *service*, or None if absent.

    Raises on unexpected Security.framework errors.
    """
    from Foundation import NSString  # noqa: PLC0415
    from Security import (  # noqa: PLC0415
        SecItemCopyMatching,
        kSecClass,
        kSecClassGenericPassword,
        kSecAttrService,
        kSecAttrAccount,
        kSecReturnData,
        kSecMatchLimit,
        kSecMatchLimitOne,
    )

    query = {
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecAttrAccount: account,
        kSecReturnData: True,
        kSecMatchLimit: kSecMatchLimitOne,
    }
    status, result = SecItemCopyMatching(query, None)
    if status == _ERR_SEC_ITEM_NOT_FOUND:
        return None
    if status != 0:
        raise OSError(f"SecItemCopyMatching returned status {status}")
    if result is None:
        return None
    # result is NSData; decode to str
    return NSString.alloc().initWithData_encoding_(result, 4)  # NSUTF8StringEncoding = 4


def _sec_item_add(service: str, account: str, value: str) -> bool:
    """Add a new generic-password item.  Returns True on success."""
    from Foundation import NSString  # noqa: PLC0415
    from Security import (  # noqa: PLC0415
        SecItemAdd,
        kSecClass,
        kSecClassGenericPassword,
        kSecAttrService,
        kSecAttrAccount,
        kSecValueData,
    )

    data = NSString.stringWithString_(value).dataUsingEncoding_(4)  # NSUTF8StringEncoding
    attrs = {
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecAttrAccount: account,
        kSecValueData: data,
    }
    status, _ = SecItemAdd(attrs, None)
    if status != 0:
        logger.warning("SecItemAdd failed for account=%r status=%d", account, status)
        return False
    return True


def _sec_item_update(service: str, account: str, value: str) -> bool:
    """Update the password of an existing generic-password item.  Returns True on success."""
    from Foundation import NSString  # noqa: PLC0415
    from Security import (  # noqa: PLC0415
        SecItemUpdate,
        kSecClass,
        kSecClassGenericPassword,
        kSecAttrService,
        kSecAttrAccount,
        kSecValueData,
    )

    query = {
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecAttrAccount: account,
    }
    data = NSString.stringWithString_(value).dataUsingEncoding_(4)  # NSUTF8StringEncoding
    update = {kSecValueData: data}
    status = SecItemUpdate(query, update)
    if status != 0:
        logger.warning("SecItemUpdate failed for account=%r status=%d", account, status)
        return False
    return True


def _sec_item_delete(service: str, account: str) -> None:
    """Delete a generic-password item.

    Silently ignores ``errSecItemNotFound``; raises on other errors.
    """
    from Security import (  # noqa: PLC0415
        SecItemDelete,
        kSecClass,
        kSecClassGenericPassword,
        kSecAttrService,
        kSecAttrAccount,
    )

    query = {
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecAttrAccount: account,
    }
    status = SecItemDelete(query)
    if status not in (0, _ERR_SEC_ITEM_NOT_FOUND):
        raise OSError(f"SecItemDelete returned status {status}")


def _sec_item_list(service: str) -> List[str]:
    """Return all account names stored under *service*.

    Returns an empty list when no items exist.
    """
    from Security import (  # noqa: PLC0415
        SecItemCopyMatching,
        kSecClass,
        kSecClassGenericPassword,
        kSecAttrService,
        kSecReturnAttributes,
        kSecMatchLimit,
        kSecMatchLimitAll,
        kSecAttrAccount,
    )

    query = {
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecReturnAttributes: True,
        kSecMatchLimit: kSecMatchLimitAll,
    }
    status, results = SecItemCopyMatching(query, None)
    if status == _ERR_SEC_ITEM_NOT_FOUND:
        return []
    if status != 0:
        raise OSError(f"SecItemCopyMatching (list) returned status {status}")
    if not results:
        return []
    return [str(item[kSecAttrAccount]) for item in results if kSecAttrAccount in item]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _keychain_get(account: str) -> Optional[str]:
    """Return the secret string for *account*, or None if absent or on error."""
    try:
        return _sec_item_copy_matching(SERVICE, account)
    except Exception:
        logger.exception("keychain_get failed for account=%r", account)
        return None


def _keychain_set(account: str, value: str) -> bool:
    """Store *value* for *account* in the Keychain.

    Adds a new item if the account does not yet exist; updates it otherwise.
    Returns True on success, False on failure.
    """
    try:
        existing = _sec_item_copy_matching(SERVICE, account)
    except Exception:
        logger.exception("keychain_set existence-check failed for account=%r", account)
        return False

    try:
        if existing is None:
            return _sec_item_add(SERVICE, account, value)
        return _sec_item_update(SERVICE, account, value)
    except Exception:
        logger.exception("keychain_set write failed for account=%r", account)
        return False


def _keychain_delete(account: str) -> None:
    """Remove *account* from the Keychain.  Logs a warning on failure but never raises."""
    try:
        _sec_item_delete(SERVICE, account)
    except Exception:
        logger.warning("keychain_delete failed for account=%r", account, exc_info=True)


def _keychain_list(prefix: str = "") -> List[str]:
    """Return all account names under SERVICE that start with *prefix*.

    Returns an empty list on error.
    """
    try:
        all_accounts = _sec_item_list(SERVICE)
    except Exception:
        logger.exception("keychain_list failed")
        return []
    return [a for a in all_accounts if a.startswith(prefix)]


def _keychain_clear_prefix(prefix: str) -> None:
    """Delete all Keychain accounts whose names start with *prefix*."""
    for account in _keychain_list(prefix):
        _keychain_delete(account)

