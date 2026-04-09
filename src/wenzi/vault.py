"""wenzi.vault — shared AES-256-GCM encrypted vault singleton.

All secrets (provider API keys, plugin tokens, etc.) are stored in a
single JSON file encrypted with a master key held in macOS Keychain.

This module is intentionally self-contained: it does NOT import from
``wenzi.config`` to avoid circular-import issues.  The vault path is
hardcoded to ``~/.local/share/WenZi/keychain.json``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from typing import List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.expanduser(
    os.path.join("~", ".local", "share", "WenZi", "keychain.json")
)
_FLUSH_DELAY = 2.0
_MASTER_KEY_ACCOUNT = "scripting.vault.master_key"
_NONCE_SIZE = 12
_TAG_SIZE = 16

# ---------------------------------------------------------------------------
# Private wrappers around wenzi.keychain (lazy-imported to avoid import-time
# PyObjC failures in headless / test environments).
# ---------------------------------------------------------------------------


def _keychain_get(account: str) -> Optional[str]:
    from wenzi.keychain import _keychain_get as _kc_get

    return _kc_get(account)


def _keychain_set(account: str, value: str) -> bool:
    from wenzi.keychain import _keychain_set as _kc_set

    return _kc_set(account, value)


def _keychain_list(prefix: str = "") -> list[str]:
    from wenzi.keychain import _keychain_list as _kc_list

    return _kc_list(prefix)


def _keychain_delete(account: str) -> None:
    from wenzi.keychain import _keychain_delete as _kc_delete

    _kc_delete(account)


_MIGRATE_PREFIXES = ("ai_enhance.providers.", "asr.providers.")

# ---------------------------------------------------------------------------
# Vault class
# ---------------------------------------------------------------------------


class Vault:
    """Encrypted key-value store backed by a single JSON file.

    Secrets are AES-256-GCM encrypted using a master key stored in the
    macOS Keychain.  Thread-safe with deferred atomic disk writes.
    """

    def __init__(self, vault_path: Optional[str] = None) -> None:
        self._path = vault_path or _DEFAULT_PATH
        self._data: dict[str, str] = {}
        self._loaded = False
        self._lock = threading.RLock()
        self._dirty = False
        self._flush_timer: Optional[threading.Timer] = None
        self._master_key: Optional[bytes] = self._init_master_key()

    # -- master key ---------------------------------------------------------

    def _init_master_key(self) -> Optional[bytes]:
        """Load or generate the AES-256 master key from macOS Keychain."""
        try:
            existing = _keychain_get(_MASTER_KEY_ACCOUNT)
            if existing:
                return base64.b64decode(existing)

            raw_key = os.urandom(32)
            b64_key = base64.b64encode(raw_key).decode("ascii")
            if _keychain_set(_MASTER_KEY_ACCOUNT, b64_key):
                return raw_key

            logger.warning(
                "Failed to store master key in macOS Keychain; "
                "vault will be unavailable"
            )
            return None
        except Exception:
            logger.warning(
                "macOS Keychain unavailable; vault will be unavailable",
                exc_info=True,
            )
            return None

    # -- data loading -------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not os.path.isfile(self._path):
            pass
        else:
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._data = data
                logger.debug("Loaded vault: %d keys", len(self._data))
            except Exception:
                logger.warning("Failed to load vault", exc_info=True)
        self._migrate_from_keychain()

    def _migrate_from_keychain(self) -> None:
        """One-time migration: move old per-secret Keychain entries to vault."""
        if self._master_key is None:
            return
        try:
            old_accounts: list[str] = []
            for prefix in _MIGRATE_PREFIXES:
                old_accounts.extend(_keychain_list(prefix))
            if not old_accounts:
                return

            self._ensure_loaded()
            migrated = 0
            for account in old_accounts:
                if account not in self._data:
                    old_value = _keychain_get(account)
                    if old_value:
                        self.set(account, old_value)
                        migrated += 1
                _keychain_delete(account)

            if migrated:
                self.flush_sync()
                logger.info(
                    "Migrated %d secrets from macOS Keychain to vault",
                    migrated,
                )
        except Exception:
            logger.warning("Keychain migration failed", exc_info=True)

    # -- encryption ---------------------------------------------------------

    def _encrypt(self, key: str, value: str) -> str:
        """Encrypt *value* with AES-256-GCM, using *key* as AAD."""
        from wenzi._commoncrypto import aes_gcm_encrypt

        nonce = os.urandom(_NONCE_SIZE)
        ct = aes_gcm_encrypt(
            self._master_key, nonce,
            value.encode("utf-8"), key.encode("utf-8"),
        )
        return base64.b64encode(nonce + ct).decode("ascii")

    def _decrypt(self, key: str, blob: str) -> Optional[str]:
        """Decrypt a vault entry.  Returns None on any failure."""
        try:
            from wenzi._commoncrypto import aes_gcm_decrypt

            raw = base64.b64decode(blob)
            if len(raw) < _NONCE_SIZE + _TAG_SIZE:
                return None
            nonce = raw[:_NONCE_SIZE]
            ct = raw[_NONCE_SIZE:]
            plaintext = aes_gcm_decrypt(
                self._master_key, nonce, ct, key.encode("utf-8"),
            )
            return plaintext.decode("utf-8")
        except Exception:
            logger.warning("Failed to decrypt vault entry %r", key)
            return None

    # -- public CRUD --------------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        """Return the decrypted value for *key*, or None."""
        if self._master_key is None:
            return None
        with self._lock:
            self._ensure_loaded()
            blob = self._data.get(key)
        if blob is None:
            return None
        return self._decrypt(key, blob)

    def set(self, key: str, value: str) -> bool:
        """Encrypt and store *value* under *key*.  Returns True on success."""
        if self._master_key is None:
            return False
        try:
            blob = self._encrypt(key, value)
        except Exception:
            logger.warning("Failed to encrypt vault entry %r", key, exc_info=True)
            return False
        with self._lock:
            self._ensure_loaded()
            self._data[key] = blob
            self._dirty = True
        self._schedule_flush()
        return True

    def delete(self, key: str) -> None:
        """Remove *key* from the vault.  Silent no-op if missing or degraded."""
        if self._master_key is None:
            return
        removed = False
        with self._lock:
            self._ensure_loaded()
            if key in self._data:
                del self._data[key]
                self._dirty = True
                removed = True
        if removed:
            self._schedule_flush()

    def delete_prefix(self, prefix: str) -> None:
        """Remove all keys starting with *prefix*.  Silent no-op if degraded."""
        if self._master_key is None:
            return
        removed = False
        with self._lock:
            self._ensure_loaded()
            to_remove = [k for k in self._data if k.startswith(prefix)]
            for k in to_remove:
                del self._data[k]
            if to_remove:
                self._dirty = True
                removed = True
        if removed:
            self._schedule_flush()

    def keys(self) -> List[str]:
        """Return all stored key names."""
        with self._lock:
            self._ensure_loaded()
            return list(self._data.keys())

    # -- master key export / import ----------------------------------------

    def export_master_key(self) -> Optional[str]:
        """Return the base64-encoded master key, or None if unavailable."""
        if self._master_key is None:
            return None
        return base64.b64encode(self._master_key).decode("ascii")

    def import_master_key(self, b64_key: str) -> bool:
        """Import a base64-encoded master key into macOS Keychain.

        The new key is stored in Keychain and becomes active after restart.
        Returns True on success.
        """
        try:
            raw_key = base64.b64decode(b64_key, validate=True)
        except Exception:
            logger.warning("Invalid base64 master key")
            return False

        if len(raw_key) != 32:
            logger.warning(
                "Invalid master key length: expected 32 bytes, got %d",
                len(raw_key),
            )
            return False

        b64_clean = base64.b64encode(raw_key).decode("ascii")
        if not _keychain_set(_MASTER_KEY_ACCOUNT, b64_clean):
            logger.warning("Failed to store imported master key in Keychain")
            return False

        logger.info("Master key imported into macOS Keychain")
        return True

    # -- flush --------------------------------------------------------------

    def flush_sync(self) -> None:
        """Immediately flush pending data to disk."""
        with self._lock:
            timer = self._flush_timer
            self._flush_timer = None
        if timer is not None:
            timer.cancel()
        self._flush()

    def _schedule_flush(self) -> None:
        """Schedule a deferred disk write, coalescing rapid updates."""
        with self._lock:
            if self._flush_timer is not None:
                self._flush_timer.cancel()
            self._flush_timer = threading.Timer(_FLUSH_DELAY, self._flush)
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _flush(self) -> None:
        """Atomically write vault to disk (tmp + os.replace)."""
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            snapshot = json.dumps(self._data, ensure_ascii=False)

        tmp_path = self._path + ".tmp"
        try:
            dirpath = os.path.dirname(self._path)
            os.makedirs(dirpath, exist_ok=True)
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with open(fd, "w", encoding="utf-8") as f:
                f.write(snapshot)
            os.replace(tmp_path, self._path)
            os.chmod(self._path, 0o600)
        except Exception:
            logger.warning("Failed to save vault", exc_info=True)
            with self._lock:
                self._dirty = True
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Thread-safe singleton
# ---------------------------------------------------------------------------

_vault: Optional[Vault] = None
_vault_lock = threading.Lock()


def get_vault() -> Vault:
    """Return the shared Vault singleton (double-checked locking)."""
    global _vault
    if _vault is None:
        with _vault_lock:
            if _vault is None:
                _vault = Vault()
    return _vault


def shutdown_vault() -> None:
    """Flush pending vault writes.  Call during app shutdown."""
    v = _vault
    if v is not None:
        v.flush_sync()
