"""Web-based floating preview panel for ASR and AI enhancement results.

Uses WKWebView + WKScriptMessageHandler for a modern HTML/CSS/JS interface
with the same public API as the original AppKit-based ResultPreviewPanel.
"""

from __future__ import annotations

import json
import logging
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

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
    --green: #34c759; --orange: #ff9500; --red: #ff3b30;
    --text-bg: #ffffff; --enhance-bg: #edf1f8;
    --btn-bg: #e5e5ea; --btn-hover: #d1d1d6;
    --segment-bg: #e5e5ea; --segment-active: #ffffff;
    --segment-hover: rgba(0, 0, 0, 0.06);
    --shadow: rgba(0, 0, 0, 0.12);
    --focus-ring: rgba(0, 122, 255, 0.4);
}
@media (prefers-color-scheme: dark) {
    :root {
        --bg: #1d1d1f; --text: #c8c8cc; --card-bg: #2c2c2e;
        --border: #48484a; --secondary: #98989d; --accent: #0a84ff;
        --green: #30d158; --orange: #ff9f0a; --red: #ff453a;
        --text-bg: #1c1c1e; --enhance-bg: #1e2230;
        --btn-bg: #3a3a3c; --btn-hover: #48484a;
        --segment-bg: #3a3a3c; --segment-active: #636366;
        --segment-hover: rgba(255, 255, 255, 0.1);
        --shadow: rgba(0, 0, 0, 0.4);
        --focus-ring: rgba(10, 132, 255, 0.4);
    }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Mono", Menlo, monospace;
    background: var(--bg); color: var(--text);
    padding: 12px; overflow: hidden;
    -webkit-user-select: none; user-select: none;
    font-size: 13px;
    display: flex; flex-direction: column;
}
.section { margin-bottom: 8px; flex-shrink: 0; }
.section.expand {
    flex: 1; min-height: 0;
    display: flex; flex-direction: column;
}
.section-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 4px; min-height: 22px; gap: 6px;
    flex-shrink: 0;
}
.section-header .left { display: flex; align-items: center; gap: 6px; flex: 1; min-width: 0; }
.section-header .right { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
.section-title {
    font-weight: 600; font-size: 13px; white-space: nowrap;
}
.section-info {
    font-size: 11px; color: var(--secondary); white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
}
.text-area {
    width: 100%; min-height: 36px;
    background: var(--text-bg); border: 1px solid var(--border);
    border-radius: 6px; padding: 6px 10px;
    font-family: "SF Mono", Menlo, monospace; font-size: 12px;
    color: var(--text); line-height: 1.4;
    overflow-y: auto; white-space: pre-wrap; word-wrap: break-word;
    -webkit-user-select: text; user-select: text;
}
.section.expand .text-area { flex: 1; min-height: 0; }
.text-area.asr-bg { background: var(--card-bg); }
.text-area.enhance-bg { background: var(--enhance-bg); }
.text-area .thinking {
    color: var(--secondary); font-style: italic;
}
.final-area {
    width: 100%; min-height: 36px;
    background: var(--text-bg); border: 1px solid var(--border);
    border-radius: 6px; padding: 6px 10px;
    font-family: "SF Mono", Menlo, monospace; font-size: 12px;
    color: var(--text); line-height: 1.4;
    resize: none; outline: none;
    -webkit-user-select: text; user-select: text;
}
.section.expand .final-area { flex: 1; min-height: 0; }
.final-area:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--focus-ring); }

/* Buttons */
.btn {
    display: inline-flex; align-items: center; justify-content: center;
    padding: 4px 10px; border-radius: 5px;
    font-size: 11px; font-family: inherit; cursor: pointer;
    border: 1px solid var(--border); background: var(--btn-bg);
    color: var(--text); white-space: nowrap; min-height: 22px;
    transition: background 0.15s;
}
.btn:hover { background: var(--btn-hover); }
.btn:active { opacity: 0.8; }
.btn.primary {
    background: var(--accent); color: #fff; border-color: var(--accent);
    font-weight: 500;
}
.btn.primary:hover { opacity: 0.9; }
.btn.disabled { opacity: 0.4; pointer-events: none; }

/* Select dropdown */
select {
    padding: 3px 6px; border-radius: 5px;
    font-size: 11px; font-family: inherit;
    border: 1px solid var(--border); background: var(--btn-bg);
    color: var(--text); outline: none; cursor: pointer;
    max-width: 200px; min-height: 22px;
}

/* Checkbox */
.checkbox-wrap {
    display: inline-flex; align-items: center; gap: 3px;
    font-size: 11px; cursor: pointer; white-space: nowrap;
}
.checkbox-wrap input[type="checkbox"] {
    width: 14px; height: 14px; cursor: pointer;
}

/* Segmented control */
.segment-bar {
    display: flex; gap: 0; background: var(--segment-bg);
    border-radius: 7px; padding: 2px; margin-bottom: 8px;
    flex-shrink: 0;
}
.segment-btn {
    flex: 1; padding: 4px 4px; border: none; background: transparent;
    color: var(--text); font-size: 12px; font-family: inherit;
    cursor: pointer; border-radius: 5px; text-align: center;
    transition: background 0.15s, box-shadow 0.15s;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.segment-btn.active {
    background: var(--segment-active);
    box-shadow: 0 1px 3px var(--shadow);
    font-weight: 500;
}
.segment-btn:hover:not(.active) { background: var(--segment-hover); }

/* Button bar */
.button-bar {
    display: flex; justify-content: flex-end; gap: 8px;
    margin-top: 8px; padding-top: 8px;
    border-top: 1px solid var(--border);
    flex-shrink: 0;
}
.button-bar .left-group { margin-right: auto; }
.bar-btn {
    padding: 6px 18px; border-radius: 6px;
    font-size: 13px; font-family: inherit; cursor: pointer;
    border: 1px solid var(--border); background: var(--btn-bg);
    color: var(--text); font-weight: 400;
    transition: background 0.15s;
}
.bar-btn:hover { background: var(--btn-hover); }
.bar-btn.primary {
    background: var(--accent); color: #fff; border-color: var(--accent);
    font-weight: 500;
}
.bar-btn.primary:hover { opacity: 0.9; }

/* Hidden sections */
.hidden { display: none !important; }

/* Loading animation */
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
.loading { animation: pulse 1.5s ease-in-out infinite; }
</style>
</head>
<body>

<!-- ASR Section -->
<div class="section expand" id="asr-section">
    <div class="section-header">
        <div class="left">
            <span class="section-title" id="asr-title">ASR</span>
            <select id="stt-select" class="hidden"></select>
            <span class="section-info" id="asr-info"></span>
        </div>
        <div class="right">
            <label class="checkbox-wrap" id="punc-wrap">
                <input type="checkbox" id="punc-cb" checked>
                <span>Punc</span>
            </label>
            <button class="btn hidden" id="play-btn" onclick="postAction('playAudio')">Play ▶</button>
            <button class="btn hidden" id="save-btn" onclick="postAction('saveAudio')">Save</button>
        </div>
    </div>
    <div class="text-area asr-bg" id="asr-text"></div>
</div>

<!-- Mode Segment -->
<div class="segment-bar hidden" id="mode-segment"></div>

<!-- Enhance Section -->
<div class="section expand hidden" id="enhance-section">
    <div class="section-header">
        <div class="left">
            <span class="section-title">AI</span>
            <select id="llm-select" class="hidden"></select>
            <span class="section-info" id="enhance-info"></span>
        </div>
        <div class="right">
            <input type="checkbox" id="thinking-cb" style="width:14px;height:14px;cursor:pointer;">
            <button class="btn disabled" id="thinking-btn" onclick="postAction('showThinking')"
                style="opacity:0.3;">🧠</button>
            <button class="btn disabled" id="prompt-btn" onclick="postAction('showPrompt')">Prompt ⓘ</button>
        </div>
    </div>
    <div class="text-area enhance-bg" id="enhance-text"></div>
</div>

<!-- Final Result Section -->
<div class="section expand">
    <div class="section-header">
        <div class="left">
            <span class="section-title">Final Result (editable)</span>
        </div>
        <div class="right">
            <button class="btn" id="translate-btn" onclick="doTranslate()">Translate ↗</button>
        </div>
    </div>
    <textarea class="final-area" id="final-text"></textarea>
</div>

<!-- Button Bar -->
<div class="button-bar">
    <div class="left-group" id="history-dropdown-wrap" style="position:relative;">
        <button class="bar-btn" id="history-btn" onclick="toggleHistoryDropdown()" style="display:none;">History</button>
        <div id="history-dropdown" style="display:none; position:absolute; bottom:100%; left:0; margin-bottom:4px;
            min-width:280px; max-width:360px; max-height:240px; overflow-y:auto;
            background:var(--card-bg); border:1px solid var(--border); border-radius:8px;
            box-shadow:0 4px 16px var(--shadow); z-index:100;">
        </div>
    </div>
    <button class="bar-btn" id="cancel-btn" onclick="postAction('cancel')">Cancel</button>
    <button class="bar-btn primary" id="confirm-btn" onclick="doConfirm(false)">Confirm ⏎</button>
</div>

<script>
// --- State ---
const CONFIG = __CONFIG__;
let userEdited = false;
let cmdHeld = false;

// --- Init ---
function init() {
    // ASR title
    document.getElementById('asr-title').textContent = CONFIG.asrTitle;
    if (CONFIG.asrLoading) {
        document.getElementById('asr-text').innerHTML = '<span class="loading">⏳ Transcribing...</span>';
    } else {
        document.getElementById('asr-text').textContent = CONFIG.asrText;
    }
    document.getElementById('asr-info').textContent = CONFIG.asrInfo;

    // Final text
    document.getElementById('final-text').value = CONFIG.asrLoading ? '' : CONFIG.asrText;

    // Disable STT select during loading
    if (CONFIG.asrLoading) {
        const sel = document.getElementById('stt-select');
        if (sel) sel.disabled = true;
    }

    // STT popup
    if (CONFIG.sttModels.length > 0 && CONFIG.source !== 'clipboard') {
        const sel = document.getElementById('stt-select');
        sel.classList.remove('hidden');
        CONFIG.sttModels.forEach((name, i) => {
            const opt = document.createElement('option');
            opt.value = i; opt.textContent = name;
            if (i === CONFIG.sttCurrentIndex) opt.selected = true;
            sel.appendChild(opt);
        });
        sel.addEventListener('change', () => {
            postAction('sttModelChange', { index: parseInt(sel.value) });
        });
    }

    // Punc checkbox
    if (CONFIG.source === 'clipboard') {
        document.getElementById('punc-wrap').classList.add('hidden');
    } else {
        const cb = document.getElementById('punc-cb');
        cb.checked = CONFIG.puncEnabled;
        cb.addEventListener('change', () => {
            postAction('puncToggle', { enabled: cb.checked });
        });
    }

    // Audio buttons
    if (CONFIG.hasAudio) {
        document.getElementById('play-btn').classList.remove('hidden');
        document.getElementById('save-btn').classList.remove('hidden');
    }

    // Mode segment
    if (CONFIG.modes.length > 0) {
        const bar = document.getElementById('mode-segment');
        bar.classList.remove('hidden');
        CONFIG.modes.forEach(([id, label], i) => {
            const btn = document.createElement('button');
            btn.className = 'segment-btn' + (id === CONFIG.currentMode ? ' active' : '');
            btn.textContent = label;
            btn.dataset.index = i;
            btn.addEventListener('click', () => selectMode(i));
            bar.appendChild(btn);
        });
    }

    // Enhance section
    if (CONFIG.showEnhance || CONFIG.modes.length > 0) {
        document.getElementById('enhance-section').classList.remove('hidden');
        if (CONFIG.showEnhance) {
            document.getElementById('enhance-info').textContent = '⏳ Processing...';
        } else {
            document.getElementById('enhance-info').textContent = 'Off';
        }
    }

    // LLM popup
    if (CONFIG.llmModels.length > 0) {
        const sel = document.getElementById('llm-select');
        sel.classList.remove('hidden');
        CONFIG.llmModels.forEach((name, i) => {
            const opt = document.createElement('option');
            opt.value = i; opt.textContent = name;
            if (i === CONFIG.llmCurrentIndex) opt.selected = true;
            sel.appendChild(opt);
        });
        sel.addEventListener('change', () => {
            postAction('llmModelChange', { index: parseInt(sel.value) });
        });
    }

    // Thinking checkbox
    const tcb = document.getElementById('thinking-cb');
    tcb.checked = CONFIG.thinkingEnabled;
    tcb.addEventListener('change', () => {
        postAction('thinkingToggle', { enabled: tcb.checked });
    });

    // History button
    if (CONFIG.previewHistory && CONFIG.previewHistory.length > 0) {
        const btn = document.getElementById('history-btn');
        btn.style.display = '';
        btn.textContent = 'History (' + CONFIG.previewHistory.length + ')';
        buildHistoryDropdown(CONFIG.previewHistory);
    }

    // Final text edit tracking
    document.getElementById('final-text').addEventListener('input', () => {
        if (!userEdited) {
            userEdited = true;
            postAction('userEdit');
        }
    });

    // Focus final text and move cursor to end
    const ft = document.getElementById('final-text');
    ft.focus();
    ft.setSelectionRange(ft.value.length, ft.value.length);
}

// --- Actions ---
function postAction(type, data) {
    const msg = Object.assign({ type: type }, data || {});
    try {
        window.webkit.messageHandlers.action.postMessage(msg);
    } catch (e) {
        console.error('postMessage failed:', e);
        document.title = 'ERR: ' + e.message;
    }
}

function selectMode(index) {
    document.querySelectorAll('.segment-btn').forEach((btn, i) => {
        btn.classList.toggle('active', i === index);
    });
    postAction('modeChange', { index: index });
}

function doConfirm(copyToClipboard) {
    const text = document.getElementById('final-text').value;
    const enhanceText = document.getElementById('enhance-text').textContent;
    postAction('confirm', {
        text: text,
        enhanceText: enhanceText,
        userEdited: userEdited,
        copyToClipboard: copyToClipboard
    });
}

function doTranslate() {
    const text = document.getElementById('final-text').value.trim();
    if (text) postAction('googleTranslate', { text: text });
}

// --- Keyboard shortcuts ---
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        e.preventDefault();
        postAction('cancel');
        return;
    }
    if (e.metaKey && e.key === 'Enter') {
        e.preventDefault();
        doConfirm(true);
        return;
    }
    if (e.key === 'Enter' && !e.metaKey && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        doConfirm(false);
        return;
    }
    // ⌘1~⌘9 mode switching
    if (e.metaKey && !e.shiftKey && !e.altKey && e.key >= '1' && e.key <= '9') {
        const index = parseInt(e.key) - 1;
        const btns = document.querySelectorAll('.segment-btn');
        if (index < btns.length) {
            e.preventDefault();
            selectMode(index);
        }
    }
});

// Track ⌘ key state for confirm button text
document.addEventListener('keydown', (e) => {
    if (e.key === 'Meta' && !cmdHeld) {
        cmdHeld = true;
        document.getElementById('confirm-btn').textContent = 'Copy ⌘⏎';
    }
});
document.addEventListener('keyup', (e) => {
    if (e.key === 'Meta' && cmdHeld) {
        cmdHeld = false;
        document.getElementById('confirm-btn').textContent = 'Confirm ⏎';
    }
});

// --- Python→JS API ---
function setAsrText(text) {
    document.getElementById('asr-text').textContent = text;
    if (!userEdited) document.getElementById('final-text').value = text;
}

function setAsrLoading() {
    document.getElementById('asr-text').innerHTML = '<span class="loading">⏳ Re-transcribing...</span>';
    const sel = document.getElementById('stt-select');
    if (sel) sel.disabled = true;
}

function setAsrResult(text, info) {
    document.getElementById('asr-text').textContent = text;
    document.getElementById('asr-info').textContent = info;
    if (!userEdited) document.getElementById('final-text').value = text;
    const sel = document.getElementById('stt-select');
    if (sel) sel.disabled = false;
}

function setSttPopupIndex(index) {
    const sel = document.getElementById('stt-select');
    if (sel) { sel.value = index; sel.disabled = false; }
}

function clearEnhanceText() {
    document.getElementById('enhance-text').innerHTML = '';
}

function appendEnhanceText(chunk) {
    const el = document.getElementById('enhance-text');
    el.textContent += chunk;
    el.scrollTop = el.scrollHeight;
}

function appendThinkingText(chunk) {
    const el = document.getElementById('enhance-text');
    // Create or find thinking span
    let span = el.querySelector('.thinking-current');
    if (!span) {
        span = document.createElement('span');
        span.className = 'thinking thinking-current';
        el.appendChild(span);
    }
    span.textContent += chunk;
    el.scrollTop = el.scrollHeight;
}

function setEnhanceResult(text) {
    const el = document.getElementById('enhance-text');
    el.textContent = text;
}

function setEnhanceInfo(text) {
    const el = document.getElementById('enhance-info');
    let safe = text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    safe = safe.replace(/(\u2191[\d,]+)\+/g, '<span style="opacity:0.5">$1</span>+');
    el.innerHTML = safe;
}

function setEnhanceLoading() {
    document.getElementById('enhance-section').classList.remove('hidden');
    document.getElementById('enhance-text').innerHTML = '';
    document.getElementById('enhance-info').textContent = '⏳ Processing...';
    const _tb = document.getElementById('thinking-btn');
    _tb.classList.add('disabled'); _tb.style.opacity = '0.3';
    userEdited = false;
}

function setEnhanceOff() {
    document.getElementById('enhance-info').textContent = 'Off';
    document.getElementById('enhance-text').innerHTML = '';
}

function setEnhanceComplete(info, hasThinking, finalText) {
    setEnhanceInfo(info);
    if (hasThinking) {
        const _tb = document.getElementById('thinking-btn');
        _tb.classList.remove('disabled'); _tb.style.opacity = '1';
    }
    if (!userEdited) {
        document.getElementById('final-text').value =
            finalText !== null ? finalText : document.getElementById('enhance-text').textContent;
    }
}

function enablePromptButton() {
    document.getElementById('prompt-btn').classList.remove('disabled');
}

function setFinalText(text) {
    document.getElementById('final-text').value = text;
}

function finishThinkingSpan() {
    const span = document.querySelector('.thinking-current');
    if (span) span.classList.remove('thinking-current');
}

function replayCachedResult(displayText, info, hasThinking, finalText) {
    const el = document.getElementById('enhance-text');
    el.textContent = displayText;
    setEnhanceInfo(info);
    if (hasThinking) {
        const _tb = document.getElementById('thinking-btn');
        _tb.classList.remove('disabled'); _tb.style.opacity = '1';
    }
    if (!userEdited) {
        document.getElementById('final-text').value =
            finalText !== null ? finalText : displayText;
    }
}

function updateLoadingTimer(seconds) {
    const info = document.getElementById('enhance-info');
    if (info.textContent.startsWith('⏳')) {
        info.textContent = '⏳ Processing... ' + seconds + 's';
    }
}

function setStepInfo(text) {
    document.getElementById('enhance-info').textContent = text;
}

// --- History dropdown ---
let historyDropdownVisible = false;

function toggleHistoryDropdown() {
    const dd = document.getElementById('history-dropdown');
    historyDropdownVisible = !historyDropdownVisible;
    dd.style.display = historyDropdownVisible ? 'block' : 'none';
}

function buildHistoryDropdown(items) {
    const dd = document.getElementById('history-dropdown');
    dd.innerHTML = '';
    items.forEach((item, i) => {
        const row = document.createElement('div');
        row.style.cssText = 'padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border);'
            + 'font-size:12px;display:flex;gap:8px;align-items:baseline;';
        row.addEventListener('mouseenter', () => { row.style.background = 'var(--btn-hover)'; });
        row.addEventListener('mouseleave', () => { row.style.background = 'transparent'; });

        const action = document.createElement('span');
        action.style.cssText = 'flex-shrink:0; font-size:12px; width:14px; text-align:center;';
        action.textContent = item.action || '';

        const time = document.createElement('span');
        time.style.cssText = 'color:var(--secondary); white-space:nowrap; flex-shrink:0; font-size:11px;';
        time.textContent = item.time;

        const mode = document.createElement('span');
        mode.style.cssText = 'color:var(--accent); white-space:nowrap; flex-shrink:0; font-size:10px;';
        mode.textContent = item.mode || '';

        const preview = document.createElement('span');
        preview.style.cssText = 'color:var(--text); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1; min-width:0;';
        preview.textContent = item.preview;

        row.appendChild(action);
        row.appendChild(time);
        if (item.mode) row.appendChild(mode);
        row.appendChild(preview);
        row.addEventListener('click', () => {
            historyDropdownVisible = false;
            dd.style.display = 'none';
            postAction('selectHistory', { index: i });
        });
        dd.appendChild(row);
    });
}

function loadHistoryRecord(data) {
    // Update ASR
    document.getElementById('asr-text').textContent = data.asrText;
    document.getElementById('asr-info').textContent = data.asrInfo || '';

    // Update enhance
    const enhEl = document.getElementById('enhance-text');
    if (data.enhancedText) {
        document.getElementById('enhance-section').classList.remove('hidden');
        enhEl.textContent = data.enhancedText;
        document.getElementById('enhance-info').textContent = data.enhanceMode || '';
    } else {
        enhEl.textContent = '';
        document.getElementById('enhance-info').textContent = data.enhanceMode || 'Off';
    }

    // Update final text
    document.getElementById('final-text').value = data.finalText;
    userEdited = false;

    // Audio buttons
    const playBtn = document.getElementById('play-btn');
    const saveBtn = document.getElementById('save-btn');
    if (data.hasAudio) {
        playBtn.classList.remove('hidden');
        saveBtn.classList.remove('hidden');
    } else {
        playBtn.classList.add('hidden');
        saveBtn.classList.add('hidden');
    }

    // Focus final text
    const ft = document.getElementById('final-text');
    ft.focus();
    ft.setSelectionRange(ft.value.length, ft.value.length);
}

// Close dropdown on outside click
document.addEventListener('click', (e) => {
    if (historyDropdownVisible) {
        const wrap = document.getElementById('history-dropdown-wrap');
        if (!wrap.contains(e.target)) {
            historyDropdownVisible = false;
            document.getElementById('history-dropdown').style.display = 'none';
        }
    }
});

// --- Start ---
init();
</script>
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
        from Foundation import NSObject

        import WebKit  # noqa: F401

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
# Close delegate (lazy-created to avoid PyObjC import at module level)
# ---------------------------------------------------------------------------
_PanelCloseDelegate = None


def _get_panel_close_delegate_class():
    global _PanelCloseDelegate
    if _PanelCloseDelegate is None:
        from Foundation import NSObject

        class WebResultPanelCloseDelegate(NSObject):
            _panel_ref = None

            def windowWillClose_(self, notification):
                if self._panel_ref is not None:
                    self._panel_ref.cancelClicked_(None)

        _PanelCloseDelegate = WebResultPanelCloseDelegate
    return _PanelCloseDelegate


# ---------------------------------------------------------------------------
# WKScriptMessageHandler (lazy-created)
# ---------------------------------------------------------------------------
_MessageHandler = None


def _get_message_handler_class():
    global _MessageHandler
    if _MessageHandler is None:
        import objc
        from Foundation import NSObject

        # Load WebKit framework first so the protocol is available
        import WebKit  # noqa: F401

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

    def __init__(self) -> None:
        self._panel = None
        self._webview = None
        self._close_delegate = None
        self._message_handler = None
        self._on_confirm: Optional[Callable] = None
        self._on_cancel: Optional[Callable] = None
        self._on_mode_change: Optional[Callable[[str], None]] = None
        self._on_stt_model_change: Optional[Callable[[int], None]] = None
        self._on_llm_model_change: Optional[Callable[[int], None]] = None
        self._on_punc_toggle: Optional[Callable[[bool], None]] = None
        self._on_thinking_toggle: Optional[Callable[[bool], None]] = None
        self._on_google_translate: Optional[Callable[[], None]] = None
        self._on_select_history: Optional[Callable[[int], None]] = None
        self._preview_history_items: list = []
        self._user_edited = False
        self._show_enhance = False
        self._asr_text = ""
        self._available_modes: List[Tuple[str, str]] = []
        self._current_mode: str = "off"
        self._asr_info: str = ""
        self._asr_wav_data: Optional[bytes] = None
        self._asr_sound = None
        self._enhance_info: str = ""
        self._enhance_request_id: int = 0
        self._asr_request_id: int = 0
        self._system_prompt: str = ""
        self._stt_models: List[str] = []
        self._llm_models: List[str] = []
        self._stt_current_index: int = 0
        self._llm_current_index: int = 0
        self._source: str = "voice"
        self._punc_enabled: bool = True
        self._thinking_enabled: bool = False
        self._thinking_text: str = ""
        self._loading_timer = None
        self._loading_seconds: int = 0
        self._translate_webview = None
        self._page_loaded: bool = False
        self._pending_js: list[str] = []
        self._navigation_delegate = None

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
            from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

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
            config = WKWebViewConfiguration.alloc().init()
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
        available_modes: Optional[List[Tuple[str, str]]] = None,
        current_mode: Optional[str] = None,
        on_mode_change: Optional[Callable[[str], None]] = None,
        asr_info: str = "",
        asr_wav_data: Optional[bytes] = None,
        enhance_info: str = "",
        stt_models: Optional[List[str]] = None,
        stt_current_index: int = 0,
        on_stt_model_change: Optional[Callable[[int], None]] = None,
        llm_models: Optional[List[str]] = None,
        llm_current_index: int = 0,
        on_llm_model_change: Optional[Callable[[int], None]] = None,
        source: str = "voice",
        punc_enabled: bool = True,
        on_punc_toggle: Optional[Callable[[bool], None]] = None,
        thinking_enabled: bool = False,
        on_thinking_toggle: Optional[Callable[[bool], None]] = None,
        on_google_translate: Optional[Callable[[], None]] = None,
        on_select_history: Optional[Callable[[int], None]] = None,
        preview_history_items: Optional[list] = None,
        animate_from_frame: object = None,
    ) -> None:
        """Show the preview panel with ASR text."""
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
        self._preview_history_items = preview_history_items or []
        self._user_edited = False
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
                suffix = f"\u25b6 Thinking: {thinking_tokens:,}"
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
                suffix = f"\u25b6 Tokens: \u2193{completion_tokens:,}"
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

        AppHelper.callAfter(_update)

    def load_history_record(
        self,
        asr_text: str,
        enhanced_text: Optional[str],
        final_text: str,
        enhance_mode: str,
        has_audio: bool,
        asr_info: str = "",
    ) -> None:
        """Load a history record into the preview panel."""
        if self._webview is None:
            return

        from PyObjCTools import AppHelper

        data = {
            "asrText": asr_text,
            "enhancedText": enhanced_text,
            "finalText": final_text,
            "enhanceMode": enhance_mode,
            "hasAudio": has_audio,
            "asrInfo": asr_info,
        }

        def _update():
            if self._webview is None:
                return
            self._asr_text = asr_text
            self._user_edited = False
            self._eval_js(f"loadHistoryRecord({json.dumps(data)})")

        AppHelper.callAfter(_update)

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
    def is_visible(self) -> bool:
        return self._panel is not None and self._panel.isVisible()

    def bring_to_front(self) -> None:
        if self._panel is not None and self._panel.isVisible():
            self._panel.makeKeyAndOrderFront_(None)
            from AppKit import NSApp
            NSApp.activateIgnoringOtherApps_(True)

    def close(self) -> None:
        """Close the panel."""
        self._stop_playback()
        self._stop_loading_timer()
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
        self._on_confirm = None
        self._on_cancel = None

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

        elif msg_type == "showThinking":
            if self._thinking_text:
                self._show_info_panel("Thinking", self._thinking_text)

        elif msg_type == "showPrompt":
            if self._system_prompt:
                self._show_info_panel("System Prompt", self._system_prompt)

        elif msg_type == "playAudio":
            if self._asr_wav_data:
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

    def _build_panel(self) -> None:
        """Build NSPanel + WKWebView, reusing pre-created objects from warmup()."""
        from AppKit import (
            NSApp,
            NSScreen,
        )
        from Foundation import NSMakeRect, NSURL

        # Enable ⌘C/⌘V/⌘A via Edit menu in the responder chain
        from voicetext.ui.result_window import _ensure_edit_menu
        _ensure_edit_menu()

        # Calculate panel height based on content
        has_modes = len(self._available_modes) > 0
        show_enhance_section = self._show_enhance or has_modes
        height = self._PANEL_HEIGHT
        if not show_enhance_section:
            height -= 60  # Less height without enhance section
        if not has_modes:
            height -= 20  # Less height without mode segment

        NSApp.setActivationPolicy_(0)  # Regular (foreground)

        if self._panel is not None and self._webview is not None:
            # Reuse pre-created panel and webview from warmup()
            panel = self._panel
            webview = self._webview
            panel.setContentSize_((self._PANEL_WIDTH, height))
            webview.setFrame_(NSMakeRect(0, 0, self._PANEL_WIDTH, height))
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
            from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, self._PANEL_WIDTH, height),
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
            config = WKWebViewConfiguration.alloc().init()
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

        panel_title = "Enhance Clipboard" if self._source == "clipboard" else "Preview"
        panel.setTitle_(panel_title)

        # Center on screen
        screen = NSScreen.mainScreen()
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
            "asrTitle": "Clipboard Text" if self._source == "clipboard" else "ASR",
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
            "previewHistory": self._preview_history_items,
        }
        html = _HTML_TEMPLATE.replace(
            "__CONFIG__", json.dumps(config_data, ensure_ascii=False)
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
        if self._asr_sound is not None:
            try:
                self._asr_sound.stop()
            except Exception:
                pass
            self._asr_sound = None

    def _play_wav(self, wav_data: bytes) -> None:
        from AppKit import NSSound
        from Foundation import NSData

        self._stop_playback()
        ns_data = NSData.dataWithBytes_length_(wav_data, len(wav_data))
        sound = NSSound.alloc().initWithData_(ns_data)
        if sound:
            sound.play()
            self._asr_sound = sound

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
