"""Enhance subpackage — AI text enhancement, vocabulary, and conversation history."""

from .auto_vocab_builder import AutoVocabBuilder
from .conversation_history import ConversationHistory
from .enhancer import MODE_OFF, TextEnhancer, create_enhancer
from .mode_loader import ModeDefinition, get_sorted_modes, load_modes
from .vocabulary import VocabularyEntry, VocabularyIndex, get_vocab_entry_count
from .vocabulary_builder import BuildCallbacks, VocabularyBuilder

__all__ = [
    "AutoVocabBuilder",
    "BuildCallbacks",
    "ConversationHistory",
    "MODE_OFF",
    "ModeDefinition",
    "TextEnhancer",
    "VocabularyBuilder",
    "VocabularyEntry",
    "VocabularyIndex",
    "create_enhancer",
    "get_sorted_modes",
    "get_vocab_entry_count",
    "load_modes",
]
