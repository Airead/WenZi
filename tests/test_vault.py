"""Tests for wenzi.vault — shared encrypted vault singleton."""

import json
import os
import stat
from unittest.mock import patch

MOCK_MASTER_KEY_B64 = "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="  # 32 bytes


class TestVaultInit:
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_loads_existing_master_key(self, mock_get, tmp_path):
        from wenzi.vault import Vault

        v = Vault(vault_path=str(tmp_path / "vault.json"))
        assert v._master_key is not None
        assert len(v._master_key) == 32
        mock_get.assert_called_once_with("scripting.vault.master_key")

    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=None)
    def test_generates_master_key_when_absent(self, mock_get, mock_set, tmp_path):
        from wenzi.vault import Vault

        v = Vault(vault_path=str(tmp_path / "vault.json"))
        assert v._master_key is not None
        assert len(v._master_key) == 32
        mock_set.assert_called_once()
        import base64
        stored_b64 = mock_set.call_args[0][1]
        assert len(base64.b64decode(stored_b64)) == 32

    @patch("wenzi.vault._keychain_set", return_value=False)
    @patch("wenzi.vault._keychain_get", return_value=None)
    def test_degrades_when_keychain_unavailable(self, mock_get, mock_set, tmp_path):
        from wenzi.vault import Vault

        v = Vault(vault_path=str(tmp_path / "vault.json"))
        assert v._master_key is None


class TestVaultCRUD:
    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_set_get_roundtrip(self, mock_get, mock_set, tmp_path):
        from wenzi.vault import Vault

        v = Vault(vault_path=str(tmp_path / "vault.json"))
        assert v.set("token", "secret123") is True
        assert v.get("token") == "secret123"

    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_delete_removes_key(self, mock_get, mock_set, tmp_path):
        from wenzi.vault import Vault

        v = Vault(vault_path=str(tmp_path / "vault.json"))
        v.set("token", "secret123")
        v.delete("token")
        assert v.get("token") is None

    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_keys_returns_stored_keys(self, mock_get, mock_set, tmp_path):
        from wenzi.vault import Vault

        v = Vault(vault_path=str(tmp_path / "vault.json"))
        v.set("a", "1")
        v.set("b", "2")
        assert sorted(v.keys()) == ["a", "b"]

    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_degraded_mode(self, mock_get, mock_set, tmp_path):
        from wenzi.vault import Vault

        v = Vault(vault_path=str(tmp_path / "vault.json"))
        v._master_key = None
        assert v.get("x") is None
        assert v.set("x", "y") is False
        v.delete("x")  # no-op, no error


class TestVaultDeletePrefix:
    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_delete_prefix_removes_matching(self, mock_get, mock_set, tmp_path):
        from wenzi.vault import Vault

        v = Vault(vault_path=str(tmp_path / "vault.json"))
        v.set("asr.providers.groq.api_key", "key1")
        v.set("asr.providers.groq.base_url", "url1")
        v.set("asr.providers.openai.api_key", "key2")
        v.delete_prefix("asr.providers.groq.")
        assert v.get("asr.providers.groq.api_key") is None
        assert v.get("asr.providers.groq.base_url") is None
        assert v.get("asr.providers.openai.api_key") == "key2"

    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_delete_prefix_no_match_is_noop(self, mock_get, mock_set, tmp_path):
        from wenzi.vault import Vault

        v = Vault(vault_path=str(tmp_path / "vault.json"))
        v.set("asr.providers.groq.api_key", "key1")
        v.delete_prefix("nonexistent.")
        assert v.get("asr.providers.groq.api_key") == "key1"


class TestVaultFlush:
    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_flush_sync_writes_to_disk(self, mock_get, mock_set, tmp_path):
        from wenzi.vault import Vault

        vault_path = str(tmp_path / "vault.json")
        v = Vault(vault_path=vault_path)
        v.set("token", "secret")
        v.flush_sync()
        assert os.path.isfile(vault_path)
        with open(vault_path) as f:
            data = json.load(f)
        assert "token" in data
        assert data["token"] != "secret"  # encrypted

    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_persistence_across_instances(self, mock_get, mock_set, tmp_path):
        from wenzi.vault import Vault

        vault_path = str(tmp_path / "vault.json")
        v1 = Vault(vault_path=vault_path)
        v1.set("token", "persisted")
        v1.flush_sync()

        v2 = Vault(vault_path=vault_path)
        assert v2.get("token") == "persisted"

    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_flush_sets_file_permissions_0600(self, mock_get, mock_set, tmp_path):
        from wenzi.vault import Vault

        vault_path = str(tmp_path / "vault.json")
        v = Vault(vault_path=vault_path)
        v.set("token", "secret")
        v.flush_sync()
        mode = stat.S_IMODE(os.stat(vault_path).st_mode)
        assert mode == 0o600

    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_dirty_re_set_on_replace_failure(self, mock_get, mock_set, tmp_path):
        from wenzi.vault import Vault

        vault_path = str(tmp_path / "vault.json")
        v = Vault(vault_path=vault_path)
        v.set("token", "secret")
        with patch("os.replace", side_effect=OSError("disk full")):
            v.flush_sync()
        assert v._dirty is True

    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_tmp_file_cleaned_on_replace_failure(self, mock_get, mock_set, tmp_path):
        from wenzi.vault import Vault

        vault_path = str(tmp_path / "vault.json")
        v = Vault(vault_path=vault_path)
        v.set("token", "secret")
        with patch("os.replace", side_effect=OSError("disk full")):
            v.flush_sync()
        tmp_path_file = vault_path + ".tmp"
        assert not os.path.exists(tmp_path_file)


class TestVaultMigration:
    @patch("wenzi.vault._keychain_delete")
    @patch("wenzi.vault._keychain_list")
    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get")
    def test_migrates_old_entries(self, mock_get, mock_set, mock_list, mock_delete, tmp_path):
        from wenzi.vault import Vault

        mock_get.side_effect = lambda acct: {
            "scripting.vault.master_key": MOCK_MASTER_KEY_B64,
            "ai_enhance.providers.openai.api_key": "sk-old-key",
            "asr.providers.groq.api_key": "gsk-old-key",
        }.get(acct)
        mock_list.side_effect = lambda prefix: {
            "ai_enhance.providers.": ["ai_enhance.providers.openai.api_key"],
            "asr.providers.": ["asr.providers.groq.api_key"],
        }.get(prefix, [])

        v = Vault(vault_path=str(tmp_path / "vault.json"))
        assert v.get("ai_enhance.providers.openai.api_key") == "sk-old-key"
        assert v.get("asr.providers.groq.api_key") == "gsk-old-key"
        assert mock_delete.call_count == 2

    @patch("wenzi.vault._keychain_delete")
    @patch("wenzi.vault._keychain_list")
    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_migration_skips_when_no_old_entries(self, mock_get, mock_set, mock_list, mock_delete, tmp_path):
        from wenzi.vault import Vault

        mock_list.return_value = []
        v = Vault(vault_path=str(tmp_path / "vault.json"))
        # Trigger _ensure_loaded by reading
        v.keys()
        mock_delete.assert_not_called()

    @patch("wenzi.vault._keychain_delete")
    @patch("wenzi.vault._keychain_list")
    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get")
    def test_migration_skips_already_existing_keys(self, mock_get, mock_set, mock_list, mock_delete, tmp_path):
        from wenzi.vault import Vault

        mock_get.side_effect = lambda acct: {
            "scripting.vault.master_key": MOCK_MASTER_KEY_B64,
            "ai_enhance.providers.openai.api_key": "sk-old",
        }.get(acct)
        mock_list.return_value = ["ai_enhance.providers.openai.api_key"]

        # Pre-create vault file with existing entry
        v_setup = Vault(vault_path=str(tmp_path / "vault.json"))
        v_setup.set("ai_enhance.providers.openai.api_key", "sk-new")
        v_setup.flush_sync()

        # New instance should NOT overwrite existing key
        mock_list.return_value = ["ai_enhance.providers.openai.api_key"]
        v = Vault(vault_path=str(tmp_path / "vault.json"))
        assert v.get("ai_enhance.providers.openai.api_key") == "sk-new"
        # Should still delete old Keychain entry
        mock_delete.assert_called_with("ai_enhance.providers.openai.api_key")


class TestGetVault:
    @patch("wenzi.vault._keychain_set", return_value=True)
    @patch("wenzi.vault._keychain_get", return_value=MOCK_MASTER_KEY_B64)
    def test_singleton_returns_same_instance(self, mock_get, mock_set, tmp_path):
        import wenzi.vault as vault_mod

        vault_mod._vault = None  # reset singleton
        vault_mod._DEFAULT_PATH = str(tmp_path / "vault.json")
        v1 = vault_mod.get_vault()
        v2 = vault_mod.get_vault()
        assert v1 is v2
        vault_mod._vault = None  # cleanup
