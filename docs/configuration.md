# Configuration

Default config path: `~/.config/VoiceText/config.json`. Pass a JSON config file as a command-line argument to override. Only the fields you want to change are needed; everything else uses defaults.

## Full Default Configuration

```json
{
  "hotkey": "fn",
  "audio": {
    "sample_rate": 16000,
    "block_ms": 20,
    "device": null,
    "max_session_bytes": 20971520,
    "silence_rms": 20
  },
  "asr": {
    "backend": "funasr",
    "use_vad": true,
    "use_punc": true,
    "language": "zh",
    "model": null,
    "preset": null,
    "temperature": 0.0,
    "default_provider": null,
    "default_model": null,
    "providers": {}
  },
  "output": {
    "method": "auto",
    "append_newline": false,
    "preview": true
  },
  "ai_enhance": {
    "enabled": false,
    "mode": "proofread",
    "default_provider": "ollama",
    "default_model": "qwen2.5:7b",
    "providers": {
      "ollama": {
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "models": ["qwen2.5:7b"]
      }
    },
    "thinking": false,
    "timeout": 30,
    "vocabulary": {
      "enabled": false,
      "top_k": 5,
      "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
      "build_timeout": 600,
      "auto_build": true,
      "auto_build_threshold": 10
    },
    "conversation_history": {
      "enabled": false,
      "max_entries": 10
    }
  },
  "clipboard_enhance": {
    "hotkey": "ctrl+cmd+v",
    "output": "clipboard"
  },
  "logging": {
    "level": "INFO"
  }
}
```

## Options

### General

| Key | Default | Description |
|-----|---------|-------------|
| `hotkey` | `"fn"` | Trigger key. Supported: `fn`, `f1`–`f12`, `esc`, `space`, `cmd`, `ctrl`, `alt`, `shift` |

### Audio

| Key | Default | Description |
|-----|---------|-------------|
| `audio.sample_rate` | `16000` | Audio sample rate in Hz |
| `audio.block_ms` | `20` | Recording block size in milliseconds |
| `audio.device` | `null` | Audio input device (null = system default) |
| `audio.max_session_bytes` | `20971520` | Max recording size (~20 MB) |
| `audio.silence_rms` | `20` | RMS threshold below which audio is considered silence |

### ASR

| Key | Default | Description |
|-----|---------|-------------|
| `asr.backend` | `"funasr"` | ASR backend: `funasr`, `mlx-whisper`, or `whisper-api` |
| `asr.use_vad` | `true` | Enable voice activity detection (prevents hallucination on silence) |
| `asr.use_punc` | `true` | Enable automatic punctuation restoration |
| `asr.language` | `"zh"` | Language code (used by MLX-Whisper and Whisper API) |
| `asr.model` | `null` | Model identifier (e.g. `mlx-community/whisper-small`) |
| `asr.preset` | `null` | Preset ID from model registry (e.g. `mlx-whisper-small`) |
| `asr.temperature` | `0.0` | Decoding temperature (MLX-Whisper and Whisper API) |
| `asr.default_provider` | `null` | Default remote ASR provider name (e.g. `"groq"`) |
| `asr.default_model` | `null` | Default remote ASR model (e.g. `"whisper-large-v3"`) |
| `asr.providers` | `{}` | Remote ASR providers (same format as `ai_enhance.providers`) |

### Output

| Key | Default | Description |
|-----|---------|-------------|
| `output.method` | `"auto"` | Text injection method: `auto`, `clipboard`, or `applescript` |
| `output.append_newline` | `false` | Append a newline after typed text |
| `output.preview` | `true` | Show floating preview panel for reviewing results before input |

### AI Enhancement

| Key | Default | Description |
|-----|---------|-------------|
| `ai_enhance.enabled` | `false` | Enable AI text enhancement |
| `ai_enhance.mode` | `"proofread"` | Enhancement mode: `off`, `proofread`, `translate_en`, `commandline_master`, or custom mode IDs |
| `ai_enhance.default_provider` | `"ollama"` | Default LLM provider name |
| `ai_enhance.default_model` | `"qwen2.5:7b"` | Default LLM model |
| `ai_enhance.thinking` | `false` | Enable extended thinking for supported models |
| `ai_enhance.timeout` | `30` | LLM request timeout in seconds |

### Vocabulary Retrieval

| Key | Default | Description |
|-----|---------|-------------|
| `ai_enhance.vocabulary.enabled` | `false` | Enable vocabulary-based retrieval during enhancement |
| `ai_enhance.vocabulary.top_k` | `5` | Number of vocabulary entries to retrieve per query |
| `ai_enhance.vocabulary.embedding_model` | `"paraphrase-multilingual-MiniLM-L12-v2"` | Embedding model for vocabulary index |
| `ai_enhance.vocabulary.build_timeout` | `600` | Per-batch LLM timeout for vocabulary building (seconds) |
| `ai_enhance.vocabulary.auto_build` | `true` | Enable automatic vocabulary building after corrections accumulate |
| `ai_enhance.vocabulary.auto_build_threshold` | `10` | Number of corrections to trigger an automatic build |

### Conversation History

| Key | Default | Description |
|-----|---------|-------------|
| `ai_enhance.conversation_history.enabled` | `false` | Enable conversation history context injection |
| `ai_enhance.conversation_history.max_entries` | `10` | Number of recent confirmed entries to inject |

### Clipboard Enhancement

| Key | Default | Description |
|-----|---------|-------------|
| `clipboard_enhance.hotkey` | `"ctrl+cmd+v"` | Hotkey to trigger clipboard AI enhancement (modifier+key format) |
| `clipboard_enhance.output` | `"clipboard"` | Output method: `"clipboard"` places result on clipboard |

### Logging

| Key | Default | Description |
|-----|---------|-------------|
| `logging.level` | `"INFO"` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FUNASR_ASR_MODEL` | `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx` | ASR model ID |
| `FUNASR_VAD_MODEL` | `iic/speech_fsmn_vad_zh-cn-16k-common-onnx` | VAD model ID |
| `FUNASR_PUNC_MODEL` | `iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx` | Punctuation model ID |
| `FUNASR_MODEL_REVISION` | `v2.0.5` | Model revision |
| `OMP_NUM_THREADS` | `8` | ONNX runtime thread count |
