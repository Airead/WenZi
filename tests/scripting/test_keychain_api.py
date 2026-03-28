"""Tests for wz.keychain — verifies KeychainAPI delegates to vault."""

from unittest.mock import patch

import wenzi.vault as vault_mod

MOCK_MASTER_KEY_B64 = "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="


class TestKeychainAPIDelegation:
    """KeychainAPI must delegate every call to the shared Vault singleton."""

    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_get_delegates(self, mock_kc_get, mock_kc_set, tmp_path):
        vault_mod._vault = None
        vault_mod._DEFAULT_PATH = str(tmp_path / "vault.json")
        from wenzi.scripting.api.keychain import KeychainAPI

        api = KeychainAPI()
        api.set("token", "secret123")
        assert api.get("token") == "secret123"
        vault_mod._vault = None

    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_delete_delegates(self, mock_kc_get, mock_kc_set, tmp_path):
        vault_mod._vault = None
        vault_mod._DEFAULT_PATH = str(tmp_path / "vault.json")
        from wenzi.scripting.api.keychain import KeychainAPI

        api = KeychainAPI()
        api.set("token", "secret")
        api.delete("token")
        assert api.get("token") is None
        vault_mod._vault = None

    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_keys_delegates(self, mock_kc_get, mock_kc_set, tmp_path):
        vault_mod._vault = None
        vault_mod._DEFAULT_PATH = str(tmp_path / "vault.json")
        from wenzi.scripting.api.keychain import KeychainAPI

        api = KeychainAPI()
        api.set("a", "1")
        api.set("b", "2")
        assert sorted(api.keys()) == ["a", "b"]
        vault_mod._vault = None

    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_flush_sync_delegates(self, mock_kc_get, mock_kc_set, tmp_path):
        vault_mod._vault = None
        vault_mod._DEFAULT_PATH = str(tmp_path / "vault.json")
        from wenzi.scripting.api.keychain import KeychainAPI

        api = KeychainAPI()
        api.set("token", "val")
        api.flush_sync()
        import os
        assert os.path.isfile(str(tmp_path / "vault.json"))
        vault_mod._vault = None
