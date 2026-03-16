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
.preview-panel {
    flex: 1; display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    border-left: 1px solid var(--border);
    padding: 16px; overflow: hidden;
    min-height: 0;
}
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
.result-item:hover { background: var(--item-hover); }
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
    width: 18px; height: 18px; flex-shrink: 0;
    border: none; border-radius: 50%;
    background: transparent; color: var(--secondary);
    font-size: 13px; line-height: 18px; text-align: center;
    cursor: pointer; opacity: 0; transition: opacity 0.15s;
    padding: 0;
}
.result-item:hover .delete-btn,
.result-item.selected .delete-btn { opacity: 0.6; }
.result-item .delete-btn:hover {
    opacity: 1; background: var(--item-hover); color: var(--text);
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
</div>

<div class="main-content">
    <div class="left-panel">
        <div class="result-list" id="result-list"></div>
        <div class="empty-state" id="empty-state" style="display:none;">
            Type to search
        </div>
    </div>
    <div class="preview-panel empty" id="preview-panel">
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
var prefixHints = [];
var activeModifier = null;  // "alt", "ctrl", "shift" or null

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

// --- Helpers ---
function post(type, data) {
    window.webkit.messageHandlers.chooser.postMessage(
        Object.assign({ type: type }, data || {})
    );
}

function renderItems() {
    // Cancel any pending scroll-triggered render
    if (_scrollRafId) {
        cancelAnimationFrame(_scrollRafId);
        _scrollRafId = null;
    }
    resultList.innerHTML = '';
    if (items.length === 0) {
        resultList.style.display = 'none';
        emptyState.style.display = 'flex';
        emptyState.textContent = searchInput.value.trim() ? 'No results' : 'Type to search';
        setPreview(null);
        return;
    }
    resultList.style.display = '';
    emptyState.style.display = 'none';

    // Measure actual row height from a sample row if not yet measured
    if (!ITEM_HEIGHT && items.length > 0) {
        var sample = _createRow(items[0], 0);
        sample.style.visibility = 'hidden';
        sample.style.position = 'absolute';
        resultList.appendChild(sample);
        ITEM_HEIGHT = sample.offsetHeight || ITEM_HEIGHT_DEFAULT;
        resultList.removeChild(sample);
    }

    var h = ITEM_HEIGHT || ITEM_HEIGHT_DEFAULT;
    // Create spacer for virtual scrolling
    var totalHeight = items.length * h;
    var spacer = document.createElement('div');
    spacer.style.height = totalHeight + 'px';
    spacer.style.position = 'relative';
    resultList.appendChild(spacer);

    _renderVisibleRows();

    // Update preview for selected item
    updatePreview();
}

function _renderVisibleRows() {
    var spacer = resultList.firstChild;
    if (!spacer) return;

    // Remove old rows from spacer
    spacer.innerHTML = '';

    var h = ITEM_HEIGHT || ITEM_HEIGHT_DEFAULT;
    var scrollTop = resultList.scrollTop;
    var viewportHeight = resultList.clientHeight;

    var startIdx = Math.max(0, Math.floor(scrollTop / h) - BUFFER_COUNT);
    var endIdx = Math.min(items.length, Math.ceil((scrollTop + viewportHeight) / h) + BUFFER_COUNT);

    var frag = document.createDocumentFragment();
    for (var i = startIdx; i < endIdx; i++) {
        var row = _createRow(items[i], i);
        row.style.position = 'absolute';
        row.style.top = (i * h) + 'px';
        row.style.left = '0';
        row.style.right = '0';
        row.style.height = h + 'px';
        frag.appendChild(row);
    }
    spacer.appendChild(frag);
}

function _createRow(item, i) {
    var row = document.createElement('div');
    row.className = 'result-item' + (i === selectedIndex ? ' selected' : '');

    if (item.icon) {
        var img = document.createElement('img');
        img.className = 'icon';
        img.src = item.icon;
        img.draggable = false;
        row.appendChild(img);
    }

    var left = document.createElement('div');
    left.className = 'left';

    var title = document.createElement('div');
    title.className = 'title';
    title.textContent = item.title;
    left.appendChild(title);

    if (item.subtitle) {
        var sub = document.createElement('div');
        sub.className = 'subtitle-text';
        sub.textContent = item.subtitle;
        left.appendChild(sub);
    }

    row.appendChild(left);

    // Right group: badge + shortcut number
    var rightGroup = document.createElement('div');
    rightGroup.className = 'right-group';

    if (item.badge) {
        var badge = document.createElement('span');
        badge.className = 'badge';
        badge.textContent = item.badge;
        rightGroup.appendChild(badge);
    }

    // Show Cmd+N shortcut for first 9 items
    if (i < 9) {
        var shortcut = document.createElement('span');
        shortcut.className = 'shortcut';
        shortcut.textContent = '\u2318' + (i + 1);
        rightGroup.appendChild(shortcut);
    }

    // Delete button for deletable items
    if (item.deletable) {
        var delBtn = document.createElement('button');
        delBtn.className = 'delete-btn';
        delBtn.textContent = '\u00d7';
        delBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            post('deleteItem', { index: i, version: itemsVersion });
        });
        rightGroup.appendChild(delBtn);
    }

    row.appendChild(rightGroup);

    row.addEventListener('click', function() {
        selectedIndex = i;
        _renderVisibleRows();
        post('execute', { index: i, version: itemsVersion });
    });

    return row;
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
            // Request preview from Python (for lazy-loaded content)
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
}

// --- Input handling (with debounce for longer queries) ---
var _debounceTimer = null;
searchInput.addEventListener('input', function() {
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
    if (e.key === 'Escape') {
        e.preventDefault();
        post('close');
        return;
    }
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        updateSelection(selectedIndex + 1);
        return;
    }
    if (e.key === 'ArrowUp') {
        e.preventDefault();
        updateSelection(selectedIndex - 1);
        return;
    }
    if (e.key === 'Enter') {
        e.preventDefault();
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
    // Cmd+1 through Cmd+9: quick select
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
});

// --- Modifier key tracking ---
function getModifierName(e) {
    if (e.altKey && !e.metaKey && !e.ctrlKey) return 'alt';
    if (e.ctrlKey && !e.metaKey && !e.altKey) return 'ctrl';
    if (e.shiftKey && !e.metaKey && !e.altKey && !e.ctrlKey) return 'shift';
    return null;
}

document.addEventListener('keydown', function(e) {
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
        footerRight.textContent = hints.join('  ');
    } else {
        footerRight.textContent = '';
    }
    // Update placeholder with prefix hints
    if (hints.length > 0) {
        searchInput.placeholder = 'Search...  (' + hints.join(', ') + ')';
    } else {
        searchInput.placeholder = 'Search...';
    }
}

function setModifierSubtitle(index, subtitle) {
    // Find the rendered row for this index in the virtual list
    var spacer = resultList.firstChild;
    if (!spacer) return;
    var h = ITEM_HEIGHT || ITEM_HEIGHT_DEFAULT;
    var expectedTop = (index * h) + 'px';
    var row = null;
    for (var c = 0; c < spacer.childNodes.length; c++) {
        if (spacer.childNodes[c].style.top === expectedTop) {
            row = spacer.childNodes[c];
            break;
        }
    }
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
    items = [];
    selectedIndex = -1;
    renderItems();
}

function setInputValue(value) {
    searchInput.value = value;
    searchInput.setSelectionRange(value.length, value.length);
    post('search', { query: value });
}

// --- Init ---
footerLeft.innerHTML =
    '<kbd>\u2191\u2193</kbd> Navigate ' +
    '<kbd>\u21b5</kbd> Open ' +
    '<kbd>\u2318\u21b5</kbd> Reveal ' +
    '<kbd>\u23181-9</kbd> Select ' +
    '<kbd>Esc</kbd> Close';
searchInput.focus();
</script>
</body>
</html>"""
