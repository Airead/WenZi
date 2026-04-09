"""Usage statistics tracking for WenZi."""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from .config import DEFAULT_DATA_DIR

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL = 30  # seconds


def _empty_totals() -> dict[str, int | float]:
    return {
        "transcriptions": 0,
        "direct_mode": 0,
        "preview_mode": 0,
        "direct_accept": 0,
        "user_modification": 0,
        "cancel": 0,
        "clipboard_enhances": 0,
        "clipboard_enhance_confirm": 0,
        "clipboard_enhance_cancel": 0,
        "output_type_text": 0,
        "output_copy_clipboard": 0,
        "google_translate_opens": 0,
        "sound_feedback_plays": 0,
        "history_browse_opens": 0,
        "history_edits": 0,
        "recording_seconds": 0.0,
        "system_settings_opened": 0,
        "correction_pairs_recorded": 0,
        "correction_asr_hotwords_injected": 0,
        "correction_llm_vocab_injected": 0,
    }


def _empty_token_usage() -> dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
    }


def _empty_cumulative() -> dict[str, Any]:
    return {
        "version": 1,
        "first_recorded": None,
        "last_updated": None,
        "totals": _empty_totals(),
        "token_usage": _empty_token_usage(),
        "enhance_mode_usage": {},
    }


def _empty_daily(day: str) -> dict[str, Any]:
    return {
        "date": day,
        "totals": _empty_totals(),
        "token_usage": _empty_token_usage(),
        "enhance_mode_usage": {},
    }


class UsageStats:
    """Thread-safe usage statistics with cumulative + daily in-memory storage.

    Data is kept in memory and flushed to disk periodically (every 30s)
    and on shutdown, rather than on every single event.
    """

    def __init__(self, data_dir: str = DEFAULT_DATA_DIR) -> None:
        self._base_dir = os.path.expanduser(data_dir)
        self._cumulative_path = os.path.join(self._base_dir, "usage_stats.json")
        self._daily_dir = os.path.join(self._base_dir, "usage_stats")
        self._lock = threading.Lock()

        # In-memory state — loaded lazily on first access
        self._cumulative: dict[str, Any] | None = None
        self._daily: dict[str, Any] | None = None
        self._daily_date: str | None = None
        self._dirty = False

        # Periodic flush timer
        self._flush_timer: threading.Timer | None = None
        self._stopped = False

    def _daily_path(self, day: str) -> str:
        return os.path.join(self._daily_dir, f"{day}.json")

    def _read_json(self, path: str) -> dict[str, Any] | None:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def _write_json(self, path: str, data: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    def _ensure_loaded(self) -> None:
        """Load data from disk into memory if not yet loaded. Must hold _lock."""
        if self._cumulative is None:
            data = self._read_json(self._cumulative_path)
            if data is None:
                self._cumulative = _empty_cumulative()
            else:
                self._cumulative = self._backfill(data)

        day = self._today()
        if self._daily is None or self._daily_date != day:
            # Day changed — flush old daily data first, then load new day
            if self._daily is not None and self._daily_date != day and self._dirty:
                self._flush_locked()
            self._daily_date = day
            data = self._read_json(self._daily_path(day))
            if data is None:
                self._daily = _empty_daily(day)
            else:
                self._daily = self._backfill_daily(data, day)

    @staticmethod
    def _backfill(data: dict[str, Any]) -> dict[str, Any]:
        """Ensure all expected keys exist in cumulative data."""
        for key, factory in [("totals", _empty_totals), ("token_usage", _empty_token_usage)]:
            if key not in data or not isinstance(data[key], dict):
                data[key] = factory()
            else:
                for k, v in factory().items():
                    data[key].setdefault(k, v)
        data.setdefault("enhance_mode_usage", {})
        return data

    @staticmethod
    def _backfill_daily(data: dict[str, Any], day: str) -> dict[str, Any]:
        """Ensure all expected keys exist in daily data."""
        data["date"] = day
        for key, factory in [("totals", _empty_totals), ("token_usage", _empty_token_usage)]:
            if key not in data or not isinstance(data[key], dict):
                data[key] = factory()
            else:
                for k, v in factory().items():
                    data[key].setdefault(k, v)
        data.setdefault("enhance_mode_usage", {})
        return data

    def _now_iso(self) -> str:
        return datetime.now(UTC).isoformat()

    def _today(self) -> str:
        return date.today().isoformat()

    def _start_flush_timer(self) -> None:
        """Schedule the next periodic flush. Must hold _lock."""
        if self._stopped:
            return
        self._flush_timer = threading.Timer(_FLUSH_INTERVAL, self._periodic_flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _cancel_flush_timer(self) -> None:
        """Cancel any pending flush timer. Must hold _lock."""
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None

    def _periodic_flush(self) -> None:
        """Called by the timer thread to flush dirty data."""
        with self._lock:
            if self._dirty and not self._stopped:
                self._flush_locked()
            if not self._stopped:
                self._start_flush_timer()

    def _flush_locked(self) -> None:
        """Write in-memory data to disk. Must hold _lock."""
        if self._cumulative is not None:
            self._write_json(self._cumulative_path, self._cumulative)
        if self._daily is not None and self._daily_date is not None:
            self._write_json(self._daily_path(self._daily_date), self._daily)
        self._dirty = False

    def flush(self) -> None:
        """Write in-memory data to disk."""
        with self._lock:
            if self._dirty:
                self._flush_locked()

    def shutdown(self) -> None:
        """Cancel the flush timer and write any pending data to disk."""
        with self._lock:
            self._stopped = True
            self._cancel_flush_timer()
            if self._dirty:
                self._flush_locked()

    def _record(
        self,
        updater: Callable[[dict[str, Any]], None],
        set_first_recorded: bool = False,
    ) -> None:
        """Apply *updater* to both cumulative and daily data in memory."""
        with self._lock:
            self._ensure_loaded()
            now = self._now_iso()

            assert self._cumulative is not None  # noqa: S101 — guaranteed by _ensure_loaded
            assert self._daily is not None  # noqa: S101

            for data in (self._cumulative, self._daily):
                updater(data)

            if set_first_recorded and self._cumulative.get("first_recorded") is None:
                self._cumulative["first_recorded"] = now
            self._cumulative["last_updated"] = now

            if not self._dirty:
                self._dirty = True
                # Start the periodic flush timer on first dirty write
                if self._flush_timer is None and not self._stopped:
                    self._start_flush_timer()

    def record_transcription(self, mode: str, enhance_mode: str = "") -> None:
        """Record a transcription event. mode is 'direct' or 'preview'."""
        def _update(data: dict[str, Any]) -> None:
            data["totals"]["transcriptions"] += 1
            if mode == "direct":
                data["totals"]["direct_mode"] += 1
            elif mode == "preview":
                data["totals"]["preview_mode"] += 1
            if enhance_mode and enhance_mode != "off":
                data.setdefault("enhance_mode_usage", {})
                data["enhance_mode_usage"][enhance_mode] = (
                    data["enhance_mode_usage"].get(enhance_mode, 0) + 1
                )

        self._record(_update, set_first_recorded=True)

    def record_confirm(self, modified: bool) -> None:
        """Record user confirmation. modified=True means user edited before confirming."""
        key = "user_modification" if modified else "direct_accept"
        self._record(lambda data: data["totals"].__setitem__(key, data["totals"][key] + 1))

    def record_cancel(self) -> None:
        """Record user cancellation of preview."""
        self._record(lambda data: data["totals"].__setitem__("cancel", data["totals"]["cancel"] + 1))

    def record_token_usage(self, usage: dict | None) -> None:
        """Record LLM token consumption. usage should have prompt_tokens, completion_tokens, total_tokens."""
        if not usage:
            return

        def _update(data: dict[str, Any]) -> None:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cache_read_tokens"):
                val = usage.get(key, 0)
                if val:
                    data["token_usage"][key] += val

        self._record(_update)

    def record_clipboard_enhance(self, enhance_mode: str = "") -> None:
        """Record a clipboard enhance trigger."""
        def _update(data: dict[str, Any]) -> None:
            data["totals"]["clipboard_enhances"] += 1
            if enhance_mode and enhance_mode != "off":
                data.setdefault("enhance_mode_usage", {})
                data["enhance_mode_usage"][enhance_mode] = (
                    data["enhance_mode_usage"].get(enhance_mode, 0) + 1
                )

        self._record(_update, set_first_recorded=True)

    def record_clipboard_confirm(self) -> None:
        """Record clipboard enhance confirmation."""
        self._record(lambda data: data["totals"].__setitem__(
            "clipboard_enhance_confirm", data["totals"]["clipboard_enhance_confirm"] + 1
        ))

    def record_clipboard_cancel(self) -> None:
        """Record clipboard enhance cancellation."""
        self._record(lambda data: data["totals"].__setitem__(
            "clipboard_enhance_cancel", data["totals"]["clipboard_enhance_cancel"] + 1
        ))

    def record_google_translate_open(self) -> None:
        """Record a Google Translate WebView open event."""
        self._record(lambda data: data["totals"].__setitem__(
            "google_translate_opens", data["totals"]["google_translate_opens"] + 1
        ))

    def record_sound_feedback(self) -> None:
        """Record a sound feedback play event."""
        self._record(lambda data: data["totals"].__setitem__(
            "sound_feedback_plays", data["totals"]["sound_feedback_plays"] + 1
        ))

    def record_history_browse_open(self) -> None:
        """Record a history browser open event."""
        self._record(lambda data: data["totals"].__setitem__(
            "history_browse_opens", data["totals"]["history_browse_opens"] + 1
        ))

    def record_history_edit(self) -> None:
        """Record a history edit (final_text update) event."""
        self._record(lambda data: data["totals"].__setitem__(
            "history_edits", data["totals"]["history_edits"] + 1
        ))

    def record_recording_duration(self, seconds: float) -> None:
        """Record audio recording duration in seconds."""
        if seconds <= 0:
            return
        self._record(lambda data: data["totals"].__setitem__(
            "recording_seconds", data["totals"]["recording_seconds"] + seconds
        ))

    def record_system_settings_open(self) -> None:
        """Record a system settings pane opened from the chooser."""
        self._record(lambda data: data["totals"].__setitem__(
            "system_settings_opened", data["totals"]["system_settings_opened"] + 1
        ))

    def record_output_method(self, copy_to_clipboard: bool) -> None:
        """Record output method: copy to clipboard or type text."""
        key = "output_copy_clipboard" if copy_to_clipboard else "output_type_text"
        self._record(lambda data: data["totals"].__setitem__(key, data["totals"][key] + 1))

    def record_correction_pairs(self, count: int) -> None:
        """Record correction pairs recorded."""
        self._record(lambda data: data["totals"].__setitem__(
            "correction_pairs_recorded", data["totals"]["correction_pairs_recorded"] + count
        ))

    def record_correction_asr_hotwords_injected(self, count: int) -> None:
        """Record ASR hotwords injected for corrections."""
        self._record(lambda data: data["totals"].__setitem__(
            "correction_asr_hotwords_injected", data["totals"]["correction_asr_hotwords_injected"] + count
        ))

    def record_correction_llm_vocab_injected(self, count: int) -> None:
        """Record LLM vocab injected for corrections."""
        self._record(lambda data: data["totals"].__setitem__(
            "correction_llm_vocab_injected", data["totals"]["correction_llm_vocab_injected"] + count
        ))

    def get_stats(self) -> dict[str, Any]:
        """Return cumulative statistics."""
        with self._lock:
            self._ensure_loaded()
            assert self._cumulative is not None  # noqa: S101
            return copy.deepcopy(self._cumulative)

    def get_today_stats(self) -> dict[str, Any]:
        """Return today's statistics."""
        with self._lock:
            self._ensure_loaded()
            assert self._daily is not None  # noqa: S101
            return copy.deepcopy(self._daily)

    def get_daily(self, day: str) -> dict[str, Any]:
        """Return statistics for a specific day (YYYY-MM-DD).

        For today's date, returns the in-memory data. For other dates,
        reads from the daily JSON file on disk.
        """
        with self._lock:
            self._ensure_loaded()
            if day == self._daily_date:
                assert self._daily is not None  # noqa: S101
                return copy.deepcopy(self._daily)
            data = self._read_json(self._daily_path(day))
            if data is None:
                return _empty_daily(day)
            return self._backfill_daily(data, day)
