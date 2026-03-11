"""Configuration for VoiceText app."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = os.path.join("~", ".config", "VoiceText")
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.json")
DEFAULT_ENHANCE_MODES_DIR = os.path.join(DEFAULT_CONFIG_DIR, "enhance_modes")

DEFAULT_CONFIG: Dict[str, Any] = {
    "hotkey": "fn",
    "audio": {
        "sample_rate": 16000,
        "block_ms": 20,
        "device": None,
        "max_session_bytes": 20971520,
        "silence_rms": 20,
    },
    "asr": {
        "backend": "funasr",
        "use_vad": True,
        "use_punc": True,
        "language": "zh",
        "model": None,
        "preset": None,
        "temperature": 0.0,
    },
    "output": {
        "method": "auto",
        "append_newline": False,
        "preview": True,
    },
    "ai_enhance": {
        "enabled": False,
        "mode": "proofread",
        "default_provider": "ollama",
        "default_model": "qwen2.5:7b",
        "providers": {
            "ollama": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "ollama",
                "models": ["qwen2.5:7b"],
            },
        },
        "thinking": False,
        "timeout": 30,
        "vocabulary": {
            "enabled": False,
            "top_k": 5,
            "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
            "build_timeout": 600,
        },
    },
    "logging": {
        "level": "INFO",
    },
}

# FunASR model config (aligned with vocotype-cli)
MODEL_REVISION = os.environ.get("FUNASR_MODEL_REVISION", "v2.0.5")

MODELS = {
    "asr": os.environ.get(
        "FUNASR_ASR_MODEL",
        "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx",
    ),
    "vad": os.environ.get(
        "FUNASR_VAD_MODEL",
        "iic/speech_fsmn_vad_zh-cn-16k-common-onnx",
    ),
    "punc": os.environ.get(
        "FUNASR_PUNC_MODEL",
        "iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx",
    ),
}


def _merge_dict(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _ensure_default_config(config_path: str) -> None:
    """Create default config file if it does not exist."""
    config_dir = os.path.dirname(config_path)
    os.makedirs(config_dir, exist_ok=True)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        f.write("\n")

    logger.info("Created default config: %s", config_path)


def save_config(config: Dict[str, Any], path: Optional[str] = None) -> None:
    """Save configuration to a JSON file.

    If no path is given, uses ~/.config/VoiceText/config.json.
    """
    if not path:
        path = DEFAULT_CONFIG_PATH

    expanded = os.path.expanduser(path)
    config_dir = os.path.dirname(expanded)
    os.makedirs(config_dir, exist_ok=True)

    with open(expanded, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")

    logger.info("Config saved to %s", expanded)


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load configuration from a JSON file.

    If no path is given, uses ~/.config/VoiceText/config.json.
    If the file does not exist, creates it with default values.
    """
    if not path:
        path = DEFAULT_CONFIG_PATH

    expanded = os.path.expanduser(path)

    if not os.path.exists(expanded):
        _ensure_default_config(expanded)
        return dict(DEFAULT_CONFIG)

    with open(expanded, "r", encoding="utf-8") as f:
        overrides = json.load(f)

    return _merge_dict(DEFAULT_CONFIG, overrides)
