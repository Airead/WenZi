"""WebView-based settings panel.

Uses WKWebView + WKScriptMessageHandler for a modern HTML/CSS/JS settings UI.
Drop-in replacement for the native PyObjC SettingsPanel.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bridge JavaScript injected at document start
# ---------------------------------------------------------------------------
_BRIDGE_JS = """\
(function() {
    window._postMessage = function(msg) {
        window.webkit.messageHandlers.wz.postMessage(msg);
    };
    // Forward console to Python logger
    var _origConsole = {log: console.log.bind(console), warn: console.warn.bind(console), error: console.error.bind(console)};
    function _forward(level, args) {
        try {
            var msg = Array.from(args).map(function(a) { return typeof a === 'object' ? JSON.stringify(a) : String(a); }).join(' ');
            window.webkit.messageHandlers.wz.postMessage({type: 'console', level: level, message: msg});
        } catch(e) {}
    }
    console.log = function() { _origConsole.log.apply(null, arguments); _forward('info', arguments); };
    console.warn = function() { _origConsole.warn.apply(null, arguments); _forward('warning', arguments); };
    console.error = function() { _origConsole.error.apply(null, arguments); _forward('error', arguments); };
})();
"""

# ---------------------------------------------------------------------------
# Minimal placeholder HTML template (replaced in later tasks)
# ---------------------------------------------------------------------------
_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Settings</title>
<script>var CONFIG = __CONFIG__;</script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif;
  font-size: 13px;
  color: #1d1d1f;
  background: #f5f5f7;
}

.container {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

/* Sidebar */
.sidebar {
  width: 180px;
  min-width: 180px;
  background: rgba(245,245,247,0.8);
  border-right: 1px solid #d2d2d7;
  padding: 12px 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sidebar-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
  font-size: 13px;
  color: #1d1d1f;
  -webkit-user-select: none;
  user-select: none;
}

.sidebar-item:hover { background: rgba(0,0,0,0.05); }

.sidebar-item.active {
  background: rgba(0, 122, 255, 0.12);
  color: #007aff;
  font-weight: 500;
}

.sidebar-icon {
  width: 22px;
  height: 22px;
  border-radius: 5px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  color: white;
  flex-shrink: 0;
}

/* Content area */
.content {
  flex: 1;
  overflow-y: auto;
  padding: 24px 28px;
}

.content-title {
  font-size: 20px;
  font-weight: 600;
  margin-bottom: 20px;
}

.group-title {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #6e6e73;
  margin-bottom: 6px;
  margin-top: 20px;
  padding-left: 4px;
}

.group-title:first-of-type { margin-top: 0; }

.setting-group {
  background: white;
  border-radius: 10px;
  border: 0.5px solid #d2d2d7;
  overflow: hidden;
  margin-bottom: 16px;
}

.setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  min-height: 40px;
  border-bottom: 0.5px solid #e5e5e7;
}

.setting-row:last-child { border-bottom: none; }

.setting-left {
  display: flex;
  flex-direction: column;
  gap: 1px;
  flex: 1;
  min-width: 0;
}

.setting-label { font-size: 13px; color: #1d1d1f; }
.setting-desc { font-size: 11px; color: #86868b; margin-top: 1px; }
.setting-right { flex-shrink: 0; margin-left: 12px; display: flex; align-items: center; gap: 6px; }

/* Toggle switch */
.toggle {
  position: relative;
  width: 38px;
  height: 22px;
  background: #e5e5ea;
  border-radius: 11px;
  cursor: pointer;
  transition: background 0.2s;
  flex-shrink: 0;
}

.toggle.on { background: #34c759; }

.toggle::after {
  content: '';
  position: absolute;
  width: 18px;
  height: 18px;
  background: white;
  border-radius: 50%;
  top: 2px;
  left: 2px;
  transition: transform 0.2s;
  box-shadow: 0 1px 3px rgba(0,0,0,0.2);
}

.toggle.on::after { transform: translateX(16px); }

/* Select dropdown */
select {
  -webkit-appearance: none;
  appearance: none;
  background: white;
  border: 0.5px solid #d2d2d7;
  border-radius: 6px;
  padding: 4px 28px 4px 10px;
  font-size: 13px;
  color: #1d1d1f;
  cursor: pointer;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%2386868b'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 8px center;
  min-width: 100px;
}

/* Inputs */
input[type="text"], input[type="number"] {
  border: 0.5px solid #d2d2d7;
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 13px;
  width: 80px;
  background: white;
  color: #1d1d1f;
}

input[type="range"] {
  accent-color: #007aff;
}

/* Buttons */
.toolbar-btn {
  font-size: 12px;
  color: #007aff;
  cursor: pointer;
  background: none;
  border: none;
  padding: 0;
}

.toolbar-btn:hover { text-decoration: underline; }

.btn-small {
  font-size: 11px;
  color: #007aff;
  cursor: pointer;
  background: none;
  border: none;
  padding: 2px 6px;
  border-radius: 4px;
}

.btn-small:hover { background: rgba(0,122,255,0.08); }

.btn-small.danger { color: #ff3b30; }
.btn-small.danger:hover { background: rgba(255,59,48,0.08); }

/* Hotkey badge */
.hotkey-badge {
  display: inline-block;
  background: #e5e5ea;
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 11px;
  font-family: "SF Mono", Menlo, monospace;
  color: #6e6e73;
}

/* Model list styles */
.provider-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  background: #fafafa;
  border-bottom: 0.5px solid #e5e5e7;
}

.provider-name { font-size: 12px; font-weight: 600; color: #1d1d1f; }

.provider-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
  font-weight: 500;
}
.provider-badge.local { background: #d4edda; color: #1b7a3d; }
.provider-badge.remote { background: #d6e9ff; color: #1a5ab8; }

.model-row {
  display: flex;
  align-items: center;
  padding: 9px 14px;
  border-bottom: 0.5px solid #e5e5e7;
  cursor: pointer;
  transition: background 0.1s;
}
.model-row:last-child { border-bottom: none; }
.model-row:hover { background: rgba(0,0,0,0.02); }

.model-radio {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 2px solid #d2d2d7;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-right: 10px;
  transition: border-color 0.15s;
}
.model-row.selected .model-radio { border-color: #007aff; }
.model-row.selected .model-radio::after {
  content: '';
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #007aff;
}

.model-info { flex: 1; min-width: 0; }
.model-name { font-size: 13px; font-weight: 450; }
.model-detail { font-size: 11px; color: #86868b; margin-top: 1px; }

.model-tag {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
  background: #f0f0f0;
  color: #6e6e73;
  margin-left: 8px;
  flex-shrink: 0;
}

.model-size {
  font-size: 11px;
  color: #86868b;
  margin-left: 8px;
  flex-shrink: 0;
  min-width: 50px;
  text-align: right;
}

.model-actions {
  margin-left: 8px;
  flex-shrink: 0;
}

.add-row {
  display: flex;
  justify-content: center;
  padding: 8px;
  border-bottom: 0.5px solid #e5e5e7;
}
.add-row:last-child { border-bottom: none; }

/* Tab visibility */
.tab-content { display: none; }
.tab-content.active { display: block; }

/* Config path display */
.config-path {
  font-size: 11px;
  color: #86868b;
  font-family: "SF Mono", Menlo, monospace;
  word-break: break-all;
}

/* Bottom bar */
.bottom-bar {
  margin-top: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* Dark mode */
@media (prefers-color-scheme: dark) {
  body { background: #1e1e1e; color: #f5f5f7; }
  .sidebar { background: #2a2a2a; border-color: #3a3a3a; }
  .sidebar-item { color: #f5f5f7; }
  .sidebar-item:hover { background: rgba(255,255,255,0.08); }
  .sidebar-item.active { background: rgba(100,149,237,0.25); color: #6ca0f6; }
  .content { background: #1e1e1e; }
  .content-title { color: #f5f5f7; }
  .setting-group { background: #2a2a2a; border-color: #3a3a3a; }
  .setting-row { border-color: #3a3a3a; }
  .setting-label { color: #f5f5f7; }
  .setting-desc { color: #98989d; }
  .group-title { color: #98989d; }
  select, input[type="text"], input[type="number"] {
    background: #3a3a3a; color: #f5f5f7; border-color: #4a4a4a;
  }
  .toolbar-btn { color: #6ca0f6; }
  .btn-small { color: #6ca0f6; }
  .btn-small:hover { background: rgba(100,149,237,0.12); }
  .btn-small.danger { color: #ff453a; }
  .btn-small.danger:hover { background: rgba(255,69,58,0.12); }
  .hotkey-badge { background: #3a3a3a; color: #98989d; }
  .toggle { background: #48484a; }
  .toggle.on { background: #30d158; }
  .model-row:hover { background: rgba(255,255,255,0.04); }
  .model-tag { background: #3a3a3a; color: #98989d; }
  .model-radio { border-color: #48484a; }
  .model-row.selected .model-radio { border-color: #6ca0f6; }
  .model-row.selected .model-radio::after { background: #6ca0f6; }
  .provider-header { background: #333; border-color: #3a3a3a; }
  .provider-name { color: #f5f5f7; }
  .provider-badge.local { background: rgba(52,199,89,0.2); color: #30d158; }
  .provider-badge.remote { background: rgba(100,149,237,0.2); color: #6ca0f6; }
  .config-path { color: #98989d; }
}
</style>
</head>
<body>
<div class="container">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sidebar-item active" data-tab="general" onclick="switchTab('general')">
      <div class="sidebar-icon" style="background:linear-gradient(135deg,#52a3fc,#007aff);">&#x2699;</div>
      General
    </div>
    <div class="sidebar-item" data-tab="speech" onclick="switchTab('speech')">
      <div class="sidebar-icon" style="background:linear-gradient(135deg,#ff6b6b,#ee5a24);">&#x1f3a4;</div>
      Speech
    </div>
    <div class="sidebar-item" data-tab="llm" onclick="switchTab('llm')">
      <div class="sidebar-icon" style="background:linear-gradient(135deg,#a29bfe,#6c5ce7);">&#x1f9e0;</div>
      LLM
    </div>
    <div class="sidebar-item" data-tab="ai" onclick="switchTab('ai')">
      <div class="sidebar-icon" style="background:linear-gradient(135deg,#55efc4,#00b894);">&#x2728;</div>
      AI
    </div>
    <div class="sidebar-item" data-tab="launcher" onclick="switchTab('launcher')">
      <div class="sidebar-icon" style="background:linear-gradient(135deg,#fdcb6e,#e17055);">&#x1f680;</div>
      Launcher
    </div>
  </div>

  <!-- Content -->
  <div class="content">
    <!-- General Tab -->
    <div id="tab-general" class="tab-content active">
      <div class="content-title">General</div>

      <div class="group-title">Language</div>
      <div class="setting-group">
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Interface Language</div>
          </div>
          <div class="setting-right">
            <select id="ctl-language" onchange="postCallback('on_language_change', this.value)">
              <option value="auto">Auto</option>
              <option value="en">English</option>
              <option value="zh">Chinese</option>
            </select>
          </div>
        </div>
      </div>

      <div class="group-title">Hotkeys</div>
      <div class="setting-group" id="hotkeys-group">
        <!-- Dynamically populated by renderHotkeys() -->
      </div>

      <div class="group-title">Feedback</div>
      <div class="setting-group">
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Sound Effects</div>
          </div>
          <div class="setting-right">
            <div id="ctl-sound" class="toggle" onclick="toggleClick(this, 'on_sound_toggle')"></div>
          </div>
        </div>
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Volume</div>
          </div>
          <div class="setting-right">
            <input id="ctl-volume" type="range" min="0" max="100" value="70" style="width:100px;"
                   oninput="postCallback('on_volume_change', parseInt(this.value))">
          </div>
        </div>
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Visual Indicator</div>
            <div class="setting-desc">Show floating indicator while recording</div>
          </div>
          <div class="setting-right">
            <div id="ctl-visual" class="toggle" onclick="toggleClick(this, 'on_visual_toggle')"></div>
          </div>
        </div>
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Show Device Name</div>
          </div>
          <div class="setting-right">
            <div id="ctl-device-name" class="toggle" onclick="toggleClick(this, 'on_device_name_toggle')"></div>
          </div>
        </div>
      </div>

      <div class="group-title">Output</div>
      <div class="setting-group">
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Preview Before Output</div>
            <div class="setting-desc">Show transcription result for review</div>
          </div>
          <div class="setting-right">
            <div id="ctl-preview" class="toggle" onclick="toggleClick(this, 'on_preview_toggle')"></div>
          </div>
        </div>
      </div>

      <div class="group-title">Advanced</div>
      <div class="setting-group">
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Scripting</div>
            <div class="setting-desc">Enable plugin scripting engine</div>
          </div>
          <div class="setting-right">
            <div id="ctl-scripting" class="toggle" onclick="toggleClick(this, 'on_scripting_toggle')"></div>
          </div>
        </div>
      </div>

      <div class="bottom-bar">
        <div style="display:flex; gap:16px; align-items:center;">
          <button class="toolbar-btn" onclick="postCallback('on_reveal_config_folder')">Reveal Config Folder</button>
        </div>
        <div id="config-dir-display" class="config-path"></div>
      </div>
    </div>

    <!-- Speech Tab -->
    <div id="tab-speech" class="tab-content">
      <div class="content-title">Speech Recognition</div>
      <div class="group-title">Select Engine</div>
      <div class="setting-group" id="stt-model-list">
        <!-- Dynamically populated by renderSttTab() -->
      </div>
    </div>

    <!-- LLM Tab -->
    <div id="tab-llm" class="tab-content">
      <div class="content-title">LLM</div>
      <div class="group-title">Select Model</div>
      <div class="setting-group" id="llm-model-list">
        <!-- Dynamically populated by renderLlmTab() -->
      </div>
      <div class="group-title">Connection</div>
      <div class="setting-group">
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Timeout</div>
          </div>
          <div class="setting-right">
            <input id="ctl-model-timeout" type="number" value="10" style="width:60px;"
                   onchange="postCallback('on_model_timeout', parseInt(this.value))">
            <span style="color:#86868b;font-size:12px;">sec</span>
          </div>
        </div>
      </div>
    </div>

    <!-- AI Tab -->
    <div id="tab-ai" class="tab-content">
      <div class="content-title">AI Enhancement</div>

      <div class="group-title">Mode</div>
      <div class="setting-group" id="ai-modes-group">
        <!-- Dynamically populated by renderAiModes() -->
      </div>

      <div class="group-title">Thinking</div>
      <div class="setting-group">
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Enable Thinking</div>
            <div class="setting-desc">Let the model reason step-by-step</div>
          </div>
          <div class="setting-right">
            <div id="ctl-thinking" class="toggle" onclick="toggleClick(this, 'on_thinking_toggle')"></div>
          </div>
        </div>
      </div>

      <div class="group-title">Vocabulary</div>
      <div class="setting-group">
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Enable Vocabulary</div>
          </div>
          <div class="setting-right">
            <div id="ctl-vocab" class="toggle" onclick="toggleClick(this, 'on_vocab_toggle')"></div>
          </div>
        </div>
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Auto-build</div>
            <div class="setting-desc">Automatically extract new terms</div>
          </div>
          <div class="setting-right">
            <div id="ctl-auto-build" class="toggle" onclick="toggleClick(this, 'on_auto_build_toggle')"></div>
          </div>
        </div>
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Vocabulary Count</div>
          </div>
          <div class="setting-right">
            <span id="ctl-vocab-count" style="color:#86868b;font-size:12px;">0</span>
          </div>
        </div>
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Build Model</div>
          </div>
          <div class="setting-right">
            <select id="ctl-vocab-build-model"
                    onchange="postCallback('on_vocab_build_model_select', this.value)">
            </select>
          </div>
        </div>
        <div class="setting-row" style="justify-content:center; padding:8px;">
          <button class="toolbar-btn" style="font-size:13px;" onclick="postCallback('on_vocab_build')">Build Vocabulary</button>
        </div>
      </div>

      <div class="group-title">Context</div>
      <div class="setting-group">
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Conversation History</div>
            <div class="setting-desc">Include recent messages for context</div>
          </div>
          <div class="setting-right">
            <div id="ctl-history" class="toggle" onclick="toggleClick(this, 'on_history_toggle')"></div>
          </div>
        </div>
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Max Entries</div>
          </div>
          <div class="setting-right">
            <input id="ctl-history-max" type="number" value="100" style="width:60px;"
                   onchange="postCallback('on_history_max_entries', parseInt(this.value))">
          </div>
        </div>
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Refresh Threshold</div>
          </div>
          <div class="setting-right">
            <input id="ctl-history-refresh" type="number" value="50" style="width:60px;"
                   onchange="postCallback('on_history_refresh_threshold', parseInt(this.value))">
          </div>
        </div>
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Input Context Level</div>
          </div>
          <div class="setting-right">
            <select id="ctl-input-context"
                    onchange="postCallback('on_input_context_change', this.value)">
              <option value="basic">Basic</option>
              <option value="standard">Standard</option>
              <option value="full">Full</option>
            </select>
          </div>
        </div>
      </div>
    </div>

    <!-- Launcher Tab -->
    <div id="tab-launcher" class="tab-content">
      <div class="content-title">Launcher</div>

      <div class="group-title">General</div>
      <div class="setting-group">
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Enable Launcher</div>
          </div>
          <div class="setting-right">
            <div id="ctl-launcher-enabled" class="toggle" onclick="toggleClick(this, 'on_launcher_toggle')"></div>
          </div>
        </div>
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Hotkey</div>
          </div>
          <div class="setting-right">
            <span id="ctl-launcher-hotkey" class="hotkey-badge">None</span>
            <button class="btn-small" onclick="postCallback('on_launcher_hotkey_record')">Record</button>
            <button class="btn-small danger" onclick="postCallback('on_launcher_hotkey_clear')">Clear</button>
          </div>
        </div>
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Usage Learning</div>
            <div class="setting-desc">Rank results based on your usage patterns</div>
          </div>
          <div class="setting-right">
            <div id="ctl-launcher-usage-learning" class="toggle" onclick="toggleClick(this, 'on_launcher_usage_learning_toggle')"></div>
          </div>
        </div>
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">Switch to English Input</div>
            <div class="setting-desc">Automatically switch to English when launcher opens</div>
          </div>
          <div class="setting-right">
            <div id="ctl-launcher-switch-english" class="toggle" onclick="toggleClick(this, 'on_launcher_switch_english_toggle')"></div>
          </div>
        </div>
      </div>

      <div class="group-title">Sources</div>
      <div class="setting-group" id="launcher-sources-group">
        <!-- Dynamically populated by renderLauncherSources() -->
      </div>

      <div class="group-title">Snippets</div>
      <div class="setting-group">
        <div class="setting-row">
          <div class="setting-left">
            <div class="setting-label">New Snippet Hotkey</div>
          </div>
          <div class="setting-right">
            <span id="ctl-new-snippet-hotkey" class="hotkey-badge">None</span>
            <button class="btn-small" onclick="postCallback('on_new_snippet_hotkey_record')">Record</button>
            <button class="btn-small danger" onclick="postCallback('on_new_snippet_hotkey_clear')">Clear</button>
          </div>
        </div>
      </div>

      <div style="margin-top:12px;">
        <button class="toolbar-btn" onclick="postCallback('on_launcher_refresh_icons')">Refresh Icons</button>
      </div>
    </div>
  </div>
</div>

<script>
/* ------------------------------------------------------------------ */
/* Core helper functions                                               */
/* ------------------------------------------------------------------ */

function postCallback(name) {
  var args = Array.prototype.slice.call(arguments, 1);
  window._postMessage({type: 'callback', name: name, args: args});
}

function toggleClick(el, callbackName) {
  el.classList.toggle('on');
  var isOn = el.classList.contains('on');
  postCallback(callbackName, isOn);
}

function setToggle(id, value) {
  var el = document.getElementById(id);
  if (!el) return;
  if (value) { el.classList.add('on'); } else { el.classList.remove('on'); }
}

function switchTab(tabId) {
  document.querySelectorAll('.tab-content').forEach(function(t) { t.classList.remove('active'); });
  document.querySelectorAll('.sidebar-item').forEach(function(s) { s.classList.remove('active'); });
  var tab = document.getElementById('tab-' + tabId);
  if (tab) tab.classList.add('active');
  var item = document.querySelector('.sidebar-item[data-tab="' + tabId + '"]');
  if (item) item.classList.add('active');
  postCallback('on_tab_change', tabId);
}

function formatHotkey(hk) {
  if (!hk) return 'None';
  var parts = [];
  var mods = hk.modifiers || [];
  if (mods.indexOf('command') >= 0) parts.push('\\u2318');
  if (mods.indexOf('option') >= 0) parts.push('\\u2325');
  if (mods.indexOf('control') >= 0) parts.push('\\u2303');
  if (mods.indexOf('shift') >= 0) parts.push('\\u21e7');
  var key = (hk.key || '').charAt(0).toUpperCase() + (hk.key || '').slice(1);
  parts.push(key);
  return parts.join(' ');
}

/* ------------------------------------------------------------------ */
/* General tab: Hotkeys                                                */
/* ------------------------------------------------------------------ */

function renderHotkeys() {
  var container = document.getElementById('hotkeys-group');
  if (!container) return;
  var hotkeys = CONFIG.hotkeys || {};
  var html = '';
  var keys = Object.keys(hotkeys);
  for (var i = 0; i < keys.length; i++) {
    var key = keys[i];
    var hk = hotkeys[key];
    var enabled = hk.enabled !== undefined ? hk.enabled : true;
    var mode = hk.mode || 'hold';
    var label = hk.label || key;
    html += '<div class="setting-row">';
    html += '  <div class="setting-left">';
    html += '    <div class="setting-label">' + _esc(label) + '</div>';
    if (hk.description) html += '    <div class="setting-desc">' + _esc(hk.description) + '</div>';
    html += '  </div>';
    html += '  <div class="setting-right">';
    html += '    <select onchange="postCallback(\\x27on_hotkey_mode_select\\x27, \\x27' + _esc(key) + '\\x27, this.value)">';
    html += '      <option value="hold"' + (mode === 'hold' ? ' selected' : '') + '>Hold to Record</option>';
    html += '      <option value="toggle"' + (mode === 'toggle' ? ' selected' : '') + '>Toggle</option>';
    html += '    </select>';
    html += '    <div class="toggle' + (enabled ? ' on' : '') + '" data-hotkey="' + _esc(key) + '"';
    html += '         onclick="this.classList.toggle(\\x27on\\x27); postCallback(\\x27on_hotkey_toggle\\x27, \\x27' + _esc(key) + '\\x27, this.classList.contains(\\x27on\\x27))"></div>';
    html += '  </div>';
    html += '</div>';
  }
  // Record hotkey button
  html += '<div class="setting-row" style="justify-content:center; padding:8px;">';
  html += '  <button class="toolbar-btn" style="font-size:13px;" onclick="postCallback(\\x27on_record_hotkey\\x27)">Record Hotkey</button>';
  html += '</div>';
  // Restart key select
  html += '<div class="setting-row">';
  html += '  <div class="setting-left"><div class="setting-label">Restart Modifier</div></div>';
  html += '  <div class="setting-right">';
  var restartKey = CONFIG.restart_key || 'command';
  html += '    <select id="ctl-restart-key" onchange="postCallback(\\x27on_restart_key_select\\x27, this.value)">';
  html += '      <option value="command"' + (restartKey === 'command' ? ' selected' : '') + '>\\u2318 Command</option>';
  html += '      <option value="option"' + (restartKey === 'option' ? ' selected' : '') + '>\\u2325 Option</option>';
  html += '      <option value="control"' + (restartKey === 'control' ? ' selected' : '') + '>\\u2303 Control</option>';
  html += '    </select>';
  html += '    <button class="btn-small danger" onclick="postCallback(\\x27on_cancel_key_select\\x27, document.getElementById(\\x27ctl-restart-key\\x27).value)">Clear</button>';
  html += '  </div>';
  html += '</div>';
  container.innerHTML = html;
}

/* ------------------------------------------------------------------ */
/* Speech tab                                                          */
/* ------------------------------------------------------------------ */

function renderSttTab() {
  var container = document.getElementById('stt-model-list');
  if (!container) return;
  var presets = CONFIG.stt_presets || [];
  var remotes = CONFIG.stt_remote_models || [];
  var currentPreset = CONFIG.current_preset_id || '';
  var currentRemote = CONFIG.current_remote_asr || '';
  var html = '';

  // Local engines
  if (presets.length > 0) {
    html += '<div class="provider-header">';
    html += '  <span class="provider-name">Local</span>';
    html += '  <span class="provider-badge local">On-device</span>';
    html += '</div>';
    for (var i = 0; i < presets.length; i++) {
      var p = presets[i];
      var sel = (!currentRemote && p.id === currentPreset) ? ' selected' : '';
      html += '<div class="model-row' + sel + '" onclick="selectStt(\\x27preset\\x27, \\x27' + _esc(p.id) + '\\x27, this)">';
      html += '  <div class="model-radio"></div>';
      html += '  <div class="model-info">';
      html += '    <div class="model-name">' + _esc(p.name) + '</div>';
      html += '  </div>';
      if (p.available) {
        html += '  <div class="model-tag" style="background:#d4edda;color:#1b7a3d;">Available</div>';
      } else {
        html += '  <div class="model-tag">Not Installed</div>';
      }
      html += '</div>';
    }
  }

  // Remote providers
  if (remotes.length > 0) {
    html += '<div class="provider-header">';
    html += '  <span class="provider-name">Remote</span>';
    html += '  <span class="provider-badge remote">Cloud</span>';
    html += '</div>';
    for (var j = 0; j < remotes.length; j++) {
      var r = remotes[j];
      var rsel = (currentRemote === r.provider) ? ' selected' : '';
      html += '<div class="model-row' + rsel + '" onclick="selectStt(\\x27remote\\x27, \\x27' + _esc(r.provider) + '\\x27, this)">';
      html += '  <div class="model-radio"></div>';
      html += '  <div class="model-info">';
      html += '    <div class="model-name">' + _esc(r.display) + '</div>';
      html += '  </div>';
      html += '  <div class="model-tag">API Key</div>';
      html += '  <div class="model-actions">';
      html += '    <button class="btn-small danger" onclick="event.stopPropagation(); postCallback(\\x27on_stt_remove_provider\\x27, \\x27' + _esc(r.provider) + '\\x27)">Remove</button>';
      html += '  </div>';
      html += '</div>';
    }
  }

  // Add provider button
  html += '<div class="add-row">';
  html += '  <button class="toolbar-btn" style="font-size:13px;" onclick="postCallback(\\x27on_stt_add_provider\\x27)">+ Add Provider</button>';
  html += '</div>';

  container.innerHTML = html;
}

function selectStt(type, id, row) {
  var card = row.closest('.setting-group');
  card.querySelectorAll('.model-row').forEach(function(r) { r.classList.remove('selected'); });
  row.classList.add('selected');
  if (type === 'preset') {
    postCallback('on_stt_select', id);
  } else {
    postCallback('on_stt_remote_select', id);
  }
}

/* ------------------------------------------------------------------ */
/* LLM tab                                                             */
/* ------------------------------------------------------------------ */

function renderLlmTab() {
  var container = document.getElementById('llm-model-list');
  if (!container) return;
  var models = CONFIG.llm_models || [];
  var current = CONFIG.current_llm || {};
  var html = '';

  // Group by provider
  var groups = {};
  var groupOrder = [];
  for (var i = 0; i < models.length; i++) {
    var m = models[i];
    if (!groups[m.provider]) {
      groups[m.provider] = [];
      groupOrder.push(m.provider);
    }
    groups[m.provider].push(m);
  }

  for (var g = 0; g < groupOrder.length; g++) {
    var provider = groupOrder[g];
    var items = groups[provider];
    var isLocal = provider.toLowerCase().indexOf('ollama') >= 0 ||
                  provider.toLowerCase().indexOf('local') >= 0;
    html += '<div class="provider-header">';
    html += '  <span class="provider-name">' + _esc(provider) + '</span>';
    html += '  <span class="provider-badge ' + (isLocal ? 'local' : 'remote') + '">' + (isLocal ? 'Local' : 'Cloud') + '</span>';
    html += '</div>';
    for (var k = 0; k < items.length; k++) {
      var item = items[k];
      var sel = (current.provider === item.provider && current.model === item.model) ? ' selected' : '';
      html += '<div class="model-row' + sel + '" onclick="selectLlm(\\x27' + _esc(item.provider) + '\\x27, \\x27' + _esc(item.model) + '\\x27, this)">';
      html += '  <div class="model-radio"></div>';
      html += '  <div class="model-info">';
      html += '    <div class="model-name">' + _esc(item.display) + '</div>';
      html += '  </div>';
      if (!isLocal) {
        html += '  <div class="model-tag">API Key</div>';
      }
      html += '</div>';
    }
  }

  // Add provider + remove
  html += '<div class="add-row">';
  html += '  <button class="toolbar-btn" style="font-size:13px;" onclick="postCallback(\\x27on_llm_add_provider\\x27)">+ Add Provider</button>';
  html += '</div>';

  container.innerHTML = html;
}

function selectLlm(provider, model, row) {
  var card = row.closest('.setting-group');
  card.querySelectorAll('.model-row').forEach(function(r) { r.classList.remove('selected'); });
  row.classList.add('selected');
  postCallback('on_llm_select', provider, model);
}

/* ------------------------------------------------------------------ */
/* AI tab: Enhance modes                                               */
/* ------------------------------------------------------------------ */

function renderAiModes() {
  var container = document.getElementById('ai-modes-group');
  if (!container) return;
  var modes = CONFIG.enhance_modes || [];
  var current = CONFIG.current_enhance_mode || '';
  var html = '';

  for (var i = 0; i < modes.length; i++) {
    var m = modes[i];
    var sel = (m.id === current) ? ' selected' : '';
    html += '<div class="model-row' + sel + '" onclick="selectEnhanceMode(\\x27' + _esc(m.id) + '\\x27, this)">';
    html += '  <div class="model-radio"></div>';
    html += '  <div class="model-info">';
    html += '    <div class="model-name">' + _esc(m.name) + '</div>';
    html += '  </div>';
    html += '  <div class="model-actions">';
    html += '    <button class="btn-small" onclick="event.stopPropagation(); postCallback(\\x27on_enhance_mode_edit\\x27, \\x27' + _esc(m.id) + '\\x27)">Edit</button>';
    html += '  </div>';
    html += '</div>';
  }

  html += '<div class="add-row">';
  html += '  <button class="toolbar-btn" style="font-size:13px;" onclick="postCallback(\\x27on_enhance_add_mode\\x27)">+ Add Mode</button>';
  html += '</div>';

  container.innerHTML = html;
}

function selectEnhanceMode(modeId, row) {
  var card = row.closest('.setting-group');
  card.querySelectorAll('.model-row').forEach(function(r) { r.classList.remove('selected'); });
  row.classList.add('selected');
  postCallback('on_enhance_mode_select', modeId);
}

/* ------------------------------------------------------------------ */
/* Launcher tab: Sources                                               */
/* ------------------------------------------------------------------ */

function renderLauncherSources() {
  var container = document.getElementById('launcher-sources-group');
  if (!container) return;
  var launcher = CONFIG.launcher || {};
  var sources = launcher.sources || {};
  var keys = Object.keys(sources);
  var html = '';

  for (var i = 0; i < keys.length; i++) {
    var src = keys[i];
    var cfg = sources[src];
    var enabled = cfg.enabled !== undefined ? cfg.enabled : true;
    var prefix = cfg.prefix || '';
    var hk = cfg.hotkey || null;
    html += '<div class="setting-row">';
    html += '  <div class="setting-left">';
    html += '    <div class="setting-label">' + _esc(src.charAt(0).toUpperCase() + src.slice(1)) + '</div>';
    if (prefix) html += '    <div class="setting-desc">Prefix: ' + _esc(prefix) + '</div>';
    html += '  </div>';
    html += '  <div class="setting-right">';
    html += '    <span class="hotkey-badge">' + _esc(formatHotkey(hk)) + '</span>';
    html += '    <button class="btn-small" onclick="postCallback(\\x27on_launcher_source_hotkey_record\\x27, \\x27' + _esc(src) + '\\x27)">Record</button>';
    html += '    <button class="btn-small danger" onclick="postCallback(\\x27on_launcher_source_hotkey_clear\\x27, \\x27' + _esc(src) + '\\x27)">Clear</button>';
    html += '    <div class="toggle' + (enabled ? ' on' : '') + '"';
    html += '         onclick="this.classList.toggle(\\x27on\\x27); postCallback(\\x27on_launcher_source_toggle\\x27, \\x27' + _esc(src) + '\\x27, this.classList.contains(\\x27on\\x27))"></div>';
    html += '  </div>';
    html += '</div>';
  }

  if (keys.length === 0) {
    html += '<div class="setting-row"><div class="setting-left"><div class="setting-desc">No sources configured</div></div></div>';
  }

  container.innerHTML = html;
}

/* ------------------------------------------------------------------ */
/* HTML escaping                                                       */
/* ------------------------------------------------------------------ */

function _esc(s) {
  if (s === null || s === undefined) return '';
  var div = document.createElement('div');
  div.appendChild(document.createTextNode(String(s)));
  return div.innerHTML;
}

/* ------------------------------------------------------------------ */
/* Initialization                                                      */
/* ------------------------------------------------------------------ */

function _initState(config) {
  // Language
  var langSel = document.getElementById('ctl-language');
  if (langSel && config.language) langSel.value = config.language;

  // Feedback
  setToggle('ctl-sound', config.sound_enabled);
  if (config.volume !== undefined) {
    var vol = document.getElementById('ctl-volume');
    if (vol) vol.value = config.volume;
  }
  setToggle('ctl-visual', config.visual_indicator);
  setToggle('ctl-device-name', config.show_device_name);

  // Output
  setToggle('ctl-preview', config.preview);

  // Advanced
  setToggle('ctl-scripting', config.scripting_enabled);

  // Config dir
  var configDir = document.getElementById('config-dir-display');
  if (configDir && config.config_dir) configDir.textContent = config.config_dir;

  // Hotkeys
  renderHotkeys();

  // Speech
  renderSttTab();

  // LLM
  renderLlmTab();
  var timeout = document.getElementById('ctl-model-timeout');
  if (timeout && config.model_timeout !== undefined) timeout.value = config.model_timeout;

  // AI
  renderAiModes();
  setToggle('ctl-thinking', config.thinking);
  setToggle('ctl-vocab', config.vocab_enabled);
  setToggle('ctl-auto-build', config.auto_build);
  var vocabCount = document.getElementById('ctl-vocab-count');
  if (vocabCount) vocabCount.textContent = config.vocab_count || '0';

  // Vocab build model select
  var vocabModelSel = document.getElementById('ctl-vocab-build-model');
  if (vocabModelSel && config.llm_models) {
    var opts = '';
    for (var i = 0; i < config.llm_models.length; i++) {
      var m = config.llm_models[i];
      opts += '<option value="' + _esc(m.provider + '/' + m.model) + '">' + _esc(m.display) + '</option>';
    }
    vocabModelSel.innerHTML = opts;
    if (config.vocab_build_model) vocabModelSel.value = config.vocab_build_model;
  }

  // Context
  setToggle('ctl-history', config.history_enabled);
  var histMax = document.getElementById('ctl-history-max');
  if (histMax && config.history_max_entries !== undefined) histMax.value = config.history_max_entries;
  var histRefresh = document.getElementById('ctl-history-refresh');
  if (histRefresh && config.history_refresh_threshold !== undefined) histRefresh.value = config.history_refresh_threshold;
  var inputCtx = document.getElementById('ctl-input-context');
  if (inputCtx && config.input_context_level) inputCtx.value = config.input_context_level;

  // Launcher
  var launcher = config.launcher || {};
  setToggle('ctl-launcher-enabled', launcher.enabled);
  setToggle('ctl-launcher-usage-learning', launcher.usage_learning);
  setToggle('ctl-launcher-switch-english', launcher.switch_english);
  var launcherHk = document.getElementById('ctl-launcher-hotkey');
  if (launcherHk) launcherHk.textContent = formatHotkey(launcher.hotkey);
  renderLauncherSources();

  // Snippets hotkey
  var snippetHk = document.getElementById('ctl-new-snippet-hotkey');
  if (snippetHk && launcher.new_snippet_hotkey) {
    snippetHk.textContent = formatHotkey(launcher.new_snippet_hotkey);
  }

  // Restore last tab
  if (config.last_tab && config.last_tab !== 'general') {
    var tabEl = document.getElementById('tab-' + config.last_tab);
    if (tabEl) {
      document.querySelectorAll('.tab-content').forEach(function(t) { t.classList.remove('active'); });
      document.querySelectorAll('.sidebar-item').forEach(function(s) { s.classList.remove('active'); });
      tabEl.classList.add('active');
      var sideItem = document.querySelector('.sidebar-item[data-tab="' + config.last_tab + '"]');
      if (sideItem) sideItem.classList.add('active');
    }
  }
}

/* ------------------------------------------------------------------ */
/* Incremental state update (called from Python)                       */
/* ------------------------------------------------------------------ */

function _updateState(state) {
  if (state.sound_enabled !== undefined) setToggle('ctl-sound', state.sound_enabled);
  if (state.visual_indicator !== undefined) setToggle('ctl-visual', state.visual_indicator);
  if (state.show_device_name !== undefined) setToggle('ctl-device-name', state.show_device_name);
  if (state.preview !== undefined) setToggle('ctl-preview', state.preview);
  if (state.scripting_enabled !== undefined) setToggle('ctl-scripting', state.scripting_enabled);
  if (state.thinking !== undefined) setToggle('ctl-thinking', state.thinking);
  if (state.vocab_enabled !== undefined) setToggle('ctl-vocab', state.vocab_enabled);
  if (state.auto_build !== undefined) setToggle('ctl-auto-build', state.auto_build);
  if (state.history_enabled !== undefined) setToggle('ctl-history', state.history_enabled);

  if (state.language !== undefined) {
    var langSel = document.getElementById('ctl-language');
    if (langSel) langSel.value = state.language;
  }
  if (state.model_timeout !== undefined) {
    var to = document.getElementById('ctl-model-timeout');
    if (to) to.value = state.model_timeout;
  }
  if (state.vocab_count !== undefined) {
    var vc = document.getElementById('ctl-vocab-count');
    if (vc) vc.textContent = state.vocab_count;
  }
  if (state.history_max_entries !== undefined) {
    var hm = document.getElementById('ctl-history-max');
    if (hm) hm.value = state.history_max_entries;
  }
  if (state.history_refresh_threshold !== undefined) {
    var hr = document.getElementById('ctl-history-refresh');
    if (hr) hr.value = state.history_refresh_threshold;
  }
  if (state.input_context_level !== undefined) {
    var ic = document.getElementById('ctl-input-context');
    if (ic) ic.value = state.input_context_level;
  }
  if (state.config_dir !== undefined) {
    var cd = document.getElementById('config-dir-display');
    if (cd) cd.textContent = state.config_dir;
  }

  // Re-render dynamic sections if their data changed
  if (state.hotkeys !== undefined) { CONFIG.hotkeys = state.hotkeys; renderHotkeys(); }
  if (state.stt_presets !== undefined || state.stt_remote_models !== undefined) {
    if (state.stt_presets !== undefined) CONFIG.stt_presets = state.stt_presets;
    if (state.stt_remote_models !== undefined) CONFIG.stt_remote_models = state.stt_remote_models;
    if (state.current_preset_id !== undefined) CONFIG.current_preset_id = state.current_preset_id;
    if (state.current_remote_asr !== undefined) CONFIG.current_remote_asr = state.current_remote_asr;
    renderSttTab();
  }
  if (state.llm_models !== undefined || state.current_llm !== undefined) {
    if (state.llm_models !== undefined) CONFIG.llm_models = state.llm_models;
    if (state.current_llm !== undefined) CONFIG.current_llm = state.current_llm;
    renderLlmTab();
  }
  if (state.enhance_modes !== undefined || state.current_enhance_mode !== undefined) {
    if (state.enhance_modes !== undefined) CONFIG.enhance_modes = state.enhance_modes;
    if (state.current_enhance_mode !== undefined) CONFIG.current_enhance_mode = state.current_enhance_mode;
    renderAiModes();
  }

  // Launcher
  if (state.launcher !== undefined) {
    CONFIG.launcher = state.launcher;
    var launcher = state.launcher;
    if (launcher.enabled !== undefined) setToggle('ctl-launcher-enabled', launcher.enabled);
    if (launcher.usage_learning !== undefined) setToggle('ctl-launcher-usage-learning', launcher.usage_learning);
    if (launcher.switch_english !== undefined) setToggle('ctl-launcher-switch-english', launcher.switch_english);
    var lhk = document.getElementById('ctl-launcher-hotkey');
    if (lhk && launcher.hotkey !== undefined) lhk.textContent = formatHotkey(launcher.hotkey);
    renderLauncherSources();
    var snHk = document.getElementById('ctl-new-snippet-hotkey');
    if (snHk && launcher.new_snippet_hotkey !== undefined) snHk.textContent = formatHotkey(launcher.new_snippet_hotkey);
  }
}

/* ------------------------------------------------------------------ */
/* Boot                                                                */
/* ------------------------------------------------------------------ */

document.addEventListener('DOMContentLoaded', function() {
  _initState(CONFIG);
});
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Close delegate (lazy-created to avoid PyObjC import at module level)
# ---------------------------------------------------------------------------
_PanelCloseDelegate = None


def _get_panel_close_delegate_class():
    global _PanelCloseDelegate
    if _PanelCloseDelegate is None:
        from Foundation import NSObject

        class SettingsWebPanelCloseDelegate(NSObject):
            _panel_ref = None

            def windowWillClose_(self, notification):
                if self._panel_ref is not None:
                    self._panel_ref.close()

        _PanelCloseDelegate = SettingsWebPanelCloseDelegate
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

        class SettingsWebMessageHandler(
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

        _MessageHandler = SettingsWebMessageHandler
    return _MessageHandler


# ---------------------------------------------------------------------------
# Panel class
# ---------------------------------------------------------------------------


class SettingsWebPanel:
    """WKWebView-based settings panel.

    Drop-in replacement for the native PyObjC SettingsPanel, with the same
    public API surface.
    """

    _PANEL_WIDTH = 750
    _PANEL_HEIGHT = 560

    def __init__(self) -> None:
        self._panel = None
        self._webview = None
        self._close_delegate = None
        self._message_handler = None
        self._callbacks: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_visible(self) -> bool:
        """Return True if the panel is currently visible."""
        if self._panel is None:
            return False
        return bool(self._panel.isVisible())

    def show(self, state: dict, callbacks: dict) -> None:
        """Show the settings panel with the given state and callbacks."""
        self._callbacks = callbacks
        self._build_panel(state)

        self._panel.makeKeyAndOrderFront_(None)

        from AppKit import NSApp

        NSApp.activateIgnoringOtherApps_(True)

    def close(self) -> None:
        """Close the panel and release resources."""
        if self._panel is not None:
            self._panel.setDelegate_(None)
            self._close_delegate = None
            self._panel.orderOut_(None)
            self._panel = None
        self._webview = None
        self._message_handler = None
        self._callbacks = None

        from AppKit import NSApp

        NSApp.setActivationPolicy_(1)  # Accessory (statusbar-only)

    def update_state(self, state: dict) -> None:
        """Push new state to JS for incremental DOM update."""
        if self._webview is None or not self.is_visible:
            return
        prepared = self._prepare_state(state)
        payload = json.dumps(prepared, ensure_ascii=False)
        self._webview.evaluateJavaScript_completionHandler_(
            f"_updateState({payload})", None
        )

    # ------------------------------------------------------------------
    # Callbacks from JavaScript
    # ------------------------------------------------------------------

    def _handle_js_message(self, body: dict) -> None:
        """Dispatch messages from JavaScript."""
        if not self.is_visible:
            return

        msg_type = body.get("type", "")
        logger.debug("Handling JS message: type=%s body=%s", msg_type, body)

        if msg_type == "console":
            level = body.get("level", "info")
            message = body.get("message", "")
            getattr(logger, level, logger.info)("[WebView] %s", message)
            return

        if msg_type == "callback":
            name = body.get("name", "")
            args = body.get("args", [])
            if self._callbacks and name in self._callbacks:
                cb = self._callbacks[name]
                try:
                    cb(*args)
                except Exception:
                    logger.exception("Callback %s raised", name)
            else:
                logger.warning("Unknown callback: %s", name)
            return

        logger.warning("Unknown JS message type: %s", msg_type)

    # ------------------------------------------------------------------
    # State preparation (stub for Task 3)
    # ------------------------------------------------------------------

    @staticmethod
    def _prepare_state(state: dict) -> dict:
        """Convert tuple-based state values to JSON-friendly dicts."""
        s = dict(state)
        if "stt_presets" in s:
            s["stt_presets"] = [
                {"id": t[0], "name": t[1], "available": t[2]}
                for t in s["stt_presets"]
            ]
        if "stt_remote_models" in s:
            s["stt_remote_models"] = [
                {"provider": t[0], "model": t[1], "display": t[2]}
                for t in s["stt_remote_models"]
            ]
        if "llm_models" in s:
            s["llm_models"] = [
                {"provider": t[0], "model": t[1], "display": t[2]}
                for t in s["llm_models"]
            ]
        if "current_llm" in s and isinstance(s["current_llm"], (tuple, list)):
            s["current_llm"] = {
                "provider": s["current_llm"][0],
                "model": s["current_llm"][1],
            }
        if "enhance_modes" in s:
            s["enhance_modes"] = [
                {"id": t[0], "name": t[1], "order": t[2]}
                for t in s["enhance_modes"]
            ]
        if s.get("last_tab") == "models":
            s["last_tab"] = "speech"
        return s

    # ------------------------------------------------------------------
    # Panel construction
    # ------------------------------------------------------------------

    def _build_panel(self, state: dict) -> None:
        """Build NSPanel + WKWebView and load the HTML template."""
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
        from Foundation import NSMakeRect
        from WebKit import (
            WKUserContentController,
            WKUserScript,
            WKWebView,
            WKWebViewConfiguration,
        )

        from wenzi.ui.result_window_web import _ensure_edit_menu

        # Enable Cmd+C/V/A via Edit menu in the responder chain
        _ensure_edit_menu()

        NSApp.setActivationPolicy_(0)  # Regular (foreground)

        if self._panel is not None:
            # Reuse existing panel — just reload content
            self._load_html(state)
            return

        # Create NSPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
            NSTitledWindowMask | NSClosableWindowMask | NSResizableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setTitle_("Settings")

        # Close delegate
        delegate_cls = _get_panel_close_delegate_class()
        delegate = delegate_cls.alloc().init()
        delegate._panel_ref = self
        panel.setDelegate_(delegate)
        self._close_delegate = delegate

        # WKWebView with message handler and bridge script
        config = WKWebViewConfiguration.alloc().init()
        content_controller = WKUserContentController.alloc().init()

        handler_cls = _get_message_handler_class()
        handler = handler_cls.alloc().init()
        handler._panel_ref = self
        content_controller.addScriptMessageHandler_name_(handler, "wz")

        # Inject bridge JS at document start
        bridge_script = WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
            _BRIDGE_JS,
            0,  # WKUserScriptInjectionTimeAtDocumentStart
            True,
        )
        content_controller.addUserScript_(bridge_script)

        config.setUserContentController_(content_controller)

        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
            config,
        )
        webview.setAutoresizingMask_(0x12)  # width + height sizable
        webview.setValue_forKey_(False, "drawsBackground")
        panel.contentView().addSubview_(webview)

        self._panel = panel
        self._webview = webview
        self._message_handler = handler

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

        self._load_html(state)

    def _load_html(self, state: dict) -> None:
        """Load the HTML template with the given state into the webview."""
        from Foundation import NSURL

        config_data = self._prepare_state(state)
        html_content = _HTML_TEMPLATE.replace(
            "__CONFIG__", json.dumps(config_data, ensure_ascii=False)
        )
        self._webview.loadHTMLString_baseURL_(
            html_content, NSURL.fileURLWithPath_("/")
        )
