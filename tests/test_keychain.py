"""Tests for wenzi.keychain — Core CRUD over macOS Keychain.

All Security.framework calls are mocked so these tests run headless
without touching the real system Keychain.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import wenzi.keychain as kc


# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

_SERVICE = "io.github.airead.wenzi"

# errSecSuccess and errSecItemNotFound as defined by Security.framework
_ERR_SUCCESS = 0
_ERR_NOT_FOUND = -25300
_ERR_OTHER = -25299  # generic failure


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Ensure module-level caches (if any) are clean between tests."""
    yield


# ---------------------------------------------------------------------------
# keychain_get
# ---------------------------------------------------------------------------


class TestKeychainGet:
    def test_returns_value_when_found(self):
        """_keychain_get returns the decoded string when SecItemCopyMatching succeeds."""
        with patch.object(kc, "_sec_item_copy_matching", return_value="secret") as mock_copy:
            result = kc._keychain_get("my_account")

        assert result == "secret"
        mock_copy.assert_called_once_with(_SERVICE, "my_account")

    def test_returns_none_when_not_found(self):
        """_keychain_get returns None when the item does not exist."""
        with patch.object(kc, "_sec_item_copy_matching", return_value=None):
            result = kc._keychain_get("missing_account")

        assert result is None

    def test_returns_none_on_error(self):
        """_keychain_get returns None (does not propagate) when the low-level call raises."""
        with patch.object(kc, "_sec_item_copy_matching", side_effect=Exception("keychain locked")):
            result = kc._keychain_get("any_account")

        assert result is None


# ---------------------------------------------------------------------------
# keychain_set
# ---------------------------------------------------------------------------


class TestKeychainSet:
    def test_adds_new_item_returns_true(self):
        """_keychain_set adds item when it does not yet exist and returns True."""
        with (
            patch.object(kc, "_sec_item_copy_matching", return_value=None),
            patch.object(kc, "_sec_item_add", return_value=True) as mock_add,
        ):
            result = kc._keychain_set("new_account", "new_value")

        assert result is True
        mock_add.assert_called_once_with(_SERVICE, "new_account", "new_value")

    def test_updates_existing_item_returns_true(self):
        """_keychain_set updates item when it already exists and returns True."""
        with (
            patch.object(kc, "_sec_item_copy_matching", return_value="old_value"),
            patch.object(kc, "_sec_item_update", return_value=True) as mock_update,
        ):
            result = kc._keychain_set("existing_account", "new_value")

        assert result is True
        mock_update.assert_called_once_with(_SERVICE, "existing_account", "new_value")

    def test_returns_false_on_add_failure(self):
        """_keychain_set returns False when _sec_item_add fails."""
        with (
            patch.object(kc, "_sec_item_copy_matching", return_value=None),
            patch.object(kc, "_sec_item_add", return_value=False),
        ):
            result = kc._keychain_set("new_account", "value")

        assert result is False

    def test_returns_false_on_update_failure(self):
        """_keychain_set returns False when _sec_item_update fails."""
        with (
            patch.object(kc, "_sec_item_copy_matching", return_value="old"),
            patch.object(kc, "_sec_item_update", return_value=False),
        ):
            result = kc._keychain_set("existing_account", "new_value")

        assert result is False

    def test_returns_false_when_get_raises(self):
        """_keychain_set returns False (does not propagate) when existence check raises."""
        with patch.object(kc, "_sec_item_copy_matching", side_effect=Exception("oops")):
            result = kc._keychain_set("account", "value")

        assert result is False

    def test_returns_false_when_add_raises(self):
        """_keychain_set returns False when _sec_item_add raises an exception."""
        with (
            patch.object(kc, "_sec_item_copy_matching", return_value=None),
            patch.object(kc, "_sec_item_add", side_effect=OSError("SecItemAdd failed")),
        ):
            result = kc._keychain_set("account", "value")

        assert result is False


# ---------------------------------------------------------------------------
# keychain_delete
# ---------------------------------------------------------------------------


class TestKeychainDelete:
    def test_deletes_existing_item(self):
        """_keychain_delete calls _sec_item_delete with correct arguments."""
        with patch.object(kc, "_sec_item_delete") as mock_delete:
            kc._keychain_delete("my_account")

        mock_delete.assert_called_once_with(_SERVICE, "my_account")

    def test_no_exception_on_failure(self):
        """_keychain_delete does not raise even when the low-level call fails."""
        with patch.object(kc, "_sec_item_delete", side_effect=Exception("delete failed")):
            # Should not raise
            kc._keychain_delete("any_account")

    def test_no_exception_when_not_found(self):
        """_keychain_delete is silent when the item does not exist."""
        with patch.object(kc, "_sec_item_delete", side_effect=KeyError("not found")):
            kc._keychain_delete("ghost_account")


# ---------------------------------------------------------------------------
# keychain_list
# ---------------------------------------------------------------------------


class TestKeychainList:
    def test_returns_accounts_matching_prefix(self):
        """_keychain_list returns only accounts whose names start with the prefix."""
        all_accounts = [
            "wenzi.openai.api_key",
            "wenzi.openai.base_url",
            "wenzi.anthropic.api_key",
            "other_service.token",
        ]
        with patch.object(kc, "_sec_item_list", return_value=all_accounts):
            result = kc._keychain_list("wenzi.openai")

        assert result == ["wenzi.openai.api_key", "wenzi.openai.base_url"]

    def test_returns_all_when_prefix_empty(self):
        """_keychain_list with empty prefix returns every account."""
        all_accounts = ["a", "b", "c"]
        with patch.object(kc, "_sec_item_list", return_value=all_accounts):
            result = kc._keychain_list("")

        assert result == ["a", "b", "c"]

    def test_returns_empty_list_on_error(self):
        """_keychain_list returns [] (does not propagate) when the low-level call raises."""
        with patch.object(kc, "_sec_item_list", side_effect=Exception("keychain unavailable")):
            result = kc._keychain_list("wenzi")

        assert result == []

    def test_returns_empty_list_when_no_matches(self):
        """_keychain_list returns [] when nothing matches the prefix."""
        with patch.object(kc, "_sec_item_list", return_value=["unrelated.account"]):
            result = kc._keychain_list("wenzi")

        assert result == []

    def test_calls_sec_item_list_with_service(self):
        """_keychain_list passes the service name to _sec_item_list."""
        with patch.object(kc, "_sec_item_list", return_value=[]) as mock_list:
            kc._keychain_list("any.prefix")

        mock_list.assert_called_once_with(_SERVICE)


# ---------------------------------------------------------------------------
# keychain_clear_prefix
# ---------------------------------------------------------------------------


class TestKeychainClearPrefix:
    def test_deletes_matching_accounts(self):
        """_keychain_clear_prefix deletes all accounts matching the prefix."""
        with (
            patch.object(kc, "_keychain_list", return_value=["p.a", "p.b"]) as mock_list,
            patch.object(kc, "_keychain_delete") as mock_delete,
        ):
            kc._keychain_clear_prefix("p.")

        mock_list.assert_called_once_with("p.")
        assert mock_delete.call_count == 2
        mock_delete.assert_any_call("p.a")
        mock_delete.assert_any_call("p.b")

    def test_no_matches_no_deletes(self):
        """_keychain_clear_prefix does nothing when no accounts match."""
        with (
            patch.object(kc, "_keychain_list", return_value=[]),
            patch.object(kc, "_keychain_delete") as mock_delete,
        ):
            kc._keychain_clear_prefix("missing.")

        mock_delete.assert_not_called()
