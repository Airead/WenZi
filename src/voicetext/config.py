"""Configuration for VoiceText app."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional


DEFAULT_CONFIG: Dict[str, Any] = {
    "hotkey": "fn",
    "audio": {
        "sample_rate": 16000,
        "block_ms": 20,
        "device": None,
        "max_session_bytes": 20 * 1024 * 1024,
        "silence_rms": 20,
    },
    "asr": {
        "use_vad": True,
        "use_punc": True,
    },
    "output": {
        "method": "auto",
        "append_newline": False,
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


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load configuration, optionally merging a JSON config file."""
    config = dict(DEFAULT_CONFIG)
    if not path:
        return config

    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        raise FileNotFoundError(f"Config file not found: {expanded}")

    with open(expanded, "r", encoding="utf-8") as f:
        overrides = json.load(f)

    return _merge_dict(config, overrides)
