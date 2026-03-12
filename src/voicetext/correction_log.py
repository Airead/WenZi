"""Correction log for collecting user-edited AI enhancement data."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from .config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)


class CorrectionLogger:
    """Append-only JSONL logger for user corrections to AI-enhanced text."""

    def __init__(self, log_dir: str = DEFAULT_CONFIG_DIR) -> None:
        self._log_dir = os.path.expanduser(log_dir)
        self._log_path = os.path.join(self._log_dir, "corrections.jsonl")

    def log(
        self,
        asr_text: str,
        enhanced_text: str,
        final_text: str,
        enhance_mode: str,
    ) -> None:
        """Write a single correction record to the JSONL file."""
        os.makedirs(self._log_dir, exist_ok=True)

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "asr_text": asr_text,
            "enhanced_text": enhanced_text,
            "final_text": final_text,
            "enhance_mode": enhance_mode,
        }

        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.debug("Correction logged: %s", self._log_path)

    def count(self) -> int:
        """Return the number of correction records in the log file."""
        if not os.path.exists(self._log_path):
            return 0
        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except Exception:
            return 0
