"""Tests for vt.execute API."""

import threading
from unittest.mock import patch, MagicMock

from voicetext.scripting.api.execute import execute, _run


class TestExecute:
    @patch("voicetext.scripting.api.execute.subprocess")
    def test_run_success(self, mock_sp):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "hello\n"
        mock_sp.run.return_value = mock_result

        result = _run("echo hello")
        assert result == "hello\n"

    @patch("voicetext.scripting.api.execute.subprocess")
    def test_run_failure(self, mock_sp):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = MagicMock()
        mock_result.stderr.strip.return_value = "error"
        mock_sp.run.return_value = mock_result

        result = _run("bad_cmd")
        assert result == ""

    @patch("voicetext.scripting.api.execute.subprocess")
    def test_run_timeout(self, mock_sp):
        import subprocess

        mock_sp.run.side_effect = subprocess.TimeoutExpired("cmd", 30)
        mock_sp.TimeoutExpired = subprocess.TimeoutExpired
        result = _run("slow_cmd")
        assert result == ""

    @patch("voicetext.scripting.api.execute._run")
    def test_execute_background(self, mock_run):
        done = threading.Event()

        def side_effect(cmd):
            done.set()
            return ""

        mock_run.side_effect = side_effect
        result = execute("echo hi", background=True)
        assert result is None
        done.wait(timeout=2.0)
        mock_run.assert_called_once_with("echo hi")

    @patch("voicetext.scripting.api.execute._run")
    def test_execute_foreground(self, mock_run):
        mock_run.return_value = "output"
        result = execute("echo hi", background=False)
        assert result == "output"
