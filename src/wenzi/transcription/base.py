"""Abstract transcriber interface and factory."""

from __future__ import annotations

import abc
import logging
from collections.abc import Callable

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
    def transcribe(self, wav_data: bytes, *, hotwords: list[str] | None = None) -> str:
        """Transcribe WAV audio bytes to text."""

    @abc.abstractmethod
    def cleanup(self) -> None:
        """Release model resources. After this, initialized should return False."""

    # ── Streaming interface (optional) ──────────────────────────────

    @property
    def supports_streaming(self) -> bool:
        """Whether this backend supports real-time streaming transcription."""
        return False

    def start_streaming(self, on_partial: Callable[[str, bool], None]) -> None:
        """Begin a streaming recognition session.

        Args:
            on_partial: Callback invoked with (text, is_final) for each partial result.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support streaming")

    def feed_audio(self, samples: bytes) -> None:
        """Feed a chunk of raw int16 PCM audio bytes (little-endian, mono, 16 kHz)."""
        raise NotImplementedError(f"{type(self).__name__} does not support streaming")

    def stop_streaming(self) -> str:
        """End audio input and return the final transcription text."""
        raise NotImplementedError(f"{type(self).__name__} does not support streaming")

    def cancel_streaming(self) -> None:
        """Cancel the current streaming session without waiting for a final result."""
        raise NotImplementedError(f"{type(self).__name__} does not support streaming")

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


# Whisper API prompt token limit is 224; leave margin for tokenizer variance.
_MAX_PROMPT_CHARS = 200


def build_hotwords_prompt(hotwords: list[str]) -> str:
    """Join hotwords into a prompt string, truncating to fit token limit.

    Shared by MLX Whisper and Whisper API backends.
    """
    parts: list[str] = []
    total = 0
    for word in hotwords:
        added = len(word) + (2 if parts else 0)  # ", " separator
        if total + added > _MAX_PROMPT_CHARS:
            break
        parts.append(word)
        total += added
    return ", ".join(parts)


def create_transcriber(
    backend: str = "funasr",
    *,
    use_vad: bool = False,
    use_punc: bool = True,
    language: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    hotwords: list[str] | None = None,
) -> BaseTranscriber:
    """Create a transcriber for the given backend.

    Args:
        backend: "funasr", "mlx-whisper", "whisper-api", "apple", or "sherpa-onnx".
        use_vad: Enable voice activity detection (funasr only).
        use_punc: Enable punctuation restoration.
        language: Language hint (mlx-whisper / whisper-api / apple, e.g. "zh", "en").
        model: Override default model name/path. For apple: "on-device" or "server".
        temperature: Decoding temperature (mlx-whisper / whisper-api).
        base_url: API base URL (whisper-api only).
        api_key: API key (whisper-api only).
        hotwords: Vocabulary terms for ASR boosting (sherpa-onnx / whisper-api / mlx-whisper).
    """
    backend = backend.lower().replace("_", "-")

    if backend == "funasr":
        from .funasr import FunASRTranscriber
        return FunASRTranscriber(use_vad=use_vad, use_punc=use_punc)

    if backend in ("mlx-whisper", "mlx", "whisper"):
        from .mlx import MLXWhisperTranscriber
        return MLXWhisperTranscriber(
            language=language, model=model, use_punc=use_punc,
            temperature=temperature, hotwords=hotwords,
        )

    if backend in ("whisper-api", "groq"):
        from .whisper_api import WhisperAPITranscriber
        return WhisperAPITranscriber(
            base_url=base_url, api_key=api_key, model=model,
            language=language, temperature=temperature,
            hotwords=hotwords,
        )

    if backend in ("apple-speech", "apple"):
        from .apple import AppleSpeechTranscriber
        return AppleSpeechTranscriber(
            language=language or "zh",
            on_device=(model == "on-device"),
        )

    if backend in ("sherpa", "sherpa-onnx"):
        from .sherpa import SherpaOnnxTranscriber
        return SherpaOnnxTranscriber(
            model=model or "zipformer-zh",
            language=language,
            hotwords=hotwords,
        )

    raise ValueError(
        f"Unknown ASR backend: {backend!r}. "
        "Use 'funasr', 'mlx-whisper', 'whisper-api', 'apple', or 'sherpa-onnx'."
    )
