"""Tests for the vocabulary index and retrieval module."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import numpy as np

from voicetext.enhance.vocabulary import VocabularyEntry, VocabularyIndex, get_vocab_entry_count


# --- VocabularyEntry tests ---


class TestVocabularyEntry:
    def test_defaults(self):
        entry = VocabularyEntry(term="Python")
        assert entry.term == "Python"
        assert entry.category == "other"
        assert entry.variants == []
        assert entry.context == ""
        assert entry.frequency == 1

    def test_full_entry(self):
        entry = VocabularyEntry(
            term="Kubernetes",
            category="tech",
            variants=["库伯尼特斯", "K8S"],
            context="容器编排",
            frequency=5,
        )
        assert entry.term == "Kubernetes"
        assert entry.variants == ["库伯尼特斯", "K8S"]
        assert entry.frequency == 5


# --- VocabularyIndex tests ---


def _make_vocab_json(entries):
    """Create a vocabulary.json-compatible dict."""
    return {
        "last_processed_timestamp": "2026-01-01T00:00:00+00:00",
        "entries": entries,
    }


def _sample_entries():
    return [
        {
            "term": "Python",
            "category": "tech",
            "variants": ["派森"],
            "context": "编程语言",
            "frequency": 3,
        },
        {
            "term": "Kubernetes",
            "category": "tech",
            "variants": ["库伯尼特斯"],
            "context": "容器编排",
            "frequency": 2,
        },
        {
            "term": "Visual Studio Code",
            "category": "tech",
            "variants": ["VSCode"],
            "context": "代码编辑器",
            "frequency": 1,
        },
    ]


class TestVocabularyIndexLoad:
    def test_load_success(self, tmp_path):
        vocab_path = tmp_path / "vocabulary.json"
        vocab_path.write_text(
            json.dumps(_make_vocab_json(_sample_entries())),
            encoding="utf-8",
        )

        mock_model = MagicMock()
        # Return dummy vectors for each text
        def fake_embed(texts):
            return [np.random.randn(384).astype(np.float32) for _ in texts]

        mock_model.embed = fake_embed

        with patch("voicetext.enhance.vocabulary.VocabularyIndex._lazy_load_model") as mock_load:
            def set_model(self=None):
                idx._model = mock_model

            idx = VocabularyIndex({}, vocab_dir=str(tmp_path))
            mock_load.side_effect = lambda: setattr(idx, "_model", mock_model)
            result = idx.load()

        assert result is True
        assert idx.is_loaded
        assert len(idx._entries) == 3

    def test_load_no_vocabulary_file(self, tmp_path):
        idx = VocabularyIndex({}, vocab_dir=str(tmp_path))
        result = idx.load()
        assert result is False
        assert not idx.is_loaded

    def test_load_empty_entries(self, tmp_path):
        vocab_path = tmp_path / "vocabulary.json"
        vocab_path.write_text(
            json.dumps(_make_vocab_json([])), encoding="utf-8"
        )

        idx = VocabularyIndex({}, vocab_dir=str(tmp_path))
        result = idx.load()
        assert result is False

    def test_load_uses_cached_index(self, tmp_path):
        entries = _sample_entries()
        vocab_path = tmp_path / "vocabulary.json"
        vocab_path.write_text(
            json.dumps(_make_vocab_json(entries)), encoding="utf-8"
        )

        # Create a cached npz
        len(entries)
        # Each entry has: term + variants + context = 3 vectors typically
        n_vectors = sum(1 + len(e.get("variants", [])) + (1 if e.get("context") else 0) for e in entries)
        vectors = np.random.randn(n_vectors, 384).astype(np.float32)
        entry_indices = []
        for i, e in enumerate(entries):
            entry_indices.append(i)
            for _ in e.get("variants", []):
                entry_indices.append(i)
            if e.get("context"):
                entry_indices.append(i)

        index_path = tmp_path / "vocabulary_index.npz"
        np.savez_compressed(
            str(index_path),
            vectors=vectors,
            entry_indices=np.array(entry_indices, dtype=np.int32),
        )
        # Make index newer than vocab
        os.utime(str(index_path), (1e10, 1e10))

        idx = VocabularyIndex({}, vocab_dir=str(tmp_path))
        result = idx.load()
        assert result is True
        assert idx.is_loaded

    def test_load_rebuilds_stale_index(self, tmp_path):
        entries = _sample_entries()
        vocab_path = tmp_path / "vocabulary.json"
        vocab_path.write_text(
            json.dumps(_make_vocab_json(entries)), encoding="utf-8"
        )

        # Create an old npz
        index_path = tmp_path / "vocabulary_index.npz"
        np.savez_compressed(
            str(index_path),
            vectors=np.random.randn(3, 384).astype(np.float32),
            entry_indices=np.array([0, 1, 2], dtype=np.int32),
        )
        # Make vocab newer
        os.utime(str(vocab_path), (1e10, 1e10))

        mock_model = MagicMock()
        mock_model.embed = lambda texts: [
            np.random.randn(384).astype(np.float32) for _ in texts
        ]

        idx = VocabularyIndex({}, vocab_dir=str(tmp_path))
        with patch.object(idx, "_lazy_load_model", lambda: setattr(idx, "_model", mock_model)):
            result = idx.load()

        assert result is True
        assert idx.is_loaded


class TestVocabularyIndexRetrieve:
    def _make_loaded_index(self, tmp_path):
        """Create a loaded index with known vectors."""
        entries = [
            VocabularyEntry(term="Python", category="tech", variants=["派森"], context="编程语言"),
            VocabularyEntry(term="Java", category="tech", variants=["加瓦"], context="编程语言"),
            VocabularyEntry(term="北京", category="place", variants=[], context="中国首都"),
        ]

        # Create distinct vectors for each entry
        # Python-related vectors point in similar direction
        py_vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        py_variant = np.array([0.9, 0.1, 0.0], dtype=np.float32)
        py_ctx = np.array([0.8, 0.2, 0.0], dtype=np.float32)

        java_vec = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        java_variant = np.array([0.1, 0.9, 0.0], dtype=np.float32)
        java_ctx = np.array([0.2, 0.8, 0.0], dtype=np.float32)

        beijing_vec = np.array([0.0, 0.0, 1.0], dtype=np.float32)
        beijing_ctx = np.array([0.0, 0.1, 0.9], dtype=np.float32)

        vectors = np.array([
            py_vec, py_variant, py_ctx,
            java_vec, java_variant, java_ctx,
            beijing_vec, beijing_ctx,
        ])
        entry_indices = [0, 0, 0, 1, 1, 1, 2, 2]

        idx = VocabularyIndex({}, vocab_dir=str(tmp_path))
        idx._entries = entries
        idx._vectors = vectors
        idx._vector_entry_indices = entry_indices
        idx._loaded = True

        # Mock model to return specific vectors
        mock_model = MagicMock()
        idx._model = mock_model
        return idx, mock_model

    def test_retrieve_returns_matching_entries(self, tmp_path):
        idx, mock_model = self._make_loaded_index(tmp_path)
        # Query similar to Python
        mock_model.embed.return_value = [np.array([0.95, 0.05, 0.0], dtype=np.float32)]

        results = idx.retrieve("Python编程", top_k=2)
        assert len(results) == 2
        assert results[0].term == "Python"

    def test_retrieve_deduplicates(self, tmp_path):
        idx, mock_model = self._make_loaded_index(tmp_path)
        mock_model.embed.return_value = [np.array([0.9, 0.1, 0.0], dtype=np.float32)]

        results = idx.retrieve("Python", top_k=5)
        terms = [r.term for r in results]
        # No duplicates
        assert len(terms) == len(set(terms))

    def test_retrieve_respects_top_k(self, tmp_path):
        idx, mock_model = self._make_loaded_index(tmp_path)
        mock_model.embed.return_value = [np.array([0.5, 0.5, 0.0], dtype=np.float32)]

        results = idx.retrieve("编程", top_k=1)
        assert len(results) == 1

    def test_retrieve_empty_text(self, tmp_path):
        idx, mock_model = self._make_loaded_index(tmp_path)
        results = idx.retrieve("", top_k=5)
        assert results == []

    def test_retrieve_not_loaded(self, tmp_path):
        idx = VocabularyIndex({}, vocab_dir=str(tmp_path))
        results = idx.retrieve("Python", top_k=5)
        assert results == []

    def test_retrieve_exception_returns_empty(self, tmp_path):
        idx, mock_model = self._make_loaded_index(tmp_path)
        mock_model.embed.side_effect = RuntimeError("model error")

        results = idx.retrieve("Python", top_k=5)
        assert results == []


class TestVocabularyIndexFormatForPrompt:
    def test_format_with_context(self):
        entries = [
            VocabularyEntry(term="Python", context="编程语言"),
            VocabularyEntry(term="Kubernetes", context="容器编排"),
        ]
        idx = VocabularyIndex({})
        result = idx.format_for_prompt(entries)
        assert "用户词库" in result
        assert "不要强行套用" in result
        assert "- Python（编程语言）" in result
        assert "- Kubernetes（容器编排）" in result

    def test_format_without_context(self):
        entries = [VocabularyEntry(term="Python")]
        idx = VocabularyIndex({})
        result = idx.format_for_prompt(entries)
        assert "- Python" in result
        assert "- Python（" not in result

    def test_format_empty(self):
        idx = VocabularyIndex({})
        result = idx.format_for_prompt([])
        assert result == ""

    def test_format_mixed(self):
        entries = [
            VocabularyEntry(term="Python", context="编程语言"),
            VocabularyEntry(term="FastAPI"),
        ]
        idx = VocabularyIndex({})
        result = idx.format_for_prompt(entries)
        assert "- Python（编程语言）" in result
        assert "- FastAPI" in result


class TestVocabularyIndexStaleness:
    def test_stale_when_vocab_newer(self, tmp_path):
        vocab_path = tmp_path / "vocabulary.json"
        index_path = tmp_path / "vocabulary_index.npz"

        vocab_path.write_text("{}", encoding="utf-8")
        np.savez_compressed(str(index_path), vectors=np.array([]))

        # Make vocab newer
        os.utime(str(vocab_path), (1e10, 1e10))
        os.utime(str(index_path), (1e9, 1e9))

        idx = VocabularyIndex({}, vocab_dir=str(tmp_path))
        assert idx._is_index_stale() is True

    def test_not_stale_when_index_newer(self, tmp_path):
        vocab_path = tmp_path / "vocabulary.json"
        index_path = tmp_path / "vocabulary_index.npz"

        vocab_path.write_text("{}", encoding="utf-8")
        np.savez_compressed(str(index_path), vectors=np.array([]))

        os.utime(str(vocab_path), (1e9, 1e9))
        os.utime(str(index_path), (1e10, 1e10))

        idx = VocabularyIndex({}, vocab_dir=str(tmp_path))
        assert idx._is_index_stale() is False

    def test_stale_when_no_index(self, tmp_path):
        vocab_path = tmp_path / "vocabulary.json"
        vocab_path.write_text("{}", encoding="utf-8")

        idx = VocabularyIndex({}, vocab_dir=str(tmp_path))
        assert idx._is_index_stale() is True


class TestVocabularyIndexReload:
    def test_reload_resets_state(self, tmp_path):
        vocab_path = tmp_path / "vocabulary.json"
        vocab_path.write_text(
            json.dumps(_make_vocab_json(_sample_entries())),
            encoding="utf-8",
        )

        mock_model = MagicMock()
        mock_model.embed = lambda texts: [
            np.random.randn(384).astype(np.float32) for _ in texts
        ]

        idx = VocabularyIndex({}, vocab_dir=str(tmp_path))
        idx._model = mock_model
        with patch.object(idx, "_lazy_load_model"):
            idx.load()
        assert idx.is_loaded

        with patch.object(idx, "_lazy_load_model"):
            idx.reload()
        assert idx.is_loaded


class TestEntryCount:
    def test_entry_count_after_load(self, tmp_path):
        vocab_path = tmp_path / "vocabulary.json"
        vocab_path.write_text(
            json.dumps(_make_vocab_json(_sample_entries())),
            encoding="utf-8",
        )

        mock_model = MagicMock()
        mock_model.embed = lambda texts: [
            np.random.randn(384).astype(np.float32) for _ in texts
        ]

        idx = VocabularyIndex({}, vocab_dir=str(tmp_path))
        assert idx.entry_count == 0

        idx._model = mock_model
        with patch.object(idx, "_lazy_load_model"):
            idx.load()
        assert idx.entry_count == 3

    def test_entry_count_after_reload(self, tmp_path):
        vocab_path = tmp_path / "vocabulary.json"
        vocab_path.write_text(
            json.dumps(_make_vocab_json(_sample_entries())),
            encoding="utf-8",
        )

        mock_model = MagicMock()
        mock_model.embed = lambda texts: [
            np.random.randn(384).astype(np.float32) for _ in texts
        ]

        idx = VocabularyIndex({}, vocab_dir=str(tmp_path))
        idx._model = mock_model
        with patch.object(idx, "_lazy_load_model"):
            idx.load()

        # Add one more entry and reload
        entries = _sample_entries() + [
            {"term": "Docker", "category": "tech", "variants": [], "context": "", "frequency": 1}
        ]
        vocab_path.write_text(
            json.dumps(_make_vocab_json(entries)),
            encoding="utf-8",
        )
        with patch.object(idx, "_lazy_load_model"):
            idx.reload()
        assert idx.entry_count == 4


class TestGetVocabEntryCount:
    def test_count_with_entries(self, tmp_path):
        vocab_path = tmp_path / "vocabulary.json"
        vocab_path.write_text(
            json.dumps(_make_vocab_json(_sample_entries())),
            encoding="utf-8",
        )
        assert get_vocab_entry_count(str(tmp_path)) == 3

    def test_count_no_file(self, tmp_path):
        assert get_vocab_entry_count(str(tmp_path)) == 0

    def test_count_empty_entries(self, tmp_path):
        vocab_path = tmp_path / "vocabulary.json"
        vocab_path.write_text(
            json.dumps(_make_vocab_json([])),
            encoding="utf-8",
        )
        assert get_vocab_entry_count(str(tmp_path)) == 0

    def test_count_invalid_json(self, tmp_path):
        vocab_path = tmp_path / "vocabulary.json"
        vocab_path.write_text("not json", encoding="utf-8")
        assert get_vocab_entry_count(str(tmp_path)) == 0
