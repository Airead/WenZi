"""Statistics visualization panel with interactive charts via WKWebView + Chart.js."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wenzi.i18n import t
from wenzi.ui.templates import load_template

if TYPE_CHECKING:
    from wenzi.usage_stats import UsageStats

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Close delegate (shared factory from web_utils)
# ---------------------------------------------------------------------------


def _get_panel_close_delegate_class():
    from wenzi.ui.web_utils import make_panel_close_delegate_class

    return make_panel_close_delegate_class("StatsChartCloseDelegate")


def build_stats_payload(
    usage_stats: UsageStats,
    days: int = 30,
) -> dict[str, Any]:
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
) -> list[dict[str, Any]]:
    """Return daily stats for the last *days* days, sorted chronologically."""
    today = date.today()
    result = []
    for i in range(days - 1, -1, -1):
        day_str = (today - timedelta(days=i)).isoformat()
        data = usage_stats.get_daily(day_str)
        result.append(data)
    return result


def _build_i18n_payload() -> dict[str, str]:
    """Build a dict of translated strings for the stats HTML template."""
    from wenzi.i18n import get_translations_for_prefix

    raw = get_translations_for_prefix("stats.")
    return {k.replace(".", "_"): v for k, v in raw.items()}


_VENDOR_DIR = Path(__file__).parent / "vendor"


_chartjs_cache: str | None = None


def _read_chartjs() -> str:
    """Read the vendored Chart.js file and wrap in a <script> tag (cached)."""
    global _chartjs_cache
    if _chartjs_cache is None:
        js_path = _VENDOR_DIR / "chart.min.js"
        _chartjs_cache = "<script>" + js_path.read_text(encoding="utf-8") + "</script>"
    return _chartjs_cache


def build_html(payload: dict[str, Any]) -> str:
    """Build the final HTML by injecting the JSON payload into the template."""
    payload_json = json.dumps(payload, ensure_ascii=False)
    i18n = _build_i18n_payload()
    i18n_json = json.dumps(i18n, ensure_ascii=False)
    html = load_template(
        "stats_panel.html",
        CHARTJS_INLINE=_read_chartjs(),
        STATS_DATA=payload_json,
        I18N_DATA=i18n_json,
        TAB_7D=i18n.get("period_7d", ""),
        TAB_14D=i18n.get("period_14d", ""),
        TAB_30D=i18n.get("period_30d", ""),
        CHART_DAILY=i18n.get("chart_daily_transcriptions", ""),
        CHART_ACTIONS=i18n.get("chart_user_actions", ""),
        CHART_TOKENS=i18n.get("chart_token_usage", ""),
        CHART_MODES=i18n.get("chart_enhance_modes", ""),
    )
    return html


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
        try:
            if self._webview is not None:
                # Destroy Chart.js instances to release canvas memory
                # in the shared Web Content process.
                self._webview.evaluateJavaScript_completionHandler_(
                    "Object.values(chartInstances).forEach("
                    "c => { if (c) c.destroy(); });"
                    "chartInstances = {};",
                    None,
                )
                from wenzi.ui.web_utils import cleanup_webview

                cleanup_webview(self._webview, handler_name=None)
                self._webview = None
            if self._panel is not None:
                self._panel.setDelegate_(None)
                if self._close_delegate is not None:
                    self._close_delegate._panel_ref = None
                self._close_delegate = None
                from wenzi.ui_helpers import release_panel_surfaces

                release_panel_surfaces(self._panel)
                self._panel.orderOut_(None)
                self._panel = None
        except Exception:
            logger.debug("stats_panel: error during close cleanup", exc_info=True)

        from AppKit import NSApp
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
        panel.setTitle_(t("stats.title"))
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
        from wenzi.ui.web_utils import lightweight_webview_config

        config = lightweight_webview_config(shared=False)
        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, self._WIDTH, self._HEIGHT),
            config,
        )
        webview.setAutoresizingMask_(0x12)  # Width + Height sizable
        panel.contentView().addSubview_(webview)

        self._panel = panel
        self._webview = webview
