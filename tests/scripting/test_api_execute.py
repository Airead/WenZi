"""Tests for vt.execute API."""

import threading
from unittest.mock import MagicMock, patch

from wenzi.scripting.api.execute import _run, execute


class TestRun:
    @patch("wenzi.scripting.api.execute.subprocess")
    def test_run_success(self, mock_sp):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "hello\n"
        mock_result.stderr = ""
        mock_sp.run.return_value = mock_result

        result = _run("echo hello")
        assert result == {"stdout": "hello\n", "stderr": "", "returncode": 0}

    @patch("wenzi.scripting.api.execute.subprocess")
    def test_run_failure(self, mock_sp):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error\n"
        mock_sp.run.return_value = mock_result

        result = _run("bad_cmd")
        assert result["returncode"] == 1
        assert result["stderr"] == "error\n"

    @patch("wenzi.scripting.api.execute.subprocess")
    def test_run_timeout(self, mock_sp):
        import subprocess

        mock_sp.run.side_effect = subprocess.TimeoutExpired("cmd", 30)
        mock_sp.TimeoutExpired = subprocess.TimeoutExpired
        result = _run("slow_cmd")
        assert result["returncode"] == -1
        assert result["stderr"] == "timeout"

    @patch("wenzi.scripting.api.execute.subprocess")
    def test_run_exception(self, mock_sp):
        import subprocess
        mock_sp.TimeoutExpired = subprocess.TimeoutExpired
        mock_sp.run.side_effect = OSError("no such file")
        result = _run("bad")
        assert result["returncode"] == -1
        assert "no such file" in result["stderr"]

    @patch("wenzi.scripting.api.execute.subprocess")
    def test_run_custom_timeout(self, mock_sp):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_sp.run.return_value = mock_result

        _run("cmd", timeout=60)
        _, kwargs = mock_sp.run.call_args
        assert kwargs["timeout"] == 60


class TestExecute:
    @patch("wenzi.scripting.api.execute._run")
    def test_execute_background(self, mock_run):
        done = threading.Event()

        def side_effect(cmd, timeout=30):
            done.set()
            return {"stdout": "", "stderr": "", "returncode": 0}

        mock_run.side_effect = side_effect
        result = execute("echo hi", background=True)
        assert result is None
        done.wait(timeout=2.0)
        mock_run.assert_called_once_with("echo hi", timeout=30)

    @patch("wenzi.scripting.api.execute._run")
    def test_execute_foreground(self, mock_run):
        mock_run.return_value = {"stdout": "output", "stderr": "", "returncode": 0}
        result = execute("echo hi", background=False)
        assert result == {"stdout": "output", "stderr": "", "returncode": 0}

    @patch("wenzi.scripting.api.execute._run")
    def test_execute_foreground_with_timeout(self, mock_run):
        mock_run.return_value = {"stdout": "", "stderr": "", "returncode": 0}
        execute("cmd", background=False, timeout=60)
        mock_run.assert_called_once_with("cmd", timeout=60)

    @patch("wenzi.scripting.api.execute._run")
    def test_execute_background_on_done(self, mock_run):
        done = threading.Event()
        results = []

        mock_run.return_value = {"stdout": "ok", "stderr": "", "returncode": 0}

        def on_done(r):
            results.append(r)
            done.set()

        execute("echo hi", background=True, on_done=on_done)
        done.wait(timeout=2.0)
        assert len(results) == 1
        assert results[0]["stdout"] == "ok"

    @patch("wenzi.scripting.api.execute._run")
    def test_execute_background_on_done_error_handled(self, mock_run):
        """on_done callback errors should not propagate."""
        done = threading.Event()

        mock_run.return_value = {"stdout": "", "stderr": "", "returncode": 0}

        def on_done(r):
            done.set()
            raise RuntimeError("callback error")

        execute("cmd", background=True, on_done=on_done)
        done.wait(timeout=2.0)  # Should not hang
