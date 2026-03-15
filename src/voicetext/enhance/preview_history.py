"""In-memory preview history store for quick access to recent preview results."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PreviewRecord:
    """A single preview result cached in memory."""

    timestamp: Optional[str]  # None = from cancel (not in ConversationHistory)
    created_at: str  # always set — local ISO time for display
    action: str  # "confirm" | "copy" | "cancel"
    asr_text: str
    enhanced_text: Optional[str]
    final_text: str
    enhance_mode: str
    stt_model: str
    llm_model: str
    wav_data: Optional[bytes]
    audio_duration: float
    source: str  # "voice" | "clipboard"


class PreviewHistoryStore:
    """Session-scoped in-memory store for preview results.

    Keeps the most recent *max_size* records.  Oldest records are
    discarded when the limit is exceeded.  Not persisted — cleared
    on application restart.
    """

    def __init__(self, max_size: int = 10) -> None:
        self._records: list[PreviewRecord] = []
        self._max_size = max_size

    def add(self, record: PreviewRecord) -> None:
        """Append a record.  Drops the oldest if at capacity."""
        self._records.append(record)
        if len(self._records) > self._max_size:
            self._records.pop(0)

    def get_all(self) -> list[PreviewRecord]:
        """Return all records, newest first."""
        return list(reversed(self._records))

    def get(self, index: int) -> Optional[PreviewRecord]:
        """Return record by index (0 = newest).  None if out of range."""
        items = self.get_all()
        if 0 <= index < len(items):
            return items[index]
        return None

    def update_timestamp(self, index: int, timestamp: str) -> None:
        """Set the timestamp for a record (e.g. after a cancel record is confirmed).

        *index* is newest-first (0 = newest).
        """
        reversed_idx = len(self._records) - 1 - index
        if 0 <= reversed_idx < len(self._records):
            self._records[reversed_idx].timestamp = timestamp

    def move_to_front(self, index: int) -> None:
        """Move a record to the newest position (end of internal list).

        *index* is newest-first (0 = already newest, no-op).
        """
        if index <= 0:
            return
        reversed_idx = len(self._records) - 1 - index
        if 0 <= reversed_idx < len(self._records):
            record = self._records.pop(reversed_idx)
            self._records.append(record)

    def count(self) -> int:
        return len(self._records)

    def clear(self) -> None:
        self._records.clear()
