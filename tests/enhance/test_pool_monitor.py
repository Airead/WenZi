"""Tests for the connection monitoring module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from wenzi.enhance.pool_monitor import (
    PoolMonitor,
    _parse_host_port,
    get_os_socket_stats,
)

# ---------------------------------------------------------------------------
# _parse_host_port
# ---------------------------------------------------------------------------

class TestParseHostPort:
    def test_https_with_port(self):
        assert _parse_host_port("https://api.example.com:8443/v1") == ("api.example.com", 8443)

    def test_https_default_port(self):
        assert _parse_host_port("https://api.example.com/v1") == ("api.example.com", 443)

    def test_http_default_port(self):
        assert _parse_host_port("http://localhost/v1") == ("localhost", 80)

    def test_http_with_port(self):
        assert _parse_host_port("http://localhost:11434/v1") == ("localhost", 11434)

    def test_invalid_url(self):
        assert _parse_host_port("not-a-url") is None

    def test_empty_string(self):
        assert _parse_host_port("") is None


# ---------------------------------------------------------------------------
# get_os_socket_stats
# ---------------------------------------------------------------------------

class TestGetOsSocketStats:
    def test_parses_lsof_output(self):
        fake_output = (
            "COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
            "python  12345 user   10u  IPv4 0x1234      0t0  TCP 127.0.0.1:54321->93.184.216.34:443 (ESTABLISHED)\n"
            "python  12345 user   11u  IPv4 0x1235      0t0  TCP 127.0.0.1:54322->93.184.216.34:443 (TIME_WAIT)\n"
            "python  12345 user   12u  IPv4 0x1236      0t0  TCP 127.0.0.1:54323->93.184.216.34:443 (ESTABLISHED)\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=fake_output)
            stats = get_os_socket_stats("https://93.184.216.34:443/v1")
        assert stats["ESTABLISHED"] == 2
        assert stats["TIME_WAIT"] == 1
        assert stats["CLOSE_WAIT"] == 0
        assert stats["total"] == 3

    def test_invalid_url(self):
        stats = get_os_socket_stats("")
        assert stats["total"] == 0

    def test_lsof_failure(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            stats = get_os_socket_stats("https://api.example.com:443/v1")
        assert stats["total"] == 0


# ---------------------------------------------------------------------------
# PoolMonitor
# ---------------------------------------------------------------------------

class TestPoolMonitor:
    def _make_monitor(self):
        client = MagicMock()
        providers = {"test_provider": (client, ["model1"], {})}
        providers_config = {"test_provider": {"base_url": "http://localhost:11434/v1"}}
        return PoolMonitor(providers, providers_config)

    def test_log_stats_single_provider(self, caplog):
        monitor = self._make_monitor()
        with caplog.at_level("INFO"):
            monitor.log_stats("test_label", "test_provider")
        assert "[Conn:test_provider] test_label" in caplog.text

    def test_log_stats_all_providers(self, caplog):
        monitor = self._make_monitor()
        with caplog.at_level("INFO"):
            monitor.log_stats("all_test")
        assert "[Conn:test_provider]" in caplog.text

    def test_log_stats_unknown_provider(self, caplog):
        monitor = self._make_monitor()
        with caplog.at_level("INFO"):
            monitor.log_stats("test", "nonexistent")
        # Should not crash, just no output
        assert "[Conn:" not in caplog.text

    def test_stop_periodic_no_task(self):
        monitor = self._make_monitor()
        # Should not raise
        monitor.stop_periodic()

    def test_stop_periodic_with_task(self):
        monitor = self._make_monitor()
        mock_task = MagicMock()
        monitor._periodic_task = mock_task
        monitor.stop_periodic()
        mock_task.cancel.assert_called_once()
        assert monitor._periodic_task is None


# ---------------------------------------------------------------------------
# Dedicated log file setup
# ---------------------------------------------------------------------------

class TestPoolMonitorLogFile:
    """Verify that _setup_logging configures a dedicated pool_monitor log."""

    def test_dedicated_handler_is_attached(self, tmp_path, monkeypatch):
        """After _setup_logging, wenzi.enhance.pool_monitor logger should have
        its own file handler writing to pool_monitor.log with propagate=False."""
        import logging
        import logging.handlers

        monkeypatch.setattr("wenzi.app.LOG_DIR", tmp_path)
        monkeypatch.setattr("wenzi.app.LOG_FILE", tmp_path / "wenzi.log")

        # Minimal config dict
        config = {"logging": {"level": "INFO"}}

        # Build a lightweight WenZiApp stub that only runs _setup_logging
        from wenzi.app import WenZiApp

        app = object.__new__(WenZiApp)
        app._config = config

        # Clean up root logger handlers to avoid test pollution
        root = logging.getLogger()
        old_handlers = root.handlers[:]
        root.handlers.clear()

        pool_logger = logging.getLogger("wenzi.enhance.pool_monitor")
        old_pool_handlers = pool_logger.handlers[:]
        old_propagate = pool_logger.propagate
        pool_logger.handlers.clear()

        try:
            app._setup_logging()

            assert pool_logger.propagate is False
            file_handlers = [
                h for h in pool_logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert len(file_handlers) == 1
            handler = file_handlers[0]
            assert handler.baseFilename == str(tmp_path / "pool_monitor.log")
            assert handler.level == logging.DEBUG
            # Call again — should NOT accumulate a second handler
            app._setup_logging()
            file_handlers_after = [
                h for h in pool_logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert len(file_handlers_after) == 1
        finally:
            # Restore original state
            root.handlers[:] = old_handlers
            pool_logger.handlers[:] = old_pool_handlers
            pool_logger.propagate = old_propagate
