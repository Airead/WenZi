"""Tests for OCR text recognition module."""

import json
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from wenzi.scripting.ocr import (
    _build_command,
    _recognize_local,
    recognize_text,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_mock_quartz(observations=None, success=True):
    """Set up mock Quartz and Foundation modules in sys.modules.

    Returns (mock_quartz, mock_foundation, mock_handler, mock_request).
    """
    mock_quartz = MagicMock()

    mock_request = MagicMock()
    mock_request.results.return_value = observations or []

    mock_quartz.VNRecognizeTextRequest.alloc.return_value.init.return_value = mock_request

    mock_handler = MagicMock()
    mock_handler.performRequests_error_.return_value = success
    mock_quartz.VNImageRequestHandler.alloc.return_value.initWithURL_options_.return_value = mock_handler

    mock_foundation = MagicMock()

    return mock_quartz, mock_foundation, mock_handler, mock_request


def _make_observation(text: str):
    """Create a mock VNRecognizedTextObservation."""
    candidate = MagicMock()
    candidate.string.return_value = text

    obs = MagicMock()
    obs.topCandidates_.return_value = [candidate]
    return obs


# ------------------------------------------------------------------
# _recognize_local (in-process Vision logic)
# ------------------------------------------------------------------


class TestRecognizeLocal:
    def test_basic_recognition(self):
        obs = _make_observation("Hello World")
        mock_quartz, mock_foundation, _, _ = _make_mock_quartz(
            observations=[obs],
        )
        with patch.dict(sys.modules, {
            "Quartz": mock_quartz, "Foundation": mock_foundation,
        }):
            result = _recognize_local("/fake/image.png", ["en-US"])
        assert result == "Hello World"

    def test_multiple_lines(self):
        observations = [
            _make_observation("Line 1"),
            _make_observation("Line 2"),
            _make_observation("Line 3"),
        ]
        mock_quartz, mock_foundation, _, _ = _make_mock_quartz(
            observations=observations,
        )
        with patch.dict(sys.modules, {
            "Quartz": mock_quartz, "Foundation": mock_foundation,
        }):
            result = _recognize_local("/fake/image.png", ["en-US"])
        assert result == "Line 1\nLine 2\nLine 3"

    def test_no_text_found(self):
        mock_quartz, mock_foundation, _, _ = _make_mock_quartz(observations=[])
        with patch.dict(sys.modules, {
            "Quartz": mock_quartz, "Foundation": mock_foundation,
        }):
            result = _recognize_local("/fake/image.png", ["en-US"])
        assert result == ""

    def test_vision_request_failure(self):
        mock_quartz, mock_foundation, _, _ = _make_mock_quartz(success=False)
        with patch.dict(sys.modules, {
            "Quartz": mock_quartz, "Foundation": mock_foundation,
        }):
            result = _recognize_local("/fake/image.png", ["en-US"])
        assert result == ""

    def test_custom_languages(self):
        obs = _make_observation("Test")
        mock_quartz, mock_foundation, _, mock_request = _make_mock_quartz(
            observations=[obs],
        )
        with patch.dict(sys.modules, {
            "Quartz": mock_quartz, "Foundation": mock_foundation,
        }):
            _recognize_local("/fake/image.png", ["ja"])
        mock_request.setRecognitionLanguages_.assert_called_with(["ja"])


# ------------------------------------------------------------------
# recognize_text (subprocess dispatch)
# ------------------------------------------------------------------


class TestRecognizeText:
    def test_returns_subprocess_stdout(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Hello", stderr="",
        )
        with patch("wenzi.scripting.ocr.subprocess.run", return_value=completed):
            assert recognize_text("/img.png") == "Hello"

    def test_returns_empty_on_nonzero_exit(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="err",
        )
        with patch("wenzi.scripting.ocr.subprocess.run", return_value=completed):
            assert recognize_text("/img.png") == ""

    def test_returns_empty_on_timeout(self):
        with patch(
            "wenzi.scripting.ocr.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30),
        ):
            assert recognize_text("/img.png") == ""

    def test_returns_empty_on_exception(self):
        with patch(
            "wenzi.scripting.ocr.subprocess.run",
            side_effect=OSError("spawn failed"),
        ):
            assert recognize_text("/img.png") == ""

    def test_default_languages(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        with patch("wenzi.scripting.ocr.subprocess.run", return_value=completed) as mock_run:
            recognize_text("/img.png")
        cmd = mock_run.call_args[0][0]
        langs = json.loads(cmd[-1])
        assert langs == ["zh-Hans", "zh-Hant", "en-US"]

    def test_custom_languages_forwarded(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        with patch("wenzi.scripting.ocr.subprocess.run", return_value=completed) as mock_run:
            recognize_text("/img.png", languages=["ja", "en-US"])
        cmd = mock_run.call_args[0][0]
        langs = json.loads(cmd[-1])
        assert langs == ["ja", "en-US"]


# ------------------------------------------------------------------
# _build_command
# ------------------------------------------------------------------


class TestBuildCommand:
    def test_development_mode(self):
        with patch.object(sys, "frozen", False, create=True):
            cmd = _build_command("/img.png", ["en-US"])
        assert cmd[1:3] == ["-m", "wenzi.scripting.ocr"]
        assert cmd[3] == "/img.png"
        assert json.loads(cmd[4]) == ["en-US"]

    def test_frozen_mode(self):
        with patch.object(sys, "frozen", True, create=True):
            cmd = _build_command("/img.png", ["en-US"])
        assert cmd[1] == "--ocr-worker"
        assert cmd[2] == "/img.png"
        assert json.loads(cmd[3]) == ["en-US"]


# ------------------------------------------------------------------
# _main (subprocess entry point)
# ------------------------------------------------------------------


class TestMain:
    def test_writes_result_to_stdout(self, capsys):
        with patch(
            "wenzi.scripting.ocr._recognize_local", return_value="detected text",
        ), patch.object(
            sys, "argv", ["prog", "/img.png", '["en-US"]'],
        ):
            from wenzi.scripting.ocr import _main
            _main()
        assert capsys.readouterr().out == "detected text"

    def test_exits_on_error(self):
        with patch(
            "wenzi.scripting.ocr._recognize_local",
            side_effect=RuntimeError("boom"),
        ), patch.object(
            sys, "argv", ["prog", "/img.png", '["en-US"]'],
        ):
            from wenzi.scripting.ocr import _main
            with pytest.raises(SystemExit, match="1"):
                _main()
