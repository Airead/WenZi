"""Statistics visualization panel with interactive charts via WKWebView + Chart.js."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from voicetext.usage_stats import UsageStats

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Close delegate (lazy-created to avoid PyObjC import at module level)
# ---------------------------------------------------------------------------
_PanelCloseDelegate = None


def _get_panel_close_delegate_class():
    global _PanelCloseDelegate
    if _PanelCloseDelegate is None:
        from Foundation import NSObject

        class StatsChartCloseDelegate(NSObject):
            _panel_ref = None

            def windowWillClose_(self, notification):
                if self._panel_ref is not None:
                    self._panel_ref.close()

        _PanelCloseDelegate = StatsChartCloseDelegate
    return _PanelCloseDelegate


# ---------------------------------------------------------------------------
# HTML template with embedded Chart.js
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root {
    --bg: #ffffff; --text: #1d1d1f; --card-bg: #f5f5f7;
    --border: #d2d2d7; --secondary: #86868b; --accent: #007aff;
    --green: #34c759; --orange: #ff9500; --red: #ff3b30;
    --purple: #af52de; --teal: #5ac8fa; --pink: #ff2d55;
}
@media (prefers-color-scheme: dark) {
    :root {
        --bg: #1d1d1f; --text: #f5f5f7; --card-bg: #2c2c2e;
        --border: #48484a; --secondary: #98989d; --accent: #0a84ff;
        --green: #30d158; --orange: #ff9f0a; --red: #ff453a;
        --purple: #bf5af2; --teal: #64d2ff; --pink: #ff375f;
    }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
    background: var(--bg); color: var(--text);
    padding: 16px; overflow-y: auto;
}
.cards-row {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;
    margin-bottom: 20px;
}
.card {
    background: var(--card-bg); border-radius: 10px; padding: 16px;
    border: 1px solid var(--border);
}
.card .label { font-size: 12px; color: var(--secondary); margin-bottom: 4px; }
.card .value { font-size: 28px; font-weight: 600; }
.card .sub { font-size: 11px; color: var(--secondary); margin-top: 4px; }
.chart-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
}
.chart-container {
    background: var(--card-bg); border-radius: 10px; padding: 16px;
    border: 1px solid var(--border); min-width: 0;
}
.chart-container h3 {
    font-size: 14px; font-weight: 600; margin-bottom: 12px;
}
.chart-wrap {
    position: relative; height: 220px; overflow: hidden;
}
.empty-hint {
    color: var(--secondary); font-size: 13px; text-align: center;
    padding-top: 80px;
}
.tab-bar {
    display: flex; gap: 8px; margin-bottom: 16px;
}
.tab-btn {
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 6px; padding: 6px 14px; cursor: pointer;
    font-size: 13px; color: var(--text); font-family: inherit;
}
.tab-btn.active {
    background: var(--accent); color: #fff; border-color: var(--accent);
}
</style>
</head>
<body>

<div class="cards-row" id="cards"></div>

<div class="tab-bar">
    <button class="tab-btn active" onclick="setRange(7)">7 Days</button>
    <button class="tab-btn" onclick="setRange(14)">14 Days</button>
    <button class="tab-btn" onclick="setRange(30)">30 Days</button>
</div>

<div class="chart-grid">
    <div class="chart-container">
        <h3>Daily Transcriptions</h3>
        <div class="chart-wrap"><canvas id="dailyTrend"></canvas></div>
    </div>
    <div class="chart-container">
        <h3>User Actions</h3>
        <div class="chart-wrap"><canvas id="actionBar"></canvas></div>
    </div>
    <div class="chart-container">
        <h3>Token Usage</h3>
        <div class="chart-wrap"><canvas id="tokenBar"></canvas></div>
    </div>
    <div class="chart-container">
        <h3>Enhance Modes</h3>
        <div class="chart-wrap"><canvas id="enhanceBar"></canvas></div>
    </div>
</div>

<script>
const DATA = __STATS_DATA__;

const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
Chart.defaults.color = isDark ? '#f5f5f7' : '#1d1d1f';
Chart.defaults.borderColor = isDark ? 'rgba(72,72,74,0.5)' : 'rgba(210,210,215,0.5)';
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif';
Chart.defaults.font.size = 12;
Chart.defaults.plugins.legend.labels.usePointStyle = true;
Chart.defaults.plugins.legend.labels.pointStyle = 'rectRounded';

const COLORS = {
    accent: isDark ? '#0a84ff' : '#007aff',
    green: isDark ? '#30d158' : '#34c759',
    orange: isDark ? '#ff9f0a' : '#ff9500',
    red: isDark ? '#ff453a' : '#ff3b30',
    purple: isDark ? '#bf5af2' : '#af52de',
    teal: isDark ? '#64d2ff' : '#5ac8fa',
    pink: isDark ? '#ff375f' : '#ff2d55',
};

// --- Summary Cards ---
function renderCards() {
    const cum = DATA.cumulative;
    const today = DATA.today;
    const t = cum.totals || {};
    const td = today.totals || {};
    const tk = cum.token_usage || {};

    const cards = [
        { label: 'Total Transcriptions', value: t.transcriptions || 0,
          sub: `Today: ${td.transcriptions || 0}` },
        { label: 'Total Tokens', value: formatNum(tk.total_tokens || 0),
          sub: `\u2191${formatNum(tk.prompt_tokens || 0)} \u2193${formatNum(tk.completion_tokens || 0)}`
            + (tk.cache_read_tokens ? ` | Cached: ${formatNum(tk.cache_read_tokens)}` : '') },
        { label: 'Accept Rate',
          value: calcRate(t.direct_accept, t.direct_accept + t.user_modification + t.cancel),
          sub: `Accept: ${t.direct_accept || 0} | Modified: ${t.user_modification || 0}` },
        { label: 'Days Active', value: DATA.daily.filter(d => (d.totals||{}).transcriptions > 0).length,
          sub: cum.first_recorded ? `Since ${cum.first_recorded.slice(0, 10)}` : 'No data yet' },
    ];

    const container = document.getElementById('cards');
    container.innerHTML = cards.map(c => `
        <div class="card">
            <div class="label">${c.label}</div>
            <div class="value">${c.value}</div>
            <div class="sub">${c.sub}</div>
        </div>
    `).join('');
}

function formatNum(n) {
    return n.toLocaleString();
}

function calcRate(num, denom) {
    if (!denom) return '-';
    return Math.round(num / denom * 100) + '%';
}

// --- Stacked bar chart options ---
const STACKED_BAR_OPTS = {
    responsive: true, maintainAspectRatio: false,
    interaction: { intersect: false, mode: 'index' },
    scales: {
        x: { stacked: true },
        y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } },
    },
    plugins: {
        legend: { position: 'bottom' },
        tooltip: {
            mode: 'index', intersect: false,
            callbacks: {
                footer: items => {
                    const sum = items.reduce((s, i) => s + (i.parsed.y || 0), 0);
                    return 'Total: ' + sum.toLocaleString();
                }
            }
        },
    },
};

const BAR_COLORS = [COLORS.accent, COLORS.green, COLORS.orange, COLORS.purple,
                    COLORS.teal, COLORS.pink, COLORS.red];

function stackedBarOpts(extraOpts) {
    return JSON.parse(JSON.stringify({...STACKED_BAR_OPTS, ...extraOpts}));
}

// --- Charts ---
let currentRange = 7;
let chartInstances = {};

function setRange(days) {
    currentRange = days;
    document.querySelectorAll('.tab-btn').forEach((btn, i) => {
        btn.classList.toggle('active', [7,14,30][i] === days);
    });
    renderCharts();
}

function getSlicedDaily() {
    return DATA.daily.slice(-currentRange);
}

function renderCharts() {
    const daily = getSlicedDaily();
    const labels = daily.map(d => {
        const parts = (d.date || '').split('-');
        return parts.length === 3 ? parts[1] + '/' + parts[2] : d.date;
    });

    // 1. Daily Transcriptions — stacked bar: Direct + Preview
    destroyChart('dailyTrend');
    chartInstances.dailyTrend = new Chart(document.getElementById('dailyTrend'), {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Direct',
                    data: daily.map(d => (d.totals||{}).direct_mode || 0),
                    backgroundColor: COLORS.accent + 'cc',
                    borderColor: COLORS.accent, borderWidth: 1, borderRadius: 2,
                },
                {
                    label: 'Preview',
                    data: daily.map(d => (d.totals||{}).preview_mode || 0),
                    backgroundColor: COLORS.green + 'cc',
                    borderColor: COLORS.green, borderWidth: 1, borderRadius: 2,
                }
            ]
        },
        options: stackedBarOpts({}),
    });

    // 2. User Actions — stacked bar: Accept + Modified + Cancel
    destroyChart('actionBar');
    const hasActions = daily.some(d => {
        const t = d.totals || {};
        return (t.direct_accept || 0) + (t.user_modification || 0) + (t.cancel || 0) > 0;
    });
    if (hasActions) {
        chartInstances.actionBar = new Chart(document.getElementById('actionBar'), {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Accept',
                        data: daily.map(d => (d.totals||{}).direct_accept || 0),
                        backgroundColor: COLORS.green + 'cc',
                        borderColor: COLORS.green, borderWidth: 1, borderRadius: 2,
                    },
                    {
                        label: 'Modified',
                        data: daily.map(d => (d.totals||{}).user_modification || 0),
                        backgroundColor: COLORS.orange + 'cc',
                        borderColor: COLORS.orange, borderWidth: 1, borderRadius: 2,
                    },
                    {
                        label: 'Cancel',
                        data: daily.map(d => (d.totals||{}).cancel || 0),
                        backgroundColor: COLORS.red + 'cc',
                        borderColor: COLORS.red, borderWidth: 1, borderRadius: 2,
                    }
                ]
            },
            options: stackedBarOpts({}),
        });
    } else {
        document.getElementById('actionBar').closest('.chart-container').innerHTML =
            '<h3>User Actions</h3><div class="empty-hint">No action data yet</div>';
    }

    // 3. Token Usage — stacked bar: Prompt + Completion + Cached
    destroyChart('tokenBar');
    const hasTokens = daily.some(d => (d.token_usage||{}).total_tokens > 0);
    if (hasTokens) {
        chartInstances.tokenBar = new Chart(document.getElementById('tokenBar'), {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Prompt',
                        data: daily.map(d => (d.token_usage||{}).prompt_tokens || 0),
                        backgroundColor: COLORS.purple + 'cc',
                        borderColor: COLORS.purple, borderWidth: 1, borderRadius: 2,
                    },
                    {
                        label: 'Completion',
                        data: daily.map(d => (d.token_usage||{}).completion_tokens || 0),
                        backgroundColor: COLORS.teal + 'cc',
                        borderColor: COLORS.teal, borderWidth: 1, borderRadius: 2,
                    },
                    {
                        label: 'Cached',
                        data: daily.map(d => (d.token_usage||{}).cache_read_tokens || 0),
                        backgroundColor: COLORS.green + 'cc',
                        borderColor: COLORS.green, borderWidth: 1, borderRadius: 2,
                    }
                ]
            },
            options: stackedBarOpts({}),
        });
    } else {
        document.getElementById('tokenBar').closest('.chart-container').innerHTML =
            '<h3>Token Usage</h3><div class="empty-hint">No token data yet</div>';
    }

    // 4. Enhance Modes — stacked bar: one dataset per mode, stacked by day
    destroyChart('enhanceBar');
    // Collect all enhance mode names across the daily range
    const modeSet = new Set();
    daily.forEach(d => {
        Object.keys(d.enhance_mode_usage || {}).forEach(k => modeSet.add(k));
    });
    const modeNames = Array.from(modeSet).sort();
    if (modeNames.length > 0) {
        const datasets = modeNames.map((mode, i) => ({
            label: mode,
            data: daily.map(d => (d.enhance_mode_usage || {})[mode] || 0),
            backgroundColor: BAR_COLORS[i % BAR_COLORS.length] + 'cc',
            borderColor: BAR_COLORS[i % BAR_COLORS.length],
            borderWidth: 1, borderRadius: 2,
        }));
        chartInstances.enhanceBar = new Chart(document.getElementById('enhanceBar'), {
            type: 'bar',
            data: { labels: labels, datasets: datasets },
            options: stackedBarOpts({}),
        });
    } else {
        document.getElementById('enhanceBar').closest('.chart-container').innerHTML =
            '<h3>Enhance Modes</h3><div class="empty-hint">No enhance mode data yet</div>';
    }
}

function destroyChart(id) {
    if (chartInstances[id]) {
        chartInstances[id].destroy();
        chartInstances[id] = null;
    }
}

// --- Init ---
renderCards();
renderCharts();
</script>
</body>
</html>"""


def build_stats_payload(
    usage_stats: UsageStats,
    days: int = 30,
) -> Dict[str, Any]:
    """Collect cumulative, today, and daily stats into a single dict."""
    cumulative = usage_stats.get_stats()
    today = usage_stats.get_today_stats()
    daily = get_daily_range(usage_stats, days)
    return {
        "cumulative": cumulative,
        "today": today,
        "daily": daily,
    }


def get_daily_range(
    usage_stats: UsageStats,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """Return daily stats for the last *days* days, sorted chronologically."""
    today = date.today()
    result = []
    for i in range(days - 1, -1, -1):
        day_str = (today - timedelta(days=i)).isoformat()
        # Access internals through the lock-protected _load_daily
        with usage_stats._lock:
            data = usage_stats._load_daily(day_str)
        result.append(data)
    return result


def build_html(payload: Dict[str, Any]) -> str:
    """Build the final HTML by injecting the JSON payload into the template."""
    payload_json = json.dumps(payload, ensure_ascii=False)
    return _HTML_TEMPLATE.replace("__STATS_DATA__", payload_json)


# ---------------------------------------------------------------------------
# Panel class
# ---------------------------------------------------------------------------


class StatsChartPanel:
    """Floating NSPanel with WKWebView showing interactive usage statistics charts."""

    _WIDTH = 960
    _HEIGHT = 840

    def __init__(self) -> None:
        self._panel = None
        self._webview = None
        self._close_delegate = None

    def show(self, usage_stats: UsageStats) -> None:
        """Show the stats chart panel with current data."""
        from AppKit import NSApp

        NSApp.setActivationPolicy_(0)  # Regular (foreground)

        if self._panel is None:
            self._build_panel()

        # Inject fresh data every time show() is called
        payload = build_stats_payload(usage_stats)
        html = build_html(payload)

        from Foundation import NSURL

        self._webview.loadHTMLString_baseURL_(
            html, NSURL.URLWithString_("about:blank")
        )

        self._panel.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def close(self) -> None:
        """Close panel and restore accessory mode."""
        if self._panel is not None:
            from AppKit import NSApp

            self._panel.orderOut_(None)
            NSApp.setActivationPolicy_(1)  # Accessory (statusbar-only)

    def _build_panel(self) -> None:
        """Build NSPanel + WKWebView."""
        from AppKit import (
            NSBackingStoreBuffered,
            NSClosableWindowMask,
            NSMiniaturizableWindowMask,
            NSPanel,
            NSResizableWindowMask,
            NSStatusWindowLevel,
            NSTitledWindowMask,
        )
        from Foundation import NSMakeRect
        from WebKit import WKWebView

        style = (
            NSTitledWindowMask
            | NSClosableWindowMask
            | NSResizableWindowMask
            | NSMiniaturizableWindowMask
        )
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._WIDTH, self._HEIGHT),
            style,
            NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_("Usage Statistics")
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.center()

        # Close delegate
        delegate_cls = _get_panel_close_delegate_class()
        delegate = delegate_cls.alloc().init()
        delegate._panel_ref = self
        panel.setDelegate_(delegate)
        self._close_delegate = delegate

        # WKWebView fills content area
        webview = WKWebView.alloc().initWithFrame_(
            NSMakeRect(0, 0, self._WIDTH, self._HEIGHT)
        )
        webview.setAutoresizingMask_(0x12)  # Width + Height sizable
        panel.contentView().addSubview_(webview)

        self._panel = panel
        self._webview = webview
