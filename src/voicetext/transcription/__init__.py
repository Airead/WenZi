"""Transcription subpackage — speech-to-text backends and model registry."""

from .base import BaseTranscriber, create_transcriber
from .model_registry import (
    PRESET_BY_ID,
    PRESETS,
    ModelPreset,
    RemoteASRModel,
    build_remote_asr_models,
    get_model_cache_dir,
    is_backend_available,
    is_model_cached,
    resolve_preset_from_config,
)
from .punctuation import PunctuationRestorer

__all__ = [
    "BaseTranscriber",
    "create_transcriber",
    "PRESET_BY_ID",
    "PRESETS",
    "ModelPreset",
    "RemoteASRModel",
    "build_remote_asr_models",
    "get_model_cache_dir",
    "is_backend_available",
    "is_model_cached",
    "resolve_preset_from_config",
    "PunctuationRestorer",
]
