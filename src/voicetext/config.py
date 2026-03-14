"""Configuration for VoiceText app."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = os.path.join("~", ".config", "VoiceText")
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.json")
DEFAULT_ENHANCE_MODES_DIR = os.path.join(DEFAULT_CONFIG_DIR, "enhance_modes")

DEFAULT_CONFIG: Dict[str, Any] = {
    "hotkeys": {"fn": True},
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
        "default_provider": None,
        "default_model": None,
        "providers": {},
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
        "connection_timeout": 10,
        "max_retries": 2,
        "vocabulary": {
            "enabled": False,
            "top_k": 5,
            "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
            "build_timeout": 600,
            "auto_build": True,
            "auto_build_threshold": 10,
        },
        "conversation_history": {
            "enabled": False,
            "max_entries": 10,
        },
    },
    "clipboard_enhance": {
        "hotkey": "ctrl+cmd+v",
    },
    "feedback": {
        "sound_enabled": True,
        "sound_volume": 0.4,
        "visual_indicator": True,
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
    os.chmod(config_path, 0o600)

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
    os.chmod(expanded, 0o600)

    logger.info("Config saved to %s", expanded)


def validate_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Validate config values and replace invalid ones with defaults.

    Logs a warning for each invalid value found.  Never raises — the app
    should always be able to start even with a broken config file.
    """
    rules = [
        # (dotted_path, expected_type, constraint, default_value)
        ("audio.sample_rate", int, lambda v: v > 0, DEFAULT_CONFIG["audio"]["sample_rate"]),
        ("audio.block_ms", int, lambda v: v > 0, DEFAULT_CONFIG["audio"]["block_ms"]),
        ("audio.max_session_bytes", int, lambda v: v > 0, DEFAULT_CONFIG["audio"]["max_session_bytes"]),
        ("audio.silence_rms", (int, float), lambda v: v >= 0, DEFAULT_CONFIG["audio"]["silence_rms"]),
        ("feedback.sound_enabled", bool, None, DEFAULT_CONFIG["feedback"]["sound_enabled"]),
        ("feedback.sound_volume", (int, float), lambda v: 0.0 <= v <= 1.0, DEFAULT_CONFIG["feedback"]["sound_volume"]),
        ("feedback.visual_indicator", bool, None, DEFAULT_CONFIG["feedback"]["visual_indicator"]),
        ("output.method", str, lambda v: v in {"auto", "paste", "clipboard"}, DEFAULT_CONFIG["output"]["method"]),
        ("output.append_newline", bool, None, DEFAULT_CONFIG["output"]["append_newline"]),
        ("asr.backend", str, lambda v: v in {"funasr", "mlx_whisper", "apple", "api"},
         DEFAULT_CONFIG["asr"]["backend"]),
        ("asr.language", str, lambda v: len(v) > 0, DEFAULT_CONFIG["asr"]["language"]),
        ("logging.level", str, lambda v: v in {"DEBUG", "INFO", "WARNING", "ERROR"},
         DEFAULT_CONFIG["logging"]["level"]),
        ("ai_enhance.timeout", (int, float), lambda v: v > 0, DEFAULT_CONFIG["ai_enhance"]["timeout"]),
        ("ai_enhance.connection_timeout", (int, float), lambda v: v > 0,
         DEFAULT_CONFIG["ai_enhance"]["connection_timeout"]),
        ("ai_enhance.max_retries", int, lambda v: v >= 0, DEFAULT_CONFIG["ai_enhance"]["max_retries"]),
    ]

    for path, expected_type, constraint, default in rules:
        keys = path.split(".")
        # Navigate to the parent dict
        parent = config
        valid = True
        for key in keys[:-1]:
            if isinstance(parent, dict) and key in parent:
                parent = parent[key]
            else:
                valid = False
                break

        if not valid:
            continue

        leaf_key = keys[-1]
        if leaf_key not in parent:
            continue

        value = parent[leaf_key]

        # Type check
        if not isinstance(value, expected_type):
            logger.warning(
                "Config %s: invalid type %s (expected %s), using default: %r",
                path, type(value).__name__, expected_type, default,
            )
            parent[leaf_key] = default
            continue

        # Constraint check
        if constraint is not None:
            try:
                if not constraint(value):
                    logger.warning(
                        "Config %s: invalid value %r, using default: %r",
                        path, value, default,
                    )
                    parent[leaf_key] = default
            except Exception:
                logger.warning(
                    "Config %s: validation error for %r, using default: %r",
                    path, value, default,
                )
                parent[leaf_key] = default

    return config


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

    config = _merge_dict(DEFAULT_CONFIG, overrides)

    # Migrate legacy "hotkey" (string) → "hotkeys" (dict)
    if "hotkey" in config:
        old = config.pop("hotkey")
        if isinstance(old, str) and "hotkeys" not in overrides:
            config["hotkeys"] = {old: True}
            save_config(config, path)
            logger.info("Migrated hotkey %r → hotkeys dict", old)

    validate_config(config)
    return config
