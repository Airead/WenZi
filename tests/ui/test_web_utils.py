"""Tests for wenzi.ui.web_utils — lightweight WKWebView configuration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_mock_webkit():
    """Build a mock WebKit module with the classes lightweight_webview_config needs."""
    mock_webkit = MagicMock()

    mock_config = MagicMock(name="WKWebViewConfig")
    mock_webkit.WKWebViewConfiguration.alloc.return_value.init.return_value = mock_config

    mock_store = MagicMock(name="nonPersistentStore")
    mock_webkit.WKWebsiteDataStore.nonPersistentDataStore.return_value = mock_store

    return mock_webkit, mock_config, mock_store


class TestLightweightWebviewConfig:
    def test_returns_config(self):
        mock_webkit, mock_config, _ = _make_mock_webkit()
        with patch.dict("sys.modules", {"WebKit": mock_webkit}):
            from wenzi.ui.web_utils import _reset_shared_state, lightweight_webview_config

            _reset_shared_state()
            config = lightweight_webview_config()
            assert config is mock_config

    def test_uses_non_persistent_data_store_by_default(self):
        mock_webkit, mock_config, mock_store = _make_mock_webkit()
        with patch.dict("sys.modules", {"WebKit": mock_webkit}):
            from wenzi.ui.web_utils import _reset_shared_state, lightweight_webview_config

            _reset_shared_state()
            lightweight_webview_config()
            mock_config.setWebsiteDataStore_.assert_called_once_with(mock_store)

    def test_non_persistent_store_is_cached(self):
        mock_webkit, mock_config, _ = _make_mock_webkit()
        with patch.dict("sys.modules", {"WebKit": mock_webkit}):
            from wenzi.ui.web_utils import _reset_shared_state, lightweight_webview_config

            _reset_shared_state()
            lightweight_webview_config()
            lightweight_webview_config()
            # nonPersistentDataStore() should only be called once
            mock_webkit.WKWebsiteDataStore.nonPersistentDataStore.assert_called_once()

    def test_network_true_skips_non_persistent_store(self):
        mock_webkit, mock_config, _ = _make_mock_webkit()
        with patch.dict("sys.modules", {"WebKit": mock_webkit}):
            from wenzi.ui.web_utils import _reset_shared_state, lightweight_webview_config

            _reset_shared_state()
            lightweight_webview_config(network=True)
            mock_config.setWebsiteDataStore_.assert_not_called()

    def test_no_process_pool_set(self):
        """WKProcessPool is deprecated — config should NOT set one."""
        mock_webkit, mock_config, _ = _make_mock_webkit()
        with patch.dict("sys.modules", {"WebKit": mock_webkit}):
            from wenzi.ui.web_utils import _reset_shared_state, lightweight_webview_config

            _reset_shared_state()
            lightweight_webview_config()
            mock_config.setProcessPool_.assert_not_called()
