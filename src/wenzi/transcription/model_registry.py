"""Preset model registry for WenZi ASR backends."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPreset:
    """A predefined ASR model configuration."""

    id: str
    display_name: str
    backend: str
    model: str | None
    language: str | None


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
        backend="apple",
        model="on-device",
        language=None,
    ),
    ModelPreset(
        id="apple-speech-server",
        display_name="Apple Speech (Server)",
        backend="apple",
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
    ModelPreset(
        id="sherpa-zipformer-zh",
        display_name="Zipformer Chinese 14M (Sherpa)",
        backend="sherpa-onnx",
        model="zipformer-zh",
        language="zh",
    ),
    ModelPreset(
        id="sherpa-paraformer-zh",
        display_name="Paraformer Bilingual (Sherpa)",
        backend="sherpa-onnx",
        model="paraformer-zh",
        language="zh",
    ),
]

PRESET_BY_ID: dict[str, ModelPreset] = {p.id: p for p in PRESETS}

# Cache backend availability at import time
_backend_available: dict[str, bool] = {}


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
        "apple": "Speech",
        "sherpa-onnx": "sherpa_onnx",
    }

    # apple requires macOS
    if backend == "apple":
        import sys
        if sys.platform != "darwin":
            _backend_available[backend] = False
            return False

    module_name = _BACKEND_MODULES.get(backend)
    available = module_name is not None and importlib.util.find_spec(module_name) is not None

    _backend_available[backend] = available
    return available


def resolve_preset_from_config(
    backend: str, model: str | None = None
) -> str | None:
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
        if backend_norm == "apple" and preset.model == model:
            return preset.id
        if backend_norm == "sherpa-onnx" and preset.model == model:
            return preset.id

    return None


def get_model_cache_dir(preset: ModelPreset) -> Path:
    """Get the cache directory path for a preset's model files."""
    if preset.backend == "funasr":
        # FunASR models are cached under modelscope
        from modelscope.utils.file_utils import get_modelscope_cache_dir

        from wenzi.config import MODELS

        asr_model = MODELS["asr"]
        short_name = asr_model.split("/")[-1] if "/" in asr_model else asr_model
        return Path(get_modelscope_cache_dir()) / "models" / "iic" / short_name

    if preset.backend == "mlx-whisper" and preset.model:
        # HuggingFace models are cached under huggingface hub
        from huggingface_hub import constants as hf_constants

        repo_id = preset.model
        # HF cache uses -- as separator: models--org--name
        cache_name = "models--" + repo_id.replace("/", "--")
        return Path(hf_constants.HF_HUB_CACHE) / cache_name

    if preset.backend == "sherpa-onnx" and preset.model:
        from .sherpa import _get_model_dir
        return _get_model_dir(preset.model)

    from wenzi.config import DEFAULT_CACHE_DIR
    return Path(DEFAULT_CACHE_DIR).expanduser() / preset.id


def is_model_cached(preset: ModelPreset) -> bool:
    """Check if a preset's model files are already downloaded."""
    if preset.backend == "apple":
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

    if preset.backend == "sherpa-onnx" and preset.model:
        cache_dir = get_model_cache_dir(preset)
        return cache_dir.exists() and any(cache_dir.glob("*.onnx"))

    return False


def find_fallback_preset() -> ModelPreset | None:
    """Find the best non-Apple preset to fall back to.

    Prefers already-cached models (no download), then any available backend.
    Returns None if no alternative is available.
    """
    # First pass: cached models (no download needed)
    for preset in PRESETS:
        if preset.backend == "apple":
            continue
        if is_backend_available(preset.backend) and is_model_cached(preset):
            return preset
    # Second pass: any available backend (may require download)
    for preset in PRESETS:
        if preset.backend == "apple":
            continue
        if is_backend_available(preset.backend):
            return preset
    return None


def clear_model_cache(preset: ModelPreset) -> bool:
    """Delete cached model files for a preset.

    Returns True if a cache directory was found and removed.
    """
    import shutil

    cache_dir = get_model_cache_dir(preset)
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        logger.info("Cleared model cache: %s", cache_dir)
        return True
    return False


def build_remote_asr_models(providers: dict[str, Any]) -> list[RemoteASRModel]:
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
