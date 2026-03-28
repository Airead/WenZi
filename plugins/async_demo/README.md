# Async Demo

Demonstration and verification of async/await scripting features in WenZi.

## Features

This plugin registers **7 launcher commands** and **1 search source** to showcase WenZi's async scripting capabilities.

### Launcher Commands

Open the launcher and type the command name to run:

| Command | Description |
|---------|-------------|
| `async-sleep` | Async sleep with real-time progress updates. Accepts optional seconds argument (default: 3.0) |
| `async-fetch` | Fetch a URL asynchronously, save response to a temp file and open it. Accepts optional URL argument |
| `async-timer` | Start a repeating async timer (ticks every 2s). Press `Ctrl+Cmd+T` to stop |
| `async-concurrent` | Run 3 async tasks in parallel and report total elapsed time |
| `async-error` | Raise a `RuntimeError` — check the log viewer for error output |
| `async-run` | Submit a background coroutine via `wz.run()` |
| `async-pick` | Show a chooser with options and process selection asynchronously |

### Event Listener

| Event | Description |
|-------|-------------|
| `transcription_done` | Fires when a voice transcription completes, logging and notifying the result |

### Search Source

| Source | Prefix | Description |
|--------|--------|-------------|
| `async-search` | `as` | Simulates a network search with 0.5s delay, returns fuzzy-matched results |

## Usage

1. Open the WenZi launcher
2. Type any of the command names above (e.g., `async-sleep`)
3. Press Enter to execute
4. For the search source, type `as ` followed by your query

## Requirements

- WenZi ≥ 0.1.12
- No external dependencies
