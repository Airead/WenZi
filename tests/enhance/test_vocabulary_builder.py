"""Tests for the vocabulary builder module."""

from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voicetext.enhance.vocabulary_builder import BuildCallbacks, VocabularyBuilder


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
            "user_corrected": True,
        },
        {
            "timestamp": "2026-01-01T11:00:00+00:00",
            "asr_text": "库伯尼特斯容器",
            "enhanced_text": "Kubernetes容器",
            "final_text": "Kubernetes容器",
            "enhance_mode": "proofread",
            "user_corrected": True,
        },
        {
            "timestamp": "2026-01-01T12:00:00+00:00",
            "asr_text": "VSCode编辑器",
            "enhanced_text": "VS Code编辑器",
            "final_text": "Visual Studio Code编辑器",
            "enhance_mode": "proofread",
            "user_corrected": True,
        },
    ]


_PIPE_RESPONSE_TWO = (
    "term|category|variants|context\n"
    "Python|tech|派森|编程语言\n"
    "Kubernetes|tech|库伯尼特斯|容器编排"
)

_PIPE_RESPONSE_ONE = (
    "term|category|variants|context\n"
    "Python|tech|派森|编程语言"
)

_PIPE_RESPONSE_K8S = (
    "term|category|variants|context\n"
    "Kubernetes|tech|库伯尼特斯|容器编排"
)

_PIPE_RESPONSE_TEST = (
    "term|category|variants|context\n"
    "TestTerm|tech||test"
)


class TestReadCorrections:
    def test_read_all(self, tmp_path):
        corrections_path = tmp_path / "conversation_history.jsonl"
        records = _sample_corrections()
        with open(corrections_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        result = builder._read_corrections()
        assert len(result) == 3

    def test_read_since_timestamp(self, tmp_path):
        corrections_path = tmp_path / "conversation_history.jsonl"
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
        corrections_path = tmp_path / "conversation_history.jsonl"
        with open(corrections_path, "w", encoding="utf-8") as f:
            f.write('{"timestamp": "2026-01-01T10:00:00", "asr_text": "hello", "user_corrected": true}\n')
            f.write("invalid json line\n")
            f.write('{"timestamp": "2026-01-01T11:00:00", "asr_text": "world", "user_corrected": true}\n')

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        result = builder._read_corrections()
        assert len(result) == 2

    def test_read_skips_empty_lines(self, tmp_path):
        corrections_path = tmp_path / "conversation_history.jsonl"
        with open(corrections_path, "w", encoding="utf-8") as f:
            f.write('{"timestamp": "2026-01-01T10:00:00", "asr_text": "hello", "user_corrected": true}\n')
            f.write("\n")
            f.write("\n")
            f.write('{"timestamp": "2026-01-01T11:00:00", "asr_text": "world", "user_corrected": true}\n')

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        result = builder._read_corrections()
        assert len(result) == 2

    def test_read_filters_non_corrections(self, tmp_path):
        corrections_path = tmp_path / "conversation_history.jsonl"
        with open(corrections_path, "w", encoding="utf-8") as f:
            f.write('{"timestamp": "2026-01-01T10:00:00", "asr_text": "a", "user_corrected": true}\n')
            f.write('{"timestamp": "2026-01-01T11:00:00", "asr_text": "b", "user_corrected": false}\n')
            f.write('{"timestamp": "2026-01-01T12:00:00", "asr_text": "c", "user_corrected": true}\n')

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        result = builder._read_corrections()
        assert len(result) == 2
        assert result[0]["asr_text"] == "a"
        assert result[1]["asr_text"] == "c"


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

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 5
        mock_usage.total_tokens = 15

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = _PIPE_RESPONSE_ONE
        mock_response.usage = mock_usage

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            entries, usage = asyncio.run(
                builder._extract_batch(batch)
            )

        assert len(entries) == 1
        assert entries[0]["term"] == "Python"
        assert usage["total_tokens"] == 15

    def test_empty_llm_response(self):
        builder = VocabularyBuilder(_make_config())
        batch = [{"asr_text": "test", "final_text": "test"}]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        mock_response.usage = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            entries, usage = asyncio.run(
                builder._extract_batch(batch)
            )

        assert entries == []

    def test_no_provider_config(self):
        builder = VocabularyBuilder({"providers": {}})
        batch = [{"asr_text": "test", "final_text": "test"}]

        entries, usage = asyncio.run(
            builder._extract_batch(batch)
        )
        assert entries == []
        assert usage == {}

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
                asyncio.run(
                    builder._extract_batch(batch)
                )


class TestParseLLMResponse:
    def test_parse_pipe_text(self):
        builder = VocabularyBuilder(_make_config())
        content = "term|category|variants|context\nPython|tech|派森|编程语言"
        result = builder._parse_llm_response(content)
        assert len(result) == 1
        assert result[0]["term"] == "Python"
        assert result[0]["category"] == "tech"
        assert result[0]["variants"] == ["派森"]
        assert result[0]["context"] == "编程语言"

    def test_parse_multiple_entries(self):
        builder = VocabularyBuilder(_make_config())
        content = (
            "term|category|variants|context\n"
            "Python|tech|派森|编程语言\n"
            "Kubernetes|tech|库伯尼特斯|容器编排"
        )
        result = builder._parse_llm_response(content)
        assert len(result) == 2
        assert result[0]["term"] == "Python"
        assert result[1]["term"] == "Kubernetes"

    def test_parse_with_markdown_fences(self):
        builder = VocabularyBuilder(_make_config())
        content = "```\nterm|category|variants|context\nPython|tech|派森|编程语言\n```"
        result = builder._parse_llm_response(content)
        assert len(result) == 1
        assert result[0]["term"] == "Python"

    def test_parse_empty_variants(self):
        builder = VocabularyBuilder(_make_config())
        content = "term|category|variants|context\nPython|tech||编程语言"
        result = builder._parse_llm_response(content)
        assert len(result) == 1
        assert result[0]["variants"] == []

    def test_parse_multiple_variants(self):
        builder = VocabularyBuilder(_make_config())
        content = "term|category|variants|context\nKubernetes|tech|库伯尼特斯,酷伯,K8S|容器编排"
        result = builder._parse_llm_response(content)
        assert len(result) == 1
        assert result[0]["variants"] == ["库伯尼特斯", "酷伯", "K8S"]

    def test_parse_skips_short_lines(self):
        builder = VocabularyBuilder(_make_config())
        content = "term|category|variants|context\nPython|tech|派森|编程语言\nbadline|tech|only three"
        result = builder._parse_llm_response(content)
        assert len(result) == 1
        assert result[0]["term"] == "Python"

    def test_parse_empty_content(self):
        builder = VocabularyBuilder(_make_config())
        result = builder._parse_llm_response("")
        assert result == []

    def test_parse_header_only(self):
        builder = VocabularyBuilder(_make_config())
        result = builder._parse_llm_response("term|category|variants|context")
        assert result == []

    def test_parse_default_category(self):
        builder = VocabularyBuilder(_make_config())
        content = "term|category|variants|context\nPython||派森|编程语言"
        result = builder._parse_llm_response(content)
        assert len(result) == 1
        assert result[0]["category"] == "other"

    def test_parse_skips_empty_term(self):
        builder = VocabularyBuilder(_make_config())
        content = "term|category|variants|context\n|tech|派森|编程语言\nPython|tech||编程语言"
        result = builder._parse_llm_response(content)
        assert len(result) == 1
        assert result[0]["term"] == "Python"

    def test_parse_skips_blank_lines(self):
        builder = VocabularyBuilder(_make_config())
        content = "term|category|variants|context\nPython|tech|派森|编程语言\n\nJava|tech|加瓦|编程语言"
        result = builder._parse_llm_response(content)
        assert len(result) == 2


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
        result = asyncio.run(builder.build())
        assert result["new_records"] == 0

    def test_build_end_to_end(self, tmp_path):
        corrections_path = tmp_path / "conversation_history.jsonl"
        records = _sample_corrections()
        with open(corrections_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = _PIPE_RESPONSE_TWO
        mock_response.usage = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        with patch("openai.AsyncOpenAI", return_value=mock_client):
            result = asyncio.run(builder.build())

        assert result["new_records"] == 3
        assert result["total_entries"] == 2

        # Verify vocabulary.json was written with built_at
        vocab_path = tmp_path / "vocabulary.json"
        assert vocab_path.exists()
        data = json.loads(vocab_path.read_text(encoding="utf-8"))
        assert len(data["entries"]) == 2
        assert "built_at" in data

    def test_build_incremental(self, tmp_path):
        vocab_path = tmp_path / "vocabulary.json"
        existing = {
            "last_processed_timestamp": "2026-01-01T10:00:00+00:00",
            "entries": [
                {"term": "Python", "category": "tech", "variants": ["派森"], "context": "编程语言", "frequency": 1}
            ],
        }
        vocab_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

        corrections_path = tmp_path / "conversation_history.jsonl"
        records = _sample_corrections()
        with open(corrections_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = _PIPE_RESPONSE_K8S
        mock_response.usage = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        with patch("openai.AsyncOpenAI", return_value=mock_client):
            result = asyncio.run(builder.build())

        assert result["new_records"] == 2
        assert result["total_entries"] == 2  # Python + Kubernetes

        # Verify built_at is present
        data = json.loads(vocab_path.read_text(encoding="utf-8"))
        assert "built_at" in data

    def test_build_full_rebuild(self, tmp_path):
        vocab_path = tmp_path / "vocabulary.json"
        existing = {
            "last_processed_timestamp": "2026-01-01T10:00:00+00:00",
            "entries": [
                {"term": "OldTerm", "category": "other", "variants": [], "context": "", "frequency": 1}
            ],
        }
        vocab_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

        corrections_path = tmp_path / "conversation_history.jsonl"
        records = _sample_corrections()
        with open(corrections_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = _PIPE_RESPONSE_ONE
        mock_response.usage = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        with patch("openai.AsyncOpenAI", return_value=mock_client):
            result = asyncio.run(
                builder.build(full_rebuild=True)
            )

        assert result["new_records"] == 3
        assert result["total_entries"] == 2  # OldTerm + Python

        data = json.loads(vocab_path.read_text(encoding="utf-8"))
        assert "built_at" in data


class TestBuildWithCancel:
    def test_cancel_after_first_batch(self, tmp_path):
        """Cancel event set after first batch - should save partial results."""
        corrections_path = tmp_path / "conversation_history.jsonl"
        records = []
        for i in range(25):
            records.append({
                "timestamp": f"2026-01-01T{i:02d}:00:00+00:00",
                "asr_text": f"test{i}",
                "final_text": f"test{i}",
                "user_corrected": True,
            })
        with open(corrections_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = _PIPE_RESPONSE_TEST
        mock_response.usage = None

        cancel_event = threading.Event()
        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                cancel_event.set()
            return mock_response

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        with patch("openai.AsyncOpenAI", return_value=mock_client):
            result = asyncio.run(
                builder.build(cancel_event=cancel_event)
            )

        assert result.get("cancelled") is True
        assert result["new_entries"] == 1
        assert result["total_entries"] == 1
        assert (tmp_path / "vocabulary.json").exists()

    def test_cancel_before_any_batch(self, tmp_path):
        """Cancel event set before processing starts."""
        corrections_path = tmp_path / "conversation_history.jsonl"
        records = _sample_corrections()
        with open(corrections_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        cancel_event = threading.Event()
        cancel_event.set()

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        result = asyncio.run(
            builder.build(cancel_event=cancel_event)
        )

        assert result.get("cancelled") is True
        assert result["new_entries"] == 0


class _AsyncStreamMock:
    """Mock for OpenAI AsyncStream that supports async with and async for."""

    def __init__(self, items):
        self._items = iter(items)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration


class TestExtractBatchStreaming:
    def test_streaming_collects_chunks(self):
        """on_stream_chunk should be called for each streamed chunk."""
        builder = VocabularyBuilder(_make_config())
        batch = [{"asr_text": "test", "final_text": "test"}]

        pipe_parts = ["term|categ", "ory|variants|context\n", "Python|tech|派森|", "编程语言"]
        "".join(pipe_parts)

        chunks = []
        for part in pipe_parts:
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = part
            chunk.usage = None
            chunks.append(chunk)

        async def mock_create(**kwargs):
            assert kwargs.get("stream") is True
            return _AsyncStreamMock(chunks)

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        collected_chunks = []
        def on_chunk(c):
            return collected_chunks.append(c)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            entries, usage = asyncio.run(
                builder._extract_batch(batch, on_stream_chunk=on_chunk)
            )

        assert collected_chunks == pipe_parts
        assert len(entries) == 1
        assert entries[0]["term"] == "Python"

    def test_no_callback_uses_non_streaming(self):
        """Without on_stream_chunk, the non-streaming path is used."""
        builder = VocabularyBuilder(_make_config())
        batch = [{"asr_text": "test", "final_text": "test"}]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = _PIPE_RESPONSE_ONE
        mock_response.usage = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            entries, usage = asyncio.run(
                builder._extract_batch(batch)
            )

        create_call = mock_client.chat.completions.create
        create_call.assert_called_once()
        call_kwargs = create_call.call_args[1]
        assert "stream" not in call_kwargs

        assert len(entries) == 1
        assert entries[0]["term"] == "Python"


class TestBuildWithCallbacks:
    def test_callbacks_called_correctly(self, tmp_path):
        """Verify on_batch_start and on_batch_done are called for each batch."""
        corrections_path = tmp_path / "conversation_history.jsonl"
        records = _sample_corrections()
        with open(corrections_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        pipe_content = _PIPE_RESPONSE_ONE

        stream_chunk = MagicMock()
        stream_chunk.choices = [MagicMock()]
        stream_chunk.choices[0].delta.content = pipe_content
        stream_chunk.usage = None

        async def mock_create(**kwargs):
            return _AsyncStreamMock([stream_chunk])

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        on_batch_start = MagicMock()
        on_batch_done = MagicMock()
        on_stream_chunk = MagicMock()

        callbacks = BuildCallbacks(
            on_batch_start=on_batch_start,
            on_batch_done=on_batch_done,
            on_stream_chunk=on_stream_chunk,
        )

        builder = VocabularyBuilder(_make_config(), log_dir=str(tmp_path))
        with patch("openai.AsyncOpenAI", return_value=mock_client):
            result = asyncio.run(
                builder.build(callbacks=callbacks)
            )

        on_batch_start.assert_called_once_with(1, 1)
        on_batch_done.assert_called_once_with(1, 1, 1)
        on_stream_chunk.assert_called_once_with(pipe_content)
        assert result["new_entries"] == 1


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
        assert "term|category|variants|context" in prompt
        assert "管道" in prompt


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
