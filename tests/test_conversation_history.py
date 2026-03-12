"""Tests for conversation history module."""

from __future__ import annotations

import json
import os

import pytest

from voicetext.conversation_history import ConversationHistory


@pytest.fixture
def history_dir(tmp_path):
    """Return a temporary directory for conversation history."""
    return str(tmp_path)


@pytest.fixture
def history(history_dir):
    """Return a ConversationHistory instance using a temp directory."""
    return ConversationHistory(config_dir=history_dir)


class TestConversationHistoryLog:
    def test_log_creates_file(self, history, history_dir):
        history.log(
            asr_text="hello",
            enhanced_text="Hello.",
            final_text="Hello.",
            enhance_mode="proofread",
            preview_enabled=True,
        )
        path = os.path.join(history_dir, "conversation_history.jsonl")
        assert os.path.exists(path)

    def test_log_appends_records(self, history, history_dir):
        history.log("a", "A", "A", "proofread", True)
        history.log("b", "B", "B", "proofread", True)

        path = os.path.join(history_dir, "conversation_history.jsonl")
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 2

    def test_log_creates_directory(self, tmp_path):
        nested = str(tmp_path / "nested" / "dir")
        h = ConversationHistory(config_dir=nested)
        h.log("hello", None, "hello", "off", False)
        assert os.path.exists(os.path.join(nested, "conversation_history.jsonl"))

    def test_log_unicode(self, history, history_dir):
        history.log("你好世界", "你好，世界。", "你好，世界。", "proofread", True)

        path = os.path.join(history_dir, "conversation_history.jsonl")
        with open(path, "r", encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record["asr_text"] == "你好世界"
        assert record["enhanced_text"] == "你好，世界。"

    def test_log_null_enhanced_text(self, history, history_dir):
        history.log("hello", None, "hello", "off", False)

        path = os.path.join(history_dir, "conversation_history.jsonl")
        with open(path, "r", encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record["enhanced_text"] is None

    def test_log_record_fields(self, history, history_dir):
        history.log("raw", "enhanced", "final", "proofread", True)

        path = os.path.join(history_dir, "conversation_history.jsonl")
        with open(path, "r", encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record["asr_text"] == "raw"
        assert record["enhanced_text"] == "enhanced"
        assert record["final_text"] == "final"
        assert record["enhance_mode"] == "proofread"
        assert record["preview_enabled"] is True
        assert "timestamp" in record

    def test_log_preview_disabled(self, history, history_dir):
        history.log("raw", None, "raw", "off", False)

        path = os.path.join(history_dir, "conversation_history.jsonl")
        with open(path, "r", encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record["preview_enabled"] is False


class TestConversationHistoryGetRecent:
    def test_returns_only_preview_enabled(self, history):
        history.log("a", None, "a", "off", False)
        history.log("b", "B", "B", "proofread", True)
        history.log("c", None, "c", "off", False)
        history.log("d", "D", "D", "proofread", True)

        results = history.get_recent(max_entries=10)
        assert len(results) == 2
        assert results[0]["asr_text"] == "b"
        assert results[1]["asr_text"] == "d"

    def test_returns_most_recent_n(self, history):
        for i in range(5):
            history.log(f"text{i}", f"Text{i}", f"Text{i}", "proofread", True)

        results = history.get_recent(n=3)
        assert len(results) == 3
        assert results[0]["asr_text"] == "text2"
        assert results[2]["asr_text"] == "text4"

    def test_returns_fewer_than_n_when_not_enough(self, history):
        history.log("a", "A", "A", "proofread", True)

        results = history.get_recent(n=5)
        assert len(results) == 1

    def test_returns_empty_for_empty_file(self, history, history_dir):
        # Create empty file
        path = os.path.join(history_dir, "conversation_history.jsonl")
        os.makedirs(history_dir, exist_ok=True)
        with open(path, "w") as f:
            pass

        results = history.get_recent()
        assert results == []

    def test_returns_empty_when_file_not_exists(self, history):
        results = history.get_recent()
        assert results == []

    def test_default_max_entries(self, history):
        for i in range(15):
            history.log(f"text{i}", f"Text{i}", f"Text{i}", "proofread", True)

        results = history.get_recent()
        assert len(results) == 10  # default max_entries

    def test_skips_malformed_lines(self, history, history_dir):
        path = os.path.join(history_dir, "conversation_history.jsonl")
        os.makedirs(history_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"asr_text": "good", "preview_enabled": true}\n')
            f.write("not json\n")
            f.write('{"asr_text": "also good", "preview_enabled": true}\n')

        results = history.get_recent()
        assert len(results) == 2

    def test_oldest_first_order(self, history):
        history.log("first", "First", "First", "proofread", True)
        history.log("second", "Second", "Second", "proofread", True)
        history.log("third", "Third", "Third", "proofread", True)

        results = history.get_recent(n=3)
        assert results[0]["asr_text"] == "first"
        assert results[2]["asr_text"] == "third"


class TestConversationHistoryFormatForPrompt:
    def test_format_same_asr_and_final(self, history):
        entries = [
            {"asr_text": "你好世界", "final_text": "你好世界"},
        ]
        result = history.format_for_prompt(entries)
        assert "- 你好世界" in result
        assert "你好世界 →" not in result

    def test_format_different_asr_and_final(self, history):
        entries = [
            {"asr_text": "你好世界", "final_text": "你好，世界！"},
        ]
        result = history.format_for_prompt(entries)
        assert "你好世界 → 你好，世界！" in result

    def test_format_empty_list(self, history):
        result = history.format_for_prompt([])
        assert result == ""

    def test_format_mixed_entries(self, history):
        entries = [
            {"asr_text": "same", "final_text": "same"},
            {"asr_text": "平平", "final_text": "萍萍"},
        ]
        result = history.format_for_prompt(entries)
        assert "- same" in result
        assert "平平 → 萍萍" in result


class TestConversationHistoryCount:
    def test_count_no_file(self, history):
        assert history.count() == 0

    def test_count_empty_file(self, history, history_dir):
        with open(os.path.join(history_dir, "conversation_history.jsonl"), "w") as f:
            f.write("")
        assert history.count() == 0

    def test_count_multiple(self, history):
        for i in range(4):
            history.log(f"asr_{i}", f"enh_{i}", f"final_{i}", "proofread", True)
        assert history.count() == 4
