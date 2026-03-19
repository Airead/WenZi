"""Conversation history for tracking ASR sessions and providing context to AI enhancement."""

from __future__ import annotations

import glob
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from wenzi.config import DEFAULT_DATA_DIR
from wenzi.enhance.text_diff import inline_diff

logger = logging.getLogger(__name__)


class ConversationHistory:
    """Append-only JSONL logger and reader for conversation history.

    Maintains two levels of in-memory cache to avoid repeated disk reads:

    * ``_cache`` — the most recent ``_CACHE_SIZE`` raw records (unfiltered).
      Populated lazily on the first ``get_recent()`` call.  Kept in sync by
      ``log()``, ``update_record()``, and ``delete_record()``.  Invalidated
      by ``_maybe_rotate()``.

    * ``_full_cache`` — *all* parsed records from the JSONL file, used by
      ``get_all()`` and ``search()``.  Populated lazily on the first call.
      Staleness is detected via file mtime; callers (e.g. History Browser)
      should call ``release_full_cache()`` when they no longer need it so
      that memory is freed promptly.

    Archived records are stored in monthly JSONL files under
    ``conversation_history_archives/YYYY-MM.jsonl``.
    """

    _MAX_RECORDS = 20000
    _ROTATE_SIZE_THRESHOLD = 4 * 1024 * 1024  # 4 MB — cheap pre-check
    _CACHE_SIZE = 200

    def __init__(self, data_dir: str = DEFAULT_DATA_DIR) -> None:
        self._data_dir = os.path.expanduser(data_dir)
        self._history_path = os.path.join(self._data_dir, "conversation_history.jsonl")
        self._archive_dir = os.path.join(self._data_dir, "conversation_history_archives")

        # Hot-path cache: most recent _CACHE_SIZE raw records (oldest first)
        self._cache: Optional[List[Dict[str, Any]]] = None

        # Full cache: all parsed records (oldest first), for get_all/search
        self._full_cache: Optional[List[Dict[str, Any]]] = None
        self._full_cache_mtime: float = 0.0

        # Monotonically increasing counter for change detection.
        # Bumped on every log() call so that consumers (e.g. TextEnhancer)
        # can cheaply detect whether new entries were appended.
        self._log_count: int = 0

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def log_count(self) -> int:
        """Number of ``log()`` calls since this instance was created.

        Used by :class:`TextEnhancer` as a cheap O(1) change-detection
        signal to avoid unnecessary ``get_recent()`` calls.
        """
        return self._log_count

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _ensure_cache(self) -> List[Dict[str, Any]]:
        """Return the hot-path cache, loading from disk on first access."""
        if self._cache is None:
            self._cache = self._load_tail(self._CACHE_SIZE)
        return self._cache

    def _load_tail(self, n: int) -> List[Dict[str, Any]]:
        """Load the last *n* valid records from the JSONL file."""
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
            if len(results) >= n:
                break
        results.reverse()
        return results

    def _ensure_full_cache(self) -> List[Dict[str, Any]]:
        """Return the full cache, reloading from disk if the file changed."""
        try:
            mtime = os.path.getmtime(self._history_path)
        except OSError:
            return []

        if self._full_cache is not None and mtime == self._full_cache_mtime:
            return self._full_cache

        self._full_cache = self._load_all_records()
        self._full_cache_mtime = mtime
        return self._full_cache

    def _load_all_records(self) -> List[Dict[str, Any]]:
        """Parse every valid record from the JSONL file (oldest first)."""
        if not os.path.exists(self._history_path):
            return []
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logger.warning("Failed to read conversation history: %s", e)
            return []

        records: List[Dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def release_full_cache(self) -> None:
        """Release the full in-memory cache to free memory.

        Should be called when the History Browser is closed so that the
        (potentially large) record list is not kept alive unnecessarily.
        """
        self._full_cache = None
        self._full_cache_mtime = 0.0

    def _update_cache_record(self, timestamp: str, fields: Dict[str, Any]) -> None:
        """Update matching record in both caches (if loaded)."""
        for cache in (self._cache, self._full_cache):
            if cache is None:
                continue
            for rec in cache:
                if rec.get("timestamp") == timestamp:
                    rec.update(fields)
                    break

    def _delete_cache_record(self, timestamp: str) -> None:
        """Remove matching record from both caches (if loaded)."""
        if self._cache is not None:
            self._cache = [r for r in self._cache if r.get("timestamp") != timestamp]
        if self._full_cache is not None:
            self._full_cache = [r for r in self._full_cache if r.get("timestamp") != timestamp]

    def _append_cache_record(self, record: Dict[str, Any]) -> None:
        """Append a new record to both caches (if loaded)."""
        if self._cache is not None:
            self._cache.append(record)
            if len(self._cache) > self._CACHE_SIZE:
                self._cache = self._cache[-self._CACHE_SIZE:]
        if self._full_cache is not None:
            self._full_cache.append(record)
            self._full_cache_mtime = self._get_mtime()

    def _invalidate_caches(self) -> None:
        """Invalidate both caches (e.g. after rotation)."""
        self._cache = None
        self._full_cache = None
        self._full_cache_mtime = 0.0

    def _get_mtime(self) -> float:
        """Return the current mtime of the history file, or 0.0."""
        try:
            return os.path.getmtime(self._history_path)
        except OSError:
            return 0.0

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

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
        audio_duration: float = 0.0,
    ) -> str:
        """Write a single conversation record to the JSONL file.

        Returns:
            The ISO timestamp of the logged record.
        """
        os.makedirs(self._data_dir, exist_ok=True)

        ts = datetime.now(timezone.utc).isoformat()
        record = {
            "timestamp": ts,
            "asr_text": asr_text,
            "enhanced_text": enhanced_text,
            "final_text": final_text,
            "enhance_mode": enhance_mode,
            "preview_enabled": preview_enabled,
            "stt_model": stt_model,
            "llm_model": llm_model,
            "user_corrected": user_corrected,
            "audio_duration": round(audio_duration, 1),
        }

        with open(self._history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.debug("Conversation logged: %s", self._history_path)

        self._append_cache_record(record)
        self._log_count += 1
        self._maybe_rotate()
        return ts

    def _maybe_rotate(self) -> None:
        """Archive old records when the history file exceeds _MAX_RECORDS.

        Old records are grouped by month (from their timestamp) and appended
        to ``conversation_history_archives/YYYY-MM.jsonl``.
        """
        try:
            size = os.path.getsize(self._history_path)
        except OSError:
            return
        if size < self._ROTATE_SIZE_THRESHOLD:
            return

        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            return

        if len(lines) <= self._MAX_RECORDS:
            return

        old_lines = lines[: len(lines) - self._MAX_RECORDS]
        keep_lines = lines[len(lines) - self._MAX_RECORDS :]

        # Group old lines by month and append to per-month archive files
        os.makedirs(self._archive_dir, exist_ok=True)
        monthly_groups: Dict[str, List[str]] = {}
        for line in old_lines:
            month_key = self._extract_month(line)
            monthly_groups.setdefault(month_key, []).append(line)

        for month_key, group_lines in sorted(monthly_groups.items()):
            archive_path = os.path.join(self._archive_dir, f"{month_key}.jsonl")
            with open(archive_path, "a", encoding="utf-8") as f:
                f.writelines(group_lines)

        # Rewrite main file with recent records only
        tmp_path = self._history_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(keep_lines)
        os.replace(tmp_path, self._history_path)

        self._invalidate_caches()

        archived_count = len(old_lines)
        logger.info(
            "Rotated conversation history: archived %d records across %d months, kept %d",
            archived_count, len(monthly_groups), len(keep_lines),
        )

    @staticmethod
    def _extract_month(line: str) -> str:
        """Extract YYYY-MM from a JSONL line's timestamp, fallback to 'unknown'."""
        try:
            record = json.loads(line.strip())
            ts = record.get("timestamp", "")
            if len(ts) >= 7:
                return ts[:7]  # "YYYY-MM"
        except (json.JSONDecodeError, AttributeError):
            pass
        return "unknown"

    def _list_archive_files(self) -> List[str]:
        """Return sorted list of archive file paths (oldest first)."""
        if not os.path.isdir(self._archive_dir):
            return []
        pattern = os.path.join(self._archive_dir, "*.jsonl")
        files = glob.glob(pattern)
        files.sort()  # Lexicographic sort on YYYY-MM gives chronological order
        return files

    def _load_archive_records(self) -> List[Dict[str, Any]]:
        """Load all records from archive files (oldest first)."""
        records: List[Dict[str, Any]] = []
        for path in self._list_archive_files():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.warning("Failed to read archive %s: %s", path, e)
        return records

    # ------------------------------------------------------------------
    # Read operations (low-frequency, no cache)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Read operations (hot-path, cached)
    # ------------------------------------------------------------------

    # Skip history entries whose final_text exceeds this length (e.g. clipboard
    # enhance with large input).  Long texts bloat the system prompt and add
    # little value as correction context.
    _MAX_TEXT_LENGTH_FOR_CONTEXT = 500

    def get_recent(
        self,
        n: Optional[int] = None,
        max_entries: int = 10,
        enhance_mode: str = "",
    ) -> List[Dict[str, Any]]:
        """Read the most recent N preview_enabled=true records.

        Records whose ``final_text`` exceeds ``_MAX_TEXT_LENGTH_FOR_CONTEXT``
        characters are skipped so that large clipboard-enhance entries do not
        bloat the LLM context.

        Uses the in-memory cache to avoid disk reads on the hot path.

        Args:
            n: Number of records to return. Defaults to max_entries.
            max_entries: Default number of records when n is not specified.
            enhance_mode: If non-empty, only return records matching this mode.

        Returns:
            List of record dicts, oldest first.
        """
        count = n if n is not None else max_entries
        cache = self._ensure_cache()

        results: List[Dict[str, Any]] = []
        for record in reversed(cache):
            if record.get("preview_enabled") is not True:
                continue
            final = record.get("final_text", "")
            if len(final) > self._MAX_TEXT_LENGTH_FOR_CONTEXT:
                continue
            if enhance_mode and record.get("enhance_mode") != enhance_mode:
                continue
            results.append(record)
            if len(results) >= count:
                break

        results.reverse()
        return results

    # ------------------------------------------------------------------
    # Read operations (full cache, for UI)
    # ------------------------------------------------------------------

    def get_all(
        self, limit: int = 0, include_archived: bool = False
    ) -> List[Dict[str, Any]]:
        """Return all records (no filtering), newest first.

        Uses the full in-memory cache; call ``release_full_cache()`` when
        the data is no longer needed to free memory.

        Args:
            limit: Maximum number of records to return.  0 means no limit.
            include_archived: If True, also include records from archive files.

        Returns:
            List of record dicts, newest first.
        """
        cache = self._ensure_full_cache()

        if include_archived:
            archived = self._load_archive_records()
            combined = archived + (cache or [])
        else:
            combined = cache or []

        if not combined:
            return []

        if limit <= 0:
            return list(reversed(combined))

        results: List[Dict[str, Any]] = []
        for record in reversed(combined):
            results.append(record)
            if len(results) >= limit:
                break
        return results

    def search(
        self, query: str, limit: int = 0, include_archived: bool = False
    ) -> List[Dict[str, Any]]:
        """Search records by case-insensitive substring match on text fields.

        Uses the full in-memory cache; call ``release_full_cache()`` when
        the data is no longer needed to free memory.

        Args:
            query: Search string (case-insensitive).
            limit: Maximum number of results.  0 means no limit.
            include_archived: If True, also search records from archive files.

        Returns:
            Matching records, newest first.
        """
        cache = self._ensure_full_cache()

        if include_archived:
            archived = self._load_archive_records()
            combined = archived + (cache or [])
        else:
            combined = cache or []

        if not combined:
            return []

        query_lower = query.lower()
        results: List[Dict[str, Any]] = []
        for record in reversed(combined):
            searchable = " ".join(
                str(record.get(k, ""))
                for k in ("asr_text", "enhanced_text", "final_text")
            ).lower()
            if query_lower in searchable:
                results.append(record)
                if limit > 0 and len(results) >= limit:
                    break

        return results

    # ------------------------------------------------------------------
    # Update / delete
    # ------------------------------------------------------------------

    def update_final_text(self, timestamp: str, new_final_text: str) -> bool:
        """Update the final_text of a record identified by timestamp.

        Uses atomic file replacement via a temporary file + os.replace().

        Args:
            timestamp: The ISO timestamp identifying the record.
            new_final_text: The new final_text value.

        Returns:
            True if record was found and updated, False otherwise.
        """
        return self.update_record(timestamp, final_text=new_final_text)

    def update_record(self, timestamp: str, **fields: Any) -> bool:
        """Update one or more fields of a record identified by timestamp.

        Sets ``edited_at`` automatically.  Uses atomic file replacement.

        Args:
            timestamp: The ISO timestamp identifying the record.
            **fields: Field names and new values to set (e.g.
                ``final_text="new"``, ``enhance_mode="translate"``).

        Returns:
            True if record was found and updated, False otherwise.
        """
        if not fields:
            return False
        if not os.path.exists(self._history_path):
            return False

        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logger.warning("Failed to read conversation history: %s", e)
            return False

        found = False
        edit_ts = datetime.now(timezone.utc).isoformat()
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
                for key, value in fields.items():
                    record[key] = value
                record["edited_at"] = edit_ts
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

        cache_fields = {**fields, "edited_at": edit_ts}
        self._update_cache_record(timestamp, cache_fields)
        if self._full_cache is not None:
            self._full_cache_mtime = self._get_mtime()

        return True

    def delete_record(self, timestamp: str) -> bool:
        """Delete a record identified by timestamp.

        Uses atomic file replacement via a temporary file + os.replace().

        Args:
            timestamp: The ISO timestamp identifying the record.

        Returns:
            True if record was found and deleted, False otherwise.
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
                found = True
                # Skip this line (delete)
            else:
                new_lines.append(line)

        if not found:
            return False

        tmp_path = self._history_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        os.replace(tmp_path, self._history_path)

        self._delete_cache_record(timestamp)
        if self._full_cache is not None:
            self._full_cache_mtime = self._get_mtime()

        return True

    # ------------------------------------------------------------------
    # Prompt formatting
    # ------------------------------------------------------------------

    # Header and footer used by format_for_prompt (standalone formatting).
    # The incremental builder in TextEnhancer uses its own combined header
    # via _context_section_header().
    HISTORY_PROMPT_HEADER = (
        "---\n"
        "以下是用户近期的对话记录，用于学习纠错偏好和话题上下文。\n"
        "差异部分以[误→正]标注，无标注表示该部分无需纠错：\n"
        "\n"
    )
    HISTORY_PROMPT_FOOTER = "---"

    # Maximum total characters for formatted history injected into the
    # system prompt.  Keeps token usage bounded regardless of entry count.
    _MAX_PROMPT_CHARS = 2000

    @staticmethod
    def format_entry_line(entry: Dict[str, Any]) -> str:
        """Format a single history entry as a prompt line.

        Uses inline diff notation: unchanged text appears as-is, and
        replacements are bracketed as ``[old→new]``.  Newlines in the
        text are replaced with the ⏎ symbol.
        """
        asr = entry.get("asr_text", "").replace("\n", "\u23ce")
        final = entry.get("final_text", "").replace("\n", "\u23ce")
        return f"- {inline_diff(asr, final)}"

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

        header_text = self.HISTORY_PROMPT_HEADER
        footer = self.HISTORY_PROMPT_FOOTER
        overhead = len(header_text) + len(footer) + 1  # +1 for trailing \n

        formatted = [self.format_entry_line(e) for e in entries]

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
