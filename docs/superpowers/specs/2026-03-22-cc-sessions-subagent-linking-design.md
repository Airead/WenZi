# CC-Sessions: Subagent Session Linking

## Overview

Add clickable links in the session viewer to navigate from a parent session into subagent sessions. Each subagent session opens in an independent viewer panel with full functionality (info bar, stats, outline, conversation flow), plus a "Parent Session" link to navigate back.

## Background

Claude Code stores subagent sessions alongside the parent:
```
~/.claude/projects/{project}/
  {session_id}.jsonl              # parent session
  {session_id}/subagents/
    agent-{agentId}.jsonl         # subagent session
    agent-{agentId}.meta.json     # metadata (agentType)
```

The parent session's JSONL contains Agent tool_use calls, and the corresponding tool_result includes an `agentId: {hex_id}` string that maps to the subagent file name.

Subagent sessions are NOT listed in the launcher's session list — they are only accessible through the parent session viewer.

## Data Flow

### agentId Extraction (viewer.html JS)

agentId extraction happens in `renderStats(messages)`, NOT in `computeStats()`, because it needs access to `globalResultMap` which is built in `renderConversation` scope.

Approach: `renderStats` builds its own lightweight result map from messages (scan user messages for `tool_result` entries), then:

1. Call `computeStats(messages)` as before — extended to also record `toolUseId` on each subagent entry
2. For each subagent with a `toolUseId`, look up the corresponding `tool_result` in the result map
3. Extract agentId via regex `agentId:\s*([a-fA-F0-9]+)` from tool_result text content
4. Attach agentId to the subagent record

The extracted subagent-agentId mapping is stored in a module-level variable (e.g. `window._subagentMap`) so that both `buildStatsHTML` and `createToolSingle` can access it.

### File Existence Check

On viewer load, after extracting all agentIds, call:
```js
const existsMap = await wz.call("check_subagent_exists", {
  root_session_path: sessionInfo.root_session_path,
  agent_ids: [id1, id2, ...]
});
// Returns: { "ae2a981d3905efa69": true, "bf3c...": false }
```

Only subagents with `existsMap[agentId] === true` are rendered as clickable links. Others remain plain text.

### Subagent JSONL Path Resolution (Python)

All subagent files live flat under the **root** (top-level) session's subagents directory. Path resolution always uses `root_session_path`:

```
root_dir    = dirname(root_session_path)     # e.g. ~/.claude/projects/{project}/
session_id  = stem(root_session_path)        # e.g. UUID of root session
subagent_path = root_dir / session_id / "subagents" / f"agent-{agent_id}.jsonl"
```

This handles nested subagents correctly: even if a subagent spawns sub-subagents, the file is resolved from the root session, not the immediate parent.

## Python Bridge API (init_plugin.py)

### Panel creation pattern

Both `_open_viewer` (existing) and `open_subagent` (new) create a `wz.ui.webview_panel()` and register bridge handlers on it via `@panel.handle` closures. The `get_session_info` handler on each panel returns the data specific to that panel's session.

For subagent panels, `get_session_info` returns additional fields:
- `parent_file_path` — the immediate parent's JSONL file path (for the "Parent Session" link)
- `root_session_path` — the root (top-level) session JSONL path (for subagent path resolution)
- `is_subagent: true` — flag for viewer UI mode switching

For root session panels, `get_session_info` returns:
- `root_session_path` = same as `file` (it IS the root)
- `is_subagent: false` (or absent)

### `check_subagent_exists(root_session_path, agent_ids)`

- For each agent_id, resolve subagent path from `root_session_path` and check file existence
- Returns `{agent_id: bool}` map
- Registered as a global bridge handler (not per-panel), since it only does file existence checks

### `open_subagent(root_session_path, parent_file_path, agent_id, description)`

1. Resolve subagent JSONL path from `root_session_path` + `agent_id`
2. Verify file exists (guard)
3. Create new `wz.ui.webview_panel()` with viewer.html
4. Register `get_session_info` handler on the new panel returning:
   - `file` = subagent JSONL path
   - `parent_file_path` = caller's file path
   - `root_session_path` = passed through unchanged
   - `is_subagent: true`
   - `project`, `cwd`, `session_id`, `git_branch`, `version` extracted from subagent JSONL (first few lines, same as scanner logic)
5. Register `copy_resume` handler (same as existing)
6. Set `allowed_read_paths = [os.path.expanduser("~/.claude/")]`
7. Panel title: `"Subagent: {description}"`
8. `panel.show()`

Each panel captures its own reference in the `open_parent_session` closure (see below).

### `open_parent_session(parent_file_path)` — per-panel handler

Registered on each **subagent** panel via closure that captures the panel reference:

```python
@panel.handle("open_parent_session")
def _open_parent(_data):
    panel.close()  # close this subagent viewer
    # Find or re-open parent viewer (implementation detail)
```

For simplicity in v1: just close the current subagent panel. The parent panel is already open underneath (user opened it first). If the parent was closed, the user can reopen from the launcher. This avoids complex panel tracking.

## Viewer UI Changes (viewer.html)

### Info Bar — Parent Link (subagent mode only)

When `sessionInfo.is_subagent` is true, render at the left of info bar:

```
[← Parent Session]  Project: VoiceText  Branch: main  ...
```

Click calls `wz.call("open_parent_session", { parent_file_path: sessionInfo.parent_file_path })`.

### Info Bar — Copy Resume Button (subagent mode)

When `sessionInfo.is_subagent` is true, hide the "Copy Resume Command" button since `claude --resume` does not work for subagent sessions.

### Stats Panel — Subagents Card

Each subagent description line becomes a clickable link (when agentId exists and file confirmed):

```
🔗 Explore recording stop logic  (haiku)     ← clickable
   Some other task  (opus)                    ← plain text if file missing
```

Click calls `wz.call("open_subagent", { root_session_path, parent_file_path: sessionInfo.file, agent_id, description })`.

### Conversation Flow — Agent Tool Block

In `createToolSingle` for Agent tool_use, when agentId is available and file exists, add a `[View Session]` button in the tool-header (right side, before the arrow):

```
🤖 Agent  "Explore recording stop logic"  [View Session]  ▶
```

- Click on `[View Session]` calls `open_subagent` — must NOT trigger the tool block expand/collapse toggle
- `event.stopPropagation()` on the button to prevent header click propagation

### Stats Summary Line

No change — `"3 subagents"` text remains as-is.

## Edge Cases

### agentId extraction failure
Old Claude Code sessions may not include `agentId:` in tool_result. These subagents have `agentId = null`, all link positions fall back to plain text.

### File not found
Handled by `check_subagent_exists` at load time. Non-existent subagent files are rendered as non-clickable text.

### Multiple panels
Multiple subagent viewers can be open simultaneously. Each is independent with its own title.

### Nested subagents
All subagent files are stored flat under the root session's `subagents/` directory. `root_session_path` is passed through unchanged from parent to child, so path resolution always works regardless of nesting depth.

### Copy Resume in subagent mode
Button is hidden since `claude --resume` does not support subagent sessions.

## Files Changed

| File | Change |
|------|--------|
| `plugins/cc_sessions/viewer.html` | agentId extraction, subagent link rendering, parent link, View Session button, bridge calls, hide resume button in subagent mode |
| `plugins/cc_sessions/init_plugin.py` | New bridge handlers: `check_subagent_exists`, `open_subagent`, `open_parent_session`; `get_session_info` extended with `root_session_path`, `parent_file_path`, `is_subagent` |

## Not Changed

- `scanner.py` — no scanning of subagent files
- `cache.py` — cache structure unchanged
- `preview.py` — launcher preview unchanged
- Launcher session list — subagents not listed
