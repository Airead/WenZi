"""Vocabulary index and retrieval using fastembed + numpy cosine similarity."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from voicetext.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)


@dataclass
class VocabularyEntry:
    """A single vocabulary entry extracted from correction logs."""

    term: str
    category: str = "other"
    variants: List[str] = field(default_factory=list)
    context: str = ""
    frequency: int = 1


class VocabularyIndex:
    """Embedding-based vocabulary index for retrieval during text enhancement."""

    def __init__(
        self,
        config: Dict[str, Any],
        vocab_dir: str = DEFAULT_CONFIG_DIR,
    ) -> None:
        self._config = config
        self._vocab_dir = os.path.expanduser(vocab_dir)
        self._vocab_path = os.path.join(self._vocab_dir, "vocabulary.json")
        self._index_path = os.path.join(self._vocab_dir, "vocabulary_index.npz")
        self._model_name = config.get(
            "embedding_model", "paraphrase-multilingual-MiniLM-L12-v2"
        )

        self._entries: List[VocabularyEntry] = []
        # Each row in _vectors maps to _vector_entry_indices[row] -> index into _entries
        self._vectors: Optional[np.ndarray] = None
        self._vector_entry_indices: List[int] = []
        self._model: Any = None
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def load(self) -> bool:
        """Load vocabulary.json and build/load the embedding index.

        Returns True if loaded successfully.
        """
        try:
            entries = self._read_vocabulary()
            if not entries:
                logger.info("No vocabulary entries found")
                return False

            self._entries = entries

            if self._load_index():
                self._loaded = True
                logger.info(
                    "Vocabulary loaded: %d entries, %d vectors",
                    len(self._entries),
                    len(self._vector_entry_indices),
                )
                return True

            # Index missing or stale, rebuild
            self._lazy_load_model()
            self._build_index(self._entries)
            self._save_index()
            self._loaded = True
            logger.info(
                "Vocabulary index built: %d entries, %d vectors",
                len(self._entries),
                len(self._vector_entry_indices),
            )
            return True
        except Exception as e:
            logger.warning("Failed to load vocabulary: %s", e)
            return False

    def reload(self) -> bool:
        """Reload vocabulary and rebuild index."""
        self._loaded = False
        self._entries = []
        self._vectors = None
        self._vector_entry_indices = []
        return self.load()

    def retrieve(self, text: str, top_k: int = 5) -> List[VocabularyEntry]:
        """Retrieve top-K relevant vocabulary entries for the given text."""
        if not self._loaded or self._vectors is None or not text.strip():
            return []

        try:
            self._lazy_load_model()
            query_vectors = list(self._model.embed([text.strip()]))
            if not query_vectors:
                return []

            query_vec = np.array(query_vectors[0], dtype=np.float32)
            return self._search(query_vec, top_k)
        except Exception as e:
            logger.warning("Vocabulary retrieval failed: %s", e)
            return []

    def format_for_prompt(self, entries: List[VocabularyEntry]) -> str:
        """Format vocabulary entries for injection into LLM prompt."""
        if not entries:
            return ""

        lines = [
            "---",
            "以下是用户词库中与本次输入相关的专有名词，ASR 常将其误写为同音近音词。",
            "仅当输入中确实存在对应误写时才替换，不要强行套用：",
            "",
        ]
        for entry in entries:
            if entry.context:
                lines.append(f"- {entry.term}（{entry.context}）")
            else:
                lines.append(f"- {entry.term}")
        lines.append("---")
        return "\n".join(lines)

    def _lazy_load_model(self) -> None:
        """Load the embedding model on first use."""
        if self._model is not None:
            return

        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise ImportError(
                "fastembed is required for vocabulary features but could not be imported. "
                "Try reinstalling with: uv sync"
            )

        model_id = f"sentence-transformers/{self._model_name}"
        cache_dir = os.path.expanduser("~/.cache/fastembed")
        self._model = TextEmbedding(model_id, cache_dir=cache_dir)
        logger.info("Embedding model loaded: %s", model_id)

    def _build_index(self, entries: List[VocabularyEntry]) -> None:
        """Build embedding vectors for all entries."""
        texts: List[str] = []
        entry_indices: List[int] = []

        for i, entry in enumerate(entries):
            # Embed term
            texts.append(entry.term)
            entry_indices.append(i)

            # Embed each variant
            for variant in entry.variants:
                texts.append(variant)
                entry_indices.append(i)

            # Embed context+term if context exists
            if entry.context:
                texts.append(f"{entry.context} {entry.term}")
                entry_indices.append(i)

        if not texts:
            self._vectors = None
            self._vector_entry_indices = []
            return

        vectors = list(self._model.embed(texts))
        self._vectors = np.array(vectors, dtype=np.float32)
        self._vector_entry_indices = entry_indices

    def _search(self, query_vec: np.ndarray, top_k: int) -> List[VocabularyEntry]:
        """Search for similar vectors using cosine similarity, deduplicate by entry."""
        if self._vectors is None or len(self._vectors) == 0:
            return []

        # Cosine similarity: dot(q, V^T) / (|q| * |V|)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []

        vec_norms = np.linalg.norm(self._vectors, axis=1)
        # Avoid division by zero
        nonzero_mask = vec_norms > 0
        similarities = np.zeros(len(self._vectors), dtype=np.float32)
        similarities[nonzero_mask] = (
            np.dot(self._vectors[nonzero_mask], query_vec)
            / (vec_norms[nonzero_mask] * query_norm)
        )

        # Sort by similarity descending
        sorted_indices = np.argsort(-similarities)

        # Deduplicate by entry index, take top_k
        seen_entries: set = set()
        results: List[VocabularyEntry] = []
        for idx in sorted_indices:
            entry_idx = self._vector_entry_indices[idx]
            if entry_idx in seen_entries:
                continue
            seen_entries.add(entry_idx)
            results.append(self._entries[entry_idx])
            if len(results) >= top_k:
                break

        return results

    def _save_index(self) -> None:
        """Save vectors and entry indices to npz file."""
        if self._vectors is None:
            return

        os.makedirs(self._vocab_dir, exist_ok=True)
        np.savez_compressed(
            self._index_path,
            vectors=self._vectors,
            entry_indices=np.array(self._vector_entry_indices, dtype=np.int32),
        )
        logger.info("Vocabulary index saved: %s", self._index_path)

    def _load_index(self) -> bool:
        """Load cached index from npz file. Returns False if stale or missing."""
        if not os.path.exists(self._index_path):
            return False

        if self._is_index_stale():
            logger.info("Vocabulary index is stale, will rebuild")
            return False

        try:
            data = np.load(self._index_path)
            self._vectors = data["vectors"].astype(np.float32)
            self._vector_entry_indices = data["entry_indices"].tolist()

            # Validate that entry indices are within range
            if self._vector_entry_indices and max(self._vector_entry_indices) >= len(
                self._entries
            ):
                logger.warning("Index entry indices out of range, will rebuild")
                return False

            return True
        except Exception as e:
            logger.warning("Failed to load vocabulary index: %s", e)
            return False

    def _is_index_stale(self) -> bool:
        """Check if the npz index is older than vocabulary.json."""
        try:
            vocab_mtime = os.path.getmtime(self._vocab_path)
            index_mtime = os.path.getmtime(self._index_path)
            return index_mtime < vocab_mtime
        except OSError:
            return True

    def _read_vocabulary(self) -> List[VocabularyEntry]:
        """Read vocabulary.json and parse entries."""
        if not os.path.exists(self._vocab_path):
            return []

        try:
            with open(self._vocab_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            raw_entries = data.get("entries", [])
            entries = []
            for raw in raw_entries:
                entry = VocabularyEntry(
                    term=raw["term"],
                    category=raw.get("category", "other"),
                    variants=raw.get("variants", []),
                    context=raw.get("context", ""),
                    frequency=raw.get("frequency", 1),
                )
                entries.append(entry)
            return entries
        except Exception as e:
            logger.warning("Failed to read vocabulary.json: %s", e)
            return []


def get_vocab_entry_count(config_dir: str = DEFAULT_CONFIG_DIR) -> int:
    """Read the number of entries in vocabulary.json without loading the index."""
    vocab_path = os.path.join(os.path.expanduser(config_dir), "vocabulary.json")
    if not os.path.exists(vocab_path):
        return 0
    try:
        with open(vocab_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return len(data.get("entries", []))
    except Exception:
        return 0
