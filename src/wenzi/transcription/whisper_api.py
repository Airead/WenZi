"""Whisper API speech-to-text transcriber (OpenAI-compatible, e.g. Groq)."""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

from .base import BaseTranscriber, build_hotwords_prompt

if TYPE_CHECKING:
    from wenzi.llm_http import TranscriptionClient

logger = logging.getLogger(__name__)


class WhisperAPITranscriber(BaseTranscriber):
    """Speech-to-text via OpenAI-compatible audio transcription API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        language: str | None = None,
        temperature: float | None = None,
        hotwords: list[str] | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._language = language
        self._temperature = temperature if temperature is not None else 0.0
        self._hotwords = hotwords
        self._client: TranscriptionClient | None = None
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def model_display_name(self) -> str:
        return self._model

    def initialize(self) -> None:
        if self._initialized:
            return
        from wenzi.llm_http import TranscriptionClient
        self._client = TranscriptionClient(base_url=self._base_url, api_key=self._api_key)
        self._initialized = True
        logger.info(
            "Whisper API transcriber ready (base_url=%s, model=%s)",
            self._base_url,
            self._model,
        )

    def cleanup(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._initialized = False
        logger.info("Whisper API transcriber cleaned up")

    def transcribe(self, wav_data: bytes, *, hotwords: list[str] | None = None) -> str:
        if not self._initialized:
            self.initialize()

        audio_file = io.BytesIO(wav_data)
        audio_file.name = "audio.wav"

        kwargs: dict = {
            "model": self._model,
            "file": audio_file,
            "temperature": self._temperature,
        }
        if self._language:
            kwargs["language"] = self._language
        effective_hotwords = hotwords if hotwords is not None else self._hotwords
        if effective_hotwords:
            prompt = build_hotwords_prompt(effective_hotwords)
            if prompt:
                kwargs["prompt"] = prompt
                logger.debug("ASR hotwords prompt: %s", prompt)

        text = self._client.create(**kwargs).strip()

        logger.info("Transcription result: %s", text[:100])
        return text

    @staticmethod
    def verify_provider(base_url: str, api_key: str, model: str) -> str | None:
        """Test an ASR provider connection with a silent WAV file.

        Returns None on success or an error message string on failure.
        """
        import struct
        import wave

        # Generate a short silent WAV (0.5s, 16kHz, 16-bit mono)
        sample_rate = 16000
        num_samples = sample_rate // 2
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(struct.pack(f"<{num_samples}h", *([0] * num_samples)))
        wav_data = buf.getvalue()

        client = None
        try:
            from wenzi.llm_http import TranscriptionClient
            client = TranscriptionClient(base_url=base_url, api_key=api_key)
            audio_file = io.BytesIO(wav_data)
            audio_file.name = "test.wav"
            client.create(model=model, file=audio_file)
            return None
        except Exception as e:
            return str(e)
        finally:
            if client is not None:
                client.close()
