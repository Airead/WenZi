"""HTML/CSS/JS template for the Chooser panel."""

CHOOSER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {
    --bg: #f5f5f7;
    --text: #1d1d1f;
    --secondary: #86868b;
    --border: #d2d2d7;
    --accent: #007aff;
    --item-hover: rgba(0, 0, 0, 0.04);
    --item-selected: rgba(0, 122, 255, 0.12);
    --item-selected-text: #003d99;
    --input-bg: #ffffff;
    --footer-bg: #ececee;
    --shadow: rgba(0, 0, 0, 0.06);
}
@media (prefers-color-scheme: dark) {
    :root {
        --bg: #2c2c2e;
        --text: #e5e5e7;
        --secondary: #98989d;
        --border: #48484a;
        --accent: #0a84ff;
        --item-hover: rgba(255, 255, 255, 0.06);
        --item-selected: rgba(10, 132, 255, 0.25);
        --item-selected-text: #64d2ff;
        --input-bg: #3a3a3c;
        --footer-bg: #1c1c1e;
        --shadow: rgba(0, 0, 0, 0.3);
    }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
    height: 100%; overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    background: var(--bg); color: var(--text);
    -webkit-user-select: none; user-select: none;
}
body { display: flex; flex-direction: column; }

/* Main content: left panel + preview */
.main-content {
    display: flex; flex: 1; min-height: 0;
}
.left-panel {
    width: 400px; flex-shrink: 0;
    display: flex; flex-direction: column;
    min-height: 0;
}
.left-panel.full-width { width: 100%; }
.preview-panel {
    flex: 1; display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    border-left: 1px solid var(--border);
    padding: 16px; overflow: hidden;
    min-height: 0;
}
.preview-panel.hidden { display: none; }
.preview-panel.empty {
    color: var(--secondary); font-size: 13px;
}
.preview-text {
    width: 100%; height: 100%; overflow-y: auto;
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 12px; line-height: 1.5;
    white-space: pre-wrap; word-break: break-word;
    color: var(--text);
    -webkit-user-select: text; user-select: text;
}
.preview-text::-webkit-scrollbar { width: 6px; }
.preview-text::-webkit-scrollbar-thumb {
    background: var(--secondary); border-radius: 3px; opacity: 0.5;
}
.preview-image-wrapper {
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    width: 100%; height: 100%; gap: 8px;
}
.preview-image-wrapper img {
    max-width: 100%; max-height: calc(100% - 30px);
    object-fit: contain; border-radius: 4px;
}
.preview-image-info {
    font-size: 11px; color: var(--secondary);
    text-align: center;
}

/* Search bar */
.search-bar {
    display: flex; align-items: center; gap: 8px;
    padding: 12px 14px;
    background: var(--input-bg);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
}
.search-icon {
    font-size: 16px; color: var(--secondary); flex-shrink: 0;
    line-height: 1;
}
.search-input {
    flex: 1; border: none; outline: none;
    font-size: 16px; font-family: inherit;
    background: transparent; color: var(--text);
    caret-color: var(--accent);
}
.search-input::placeholder { color: var(--secondary); }

/* Create button (shown for sources that support creation) */
.create-group {
    display: none; flex-shrink: 0;
    align-items: center; gap: 4px;
}
.create-group.visible { display: flex; }
.create-btn {
    width: 24px; height: 24px;
    border: 1px solid var(--border); border-radius: 6px;
    background: var(--input-bg); color: var(--secondary);
    font-size: 18px; line-height: 22px; text-align: center;
    cursor: pointer; transition: all 0.15s;
    padding: 0;
}
.create-btn:hover { color: var(--accent); border-color: var(--accent); }
.create-hint {
    font-size: 10px; color: var(--secondary);
    background: var(--input-bg); border: 1px solid var(--border);
    border-radius: 3px; padding: 1px 4px;
    white-space: nowrap; opacity: 0.7;
}

/* Result list */
.result-list {
    flex: 1; overflow-y: auto; overflow-x: hidden;
    padding: 4px 0;
    position: relative;
}
.result-list::-webkit-scrollbar { width: 6px; }
.result-list::-webkit-scrollbar-thumb {
    background: var(--secondary); border-radius: 3px; opacity: 0.5;
}
.result-item {
    display: flex; align-items: center;
    padding: 6px 14px; cursor: default;
    transition: background 0.1s;
    gap: 10px;
    box-sizing: border-box;
    overflow: hidden;
}
.result-item .icon {
    width: 32px; height: 32px; flex-shrink: 0;
    border-radius: 6px;
    image-rendering: -webkit-optimize-contrast;
}
.result-item .icon-placeholder {
    background-color: var(--secondary);
    -webkit-mask-size: 20px 20px;
    -webkit-mask-repeat: no-repeat;
    -webkit-mask-position: center;
    opacity: 0.35;
}
#placeholder-mask-style { display: none; }
.result-item.selected { background: var(--item-selected); }
.result-item .left {
    display: flex; flex-direction: column; gap: 1px;
    min-width: 0; flex: 1;
}
.result-item .title {
    font-size: 14px; font-weight: 500;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.result-item.selected .title { color: var(--item-selected-text); }
.result-item .subtitle-text {
    font-size: 11px; color: var(--secondary);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.result-item .right-group {
    display: flex; align-items: center; gap: 6px;
    flex-shrink: 0;
}
.result-item .badge {
    font-size: 11px; color: var(--secondary);
    white-space: nowrap;
}
.result-item .shortcut {
    font-size: 10px; color: var(--secondary);
    background: var(--input-bg); border: 1px solid var(--border);
    border-radius: 3px; padding: 1px 4px;
    white-space: nowrap; opacity: 0.7;
}
.result-item .delete-btn {
    flex-shrink: 0; height: 18px; min-width: 18px;
    border: none; border-radius: 9px;
    background: transparent; color: var(--secondary);
    font-size: 13px; line-height: 18px; text-align: center;
    cursor: pointer; opacity: 0; transition: opacity 0.15s, background 0.15s, color 0.15s;
    padding: 0;
}
.result-item.selected .delete-btn { opacity: 0.6; }
.result-item .delete-btn:hover {
    opacity: 1; background: var(--item-hover); color: var(--text);
}
.result-item .delete-btn.confirm {
    opacity: 1; background: #ff3b30; color: #fff;
    font-size: 10px; padding: 0 6px; font-weight: 500;
}

/* Empty state */
.empty-state {
    display: flex; align-items: center; justify-content: center;
    flex: 1; color: var(--secondary); font-size: 13px;
    padding: 20px;
}

/* Footer hint */
.footer {
    display: flex; align-items: center; justify-content: space-between;
    padding: 6px 14px; font-size: 11px; color: var(--secondary);
    background: var(--footer-bg);
    border-top: 1px solid var(--border); flex-shrink: 0;
    gap: 12px;
}
.footer kbd {
    display: inline-block; padding: 1px 5px;
    background: var(--input-bg); border: 1px solid var(--border);
    border-radius: 3px; font-size: 10px; font-family: inherit;
}
</style>
</head>
<body>

<div class="search-bar">
    <span class="search-icon">&#128269;</span>
    <input class="search-input" id="search-input"
           type="text" placeholder="Search..." autocomplete="off"
           autocorrect="off" autocapitalize="off" spellcheck="false">
    <span class="create-group" id="create-group">
        <button class="create-btn" id="create-btn" title="New...">+</button>
        <span class="create-hint">&#8984;N</span>
    </span>
</div>

<div class="main-content">
    <div class="left-panel full-width">
        <div class="result-list" id="result-list"></div>
        <div class="empty-state" id="empty-state" style="display:none;">
            Type to search
        </div>
    </div>
    <div class="preview-panel empty hidden" id="preview-panel">
        Select an item to preview
    </div>
</div>

<div class="footer" id="footer">
    <span id="footer-left"></span>
    <span id="footer-right"></span>
</div>

<script>
// --- State ---
var items = [];
var selectedIndex = -1;
var itemsVersion = 0;
var hasAnyIcon = false;  // true when at least one item has an icon
var _lastMouseX = -1, _lastMouseY = -1;  // suppress scroll-induced hover
var _PLACEHOLDER_SVG = "data:image/svg+xml,"
    + "%3Csvg xmlns='http://www.w3.org/2000/svg' "
    + "viewBox='0 0 24 24' fill='none' stroke='black' "
    + "stroke-width='1.5' stroke-linecap='round' "
    + "stroke-linejoin='round'%3E"
    + "%3Crect x='3' y='2' width='18' height='20' rx='2.5'/%3E"
    + "%3Cline x1='7' y1='8' x2='17' y2='8'/%3E"
    + "%3Cline x1='7' y1='12' x2='17' y2='12'/%3E"
    + "%3Cline x1='7' y1='16' x2='13' y2='16'/%3E"
    + "%3C/svg%3E";
var prefixHints = [];
var activeModifier = null;  // "alt", "ctrl", "shift" or null
var qlPreviewOpen = false;  // Shift-toggle Quick Look preview
var _shiftAlone = false;    // true when Shift pressed without other keys
var _shiftDownTime = 0;
var inHistoryMode = false;
var _settingHistoryValue = false;

// --- Virtual scrolling ---
var ITEM_HEIGHT = 0;  // measured from first rendered row
var ITEM_HEIGHT_DEFAULT = 45;  // fallback before measurement
var BUFFER_COUNT = 5;  // extra rows above/below viewport

// --- DOM ---
var searchInput = document.getElementById('search-input');
var resultList = document.getElementById('result-list');
var emptyState = document.getElementById('empty-state');
var previewPanel = document.getElementById('preview-panel');
var footerLeft = document.getElementById('footer-left');
var footerRight = document.getElementById('footer-right');
var createGroup = document.getElementById('create-group');
var createBtn = document.getElementById('create-btn');

// --- Helpers ---
function post(type, data) {
    window.webkit.messageHandlers.chooser.postMessage(
        Object.assign({ type: type }, data || {})
    );
}

// --- Placeholder mask: inject once into a <style> element ---
var _phStyle = document.createElement('style');
_phStyle.textContent = '.icon-placeholder{-webkit-mask-image:url("'
    + _PLACEHOLDER_SVG + '")}';
document.head.appendChild(_phStyle);

// --- Rendering (innerHTML-based for speed) ---

function _escHtml(s) {
    // Minimal HTML escaping for user text in innerHTML templates
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
            .replace(/"/g,'&quot;');
}

function _buildRowHtml(item, i) {
    var cls = 'result-item' + (i === selectedIndex ? ' selected' : '');
    var h = ITEM_HEIGHT || ITEM_HEIGHT_DEFAULT;
    var parts = ['<div class="', cls,
        '" style="position:absolute;top:', (i * h),
        'px;left:0;right:0;height:', h, 'px" data-idx="', i, '">'];

    if (item.icon) {
        parts.push('<img class="icon" draggable="false" src="',
                   _escHtml(item.icon), '">');
    } else if (hasAnyIcon) {
        parts.push('<div class="icon icon-placeholder"></div>');
    }

    parts.push('<div class="left"><div class="title">',
               _escHtml(item.title), '</div>');
    if (item.subtitle) {
        parts.push('<div class="subtitle-text">',
                   _escHtml(item.subtitle), '</div>');
    }
    parts.push('</div><div class="right-group">');

    if (item.badge) {
        parts.push('<span class="badge">', _escHtml(item.badge), '</span>');
    }
    if (i < 9) {
        parts.push('<span class="shortcut">\u2318', (i + 1), '</span>');
    }
    if (item.deletable) {
        parts.push('<button class="delete-btn">\u00d7</button>');
    }
    parts.push('</div></div>');
    return parts.join('');
}

var _spacer = null;  // reuse spacer element across renders

function renderItems() {
    if (_scrollRafId) {
        cancelAnimationFrame(_scrollRafId);
        _scrollRafId = null;
    }
    if (items.length === 0) {
        if (_spacer) { _spacer.innerHTML = ''; _spacer.style.height = '0'; }
        resultList.style.display = 'none';
        emptyState.style.display = 'flex';
        emptyState.textContent = searchInput.value.trim()
            ? 'No results' : 'Type to search';
        setPreview(null);
        return;
    }
    resultList.style.display = '';
    emptyState.style.display = 'none';

    // Measure row height once
    if (!ITEM_HEIGHT && items.length > 0) {
        if (!_spacer) {
            _spacer = document.createElement('div');
            _spacer.style.position = 'relative';
            resultList.appendChild(_spacer);
        }
        _spacer.innerHTML = _buildRowHtml(items[0], 0);
        ITEM_HEIGHT = _spacer.firstChild.offsetHeight || ITEM_HEIGHT_DEFAULT;
        _spacer.innerHTML = '';
    }

    // Create or reuse spacer
    if (!_spacer) {
        _spacer = document.createElement('div');
        _spacer.style.position = 'relative';
        resultList.appendChild(_spacer);
    }
    _spacer.style.height = (items.length * (ITEM_HEIGHT || ITEM_HEIGHT_DEFAULT)) + 'px';

    _renderVisibleRows();
    updatePreview();
}

function _renderVisibleRows() {
    if (!_spacer) return;

    var h = ITEM_HEIGHT || ITEM_HEIGHT_DEFAULT;
    var scrollTop = resultList.scrollTop;
    var viewportHeight = resultList.clientHeight;

    var startIdx = Math.max(0, Math.floor(scrollTop / h) - BUFFER_COUNT);
    var endIdx = Math.min(items.length,
        Math.ceil((scrollTop + viewportHeight) / h) + BUFFER_COUNT);

    var htmlParts = [];
    for (var i = startIdx; i < endIdx; i++) {
        htmlParts.push(_buildRowHtml(items[i], i));
    }
    _spacer.innerHTML = htmlParts.join('');
}

// --- Event delegation on resultList (replaces per-row listeners) ---
resultList.addEventListener('mousemove', function(e) {
    if (e.clientX === _lastMouseX && e.clientY === _lastMouseY) return;
    _lastMouseX = e.clientX;
    _lastMouseY = e.clientY;
    var row = e.target.closest('.result-item');
    if (!row) return;
    var idx = parseInt(row.getAttribute('data-idx'), 10);
    if (isNaN(idx) || idx === selectedIndex) return;
    // Swap selected class without full re-render
    var prev = _spacer.querySelector('.result-item.selected');
    if (prev) prev.className = 'result-item';
    row.className = 'result-item selected';
    selectedIndex = idx;
    updatePreview();
}, false);

resultList.addEventListener('click', function(e) {
    // Delete button
    if (e.target.classList.contains('delete-btn')) {
        e.stopPropagation();
        var row = e.target.closest('.result-item');
        if (row) {
            var idx = parseInt(row.getAttribute('data-idx'), 10);
            if (!isNaN(idx)) {
                if (items[idx] && items[idx].confirmDelete) {
                    triggerDelete(idx, e.target);
                } else {
                    post('deleteItem', { index: idx, version: itemsVersion });
                }
            }
        }
        return;
    }
    // Row click
    var row = e.target.closest('.result-item');
    if (row) {
        var idx = parseInt(row.getAttribute('data-idx'), 10);
        if (!isNaN(idx)) {
            selectedIndex = idx;
            post('execute', { index: idx, version: itemsVersion });
        }
    }
}, false);

function _findRenderedRow(index) {
    var spacer = resultList.firstChild;
    if (!spacer) return null;
    var h = ITEM_HEIGHT || ITEM_HEIGHT_DEFAULT;
    var expectedTop = (index * h) + 'px';
    for (var c = 0; c < spacer.childNodes.length; c++) {
        if (spacer.childNodes[c].style.top === expectedTop) {
            return spacer.childNodes[c];
        }
    }
    return null;
}

// Virtual scroll handler
var _scrollRafId = null;
resultList.addEventListener('scroll', function() {
    if (_scrollRafId) return;
    _scrollRafId = requestAnimationFrame(function() {
        _scrollRafId = null;
        _renderVisibleRows();
    });
});

function updatePreview() {
    if (selectedIndex >= 0 && selectedIndex < items.length) {
        var item = items[selectedIndex];
        if (item.preview) {
            setPreview(item.preview);
        } else {
            post('requestPreview', { index: selectedIndex });
        }
    } else {
        setPreview(null);
    }
}

function setPreview(data) {
    if (!data) {
        previewPanel.className = 'preview-panel empty';
        previewPanel.textContent = 'Select an item to preview';
        return;
    }
    previewPanel.className = 'preview-panel';
    previewPanel.innerHTML = '';

    if (data.type === 'text') {
        var textDiv = document.createElement('div');
        textDiv.className = 'preview-text';
        textDiv.textContent = data.content || '';
        previewPanel.appendChild(textDiv);
    } else if (data.type === 'image') {
        var wrapper = document.createElement('div');
        wrapper.className = 'preview-image-wrapper';
        var img = document.createElement('img');
        img.src = data.src || '';
        wrapper.appendChild(img);
        if (data.info) {
            var info = document.createElement('div');
            info.className = 'preview-image-info';
            info.textContent = data.info;
            wrapper.appendChild(info);
        }
        previewPanel.appendChild(wrapper);
    } else if (data.type === 'path') {
        var pathDiv = document.createElement('div');
        pathDiv.className = 'preview-text';
        pathDiv.textContent = data.content || '';
        previewPanel.appendChild(pathDiv);
    }
}

function updateSelection(newIndex) {
    if (items.length === 0) return;
    selectedIndex = Math.max(0, Math.min(newIndex, items.length - 1));
    // Scroll selected item into view
    var h = ITEM_HEIGHT || ITEM_HEIGHT_DEFAULT;
    var itemTop = selectedIndex * h;
    var itemBottom = itemTop + h;
    var scrollTop = resultList.scrollTop;
    var viewportHeight = resultList.clientHeight;
    if (itemTop < scrollTop) {
        resultList.scrollTop = itemTop;
    } else if (itemBottom > scrollTop + viewportHeight) {
        resultList.scrollTop = itemBottom - viewportHeight;
    }
    _renderVisibleRows();
    updatePreview();
    if (qlPreviewOpen) {
        post('qlNavigate', { index: selectedIndex });
    }
}

// --- Panel resize (collapsed ↔ expanded) ---
var _panelExpanded = false;

function _checkPanelResize() {
    var shouldExpand = searchInput.value.length > 0;
    if (shouldExpand !== _panelExpanded) {
        _panelExpanded = shouldExpand;
        post('panelResize', { expanded: shouldExpand });
    }
}

// --- Input handling (with debounce for longer queries) ---
var _debounceTimer = null;
searchInput.addEventListener('input', function() {
    if (_settingHistoryValue) return;
    if (inHistoryMode) {
        inHistoryMode = false;
        post('exitHistory');
    }
    _checkPanelResize();
    var query = searchInput.value;
    if (_debounceTimer) { clearTimeout(_debounceTimer); _debounceTimer = null; }
    // Short queries (<=3 chars): search immediately (prefix activation like "f ")
    if (query.length <= 3) {
        post('search', { query: query });
    } else {
        _debounceTimer = setTimeout(function() {
            _debounceTimer = null;
            post('search', { query: query });
        }, 150);
    }
});

// --- Keyboard navigation ---
document.addEventListener('keydown', function(e) {
    // Discard leading space when input is empty
    if (e.key === ' ' && searchInput.value === '') {
        e.preventDefault();
        return;
    }
    if (e.key === 'Escape') {
        e.preventDefault();
        if (qlPreviewOpen) {
            qlPreviewOpen = false;
            post('shiftPreview', { open: false, index: selectedIndex });
        }
        post('close');
        return;
    }
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (inHistoryMode) {
            post('historyDown');
        } else {
            updateSelection(selectedIndex + 1);
        }
        return;
    }
    if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (inHistoryMode || (searchInput.value === '' && items.length === 0)) {
            post('historyUp');
        } else {
            updateSelection(selectedIndex - 1);
        }
        return;
    }
    if (e.key === 'Tab') {
        e.preventDefault();
        if (selectedIndex >= 0 && selectedIndex < items.length) {
            post('tab', { index: selectedIndex });
        }
        return;
    }
    if (e.key === 'Enter') {
        e.preventDefault();
        inHistoryMode = false;
        if (selectedIndex >= 0 && selectedIndex < items.length) {
            if (e.metaKey) {
                post('reveal', {
                    index: selectedIndex, version: itemsVersion
                });
            } else {
                var mod = activeModifier;
                var msg = {
                    index: selectedIndex, version: itemsVersion
                };
                if (mod) msg.modifier = mod;
                post('execute', msg);
            }
        }
        return;
    }
    // Cmd+, : open Settings
    if (e.metaKey && e.key === ',') {
        e.preventDefault();
        post('openSettings');
        return;
    }
    // Cmd+1 through Cmd+9: quick select and execute
    if (e.metaKey && e.key >= '1' && e.key <= '9') {
        var idx = parseInt(e.key) - 1;
        if (idx < items.length) {
            e.preventDefault();
            selectedIndex = idx;
            renderItems();
            post('execute', { index: idx, version: itemsVersion });
        }
        return;
    }
    // Cmd+N: create new item (when create button is visible)
    if (e.metaKey && (e.key === 'n' || e.key === 'N') && !e.shiftKey) {
        if (createGroup.classList.contains('visible')) {
            e.preventDefault();
            createBtn.click();
        }
        return;
    }
    // Delete/Backspace: delete selected item
    if ((e.key === 'Delete' || e.key === 'Backspace') && e.metaKey) {
        if (selectedIndex >= 0 && selectedIndex < items.length
            && items[selectedIndex].deletable) {
            e.preventDefault();
            if (items[selectedIndex].confirmDelete) {
                var row = _findRenderedRow(selectedIndex);
                if (row) {
                    var btn = row.querySelector('.delete-btn');
                    if (btn) triggerDelete(selectedIndex, btn);
                }
            } else {
                post('deleteItem', {
                    index: selectedIndex, version: itemsVersion
                });
            }
        }
        return;
    }
});

// --- Modifier key tracking ---
function getModifierName(e) {
    if (e.altKey && !e.metaKey && !e.ctrlKey) return 'alt';
    if (e.ctrlKey && !e.metaKey && !e.altKey) return 'ctrl';
    if (e.shiftKey && !e.metaKey && !e.altKey && !e.ctrlKey) return 'shift';
    return null;
}

document.addEventListener('keydown', function(e) {
    // Track Shift-alone for Quick Look toggle
    if (e.key === 'Shift' && !e.metaKey && !e.altKey && !e.ctrlKey) {
        _shiftAlone = true;
        _shiftDownTime = Date.now();
    } else if (e.key !== 'Shift') {
        _shiftAlone = false;
    }

    if (e.key === 'Alt' || e.key === 'Control' || e.key === 'Shift') {
        var mod = getModifierName(e);
        if (mod !== activeModifier) {
            activeModifier = mod;
            if (selectedIndex >= 0) {
                post('modifierChange', {
                    index: selectedIndex, modifier: mod
                });
            }
        }
    }
}, true);

document.addEventListener('keyup', function(e) {
    // Shift-alone tap toggles Quick Look preview
    if (e.key === 'Shift' && _shiftAlone
            && (Date.now() - _shiftDownTime < 400)) {
        _shiftAlone = false;
        qlPreviewOpen = !qlPreviewOpen;
        post('shiftPreview', { open: qlPreviewOpen, index: selectedIndex });
    }
    _shiftAlone = false;

    if (e.key === 'Alt' || e.key === 'Control' || e.key === 'Shift') {
        if (activeModifier !== null) {
            activeModifier = null;
            if (selectedIndex >= 0) {
                post('modifierChange', {
                    index: selectedIndex, modifier: null
                });
            }
        }
    }
}, true);

// --- Python -> JS API ---

function setResults(newItems, version, selectedIdx) {
    items = newItems || [];
    itemsVersion = version || 0;
    hasAnyIcon = items.some(function(it) { return !!it.icon; });
    if (typeof selectedIdx === 'number') {
        // Preserve scroll position for delete/refresh operations
        selectedIndex = Math.max(0, Math.min(selectedIdx, items.length - 1));
    } else {
        // New search results — reset to top
        selectedIndex = items.length > 0 ? 0 : -1;
        resultList.scrollTop = 0;
    }
    renderItems();
    // Ensure selected item is visible after delete
    if (typeof selectedIdx === 'number' && selectedIndex >= 0) {
        var h = ITEM_HEIGHT || ITEM_HEIGHT_DEFAULT;
        var itemTop = selectedIndex * h;
        var itemBottom = itemTop + h;
        var scrollTop = resultList.scrollTop;
        var viewportHeight = resultList.clientHeight;
        if (itemTop < scrollTop) {
            resultList.scrollTop = itemTop;
            _renderVisibleRows();
        } else if (itemBottom > scrollTop + viewportHeight) {
            resultList.scrollTop = itemBottom - viewportHeight;
            _renderVisibleRows();
        }
    }
}

function setPrefixHints(hints) {
    prefixHints = hints || [];
    if (hints.length > 0) {
        footerRight.innerHTML = hints.map(function(h) {
            var parts = h.split(' ', 2);
            return '<kbd>' + parts[0] + '</kbd> ' + (parts[1] || '');
        }).join('  ');
    } else {
        footerRight.innerHTML = '';
    }
}

function setModifierSubtitle(index, subtitle) {
    var row = _findRenderedRow(index);
    if (!row) return;
    var sub = row.querySelector('.subtitle-text');
    if (sub && subtitle !== null) {
        sub.textContent = subtitle;
    } else if (sub && subtitle === null) {
        // Restore original subtitle
        if (index < items.length) {
            sub.textContent = items[index].subtitle || '';
        }
    }
}

function setPlaceholder(text) {
    searchInput.placeholder = text || 'Search...';
}

function focusInput() {
    activeModifier = null;  // Reset stale modifier state on panel reopen
    searchInput.focus();
    searchInput.select();
}

function clearInput() {
    searchInput.value = '';
    inHistoryMode = false;
    items = [];
    selectedIndex = -1;
    renderItems();
    _checkPanelResize();
}

function setInputValue(value) {
    searchInput.value = value;
    searchInput.setSelectionRange(value.length, value.length);
    _checkPanelResize();
    post('search', { query: value });
}

function setHistoryQuery(value) {
    _settingHistoryValue = true;
    searchInput.value = value;
    searchInput.setSelectionRange(value.length, value.length);
    _settingHistoryValue = false;
    inHistoryMode = true;
    _checkPanelResize();
    post('search', { query: value });
}

function exitHistoryMode() {
    inHistoryMode = false;
}

// --- Action hints (dynamic per source) ---
function setActionHints(hints) {
    var parts = ['<kbd>\u2191\u2193</kbd> Navigate'];
    if (hints.enter) {
        parts.push('<kbd>\u21b5</kbd> ' + hints.enter);
    }
    if (hints.cmd_enter) {
        parts.push('<kbd>\u2318\u21b5</kbd> ' + hints.cmd_enter);
    }
    if (hints.alt_enter) {
        parts.push('<kbd>\u2325\u21b5</kbd> ' + hints.alt_enter);
    }
    if (hints['delete']) {
        parts.push('<kbd>\u2318\u232b</kbd> ' + hints['delete']);
    }
    if (hints.shift) {
        parts.push('<kbd>\u21e7</kbd> ' + hints.shift);
    }
    if (hints.tab) {
        parts.push('<kbd>\u21e5</kbd> ' + hints.tab);
    }
    parts.push('<kbd>Esc</kbd> Close');
    footerLeft.innerHTML = parts.join('  ');
}

function setPreviewVisible(visible) {
    var leftPanel = document.querySelector('.left-panel');
    if (visible) {
        previewPanel.classList.remove('hidden');
        leftPanel.classList.remove('full-width');
    } else {
        previewPanel.classList.add('hidden');
        leftPanel.classList.add('full-width');
    }
}

// --- Delete confirmation (two-step) ---
var _deleteConfirmTimer = null;
var _deleteConfirmIndex = -1;

function triggerDelete(index, btn) {
    if (_deleteConfirmIndex === index && btn.classList.contains('confirm')) {
        // Second click/press — actually delete
        clearDeleteConfirm();
        post('deleteItem', { index: index, version: itemsVersion });
        return;
    }
    // First click/press — enter confirm state
    clearDeleteConfirm();
    _deleteConfirmIndex = index;
    btn.classList.add('confirm');
    btn.textContent = 'Delete?';
    _deleteConfirmTimer = setTimeout(function() {
        clearDeleteConfirm();
        _renderVisibleRows();
    }, 3000);
}

function clearDeleteConfirm() {
    if (_deleteConfirmTimer) {
        clearTimeout(_deleteConfirmTimer);
        _deleteConfirmTimer = null;
    }
    _deleteConfirmIndex = -1;
}

// --- Create button ---
createBtn.addEventListener('click', function(e) {
    e.preventDefault();
    // Strip prefix from query (e.g. "sn hello" -> "hello")
    var q = searchInput.value.trim();
    var spaceIdx = q.indexOf(' ');
    var stripped = spaceIdx >= 0 ? q.substring(spaceIdx + 1).trim() : '';
    post('createItem', { query: stripped });
});

function setCreateButton(visible) {
    if (visible) {
        createGroup.classList.add('visible');
    } else {
        createGroup.classList.remove('visible');
    }
}

// --- Init ---
setActionHints({ enter: 'Open', cmd_enter: 'Reveal' });
searchInput.focus();
</script>
</body>
</html>"""
