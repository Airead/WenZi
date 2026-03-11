"""Tests for the vocabulary builder module."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voicetext.vocabulary_builder import VocabularyBuilder


def _make_config(**overrides):
    """Helper to create a valid builder config."""
    cfg = {
        "default_provider": "ollama",
        "default_model": "qwen2.5:7b",
        "providers": {
            "ollama": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "ollama",
                "models": ["qwen2.5:7b"],
            },
        },
    }
    cfg.update(overrides)
    return cfg


def _sample_corrections():
    return [
        {
            "timestamp": "2026-01-01T10:00:00+00:00",
            "asr_text": "派森编程语言",
            "enhanced_text": "Python编程语言",
            "final_text": "Python编程语言",
            "enhance_mode": "proofread",
        },
        {
            "timestamp": "2026-01-01T11:00:00+00:00",
            "asr_text": "库伯尼特斯容器",
            "enhanced_text": "Kubernetes容器",
            "final_text": "Kubernetes容器",
            "enhance_mode": "proofread",
        },
        {
            "timestamp": "2026-01-01T12:00:00+00:00",
            "asr_text": "VSCode编辑器",
            "enhanced_text": "VS Code编辑器",
            "final_text": "Visual Studio Code编辑器",
            "enhance_mode": "proofread",
        },
    ]


class TestReadCorrections:
    def test_read_all(self, tmp_path):
        corrections_path = tmp_path / "corrections.jsonl"
        records = _sample_corrections()
        with open(corrections_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        result = builder._read_corrections()
        assert len(result) == 3

    def test_read_since_timestamp(self, tmp_path):
        corrections_path = tmp_path / "corrections.jsonl"
        records = _sample_corrections()
        with open(corrections_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        result = builder._read_corrections(since="2026-01-01T10:00:00+00:00")
        assert len(result) == 2
        assert result[0]["asr_text"] == "库伯尼特斯容器"

    def test_read_no_file(self, tmp_path):
        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        result = builder._read_corrections()
        assert result == []

    def test_read_skips_invalid_json(self, tmp_path):
        corrections_path = tmp_path / "corrections.jsonl"
        with open(corrections_path, "w", encoding="utf-8") as f:
            f.write('{"timestamp": "2026-01-01T10:00:00", "asr_text": "hello"}\n')
            f.write("invalid json line\n")
            f.write('{"timestamp": "2026-01-01T11:00:00", "asr_text": "world"}\n')

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        result = builder._read_corrections()
        assert len(result) == 2

    def test_read_skips_empty_lines(self, tmp_path):
        corrections_path = tmp_path / "corrections.jsonl"
        with open(corrections_path, "w", encoding="utf-8") as f:
            f.write('{"timestamp": "2026-01-01T10:00:00", "asr_text": "hello"}\n')
            f.write("\n")
            f.write("\n")
            f.write('{"timestamp": "2026-01-01T11:00:00", "asr_text": "world"}\n')

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        result = builder._read_corrections()
        assert len(result) == 2


class TestBatchRecords:
    def test_batch_exact_size(self):
        builder = VocabularyBuilder(_make_config())
        records = [{"i": i} for i in range(20)]
        batches = builder._batch_records(records, batch_size=20)
        assert len(batches) == 1
        assert len(batches[0]) == 20

    def test_batch_remainder(self):
        builder = VocabularyBuilder(_make_config())
        records = [{"i": i} for i in range(25)]
        batches = builder._batch_records(records, batch_size=20)
        assert len(batches) == 2
        assert len(batches[0]) == 20
        assert len(batches[1]) == 5

    def test_batch_smaller_than_size(self):
        builder = VocabularyBuilder(_make_config())
        records = [{"i": i} for i in range(5)]
        batches = builder._batch_records(records, batch_size=20)
        assert len(batches) == 1
        assert len(batches[0]) == 5

    def test_batch_empty(self):
        builder = VocabularyBuilder(_make_config())
        batches = builder._batch_records([], batch_size=20)
        assert batches == []


class TestExtractBatch:
    def test_successful_extraction(self):
        builder = VocabularyBuilder(_make_config())
        batch = [{"asr_text": "派森", "final_text": "Python"}]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"term": "Python", "category": "tech", "variants": ["派森"], "context": "编程语言"}
        ])

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                builder._extract_batch(batch)
            )

        assert len(result) == 1
        assert result[0]["term"] == "Python"

    def test_empty_llm_response(self):
        builder = VocabularyBuilder(_make_config())
        batch = [{"asr_text": "test", "final_text": "test"}]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                builder._extract_batch(batch)
            )

        assert result == []

    def test_no_provider_config(self):
        builder = VocabularyBuilder({"providers": {}})
        batch = [{"asr_text": "test", "final_text": "test"}]

        result = asyncio.get_event_loop().run_until_complete(
            builder._extract_batch(batch)
        )
        assert result == []

    def test_timeout(self):
        cfg = _make_config()
        cfg["vocabulary"] = {"build_timeout": 1}
        builder = VocabularyBuilder(cfg)
        batch = [{"asr_text": "test", "final_text": "test"}]

        async def slow_create(**kwargs):
            await asyncio.sleep(10)

        mock_client = MagicMock()
        mock_client.chat.completions.create = slow_create

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            with pytest.raises(asyncio.TimeoutError):
                asyncio.get_event_loop().run_until_complete(
                    builder._extract_batch(batch)
                )


class TestParseLLMResponse:
    def test_parse_json_array(self):
        builder = VocabularyBuilder(_make_config())
        content = '[{"term": "Python", "category": "tech"}]'
        result = builder._parse_llm_response(content)
        assert len(result) == 1
        assert result[0]["term"] == "Python"

    def test_parse_with_markdown_fences(self):
        builder = VocabularyBuilder(_make_config())
        content = '```json\n[{"term": "Python"}]\n```'
        result = builder._parse_llm_response(content)
        assert len(result) == 1
        assert result[0]["term"] == "Python"

    def test_parse_invalid_json(self):
        builder = VocabularyBuilder(_make_config())
        result = builder._parse_llm_response("not json at all")
        assert result == []

    def test_parse_non_array(self):
        builder = VocabularyBuilder(_make_config())
        result = builder._parse_llm_response('{"term": "Python"}')
        assert result == []

    def test_parse_filters_entries_without_term(self):
        builder = VocabularyBuilder(_make_config())
        content = '[{"term": "Python"}, {"category": "tech"}, {"term": "Java"}]'
        result = builder._parse_llm_response(content)
        assert len(result) == 2
        assert result[0]["term"] == "Python"
        assert result[1]["term"] == "Java"


class TestMergeEntries:
    def test_merge_new_entries(self):
        builder = VocabularyBuilder(_make_config())
        existing = [
            {"term": "Python", "category": "tech", "variants": ["派森"], "context": "编程语言", "frequency": 1}
        ]
        new = [
            {"term": "Java", "category": "tech", "variants": ["加瓦"], "context": "编程语言"}
        ]
        result = builder._merge_entries(existing, new)
        assert len(result) == 2
        terms = {e["term"] for e in result}
        assert terms == {"Python", "Java"}

    def test_merge_deduplicates_by_term(self):
        builder = VocabularyBuilder(_make_config())
        existing = [
            {"term": "Python", "variants": ["派森"], "frequency": 2}
        ]
        new = [
            {"term": "Python", "variants": ["拍森"], "context": ""}
        ]
        result = builder._merge_entries(existing, new)
        assert len(result) == 1
        entry = result[0]
        assert entry["term"] == "Python"
        assert set(entry["variants"]) == {"派森", "拍森"}
        assert entry["frequency"] == 3

    def test_merge_accumulates_frequency(self):
        builder = VocabularyBuilder(_make_config())
        existing = [{"term": "Python", "frequency": 5}]
        new = [{"term": "Python"}]
        result = builder._merge_entries(existing, new)
        assert result[0]["frequency"] == 6

    def test_merge_updates_empty_context(self):
        builder = VocabularyBuilder(_make_config())
        existing = [{"term": "Python", "context": "", "frequency": 1}]
        new = [{"term": "Python", "context": "编程语言"}]
        result = builder._merge_entries(existing, new)
        assert result[0]["context"] == "编程语言"

    def test_merge_keeps_existing_context(self):
        builder = VocabularyBuilder(_make_config())
        existing = [{"term": "Python", "context": "编程语言", "frequency": 1}]
        new = [{"term": "Python", "context": "脚本语言"}]
        result = builder._merge_entries(existing, new)
        assert result[0]["context"] == "编程语言"

    def test_merge_empty_existing(self):
        builder = VocabularyBuilder(_make_config())
        new = [{"term": "Python", "variants": ["派森"]}]
        result = builder._merge_entries([], new)
        assert len(result) == 1
        assert result[0]["frequency"] == 1

    def test_merge_empty_new(self):
        builder = VocabularyBuilder(_make_config())
        existing = [{"term": "Python", "frequency": 1}]
        result = builder._merge_entries(existing, [])
        assert len(result) == 1

    def test_merge_skips_empty_term(self):
        builder = VocabularyBuilder(_make_config())
        new = [{"term": "", "category": "tech"}, {"term": "Python"}]
        result = builder._merge_entries([], new)
        assert len(result) == 1
        assert result[0]["term"] == "Python"


class TestBuild:
    def test_build_no_records(self, tmp_path):
        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        result = asyncio.get_event_loop().run_until_complete(builder.build())
        assert result["new_records"] == 0

    def test_build_end_to_end(self, tmp_path):
        # Write corrections
        corrections_path = tmp_path / "corrections.jsonl"
        records = _sample_corrections()
        with open(corrections_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        # Mock LLM response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"term": "Python", "category": "tech", "variants": ["派森"], "context": "编程语言"},
            {"term": "Kubernetes", "category": "tech", "variants": ["库伯尼特斯"], "context": "容器编排"},
        ])

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        with patch("openai.AsyncOpenAI", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(builder.build())

        assert result["new_records"] == 3
        assert result["total_entries"] == 2

        # Verify vocabulary.json was written
        vocab_path = tmp_path / "vocabulary.json"
        assert vocab_path.exists()
        data = json.loads(vocab_path.read_text(encoding="utf-8"))
        assert len(data["entries"]) == 2

    def test_build_incremental(self, tmp_path):
        # Write existing vocabulary
        vocab_path = tmp_path / "vocabulary.json"
        existing = {
            "last_processed_timestamp": "2026-01-01T10:00:00+00:00",
            "entries": [
                {"term": "Python", "category": "tech", "variants": ["派森"], "context": "编程语言", "frequency": 1}
            ],
        }
        vocab_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

        # Write corrections (only newer ones should be processed)
        corrections_path = tmp_path / "corrections.jsonl"
        records = _sample_corrections()
        with open(corrections_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        # Mock LLM
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"term": "Kubernetes", "category": "tech", "variants": ["库伯尼特斯"], "context": "容器编排"},
        ])

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        with patch("openai.AsyncOpenAI", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(builder.build())

        # Only 2 records after the timestamp should be processed
        assert result["new_records"] == 2
        assert result["total_entries"] == 2  # Python + Kubernetes

    def test_build_full_rebuild(self, tmp_path):
        # Write existing vocabulary with timestamp
        vocab_path = tmp_path / "vocabulary.json"
        existing = {
            "last_processed_timestamp": "2026-01-01T10:00:00+00:00",
            "entries": [
                {"term": "OldTerm", "category": "other", "variants": [], "context": "", "frequency": 1}
            ],
        }
        vocab_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

        corrections_path = tmp_path / "corrections.jsonl"
        records = _sample_corrections()
        with open(corrections_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"term": "Python", "category": "tech", "variants": ["派森"]},
        ])

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        with patch("openai.AsyncOpenAI", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                builder.build(full_rebuild=True)
            )

        # All 3 records processed, OldTerm merged with new
        assert result["new_records"] == 3
        # OldTerm from existing + Python from LLM
        assert result["total_entries"] == 2


class TestBuildExtractionPrompt:
    def test_prompt_contains_records(self):
        builder = VocabularyBuilder(_make_config())
        batch = [
            {"asr_text": "派森", "final_text": "Python"},
            {"asr_text": "加瓦", "final_text": "Java"},
        ]
        prompt = builder._build_extraction_prompt(batch)
        assert "派森" in prompt
        assert "Python" in prompt
        assert "加瓦" in prompt
        assert "Java" in prompt
        assert "JSON" in prompt


class TestSaveLoadVocabulary:
    def test_save_and_load(self, tmp_path):
        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        vocab = {
            "last_processed_timestamp": "2026-01-01T00:00:00",
            "entries": [{"term": "Python", "category": "tech"}],
        }
        builder._save_vocabulary(vocab)

        loaded = builder._load_existing_vocabulary()
        assert loaded["entries"][0]["term"] == "Python"

    def test_load_nonexistent(self, tmp_path):
        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        result = builder._load_existing_vocabulary()
        assert result == {}
