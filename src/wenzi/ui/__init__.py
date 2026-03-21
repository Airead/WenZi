"""UI subpackage — panels, windows, and overlays."""

from .history_browser_window import HistoryBrowserPanel
from .history_browser_window_web import HistoryBrowserPanel as WebHistoryBrowserPanel
from .live_transcription_overlay import LiveTranscriptionOverlay
from .log_viewer_window import LogViewerPanel
from .result_window_web import ResultPreviewPanel
from .settings_window_web import SettingsWebPanel as SettingsPanel
from .streaming_overlay import StreamingOverlayPanel
from .stats_panel import StatsChartPanel
from .translate_webview import TranslateWebViewPanel
from .vocab_build_window import VocabBuildProgressPanel

__all__ = [
    "HistoryBrowserPanel",
    "WebHistoryBrowserPanel",
    "LiveTranscriptionOverlay",
    "LogViewerPanel",
    "ResultPreviewPanel",
    "SettingsPanel",
    "StatsChartPanel",
    "StreamingOverlayPanel",
    "TranslateWebViewPanel",
    "VocabBuildProgressPanel",
]
