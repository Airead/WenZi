"""In-memory preview history store for quick access to recent preview results."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wenzi.input_context import InputContext

logger = logging.getLogger(__name__)


@dataclass
class PreviewRecord:
    """A single preview result cached in memory.

    WAV audio is stored in a temporary file to avoid keeping large
    byte buffers in RAM.  Use :meth:`load_wav_data` to read the bytes
    back and :attr:`wav_path` to check whether audio is available.
    """

    timestamp: str | None  # None = from cancel (not in ConversationHistory)
    created_at: str  # always set — local ISO time for display
    action: str  # "confirm" | "copy" | "cancel"
    asr_text: str
    enhanced_text: str | None
    final_text: str
    enhance_mode: str
    stt_model: str
    llm_model: str
    wav_data: bytes | None  # deprecated — use wav_path / load_wav_data()
    audio_duration: float
    source: str  # "voice" | "clipboard"
    system_prompt: str = ""
    thinking_text: str = ""
    token_usage: dict | None = None
    hotwords_detail: list = field(default_factory=list)  # List[HotwordDetail]
    input_context: InputContext | None = None
    wav_path: str | None = None

    def __post_init__(self) -> None:
        """If wav_data bytes were provided, spill them to a temp file."""
        if self.wav_data is not None and self.wav_path is None:
            try:
                fd, path = tempfile.mkstemp(suffix=".wav", prefix="wenzi_preview_")
                try:
                    os.write(fd, self.wav_data)
                finally:
                    os.close(fd)
                self.wav_path = path
                self.wav_data = None  # only clear after successful write
            except Exception as e:
                logger.warning("Failed to write WAV temp file: %s", e)

    def load_wav_data(self) -> bytes | None:
        """Read WAV bytes from the backing temp file, or return None."""
        if self.wav_path is None:
            return self.wav_data
        try:
            with open(self.wav_path, "rb") as f:
                return f.read()
        except Exception as e:
            logger.warning("Failed to read WAV temp file %s: %s", self.wav_path, e)
            return None

    def cleanup_wav(self) -> None:
        """Delete the backing WAV temp file if it exists."""
        if self.wav_path is not None:
            try:
                os.unlink(self.wav_path)
            except OSError:
                pass
            self.wav_path = None


class PreviewHistoryStore:
    """Session-scoped in-memory store for preview results.

    Keeps the most recent *max_size* records.  Oldest records are
    discarded when the limit is exceeded.  Not persisted — cleared
    on application restart.

    WAV audio data is stored in temporary files rather than in-memory
    bytes to limit RAM usage.  Temp files are deleted when records are
    evicted or when :meth:`clear` / :meth:`shutdown` is called.
    """

    def __init__(self, max_size: int = 10) -> None:
        self._records: list[PreviewRecord] = []
        self._max_size = max_size

    def add(self, record: PreviewRecord) -> None:
        """Append a record.  Drops the oldest if at capacity."""
        self._records.append(record)
        if len(self._records) > self._max_size:
            evicted = self._records.pop(0)
            evicted.cleanup_wav()

    def get_all(self) -> list[PreviewRecord]:
        """Return all records, newest first."""
        return list(reversed(self._records))

    def get(self, index: int) -> PreviewRecord | None:
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
        """Remove all records and clean up their temp files."""
        for record in self._records:
            record.cleanup_wav()
        self._records.clear()

    def shutdown(self) -> None:
        """Clean up all temp files.  Call on application quit."""
        self.clear()
