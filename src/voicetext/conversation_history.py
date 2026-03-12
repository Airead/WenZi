"""Conversation history for tracking ASR sessions and providing context to AI enhancement."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)


class ConversationHistory:
    """Append-only JSONL logger and reader for conversation history."""

    def __init__(self, config_dir: str = DEFAULT_CONFIG_DIR) -> None:
        self._config_dir = os.path.expanduser(config_dir)
        self._history_path = os.path.join(self._config_dir, "conversation_history.jsonl")

    def log(
        self,
        asr_text: str,
        enhanced_text: Optional[str],
        final_text: str,
        enhance_mode: str,
        preview_enabled: bool,
    ) -> None:
        """Write a single conversation record to the JSONL file."""
        os.makedirs(self._config_dir, exist_ok=True)

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "asr_text": asr_text,
            "enhanced_text": enhanced_text,
            "final_text": final_text,
            "enhance_mode": enhance_mode,
            "preview_enabled": preview_enabled,
        }

        with open(self._history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.debug("Conversation logged: %s", self._history_path)

    def count(self) -> int:
        """Return the number of conversation records in the log file."""
        if not os.path.exists(self._history_path):
            return 0
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except Exception:
            return 0

    def get_recent(self, n: Optional[int] = None, max_entries: int = 10) -> List[Dict[str, Any]]:
        """Read the most recent N preview_enabled=true records.

        Args:
            n: Number of records to return. Defaults to max_entries.
            max_entries: Default number of records when n is not specified.

        Returns:
            List of record dicts, oldest first.
        """
        count = n if n is not None else max_entries

        if not os.path.exists(self._history_path):
            return []

        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logger.warning("Failed to read conversation history: %s", e)
            return []

        # Parse lines in reverse, collect preview_enabled=true records
        results: List[Dict[str, Any]] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("preview_enabled") is True:
                results.append(record)
                if len(results) >= count:
                    break

        # Return oldest first
        results.reverse()
        return results

    def format_for_prompt(self, entries: List[Dict[str, Any]]) -> str:
        """Format conversation history entries for injection into LLM prompt.

        Args:
            entries: List of record dicts from get_recent().

        Returns:
            Formatted string for system prompt, or empty string if no entries.
        """
        if not entries:
            return ""

        lines = [
            "---",
            "以下是用户近期的对话记录，用于学习纠错偏好和话题上下文。",
            "若 ASR 识别与最终确认不同则用→分隔（识别→确认），相同则表示无需纠错：",
            "",
        ]
        for entry in entries:
            asr = entry.get("asr_text", "")
            final = entry.get("final_text", "")
            if asr == final:
                lines.append(f"- {final}")
            else:
                lines.append(f"- {asr} → {final}")

        lines.append("---")
        return "\n".join(lines)
