"""Tests for UsageStats."""

import json
import os
import stat
import threading
from datetime import date
from unittest.mock import patch

import pytest

from voicetext.usage_stats import UsageStats


@pytest.fixture
def stats_dir(tmp_path):
    return str(tmp_path / "config")


@pytest.fixture
def stats(stats_dir):
    return UsageStats(stats_dir=stats_dir)


class TestInitialState:
    def test_initial_stats_empty(self, stats):
        s = stats.get_stats()
        assert s["totals"]["transcriptions"] == 0
        assert s["totals"]["direct_mode"] == 0
        assert s["totals"]["preview_mode"] == 0
        assert s["totals"]["direct_accept"] == 0
        assert s["totals"]["user_modification"] == 0
        assert s["totals"]["cancel"] == 0
        assert s["token_usage"]["prompt_tokens"] == 0
        assert s["token_usage"]["completion_tokens"] == 0
        assert s["token_usage"]["total_tokens"] == 0
        assert s["token_usage"]["cache_read_tokens"] == 0
        assert s["first_recorded"] is None


class TestRecordTranscription:
    def test_record_transcription_direct(self, stats):
        stats.record_transcription(mode="direct", enhance_mode="proofread")
        s = stats.get_stats()
        assert s["totals"]["transcriptions"] == 1
        assert s["totals"]["direct_mode"] == 1
        assert s["totals"]["preview_mode"] == 0
        assert s["enhance_mode_usage"]["proofread"] == 1

    def test_record_transcription_preview(self, stats):
        stats.record_transcription(mode="preview", enhance_mode="translate_en")
        s = stats.get_stats()
        assert s["totals"]["transcriptions"] == 1
        assert s["totals"]["preview_mode"] == 1
        assert s["totals"]["direct_mode"] == 0
        assert s["enhance_mode_usage"]["translate_en"] == 1

    def test_enhance_mode_off_not_tracked(self, stats):
        stats.record_transcription(mode="direct", enhance_mode="off")
        s = stats.get_stats()
        assert s["enhance_mode_usage"] == {}

    def test_enhance_mode_empty_not_tracked(self, stats):
        stats.record_transcription(mode="direct", enhance_mode="")
        s = stats.get_stats()
        assert s["enhance_mode_usage"] == {}


class TestRecordConfirm:
    def test_record_confirm_modified(self, stats):
        stats.record_confirm(modified=True)
        s = stats.get_stats()
        assert s["totals"]["user_modification"] == 1
        assert s["totals"]["direct_accept"] == 0

    def test_record_confirm_direct_accept(self, stats):
        stats.record_confirm(modified=False)
        s = stats.get_stats()
        assert s["totals"]["direct_accept"] == 1
        assert s["totals"]["user_modification"] == 0


class TestRecordCancel:
    def test_record_cancel(self, stats):
        stats.record_cancel()
        s = stats.get_stats()
        assert s["totals"]["cancel"] == 1


class TestTokenUsage:
    def test_record_token_usage(self, stats):
        stats.record_token_usage({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        stats.record_token_usage({"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280})
        s = stats.get_stats()
        assert s["token_usage"]["prompt_tokens"] == 300
        assert s["token_usage"]["completion_tokens"] == 130
        assert s["token_usage"]["total_tokens"] == 430

    def test_record_token_usage_none(self, stats):
        stats.record_token_usage(None)
        s = stats.get_stats()
        assert s["token_usage"]["total_tokens"] == 0

    def test_record_token_usage_empty_dict(self, stats):
        stats.record_token_usage({})
        s = stats.get_stats()
        assert s["token_usage"]["total_tokens"] == 0

    def test_record_cache_read_tokens(self, stats):
        stats.record_token_usage({
            "prompt_tokens": 100, "completion_tokens": 50,
            "total_tokens": 150, "cache_read_tokens": 40,
        })
        stats.record_token_usage({
            "prompt_tokens": 200, "completion_tokens": 80,
            "total_tokens": 280, "cache_read_tokens": 60,
        })
        s = stats.get_stats()
        assert s["token_usage"]["cache_read_tokens"] == 100

    def test_record_cache_read_tokens_missing(self, stats):
        """Providers that don't support cache return no cache_read_tokens key."""
        stats.record_token_usage({
            "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
        })
        s = stats.get_stats()
        assert s["token_usage"]["cache_read_tokens"] == 0

    def test_record_cache_read_tokens_zero(self, stats):
        stats.record_token_usage({
            "prompt_tokens": 100, "completion_tokens": 50,
            "total_tokens": 150, "cache_read_tokens": 0,
        })
        s = stats.get_stats()
        assert s["token_usage"]["cache_read_tokens"] == 0


class TestEnhanceModeTracking:
    def test_enhance_mode_tracking(self, stats):
        stats.record_transcription(mode="direct", enhance_mode="proofread")
        stats.record_transcription(mode="preview", enhance_mode="proofread")
        stats.record_transcription(mode="preview", enhance_mode="translate_en")
        stats.record_transcription(mode="direct", enhance_mode="commandline_master")
        s = stats.get_stats()
        assert s["enhance_mode_usage"]["proofread"] == 2
        assert s["enhance_mode_usage"]["translate_en"] == 1
        assert s["enhance_mode_usage"]["commandline_master"] == 1


class TestGetTodayStats:
    def test_get_today_stats(self, stats):
        stats.record_transcription(mode="direct", enhance_mode="proofread")
        stats.record_confirm(modified=False)
        stats.record_token_usage({"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70})

        today = stats.get_today_stats()
        assert today["date"] == date.today().isoformat()
        assert today["totals"]["transcriptions"] == 1
        assert today["totals"]["direct_mode"] == 1
        assert today["totals"]["direct_accept"] == 1
        assert today["token_usage"]["total_tokens"] == 70
        assert today["enhance_mode_usage"]["proofread"] == 1

    def test_get_today_stats_empty(self, stats):
        today = stats.get_today_stats()
        assert today["totals"]["transcriptions"] == 0


class TestDailyFiles:
    def test_daily_file_created(self, stats, stats_dir):
        stats.record_transcription(mode="direct")
        today = date.today().isoformat()
        daily_path = os.path.join(stats_dir, "usage_stats", f"{today}.json")
        assert os.path.exists(daily_path)
        with open(daily_path) as f:
            data = json.load(f)
        assert data["date"] == today
        assert data["totals"]["transcriptions"] == 1

    def test_daily_file_isolation(self, stats, stats_dir):
        # Record on "day 1"
        with patch("voicetext.usage_stats.date") as mock_date:
            mock_date.today.return_value = date(2026, 1, 1)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            stats.record_transcription(mode="direct")

        # Record on "day 2"
        with patch("voicetext.usage_stats.date") as mock_date:
            mock_date.today.return_value = date(2026, 1, 2)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            stats.record_transcription(mode="preview")
            stats.record_transcription(mode="preview")

        day1_path = os.path.join(stats_dir, "usage_stats", "2026-01-01.json")
        day2_path = os.path.join(stats_dir, "usage_stats", "2026-01-02.json")

        with open(day1_path) as f:
            d1 = json.load(f)
        with open(day2_path) as f:
            d2 = json.load(f)

        assert d1["totals"]["transcriptions"] == 1
        assert d1["totals"]["direct_mode"] == 1
        assert d2["totals"]["transcriptions"] == 2
        assert d2["totals"]["preview_mode"] == 2

        # Cumulative should have all 3
        s = stats.get_stats()
        assert s["totals"]["transcriptions"] == 3


class TestPersistence:
    def test_persistence_across_instances(self, stats_dir):
        s1 = UsageStats(stats_dir=stats_dir)
        s1.record_transcription(mode="direct", enhance_mode="proofread")
        s1.record_confirm(modified=False)
        s1.record_token_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

        s2 = UsageStats(stats_dir=stats_dir)
        s2.record_transcription(mode="preview", enhance_mode="translate_en")

        data = s2.get_stats()
        assert data["totals"]["transcriptions"] == 2
        assert data["totals"]["direct_accept"] == 1
        assert data["token_usage"]["total_tokens"] == 15
        assert data["enhance_mode_usage"]["proofread"] == 1
        assert data["enhance_mode_usage"]["translate_en"] == 1


class TestCorruptedFile:
    def test_corrupted_file_recovery(self, stats, stats_dir):
        # Write valid data first
        stats.record_transcription(mode="direct")

        # Corrupt the cumulative file
        cum_path = os.path.join(stats_dir, "usage_stats.json")
        with open(cum_path, "w") as f:
            f.write("{invalid json")

        # Should recover gracefully (starts from empty)
        s = stats.get_stats()
        assert s["totals"]["transcriptions"] == 0

        # Should be able to write again
        stats.record_transcription(mode="preview")
        s = stats.get_stats()
        assert s["totals"]["transcriptions"] == 1


class TestDirectoryCreation:
    def test_creates_directory_if_missing(self, tmp_path):
        deep_dir = str(tmp_path / "a" / "b" / "c")
        s = UsageStats(stats_dir=deep_dir)
        s.record_transcription(mode="direct")
        assert os.path.exists(os.path.join(deep_dir, "usage_stats.json"))


class TestInitialStateNewCounters:
    def test_new_counters_initial_zero(self, stats):
        s = stats.get_stats()
        assert s["totals"]["clipboard_enhances"] == 0
        assert s["totals"]["clipboard_enhance_confirm"] == 0
        assert s["totals"]["clipboard_enhance_cancel"] == 0
        assert s["totals"]["output_type_text"] == 0
        assert s["totals"]["output_copy_clipboard"] == 0
        assert s["totals"]["google_translate_opens"] == 0
        assert s["totals"]["sound_feedback_plays"] == 0


class TestRecordClipboardEnhance:
    def test_record_clipboard_enhance(self, stats):
        stats.record_clipboard_enhance(enhance_mode="proofread")
        s = stats.get_stats()
        assert s["totals"]["clipboard_enhances"] == 1
        assert s["enhance_mode_usage"]["proofread"] == 1

    def test_record_clipboard_enhance_off_mode(self, stats):
        stats.record_clipboard_enhance(enhance_mode="off")
        s = stats.get_stats()
        assert s["totals"]["clipboard_enhances"] == 1
        assert s["enhance_mode_usage"] == {}

    def test_record_clipboard_enhance_empty_mode(self, stats):
        stats.record_clipboard_enhance(enhance_mode="")
        s = stats.get_stats()
        assert s["totals"]["clipboard_enhances"] == 1
        assert s["enhance_mode_usage"] == {}

    def test_record_clipboard_enhance_sets_first_recorded(self, stats):
        stats.record_clipboard_enhance(enhance_mode="proofread")
        s = stats.get_stats()
        assert s["first_recorded"] is not None

    def test_record_clipboard_enhance_daily(self, stats):
        stats.record_clipboard_enhance(enhance_mode="translate_en")
        today = stats.get_today_stats()
        assert today["totals"]["clipboard_enhances"] == 1
        assert today["enhance_mode_usage"]["translate_en"] == 1


class TestRecordClipboardConfirm:
    def test_record_clipboard_confirm(self, stats):
        stats.record_clipboard_confirm()
        s = stats.get_stats()
        assert s["totals"]["clipboard_enhance_confirm"] == 1

    def test_record_clipboard_confirm_multiple(self, stats):
        stats.record_clipboard_confirm()
        stats.record_clipboard_confirm()
        stats.record_clipboard_confirm()
        s = stats.get_stats()
        assert s["totals"]["clipboard_enhance_confirm"] == 3


class TestRecordClipboardCancel:
    def test_record_clipboard_cancel(self, stats):
        stats.record_clipboard_cancel()
        s = stats.get_stats()
        assert s["totals"]["clipboard_enhance_cancel"] == 1

    def test_record_clipboard_cancel_multiple(self, stats):
        stats.record_clipboard_cancel()
        stats.record_clipboard_cancel()
        s = stats.get_stats()
        assert s["totals"]["clipboard_enhance_cancel"] == 2


class TestRecordOutputMethod:
    def test_record_output_type_text(self, stats):
        stats.record_output_method(copy_to_clipboard=False)
        s = stats.get_stats()
        assert s["totals"]["output_type_text"] == 1
        assert s["totals"]["output_copy_clipboard"] == 0

    def test_record_output_copy_clipboard(self, stats):
        stats.record_output_method(copy_to_clipboard=True)
        s = stats.get_stats()
        assert s["totals"]["output_type_text"] == 0
        assert s["totals"]["output_copy_clipboard"] == 1

    def test_record_output_method_mixed(self, stats):
        stats.record_output_method(copy_to_clipboard=False)
        stats.record_output_method(copy_to_clipboard=False)
        stats.record_output_method(copy_to_clipboard=True)
        s = stats.get_stats()
        assert s["totals"]["output_type_text"] == 2
        assert s["totals"]["output_copy_clipboard"] == 1

    def test_record_output_method_daily(self, stats):
        stats.record_output_method(copy_to_clipboard=True)
        today = stats.get_today_stats()
        assert today["totals"]["output_copy_clipboard"] == 1


class TestRecordGoogleTranslateOpen:
    def test_record_google_translate_open(self, stats):
        stats.record_google_translate_open()
        s = stats.get_stats()
        assert s["totals"]["google_translate_opens"] == 1

    def test_record_google_translate_open_multiple(self, stats):
        stats.record_google_translate_open()
        stats.record_google_translate_open()
        stats.record_google_translate_open()
        s = stats.get_stats()
        assert s["totals"]["google_translate_opens"] == 3

    def test_record_google_translate_open_daily(self, stats):
        stats.record_google_translate_open()
        today = stats.get_today_stats()
        assert today["totals"]["google_translate_opens"] == 1


class TestRecordSoundFeedback:
    def test_record_sound_feedback(self, stats):
        stats.record_sound_feedback()
        s = stats.get_stats()
        assert s["totals"]["sound_feedback_plays"] == 1

    def test_record_sound_feedback_multiple(self, stats):
        stats.record_sound_feedback()
        stats.record_sound_feedback()
        stats.record_sound_feedback()
        s = stats.get_stats()
        assert s["totals"]["sound_feedback_plays"] == 3

    def test_record_sound_feedback_daily(self, stats):
        stats.record_sound_feedback()
        today = stats.get_today_stats()
        assert today["totals"]["sound_feedback_plays"] == 1


class TestRecordHistoryBrowseOpen:
    def test_record_history_browse_open(self, stats):
        stats.record_history_browse_open()
        s = stats.get_stats()
        assert s["totals"]["history_browse_opens"] == 1

    def test_record_history_browse_open_multiple(self, stats):
        stats.record_history_browse_open()
        stats.record_history_browse_open()
        stats.record_history_browse_open()
        s = stats.get_stats()
        assert s["totals"]["history_browse_opens"] == 3

    def test_record_history_browse_open_daily(self, stats):
        stats.record_history_browse_open()
        today = stats.get_today_stats()
        assert today["totals"]["history_browse_opens"] == 1


class TestRecordHistoryEdit:
    def test_record_history_edit(self, stats):
        stats.record_history_edit()
        s = stats.get_stats()
        assert s["totals"]["history_edits"] == 1

    def test_record_history_edit_multiple(self, stats):
        stats.record_history_edit()
        stats.record_history_edit()
        s = stats.get_stats()
        assert s["totals"]["history_edits"] == 2

    def test_record_history_edit_daily(self, stats):
        stats.record_history_edit()
        today = stats.get_today_stats()
        assert today["totals"]["history_edits"] == 1


class TestFilePermissions:
    def test_cumulative_file_is_owner_only(self, stats):
        """Stats files should be owner-only readable (0o600)."""
        stats.record_transcription(mode="direct")
        mode = stat.S_IMODE(os.stat(stats._cumulative_path).st_mode)
        assert mode == 0o600

    def test_daily_file_is_owner_only(self, stats):
        """Daily stats files should be owner-only readable (0o600)."""
        stats.record_transcription(mode="direct")
        today = date.today().isoformat()
        daily_path = stats._daily_path(today)
        mode = stat.S_IMODE(os.stat(daily_path).st_mode)
        assert mode == 0o600


class TestThreadSafety:
    def test_thread_safety(self, stats):
        n_threads = 10
        n_ops = 50
        barrier = threading.Barrier(n_threads)

        def worker():
            barrier.wait()
            for _ in range(n_ops):
                stats.record_transcription(mode="direct")

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        s = stats.get_stats()
        assert s["totals"]["transcriptions"] == n_threads * n_ops
        assert s["totals"]["direct_mode"] == n_threads * n_ops
