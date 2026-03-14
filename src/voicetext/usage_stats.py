"""Usage statistics tracking for VoiceText."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict

from .config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)


def _empty_totals() -> Dict[str, int]:
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
    }


def _empty_token_usage() -> Dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
    }


def _empty_cumulative() -> Dict[str, Any]:
    return {
        "version": 1,
        "first_recorded": None,
        "last_updated": None,
        "totals": _empty_totals(),
        "token_usage": _empty_token_usage(),
        "enhance_mode_usage": {},
    }


def _empty_daily(day: str) -> Dict[str, Any]:
    return {
        "date": day,
        "totals": _empty_totals(),
        "token_usage": _empty_token_usage(),
        "enhance_mode_usage": {},
    }


class UsageStats:
    """Thread-safe usage statistics with cumulative + daily file storage."""

    def __init__(self, stats_dir: str = DEFAULT_CONFIG_DIR) -> None:
        self._base_dir = os.path.expanduser(stats_dir)
        self._cumulative_path = os.path.join(self._base_dir, "usage_stats.json")
        self._daily_dir = os.path.join(self._base_dir, "usage_stats")
        self._lock = threading.Lock()

    def _daily_path(self, day: str) -> str:
        return os.path.join(self._daily_dir, f"{day}.json")

    def _read_json(self, path: str) -> Dict[str, Any] | None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def _write_json(self, path: str, data: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    def _load_cumulative(self) -> Dict[str, Any]:
        data = self._read_json(self._cumulative_path)
        if data is None:
            return _empty_cumulative()
        # Ensure all expected keys exist
        for key, factory in [("totals", _empty_totals), ("token_usage", _empty_token_usage)]:
            if key not in data or not isinstance(data[key], dict):
                data[key] = factory()
            else:
                for k, v in factory().items():
                    data[key].setdefault(k, v)
        data.setdefault("enhance_mode_usage", {})
        return data

    def _load_daily(self, day: str) -> Dict[str, Any]:
        data = self._read_json(self._daily_path(day))
        if data is None:
            return _empty_daily(day)
        for key, factory in [("totals", _empty_totals), ("token_usage", _empty_token_usage)]:
            if key not in data or not isinstance(data[key], dict):
                data[key] = factory()
            else:
                for k, v in factory().items():
                    data[key].setdefault(k, v)
        data.setdefault("enhance_mode_usage", {})
        return data

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _today(self) -> str:
        return date.today().isoformat()

    def _record(
        self,
        updater: Callable[[Dict[str, Any]], None],
        set_first_recorded: bool = False,
    ) -> None:
        """Apply *updater* to both cumulative and daily data, then persist."""
        with self._lock:
            now = self._now_iso()
            day = self._today()

            cum = self._load_cumulative()
            daily = self._load_daily(day)

            for data in (cum, daily):
                updater(data)

            if set_first_recorded and cum.get("first_recorded") is None:
                cum["first_recorded"] = now
            cum["last_updated"] = now

            self._write_json(self._cumulative_path, cum)
            self._write_json(self._daily_path(day), daily)

    def record_transcription(self, mode: str, enhance_mode: str = "") -> None:
        """Record a transcription event. mode is 'direct' or 'preview'."""
        def _update(data: Dict[str, Any]) -> None:
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

        def _update(data: Dict[str, Any]) -> None:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cache_read_tokens"):
                val = usage.get(key, 0)
                if val:
                    data["token_usage"][key] += val

        self._record(_update)

    def record_clipboard_enhance(self, enhance_mode: str = "") -> None:
        """Record a clipboard enhance trigger."""
        def _update(data: Dict[str, Any]) -> None:
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

    def record_output_method(self, copy_to_clipboard: bool) -> None:
        """Record output method: copy to clipboard or type text."""
        key = "output_copy_clipboard" if copy_to_clipboard else "output_type_text"
        self._record(lambda data: data["totals"].__setitem__(key, data["totals"][key] + 1))

    def get_stats(self) -> Dict[str, Any]:
        """Return cumulative statistics."""
        with self._lock:
            return self._load_cumulative()

    def get_today_stats(self) -> Dict[str, Any]:
        """Return today's statistics."""
        with self._lock:
            return self._load_daily(self._today())
