"""Tests for AutoVocabBuilder."""

from __future__ import annotations

import json

from unittest.mock import MagicMock, patch

import pytest

from wenzi.enhance.auto_vocab_builder import AutoVocabBuilder
from wenzi.enhance.conversation_history import ConversationHistory


@pytest.fixture
def config():
    return {
        "ai_enhance": {
            "default_provider": "ollama",
            "default_model": "qwen2.5:7b",
            "providers": {
                "ollama": {
                    "base_url": "http://localhost:11434/v1",
                    "api_key": "ollama",
                    "models": ["qwen2.5:7b"],
                },
            },
            "vocabulary": {
                "enabled": True,
                "build_timeout": 600,
            },
        },
    }


class TestCounterIncrement:
    def test_counter_increments(self, config):
        builder = AutoVocabBuilder(config, enabled=True, threshold=5)
        builder.on_correction_logged()
        assert builder._counter == 1

    def test_counter_increments_multiple(self, config):
        builder = AutoVocabBuilder(config, enabled=True, threshold=10)
        for _ in range(5):
            builder.on_correction_logged()
        assert builder._counter == 5


class TestThresholdTrigger:
    @patch.object(AutoVocabBuilder, "_run_silent_build")
    def test_triggers_at_threshold(self, mock_build, config):
        builder = AutoVocabBuilder(config, enabled=True, threshold=3)
        builder.on_correction_logged()
        builder.on_correction_logged()
        mock_build.assert_not_called()
        builder.on_correction_logged()
        mock_build.assert_called_once()

    @patch.object(AutoVocabBuilder, "_run_silent_build")
    def test_no_trigger_below_threshold(self, mock_build, config):
        builder = AutoVocabBuilder(config, enabled=True, threshold=5)
        for _ in range(4):
            builder.on_correction_logged()
        mock_build.assert_not_called()

    @patch.object(AutoVocabBuilder, "_run_silent_build")
    def test_counter_resets_on_trigger(self, mock_build, config):
        builder = AutoVocabBuilder(config, enabled=True, threshold=3)
        for _ in range(3):
            builder.on_correction_logged()
        assert builder._counter == 0

    @patch.object(AutoVocabBuilder, "_run_silent_build")
    def test_disabled_does_not_trigger(self, mock_build, config):
        builder = AutoVocabBuilder(config, enabled=False, threshold=2)
        for _ in range(5):
            builder.on_correction_logged()
        mock_build.assert_not_called()
        assert builder._counter == 0


class TestBuildingFlag:
    @patch.object(AutoVocabBuilder, "_run_silent_build")
    def test_no_duplicate_trigger_while_building(self, mock_build, config):
        builder = AutoVocabBuilder(config, enabled=True, threshold=2)
        # First trigger
        builder.on_correction_logged()
        builder.on_correction_logged()
        assert builder._building is True
        mock_build.assert_called_once()

        # New corrections during build should count but not trigger
        builder.on_correction_logged()
        builder.on_correction_logged()
        mock_build.assert_called_once()  # Still only one call
        assert builder._counter == 2  # Counted but not triggered

    @patch.object(AutoVocabBuilder, "_run_silent_build")
    def test_is_building(self, mock_build, config):
        builder = AutoVocabBuilder(config, enabled=True, threshold=1)
        assert builder.is_building() is False
        builder.on_correction_logged()
        assert builder.is_building() is True

    @patch.object(AutoVocabBuilder, "_run_silent_build")
    def test_corrections_accumulate_during_build(self, mock_build, config):
        builder = AutoVocabBuilder(config, enabled=True, threshold=3)
        # Trigger first build
        for _ in range(3):
            builder.on_correction_logged()
        assert builder._building is True
        assert builder._counter == 0

        # Corrections during build
        builder.on_correction_logged()
        builder.on_correction_logged()
        assert builder._counter == 2


class TestBuildExecution:
    @patch("wenzi.enhance.auto_vocab_builder.send_notification")
    def test_build_success_with_new_entries(self, mock_notify, config):
        """Test that a successful build reloads index and sends notification."""
        async def fake_build(**kwargs):
            return {"new_entries": 5, "total_entries": 20}

        mock_builder = MagicMock()
        mock_builder.build.return_value = fake_build()

        mock_enhancer = MagicMock()
        mock_enhancer.vocab_index = MagicMock()

        builder = AutoVocabBuilder(config, enabled=True, threshold=1)
        builder.set_enhancer(mock_enhancer)
        builder._building = True

        with patch(
            "wenzi.enhance.vocabulary_builder.VocabularyBuilder",
            return_value=mock_builder,
        ):
            builder._build()

        mock_enhancer.vocab_index.reload.assert_called_once()
        mock_notify.assert_called_once()
        assert builder._building is False

    @patch("wenzi.enhance.auto_vocab_builder.send_notification")
    def test_build_success_no_new_entries(self, mock_notify, config):
        """Test that no notification is sent when there are no new entries."""
        async def fake_build(**kwargs):
            return {"new_entries": 0, "total_entries": 10}

        mock_builder = MagicMock()
        mock_builder.build.return_value = fake_build()

        builder = AutoVocabBuilder(config, enabled=True, threshold=1)
        builder._building = True

        with patch(
            "wenzi.enhance.vocabulary_builder.VocabularyBuilder",
            return_value=mock_builder,
        ):
            builder._build()

        mock_notify.assert_not_called()
        assert builder._building is False

    def test_build_failure_clears_building_flag(self, config):
        """Test that building flag is cleared even when build fails."""
        builder = AutoVocabBuilder(config, enabled=True, threshold=1)
        builder._building = True

        with patch(
            "wenzi.enhance.vocabulary_builder.VocabularyBuilder",
            side_effect=Exception("LLM unavailable"),
        ):
            builder._build()

        assert builder._building is False


class TestSetEnhancer:
    def test_set_enhancer(self, config):
        builder = AutoVocabBuilder(config)
        mock_enhancer = MagicMock()
        builder.set_enhancer(mock_enhancer)
        assert builder._enhancer is mock_enhancer


class TestOnBuildDoneCallback:
    @patch("wenzi.enhance.auto_vocab_builder.send_notification")
    def test_on_build_done_called(self, mock_notify, config):
        """Test that on_build_done callback is invoked after successful build."""
        async def fake_build(**kwargs):
            return {"new_entries": 3, "total_entries": 15}

        mock_builder = MagicMock()
        mock_builder.build.return_value = fake_build()

        callback = MagicMock()
        builder = AutoVocabBuilder(config, enabled=True, threshold=1, on_build_done=callback)
        builder._building = True

        with patch(
            "wenzi.enhance.vocabulary_builder.VocabularyBuilder",
            return_value=mock_builder,
        ):
            builder._build()

        callback.assert_called_once()

    def test_on_build_done_not_called_on_failure(self, config):
        """Test that on_build_done is not called when build fails."""
        callback = MagicMock()
        builder = AutoVocabBuilder(config, enabled=True, threshold=1, on_build_done=callback)
        builder._building = True

        with patch(
            "wenzi.enhance.vocabulary_builder.VocabularyBuilder",
            side_effect=Exception("fail"),
        ):
            builder._build()

        callback.assert_not_called()


class TestInitCounterFromDisk:
    def test_counter_initialized_from_pending_corrections(self, config, tmp_path):
        """Counter should reflect unprocessed corrections on disk."""
        # Write vocabulary.json with a last_processed_timestamp
        vocab_path = tmp_path / "vocabulary.json"
        vocab_path.write_text(
            json.dumps({
                "last_processed_timestamp": "2026-01-01T10:00:00+00:00",
                "entries": [],
            }),
            encoding="utf-8",
        )

        # Write conversation history with corrections after the timestamp
        ch = ConversationHistory(config_dir=str(tmp_path))
        ch.log("old asr", "old enhanced", "old final", "proofread", True, user_corrected=True)
        # Manually backdate the record to before last_processed_timestamp
        with open(tmp_path / "conversation_history.jsonl", "r") as f:
            lines = f.readlines()
        old_record = json.loads(lines[0])
        old_record["timestamp"] = "2026-01-01T09:00:00+00:00"
        with open(tmp_path / "conversation_history.jsonl", "w") as f:
            f.write(json.dumps(old_record, ensure_ascii=False) + "\n")

        # Add 3 corrections after the timestamp
        for i in range(3):
            ch.log(f"asr{i}", f"enhanced{i}", f"final{i}", "proofread", True, user_corrected=True)

        builder = AutoVocabBuilder(
            config, enabled=True, threshold=10,
            conversation_history=ch, config_dir=str(tmp_path),
        )
        assert builder._counter == 3

    def test_counter_zero_when_no_vocab_file(self, config, tmp_path):
        """Without vocabulary.json, all corrections count as pending."""
        ch = ConversationHistory(config_dir=str(tmp_path))
        for i in range(5):
            ch.log(f"asr{i}", f"enhanced{i}", f"final{i}", "proofread", True, user_corrected=True)

        builder = AutoVocabBuilder(
            config, enabled=True, threshold=10,
            conversation_history=ch, config_dir=str(tmp_path),
        )
        assert builder._counter == 5

    def test_counter_zero_when_no_corrections(self, config, tmp_path):
        """Counter stays 0 when there are no corrections since last build."""
        vocab_path = tmp_path / "vocabulary.json"
        vocab_path.write_text(
            json.dumps({
                "last_processed_timestamp": "2099-01-01T00:00:00+00:00",
                "entries": [],
            }),
            encoding="utf-8",
        )

        ch = ConversationHistory(config_dir=str(tmp_path))
        ch.log("asr", "enhanced", "final", "proofread", True, user_corrected=True)

        builder = AutoVocabBuilder(
            config, enabled=True, threshold=10,
            conversation_history=ch, config_dir=str(tmp_path),
        )
        assert builder._counter == 0

    def test_counter_zero_when_disabled(self, config, tmp_path):
        """Disabled builder should not init counter from disk."""
        ch = ConversationHistory(config_dir=str(tmp_path))
        for i in range(5):
            ch.log(f"asr{i}", f"enhanced{i}", f"final{i}", "proofread", True, user_corrected=True)

        builder = AutoVocabBuilder(
            config, enabled=False, threshold=10,
            conversation_history=ch, config_dir=str(tmp_path),
        )
        assert builder._counter == 0

    def test_counter_zero_when_no_conversation_history(self, config, tmp_path):
        """Without conversation_history, counter stays 0."""
        builder = AutoVocabBuilder(
            config, enabled=True, threshold=10,
            conversation_history=None, config_dir=str(tmp_path),
        )
        assert builder._counter == 0

    @patch.object(AutoVocabBuilder, "_run_silent_build")
    def test_next_correction_triggers_build_after_init(self, mock_build, config, tmp_path):
        """When disk counter is threshold-1, one more correction triggers build."""
        ch = ConversationHistory(config_dir=str(tmp_path))
        for i in range(4):
            ch.log(f"asr{i}", f"enhanced{i}", f"final{i}", "proofread", True, user_corrected=True)

        builder = AutoVocabBuilder(
            config, enabled=True, threshold=5,
            conversation_history=ch, config_dir=str(tmp_path),
        )
        assert builder._counter == 4

        mock_build.assert_not_called()
        builder.on_correction_logged()
        mock_build.assert_called_once()
        assert builder._counter == 0

    @patch.object(AutoVocabBuilder, "_run_silent_build")
    def test_exceeding_threshold_triggers_on_next_correction(self, mock_build, config, tmp_path):
        """When disk corrections already exceed threshold, next correction triggers."""
        ch = ConversationHistory(config_dir=str(tmp_path))
        for i in range(20):
            ch.log(f"asr{i}", f"enhanced{i}", f"final{i}", "proofread", True, user_corrected=True)

        builder = AutoVocabBuilder(
            config, enabled=True, threshold=5,
            conversation_history=ch, config_dir=str(tmp_path),
        )
        assert builder._counter == 20

        builder.on_correction_logged()
        mock_build.assert_called_once()


class TestStatusUpdate:
    @patch("wenzi.enhance.auto_vocab_builder.send_notification")
    def test_status_updates_during_build(self, mock_notify, config):
        """on_status_update should be called with progress and then empty to restore."""
        # Simulate a build that streams "term|cat|var|ctx\nPython|tech||lang\n"
        async def fake_build(callbacks=None, **kwargs):
            if callbacks and callbacks.on_batch_start:
                callbacks.on_batch_start(1, 1)
            if callbacks and callbacks.on_stream_chunk:
                callbacks.on_stream_chunk("term|cat|var|ctx\n")
                callbacks.on_stream_chunk("Python|tech||lang\n")
                callbacks.on_stream_chunk("Java|tech||lang\n")
            return {"new_entries": 2, "total_entries": 2}

        mock_builder = MagicMock()
        mock_builder.build = fake_build

        status_calls = []
        builder = AutoVocabBuilder(
            config, enabled=True, threshold=1,
            on_status_update=lambda s: status_calls.append(s),
        )
        builder._building = True

        with patch(
            "wenzi.enhance.vocabulary_builder.VocabularyBuilder",
            return_value=mock_builder,
        ):
            builder._build()

        # Should have: "VB ..." (start), "VB +1/0" (Python), "VB +2/0" (Java), "" (restore)
        assert status_calls[0] == "VB ..."
        assert "VB 1/0" in status_calls
        assert "VB 2/0" in status_calls
        assert status_calls[-1] == ""

    @patch("wenzi.enhance.auto_vocab_builder.send_notification")
    def test_status_restored_on_failure(self, mock_notify, config):
        """on_status_update should send empty string even when build fails."""
        status_calls = []
        builder = AutoVocabBuilder(
            config, enabled=True, threshold=1,
            on_status_update=lambda s: status_calls.append(s),
        )
        builder._building = True

        with patch(
            "wenzi.enhance.vocabulary_builder.VocabularyBuilder",
            side_effect=Exception("fail"),
        ):
            builder._build()

        # Should still restore status
        assert status_calls[-1] == ""

    def test_no_status_update_without_callback(self, config):
        """Build should work fine without on_status_update callback."""
        async def fake_build(**kwargs):
            return {"new_entries": 0, "total_entries": 0}

        mock_builder = MagicMock()
        mock_builder.build = fake_build

        builder = AutoVocabBuilder(config, enabled=True, threshold=1)
        builder._building = True

        with patch(
            "wenzi.enhance.auto_vocab_builder.send_notification",
        ), patch(
            "wenzi.enhance.vocabulary_builder.VocabularyBuilder",
            return_value=mock_builder,
        ):
            builder._build()

        assert builder._building is False
