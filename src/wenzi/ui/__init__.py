"""UI subpackage — panels, windows, and overlays."""

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "HistoryBrowserPanel": (".history_browser_window", "HistoryBrowserPanel"),
    "WebHistoryBrowserPanel": (".history_browser_window_web", "HistoryBrowserPanel"),
    "LiveTranscriptionOverlay": (".live_transcription_overlay", "LiveTranscriptionOverlay"),
    "LogViewerPanel": (".log_viewer_window", "LogViewerPanel"),
    "ResultPreviewPanel": (".result_window_web", "ResultPreviewPanel"),
    "SettingsPanel": (".settings_window_web", "SettingsWebPanel"),
    "StatsChartPanel": (".stats_panel", "StatsChartPanel"),
    "StreamingOverlayPanel": (".streaming_overlay", "StreamingOverlayPanel"),
    "TranslateWebViewPanel": (".translate_webview", "TranslateWebViewPanel"),
    "VocabBuildProgressPanel": (".vocab_build_window", "VocabBuildProgressPanel"),
    "VocabManagerPanel": (".vocab_manager_window", "VocabManagerPanel"),
}

__all__ = list(_LAZY_IMPORTS)


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        import importlib

        module_path, attr = _LAZY_IMPORTS[name]
        mod = importlib.import_module(module_path, __package__)
        value = getattr(mod, attr)
        globals()[name] = value  # cache for subsequent access
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
