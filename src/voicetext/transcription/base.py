"""Abstract transcriber interface and factory."""

from __future__ import annotations

import abc
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class BaseTranscriber(abc.ABC):
    """Base interface for speech-to-text backends."""

    skip_punc: bool = False

    @property
    @abc.abstractmethod
    def initialized(self) -> bool:
        """Whether the model has been loaded."""

    @property
    @abc.abstractmethod
    def model_display_name(self) -> str:
        """Human-readable model name for display in the UI."""

    @abc.abstractmethod
    def initialize(self) -> None:
        """Load models. Call once at startup."""

    @abc.abstractmethod
    def transcribe(self, wav_data: bytes) -> str:
        """Transcribe WAV audio bytes to text."""

    @abc.abstractmethod
    def cleanup(self) -> None:
        """Release model resources. After this, initialized should return False."""

    @staticmethod
    def wav_duration_seconds(wav_data: bytes) -> float:
        """Calculate audio duration in seconds from WAV data."""
        import io
        import wave

        try:
            with wave.open(io.BytesIO(wav_data), "rb") as wf:
                return wf.getnframes() / wf.getframerate()
        except Exception:
            return 0.0


def create_transcriber(
    backend: str = "funasr",
    *,
    use_vad: bool = False,
    use_punc: bool = True,
    language: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> BaseTranscriber:
    """Create a transcriber for the given backend.

    Args:
        backend: "funasr", "mlx-whisper", "whisper-api", or "apple-speech".
        use_vad: Enable voice activity detection (funasr only).
        use_punc: Enable punctuation restoration.
        language: Language hint (mlx-whisper / whisper-api / apple-speech, e.g. "zh", "en").
        model: Override default model name/path. For apple-speech: "on-device" or "server".
        temperature: Decoding temperature (mlx-whisper / whisper-api).
        base_url: API base URL (whisper-api only).
        api_key: API key (whisper-api only).
    """
    backend = backend.lower().replace("_", "-")

    if backend == "funasr":
        from .funasr import FunASRTranscriber
        return FunASRTranscriber(use_vad=use_vad, use_punc=use_punc)

    if backend in ("mlx-whisper", "mlx", "whisper"):
        from .mlx import MLXWhisperTranscriber
        return MLXWhisperTranscriber(
            language=language, model=model, use_punc=use_punc, temperature=temperature,
        )

    if backend in ("whisper-api", "groq"):
        from .whisper_api import WhisperAPITranscriber
        return WhisperAPITranscriber(
            base_url=base_url, api_key=api_key, model=model,
            language=language, temperature=temperature,
        )

    if backend in ("apple-speech", "apple"):
        from .apple import AppleSpeechTranscriber
        return AppleSpeechTranscriber(
            language=language or "zh",
            on_device=(model == "on-device"),
        )

    raise ValueError(
        f"Unknown ASR backend: {backend!r}. "
        "Use 'funasr', 'mlx-whisper', 'whisper-api', or 'apple-speech'."
    )
