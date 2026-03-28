"""wz.keychain — encrypted key-value vault for plugin secrets.

Thin delegation to the shared ``wenzi.vault.Vault`` singleton.
"""

from __future__ import annotations

from typing import List, Optional

from wenzi.vault import Vault, get_vault


class KeychainAPI:
    """Plugin-facing encrypted key-value store.

    Delegates all operations to the shared ``Vault`` singleton.
    """

    def __init__(self) -> None:
        self._vault: Vault = get_vault()

    def get(self, key: str) -> Optional[str]:
        return self._vault.get(key)

    def set(self, key: str, value: str) -> bool:
        return self._vault.set(key, value)

    def delete(self, key: str) -> None:
        self._vault.delete(key)

    def keys(self) -> List[str]:
        return self._vault.keys()

    def flush_sync(self) -> None:
        self._vault.flush_sync()
