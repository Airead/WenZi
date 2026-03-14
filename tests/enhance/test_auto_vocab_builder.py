"""Tests for AutoVocabBuilder."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from voicetext.enhance.auto_vocab_builder import AutoVocabBuilder


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
    @patch("voicetext.enhance.auto_vocab_builder.send_notification")
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
            "voicetext.enhance.vocabulary_builder.VocabularyBuilder",
            return_value=mock_builder,
        ):
            builder._build()

        mock_enhancer.vocab_index.reload.assert_called_once()
        mock_notify.assert_called_once()
        assert builder._building is False

    @patch("voicetext.enhance.auto_vocab_builder.send_notification")
    def test_build_success_no_new_entries(self, mock_notify, config):
        """Test that no notification is sent when there are no new entries."""
        async def fake_build(**kwargs):
            return {"new_entries": 0, "total_entries": 10}

        mock_builder = MagicMock()
        mock_builder.build.return_value = fake_build()

        builder = AutoVocabBuilder(config, enabled=True, threshold=1)
        builder._building = True

        with patch(
            "voicetext.enhance.vocabulary_builder.VocabularyBuilder",
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
            "voicetext.enhance.vocabulary_builder.VocabularyBuilder",
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
    @patch("voicetext.enhance.auto_vocab_builder.send_notification")
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
            "voicetext.enhance.vocabulary_builder.VocabularyBuilder",
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
            "voicetext.enhance.vocabulary_builder.VocabularyBuilder",
            side_effect=Exception("fail"),
        ):
            builder._build()

        callback.assert_not_called()
