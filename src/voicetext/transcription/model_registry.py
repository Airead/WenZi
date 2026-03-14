"""Preset model registry for VoiceText ASR backends."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPreset:
    """A predefined ASR model configuration."""

    id: str
    display_name: str
    backend: str
    model: Optional[str]
    language: Optional[str]


@dataclass(frozen=True)
class RemoteASRModel:
    """A remote ASR model from a configured provider."""

    provider: str
    model: str
    display_name: str
    base_url: str
    api_key: str


PRESETS = [
    ModelPreset(
        id="funasr-paraformer",
        display_name="FunASR Paraformer (Chinese)",
        backend="funasr",
        model=None,
        language=None,
    ),
    ModelPreset(
        id="apple-speech-ondevice",
        display_name="Apple Speech (On-Device)",
        backend="apple-speech",
        model="on-device",
        language=None,
    ),
    ModelPreset(
        id="apple-speech-server",
        display_name="Apple Speech (Server)",
        backend="apple-speech",
        model="server",
        language=None,
    ),
    ModelPreset(
        id="mlx-whisper-medium",
        display_name="Whisper medium (MLX)",
        backend="mlx-whisper",
        model="mlx-community/whisper-medium",
        language=None,
    ),
    ModelPreset(
        id="mlx-whisper-large-v3-turbo",
        display_name="Whisper large-v3-turbo (MLX)",
        backend="mlx-whisper",
        model="mlx-community/whisper-large-v3-turbo",
        language=None,
    ),
]

PRESET_BY_ID: Dict[str, ModelPreset] = {p.id: p for p in PRESETS}

# Cache backend availability at import time
_backend_available: Dict[str, bool] = {}


def is_backend_available(backend: str) -> bool:
    """Check if a backend's required packages are installed.

    Uses importlib.util.find_spec for a lightweight check that avoids
    triggering heavy initialization (e.g. Metal/GPU setup for mlx).
    """
    if backend in _backend_available:
        return _backend_available[backend]

    import importlib.util

    _BACKEND_MODULES = {
        "funasr": "funasr_onnx",
        "mlx-whisper": "mlx_whisper",
        "apple-speech": "Speech",
    }

    # apple-speech requires macOS
    if backend == "apple-speech":
        import sys
        if sys.platform != "darwin":
            _backend_available[backend] = False
            return False

    module_name = _BACKEND_MODULES.get(backend)
    available = module_name is not None and importlib.util.find_spec(module_name) is not None

    _backend_available[backend] = available
    return available


def resolve_preset_from_config(
    backend: str, model: Optional[str] = None
) -> Optional[str]:
    """Resolve a preset ID from a backend+model combination.

    Returns the preset ID if a match is found, otherwise None.
    """
    backend_norm = backend.lower().replace("_", "-")

    for preset in PRESETS:
        if preset.backend != backend_norm:
            continue
        if backend_norm == "funasr" and preset.model is None and model is None:
            return preset.id
        if backend_norm == "mlx-whisper" and preset.model == model:
            return preset.id
        if backend_norm == "apple-speech" and preset.model == model:
            return preset.id

    return None


def get_model_cache_dir(preset: ModelPreset) -> Path:
    """Get the cache directory path for a preset's model files."""
    home = Path.home()

    if preset.backend == "funasr":
        # FunASR models are cached under modelscope
        from voicetext.config import MODELS

        asr_model = MODELS["asr"]
        short_name = asr_model.split("/")[-1] if "/" in asr_model else asr_model
        return home / ".cache" / "modelscope" / "hub" / "models" / "iic" / short_name

    if preset.backend == "mlx-whisper" and preset.model:
        # HuggingFace models are cached under huggingface hub
        repo_id = preset.model
        # HF cache uses -- as separator: models--org--name
        cache_name = "models--" + repo_id.replace("/", "--")
        return home / ".cache" / "huggingface" / "hub" / cache_name

    return home / ".cache" / "voicetext" / preset.id


def is_model_cached(preset: ModelPreset) -> bool:
    """Check if a preset's model files are already downloaded."""
    if preset.backend == "apple-speech":
        return True  # No model download needed

    if preset.backend == "mlx-whisper" and preset.model:
        try:
            from huggingface_hub import try_to_load_from_cache

            result = try_to_load_from_cache(preset.model, "config.json")
            # Returns file path string if cached, _CACHED_NO_EXIST or None otherwise
            return isinstance(result, str)
        except Exception:
            logger.debug("Could not check HF cache for %s", preset.model)
            return False

    if preset.backend == "funasr":
        cache_dir = get_model_cache_dir(preset)
        if not cache_dir.exists():
            return False
        return (cache_dir / "model_quant.onnx").exists() or (
            cache_dir / "model.onnx"
        ).exists()

    return False


def build_remote_asr_models(providers: Dict[str, Any]) -> List[RemoteASRModel]:
    """Build a list of RemoteASRModel from the asr.providers config section."""
    result = []
    for pname, pcfg in providers.items():
        base_url = pcfg.get("base_url", "")
        api_key = pcfg.get("api_key", "")
        for model in pcfg.get("models", []):
            result.append(
                RemoteASRModel(
                    provider=pname,
                    model=model,
                    display_name=f"{pname} / {model}",
                    base_url=base_url,
                    api_key=api_key,
                )
            )
    return result
