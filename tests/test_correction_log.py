"""Tests for the correction log module."""

from __future__ import annotations

import json
import os

import pytest

from voicetext.correction_log import CorrectionLogger


class TestCorrectionLogger:
    """Test CorrectionLogger JSONL writing."""

    def test_log_writes_jsonl_record(self, tmp_path):
        logger = CorrectionLogger(log_dir=str(tmp_path))
        logger.log(
            asr_text="raw asr",
            enhanced_text="enhanced",
            final_text="user corrected",
            enhance_mode="proofread",
        )

        log_file = tmp_path / "corrections.jsonl"
        assert log_file.exists()

        record = json.loads(log_file.read_text().strip())
        assert record["asr_text"] == "raw asr"
        assert record["enhanced_text"] == "enhanced"
        assert record["final_text"] == "user corrected"
        assert record["enhance_mode"] == "proofread"
        assert "timestamp" in record

    def test_log_appends_multiple_records(self, tmp_path):
        logger = CorrectionLogger(log_dir=str(tmp_path))
        logger.log("a1", "e1", "f1", "proofread")
        logger.log("a2", "e2", "f2", "rewrite")

        log_file = tmp_path / "corrections.jsonl"
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2

        r1 = json.loads(lines[0])
        r2 = json.loads(lines[1])
        assert r1["asr_text"] == "a1"
        assert r2["asr_text"] == "a2"
        assert r2["enhance_mode"] == "rewrite"

    def test_log_creates_directory_if_missing(self, tmp_path):
        nested = tmp_path / "sub" / "dir"
        logger = CorrectionLogger(log_dir=str(nested))
        logger.log("a", "e", "f", "proofread")

        assert (nested / "corrections.jsonl").exists()

    def test_log_record_fields_complete(self, tmp_path):
        logger = CorrectionLogger(log_dir=str(tmp_path))
        logger.log("asr", "enhanced", "final", "translate")

        log_file = tmp_path / "corrections.jsonl"
        record = json.loads(log_file.read_text().strip())
        expected_keys = {"timestamp", "asr_text", "enhanced_text", "final_text", "enhance_mode"}
        assert set(record.keys()) == expected_keys

    def test_log_handles_unicode(self, tmp_path):
        logger = CorrectionLogger(log_dir=str(tmp_path))
        logger.log("你好世界", "Hello World", "你好，世界", "translate")

        log_file = tmp_path / "corrections.jsonl"
        record = json.loads(log_file.read_text().strip())
        assert record["asr_text"] == "你好世界"
        assert record["final_text"] == "你好，世界"

    def test_log_expands_user_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        logger = CorrectionLogger(log_dir="~/test_logs")
        logger.log("a", "e", "f", "proofread")

        assert (tmp_path / "test_logs" / "corrections.jsonl").exists()

    def test_count_empty(self, tmp_path):
        logger = CorrectionLogger(log_dir=str(tmp_path))
        assert logger.count() == 0

    def test_count_no_file(self, tmp_path):
        logger = CorrectionLogger(log_dir=str(tmp_path / "nonexistent"))
        assert logger.count() == 0

    def test_count_multiple_records(self, tmp_path):
        logger = CorrectionLogger(log_dir=str(tmp_path))
        for i in range(5):
            logger.log(f"asr_{i}", f"enh_{i}", f"final_{i}", "proofread")
        assert logger.count() == 5
