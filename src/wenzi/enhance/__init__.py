"""Enhance subpackage — AI text enhancement, vocabulary, and conversation history."""

from .conversation_history import ConversationHistory
from .enhancer import MODE_OFF, TextEnhancer, create_enhancer
from .mode_loader import ModeDefinition, get_sorted_modes, load_modes
from .preview_history import PreviewHistoryStore, PreviewRecord
from .manual_vocabulary import ManualVocabEntry, ManualVocabularyStore
from .vocabulary import (
    HotwordDetail,
    build_hotword_list_detailed,
)

__all__ = [
    "ConversationHistory",
    "MODE_OFF",
    "ModeDefinition",
    "PreviewHistoryStore",
    "PreviewRecord",
    "TextEnhancer",
    "build_hotword_list_detailed",
    "create_enhancer",
    "get_sorted_modes",
    "HotwordDetail",
    "ManualVocabEntry",
    "ManualVocabularyStore",
    "load_modes",
]
