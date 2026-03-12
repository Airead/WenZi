# VoiceText

A macOS menubar speech-to-text application. Hold a hotkey to record, release to transcribe and automatically type the result into the active application.

- **Offline-first**: Uses [FunASR](https://github.com/modelscope/FunASR) ONNX models by default — no cloud dependency
- **Multi-backend**: Supports FunASR (Chinese-optimized) and [MLX-Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) (99 languages, Apple Silicon GPU)
- **AI Enhancement**: Optional LLM-powered text proofreading, formatting, completion, and translation via OpenAI-compatible APIs
- **Vocabulary Retrieval**: Personal vocabulary index with embedding-based retrieval to improve correction of proper nouns and domain terms, with automatic background building
- **Conversation History**: Injects recent confirmed outputs into the AI prompt for topic continuity and consistent entity resolution
- **Lightweight**: Runs as a menubar-only app (hidden from Dock)

## Quick Start

### Option 1: Build as macOS App (Recommended)

For daily use, build VoiceText as a native macOS app bundle — no terminal needed after installation.

```bash
git clone <repo-url>
cd VoiceText
uv sync
uv run pyinstaller VoiceText.spec
```

The built `VoiceText.app` will be in the `dist/` directory. Drag it to `/Applications` and launch like any other app.

> Alternatively, build with py2app: `uv run python setup.py py2app`

### Option 2: Run from Source (Development)

If you want to modify the code or debug, run directly with `uv`:

```bash
git clone https://github.com/Airead/VoiceText
cd VoiceText
uv sync

# Run
uv run python -m voicetext

# Run with a custom config file
uv run python -m voicetext path/to/config.json
```

### Requirements

- macOS (Apple Silicon recommended for MLX-Whisper)
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (recommended package manager)

ASR models will be downloaded automatically on first launch (FunASR ~500 MB cached in `~/.cache/modelscope/`, MLX-Whisper models cached in `~/.cache/huggingface/`).

### Permissions

On first launch the app will prompt for:

- **Microphone** — for audio recording
- **Accessibility** — for typing text into other applications

## Usage

1. The app starts with a **VT** icon in the menubar.
2. Hold the hotkey (default: `fn`) to record.
3. Release to transcribe — the recognized text is typed into the active window.

When **Preview** is enabled, a floating panel shows the result for review before input. In the preview panel you can:

- Edit the text before confirming
- Use `⌘1` ~ `⌘9` to quickly switch AI enhancement modes and re-enhance with the selected mode

### Menubar Controls

- **Model**: Switch between FunASR and MLX-Whisper models at runtime (models download on first use with progress display)
- **AI Enhance**: Select enhancement mode (proofread, translate, commandline, custom modes, etc.) and add new modes
- **Preview**: Toggle the floating preview panel for reviewing and editing results before input
- **Vocabulary (N)**: Toggle vocabulary retrieval for improving correction of proper nouns and domain terms. Entry count shown in title
- **Conversation History**: Toggle conversation history injection for topic continuity
- **AI Settings**: Configure provider, model, thinking mode, build vocabulary, auto build toggle, and manage providers
- **Debug**: Log level, debug toggles (print prompt, print request body), and copy log path
- **Show Config...**: Open the current config file
- **Usage Stats**: View cumulative and today's usage statistics, plus stored data counts (conversations, corrections, vocabulary entries)
- **About VoiceText**: Show version and build info

## ASR Backends

### FunASR (default)

Offline Chinese speech recognition using ONNX models. Includes voice activity detection (VAD) and automatic punctuation restoration.

### MLX-Whisper

OpenAI Whisper running on Apple Metal GPU via MLX. Supports 99 languages with multiple model sizes:

| Preset | Model | Size |
|--------|-------|------|
| Whisper tiny | `mlx-community/whisper-tiny` | ~75 MB |
| Whisper base | `mlx-community/whisper-base` | ~140 MB |
| Whisper small | `mlx-community/whisper-small` | ~460 MB |
| Whisper medium | `mlx-community/whisper-medium` | ~1.5 GB |
| Whisper large-v3-turbo | `mlx-community/whisper-large-v3-turbo` | ~1.6 GB |

## AI Text Enhancement

Optional post-processing of transcribed text using any OpenAI-compatible API (cloud or local like [Ollama](https://ollama.ai)).

### Enhancement Modes

| Mode | Description |
|------|-------------|
| Off | No enhancement |
| Proofread (纠错润色) | Fix typos, grammar, and punctuation |
| Translate to English (翻译为英文) | Translate Chinese text to English |
| Commandline Master (命令行大神) | Convert natural language to shell commands |

Additional modes can be added via Markdown files or the **AI Enhance > Add Mode...** menu — see [docs/enhance-modes.md](docs/enhance-modes.md).

### Multi-Provider Support

Configure multiple LLM providers and switch between them at runtime from the menubar. Each provider supports:

- Custom base URL and API key
- Multiple models per provider
- Provider-specific `extra_body` parameters
- Optional extended thinking mode
- Configurable timeout

Providers can be added, removed, and verified directly from the menubar UI. See [docs/provider-model-guide.md](docs/provider-model-guide.md) for step-by-step setup instructions covering both GUI and config file approaches.

### Vocabulary Retrieval

VoiceText can build a personal vocabulary index from your correction history to improve recognition of proper nouns, technical terms, and domain-specific words. When enabled, relevant vocabulary entries are retrieved via embedding similarity and injected into the LLM prompt as context.

The vocabulary supports both automatic and manual building:

- **Accumulate corrections**: Edit AI-enhanced text in the preview window; each edit is logged to `corrections.jsonl`
- **Auto build**: By default, vocabulary is automatically rebuilt in the background after every 10 corrections. A macOS notification shows the result. Toggle via **AI Settings > Auto Build Vocabulary**
- **Manual build**: Click **AI Settings > Build Vocabulary...** to trigger a build on demand. Supports incremental builds — only new corrections since the last build are processed
- **Toggle**: Click **Vocabulary (N)** in the menubar to enable/disable retrieval during enhancement. The entry count is shown in the menu title
- Uses `fastembed` with a multilingual embedding model for local, offline semantic matching

See [docs/vocabulary-embedding-retrieval.md](docs/vocabulary-embedding-retrieval.md) for detailed design and motivation.

### Conversation History

VoiceText can inject recent conversation history into the AI enhancement prompt, enabling the LLM to understand the current topic and resolve recurring entities consistently. For example, if the user confirmed "萍萍" in a previous turn, subsequent ASR errors like "平平" can be correctly resolved.

- **Toggle**: Click **Conversation History** in the menubar to enable/disable
- Only preview-confirmed records (where the user reviewed and approved the output) are injected — ensuring data quality
- Token-efficient format: identical ASR/output shown once, corrections shown with arrow notation (e.g., `平平 → 萍萍`)

See [docs/conversation-history-enhancement.md](docs/conversation-history-enhancement.md) for detailed design and motivation.

## Configuration

Default config path: `~/.config/VoiceText/config.json`. Pass a JSON config file as a command-line argument to override. Only the fields you want to change are needed; everything else uses defaults.

See [docs/configuration.md](docs/configuration.md) for the full default configuration, all available options, and environment variables.

## Testing

```bash
uv run pytest
```

## Logging

Logs are saved to `~/Library/Logs/VoiceText/voicetext.log` with rotation (5 MB per file, 3 backups). The log path can be copied from the menubar menu.

## Project Structure

```
src/voicetext/
├── app.py              # Menubar application (rumps)
├── config.py           # Configuration loading and defaults
├── hotkey.py           # Global hotkey listener (Quartz / pynput)
├── recorder.py         # Audio recording (sounddevice)
├── transcriber.py      # Abstract transcriber interface and factory
├── transcriber_funasr.py  # FunASR ONNX backend
├── transcriber_mlx.py     # MLX-Whisper backend
├── model_registry.py   # Model preset registry and cache management
├── auto_vocab_builder.py # Automatic vocabulary building triggered by correction count
├── enhancer.py         # AI text enhancement (OpenAI-compatible API)
├── mode_loader.py      # Enhancement mode definitions and file loading
├── result_window.py    # Floating preview panel for ASR/AI results
├── vocabulary.py       # Vocabulary embedding index and retrieval
├── vocabulary_builder.py # Extract vocabulary from correction logs via LLM
├── vocab_build_window.py # Vocabulary build progress UI
├── conversation_history.py # Conversation history recording and context injection
├── correction_log.py   # User-edited text logging (JSONL)
├── usage_stats.py      # Usage statistics with cumulative and daily breakdown
├── punctuation.py      # Punctuation restoration (CT-Transformer)
└── input.py            # Text injection (clipboard / AppleScript)
```

## Documentation

- [Configuration](docs/configuration.md) — full default config, all options, and environment variables
- [AI Enhancement Modes Guide](docs/enhance-modes.md) — how to customize and create enhancement modes
- [Enhancement Mode Examples](docs/enhance-mode-examples.md) — ready-to-use mode templates for inspiration
- [Vocabulary Embedding Retrieval](docs/vocabulary-embedding-retrieval.md) — design and motivation of the vocabulary retrieval system
- [Conversation History Enhancement](docs/conversation-history-enhancement.md) — how conversation history improves AI enhancement accuracy

## License

MIT
