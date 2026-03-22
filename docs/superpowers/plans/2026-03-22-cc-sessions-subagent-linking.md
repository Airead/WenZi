# CC-Sessions Subagent Linking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add clickable links in the cc-sessions viewer to navigate from parent sessions into subagent sessions via independent viewer panels.

**Architecture:** Subagent JSONL files live at `{session_id}/subagents/agent-{agentId}.jsonl` under the parent session directory. The viewer.html JS extracts agentIds from Agent tool_result text before rendering, checks file existence via Python bridge, then renders clickable links in both the Stats card and Agent tool blocks. Python side provides bridge handlers to check existence, open subagent panels, and close them.

**Tech Stack:** JavaScript (viewer.html WKWebView), Python (init_plugin.py bridge handlers), pytest

**Spec:** `docs/superpowers/specs/2026-03-22-cc-sessions-subagent-linking-design.md`

---

### Task 1: Python bridge — subagent path resolution and existence check

**Files:**
- Modify: `plugins/cc_sessions/init_plugin.py:83-145`
- Create: `tests/plugins/test_cc_sessions_subagent.py`

- [ ] **Step 1: Write tests for subagent path resolution and existence check**

```python
"""Tests for cc-sessions subagent bridge helpers."""

import json
import os
from pathlib import Path


def _make_subagent_fixture(tmp_path):
    """Create a parent session with subagent files for testing."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Parent session JSONL
    parent_jsonl = project_dir / "aaa-bbb-ccc.jsonl"
    parent_jsonl.write_text("")

    # Subagent directory and files
    subagents_dir = project_dir / "aaa-bbb-ccc" / "subagents"
    subagents_dir.mkdir(parents=True)

    agent1_jsonl = subagents_dir / "agent-abc123def.jsonl"
    agent1_jsonl.write_text(json.dumps({
        "type": "user",
        "agentId": "abc123def",
        "message": {"role": "user", "content": "test prompt"},
    }) + "\n")

    agent1_meta = subagents_dir / "agent-abc123def.meta.json"
    agent1_meta.write_text(json.dumps({"agentType": "Explore"}))

    return {
        "parent_path": str(parent_jsonl),
        "agent1_id": "abc123def",
        "agent1_path": str(agent1_jsonl),
    }


class TestResolveSubagentPath:
    def test_resolves_correct_path(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _resolve_subagent_path

        fix = _make_subagent_fixture(tmp_path)
        result = _resolve_subagent_path(fix["parent_path"], "abc123def")
        assert result == fix["agent1_path"]

    def test_nonexistent_agent_id(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _resolve_subagent_path

        fix = _make_subagent_fixture(tmp_path)
        result = _resolve_subagent_path(fix["parent_path"], "nonexistent")
        expected = os.path.join(
            os.path.dirname(fix["parent_path"]),
            "aaa-bbb-ccc", "subagents", "agent-nonexistent.jsonl",
        )
        assert result == expected


class TestCheckSubagentExists:
    def test_existing_and_missing(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _check_subagent_exists

        fix = _make_subagent_fixture(tmp_path)
        result = _check_subagent_exists(
            fix["parent_path"], ["abc123def", "missing999"]
        )
        assert result == {"abc123def": True, "missing999": False}

    def test_empty_list(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _check_subagent_exists

        fix = _make_subagent_fixture(tmp_path)
        result = _check_subagent_exists(fix["parent_path"], [])
        assert result == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/plugins/test_cc_sessions_subagent.py -v`
Expected: FAIL with `ImportError` (functions don't exist yet)

- [ ] **Step 3: Implement path resolution and existence check functions**

First, add `import json` to the imports section of `plugins/cc_sessions/init_plugin.py` (after `import os`).

Then add two functions at module level (before the `register` function, after the imports):

```python
def _resolve_subagent_path(root_session_path: str, agent_id: str) -> str:
    """Resolve subagent JSONL path from root session path and agent ID."""
    root_dir = os.path.dirname(root_session_path)
    session_id = os.path.splitext(os.path.basename(root_session_path))[0]
    return os.path.join(
        root_dir, session_id, "subagents", f"agent-{agent_id}.jsonl"
    )


def _check_subagent_exists(
    root_session_path: str, agent_ids: list,
) -> dict:
    """Check which subagent JSONL files exist on disk."""
    return {
        aid: os.path.isfile(_resolve_subagent_path(root_session_path, aid))
        for aid in agent_ids
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/plugins/test_cc_sessions_subagent.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add plugins/cc_sessions/init_plugin.py tests/plugins/test_cc_sessions_subagent.py
git commit -m "feat(cc-sessions): add subagent path resolution and existence check"
```

---

### Task 2: Python bridge — extend `get_session_info` with `root_session_path`

**Files:**
- Modify: `plugins/cc_sessions/init_plugin.py:127-136`

- [ ] **Step 1: Extend `get_session_info` return value in `_open_viewer`**

In the existing `_open_viewer` function, modify the `get_session_info` handler to include `root_session_path` and `is_subagent`:

```python
@panel.handle("get_session_info")
def get_session_info(_data):
    return {
        "file": session["file_path"],
        "project": session["project"],
        "cwd": session["cwd"],
        "session_id": session["session_id"],
        "git_branch": session.get("git_branch", ""),
        "version": session.get("version", ""),
        "root_session_path": session["file_path"],
        "is_subagent": False,
    }
```

- [ ] **Step 2: Register `check_subagent_exists` bridge handler on the panel**

Inside `_open_viewer`, after the existing `get_session_info` handler, add:

```python
@panel.handle("check_subagent_exists")
def check_subagent_exists(data):
    root_path = data.get("root_session_path", "")
    agent_ids = data.get("agent_ids", [])
    return _check_subagent_exists(root_path, agent_ids)
```

- [ ] **Step 3: Verify existing tests still pass**

Run: `uv run pytest tests/plugins/test_cc_sessions_search.py tests/plugins/test_cc_sessions_subagent.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add plugins/cc_sessions/init_plugin.py
git commit -m "feat(cc-sessions): extend get_session_info with root_session_path"
```

---

### Task 3: Python bridge — `open_subagent` handler

**Files:**
- Modify: `plugins/cc_sessions/init_plugin.py`
- Modify: `tests/plugins/test_cc_sessions_subagent.py`

- [ ] **Step 1: Write test for subagent metadata extraction**

The `open_subagent` handler needs to extract basic metadata (cwd, version, etc.) from the subagent JSONL. Add a helper and its test:

```python
class TestParseSubagentMeta:
    def test_extracts_basic_fields(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _parse_subagent_meta

        jsonl_path = tmp_path / "agent-test.jsonl"
        lines = [
            json.dumps({"type": "user", "agentId": "abc", "cwd": "/work/project",
                         "version": "2.1.81", "message": {"role": "user", "content": "do stuff"}}),
            json.dumps({"type": "assistant", "agentId": "abc",
                         "message": {"role": "assistant", "model": "claude-haiku-4-5-20251001",
                                     "content": [{"type": "text", "text": "ok"}]}}),
        ]
        jsonl_path.write_text("\n".join(lines) + "\n")

        meta = _parse_subagent_meta(str(jsonl_path))
        assert meta["cwd"] == "/work/project"
        assert meta["version"] == "2.1.81"

    def test_missing_fields_return_defaults(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _parse_subagent_meta

        jsonl_path = tmp_path / "agent-test.jsonl"
        jsonl_path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")

        meta = _parse_subagent_meta(str(jsonl_path))
        assert meta["cwd"] == ""
        assert meta["version"] == ""

    def test_nonexistent_file(self):
        from plugins.cc_sessions.init_plugin import _parse_subagent_meta

        meta = _parse_subagent_meta("/nonexistent/path.jsonl")
        assert meta["cwd"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/plugins/test_cc_sessions_subagent.py::TestParseSubagentMeta -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `_parse_subagent_meta`**

Add in `init_plugin.py` after `_check_subagent_exists`:

```python
def _parse_subagent_meta(jsonl_path: str) -> Dict[str, str]:
    """Extract basic metadata from the first few lines of a subagent JSONL."""
    meta: dict = {"cwd": "", "version": "", "git_branch": "", "project": ""}
    try:
        with open(jsonl_path) as f:
            for i, line in enumerate(f):
                if i >= 20:
                    break
                try:
                    msg = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not meta["cwd"] and msg.get("cwd"):
                    meta["cwd"] = msg["cwd"]
                if not meta["version"] and msg.get("version"):
                    meta["version"] = msg["version"]
                if not meta["git_branch"] and msg.get("git_branch"):
                    meta["git_branch"] = msg["git_branch"]
    except OSError:
        pass
    return meta
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/plugins/test_cc_sessions_subagent.py -v`
Expected: All PASS

- [ ] **Step 5: Implement `_open_subagent_viewer` and register bridge handler**

Inside `register()`, after the existing `_open_viewer` function, add:

```python
def _open_subagent_viewer(
    root_session_path: str,
    parent_file_path: str,
    agent_id: str,
    description: str,
) -> None:
    """Open a viewer panel for a subagent session."""
    subagent_path = _resolve_subagent_path(root_session_path, agent_id)
    if not os.path.isfile(subagent_path):
        logger.warning("Subagent file not found: %s", subagent_path)
        return

    meta = _parse_subagent_meta(subagent_path)
    session_id = f"agent-{agent_id}"

    panel = wz.ui.webview_panel(
        title=f"Subagent: {description}",
        html_file=viewer_html_path,
        width=900,
        height=700,
        resizable=True,
        allowed_read_paths=[
            os.path.expanduser("~/.claude/"),
        ],
    )

    @panel.handle("get_session_info")
    def get_session_info(_data):
        return {
            "file": subagent_path,
            "project": meta.get("project", ""),
            "cwd": meta.get("cwd", ""),
            "session_id": session_id,
            "git_branch": meta.get("git_branch", ""),
            "version": meta.get("version", ""),
            "root_session_path": root_session_path,
            "parent_file_path": parent_file_path,
            "is_subagent": True,
        }

    @panel.handle("check_subagent_exists")
    def check_subagent_exists(data):
        root_path = data.get("root_session_path", "")
        agent_ids = data.get("agent_ids", [])
        return _check_subagent_exists(root_path, agent_ids)

    @panel.handle("open_subagent")
    def open_subagent(data):
        _open_subagent_viewer(
            data.get("root_session_path", ""),
            data.get("parent_file_path", ""),
            data.get("agent_id", ""),
            data.get("description", ""),
        )

    @panel.handle("open_parent_session")
    def open_parent(_data):
        panel.close()

    def _copy_text(text: str) -> None:
        from wenzi.scripting.sources import copy_to_clipboard
        copy_to_clipboard(text)

    panel.on("copy_resume", lambda data: _copy_text(data.get("text", "")))
    panel.show()
```

Also register the `open_subagent` bridge handler on the **existing** `_open_viewer` panel. Add these lines inside `_open_viewer`, after the `panel.on("copy_resume", ...)` line:

```python
@panel.handle("check_subagent_exists")
def check_subagent_exists(data):
    root_path = data.get("root_session_path", "")
    agent_ids = data.get("agent_ids", [])
    return _check_subagent_exists(root_path, agent_ids)

@panel.handle("open_subagent")
def open_subagent(data):
    _open_subagent_viewer(
        data.get("root_session_path", ""),
        data.get("parent_file_path", ""),
        data.get("agent_id", ""),
        data.get("description", ""),
    )
```

- [ ] **Step 6: Run all tests**

Run: `uv run pytest tests/plugins/test_cc_sessions_subagent.py tests/plugins/test_cc_sessions_search.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add plugins/cc_sessions/init_plugin.py tests/plugins/test_cc_sessions_subagent.py
git commit -m "feat(cc-sessions): add open_subagent bridge handler and metadata parser"
```

---

### Task 4: viewer.html — `resolveSubagents()` pre-resolution function

**Files:**
- Modify: `plugins/cc_sessions/viewer.html:646-709`

- [ ] **Step 1: Add the `resolveSubagents` function**

Add after the state variables block (after line 649, before `document.addEventListener`):

```javascript
// ── Subagent resolution map ──
// Populated by resolveSubagents() before rendering.
// Keys: tool_use_id of Agent calls. Values: { agentId, description, type, model, exists }
window._subagentMap = {};

async function resolveSubagents(messages, info) {
  window._subagentMap = {};

  // Build tool_result map from user messages
  const resultMap = {};
  for (const msg of messages) {
    if (msg.type !== "user") continue;
    const content = msg.message?.content;
    if (!Array.isArray(content)) continue;
    for (const part of content) {
      if (part.type === "tool_result" && part.tool_use_id) {
        resultMap[part.tool_use_id] = part;
      }
    }
  }

  // Find Agent tool_use calls and extract agentIds from results
  const agentCalls = [];
  for (const msg of messages) {
    if (msg.type !== "assistant") continue;
    const content = msg.message?.content;
    if (!Array.isArray(content)) continue;
    for (const part of content) {
      if (part.type === "tool_use" && part.name === "Agent") {
        const result = resultMap[part.id];
        let agentId = null;
        if (result) {
          const text = extractResultText(result);
          const match = text.match(/agentId:\s*([a-fA-F0-9]+)/);
          if (match) agentId = match[1];
        }
        agentCalls.push({
          toolUseId: part.id,
          agentId,
          description: part.input?.description || "",
          type: part.input?.subagent_type || "general-purpose",
          model: part.input?.model || "",
        });
      }
    }
  }

  if (agentCalls.length === 0) return;

  // Batch check file existence
  const idsToCheck = agentCalls.filter(a => a.agentId).map(a => a.agentId);
  let existsMap = {};
  if (idsToCheck.length > 0 && typeof wz !== "undefined" && wz.call) {
    try {
      existsMap = await wz.call("check_subagent_exists", {
        root_session_path: info.root_session_path,
        agent_ids: idsToCheck,
      });
    } catch (err) {
      console.error("check_subagent_exists failed:", err);
    }
  }

  // Populate global map
  for (const ac of agentCalls) {
    window._subagentMap[ac.toolUseId] = {
      agentId: ac.agentId,
      description: ac.description,
      type: ac.type,
      model: ac.model,
      exists: ac.agentId ? (existsMap[ac.agentId] === true) : false,
    };
  }
}
```

Note: `extractResultText` is a helper that extracts text from a tool_result. It already exists as inline logic in `formatToolResult` (line 1026-1038). Extract it as a reusable function:

```javascript
function extractResultText(result) {
  if (!result) return "";
  const c = result.content;
  if (typeof c === "string") return c;
  if (Array.isArray(c)) {
    return c.filter(p => p.type === "text").map(p => p.text || "").join("\n");
  }
  return "";
}
```

Then update `formatToolResult` to use it:

```javascript
function formatToolResult(result) {
  return truncate(extractResultText(result), 2000);
}
```

- [ ] **Step 2: Update `loadSession` to call `resolveSubagents` before rendering**

Modify `loadSession` (around line 703-708). Change:

```javascript
  const messages = text.trim().split("\n").map(line => {
    try { return JSON.parse(line); } catch { return null; }
  }).filter(Boolean);

  renderConversation(messages);
  renderStats(messages);
```

To:

```javascript
  const messages = text.trim().split("\n").map(line => {
    try { return JSON.parse(line); } catch { return null; }
  }).filter(Boolean);

  await resolveSubagents(messages, info);
  renderConversation(messages);
  renderStats(messages);
```

- [ ] **Step 3: Manually test with a real session**

Open WenZi, launch a session that has Agent tool calls. Open browser console (if possible) or add `console.log` statements to verify:
- `window._subagentMap` is populated with correct agentIds
- `exists` flags are correct

- [ ] **Step 4: Commit**

```bash
git add plugins/cc_sessions/viewer.html
git commit -m "feat(cc-sessions): add resolveSubagents pre-resolution in viewer"
```

---

### Task 5: viewer.html — Info bar changes (parent link + hide resume)

**Files:**
- Modify: `plugins/cc_sessions/viewer.html:515-530` (HTML), `plugins/cc_sessions/viewer.html:667-682` (JS)

- [ ] **Step 1: Add parent link element in HTML info bar**

Modify the info-bar HTML (line 516). Add a hidden parent-link element at the start:

```html
<div class="info-bar" id="info-bar">
    <a class="parent-link" id="parent-link" style="display:none;" href="#">&#8592; Parent Session</a>
    <span class="label">Project:</span>
```

- [ ] **Step 2: Add CSS for parent-link**

Add after the `.copy-btn:hover` rule (around line 115):

```css
.parent-link {
    color: var(--copy-btn-color);
    text-decoration: none;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    padding: 2px 8px;
    border-radius: 4px;
    margin-right: 4px;
}
.parent-link:hover {
    background: var(--copy-btn-bg);
}
```

- [ ] **Step 3: Update `loadSession` JS to show/hide parent link and resume button**

In `loadSession`, after the info bar updates (around line 669-682), add conditional logic:

```javascript
// Subagent mode: show parent link, hide resume button
if (info.is_subagent) {
    const parentLink = document.getElementById("parent-link");
    parentLink.style.display = "";
    parentLink.addEventListener("click", (e) => {
        e.preventDefault();
        wz.call("open_parent_session", {
            parent_file_path: info.parent_file_path,
        });
    });

    document.getElementById("btn-copy-resume").style.display = "none";
} else {
    // Setup copy-resume button (existing code)
    document.getElementById("btn-copy-resume").addEventListener("click", () => {
        // ... existing code unchanged ...
    });
}
```

Move the existing resume button setup into the `else` branch.

- [ ] **Step 4: Manually test**

Open a subagent session to verify:
- "← Parent Session" link appears
- "Copy Resume Command" button is hidden
- Clicking parent link closes the subagent panel

- [ ] **Step 5: Commit**

```bash
git add plugins/cc_sessions/viewer.html
git commit -m "feat(cc-sessions): add parent link and hide resume in subagent mode"
```

---

### Task 6: viewer.html — Agent tool block `[View Session]` button

**Files:**
- Modify: `plugins/cc_sessions/viewer.html:972-1004` (`createToolSingle` function)

- [ ] **Step 1: Add CSS for view-session button**

Add after the `.parent-link:hover` rule:

```css
.view-session-btn {
    font-size: 10px;
    color: var(--copy-btn-color);
    background: var(--copy-btn-bg);
    border: none;
    border-radius: 3px;
    padding: 1px 6px;
    cursor: pointer;
    margin-left: auto;
    margin-right: 4px;
    font-family: inherit;
    white-space: nowrap;
}
.view-session-btn:hover {
    opacity: 0.85;
}
```

- [ ] **Step 2: Modify `createToolSingle` to add View Session button for Agent calls**

In `createToolSingle` (line 972), after building `headerEl.innerHTML` (line 983-988), add:

```javascript
// Add "View Session" button for Agent tool calls with resolved subagent
if (call.name === "Agent" && call.id) {
    const sub = window._subagentMap[call.id];
    if (sub && sub.exists) {
        const viewBtn = document.createElement("button");
        viewBtn.className = "view-session-btn";
        viewBtn.textContent = "View Session";
        viewBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            wz.call("open_subagent", {
                root_session_path: sessionInfo.root_session_path,
                parent_file_path: sessionInfo.file,
                agent_id: sub.agentId,
                description: sub.description,
            });
        });
        headerEl.appendChild(viewBtn);
    }
}
```

Note: The `headerEl` uses `innerHTML` which creates child nodes, then we append the button after. The arrow `<span>` is already inside. We need the button to appear before the arrow. Adjust approach: insert the button before the arrow span:

```javascript
if (call.name === "Agent" && call.id) {
    const sub = window._subagentMap[call.id];
    if (sub && sub.exists) {
        const viewBtn = document.createElement("button");
        viewBtn.className = "view-session-btn";
        viewBtn.textContent = "View Session";
        viewBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            wz.call("open_subagent", {
                root_session_path: sessionInfo.root_session_path,
                parent_file_path: sessionInfo.file,
                agent_id: sub.agentId,
                description: sub.description,
            });
        });
        const arrow = headerEl.querySelector(".tool-arrow");
        headerEl.insertBefore(viewBtn, arrow);
    }
}
```

- [ ] **Step 3: Manually test**

Open a parent session that has Agent tool calls with subagent files. Verify:
- `[View Session]` button appears on Agent tool blocks
- Clicking it opens a new viewer panel for the subagent
- Clicking it does NOT expand/collapse the tool block
- Agent tool blocks without matching files show no button

- [ ] **Step 4: Commit**

```bash
git add plugins/cc_sessions/viewer.html
git commit -m "feat(cc-sessions): add View Session button on Agent tool blocks"
```

---

### Task 7: viewer.html — Stats card clickable subagent links

**Files:**
- Modify: `plugins/cc_sessions/viewer.html:1163-1252` (`computeStats`), `plugins/cc_sessions/viewer.html:1316-1335` (`buildStatsHTML` subagents section)

- [ ] **Step 1: Extend `computeStats` to record `toolUseId` on subagent entries**

In `computeStats` (line 1208-1213), change the subagent push to include `toolUseId`:

```javascript
} else if (name === "Agent") {
    subagents.push({
        description: p.input?.description || "",
        type: p.input?.subagent_type || "general-purpose",
        model: p.input?.model || "",
        toolUseId: p.id,
    });
```

- [ ] **Step 2: Modify `buildStatsHTML` subagents card to render clickable links**

Replace the subagent description list section (lines 1327-1334) with:

```javascript
// List individual subagent descriptions
html += '<div style="margin-top:6px;border-top:1px solid var(--border-color);padding-top:6px;">';
for (const sa of s.subagents) {
    const sub = window._subagentMap[sa.toolUseId];
    const modelTag = sa.model ? ` <span style="opacity:0.5">(${escapeHtml(sa.model)})</span>` : "";

    if (sub && sub.exists) {
        html += `<div class="stats-list-item">
            <a href="#" class="subagent-link" data-agent-id="${escapeHtml(sub.agentId)}"
               data-description="${escapeHtml(sa.description)}"
               style="color:var(--copy-btn-color);text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;">
                ${escapeHtml(sa.description)}${modelTag}
            </a>
        </div>`;
    } else {
        html += `<div class="stats-list-item">
            <span class="name">${escapeHtml(sa.description)}${modelTag}</span>
        </div>`;
    }
}
html += '</div></div>';
```

- [ ] **Step 3: Add click handler for subagent links in stats panel**

In `renderStats`, after `panel.innerHTML = buildStatsHTML(stats);` (line 1154), add event delegation:

```javascript
// Attach click handlers for subagent links
panel.querySelectorAll(".subagent-link").forEach(link => {
    link.addEventListener("click", (e) => {
        e.preventDefault();
        const agentId = link.dataset.agentId;
        const description = link.dataset.description;
        wz.call("open_subagent", {
            root_session_path: sessionInfo.root_session_path,
            parent_file_path: sessionInfo.file,
            agent_id: agentId,
            description: description,
        });
    });
});
```

- [ ] **Step 4: Manually test**

Open a parent session with subagents. Verify:
- Stats card shows subagent descriptions as clickable links (styled with `--copy-btn-color`)
- Subagents with missing files show as plain text
- Clicking a link opens the subagent viewer

- [ ] **Step 5: Commit**

```bash
git add plugins/cc_sessions/viewer.html
git commit -m "feat(cc-sessions): add clickable subagent links in stats card"
```

---

### Task 8: Add Agent to TOOL_META for better display

**Files:**
- Modify: `plugins/cc_sessions/viewer.html:569-576`

- [ ] **Step 1: Add Agent entry to TOOL_META**

Currently Agent tool calls get the generic fallback icon. Add a dedicated entry:

```javascript
const TOOL_META = {
  Read:       { icon: "\u{1F4C4}", css: "tool-read",  mergeable: true },
  Glob:       { icon: "\u{1F4C4}", css: "tool-read",  mergeable: true },
  Grep:       { icon: "\u{1F50D}", css: "tool-grep",  mergeable: true },
  Edit:       { icon: "\u270F\uFE0F",  css: "tool-edit",  mergeable: true },
  Bash:       { icon: "\u25B6\uFE0F",  css: "tool-bash",  mergeable: false },
  Write:      { icon: "\u{1F4DD}", css: "tool-write", mergeable: false },
  Agent:      { icon: "\u{1F916}", css: "tool-other", mergeable: false },
};
```

- [ ] **Step 2: Update `toolDetail` for Agent calls**

In `toolDetail` (line 583), add a case for Agent:

```javascript
if (name === "Agent" && input.description) return truncate(input.description, 60);
```

- [ ] **Step 3: Commit**

```bash
git add plugins/cc_sessions/viewer.html
git commit -m "feat(cc-sessions): add Agent to TOOL_META for better display"
```

---

### Task 9: Final integration test and lint

**Files:**
- All modified files

- [ ] **Step 1: Run full lint**

Run: `uv run ruff check`
Expected: 0 errors

- [ ] **Step 2: Run all cc-sessions tests**

Run: `uv run pytest tests/plugins/test_cc_sessions_*.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v --cov=wenzi`
Expected: All PASS, no regressions

- [ ] **Step 4: Manual end-to-end test**

1. Open WenZi launcher, search for a session known to have Agent tool calls
2. Open the session viewer
3. Verify Stats card shows clickable subagent links
4. Verify Agent tool blocks have `[View Session]` button
5. Click a link → subagent viewer opens in new panel
6. Verify subagent viewer has "← Parent Session" link and no "Copy Resume" button
7. Click "← Parent Session" → subagent panel closes
8. Open a session WITHOUT subagents → verify no regressions (no buttons, no errors)
