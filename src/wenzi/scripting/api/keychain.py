"""wz.keychain — encrypted key-value vault for plugin secrets."""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from typing import List, Optional

from wenzi.config import DEFAULT_KEYCHAIN_VAULT_PATH
from wenzi.keychain import keychain_get, keychain_set

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.expanduser(DEFAULT_KEYCHAIN_VAULT_PATH)
_FLUSH_DELAY = 2.0
_MASTER_KEY_ACCOUNT = "scripting.vault.master_key"
_NONCE_SIZE = 12


class KeychainAPI:
    """Encrypted key-value store for plugin secrets.

    Secrets are AES-256-GCM encrypted using a master key stored in
    the macOS Keychain.  The encrypted vault lives on disk as JSON.

    Thread-safe with deferred atomic disk writes.
    """

    def __init__(self, vault_path: Optional[str] = None) -> None:
        self._path = vault_path or _DEFAULT_PATH
        self._data: dict[str, str] = {}
        self._loaded = False
        self._lock = threading.Lock()
        self._dirty = False
        self._flush_timer: Optional[threading.Timer] = None
        self._master_key: Optional[bytes] = self._init_master_key()

    def _init_master_key(self) -> Optional[bytes]:
        """Load or generate the AES-256 master key from macOS Keychain."""
        try:
            existing = keychain_get(_MASTER_KEY_ACCOUNT)
            if existing:
                return base64.b64decode(existing)

            raw_key = os.urandom(32)
            b64_key = base64.b64encode(raw_key).decode("ascii")
            if keychain_set(_MASTER_KEY_ACCOUNT, b64_key):
                return raw_key

            logger.warning(
                "Failed to store master key in macOS Keychain; "
                "wz.keychain will be unavailable"
            )
            return None
        except Exception:
            logger.warning(
                "macOS Keychain unavailable; wz.keychain will be unavailable",
                exc_info=True,
            )
            return None

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not os.path.isfile(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._data = data
            logger.debug("Loaded keychain vault: %d keys", len(self._data))
        except Exception:
            logger.warning("Failed to load keychain vault", exc_info=True)

    def _encrypt(self, key: str, value: str) -> str:
        """Encrypt *value* with AES-256-GCM, using *key* as AAD."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(_NONCE_SIZE)
        aesgcm = AESGCM(self._master_key)
        ct = aesgcm.encrypt(nonce, value.encode("utf-8"), key.encode("utf-8"))
        return base64.b64encode(nonce + ct).decode("ascii")

    def _decrypt(self, key: str, blob: str) -> Optional[str]:
        """Decrypt a vault entry.  Returns None on any failure."""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            raw = base64.b64decode(blob)
            if len(raw) < _NONCE_SIZE + 16:
                return None
            nonce = raw[:_NONCE_SIZE]
            ct = raw[_NONCE_SIZE:]
            aesgcm = AESGCM(self._master_key)
            plaintext = aesgcm.decrypt(nonce, ct, key.encode("utf-8"))
            return plaintext.decode("utf-8")
        except Exception:
            logger.warning("Failed to decrypt keychain entry %r", key)
            return None

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
            logger.warning("Failed to encrypt keychain entry %r", key, exc_info=True)
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

    def keys(self) -> List[str]:
        """Return all stored key names."""
        with self._lock:
            self._ensure_loaded()
            return list(self._data.keys())

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

        try:
            dirpath = os.path.dirname(self._path)
            os.makedirs(dirpath, exist_ok=True)
            tmp_path = self._path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(snapshot)
            os.replace(tmp_path, self._path)
        except Exception:
            logger.warning("Failed to save keychain vault", exc_info=True)
