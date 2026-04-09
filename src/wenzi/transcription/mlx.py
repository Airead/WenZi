"""MLX Whisper speech-to-text transcriber for Apple Silicon."""

from __future__ import annotations

import gc
import logging
import time

from .base import BaseTranscriber, build_hotwords_prompt

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


class MLXWhisperTranscriber(BaseTranscriber):
    """Speech-to-text using mlx-whisper on Apple Silicon GPU."""

    def __init__(
        self,
        language: str | None = None,
        model: str | None = None,
        use_punc: bool = False,
        temperature: float | None = None,
        hotwords: list[str] | None = None,
    ) -> None:
        self._model_name = model or DEFAULT_MODEL
        self._language = language
        self._use_punc = use_punc
        self._temperature = temperature if temperature is not None else 0.0
        self._hotwords = hotwords
        self._initialized = False
        self._mlx_whisper = None
        self._punc_restorer = None
        self._transcription_count = 0

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def model_display_name(self) -> str:
        name = self._model_name
        # Strip common prefixes for a cleaner display
        if "/" in name:
            name = name.rsplit("/", 1)[-1]
        return name

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

        # Try to clear mlx_whisper's internal model cache
        try:
            if self._mlx_whisper is not None:
                # Try known cache locations in different mlx_whisper versions
                for attr in ('_cache', '_model_cache', 'models'):
                    if hasattr(self._mlx_whisper, attr):
                        cache = getattr(self._mlx_whisper, attr)
                        if isinstance(cache, dict):
                            cache.clear()
                # Also check the load_models submodule
                load_mod = getattr(self._mlx_whisper, 'load_models', None)
                if load_mod:
                    for attr in ('_cache', '_model_cache'):
                        if hasattr(load_mod, attr):
                            cache = getattr(load_mod, attr)
                            if isinstance(cache, dict):
                                cache.clear()
        except Exception:
            pass

        self._mlx_whisper = None
        self._initialized = False
        self._transcription_count = 0

        # Remove mlx_whisper modules from sys.modules to release the
        # module-level model cache in mlx_whisper.load_models.  Without
        # this, the cached model stays alive via sys.modules even after
        # self._mlx_whisper is set to None.
        import sys
        for mod_name in list(sys.modules):
            if mod_name.startswith("mlx_whisper"):
                del sys.modules[mod_name]

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

    def transcribe(self, wav_data: bytes, *, hotwords: list[str] | None = None) -> str:
        """Transcribe WAV audio bytes to text."""
        if not self._initialized:
            self.initialize()

        # Decode WAV in Python — no ffmpeg needed
        audio = self._wav_bytes_to_float32(wav_data)

        kwargs: dict = {
            "path_or_hf_repo": self._model_name,
            "language": self._language,
            "temperature": self._temperature,
            "condition_on_previous_text": False,
        }

        effective_hotwords = hotwords if hotwords is not None else self._hotwords
        if effective_hotwords:
            prompt = build_hotwords_prompt(effective_hotwords)
            if prompt:
                kwargs["initial_prompt"] = prompt
                logger.debug("MLX Whisper hotwords prompt: %s", prompt)

        result = self._mlx_whisper.transcribe(audio, **kwargs)

        text = result.get("text", "")

        if self._punc_restorer and text.strip() and not self.skip_punc:
            text = self._punc_restorer.restore(text)

        # Periodically clear GPU memory to prevent accumulation
        self._transcription_count += 1
        if self._transcription_count % 5 == 0:
            try:
                import mlx.core as mx

                mx.metal.clear_cache()
                logger.debug(
                    "Cleared Metal cache after %d transcriptions",
                    self._transcription_count,
                )
            except Exception:
                pass

        logger.info("Transcription result: %s", text[:100])
        return text
