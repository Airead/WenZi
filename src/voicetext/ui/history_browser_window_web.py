"""Web-based history browser panel using WKWebView.

Drop-in replacement for the AppKit-based HistoryBrowserPanel, with the
same public API surface.  See dev/wkwebview-pitfalls.md for background.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set

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
    --text-bg: #ffffff; --row-hover: #e8f0fe; --row-selected: #3a76c4;
    --btn-bg: #e5e5ea; --btn-hover: #d1d1d6;
    --btn-primary-bg: #007aff; --btn-primary-text: #ffffff;
    --focus-ring: rgba(0, 122, 255, 0.4);
    --alt-row: #fafafa;
    --tag-proofread: #4a90d9; --tag-translate: #9b6fb0;
    --tag-format: #5da469; --tag-off: #8e8e93;
    --tag-corrected: #cc8840; --tag-stt: #5a9eb8; --tag-llm: #c06080;
    --tag-pill-bg: rgba(0,0,0,0.06); --tag-pill-text: var(--secondary);
}
@media (prefers-color-scheme: dark) {
    :root {
        --bg: #1d1d1f; --text: #c8c8cc; --card-bg: #2c2c2e;
        --border: #48484a; --secondary: #98989d; --accent: #0a84ff;
        --text-bg: #1c1c1e; --row-hover: #2c3a50; --row-selected: #2e5080;
        --btn-bg: #3a3a3c; --btn-hover: #48484a;
        --btn-primary-bg: #0a84ff; --btn-primary-text: #ffffff;
        --focus-ring: rgba(10, 132, 255, 0.4);
        --alt-row: #242426;
        --tag-proofread: #5a9ad9; --tag-translate: #9a7ab8;
        --tag-format: #6aad76; --tag-off: #787880;
        --tag-corrected: #c89050; --tag-stt: #6aafc5; --tag-llm: #b87090;
        --tag-pill-bg: rgba(255,255,255,0.08); --tag-pill-text: var(--secondary);
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

/* Search bar */
.search-bar {
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
.time-select {
    height: 28px; padding: 0 8px;
    border: 1px solid var(--border); border-radius: 6px;
    background: var(--text-bg); color: var(--text);
    font-size: 12px; outline: none; cursor: pointer;
}
.time-select:focus { border-color: var(--accent); }
.btn {
    height: 28px; padding: 0 14px; border: none; border-radius: 6px;
    font-size: 12px; font-weight: 500; cursor: pointer;
    background: var(--btn-bg); color: var(--text);
    transition: background 0.15s; white-space: nowrap;
}
.btn:hover { background: var(--btn-hover); }
.btn-primary { background: var(--btn-primary-bg); color: var(--btn-primary-text); }
.btn-primary:hover { opacity: 0.9; }
.btn-danger { background: #c44; color: #fff; }
.btn-danger:hover { background: #b33; }
.btn:disabled { opacity: 0.4; cursor: default; }

/* Tag filter row */
.tag-row {
    display: flex; align-items: center; gap: 5px;
    margin-bottom: 8px; flex-shrink: 0;
    overflow-x: auto; overflow-y: hidden;
    scrollbar-width: none; -webkit-overflow-scrolling: touch;
}
.tag-row::-webkit-scrollbar { display: none; }
.tag-group-label {
    font-size: 10px; color: var(--secondary); font-weight: 600;
    white-space: nowrap; margin-left: 6px;
    -webkit-user-select: none; user-select: none;
}
.tag-group-label:first-child { margin-left: 0; }
.tag-sep {
    width: 1px; height: 16px; background: var(--border);
    margin: 0 4px; flex-shrink: 0;
}
.tag-pill {
    display: inline-flex; align-items: center; flex-shrink: 0;
    height: 20px; padding: 0 7px; border-radius: 10px;
    font-size: 10px; font-weight: 500; cursor: pointer;
    border: 1px solid var(--border);
    background: var(--tag-pill-bg); color: var(--tag-pill-text);
    transition: all 0.15s; position: relative;
    -webkit-user-select: none; user-select: none;
}
/* Dimmed: color from --c custom property set via JS */
.tag-pill {
    --c: var(--secondary);
    color: var(--c);
    background: color-mix(in srgb, var(--c) 15%, transparent);
    border-color: color-mix(in srgb, var(--c) 25%, transparent);
}
.tag-pill:hover { opacity: 0.85; }
.tag-pill.active {
    color: #fff; border-color: transparent;
    background: var(--c);
}

/* Stats */
.stats-line {
    font-size: 11px; color: var(--secondary); margin-bottom: 6px; flex-shrink: 0;
    -webkit-user-select: none; user-select: none;
}
.stats-line .filtered { color: var(--accent); margin-left: 4px; }

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
.table-header .col { padding: 6px 8px; }
.table-body { flex: 1; overflow-y: auto; overflow-x: hidden; }
.row {
    display: flex; align-items: center; cursor: pointer;
    border-bottom: 1px solid var(--border);
    transition: background 0.1s;
}
.row:last-child { border-bottom: none; }
.row:nth-child(even) { background: var(--alt-row); }
.row:hover { background: var(--row-hover); }
.row.selected { background: var(--row-selected); color: #fff; }
.row.selected .col { color: #fff; }
.row.selected .col-time { color: rgba(255,255,255,0.7); }
.row.selected .mini-tag { opacity: 0.9; }
.col {
    padding: 5px 8px; font-size: 12px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.col-time { width: 166px; flex-shrink: 0; color: var(--secondary); font-family: "SF Mono", Menlo, monospace; }
.col-mode { width: 80px; flex-shrink: 0; }
.col-content { flex: 1; min-width: 0; }
.col-tags {
    flex-shrink: 0; width: auto;
    display: flex; gap: 3px; align-items: center;
    white-space: nowrap; padding-right: 8px;
}
.mini-tag {
    display: inline-flex; align-items: center; justify-content: center;
    width: 62px; padding: 1px 4px; border-radius: 8px;
    font-family: "SF Mono", Menlo, monospace;
    font-size: 9px; font-weight: 600; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
    cursor: default; position: relative; flex-shrink: 0;
    --mc: #888;
    color: var(--mc);
    background: color-mix(in srgb, var(--mc) 18%, transparent);
}
.mini-tag.mini-corr { width: 34px; }
.mini-tag-placeholder {
    display: inline-block; width: 34px; height: 14px; flex-shrink: 0;
}
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
    border: 1px solid var(--row-selected); border-radius: 6px;
    background: var(--text-bg); color: var(--text);
    font-family: "SF Mono", Menlo, monospace; font-size: 12px;
    outline: none;
    -webkit-user-select: text; user-select: text;
}
.final-input:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--focus-ring); }
.final-input:disabled { opacity: 0.5; border-color: var(--border); }
.detail-info {
    display: flex; align-items: center; gap: 16px;
    font-size: 11px; color: var(--secondary); margin-top: 2px;
}

/* Bottom buttons */
.btn-row {
    display: flex; justify-content: flex-end; gap: 8px;
    margin-top: 8px; flex-shrink: 0;
}

/* Global tooltip */
#tooltip {
    display: none; position: fixed; padding: 4px 10px; border-radius: 5px;
    background: var(--card-bg); color: var(--text);
    border: 1px solid var(--border);
    font-size: 11px; white-space: nowrap; z-index: 9999;
    pointer-events: none; box-shadow: 0 2px 8px rgba(0,0,0,0.15);
}
</style>
</head>
<body>

<div class="search-bar">
    <input type="text" class="search-input" id="search" placeholder="Search history...">
    <select class="time-select" id="time-range">
        <option value="all">All Time</option>
        <option value="today">Today</option>
        <option value="7d" selected>Last 7 Days</option>
        <option value="30d">Last 30 Days</option>
    </select>
    <button class="btn" id="clear-btn">Clear</button>
</div>

<div class="tag-row" id="tag-row"></div>

<div class="stats-line" id="stats-line"></div>

<div class="table-wrap">
    <div class="table-header">
        <div class="col col-time">Time ↓</div>
        <div class="col col-mode">Mode</div>
        <div class="col col-content">Content</div>
        <div class="col col-tags">Tags</div>
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
    <button class="btn btn-danger" id="delete-btn" disabled>Delete</button>
    <span style="flex:1"></span>
    <button class="btn btn-primary" id="save-btn" disabled>Save</button>
    <button class="btn" id="close-btn">Close</button>
</div>

<div id="tooltip"></div>

<script>
const tooltipEl = document.getElementById('tooltip');
const tableBody = document.getElementById('table-body');
const detail = document.getElementById('detail');
const searchEl = document.getElementById('search');
const timeRange = document.getElementById('time-range');
const clearBtn = document.getElementById('clear-btn');
const tagRow = document.getElementById('tag-row');
const statsLine = document.getElementById('stats-line');
const asrLabel = document.getElementById('asr-label');
const asrText = document.getElementById('asr-text');
const enhancedLabel = document.getElementById('enhanced-label');
const enhancedText = document.getElementById('enhanced-text');
const finalInput = document.getElementById('final-input');
const modeInfo = document.getElementById('mode-info');
const timeInfo = document.getElementById('time-info');
const deleteBtn = document.getElementById('delete-btn');
const saveBtn = document.getElementById('save-btn');
const closeBtn = document.getElementById('close-btn');

let selectedIndex = -1;
let currentRecords = [];
let originalFinalText = '';
let activeTags = new Set();

const MODE_COLORS = {
    proofread: 'proofread', translate: 'translate',
    format: 'format', off: 'off',
};
function tagColor(tag, group) {
    if (group === 'stt') return 'stt';
    if (group === 'llm') return 'llm';
    if (tag === 'corrected') return 'corrected';
    if (tag.startsWith('translate')) return 'translate';
    return MODE_COLORS[tag] || 'other';
}
function tagBgColor(tag, group) {
    const map = {
        proofread: 'var(--tag-proofread)', translate: 'var(--tag-translate)',
        format: 'var(--tag-format)', off: 'var(--tag-off)',
        corrected: 'var(--tag-corrected)', stt: 'var(--tag-stt)',
        llm: 'var(--tag-llm)', other: 'var(--accent)',
    };
    return map[tagColor(tag, group)] || map.other;
}

function post(msg) {
    window.webkit.messageHandlers.action.postMessage(msg);
}

/* --- Search bar (auto-query with debounce) --- */
let searchTimer = null;
function triggerSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
        post({type:'search', text: searchEl.value, timeRange: timeRange.value});
    }, 300);
}
searchEl.addEventListener('input', triggerSearch);
timeRange.addEventListener('change', triggerSearch);
clearBtn.addEventListener('click', () => {
    searchEl.value = '';
    timeRange.value = '7d';
    activeTags.clear();
    post({type:'clearFilters'});
});

/* --- Tag toggle --- */
function onTagClick(tag) {
    if (activeTags.has(tag)) activeTags.delete(tag);
    else activeTags.add(tag);
    renderTagPills();
    post({type:'toggleTags', tags: [...activeTags]});
}

/* --- Table row click --- */
tableBody.addEventListener('click', (e) => {
    const row = e.target.closest('.row');
    if (!row) return;
    const idx = parseInt(row.dataset.idx, 10);
    selectRow(idx);
    post({type:'selectRow', index: idx});
});

function selectRow(idx) {
    document.querySelectorAll('.row.selected').forEach(r => r.classList.remove('selected'));
    selectedIndex = idx;
    const row = tableBody.querySelector(`.row[data-idx="${idx}"]`);
    if (row) row.classList.add('selected');
}

/* --- Final text edit --- */
finalInput.addEventListener('input', () => {
    saveBtn.disabled = (finalInput.value === originalFinalText);
});

/* --- Buttons --- */
deleteBtn.addEventListener('click', () => {
    if (selectedIndex < 0 || selectedIndex >= currentRecords.length) return;
    const rec = currentRecords[selectedIndex];
    post({type:'delete', timestamp: rec.timestamp || ''});
});
saveBtn.addEventListener('click', () => {
    if (selectedIndex < 0 || selectedIndex >= currentRecords.length) return;
    const rec = currentRecords[selectedIndex];
    post({type:'save', timestamp: rec.timestamp || '', text: finalInput.value});
});
closeBtn.addEventListener('click', () => post({type:'close'}));

/* --- Keyboard --- */
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        e.preventDefault();
        if (detail.style.display !== 'none') clearDetail();
        else post({type:'close'});
    }
    if (e.metaKey && e.key === 's') { e.preventDefault(); if (!saveBtn.disabled) saveBtn.click(); }
});

/* === Python → JS API === */

function setRecords(records, totalCount) {
    currentRecords = records;
    selectedIndex = -1;
    detail.style.display = 'none';
    finalInput.disabled = true; finalInput.value = '';
    saveBtn.disabled = true; deleteBtn.disabled = true;

    /* Stats */
    if (totalCount !== records.length) {
        statsLine.innerHTML = `Total ${totalCount} records<span class="filtered"> (${records.length} filtered)</span>`;
    } else {
        statsLine.textContent = `Total ${totalCount} records`;
    }

    /* Table rows */
    if (records.length === 0) {
        tableBody.innerHTML = '<div class="empty-msg">No records found.</div>';
        return;
    }
    let html = '';
    for (let i = 0; i < records.length; i++) {
        const r = records[i];
        const ts = fmtTs(r.timestamp || '');
        const mode = r.enhance_mode || 'off';
        const stt = r.stt_model || '';
        const llm = r.llm_model || '';
        let preview = (r.final_text || r.asr_text || '').replace(/\n/g, ' ');
        if (preview.length > 80) preview = preview.substring(0, 80) + '\u2026';
        /* Mini tags: Corr first (with placeholder), then STT, LLM */
        let tags = '';
        if (r._corrected) tags += miniTag('Corr', 'corrected', 'User corrected', 'mini-corr');
        else tags += '<span class="mini-tag-placeholder"></span>';
        if (stt) tags += miniTag(abbr(stt), 'stt', 'STT: ' + stt);
        if (llm) tags += miniTag(abbr(llm), 'llm', 'LLM: ' + llm);
        html += `<div class="row" data-idx="${i}">` +
            `<div class="col col-time">${esc(ts)}</div>` +
            `<div class="col col-mode">${esc(mode)}</div>` +
            `<div class="col col-content">${esc(preview)}</div>` +
            `<div class="col col-tags">${tags}</div></div>`;
    }
    tableBody.innerHTML = html;
}

const GROUP_LABELS = {mode: 'Mode', stt: 'STT', llm: 'LLM', special: ''};
function setTagOptions(tags) {
    /* tags = [{name, count, group}, ...] where group is 'mode'|'stt'|'llm'|'special' */
    tagRow.innerHTML = '';
    let lastGroup = null;
    tags.forEach(t => {
        if (t.group !== lastGroup) {
            if (lastGroup !== null) {
                const sep = document.createElement('span');
                sep.className = 'tag-sep';
                tagRow.appendChild(sep);
            }
            const gl = GROUP_LABELS[t.group];
            if (gl) {
                const lbl = document.createElement('span');
                lbl.className = 'tag-group-label';
                lbl.textContent = gl + ':';
                tagRow.appendChild(lbl);
            }
            lastGroup = t.group;
        }
        const pill = document.createElement('span');
        pill.className = 'tag-pill' + (activeTags.has(t.name) ? ' active' : '');
        pill.setAttribute('data-name', t.name);
        pill.style.setProperty('--c', tagBgColor(t.name, t.group));
        const short = abbr(t.name);
        pill.textContent = short + ':' + t.count;
        if (short !== t.name) pill.setAttribute('data-tip', t.name);
        pill.addEventListener('click', () => onTagClick(t.name));
        tagRow.appendChild(pill);
    });
}

function renderTagPills() {
    tagRow.querySelectorAll('.tag-pill').forEach(pill => {
        const name = pill.getAttribute('data-name');
        if (activeTags.has(name)) pill.classList.add('active');
        else pill.classList.remove('active');
    });
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
    let ts = fmtTs(record.timestamp || '');
    let label = `Time: ${ts}`;
    if (record.edited_at) label += `  (edited: ${fmtTs(record.edited_at)})`;
    timeInfo.textContent = label;
    saveBtn.disabled = true;
    deleteBtn.disabled = false;
}

function clearDetail() {
    detail.style.display = 'none';
    finalInput.value = ''; finalInput.disabled = true;
    saveBtn.disabled = true; deleteBtn.disabled = true;
    selectedIndex = -1;
    document.querySelectorAll('.row.selected').forEach(r => r.classList.remove('selected'));
}

function markSaved(index) {
    if (index >= 0 && index < currentRecords.length) {
        originalFinalText = finalInput.value;
        currentRecords[index].final_text = finalInput.value;
        saveBtn.disabled = true;
        const row = tableBody.querySelector(`.row[data-idx="${index}"]`);
        if (row) {
            const c = row.querySelector('.col-content');
            if (c) {
                let p = (finalInput.value || '').replace(/\n/g, ' ');
                if (p.length > 60) p = p.substring(0, 60) + '\u2026';
                c.textContent = p;
            }
        }
    }
}

function resetFilters() {
    searchEl.value = '';
    timeRange.value = '7d';
    activeTags.clear();
}

/* --- Helpers --- */
function fmtTs(ts) {
    if (!ts || ts.length < 19) return ts;
    return ts.substring(0, 10) + ' ' + ts.substring(11, 19);
}
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function abbr(name) {
    if (name.length <= 8) return name;
    return name.substring(0, 4) + '\u2026' + name.slice(-4);
}
function miniTag(label, group, tooltip, extraCls) {
    const t = tooltip ? ` data-tip="${esc(tooltip)}"` : '';
    const cls = 'mini-tag' + (extraCls ? ' ' + extraCls : '');
    return `<span class="${cls}"${t} style="--mc:${tagBgColor(group, group)}">${esc(label)}</span>`;
}

/* --- Global tooltip on [data-tip] elements --- */
document.addEventListener('mouseover', (e) => {
    const el = e.target.closest('[data-tip]');
    if (!el) { tooltipEl.style.display = 'none'; return; }
    tooltipEl.textContent = el.getAttribute('data-tip');
    tooltipEl.style.display = 'block';
    const r = el.getBoundingClientRect();
    let x = r.left + r.width / 2 - tooltipEl.offsetWidth / 2;
    let y = r.top - tooltipEl.offsetHeight - 6;
    if (y < 4) y = r.bottom + 6;
    if (x < 4) x = 4;
    if (x + tooltipEl.offsetWidth > window.innerWidth - 4)
        x = window.innerWidth - tooltipEl.offsetWidth - 4;
    tooltipEl.style.left = x + 'px';
    tooltipEl.style.top = y + 'px';
});
document.addEventListener('mouseout', (e) => {
    if (e.target.closest('[data-tip]')) tooltipEl.style.display = 'none';
});
</script>
</body>
</html>"""


def _format_timestamp(ts: str) -> str:
    """Format ISO timestamp as 'YYYY-MM-DD HH:MM'."""
    try:
        return ts[:16].replace("T", " ")
    except Exception:
        return ts


def _time_range_cutoff(time_range: str) -> Optional[str]:
    """Return ISO timestamp cutoff for a time range value, or None for 'all'."""
    now = datetime.now(timezone.utc)
    if time_range == "today":
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif time_range == "7d":
        cutoff = now - timedelta(days=7)
    elif time_range == "30d":
        cutoff = now - timedelta(days=30)
    else:
        return None
    return cutoff.isoformat()


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

    _PANEL_WIDTH = 1000
    _PANEL_HEIGHT = 720

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
        self._time_range: str = "7d"
        self._active_tags: Set[str] = set()

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
        self._push_tag_options()
        self._push_records()

    def _apply_filters(self) -> None:
        """Filter _all_records by time range and active tags."""
        from voicetext.enhance.conversation_history import ConversationHistory

        records = self._all_records

        # Time range filter
        cutoff = _time_range_cutoff(self._time_range)
        if cutoff:
            records = [r for r in records if r.get("timestamp", "") >= cutoff]

        # Tag filter (OR logic): show records matching ANY active tag
        if self._active_tags:
            filtered = []
            for r in records:
                mode = r.get("enhance_mode", "off") or "off"
                stt = r.get("stt_model", "")
                llm = r.get("llm_model", "")
                is_corrected = ConversationHistory._is_corrected(r)
                if mode in self._active_tags:
                    filtered.append(r)
                elif stt and stt in self._active_tags:
                    filtered.append(r)
                elif llm and llm in self._active_tags:
                    filtered.append(r)
                elif "corrected" in self._active_tags and is_corrected:
                    filtered.append(r)
            records = filtered

        self._filtered_records = records

    def _push_records(self) -> None:
        """Send current filtered records to JS."""
        from voicetext.enhance.conversation_history import ConversationHistory

        records_json = []
        for r in self._filtered_records:
            entry = dict(r)
            entry["_corrected"] = ConversationHistory._is_corrected(r)
            records_json.append(entry)
        total = len(self._all_records)
        self._eval_js(f"setRecords({json.dumps(records_json, ensure_ascii=False)},{total})")

    def _push_tag_options(self) -> None:
        """Send available tag options with counts to JS."""
        from voicetext.enhance.conversation_history import ConversationHistory

        mode_counts: Dict[str, int] = {}
        stt_counts: Dict[str, int] = {}
        llm_counts: Dict[str, int] = {}
        corrected_count = 0
        cutoff = _time_range_cutoff(self._time_range)
        for r in self._all_records:
            if cutoff and r.get("timestamp", "") < cutoff:
                continue
            mode = r.get("enhance_mode", "off") or "off"
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
            stt = r.get("stt_model", "")
            if stt:
                stt_counts[stt] = stt_counts.get(stt, 0) + 1
            llm = r.get("llm_model", "")
            if llm:
                llm_counts[llm] = llm_counts.get(llm, 0) + 1
            if ConversationHistory._is_corrected(r):
                corrected_count += 1

        tags: List[Dict[str, Any]] = []
        # Corrected first
        if corrected_count > 0:
            tags.append({"name": "corrected", "count": corrected_count, "group": "special"})
        for m in sorted(mode_counts.keys()):
            tags.append({"name": m, "count": mode_counts[m], "group": "mode"})
        for s in sorted(stt_counts.keys()):
            tags.append({"name": s, "count": stt_counts[s], "group": "stt"})
        for lm in sorted(llm_counts.keys()):
            tags.append({"name": lm, "count": llm_counts[lm], "group": "llm"})
        self._eval_js(f"setTagOptions({json.dumps(tags)})")

    # ------------------------------------------------------------------
    # JS message handler
    # ------------------------------------------------------------------

    def _handle_js_message(self, body: dict) -> None:
        """Dispatch messages from JavaScript."""
        msg_type = body.get("type", "")

        if msg_type == "search":
            self._search_text = body.get("text", "")
            self._time_range = body.get("timeRange", "7d")
            self._reload_data()

        elif msg_type == "toggleTags":
            self._active_tags = set(body.get("tags", []))
            self._apply_filters()
            self._selected_index = -1
            self._push_records()
            self._eval_js("clearDetail()")

        elif msg_type == "clearFilters":
            self._search_text = ""
            self._time_range = "7d"
            self._active_tags = set()
            self._eval_js("resetFilters()")
            self._reload_data()

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

        elif msg_type == "delete":
            self._on_delete_clicked(body.get("timestamp", ""))

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

    def _on_delete_clicked(self, timestamp: str) -> None:
        """Delete a record from conversation history."""
        if not timestamp or self._conversation_history is None:
            return
        if self._selected_index < 0 or self._selected_index >= len(self._filtered_records):
            return

        ok = self._conversation_history.delete_record(timestamp)
        if ok:
            # Remove from both lists
            deleted = self._filtered_records[self._selected_index]
            self._filtered_records.pop(self._selected_index)
            if deleted in self._all_records:
                self._all_records.remove(deleted)
            self._selected_index = -1
            self._push_tag_options()
            self._push_records()
            self._eval_js("clearDetail()")

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
        panel.setMinSize_(NSMakeSize(800, 550))
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
