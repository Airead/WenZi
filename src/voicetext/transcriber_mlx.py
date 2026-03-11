"""MLX Whisper speech-to-text transcriber for Apple Silicon."""

from __future__ import annotations

import gc
import logging
import time
from typing import Optional

from .transcriber import BaseTranscriber

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


class MLXWhisperTranscriber(BaseTranscriber):
    """Speech-to-text using mlx-whisper on Apple Silicon GPU."""

    def __init__(
        self,
        language: Optional[str] = None,
        model: Optional[str] = None,
        use_punc: bool = False,
    ) -> None:
        self._model_name = model or DEFAULT_MODEL
        self._language = language
        self._use_punc = use_punc
        self._initialized = False
        self._mlx_whisper = None
        self._punc_restorer = None

    @property
    def initialized(self) -> bool:
        return self._initialized

    def initialize(self) -> None:
        """Import mlx_whisper and warm up the model."""
        if self._initialized:
            return

        logger.info("Initializing mlx-whisper with model: %s", self._model_name)
        start = time.time()

        try:
            import mlx_whisper
        except ImportError as e:
            raise ImportError(
                f"Failed to import mlx-whisper: {e}"
            ) from e

        self._mlx_whisper = mlx_whisper

        # Warm up: run a short silent audio to trigger model download and JIT
        self._warmup()

        # Load punctuation restorer if requested
        if self._use_punc:
            from .punctuation import PunctuationRestorer

            self._punc_restorer = PunctuationRestorer()
            self._punc_restorer.initialize()

        elapsed = time.time() - start
        self._initialized = True
        logger.info("mlx-whisper ready in %.1fs", elapsed)

    def cleanup(self) -> None:
        """Release mlx-whisper model and free GPU memory."""
        if self._punc_restorer:
            self._punc_restorer.cleanup()
            self._punc_restorer = None
        self._mlx_whisper = None
        self._initialized = False
        gc.collect()
        try:
            import mlx.core as mx

            mx.metal.clear_cache()
        except Exception:
            pass
        logger.info("mlx-whisper model cleaned up")

    def _warmup(self) -> None:
        """Run a tiny transcription to preload the model."""
        import numpy as np

        # Pass a short silent audio as numpy array (bypasses ffmpeg)
        audio = np.zeros(int(16000 * 0.1), dtype=np.float32)
        try:
            self._mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=self._model_name,
                language=self._language,
            )
            logger.info("mlx-whisper warmup done")
        except Exception as e:
            logger.warning("mlx-whisper warmup failed (non-fatal): %s", e)

    @staticmethod
    def _wav_bytes_to_float32(wav_data: bytes):
        """Decode WAV bytes to float32 numpy array (mono, original sample rate)."""
        import io
        import wave
        import numpy as np

        with wave.open(io.BytesIO(wav_data), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return audio

    def transcribe(self, wav_data: bytes) -> str:
        """Transcribe WAV audio bytes to text."""
        if not self._initialized:
            self.initialize()

        # Decode WAV in Python — no ffmpeg needed
        audio = self._wav_bytes_to_float32(wav_data)

        result = self._mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self._model_name,
            language=self._language,
        )

        text = result.get("text", "")

        if self._punc_restorer and text.strip():
            text = self._punc_restorer.restore(text)

        logger.info("Transcription result: %s", text[:100])
        return text
