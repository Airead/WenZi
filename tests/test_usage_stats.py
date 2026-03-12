"""Tests for UsageStats."""

import json
import os
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
