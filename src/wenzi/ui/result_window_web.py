"""Web-based floating preview panel for ASR and AI enhancement results.

Uses WKWebView + WKScriptMessageHandler for a modern HTML/CSS/JS interface.
"""

from __future__ import annotations

import html
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from wenzi.ui.templates import load_template
from wenzi.ui.web_utils import cleanup_webview

if TYPE_CHECKING:
    from wenzi.enhance.manual_vocabulary import ManualVocabEntry
    from wenzi.enhance.vocabulary import HotwordDetail

logger = logging.getLogger(__name__)


def _ensure_edit_menu() -> None:
    """Ensure NSApp has a main menu with a standard Edit submenu.

    Statusbar-only apps (NSApplicationActivationPolicyAccessory) have no menu
    bar, so ⌘A/⌘C/⌘V/⌘X key equivalents are never dispatched.  Adding a
    hidden Edit menu to the main menu restores the standard responder-chain
    routing for these shortcuts.
    """
    from AppKit import NSApp, NSMenu, NSMenuItem

    main_menu = NSApp.mainMenu()
    if main_menu is None:
        main_menu = NSMenu.alloc().init()
        NSApp.setMainMenu_(main_menu)

    # Check if Edit menu already exists
    if main_menu.itemWithTitle_("Edit") is not None:
        return

    edit_menu = NSMenu.alloc().initWithTitle_("Edit")
    for title, action, key in [
        ("Undo", "undo:", "z"),
        ("Redo", "redo:", "Z"),
        ("Cut", "cut:", "x"),
        ("Copy", "copy:", "c"),
        ("Paste", "paste:", "v"),
        ("Select All", "selectAll:", "a"),
    ]:
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, action, key
        )
        edit_menu.addItem_(item)

    edit_item = NSMenuItem.alloc().init()
    edit_item.setTitle_("Edit")
    edit_item.setSubmenu_(edit_menu)
    main_menu.addItem_(edit_item)

# ---------------------------------------------------------------------------
# Hotwords table HTML
# ---------------------------------------------------------------------------


_VOCAB_TABLE_CSS = """\
:root {
    --bg: #ffffff; --text: #1d1d1f; --header-bg: #f0f0f2;
    --border: #d2d2d7; --secondary: #86868b;
    --hover: #f5f5f7; --accent: #007aff;
}
@media (prefers-color-scheme: dark) {
    :root {
        --bg: #1d1d1f; --text: #c8c8cc; --header-bg: #2c2c2e;
        --border: #48484a; --secondary: #98989d;
        --hover: #2c2c2e; --accent: #0a84ff;
    }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Mono",
                 Menlo, monospace;
    font-size: 12px; color: var(--text); background: var(--bg);
    padding: 0; overflow: auto;
}
table {
    width: 100%; border-collapse: collapse; table-layout: auto;
}
th {
    background: var(--header-bg); font-weight: 600; font-size: 11px;
    padding: 6px 8px; text-align: left; border-bottom: 1px solid var(--border);
    white-space: nowrap; color: var(--secondary);
}
td {
    padding: 5px 8px; border-bottom: 1px solid var(--border);
    white-space: nowrap; vertical-align: top;
}
tr:hover { background: var(--hover); }
.cell-term { font-weight: 600; color: var(--accent); }
.cell-variant { color: var(--secondary); font-size: 11px; }
.cell-source {
    font-size: 10px; font-weight: 600; text-transform: uppercase;
    color: var(--secondary);
}
.cell-num { text-align: right; font-variant-numeric: tabular-nums; }
.cell-time {
    color: var(--secondary); font-size: 11px;
    font-family: "SF Mono", Menlo, monospace;
}"""

_SECTION_CSS = """\
.section { padding: 10px 12px; }
.section + .section { border-top: 1px solid var(--border); padding-top: 10px; }
.section-title {
    font-weight: 600; font-size: 11px; color: var(--secondary);
    text-transform: uppercase; margin-bottom: 6px;
}
.context-text { font-size: 12px; }
.ctx-row { display: flex; gap: 8px; padding: 2px 0; }
.ctx-key {
    flex-shrink: 0; width: 60px; text-align: right;
    font-weight: 600; font-size: 11px; color: var(--secondary);
}
.ctx-val { font-family: "SF Mono", Menlo, monospace; }"""

_FMTDATE_JS = """\
<script>
function fmtDate(ts) {
    if (!ts || ts.length < 10) return '';
    var d = new Date(ts);
    if (isNaN(d.getTime())) return '';
    var diff = Math.floor((Date.now() - d.getTime()) / 1000);
    if (diff < 60) return diff + 's';
    if (diff < 3600) return Math.floor(diff / 60) + 'm';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h';
    if (diff < 2592000) return Math.floor(diff / 86400) + 'd';
    if (diff < 31536000) return Math.floor(diff / 2592000) + 'mo';
    return Math.floor(diff / 31536000) + 'y';
}
document.querySelectorAll('.cell-time[data-ts]').forEach(function(el) {
    var ts = el.getAttribute('data-ts');
    el.textContent = fmtDate(ts);
    if (ts) el.title = ts;
});
</script>"""


def _build_vocab_table_html(
    entries: list[HotwordDetail] | list[ManualVocabEntry],
) -> tuple[str, str]:
    """Build vocab table rows (tbody) and header row (thead tr) HTML.

    For HotwordDetail entries: shows ASR Miss and ASR Hit columns.
    For ManualVocabEntry entries: shows LLM Miss and LLM Hit columns.
    """
    from wenzi.enhance.manual_vocabulary import ManualVocabEntry
    from wenzi.i18n import t

    is_llm_vocab = bool(entries) and isinstance(entries[0], ManualVocabEntry)

    if is_llm_vocab:
        entries = sorted(entries, key=lambda e: e.llm_miss_count, reverse=True)
    else:
        entries = sorted(entries, key=lambda e: e.asr_miss_count, reverse=True)

    rows: list[str] = []
    for e in entries:
        term = html.escape(e.term)
        variant = html.escape(e.variant)
        source = html.escape(e.source)
        first_seen_attr = html.escape(e.first_seen, quote=True)
        if is_llm_vocab:
            col1 = e.llm_miss_count
            col2 = e.llm_hit_count
        else:
            col1 = e.asr_miss_count
            col2 = e.asr_hit_count
        rows.append(
            f"<tr>"
            f"<td class='cell-term'>{term}</td>"
            f"<td class='cell-variant'>{variant}</td>"
            f"<td class='cell-source'>{source}</td>"
            f"<td class='cell-num'>{col1}</td>"
            f"<td class='cell-num'>{col2}</td>"
            f'<td class="cell-time" data-ts="{first_seen_attr}"></td>'
            f"</tr>"
        )

    th_term = html.escape(t("preview.hotwords_table.term"))
    th_variant = html.escape(t("preview.hotwords_table.variant"))
    th_source = html.escape(t("preview.hotwords_table.source"))
    th_first_seen = html.escape(t("preview.hotwords_table.first_seen"))
    if is_llm_vocab:
        th_col1 = html.escape(t("preview.hotwords_table.llm_miss"))
        th_col2 = html.escape(t("preview.hotwords_table.llm_hit"))
    else:
        th_col1 = html.escape(t("preview.hotwords_table.asr_miss"))
        th_col2 = html.escape(t("preview.hotwords_table.asr_hit"))
    thead = (
        f"<th>{th_term}</th><th>{th_variant}</th><th>{th_source}</th>"
        f"<th>{th_col1}</th><th>{th_col2}</th><th>{th_first_seen}</th>"
    )
    return "\n".join(rows), thead


def _build_context_section_html(context_text: str) -> str:
    """Build the Input Context section HTML (empty string if no context)."""
    from wenzi.i18n import t

    if not context_text:
        return ""

    items: list[str] = []
    for line in context_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            key = html.escape(key.strip())
            val = html.escape(val.strip())
            items.append(
                f'<div class="ctx-row">'
                f'<span class="ctx-key">{key}</span>'
                f'<span class="ctx-val">{val}</span>'
                f'</div>'
            )
        elif line.strip():
            items.append(
                f'<div class="ctx-row">'
                f'<span class="ctx-val">{html.escape(line.strip())}</span>'
                f'</div>'
            )

    if not items:
        return ""

    lbl = html.escape(t("preview.context_panel.input_context"))
    body = "\n".join(items)
    return f"""<div class="section">
<div class="section-title">{lbl}</div>
<div class="context-text">{body}</div>
</div>"""


def _build_hotwords_html(
    details: list[HotwordDetail], context_text: str = "",
) -> str:
    """Build an HTML page with a styled table of hotword details."""
    tbody, thead = _build_vocab_table_html(details)
    context_section = _build_context_section_html(context_text)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
{_VOCAB_TABLE_CSS}
thead {{ position: sticky; top: 0; z-index: 1; }}
{_SECTION_CSS}
</style>
</head>
<body>
{context_section}
<table>
<thead><tr>    {thead}
</tr></thead>
<tbody>
{tbody}
</tbody>
</table>
{_FMTDATE_JS}
</body>
</html>"""


def _build_context_panel_html(
    context_text: str, vocab_entries: list[ManualVocabEntry],
) -> str:
    """Build HTML page with input context and LLM vocabulary table."""
    from wenzi.i18n import t

    context_section = _build_context_section_html(context_text)

    vocab_section = ""
    if vocab_entries:
        lbl_vocab = html.escape(t("preview.context_panel.llm_vocab"))
        tbody, thead = _build_vocab_table_html(vocab_entries)
        vocab_section = f"""<div class="section">
<div class="section-title">{lbl_vocab} ({len(vocab_entries)})</div>
<table>
<thead><tr>    {thead}
</tr></thead>
<tbody>
{tbody}
</tbody>
</table>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
{_VOCAB_TABLE_CSS}
{_SECTION_CSS}
</style>
</head>
<body>
{context_section}
{vocab_section}
{_FMTDATE_JS}
</body>
</html>"""


# ---------------------------------------------------------------------------
# WKNavigationDelegate (lazy-created to avoid PyObjC import at module level)
# ---------------------------------------------------------------------------
_NavigationDelegate = None


def _get_navigation_delegate_class():
    global _NavigationDelegate
    if _NavigationDelegate is None:
        import objc
        import WebKit  # noqa: F401
        from Foundation import NSObject

        WKNavigationDelegate = objc.protocolNamed("WKNavigationDelegate")

        class WebPreviewNavigationDelegate(
            NSObject, protocols=[WKNavigationDelegate]
        ):
            _panel_ref = None

            def webView_didFinishNavigation_(self, webview, navigation):
                if self._panel_ref is not None:
                    self._panel_ref._on_page_loaded()

        _NavigationDelegate = WebPreviewNavigationDelegate
    return _NavigationDelegate


# ---------------------------------------------------------------------------
# Close delegate (shared factory from web_utils)
# ---------------------------------------------------------------------------


def _get_panel_close_delegate_class():
    from wenzi.ui.web_utils import make_panel_close_delegate_class

    return make_panel_close_delegate_class(
        "WebResultPanelCloseDelegate", close_method="cancelClicked_"
    )


# ---------------------------------------------------------------------------
# WKScriptMessageHandler (lazy-created)
# ---------------------------------------------------------------------------
_MessageHandler = None


def _get_message_handler_class():
    global _MessageHandler
    if _MessageHandler is None:
        import objc

        # Load WebKit framework first so the protocol is available
        import WebKit  # noqa: F401
        from Foundation import NSObject

        WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")
        logger.debug("WKScriptMessageHandler protocol: %s", WKScriptMessageHandler)

        class WebPreviewMessageHandler(
            NSObject, protocols=[WKScriptMessageHandler]
        ):
            _panel_ref = None

            def userContentController_didReceiveScriptMessage_(
                self, controller, message
            ):
                if self._panel_ref is None:
                    return
                raw = message.body()
                # WKWebView returns NSDictionary with ObjC value types;
                # JSON roundtrip converts everything to native Python types
                try:
                    from Foundation import NSJSONSerialization
                    json_data, _ = (
                        NSJSONSerialization
                        .dataWithJSONObject_options_error_(raw, 0, None)
                    )
                    body = json.loads(bytes(json_data))
                except Exception:
                    logger.warning("Cannot convert message body: %r", raw)
                    return
                self._panel_ref._handle_js_message(body)

        _MessageHandler = WebPreviewMessageHandler
    return _MessageHandler


# ---------------------------------------------------------------------------
# Panel class
# ---------------------------------------------------------------------------


class ResultPreviewPanel:
    """WKWebView-based floating preview panel.

    Drop-in replacement for the AppKit-based ResultPreviewPanel, with the
    same public API surface.
    """

    _PANEL_WIDTH = 640
    _PANEL_HEIGHT = 396  # Golden ratio: 640 / 1.618
    _DIFF_PANEL_WIDTH = 280

    def __init__(self) -> None:
        self._panel = None
        self._webview = None
        self._close_delegate = None
        self._message_handler = None
        self._on_confirm: Callable | None = None
        self._on_cancel: Callable | None = None
        self._on_mode_change: Callable[[str], None] | None = None
        self._on_stt_model_change: Callable[[int], None] | None = None
        self._on_llm_model_change: Callable[[int], None] | None = None
        self._on_punc_toggle: Callable[[bool], None] | None = None
        self._on_thinking_toggle: Callable[[bool], None] | None = None
        self._on_google_translate: Callable[[], None] | None = None
        self._on_select_history: Callable[[int], None] | None = None
        self._preview_history_items: list = []
        self._user_edited = False
        self._show_enhance = False
        self._asr_text = ""
        self._available_modes: list[tuple[str, str]] = []
        self._current_mode: str = "off"
        self._asr_info: str = ""
        self._asr_wav_data: bytes | None = None
        self._asr_sound = None
        self._enhance_info: str = ""
        self._enhance_request_id: int = 0
        self._asr_request_id: int = 0
        self._system_prompt: str = ""
        self._input_context_text: str = ""
        self._stt_models: list[str] = []
        self._llm_models: list[str] = []
        self._stt_current_index: int = 0
        self._llm_current_index: int = 0
        self._source: str = "voice"
        self._punc_enabled: bool = True
        self._thinking_enabled: bool = False
        self._thinking_text: str = ""
        self._hotwords_detail: list[HotwordDetail] = []
        self._llm_vocab_detail: list[ManualVocabEntry] = []
        self._loading_timer = None
        self._loading_seconds: int = 0
        self._playback_timer = None
        self._translate_webview = None
        self._hotwords_webview_panel = None
        self._context_webview_panel = None
        self._info_panel = None
        self._page_loaded: bool = False
        self._pending_js: list[str] = []
        self._navigation_delegate = None
        # Diff panel / manual vocabulary
        self._on_add_manual_vocab: Callable | None = None
        self._on_remove_manual_vocab: Callable | None = None
        self._on_diff_panel_toggle: Callable[[bool], None] | None = None
        self._diff_panel_open: bool = False
        self._diff_panel_original_x: float | None = None
        self._enhanced_text_cache: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def warmup(self) -> None:
        """Pre-create NSPanel + WKWebView to eliminate first-show latency.

        Call via AppHelper.callAfter() after the event loop starts.
        The pre-created panel and webview are reused by _build_panel().
        """
        if self._panel is not None:
            return
        try:
            from AppKit import (
                NSBackingStoreBuffered,
                NSClosableWindowMask,
                NSPanel,
                NSStatusWindowLevel,
                NSTitledWindowMask,
            )
            from Foundation import NSMakeRect
            from WebKit import WKUserContentController, WKWebView

            height = self._PANEL_HEIGHT

            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, self._PANEL_WIDTH, height),
                NSTitledWindowMask | NSClosableWindowMask,
                NSBackingStoreBuffered,
                True,  # defer=True, don't create window server resources yet
            )
            panel.setLevel_(NSStatusWindowLevel)
            panel.setFloatingPanel_(True)
            panel.setHidesOnDeactivate_(False)

            # Close delegate
            delegate_cls = _get_panel_close_delegate_class()
            delegate = delegate_cls.alloc().init()
            delegate._panel_ref = self
            panel.setDelegate_(delegate)
            self._close_delegate = delegate

            # WKWebView with message handler
            from wenzi.ui.web_utils import lightweight_webview_config

            config = lightweight_webview_config(shared=False)
            content_controller = WKUserContentController.alloc().init()
            handler_cls = _get_message_handler_class()
            handler = handler_cls.alloc().init()
            handler._panel_ref = self
            content_controller.addScriptMessageHandler_name_(handler, "action")
            config.setUserContentController_(content_controller)

            webview = WKWebView.alloc().initWithFrame_configuration_(
                NSMakeRect(0, 0, self._PANEL_WIDTH, height),
                config,
            )
            webview.setAutoresizingMask_(0x12)
            webview.setValue_forKey_(False, "drawsBackground")

            # Navigation delegate
            nav_delegate_cls = _get_navigation_delegate_class()
            nav_delegate = nav_delegate_cls.alloc().init()
            nav_delegate._panel_ref = self
            webview.setNavigationDelegate_(nav_delegate)

            self._panel = panel
            self._webview = webview
            self._message_handler = handler
            self._navigation_delegate = nav_delegate
            self._page_loaded = False
            self._pending_js = []

            logger.debug("WebResultPreviewPanel warmup complete")
        except Exception:
            logger.debug("WebResultPreviewPanel warmup failed", exc_info=True)

    def show(
        self,
        asr_text: str,
        show_enhance: bool,
        on_confirm: Callable,
        on_cancel: Callable,
        available_modes: list[tuple[str, str]] | None = None,
        current_mode: str | None = None,
        on_mode_change: Callable[[str], None] | None = None,
        asr_info: str = "",
        asr_wav_data: bytes | None = None,
        enhance_info: str = "",
        stt_models: list[str] | None = None,
        stt_current_index: int = 0,
        on_stt_model_change: Callable[[int], None] | None = None,
        llm_models: list[str] | None = None,
        llm_current_index: int = 0,
        on_llm_model_change: Callable[[int], None] | None = None,
        source: str = "voice",
        punc_enabled: bool = True,
        on_punc_toggle: Callable[[bool], None] | None = None,
        thinking_enabled: bool = False,
        on_thinking_toggle: Callable[[bool], None] | None = None,
        on_google_translate: Callable[[], None] | None = None,
        on_select_history: Callable[[int], None] | None = None,
        preview_history_items: list | None = None,
        animate_from_frame: object = None,
        on_add_manual_vocab: Callable | None = None,
        on_remove_manual_vocab: Callable | None = None,
        on_diff_panel_toggle: Callable[[bool], None] | None = None,
        diff_panel_open: bool = False,
    ) -> None:
        """Show the preview panel with ASR text."""
        self._stop_playback()
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self._on_mode_change = on_mode_change
        self._on_stt_model_change = on_stt_model_change
        self._on_llm_model_change = on_llm_model_change
        self._on_punc_toggle = on_punc_toggle
        self._punc_enabled = punc_enabled
        self._on_thinking_toggle = on_thinking_toggle
        self._thinking_enabled = thinking_enabled
        self._on_google_translate = on_google_translate
        self._on_select_history = on_select_history
        self._on_add_manual_vocab = on_add_manual_vocab
        self._on_remove_manual_vocab = on_remove_manual_vocab
        self._on_diff_panel_toggle = on_diff_panel_toggle
        self._diff_panel_open = diff_panel_open
        self._preview_history_items = preview_history_items or []
        self._user_edited = False
        self._enhanced_text_cache = ""
        self._show_enhance = show_enhance
        self._asr_text = asr_text
        self._source = source
        self._available_modes = available_modes or []
        self._current_mode = current_mode or "off"
        self._asr_info = asr_info
        self._asr_wav_data = asr_wav_data
        self._enhance_info = enhance_info
        self._enhance_request_id = 0
        self._asr_request_id = 0
        self._stt_models = stt_models or []
        self._stt_current_index = stt_current_index
        self._llm_models = llm_models or []
        self._llm_current_index = llm_current_index
        self._thinking_text = ""

        self._build_panel()

        if animate_from_frame is not None:
            from AppKit import NSAnimationContext

            target_frame = self._panel.frame()
            self._panel.setFrame_display_(animate_from_frame, False)
            self._panel.setAlphaValue_(0.0)
            self._panel.makeKeyAndOrderFront_(None)

            NSAnimationContext.beginGrouping()
            ctx = NSAnimationContext.currentContext()
            ctx.setDuration_(0.3)
            self._panel.animator().setFrame_display_(target_frame, True)
            self._panel.animator().setAlphaValue_(1.0)
            NSAnimationContext.endGrouping()
        else:
            self._panel.makeKeyAndOrderFront_(None)

        from AppKit import NSApp

        NSApp.activateIgnoringOtherApps_(True)

    def set_enhance_result(
        self, text: str, request_id: int = 0,
        usage: dict | None = None, system_prompt: str = "",
    ) -> None:
        """Update the AI enhancement result."""
        if self._webview is None:
            return

        from PyObjCTools import AppHelper

        def _update():
            if self._webview is None:
                return
            if request_id != 0 and request_id != self._enhance_request_id:
                return
            self._stop_loading_timer()
            self._eval_js(f"setEnhanceResult({json.dumps(text)})")
            if system_prompt:
                self._system_prompt = system_prompt
                self._eval_js("enablePromptButton()")
            suffix = self._format_token_suffix(usage)
            self._eval_js(f"setEnhanceInfo({json.dumps(self._enhance_label_text(suffix))})")
            if not self._user_edited:
                self._eval_js(f"setFinalText({json.dumps(text)})")

        AppHelper.callAfter(_update)

    def append_thinking_text(
        self, chunk: str, request_id: int = 0,
        thinking_tokens: int = 0,
    ) -> None:
        """Append a thinking/reasoning text chunk."""
        if self._webview is None:
            return

        self._thinking_text += chunk

        from PyObjCTools import AppHelper

        def _update():
            if self._webview is None:
                return
            if request_id != 0 and request_id != self._enhance_request_id:
                return
            self._stop_loading_timer()
            self._eval_js(f"appendThinkingText({json.dumps(chunk)})")
            if thinking_tokens > 0:
                suffix = f"\u25b6 Thinking: {thinking_tokens:,} chars"
                self._eval_js(f"setEnhanceInfo({json.dumps(self._enhance_label_text(suffix))})")

        AppHelper.callAfter(_update)

    def clear_enhance_text(self, request_id: int = 0) -> None:
        """Clear the enhancement text view content."""
        if self._webview is None:
            return

        from PyObjCTools import AppHelper

        def _update():
            if self._webview is None:
                return
            if request_id != 0 and request_id != self._enhance_request_id:
                return
            self._eval_js("clearEnhanceText()")

        AppHelper.callAfter(_update)

    def append_enhance_text(
        self, chunk: str, request_id: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """Append a text chunk to the AI enhancement area (streaming)."""
        if self._webview is None:
            return

        from PyObjCTools import AppHelper

        def _update():
            if self._webview is None:
                return
            if request_id != 0 and request_id != self._enhance_request_id:
                return
            self._stop_loading_timer()
            self._eval_js(f"appendEnhanceText({json.dumps(chunk)})")
            if completion_tokens > 0:
                suffix = f"\u25b6 Chars: \u2193{completion_tokens:,}"
                self._eval_js(f"setEnhanceInfo({json.dumps(self._enhance_label_text(suffix))})")

        AppHelper.callAfter(_update)

    def update_system_prompt(self, system_prompt: str) -> None:
        """Update the stored system prompt and enable the prompt button."""
        if not system_prompt:
            return

        from PyObjCTools import AppHelper

        def _update():
            self._system_prompt = system_prompt
            if self._webview is not None:
                self._eval_js("enablePromptButton()")

        AppHelper.callAfter(_update)

    def set_enhance_complete(
        self, request_id: int = 0, usage: dict | None = None,
        system_prompt: str = "", final_text: str | None = None,
    ) -> None:
        """Mark streaming enhancement as complete."""
        if self._webview is None:
            return

        from PyObjCTools import AppHelper

        def _update():
            if self._webview is None:
                return
            if request_id != 0 and request_id != self._enhance_request_id:
                return
            self._stop_loading_timer()
            if system_prompt:
                self._system_prompt = system_prompt
            suffix = self._format_token_suffix(usage)
            has_thinking = bool(self._thinking_text)
            ft_json = json.dumps(final_text) if final_text is not None else "null"
            self._eval_js(
                f"finishThinkingSpan();"
                f"setEnhanceComplete({json.dumps(self._enhance_label_text(suffix))},"
                f"{json.dumps(has_thinking)},{ft_json})"
            )
            if system_prompt:
                self._eval_js("enablePromptButton()")
            if self._input_context_text or self._llm_vocab_detail:
                self._eval_js("enableContextButton()")

        AppHelper.callAfter(_update)

    def replay_cached_result(
        self, display_text: str, usage: dict | None,
        system_prompt: str, thinking_text: str,
        final_text: str | None,
    ) -> None:
        """Instantly display a cached enhancement result (no streaming)."""
        if self._webview is None:
            return

        from PyObjCTools import AppHelper

        def _update():
            if self._webview is None:
                return
            self._stop_loading_timer()
            self._system_prompt = system_prompt
            self._thinking_text = thinking_text
            suffix = self._format_token_suffix(usage)
            if suffix:
                suffix += " [cached]"
            else:
                suffix = "[cached]"
            has_thinking = bool(thinking_text)
            ft_json = json.dumps(final_text) if final_text is not None else "null"
            self._eval_js(
                f"replayCachedResult({json.dumps(display_text)},"
                f"{json.dumps(self._enhance_label_text(suffix))},"
                f"{json.dumps(has_thinking)},{ft_json})"
            )
            if system_prompt:
                self._eval_js("enablePromptButton()")
            if self._input_context_text or self._llm_vocab_detail:
                self._eval_js("enableContextButton()")

        AppHelper.callAfter(_update)

    def load_history_record(
        self,
        asr_text: str,
        enhanced_text: str | None,
        final_text: str,
        enhance_mode: str,
        has_audio: bool,
        asr_info: str = "",
        system_prompt: str = "",
        thinking_text: str = "",
        token_usage: dict | None = None,
    ) -> None:
        """Load a history record into the preview panel."""
        if self._webview is None:
            return

        from PyObjCTools import AppHelper

        # Token info only — mode is shown via the segmented control tab
        enhance_info = self._format_token_suffix(token_usage)

        data = {
            "asrText": asr_text,
            "enhancedText": enhanced_text,
            "finalText": final_text,
            "enhanceMode": enhance_mode,
            "enhanceInfo": enhance_info,
            "hasAudio": has_audio,
            "asrInfo": asr_info,
            "hasPrompt": bool(system_prompt),
            "hasThinking": bool(thinking_text),
            "hasContext": bool(self._input_context_text),
        }

        def _update():
            if self._webview is None:
                return
            self._asr_text = asr_text
            self._user_edited = False
            self._system_prompt = system_prompt
            self._thinking_text = thinking_text
            self._eval_js(f"loadHistoryRecord({json.dumps(data)})")

        AppHelper.callAfter(_update)

    def set_input_context(self, text: str) -> None:
        """Cache input context display text.

        The button is enabled later by ``set_enhance_complete()``,
        ``replay_cached_result()``, or ``load_history_record()`` —
        i.e. only after the webview is guaranteed to be ready.
        """
        self._input_context_text = text

    def set_llm_vocab(self, entries: list[ManualVocabEntry]) -> None:
        """Cache LLM vocabulary entries for display in the context panel."""
        self._llm_vocab_detail = list(entries)

    def set_enhance_label(self, suffix: str, request_id: int = 0) -> None:
        """Update only the enhancement label text."""
        from PyObjCTools import AppHelper

        def _update():
            if self._webview is None:
                return
            if request_id != 0 and request_id != self._enhance_request_id:
                return
            self._eval_js(f"setEnhanceInfo({json.dumps(self._enhance_label_text(suffix))})")

        AppHelper.callAfter(_update)

    def set_enhance_loading(self) -> None:
        """Show loading state in the enhancement section."""
        from PyObjCTools import AppHelper

        def _update():
            self._user_edited = False
            self._show_enhance = True
            self._thinking_text = ""
            if self._webview is not None:
                self._eval_js("setEnhanceLoading()")
            self._stop_loading_timer()
            self._loading_seconds = 0
            from Foundation import NSTimer
            self._loading_timer = (
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    1.0, self, b"tickLoadingTimer:", None, True,
                )
            )

        AppHelper.callAfter(_update)

    def tickLoadingTimer_(self, timer) -> None:
        """NSTimer callback: increment seconds counter and update label."""
        self._loading_seconds += 1
        if self._webview is not None:
            self._eval_js(f"updateLoadingTimer({self._loading_seconds})")

    def set_enhance_step_info(self, step: int, total: int, label: str) -> None:
        """Update enhance label to show chain step progress."""
        from PyObjCTools import AppHelper

        def _update():
            if self._webview is not None:
                text = f"\u23f3 Step {step}/{total}: {label}"
                self._eval_js(f"setStepInfo({json.dumps(text)})")

        AppHelper.callAfter(_update)

    def set_enhance_off(self) -> None:
        """Show off state: clear enhancement and restore ASR text to final field."""
        from PyObjCTools import AppHelper

        def _update():
            self._stop_loading_timer()
            self._system_prompt = ""
            self._thinking_text = ""
            if self._webview is not None:
                self._eval_js("setEnhanceOff()")
                if not self._user_edited:
                    self._eval_js(f"setFinalText({json.dumps(self._asr_text)})")
            self._show_enhance = False

        AppHelper.callAfter(_update)

    def set_asr_loading(self) -> None:
        """Show loading state in the ASR section for re-transcription."""
        from PyObjCTools import AppHelper

        self._asr_request_id += 1

        def _update():
            if self._webview is not None:
                self._eval_js("setAsrLoading()")

        AppHelper.callAfter(_update)

    def set_asr_result(self, text: str, asr_info: str = "", request_id: int = 0) -> None:
        """Update ASR result after re-transcription."""
        from PyObjCTools import AppHelper

        def _update():
            if self._webview is None:
                return
            if request_id != 0 and request_id != self._asr_request_id:
                return
            self._asr_text = text
            self._asr_info = asr_info
            self._eval_js(
                f"setAsrResult({json.dumps(text)},{json.dumps(asr_info)})"
            )

        AppHelper.callAfter(_update)

    def set_hotwords(self, details: list[HotwordDetail]) -> None:
        """Cache hotword details and update the button count in the UI."""
        self._hotwords_detail = details

        from PyObjCTools import AppHelper

        def _update():
            if self._webview is not None:
                self._eval_js(f"setHotwordsCount({len(details)})")

        AppHelper.callAfter(_update)

    def set_stt_popup_index(self, index: int) -> None:
        """Set the STT popup selection (for rollback on failure)."""
        from PyObjCTools import AppHelper

        def _update():
            if self._webview is not None:
                self._eval_js(f"setSttPopupIndex({index})")

        AppHelper.callAfter(_update)

    @property
    def asr_request_id(self) -> int:
        return self._asr_request_id

    @property
    def enhance_request_id(self) -> int:
        return self._enhance_request_id

    @enhance_request_id.setter
    def enhance_request_id(self, value: int) -> None:
        self._enhance_request_id = value

    @property
    def hotwords_detail(self) -> list[HotwordDetail]:
        return self._hotwords_detail

    @property
    def is_visible(self) -> bool:
        return self._panel is not None and self._panel.isVisible()

    def close(self) -> None:
        """Close the panel."""
        self._stop_playback()
        self._stop_loading_timer()
        if self._panel is not None:
            self._panel.setDelegate_(None)
            self._close_delegate = None
            from wenzi.ui_helpers import release_panel_surfaces

            release_panel_surfaces(self._panel)
            self._panel.orderOut_(None)
            self._panel = None
        cleanup_webview(self._webview)
        self._webview = None
        if self._message_handler is not None:
            self._message_handler._panel_ref = None
        if self._navigation_delegate is not None:
            self._navigation_delegate._panel_ref = None
        self._message_handler = None
        self._navigation_delegate = None
        self._page_loaded = False
        self._pending_js = []
        # Close child panels to prevent memory leaks
        for attr in (
            '_hotwords_webview_panel',
            '_context_webview_panel',
            '_info_panel',
        ):
            child = getattr(self, attr, None)
            if child is not None:
                try:
                    # Release WKWebView canvas/IOSurface memory before closing
                    for subview in (child.contentView().subviews() or []):
                        if hasattr(subview, 'stopLoading_'):
                            cleanup_webview(subview, handler_name=None)
                    child.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        if self._translate_webview is not None:
            try:
                self._translate_webview.close()
            except Exception:
                pass
            self._translate_webview = None
        self._asr_wav_data = None
        self._on_confirm = None
        self._on_cancel = None
        self._on_add_manual_vocab = None
        self._on_remove_manual_vocab = None
        self._on_diff_panel_toggle = None

    # ------------------------------------------------------------------
    # Callbacks from JavaScript
    # ------------------------------------------------------------------

    def cancelClicked_(self, sender) -> None:
        callback = self._on_cancel
        self.close()
        if callback is not None:
            callback()

    def _handle_js_message(self, body: dict) -> None:
        """Dispatch messages from JavaScript."""
        msg_type = body.get("type", "")
        logger.debug("Handling JS message: type=%s body=%s", msg_type, body)

        if msg_type == "confirm":
            self._user_edited = body.get("userEdited", False)
            copy_to_clipboard = body.get("copyToClipboard", False)
            text = body.get("text", "")
            correction_info = None
            if self._user_edited and self._show_enhance:
                enhanced = body.get("enhanceText", "")
                correction_info = {
                    "asr_text": self._asr_text,
                    "enhanced_text": enhanced,
                    "final_text": text,
                }
            callback = self._on_confirm
            self.close()
            if callback is not None:
                callback(text, correction_info, copy_to_clipboard)

        elif msg_type == "cancel":
            self.cancelClicked_(None)

        elif msg_type == "modeChange":
            index = body.get("index", 0)
            if index < len(self._available_modes):
                mode_id = self._available_modes[index][0]
                if mode_id != self._current_mode:
                    self._current_mode = mode_id
                    if self._on_mode_change is not None:
                        self._on_mode_change(mode_id)

        elif msg_type == "sttModelChange":
            if self._on_stt_model_change is not None:
                self._on_stt_model_change(body.get("index", 0))

        elif msg_type == "llmModelChange":
            if self._on_llm_model_change is not None:
                self._on_llm_model_change(body.get("index", 0))

        elif msg_type == "puncToggle":
            enabled = body.get("enabled", True)
            self._punc_enabled = enabled
            if self._on_punc_toggle is not None:
                self._on_punc_toggle(enabled)

        elif msg_type == "thinkingToggle":
            enabled = body.get("enabled", False)
            logger.info("Thinking toggle: enabled=%r (type=%s)", enabled, type(enabled).__name__)
            self._thinking_enabled = enabled
            if self._on_thinking_toggle is not None:
                self._on_thinking_toggle(enabled)

        elif msg_type == "showHotwords":
            if self._hotwords_detail:
                self._show_hotwords_panel(self._hotwords_detail)

        elif msg_type == "showThinking":
            if self._thinking_text:
                self._show_info_panel("Thinking", self._thinking_text)

        elif msg_type == "showPrompt":
            if self._system_prompt:
                self._show_info_panel("System Prompt", self._system_prompt)

        elif msg_type == "showContext":
            if self._input_context_text or self._llm_vocab_detail:
                self._show_context_panel()

        elif msg_type == "toggleAudio":
            if self._asr_wav_data:
                if self._asr_sound is not None and self._asr_sound.isPlaying():
                    self._stop_playback()
                else:
                    self._play_wav(self._asr_wav_data)

        elif msg_type == "saveAudio":
            if self._asr_wav_data:
                self._save_wav(self._asr_wav_data)

        elif msg_type == "googleTranslate":
            text = body.get("text", "").strip()
            if text:
                from .translate_webview import TranslateWebViewPanel
                if self._translate_webview is None:
                    self._translate_webview = TranslateWebViewPanel()
                self._translate_webview.show(text)
                if self._on_google_translate is not None:
                    self._on_google_translate()

        elif msg_type == "selectHistory":
            index = body.get("index", 0)
            if self._on_select_history is not None:
                self._on_select_history(index)

        elif msg_type == "userEdit":
            self._user_edited = True

        elif msg_type == "addManualVocab":
            variant = body.get("variant", "")
            term = body.get("term", "")
            from wenzi.enhance.manual_vocabulary import SOURCE_ASR
            source = body.get("source", SOURCE_ASR)
            if variant and term and self._on_add_manual_vocab is not None:
                self._on_add_manual_vocab(variant, term, source)

        elif msg_type == "removeManualVocab":
            variant = body.get("variant", "")
            term = body.get("term", "")
            if variant and term and self._on_remove_manual_vocab is not None:
                self._on_remove_manual_vocab(variant, term)

        elif msg_type == "diffPanelToggle":
            is_open = body.get("open", False)
            self._resize_for_diff_panel(is_open)
            if self._on_diff_panel_toggle is not None:
                self._on_diff_panel_toggle(is_open)

        elif msg_type == "computeUserDiffs":
            final_text = body.get("finalText", "")
            if self._enhanced_text_cache and final_text:
                try:
                    from wenzi.enhance.text_diff import extract_word_pairs
                    pairs = extract_word_pairs(self._enhanced_text_cache, final_text)
                    self._eval_js(f"setUserDiffs({json.dumps(self._pairs_to_dicts(pairs))})")
                except Exception as e:
                    logger.warning("Failed to compute user diffs: %s", e)

    # ------------------------------------------------------------------
    # Diff panel public API
    # ------------------------------------------------------------------

    @staticmethod
    def _pairs_to_dicts(pairs: list[tuple[str, str]]) -> list[dict]:
        return [{"variant": v, "term": t} for v, t in pairs]

    def set_asr_diffs(self, pairs: list[tuple[str, str]]) -> None:
        """Push ASR→Enhanced diff pairs to the diff panel."""
        self._push_js(f"setAsrDiffs({json.dumps(self._pairs_to_dicts(pairs))})")

    def set_user_diffs(self, pairs: list[tuple[str, str]]) -> None:
        """Push Enhanced→Final diff pairs to the diff panel."""
        self._push_js(f"setUserDiffs({json.dumps(self._pairs_to_dicts(pairs))})")

    def set_manual_vocab_state(self, entries: list[dict]) -> None:
        """Tell JS which diff pairs are already in manual vocab."""
        self._push_js(f"setManualVocabState({json.dumps(entries)})")

    def set_vocab_hits(self, hits: list[dict]) -> None:
        """Push vocab hit info cards to the diff panel."""
        self._push_js(f"setVocabHits({json.dumps(hits)})")

    def _push_js(self, js_call: str) -> None:
        """Schedule a JS call on the main thread (no-op if webview is gone)."""
        if self._webview is None:
            return
        from PyObjCTools import AppHelper

        def _update():
            if self._webview is not None:
                self._eval_js(js_call)

        AppHelper.callAfter(_update)

    @property
    def enhanced_text(self) -> str:
        """Return the cached enhanced text."""
        return self._enhanced_text_cache

    def cache_enhanced_text(self, text: str) -> None:
        """Cache enhanced text for computing user-edit diffs."""
        self._enhanced_text_cache = text

    def clear_diffs(self) -> None:
        """Clear all diff and vocab hit cards."""
        self._push_js("setAsrDiffs([]); setUserDiffs([]); setVocabHits([])")

    def _resize_for_diff_panel(self, is_open: bool) -> None:
        """Instantly resize the NSPanel for diff panel open/close."""
        if self._panel is None:
            return
        width = self._PANEL_WIDTH + (self._DIFF_PANEL_WIDTH if is_open else 0)
        frame = self._panel.frame()
        x = frame.origin.x
        # Shift left if expanding would overflow the screen right edge
        if is_open:
            self._diff_panel_original_x = x
            screen = self._screen_for_mouse()
            if screen:
                vis = screen.visibleFrame()
                max_right = vis.origin.x + vis.size.width
                overflow = (x + width) - max_right
                if overflow > 0:
                    x = max(vis.origin.x, x - overflow)
        else:
            if self._diff_panel_original_x is not None:
                x = self._diff_panel_original_x
                self._diff_panel_original_x = None
        from Foundation import NSMakeRect
        self._panel.setFrame_display_(
            NSMakeRect(x, frame.origin.y, width, frame.size.height),
            True,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _eval_js(self, js_code: str) -> None:
        """Evaluate JavaScript in the WKWebView.

        If the page hasn't finished loading yet, the call is queued and
        will be replayed in order once ``webView:didFinishNavigation:``
        fires.  This prevents JS calls from being silently dropped when
        fast STT backends (e.g. FunASR) complete before WKWebView is ready.
        """
        if self._webview is None:
            return
        if not self._page_loaded:
            self._pending_js.append(js_code)
            return
        self._webview.evaluateJavaScript_completionHandler_(js_code, None)

    def _on_page_loaded(self) -> None:
        """Called by the WKNavigationDelegate when the page finishes loading."""
        pending = self._pending_js[:]
        self._pending_js.clear()
        self._page_loaded = True
        if pending and self._webview is not None:
            # Execute all queued JS as a single atomic evaluation to guarantee
            # execution order.  Sending them one-by-one via evaluateJavaScript
            # is asynchronous and WKWebView may interleave other callbacks
            # between evaluations, causing DOM state inconsistencies (e.g.
            # "streaming result" text leaking into the final-text textarea).
            combined = ";".join(pending)
            self._webview.evaluateJavaScript_completionHandler_(combined, None)

    @staticmethod
    def _screen_for_mouse() -> object:
        """Return the NSScreen containing the mouse pointer, or mainScreen."""
        from AppKit import NSEvent, NSScreen
        from Foundation import NSPointInRect

        mouse_loc = NSEvent.mouseLocation()
        for s in NSScreen.screens():
            if NSPointInRect(mouse_loc, s.frame()):
                return s
        return NSScreen.mainScreen()

    def _build_panel(self) -> None:
        """Build NSPanel + WKWebView, reusing pre-created objects from warmup()."""
        from AppKit import NSApp
        from Foundation import NSURL, NSMakeRect

        # Enable ⌘C/⌘V/⌘A via Edit menu in the responder chain
        _ensure_edit_menu()

        # Calculate panel dimensions based on content
        has_modes = len(self._available_modes) > 0
        show_enhance_section = self._show_enhance or has_modes
        height = self._PANEL_HEIGHT
        if not show_enhance_section:
            height -= 60  # Less height without enhance section
        if not has_modes:
            height -= 20  # Less height without mode segment
        panel_width = self._PANEL_WIDTH
        if self._diff_panel_open:
            panel_width += self._DIFF_PANEL_WIDTH

        NSApp.setActivationPolicy_(0)  # Regular (foreground)

        if self._panel is not None and self._webview is not None:
            # Reuse pre-created panel and webview from warmup()
            panel = self._panel
            webview = self._webview
            panel.setContentSize_((panel_width, height))
            webview.setFrame_(NSMakeRect(0, 0, panel_width, height))
            if webview.superview() is None:
                panel.contentView().addSubview_(webview)
        else:
            # Cold path: create from scratch (warmup was not called)
            from AppKit import (
                NSBackingStoreBuffered,
                NSClosableWindowMask,
                NSPanel,
                NSStatusWindowLevel,
                NSTitledWindowMask,
            )
            from WebKit import WKUserContentController, WKWebView

            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, panel_width, height),
                NSTitledWindowMask | NSClosableWindowMask,
                NSBackingStoreBuffered,
                False,
            )
            panel.setLevel_(NSStatusWindowLevel)
            panel.setFloatingPanel_(True)
            panel.setHidesOnDeactivate_(False)

            # Close delegate
            delegate_cls = _get_panel_close_delegate_class()
            delegate = delegate_cls.alloc().init()
            delegate._panel_ref = self
            panel.setDelegate_(delegate)
            self._close_delegate = delegate

            # WKWebView with message handler
            from wenzi.ui.web_utils import lightweight_webview_config

            config = lightweight_webview_config(shared=False)
            content_controller = WKUserContentController.alloc().init()
            handler_cls = _get_message_handler_class()
            handler = handler_cls.alloc().init()
            handler._panel_ref = self
            content_controller.addScriptMessageHandler_name_(handler, "action")
            config.setUserContentController_(content_controller)

            webview = WKWebView.alloc().initWithFrame_configuration_(
                NSMakeRect(0, 0, panel_width, height),
                config,
            )
            webview.setAutoresizingMask_(0x12)
            webview.setValue_forKey_(False, "drawsBackground")
            panel.contentView().addSubview_(webview)

            # Navigation delegate
            nav_delegate_cls = _get_navigation_delegate_class()
            nav_delegate = nav_delegate_cls.alloc().init()
            nav_delegate._panel_ref = self
            webview.setNavigationDelegate_(nav_delegate)

            self._panel = panel
            self._webview = webview
            self._message_handler = handler
            self._navigation_delegate = nav_delegate

        from wenzi.i18n import get_translations_for_prefix, t

        panel_title = (
            t("preview.title.enhance_clipboard")
            if self._source == "clipboard"
            else t("preview.title.preview")
        )
        panel.setTitle_(panel_title)

        # Center on the screen where the mouse pointer is
        screen = self._screen_for_mouse()
        if screen:
            sf = screen.visibleFrame()
            pf = panel.frame()
            x = sf.origin.x + (sf.size.width - pf.size.width) / 2
            y = sf.origin.y + (sf.size.height - pf.size.height) / 2
            panel.setFrameOrigin_((x, y))
        else:
            panel.center()

        self._page_loaded = False
        self._pending_js = []

        # Build config JSON and load HTML
        asr_loading = self._asr_text == "" and self._source != "clipboard"
        config_data = {
            "asrTitle": (
                t("preview.asr_title.clipboard")
                if self._source == "clipboard"
                else t("preview.asr_title.asr")
            ),
            "asrText": self._asr_text,
            "asrInfo": self._asr_info,
            "asrLoading": asr_loading,
            "showEnhance": self._show_enhance,
            "enhanceInfo": self._enhance_info,
            "modes": self._available_modes,
            "currentMode": self._current_mode,
            "sttModels": self._stt_models,
            "sttCurrentIndex": self._stt_current_index,
            "llmModels": self._llm_models,
            "llmCurrentIndex": self._llm_current_index,
            "source": self._source,
            "puncEnabled": self._punc_enabled,
            "thinkingEnabled": self._thinking_enabled,
            "hasAudio": self._asr_wav_data is not None,
            "diffPanelOpen": self._diff_panel_open,
            "previewHistory": self._preview_history_items,
            "i18n": get_translations_for_prefix("preview."),
        }
        html = load_template(
            "result_window_web.html",
            CONFIG=json.dumps(config_data, ensure_ascii=False),
        )
        webview.loadHTMLString_baseURL_(
            html, NSURL.fileURLWithPath_("/")
        )

    def _enhance_label_text(self, suffix: str = "") -> str:
        """Build the enhance label string with optional provider/model info."""
        if self._llm_models:
            return suffix or ""
        base = "AI"
        if self._enhance_info:
            base = f"AI ({self._enhance_info})"
        if suffix:
            return f"{base}  {suffix}"
        return base

    @staticmethod
    def _format_token_suffix(usage: dict | None) -> str:
        """Format token usage into a display string."""
        if not usage or not usage.get("total_tokens"):
            return ""
        total = usage["total_tokens"]
        prompt = usage.get("prompt_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        cached = usage.get("cache_read_tokens", 0)
        if cached:
            uncached = prompt - cached
            prompt_part = f"\u2191{cached:,}+{uncached:,}"
        else:
            prompt_part = f"\u2191{prompt:,}"
        return f"Tokens: {total:,} ({prompt_part} \u2193{completion:,})"

    def _stop_loading_timer(self) -> None:
        if self._loading_timer is not None:
            self._loading_timer.invalidate()
            self._loading_timer = None

    def _stop_playback(self) -> None:
        had_playback = self._playback_timer is not None or self._asr_sound is not None
        if self._playback_timer is not None:
            self._playback_timer.invalidate()
            self._playback_timer = None
        if self._asr_sound is not None:
            try:
                self._asr_sound.stop()
            except Exception:
                pass
            self._asr_sound = None
        if had_playback:
            self._eval_js(
                "document.getElementById('play-btn').textContent = i18n('btn.play')"
            )

    def _play_wav(self, wav_data: bytes) -> None:
        from AppKit import NSSound
        from Foundation import NSData, NSTimer

        self._stop_playback()
        ns_data = NSData.dataWithBytes_length_(wav_data, len(wav_data))
        sound = NSSound.alloc().initWithData_(ns_data)
        if sound:
            sound.play()
            self._asr_sound = sound
            self._eval_js(
                "document.getElementById('play-btn').textContent = i18n('btn.pause')"
            )
            self._playback_timer = (
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    0.3, self, b"tickPlaybackTimer:", None, True,
                )
            )

    def tickPlaybackTimer_(self, timer) -> None:
        """NSTimer callback: check if audio playback has finished."""
        if self._asr_sound is None or not self._asr_sound.isPlaying():
            self._stop_playback()

    def _save_wav(self, wav_data: bytes) -> None:
        from AppKit import NSSavePanel

        panel = NSSavePanel.savePanel()
        panel.setTitle_("Save Audio")
        panel.setNameFieldStringValue_("recording.wav")
        panel.setAllowedFileTypes_(["wav"])
        result = panel.runModal()
        if result == 1:
            url = panel.URL()
            if url:
                try:
                    with open(url.path(), "wb") as f:
                        f.write(wav_data)
                    logger.info("Audio saved to: %s", url.path())
                except Exception as e:
                    logger.error("Failed to save audio: %s", e)

    def _show_info_panel(self, title: str, content: str) -> None:
        """Display text content in a read-only scrollable panel."""
        from AppKit import (
            NSBackingStoreBuffered,
            NSClosableWindowMask,
            NSFont,
            NSPanel,
            NSResizableWindowMask,
            NSScrollView,
            NSStatusWindowLevel,
            NSTextView,
            NSTitledWindowMask,
        )
        from Foundation import NSMakeRect

        # Close previous info panel to avoid orphan windows
        if self._info_panel is not None:
            try:
                self._info_panel.close()
            except Exception:
                pass

        width, height = 520, 400
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, height),
            NSTitledWindowMask | NSClosableWindowMask | NSResizableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_(title)
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.center()

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutoresizingMask_(0x12)

        tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        tv.setEditable_(False)
        tv.setFont_(NSFont.userFixedPitchFontOfSize_(12.0))
        tv.setString_(content)
        tv.setVerticallyResizable_(True)
        tv.setHorizontallyResizable_(False)
        tv.textContainer().setWidthTracksTextView_(True)
        tv.setAutoresizingMask_(0x10)

        scroll.setDocumentView_(tv)
        panel.contentView().addSubview_(scroll)
        panel.makeKeyAndOrderFront_(None)
        self._info_panel = panel

    def _open_webview_panel(self, title: str, html_content: str, old_panel=None):
        """Open (or replace) a floating WKWebView panel.

        Returns the new panel so the caller can store it.
        """
        from AppKit import (
            NSBackingStoreBuffered,
            NSClosableWindowMask,
            NSPanel,
            NSResizableWindowMask,
            NSStatusWindowLevel,
            NSTitledWindowMask,
        )
        from Foundation import NSURL, NSMakeRect
        from WebKit import WKWebView

        if old_panel is not None:
            try:
                # Release WKWebView canvas/IOSurface memory before closing.
                for subview in (old_panel.contentView().subviews() or []):
                    if hasattr(subview, 'stopLoading_'):
                        cleanup_webview(subview, handler_name=None)
                old_panel.close()
            except Exception:
                pass

        width, height = 700, 420
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, height),
            NSTitledWindowMask | NSClosableWindowMask | NSResizableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_(title)
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.center()

        from wenzi.ui.web_utils import lightweight_webview_config

        config = lightweight_webview_config(shared=False)
        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, width, height), config,
        )
        webview.setAutoresizingMask_(0x12)
        webview.setValue_forKey_(False, "drawsBackground")
        webview.loadHTMLString_baseURL_(html_content, NSURL.fileURLWithPath_("/"))

        panel.contentView().addSubview_(webview)
        panel.makeKeyAndOrderFront_(None)
        return panel

    def _show_hotwords_panel(self, details: list[HotwordDetail]) -> None:
        """Display hotword details in a WKWebView-based table panel."""
        self._hotwords_webview_panel = self._open_webview_panel(
            f"Hotwords ({len(details)})",
            _build_hotwords_html(details, self._input_context_text),
            self._hotwords_webview_panel,
        )

    def _show_context_panel(self) -> None:
        """Display input context and LLM vocabulary in a WKWebView panel."""
        from wenzi.i18n import t

        self._context_webview_panel = self._open_webview_panel(
            t("preview.context_panel.title"),
            _build_context_panel_html(
                self._input_context_text, self._llm_vocab_detail,
            ),
            self._context_webview_panel,
        )
