"""UI subpackage — panels, windows, and overlays."""

from .history_browser_window import HistoryBrowserPanel
from .log_viewer_window import LogViewerPanel
from .result_window import ResultPreviewPanel
from .result_window_web import ResultPreviewPanel as WebResultPreviewPanel
from .settings_window import SettingsPanel
from .streaming_overlay import StreamingOverlayPanel
from .stats_panel import StatsChartPanel
from .translate_webview import TranslateWebViewPanel
from .vocab_build_window import VocabBuildProgressPanel

__all__ = [
    "HistoryBrowserPanel",
    "LogViewerPanel",
    "ResultPreviewPanel",
    "WebResultPreviewPanel",
    "SettingsPanel",
    "StatsChartPanel",
    "StreamingOverlayPanel",
    "TranslateWebViewPanel",
    "VocabBuildProgressPanel",
]
