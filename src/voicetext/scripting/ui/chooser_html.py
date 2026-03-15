"""HTML/CSS/JS template for the Chooser panel."""

CHOOSER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {
    --bg: rgba(245, 245, 247, 0.95);
    --text: #1d1d1f;
    --secondary: #86868b;
    --border: #d2d2d7;
    --accent: #007aff;
    --item-hover: rgba(0, 122, 255, 0.08);
    --item-selected: rgba(0, 122, 255, 0.15);
    --item-selected-text: #003d99;
    --input-bg: #ffffff;
    --shadow: rgba(0, 0, 0, 0.06);
}
@media (prefers-color-scheme: dark) {
    :root {
        --bg: rgba(30, 30, 30, 0.95);
        --text: #e5e5e7;
        --secondary: #98989d;
        --border: #48484a;
        --accent: #0a84ff;
        --item-hover: rgba(10, 132, 255, 0.12);
        --item-selected: rgba(10, 132, 255, 0.22);
        --item-selected-text: #5ac8fa;
        --input-bg: #2c2c2e;
        --shadow: rgba(0, 0, 0, 0.2);
    }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
    height: 100%; overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    background: transparent; color: var(--text);
    -webkit-user-select: none; user-select: none;
}
body { display: flex; flex-direction: column; }

/* Search bar */
.search-bar {
    display: flex; align-items: center; gap: 8px;
    padding: 10px 14px;
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

/* Source tabs */
.source-tabs {
    display: flex; gap: 0; padding: 0 14px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
}
.source-tab {
    padding: 6px 12px; font-size: 12px;
    color: var(--secondary); cursor: pointer;
    border-bottom: 2px solid transparent;
    transition: color 0.15s, border-color 0.15s;
}
.source-tab:hover { color: var(--text); }
.source-tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
}

/* Result list */
.result-list {
    flex: 1; overflow-y: auto; overflow-x: hidden;
    padding: 4px 0;
}
.result-list::-webkit-scrollbar { width: 6px; }
.result-list::-webkit-scrollbar-thumb {
    background: var(--secondary); border-radius: 3px; opacity: 0.5;
}
.result-item {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 14px; cursor: default;
    transition: background 0.1s;
    gap: 12px;
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
.result-item .badge {
    font-size: 11px; color: var(--secondary);
    flex-shrink: 0; white-space: nowrap;
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

<div class="source-tabs" id="source-tabs" style="display:none;"></div>

<div class="result-list" id="result-list"></div>

<div class="empty-state" id="empty-state" style="display:none;">
    Type to search
</div>

<div class="footer" id="footer">
    <span><kbd>↑↓</kbd> Navigate &nbsp; <kbd>⏎</kbd> Open &nbsp; <kbd>⌘⏎</kbd> Reveal &nbsp; <kbd>⎋</kbd> Close</span>
    <span id="footer-right"><kbd>⇥</kbd> Switch source</span>
</div>

<script>
// --- State ---
let items = [];
let selectedIndex = -1;
let currentSource = null;  // null = default (all non-prefix sources)
let sources = [];

// --- DOM ---
const searchInput = document.getElementById('search-input');
const resultList = document.getElementById('result-list');
const emptyState = document.getElementById('empty-state');
const sourceTabs = document.getElementById('source-tabs');

// --- Helpers ---
function post(type, data) {
    window.webkit.messageHandlers.chooser.postMessage(
        Object.assign({ type: type }, data || {})
    );
}

function renderItems() {
    resultList.innerHTML = '';
    if (items.length === 0) {
        resultList.style.display = 'none';
        emptyState.style.display = searchInput.value.trim() ? 'flex' : 'flex';
        emptyState.textContent = searchInput.value.trim() ? 'No results' : 'Type to search';
        return;
    }
    resultList.style.display = '';
    emptyState.style.display = 'none';

    items.forEach(function(item, i) {
        const row = document.createElement('div');
        row.className = 'result-item' + (i === selectedIndex ? ' selected' : '');

        const left = document.createElement('div');
        left.className = 'left';

        const title = document.createElement('div');
        title.className = 'title';
        title.textContent = item.title;
        left.appendChild(title);

        if (item.subtitle) {
            const sub = document.createElement('div');
            sub.className = 'subtitle-text';
            sub.textContent = item.subtitle;
            left.appendChild(sub);
        }

        row.appendChild(left);

        if (item.badge) {
            const badge = document.createElement('span');
            badge.className = 'badge';
            badge.textContent = item.badge;
            row.appendChild(badge);
        }

        row.addEventListener('click', function() {
            selectedIndex = i;
            renderItems();
            post('execute', { index: i });
        });

        resultList.appendChild(row);
    });

    // Scroll selected item into view
    if (selectedIndex >= 0 && selectedIndex < resultList.children.length) {
        resultList.children[selectedIndex].scrollIntoView({ block: 'nearest' });
    }
}

function updateSelection(newIndex) {
    if (items.length === 0) return;
    selectedIndex = Math.max(0, Math.min(newIndex, items.length - 1));
    renderItems();
}

function renderSourceTabs() {
    sourceTabs.innerHTML = '';
    if (sources.length <= 1) {
        sourceTabs.style.display = 'none';
        return;
    }
    sourceTabs.style.display = 'flex';
    sources.forEach(function(src) {
        const tab = document.createElement('div');
        tab.className = 'source-tab' + (src.name === currentSource ? ' active' : '');
        tab.textContent = src.label || src.name;
        tab.addEventListener('click', function() {
            switchSource(src.name);
        });
        sourceTabs.appendChild(tab);
    });
}

function switchSource(name) {
    if (name === currentSource) return;
    currentSource = name;
    renderSourceTabs();
    // Clear and re-search with current query
    post('switchSource', { source: name, query: searchInput.value });
}

function cycleSources() {
    if (sources.length <= 1) return;
    const idx = sources.findIndex(function(s) { return s.name === currentSource; });
    const next = (idx + 1) % sources.length;
    switchSource(sources[next].name);
}

// --- Input handling ---
searchInput.addEventListener('input', function() {
    const query = searchInput.value;
    post('search', { query: query });
});

// --- Keyboard navigation ---
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        e.preventDefault();
        post('close');
        return;
    }
    if (e.key === 'Tab') {
        e.preventDefault();
        cycleSources();
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
                post('reveal', { index: selectedIndex });
            } else {
                post('execute', { index: selectedIndex });
            }
        }
        return;
    }
});

// --- Python → JS API ---

function setResults(newItems) {
    items = newItems || [];
    selectedIndex = items.length > 0 ? 0 : -1;
    renderItems();
}

function setSources(srcList, active) {
    sources = srcList || [];
    currentSource = active || (sources.length > 0 ? sources[0].name : null);
    renderSourceTabs();
}

function focusInput() {
    searchInput.focus();
    searchInput.select();
}

function clearInput() {
    searchInput.value = '';
    items = [];
    selectedIndex = -1;
    renderItems();
}

// --- Init ---
searchInput.focus();
</script>
</body>
</html>"""
