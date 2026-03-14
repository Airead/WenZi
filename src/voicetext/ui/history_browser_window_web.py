"""Web-based history browser panel using WKWebView.

Drop-in replacement for the AppKit-based HistoryBrowserPanel, with the
same public API surface.  See dev/wkwebview-pitfalls.md for background.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Filter labels (shared with native version)
_MODE_ALL = "All"
_MODEL_ALL = "All Models"

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {
    --bg: #ffffff; --text: #1d1d1f; --card-bg: #f5f5f7;
    --border: #d2d2d7; --secondary: #86868b; --accent: #007aff;
    --text-bg: #ffffff; --row-hover: #e8f0fe;
    --corrected-bg: rgba(0, 122, 255, 0.08);
    --btn-bg: #e5e5ea; --btn-hover: #d1d1d6;
    --btn-primary-bg: #007aff; --btn-primary-text: #ffffff;
    --focus-ring: rgba(0, 122, 255, 0.4);
    --alt-row: #fafafa;
}
@media (prefers-color-scheme: dark) {
    :root {
        --bg: #1d1d1f; --text: #f5f5f7; --card-bg: #2c2c2e;
        --border: #48484a; --secondary: #98989d; --accent: #0a84ff;
        --text-bg: #1c1c1e; --row-hover: #2c3a50;
        --corrected-bg: rgba(10, 132, 255, 0.15);
        --btn-bg: #3a3a3c; --btn-hover: #48484a;
        --btn-primary-bg: #0a84ff; --btn-primary-text: #ffffff;
        --focus-ring: rgba(10, 132, 255, 0.4);
        --alt-row: #242426;
    }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
    background: var(--bg); color: var(--text);
    padding: 12px; overflow: hidden;
    font-size: 13px;
    display: flex; flex-direction: column;
}

/* Toolbar */
.toolbar {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 8px; flex-shrink: 0;
}
.search-input {
    flex: 1; height: 28px; padding: 0 8px;
    border: 1px solid var(--border); border-radius: 6px;
    background: var(--text-bg); color: var(--text);
    font-size: 12px; outline: none;
}
.search-input:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--focus-ring); }
.search-input::placeholder { color: var(--secondary); }
.filter-select {
    height: 28px; padding: 0 6px;
    border: 1px solid var(--border); border-radius: 6px;
    background: var(--text-bg); color: var(--text);
    font-size: 12px; outline: none; cursor: pointer;
    -webkit-appearance: none; appearance: none;
    padding-right: 20px;
}
.filter-select:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--focus-ring); }
.cb-label {
    display: flex; align-items: center; gap: 4px;
    font-size: 11px; color: var(--secondary); white-space: nowrap; cursor: pointer;
    -webkit-user-select: none; user-select: none;
}
.cb-label input { margin: 0; cursor: pointer; }

/* Table */
.table-wrap {
    flex: 1; min-height: 0;
    border: 1px solid var(--border); border-radius: 6px;
    overflow: hidden; display: flex; flex-direction: column;
}
.table-header {
    display: flex; background: var(--card-bg);
    border-bottom: 1px solid var(--border);
    font-size: 11px; font-weight: 600; color: var(--secondary);
    flex-shrink: 0;
    -webkit-user-select: none; user-select: none;
}
.table-header .col { padding: 6px 10px; }
.table-body {
    flex: 1; overflow-y: auto; overflow-x: hidden;
}
.row {
    display: flex; cursor: pointer;
    border-bottom: 1px solid var(--border);
    transition: background 0.1s;
}
.row:last-child { border-bottom: none; }
.row:nth-child(even) { background: var(--alt-row); }
.row:hover { background: var(--row-hover); }
.row.selected { background: var(--accent); color: #fff; }
.row.selected .col { color: #fff; }
.row.corrected { background: var(--corrected-bg); }
.row.corrected:hover { background: var(--row-hover); }
.row.corrected.selected { background: var(--accent); }
.col { padding: 5px 10px; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.col-time { width: 130px; flex-shrink: 0; color: var(--secondary); }
.col-mode { width: 90px; flex-shrink: 0; }
.col-content { flex: 1; min-width: 0; }
.row.selected .col-time { color: rgba(255,255,255,0.8); }
.empty-msg {
    padding: 24px; text-align: center; color: var(--secondary); font-size: 12px;
}

/* Detail */
.detail { flex-shrink: 0; margin-top: 8px; }
.detail-row { margin-bottom: 6px; }
.detail-label {
    font-size: 11px; font-weight: 600; margin-bottom: 2px;
    color: var(--secondary);
}
.detail-text {
    width: 100%; min-height: 44px; max-height: 72px;
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 6px; padding: 6px 10px;
    font-family: "SF Mono", Menlo, monospace; font-size: 12px;
    color: var(--text); line-height: 1.4;
    overflow-y: auto; white-space: pre-wrap; word-wrap: break-word;
    -webkit-user-select: text; user-select: text;
}
.final-input {
    width: 100%; height: 32px; padding: 0 10px;
    border: 2px solid var(--accent); border-radius: 6px;
    background: var(--text-bg); color: var(--text);
    font-family: "SF Mono", Menlo, monospace; font-size: 12px;
    outline: none;
    -webkit-user-select: text; user-select: text;
}
.final-input:focus { box-shadow: 0 0 0 3px var(--focus-ring); }
.final-input:disabled { opacity: 0.5; border-color: var(--border); }
.detail-info {
    display: flex; align-items: center; gap: 16px;
    font-size: 11px; color: var(--secondary); margin-top: 2px;
}

/* Buttons */
.btn-row {
    display: flex; justify-content: flex-end; gap: 8px;
    margin-top: 8px; flex-shrink: 0;
}
.btn {
    height: 28px; padding: 0 16px; border: none; border-radius: 6px;
    font-size: 12px; font-weight: 500; cursor: pointer;
    background: var(--btn-bg); color: var(--text);
    transition: background 0.15s;
}
.btn:hover { background: var(--btn-hover); }
.btn-primary { background: var(--btn-primary-bg); color: var(--btn-primary-text); }
.btn-primary:hover { opacity: 0.9; }
.btn:disabled { opacity: 0.4; cursor: default; }
</style>
</head>
<body>

<div class="toolbar">
    <input type="text" class="search-input" id="search" placeholder="Search history...">
    <select class="filter-select" id="mode-filter"><option>All</option></select>
    <select class="filter-select" id="model-filter"><option>All Models</option></select>
    <label class="cb-label"><input type="checkbox" id="corrected-cb">Corrected</label>
</div>

<div class="table-wrap">
    <div class="table-header">
        <div class="col col-time">Time</div>
        <div class="col col-mode">Mode</div>
        <div class="col col-content">Content</div>
    </div>
    <div class="table-body" id="table-body"></div>
</div>

<div class="detail" id="detail" style="display:none">
    <div class="detail-row">
        <div class="detail-label" id="asr-label">ASR:</div>
        <div class="detail-text" id="asr-text"></div>
    </div>
    <div class="detail-row">
        <div class="detail-label" id="enhanced-label">Enhanced:</div>
        <div class="detail-text" id="enhanced-text"></div>
    </div>
    <div class="detail-row">
        <div class="detail-label">Final:</div>
        <input type="text" class="final-input" id="final-input" disabled>
    </div>
    <div class="detail-info">
        <span id="mode-info"></span>
        <span id="time-info"></span>
    </div>
</div>

<div class="btn-row">
    <button class="btn btn-primary" id="save-btn" disabled>Save</button>
    <button class="btn" id="close-btn">Close</button>
</div>

<script>
const body = document.getElementById('table-body');
const detail = document.getElementById('detail');
const searchEl = document.getElementById('search');
const modeFilter = document.getElementById('mode-filter');
const modelFilter = document.getElementById('model-filter');
const correctedCb = document.getElementById('corrected-cb');
const asrLabel = document.getElementById('asr-label');
const asrText = document.getElementById('asr-text');
const enhancedLabel = document.getElementById('enhanced-label');
const enhancedText = document.getElementById('enhanced-text');
const finalInput = document.getElementById('final-input');
const modeInfo = document.getElementById('mode-info');
const timeInfo = document.getElementById('time-info');
const saveBtn = document.getElementById('save-btn');
const closeBtn = document.getElementById('close-btn');

let selectedIndex = -1;
let currentRecords = [];
let originalFinalText = '';

function post(msg) {
    window.webkit.messageHandlers.action.postMessage(msg);
}

/* --- Toolbar events --- */
let searchTimer = null;
searchEl.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => post({type:'search', text: searchEl.value}), 250);
});
modeFilter.addEventListener('change', () => post({type:'filterMode', mode: modeFilter.value}));
modelFilter.addEventListener('change', () => post({type:'filterModel', model: modelFilter.value}));
correctedCb.addEventListener('change', () => post({type:'filterCorrected', enabled: correctedCb.checked}));

/* --- Table row click --- */
body.addEventListener('click', (e) => {
    const row = e.target.closest('.row');
    if (!row) return;
    const idx = parseInt(row.dataset.idx, 10);
    selectRow(idx);
    post({type:'selectRow', index: idx});
});

function selectRow(idx) {
    document.querySelectorAll('.row.selected').forEach(r => r.classList.remove('selected'));
    selectedIndex = idx;
    const row = body.querySelector(`.row[data-idx="${idx}"]`);
    if (row) row.classList.add('selected');
}

/* --- Final text edit detection --- */
finalInput.addEventListener('input', () => {
    const changed = finalInput.value !== originalFinalText;
    saveBtn.disabled = !changed;
});

/* --- Buttons --- */
saveBtn.addEventListener('click', () => {
    if (selectedIndex < 0 || selectedIndex >= currentRecords.length) return;
    const rec = currentRecords[selectedIndex];
    post({type:'save', timestamp: rec.timestamp || '', text: finalInput.value});
});
closeBtn.addEventListener('click', () => post({type:'close'}));

/* --- Keyboard shortcuts --- */
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); post({type:'close'}); }
    if (e.metaKey && e.key === 's') { e.preventDefault(); if (!saveBtn.disabled) saveBtn.click(); }
});

/* --- Python → JS API --- */

function setRecords(records) {
    currentRecords = records;
    selectedIndex = -1;
    body.innerHTML = '';
    detail.style.display = 'none';
    finalInput.disabled = true;
    finalInput.value = '';
    saveBtn.disabled = true;
    if (records.length === 0) {
        body.innerHTML = '<div class="empty-msg">No records found.</div>';
        return;
    }
    let html = '';
    for (let i = 0; i < records.length; i++) {
        const r = records[i];
        const ts = formatTimestamp(r.timestamp || '');
        const mode = r.enhance_mode || 'off';
        let preview = (r.final_text || r.asr_text || '').replace(/\n/g, ' ');
        if (preview.length > 80) preview = preview.substring(0, 80) + '...';
        const corrected = r._corrected ? ' corrected' : '';
        html += `<div class="row${corrected}" data-idx="${i}">` +
            `<div class="col col-time">${esc(ts)}</div>` +
            `<div class="col col-mode">${esc(mode)}</div>` +
            `<div class="col col-content">${esc(preview)}</div></div>`;
    }
    body.innerHTML = html;
}

function showDetail(record) {
    detail.style.display = 'block';
    const stt = record.stt_model || '';
    asrLabel.textContent = stt ? `ASR (${stt}):` : 'ASR:';
    asrText.textContent = record.asr_text || '';
    const llm = record.llm_model || '';
    enhancedLabel.textContent = llm ? `Enhanced (${llm}):` : 'Enhanced:';
    enhancedText.textContent = record.enhanced_text || '';
    finalInput.value = record.final_text || '';
    finalInput.disabled = false;
    originalFinalText = record.final_text || '';
    modeInfo.textContent = `Mode: ${record.enhance_mode || 'off'}`;
    let ts = formatTimestamp(record.timestamp || '');
    let label = `Time: ${ts}`;
    if (record.edited_at) label += `  (edited: ${formatTimestamp(record.edited_at)})`;
    timeInfo.textContent = label;
    saveBtn.disabled = true;
}

function clearDetail() {
    detail.style.display = 'none';
    finalInput.value = '';
    finalInput.disabled = true;
    saveBtn.disabled = true;
    selectedIndex = -1;
    document.querySelectorAll('.row.selected').forEach(r => r.classList.remove('selected'));
}

function setFilterOptions(modes, models) {
    const prevMode = modeFilter.value;
    const prevModel = modelFilter.value;
    modeFilter.innerHTML = '<option>All</option>';
    modes.forEach(m => { const o = document.createElement('option'); o.textContent = m; modeFilter.appendChild(o); });
    if ([...modeFilter.options].some(o => o.value === prevMode)) modeFilter.value = prevMode;
    modelFilter.innerHTML = '<option>All Models</option>';
    models.forEach(m => { const o = document.createElement('option'); o.textContent = m; modelFilter.appendChild(o); });
    if ([...modelFilter.options].some(o => o.value === prevModel)) modelFilter.value = prevModel;
}

function setSaveEnabled(enabled) { saveBtn.disabled = !enabled; }

function markSaved(index) {
    if (index >= 0 && index < currentRecords.length) {
        originalFinalText = finalInput.value;
        currentRecords[index].final_text = finalInput.value;
        saveBtn.disabled = true;
        /* Re-render the row preview */
        const row = body.querySelector(`.row[data-idx="${index}"]`);
        if (row) {
            const previewCol = row.querySelector('.col-content');
            if (previewCol) {
                let preview = (finalInput.value || '').replace(/\n/g, ' ');
                if (preview.length > 80) preview = preview.substring(0, 80) + '...';
                previewCol.textContent = preview;
            }
        }
    }
}

/* --- Helpers --- */
function formatTimestamp(ts) {
    if (!ts || ts.length < 16) return ts;
    return ts.substring(0, 10) + ' ' + ts.substring(11, 16);
}
function esc(s) {
    const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}
</script>
</body>
</html>"""


def _format_timestamp(ts: str) -> str:
    """Format ISO timestamp as 'YYYY-MM-DD HH:MM'."""
    try:
        return ts[:16].replace("T", " ")
    except Exception:
        return ts


# ---------------------------------------------------------------------------
# NSObject subclasses (lazy-created, unique class names)
# ---------------------------------------------------------------------------

_HistoryBrowserWebCloseDelegate = None


def _get_panel_close_delegate_class():
    global _HistoryBrowserWebCloseDelegate
    if _HistoryBrowserWebCloseDelegate is None:
        from Foundation import NSObject

        class HistoryBrowserWebCloseDelegate(NSObject):
            _panel_ref = None

            def windowWillClose_(self, notification):
                if self._panel_ref is not None:
                    self._panel_ref.close()

        _HistoryBrowserWebCloseDelegate = HistoryBrowserWebCloseDelegate
    return _HistoryBrowserWebCloseDelegate


_HistoryBrowserWebNavigationDelegate = None


def _get_navigation_delegate_class():
    global _HistoryBrowserWebNavigationDelegate
    if _HistoryBrowserWebNavigationDelegate is None:
        from Foundation import NSObject

        class HistoryBrowserWebNavigationDelegate(NSObject):
            _panel_ref = None

            def webView_didFinishNavigation_(self, webview, navigation):
                if self._panel_ref is not None:
                    self._panel_ref._on_page_loaded()

        _HistoryBrowserWebNavigationDelegate = HistoryBrowserWebNavigationDelegate
    return _HistoryBrowserWebNavigationDelegate


_HistoryBrowserWebMessageHandler = None


def _get_message_handler_class():
    global _HistoryBrowserWebMessageHandler
    if _HistoryBrowserWebMessageHandler is None:
        import json as _json

        import objc
        from Foundation import NSObject

        import WebKit  # noqa: F401

        WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")

        class HistoryBrowserWebMessageHandler(NSObject, protocols=[WKScriptMessageHandler]):
            _panel_ref = None

            def userContentController_didReceiveScriptMessage_(self, controller, message):
                if self._panel_ref is None:
                    return
                raw = message.body()
                try:
                    from Foundation import NSJSONSerialization

                    json_data, _ = NSJSONSerialization.dataWithJSONObject_options_error_(raw, 0, None)
                    body = _json.loads(bytes(json_data))
                except Exception:
                    logger.warning("Cannot convert message body: %r", raw)
                    return
                self._panel_ref._handle_js_message(body)

        _HistoryBrowserWebMessageHandler = HistoryBrowserWebMessageHandler
    return _HistoryBrowserWebMessageHandler


# ---------------------------------------------------------------------------
# Panel class
# ---------------------------------------------------------------------------


class HistoryBrowserPanel:
    """WKWebView-based floating panel for browsing conversation history.

    Drop-in replacement for the AppKit-based HistoryBrowserPanel.
    """

    _PANEL_WIDTH = 780
    _PANEL_HEIGHT = 600

    def __init__(self) -> None:
        self._panel = None
        self._webview = None
        self._close_delegate = None
        self._message_handler = None
        self._navigation_delegate = None
        self._page_loaded: bool = False
        self._pending_js: list[str] = []

        self._all_records: List[Dict[str, Any]] = []
        self._filtered_records: List[Dict[str, Any]] = []
        self._selected_index: int = -1
        self._conversation_history = None
        self._on_save: Optional[Callable[[str, str], None]] = None
        self._search_text: str = ""
        self._filter_mode: str = _MODE_ALL
        self._filter_model: str = _MODEL_ALL
        self._filter_corrected_only: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(
        self,
        conversation_history,
        on_save: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """Show the history browser panel."""
        from AppKit import NSApp

        self._conversation_history = conversation_history
        self._on_save = on_save

        NSApp.setActivationPolicy_(0)  # Regular
        self._build_panel()
        self._reload_data()
        self._panel.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def close(self) -> None:
        """Close the panel and clean up."""
        if self._panel is not None:
            self._panel.setDelegate_(None)
            self._close_delegate = None
            self._panel.orderOut_(None)
            self._panel = None
        if self._webview is not None:
            self._webview.setNavigationDelegate_(None)
        self._webview = None
        self._message_handler = None
        self._navigation_delegate = None
        self._page_loaded = False
        self._pending_js = []

        from AppKit import NSApp

        NSApp.setActivationPolicy_(1)  # Accessory

    # ------------------------------------------------------------------
    # Data loading and filtering
    # ------------------------------------------------------------------

    def _reload_data(self) -> None:
        """Reload all records and push to JS."""
        if self._conversation_history is None:
            return
        if self._search_text:
            self._all_records = self._conversation_history.search(self._search_text, limit=500)
        else:
            self._all_records = self._conversation_history.get_all(limit=500)

        self._apply_filters()
        self._selected_index = -1
        self._push_filter_options()
        self._push_records()

    def _apply_filters(self) -> None:
        """Filter _all_records by mode, model, and corrected flag."""
        from voicetext.enhance.conversation_history import ConversationHistory

        records = self._all_records
        if self._filter_mode != _MODE_ALL:
            records = [r for r in records if r.get("enhance_mode", "") == self._filter_mode]
        if self._filter_model != _MODEL_ALL:
            records = [
                r
                for r in records
                if r.get("stt_model", "") == self._filter_model or r.get("llm_model", "") == self._filter_model
            ]
        if self._filter_corrected_only:
            records = [r for r in records if ConversationHistory._is_corrected(r)]
        self._filtered_records = records

    def _push_records(self) -> None:
        """Send current filtered records to JS."""
        from voicetext.enhance.conversation_history import ConversationHistory

        records_json = []
        for r in self._filtered_records:
            entry = dict(r)
            entry["_corrected"] = ConversationHistory._is_corrected(r)
            records_json.append(entry)
        self._eval_js(f"setRecords({json.dumps(records_json, ensure_ascii=False)})")

    def _push_filter_options(self) -> None:
        """Send available filter options to JS."""
        modes: Set[str] = set()
        models: Set[str] = set()
        for r in self._all_records:
            m = r.get("enhance_mode", "")
            if m:
                modes.add(m)
            for key in ("stt_model", "llm_model"):
                v = r.get(key, "")
                if v:
                    models.add(v)
        self._eval_js(f"setFilterOptions({json.dumps(sorted(modes))},{json.dumps(sorted(models))})")

    # ------------------------------------------------------------------
    # JS message handler
    # ------------------------------------------------------------------

    def _handle_js_message(self, body: dict) -> None:
        """Dispatch messages from JavaScript."""
        msg_type = body.get("type", "")

        if msg_type == "search":
            self._search_text = body.get("text", "")
            self._reload_data()

        elif msg_type == "filterMode":
            self._filter_mode = body.get("mode", _MODE_ALL)
            self._apply_filters()
            self._selected_index = -1
            self._push_records()
            self._eval_js("clearDetail()")

        elif msg_type == "filterModel":
            self._filter_model = body.get("model", _MODEL_ALL)
            self._apply_filters()
            self._selected_index = -1
            self._push_records()
            self._eval_js("clearDetail()")

        elif msg_type == "filterCorrected":
            self._filter_corrected_only = body.get("enabled", False)
            self._apply_filters()
            self._selected_index = -1
            self._push_records()
            self._eval_js("clearDetail()")

        elif msg_type == "selectRow":
            index = body.get("index", -1)
            if 0 <= index < len(self._filtered_records):
                self._selected_index = index
                record = self._filtered_records[index]
                self._eval_js(f"showDetail({json.dumps(record, ensure_ascii=False)})")
            else:
                self._selected_index = -1
                self._eval_js("clearDetail()")

        elif msg_type == "save":
            self._on_save_clicked(body.get("timestamp", ""), body.get("text", ""))

        elif msg_type == "close":
            self.close()

    def _on_save_clicked(self, timestamp: str, new_text: str) -> None:
        """Save edited final_text back to conversation history."""
        if not timestamp or self._conversation_history is None:
            return
        if self._selected_index < 0 or self._selected_index >= len(self._filtered_records):
            return

        ok = self._conversation_history.update_final_text(timestamp, new_text)
        if ok:
            self._filtered_records[self._selected_index]["final_text"] = new_text
            self._eval_js(f"markSaved({self._selected_index})")
            if self._on_save:
                self._on_save(timestamp, new_text)

    # ------------------------------------------------------------------
    # WKWebView JS bridge
    # ------------------------------------------------------------------

    def _eval_js(self, js_code: str) -> None:
        """Evaluate JS in WKWebView, with queue for pre-load calls."""
        if self._webview is None:
            return
        if not self._page_loaded:
            self._pending_js.append(js_code)
            return
        self._webview.evaluateJavaScript_completionHandler_(js_code, None)

    def _on_page_loaded(self) -> None:
        """Flush pending JS calls atomically when page finishes loading."""
        pending = self._pending_js[:]
        self._pending_js.clear()
        self._page_loaded = True
        if pending and self._webview is not None:
            combined = ";".join(pending)
            self._webview.evaluateJavaScript_completionHandler_(combined, None)

    # ------------------------------------------------------------------
    # Panel construction
    # ------------------------------------------------------------------

    def _build_panel(self) -> None:
        """Build NSPanel + WKWebView."""
        from AppKit import (
            NSApp,
            NSBackingStoreBuffered,
            NSClosableWindowMask,
            NSPanel,
            NSResizableWindowMask,
            NSScreen,
            NSStatusWindowLevel,
            NSTitledWindowMask,
        )
        from Foundation import NSMakeRect, NSMakeSize, NSURL
        from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

        from voicetext.ui.result_window import _ensure_edit_menu

        _ensure_edit_menu()

        NSApp.setActivationPolicy_(0)

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
            NSTitledWindowMask | NSClosableWindowMask | NSResizableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setMinSize_(NSMakeSize(600, 450))
        panel.setTitle_("Conversation History")
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)

        screen = NSScreen.mainScreen()
        if screen:
            sf = screen.visibleFrame()
            pf = panel.frame()
            x = sf.origin.x + (sf.size.width - pf.size.width) / 2
            y = sf.origin.y + (sf.size.height - pf.size.height) / 2
            panel.setFrameOrigin_((x, y))
        else:
            panel.center()

        delegate_cls = _get_panel_close_delegate_class()
        delegate = delegate_cls.alloc().init()
        delegate._panel_ref = self
        panel.setDelegate_(delegate)
        self._close_delegate = delegate

        config = WKWebViewConfiguration.alloc().init()
        content_controller = WKUserContentController.alloc().init()

        handler_cls = _get_message_handler_class()
        handler = handler_cls.alloc().init()
        handler._panel_ref = self
        content_controller.addScriptMessageHandler_name_(handler, "action")
        config.setUserContentController_(content_controller)

        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
            config,
        )
        webview.setAutoresizingMask_(0x12)  # Width + Height sizable
        webview.setValue_forKey_(False, "drawsBackground")
        panel.contentView().addSubview_(webview)

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

        html = _HTML_TEMPLATE
        webview.loadHTMLString_baseURL_(html, NSURL.URLWithString_("file:///"))
