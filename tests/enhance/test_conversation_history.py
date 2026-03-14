"""Tests for conversation history module."""

from __future__ import annotations

import json
import os

import pytest

from voicetext.enhance.conversation_history import ConversationHistory


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
        with open(path, "w"):
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

    def test_skips_long_final_text(self, history):
        """Records with final_text exceeding the max length should be excluded."""
        history.log("short", "Short", "Short", "proofread", True)
        history.log("long", "Long", "x" * 501, "proofread", True)
        history.log("short2", "Short2", "Short2", "proofread", True)

        results = history.get_recent(max_entries=10)
        assert len(results) == 2
        assert results[0]["asr_text"] == "short"
        assert results[1]["asr_text"] == "short2"

    def test_long_text_at_boundary(self, history):
        """Text exactly at the limit should be included, one over should be excluded."""
        history.log("exact", "Exact", "x" * 500, "proofread", True)
        history.log("over", "Over", "x" * 501, "proofread", True)

        results = history.get_recent(max_entries=10)
        assert len(results) == 1
        assert results[0]["asr_text"] == "exact"

    def test_skips_long_text_still_fills_n(self, history):
        """Skipped long entries should not count toward the N limit."""
        history.log("a", "A", "A", "proofread", True)
        history.log("long1", "L1", "x" * 600, "proofread", True)
        history.log("b", "B", "B", "proofread", True)
        history.log("long2", "L2", "x" * 700, "proofread", True)
        history.log("c", "C", "C", "proofread", True)

        results = history.get_recent(n=3)
        assert len(results) == 3
        texts = [r["asr_text"] for r in results]
        assert texts == ["a", "b", "c"]


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

    def test_format_replaces_newlines_with_return_symbol(self, history):
        entries = [
            {"asr_text": "line1\nline2", "final_text": "line1\nline2"},
        ]
        result = history.format_for_prompt(entries)
        assert "\n" not in result.split("\n")[-2]  # the entry line itself
        assert "line1\u23celine2" in result

    def test_format_newlines_in_both_asr_and_final(self, history):
        entries = [
            {"asr_text": "a\nb", "final_text": "a\nc"},
        ]
        result = history.format_for_prompt(entries)
        assert "a\u23ceb → a\u23cec" in result

    def test_format_respects_max_chars(self, history):
        """Output should not exceed max_chars."""
        entries = [
            {"asr_text": f"text{i}", "final_text": f"text{i}"}
            for i in range(50)
        ]
        result = history.format_for_prompt(entries, max_chars=300)
        assert len(result) <= 300
        # Should still have header and footer
        assert result.startswith("---")
        assert result.endswith("---")

    def test_format_truncates_older_entries(self, history):
        """Oldest entries should be dropped when budget is exceeded."""
        entries = [
            {"asr_text": f"entry_{i:03d}", "final_text": f"entry_{i:03d}"}
            for i in range(20)
        ]
        # Use a budget that fits header + a few entries but not all 20
        result = history.format_for_prompt(entries, max_chars=250)
        # Newest entries should be present
        assert "entry_019" in result
        # Some older entries should be dropped
        assert "entry_000" not in result

    def test_format_default_max_chars(self, history):
        """Default max_chars should use _MAX_PROMPT_CHARS."""
        entries = [
            {"asr_text": "x" * 100, "final_text": "x" * 100}
            for _ in range(50)
        ]
        result = history.format_for_prompt(entries)
        assert len(result) <= history._MAX_PROMPT_CHARS

    def test_format_single_entry_exceeding_budget_still_included(self, history):
        """A single entry should still be included even if it exceeds budget."""
        entries = [
            {"asr_text": "x" * 200, "final_text": "x" * 200},
        ]
        result = history.format_for_prompt(entries, max_chars=100)
        assert "x" * 200 in result

    def test_format_explicit_max_chars_zero_uses_default(self, history):
        """max_chars=0 should fall back to the class default."""
        entries = [
            {"asr_text": f"e{i}", "final_text": f"e{i}"}
            for i in range(50)
        ]
        result = history.format_for_prompt(entries, max_chars=0)
        assert len(result) <= history._MAX_PROMPT_CHARS


class TestConversationHistoryGetAll:
    def test_get_all_returns_newest_first(self, history):
        history.log("first", "First", "First", "proofread", True)
        history.log("second", "Second", "Second", "proofread", True)
        history.log("third", "Third", "Third", "proofread", True)

        results = history.get_all()
        assert len(results) == 3
        assert results[0]["asr_text"] == "third"
        assert results[2]["asr_text"] == "first"

    def test_get_all_includes_all_records(self, history):
        """get_all should include records regardless of preview_enabled or text length."""
        history.log("a", None, "a", "off", False)
        history.log("b", "B", "x" * 600, "proofread", True)
        history.log("c", "C", "C", "proofread", True)

        results = history.get_all()
        assert len(results) == 3

    def test_get_all_respects_limit(self, history):
        for i in range(10):
            history.log(f"text{i}", f"Text{i}", f"Text{i}", "proofread", True)

        results = history.get_all(limit=3)
        assert len(results) == 3
        # Should be newest first
        assert results[0]["asr_text"] == "text9"
        assert results[2]["asr_text"] == "text7"

    def test_get_all_empty_file(self, history):
        results = history.get_all()
        assert results == []

    def test_get_all_skips_malformed_lines(self, history, history_dir):
        path = os.path.join(history_dir, "conversation_history.jsonl")
        os.makedirs(history_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"asr_text": "good"}\n')
            f.write("not json\n")
            f.write('{"asr_text": "also good"}\n')

        results = history.get_all()
        assert len(results) == 2


class TestConversationHistoryUpdateFinalText:
    def test_update_final_text(self, history, history_dir):
        history.log("hello", "Hello.", "Hello.", "proofread", True)

        # Get the timestamp
        path = os.path.join(history_dir, "conversation_history.jsonl")
        with open(path, "r", encoding="utf-8") as f:
            record = json.loads(f.readline())
        ts = record["timestamp"]

        result = history.update_final_text(ts, "Hello World!")
        assert result is True

        # Verify the update
        with open(path, "r", encoding="utf-8") as f:
            updated = json.loads(f.readline())
        assert updated["final_text"] == "Hello World!"
        assert "edited_at" in updated

    def test_update_final_text_not_found(self, history):
        history.log("hello", "Hello.", "Hello.", "proofread", True)

        result = history.update_final_text("nonexistent-timestamp", "new text")
        assert result is False

    def test_update_final_text_no_file(self, history):
        result = history.update_final_text("any-timestamp", "new text")
        assert result is False

    def test_update_final_text_preserves_other_records(self, history, history_dir):
        history.log("first", "First", "First", "proofread", True)
        history.log("second", "Second", "Second", "proofread", True)

        path = os.path.join(history_dir, "conversation_history.jsonl")
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        ts2 = json.loads(lines[1])["timestamp"]

        history.update_final_text(ts2, "Modified")

        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        r1 = json.loads(lines[0])
        r2 = json.loads(lines[1])
        assert r1["final_text"] == "First"
        assert r2["final_text"] == "Modified"


class TestConversationHistorySearch:
    def test_search_basic(self, history):
        history.log("hello world", None, "hello world", "off", True)
        history.log("goodbye", None, "goodbye", "off", True)

        results = history.search("hello")
        assert len(results) == 1
        assert results[0]["asr_text"] == "hello world"

    def test_search_case_insensitive(self, history):
        history.log("Hello World", None, "Hello World", "off", True)

        results = history.search("hello world")
        assert len(results) == 1

    def test_search_chinese(self, history):
        history.log("你好世界", "你好，世界", "你好，世界", "proofread", True)
        history.log("goodbye", None, "goodbye", "off", True)

        results = history.search("你好")
        assert len(results) == 1
        assert results[0]["asr_text"] == "你好世界"

    def test_search_empty_result(self, history):
        history.log("hello", None, "hello", "off", True)

        results = history.search("nonexistent")
        assert results == []

    def test_search_newest_first(self, history):
        history.log("first hello", None, "first hello", "off", True)
        history.log("second hello", None, "second hello", "off", True)

        results = history.search("hello")
        assert len(results) == 2
        assert results[0]["asr_text"] == "second hello"
        assert results[1]["asr_text"] == "first hello"

    def test_search_in_enhanced_text(self, history):
        history.log("asr", "enhanced special", "final", "proofread", True)

        results = history.search("special")
        assert len(results) == 1

    def test_search_no_file(self, history):
        results = history.search("anything")
        assert results == []

    def test_search_respects_limit(self, history):
        for i in range(10):
            history.log(f"hello {i}", None, f"hello {i}", "off", True)

        results = history.search("hello", limit=3)
        assert len(results) == 3


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


class TestUserCorrectedField:
    def test_log_user_corrected_true(self, history, history_dir):
        history.log("raw", "enhanced", "corrected", "proofread", True, user_corrected=True)

        path = os.path.join(history_dir, "conversation_history.jsonl")
        with open(path, "r", encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record["user_corrected"] is True

    def test_log_user_corrected_false(self, history, history_dir):
        history.log("raw", "enhanced", "enhanced", "proofread", True, user_corrected=False)

        path = os.path.join(history_dir, "conversation_history.jsonl")
        with open(path, "r", encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record["user_corrected"] is False

    def test_log_user_corrected_default(self, history, history_dir):
        history.log("raw", "enhanced", "enhanced", "proofread", True)

        path = os.path.join(history_dir, "conversation_history.jsonl")
        with open(path, "r", encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record["user_corrected"] is False


class TestCorrectionCount:
    def test_no_file(self, history):
        assert history.correction_count() == 0

    def test_no_corrections(self, history):
        history.log("hello", "Hello.", "Hello.", "proofread", True, user_corrected=False)
        assert history.correction_count() == 0

    def test_with_corrections(self, history):
        history.log("hello", "Hello.", "Hello.", "proofread", True, user_corrected=False)
        history.log("raw", "enhanced", "corrected", "proofread", True, user_corrected=True)
        history.log("raw2", "enh2", "enh2", "proofread", True, user_corrected=False)
        assert history.correction_count() == 1

    def test_legacy_records_inferred(self, history, history_dir):
        """Legacy records without user_corrected field should be inferred."""
        path = os.path.join(history_dir, "conversation_history.jsonl")
        os.makedirs(history_dir, exist_ok=True)
        # Legacy record: enhanced != final → correction
        legacy_corrected = json.dumps({
            "timestamp": "2026-01-01T00:00:00+00:00",
            "asr_text": "raw",
            "enhanced_text": "enhanced",
            "final_text": "user corrected",
            "enhance_mode": "proofread",
        })
        # Legacy record: enhanced == final → not a correction
        legacy_not_corrected = json.dumps({
            "timestamp": "2026-01-01T01:00:00+00:00",
            "asr_text": "raw",
            "enhanced_text": "same",
            "final_text": "same",
            "enhance_mode": "proofread",
        })
        # Legacy record: enhanced is None → not a correction
        legacy_no_enhance = json.dumps({
            "timestamp": "2026-01-01T02:00:00+00:00",
            "asr_text": "raw",
            "enhanced_text": None,
            "final_text": "raw",
            "enhance_mode": "off",
        })
        with open(path, "w", encoding="utf-8") as f:
            f.write(legacy_corrected + "\n")
            f.write(legacy_not_corrected + "\n")
            f.write(legacy_no_enhance + "\n")

        assert history.correction_count() == 1


class TestGetCorrections:
    def test_returns_corrected_records(self, history):
        history.log("a", "A", "A", "proofread", True, user_corrected=False)
        history.log("b", "B", "B-corrected", "proofread", True, user_corrected=True)
        history.log("c", "C", "C", "proofread", True, user_corrected=False)
        history.log("d", "D", "D-corrected", "proofread", True, user_corrected=True)

        results = history.get_corrections()
        assert len(results) == 2
        assert results[0]["final_text"] == "B-corrected"
        assert results[1]["final_text"] == "D-corrected"

    def test_returns_empty_when_no_corrections(self, history):
        history.log("a", "A", "A", "proofread", True, user_corrected=False)
        assert history.get_corrections() == []

    def test_returns_empty_when_no_file(self, history):
        assert history.get_corrections() == []

    def test_since_filter(self, history, history_dir):
        path = os.path.join(history_dir, "conversation_history.jsonl")
        os.makedirs(history_dir, exist_ok=True)
        records = [
            {"timestamp": "2026-01-01T10:00:00+00:00", "asr_text": "a",
             "enhanced_text": "A", "final_text": "A-fix", "user_corrected": True},
            {"timestamp": "2026-01-01T11:00:00+00:00", "asr_text": "b",
             "enhanced_text": "B", "final_text": "B-fix", "user_corrected": True},
            {"timestamp": "2026-01-01T12:00:00+00:00", "asr_text": "c",
             "enhanced_text": "C", "final_text": "C-fix", "user_corrected": True},
        ]
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        results = history.get_corrections(since="2026-01-01T10:00:00+00:00")
        assert len(results) == 2
        assert results[0]["asr_text"] == "b"

    def test_legacy_inferred_corrections(self, history, history_dir):
        """Legacy records without user_corrected should be inferred."""
        path = os.path.join(history_dir, "conversation_history.jsonl")
        os.makedirs(history_dir, exist_ok=True)
        records = [
            {"timestamp": "2026-01-01T10:00:00+00:00", "asr_text": "raw",
             "enhanced_text": "enhanced", "final_text": "corrected"},
            {"timestamp": "2026-01-01T11:00:00+00:00", "asr_text": "raw",
             "enhanced_text": "same", "final_text": "same"},
        ]
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        results = history.get_corrections()
        assert len(results) == 1
        assert results[0]["final_text"] == "corrected"


class TestIsCorrected:
    def test_explicit_true(self):
        from voicetext.enhance.conversation_history import ConversationHistory
        assert ConversationHistory._is_corrected({"user_corrected": True}) is True

    def test_explicit_false(self):
        from voicetext.enhance.conversation_history import ConversationHistory
        assert ConversationHistory._is_corrected({"user_corrected": False}) is False

    def test_inferred_corrected(self):
        from voicetext.enhance.conversation_history import ConversationHistory
        record = {"enhanced_text": "enhanced", "final_text": "different"}
        assert ConversationHistory._is_corrected(record) is True

    def test_inferred_not_corrected(self):
        from voicetext.enhance.conversation_history import ConversationHistory
        record = {"enhanced_text": "same", "final_text": "same"}
        assert ConversationHistory._is_corrected(record) is False

    def test_inferred_no_enhance(self):
        from voicetext.enhance.conversation_history import ConversationHistory
        record = {"enhanced_text": None, "final_text": "text"}
        assert ConversationHistory._is_corrected(record) is False
