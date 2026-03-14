"""Conversation history for tracking ASR sessions and providing context to AI enhancement."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from voicetext.config import DEFAULT_CONFIG_DIR

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
        stt_model: str = "",
        llm_model: str = "",
        user_corrected: bool = False,
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
            "stt_model": stt_model,
            "llm_model": llm_model,
            "user_corrected": user_corrected,
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

    def correction_count(self) -> int:
        """Return the number of user-corrected records."""
        if not os.path.exists(self._history_path):
            return 0
        try:
            count = 0
            with open(self._history_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if self._is_corrected(record):
                        count += 1
            return count
        except Exception:
            return 0

    def get_corrections(self, since: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return records where the user made corrections.

        For records with an explicit ``user_corrected`` field, that value is
        used.  For legacy records without the field, correction is inferred
        when ``enhanced_text`` differs from ``final_text``.

        Args:
            since: If provided, only return records with timestamp > since.

        Returns:
            List of correction records in chronological order.
        """
        if not os.path.exists(self._history_path):
            return []

        records: List[Dict[str, Any]] = []
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if since and record.get("timestamp", "") <= since:
                        continue
                    if self._is_corrected(record):
                        records.append(record)
        except Exception as e:
            logger.warning("Failed to read corrections: %s", e)

        return records

    @staticmethod
    def _is_corrected(record: Dict[str, Any]) -> bool:
        """Determine whether a record represents a user correction.

        Uses the explicit ``user_corrected`` field when present; otherwise
        falls back to comparing ``enhanced_text`` with ``final_text``.
        """
        if "user_corrected" in record:
            return bool(record["user_corrected"])
        enhanced = record.get("enhanced_text")
        final = record.get("final_text")
        return enhanced is not None and enhanced != final

    # Skip history entries whose final_text exceeds this length (e.g. clipboard
    # enhance with large input).  Long texts bloat the system prompt and add
    # little value as correction context.
    _MAX_TEXT_LENGTH_FOR_CONTEXT = 500

    def get_recent(self, n: Optional[int] = None, max_entries: int = 10) -> List[Dict[str, Any]]:
        """Read the most recent N preview_enabled=true records.

        Records whose ``final_text`` exceeds ``_MAX_TEXT_LENGTH_FOR_CONTEXT``
        characters are skipped so that large clipboard-enhance entries do not
        bloat the LLM context.

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
                final = record.get("final_text", "")
                if len(final) > self._MAX_TEXT_LENGTH_FOR_CONTEXT:
                    continue
                results.append(record)
                if len(results) >= count:
                    break

        # Return oldest first
        results.reverse()
        return results

    def get_all(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return all records (no filtering), newest first.

        Args:
            limit: Maximum number of records to return.

        Returns:
            List of record dicts, newest first.
        """
        if not os.path.exists(self._history_path):
            return []

        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logger.warning("Failed to read conversation history: %s", e)
            return []

        results: List[Dict[str, Any]] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            results.append(record)
            if len(results) >= limit:
                break

        return results

    def update_final_text(self, timestamp: str, new_final_text: str) -> bool:
        """Update the final_text of a record identified by timestamp.

        Uses atomic file replacement via a temporary file + os.replace().

        Args:
            timestamp: The ISO timestamp identifying the record.
            new_final_text: The new final_text value.

        Returns:
            True if record was found and updated, False otherwise.
        """
        if not os.path.exists(self._history_path):
            return False

        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logger.warning("Failed to read conversation history: %s", e)
            return False

        found = False
        new_lines: List[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                new_lines.append(line)
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                new_lines.append(line)
                continue

            if record.get("timestamp") == timestamp and not found:
                record["final_text"] = new_final_text
                record["edited_at"] = datetime.now(timezone.utc).isoformat()
                new_lines.append(json.dumps(record, ensure_ascii=False) + "\n")
                found = True
            else:
                new_lines.append(line)

        if not found:
            return False

        tmp_path = self._history_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        os.replace(tmp_path, self._history_path)
        return True

    def search(self, query: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Search records by case-insensitive substring match on text fields.

        Args:
            query: Search string (case-insensitive).
            limit: Maximum number of results.

        Returns:
            Matching records, newest first.
        """
        if not os.path.exists(self._history_path):
            return []

        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logger.warning("Failed to read conversation history: %s", e)
            return []

        query_lower = query.lower()
        results: List[Dict[str, Any]] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            searchable = " ".join(
                str(record.get(k, ""))
                for k in ("asr_text", "enhanced_text", "final_text")
            ).lower()
            if query_lower in searchable:
                results.append(record)
                if len(results) >= limit:
                    break

        return results

    # Maximum total characters for formatted history injected into the
    # system prompt.  Keeps token usage bounded regardless of entry count.
    _MAX_PROMPT_CHARS = 2000

    def format_for_prompt(
        self, entries: List[Dict[str, Any]], max_chars: int = 0
    ) -> str:
        """Format conversation history entries for injection into LLM prompt.

        Entries are added from newest to oldest.  Once the total formatted
        length would exceed *max_chars*, older entries are dropped.

        Args:
            entries: List of record dicts from get_recent(), oldest first.
            max_chars: Maximum total characters for the output.  Defaults to
                ``_MAX_PROMPT_CHARS`` when 0 or negative.

        Returns:
            Formatted string for system prompt, or empty string if no entries.
        """
        if not entries:
            return ""

        if max_chars <= 0:
            max_chars = self._MAX_PROMPT_CHARS

        header_lines = [
            "---",
            "以下是用户近期的对话记录，用于学习纠错偏好和话题上下文。",
            "若 ASR 识别与最终确认不同则用→分隔（识别→确认），相同则表示无需纠错：",
            "",
        ]
        footer = "---"
        header_text = "\n".join(header_lines) + "\n"
        overhead = len(header_text) + len(footer) + 1  # +1 for trailing \n

        # Format all entry lines, then select from newest backwards
        formatted: List[str] = []
        for entry in entries:
            asr = entry.get("asr_text", "").replace("\n", "\u23ce")
            final = entry.get("final_text", "").replace("\n", "\u23ce")
            if asr == final:
                formatted.append(f"- {final}")
            else:
                formatted.append(f"- {asr} → {final}")

        # Select entries from newest (end) to oldest, respecting budget
        budget = max_chars - overhead
        selected: List[str] = []
        for line in reversed(formatted):
            # +1 for the newline after the entry line
            cost = len(line) + 1
            if budget - cost < 0 and selected:
                break
            selected.append(line)
            budget -= cost

        if not selected:
            return ""

        selected.reverse()
        return header_text + "\n".join(selected) + "\n" + footer
