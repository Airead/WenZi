"""Tests for the statistics chart panel."""

from __future__ import annotations

import json
import sys
from datetime import date
from unittest.mock import MagicMock

import pytest

from tests.conftest import mock_panel_close_delegate


@pytest.fixture(autouse=True)
def _mock_appkit(mock_appkit_modules, monkeypatch):
    """Mock AppKit, Foundation, WebKit modules for headless testing."""
    mock_webkit = MagicMock()
    monkeypatch.setitem(sys.modules, "WebKit", mock_webkit)

    import voicetext.ui.stats_panel as _sp

    mock_panel_close_delegate(monkeypatch, _sp)
    return mock_appkit_modules


# ---------------------------------------------------------------------------
# Pure-logic tests (no AppKit needed)
# ---------------------------------------------------------------------------


class TestGetDailyRange:
    """Tests for get_daily_range helper."""

    def test_returns_correct_number_of_days(self, tmp_path):
        from voicetext.usage_stats import UsageStats

        from voicetext.ui.stats_panel import get_daily_range

        stats = UsageStats(stats_dir=str(tmp_path / "cfg"))
        result = get_daily_range(stats, days=7)
        assert len(result) == 7

    def test_days_are_chronologically_ordered(self, tmp_path):
        from voicetext.usage_stats import UsageStats

        from voicetext.ui.stats_panel import get_daily_range

        stats = UsageStats(stats_dir=str(tmp_path / "cfg"))
        result = get_daily_range(stats, days=5)
        dates = [d["date"] for d in result]
        assert dates == sorted(dates)

    def test_last_day_is_today(self, tmp_path):
        from voicetext.usage_stats import UsageStats

        from voicetext.ui.stats_panel import get_daily_range

        stats = UsageStats(stats_dir=str(tmp_path / "cfg"))
        result = get_daily_range(stats, days=3)
        assert result[-1]["date"] == date.today().isoformat()

    def test_includes_recorded_data(self, tmp_path):
        from voicetext.usage_stats import UsageStats

        from voicetext.ui.stats_panel import get_daily_range

        stats = UsageStats(stats_dir=str(tmp_path / "cfg"))
        stats.record_transcription(mode="direct")
        result = get_daily_range(stats, days=1)
        assert result[0]["totals"]["transcriptions"] == 1
        assert result[0]["totals"]["direct_mode"] == 1

    def test_empty_days_have_zero_totals(self, tmp_path):
        from voicetext.usage_stats import UsageStats

        from voicetext.ui.stats_panel import get_daily_range

        stats = UsageStats(stats_dir=str(tmp_path / "cfg"))
        result = get_daily_range(stats, days=7)
        # Yesterday should have zero transcriptions (nothing recorded)
        yesterday = result[-2]
        assert yesterday["totals"]["transcriptions"] == 0


class TestBuildStatsPayload:
    """Tests for build_stats_payload."""

    def test_payload_structure(self, tmp_path):
        from voicetext.usage_stats import UsageStats

        from voicetext.ui.stats_panel import build_stats_payload

        stats = UsageStats(stats_dir=str(tmp_path / "cfg"))
        payload = build_stats_payload(stats, days=7)
        assert "cumulative" in payload
        assert "today" in payload
        assert "daily" in payload
        assert len(payload["daily"]) == 7

    def test_payload_is_json_serializable(self, tmp_path):
        from voicetext.usage_stats import UsageStats

        from voicetext.ui.stats_panel import build_stats_payload

        stats = UsageStats(stats_dir=str(tmp_path / "cfg"))
        stats.record_transcription(mode="preview", enhance_mode="proofread")
        payload = build_stats_payload(stats)
        # Should not raise
        result = json.dumps(payload, ensure_ascii=False)
        assert "proofread" in result


class TestBuildHtml:
    """Tests for build_html."""

    def test_placeholder_replaced(self, tmp_path):
        from voicetext.usage_stats import UsageStats

        from voicetext.ui.stats_panel import build_html, build_stats_payload

        stats = UsageStats(stats_dir=str(tmp_path / "cfg"))
        payload = build_stats_payload(stats, days=3)
        html = build_html(payload)
        assert "__STATS_DATA__" not in html
        assert "Chart.js" not in html or "chart.js" in html.lower()

    def test_html_contains_chart_elements(self):
        from voicetext.ui.stats_panel import build_html

        html = build_html({"cumulative": {}, "today": {}, "daily": []})
        assert "dailyTrend" in html
        assert "actionBar" in html
        assert "tokenBar" in html
        assert "enhanceBar" in html

    def test_html_has_dark_mode_support(self):
        from voicetext.ui.stats_panel import build_html

        html = build_html({"cumulative": {}, "today": {}, "daily": []})
        assert "prefers-color-scheme: dark" in html


class TestHtmlTemplate:
    """Tests for the HTML template string."""

    def test_template_has_placeholder(self):
        from voicetext.ui.stats_panel import _HTML_TEMPLATE

        assert "__STATS_DATA__" in _HTML_TEMPLATE

    def test_template_has_chart_js_reference(self):
        from voicetext.ui.stats_panel import _HTML_TEMPLATE

        assert "chart.js" in _HTML_TEMPLATE.lower()

    def test_template_has_canvas_elements(self):
        from voicetext.ui.stats_panel import _HTML_TEMPLATE

        for canvas_id in ("dailyTrend", "actionBar", "tokenBar", "enhanceBar"):
            assert canvas_id in _HTML_TEMPLATE


# ---------------------------------------------------------------------------
# Panel lifecycle tests (AppKit mocked)
# ---------------------------------------------------------------------------


class TestStatsChartPanel:
    """Tests for StatsChartPanel lifecycle."""

    def test_show_sets_regular_activation_policy(self, tmp_path, _mock_appkit):
        from voicetext.usage_stats import UsageStats

        from voicetext.ui.stats_panel import StatsChartPanel

        stats = UsageStats(stats_dir=str(tmp_path / "cfg"))
        panel = StatsChartPanel()
        panel.show(stats)
        _mock_appkit.appkit.NSApp.setActivationPolicy_.assert_called_with(0)

    def test_close_restores_accessory_policy(self, tmp_path, _mock_appkit):
        from voicetext.usage_stats import UsageStats

        from voicetext.ui.stats_panel import StatsChartPanel

        stats = UsageStats(stats_dir=str(tmp_path / "cfg"))
        panel = StatsChartPanel()
        panel.show(stats)

        # Reset to check close behavior
        _mock_appkit.appkit.NSApp.setActivationPolicy_.reset_mock()
        panel.close()
        _mock_appkit.appkit.NSApp.setActivationPolicy_.assert_called_with(1)

    def test_panel_reused_on_second_show(self, tmp_path, _mock_appkit):
        from voicetext.usage_stats import UsageStats

        from voicetext.ui.stats_panel import StatsChartPanel

        stats = UsageStats(stats_dir=str(tmp_path / "cfg"))
        panel = StatsChartPanel()
        panel.show(stats)
        first_panel = panel._panel
        panel.show(stats)
        assert panel._panel is first_panel

    def test_close_without_show_is_noop(self):
        from voicetext.ui.stats_panel import StatsChartPanel

        panel = StatsChartPanel()
        panel.close()  # Should not raise

    def test_webview_loads_html(self, tmp_path, _mock_appkit):
        from voicetext.usage_stats import UsageStats

        from voicetext.ui.stats_panel import StatsChartPanel

        stats = UsageStats(stats_dir=str(tmp_path / "cfg"))
        panel = StatsChartPanel()
        panel.show(stats)
        # Verify loadHTMLString_baseURL_ was called on the webview
        panel._webview.loadHTMLString_baseURL_.assert_called_once()
        html_arg = panel._webview.loadHTMLString_baseURL_.call_args[0][0]
        assert "__STATS_DATA__" not in html_arg
        assert "dailyTrend" in html_arg
