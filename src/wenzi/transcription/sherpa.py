"""Sherpa-ONNX streaming speech recognition backend."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .base import BaseTranscriber

logger = logging.getLogger(__name__)

DECODE_INTERVAL = 0.05  # 50ms polling interval for decode thread
_HOTWORD_BOOST = 1.5  # Boost score for hotword terms
_HOTWORDS_FILENAME = "sherpa_hotwords.txt"

# Pre-configured model definitions
SHERPA_MODELS: dict[str, dict] = {
    "zipformer-zh": {
        "display_name": "Zipformer Chinese (14M)",
        "language": "zh",
        "repo": "csukuangfj/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23",
        "type": "zipformer",
    },
    "paraformer-zh": {
        "display_name": "Paraformer Chinese-English",
        "language": "zh",
        "repo": "csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en",
        "type": "paraformer",
    },
}


def _get_model_cache_root() -> Path:
    return Path.home() / ".cache" / "sherpa-onnx-models"


def _get_model_dir(model_id: str) -> Path:
    info = SHERPA_MODELS.get(model_id)
    if not info:
        raise ValueError(f"Unknown sherpa model: {model_id!r}")
    return _get_model_cache_root() / info["repo"].split("/")[-1]


def _download_model(model_id: str) -> Path:
    """Download a sherpa-onnx model if not already cached. Returns the model directory."""
    info = SHERPA_MODELS[model_id]
    model_dir = _get_model_dir(model_id)

    if model_dir.exists() and any(model_dir.glob("*.onnx")):
        logger.info("Sherpa model %s already cached at %s", model_id, model_dir)
        return model_dir

    logger.info("Downloading sherpa model %s from %s ...", model_id, info["repo"])
    model_dir.mkdir(parents=True, exist_ok=True)

    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=info["repo"],
            local_dir=str(model_dir),
        )
    except Exception as e:
        logger.error("Failed to download sherpa model %s: %s", model_id, e)
        raise

    logger.info("Sherpa model %s downloaded to %s", model_id, model_dir)
    return model_dir


class SherpaOnnxTranscriber(BaseTranscriber):
    """Streaming speech recognition using sherpa-onnx OnlineRecognizer."""

    skip_punc = True  # Sherpa models handle their own punctuation

    def __init__(
        self,
        model: str = "zipformer-zh",
        language: str | None = None,
        hotwords: list[str] | None = None,
    ) -> None:
        self._model_id = model
        self._language = language
        self._hotwords = hotwords
        self._initialized = False
        self._recognizer = None
        self._stream = None
        self._on_partial: Callable[[str, bool], None] | None = None
        self._decode_thread: threading.Thread | None = None
        self._stream_stop = threading.Event()
        self._last_text = ""

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def model_display_name(self) -> str:
        info = SHERPA_MODELS.get(self._model_id)
        if info:
            return info["display_name"]
        return f"Sherpa ({self._model_id})"

    def initialize(self) -> None:
        """Download model (if needed) and create the OnlineRecognizer."""
        if self._initialized:
            return

        import sherpa_onnx

        model_dir = _download_model(self._model_id)
        info = SHERPA_MODELS[self._model_id]

        if info["type"] == "zipformer":
            self._recognizer = self._create_zipformer_recognizer(sherpa_onnx, model_dir)
        elif info["type"] == "paraformer":
            self._recognizer = self._create_paraformer_recognizer(sherpa_onnx, model_dir)
        else:
            raise ValueError(f"Unknown model type: {info['type']}")

        self._initialized = True
        logger.info("Sherpa-ONNX recognizer ready (model=%s)", self._model_id)

    def _create_zipformer_recognizer(self, sherpa_onnx, model_dir: Path):
        """Create a zipformer-based online recognizer."""
        # Find model files
        encoder = str(next(model_dir.glob("*encoder*.onnx")))
        decoder = str(next(model_dir.glob("*decoder*.onnx")))
        joiner = str(next(model_dir.glob("*joiner*.onnx")))
        tokens = str(next(model_dir.glob("tokens.txt")))

        kwargs = dict(
            encoder=encoder,
            decoder=decoder,
            joiner=joiner,
            tokens=tokens,
            num_threads=2,
            sample_rate=16000,
            feature_dim=80,
        )

        if self._hotwords:
            hotwords_path = self._write_hotwords_file()
            if hotwords_path:
                kwargs["hotwords_file"] = hotwords_path
                kwargs["hotwords_score"] = _HOTWORD_BOOST
                kwargs["decoding_method"] = "modified_beam_search"

        return sherpa_onnx.OnlineRecognizer.from_transducer(**kwargs)

    @staticmethod
    def _hotwords_path() -> Path:
        """Return the path for the sherpa hotwords cache file."""
        from wenzi.config import resolve_cache_dir
        return Path(resolve_cache_dir()) / _HOTWORDS_FILENAME

    def _write_hotwords_file(self) -> str | None:
        """Write hotwords to a cache file. Returns the path or None on failure."""
        hotwords_path = self._hotwords_path()
        try:
            hotwords_path.parent.mkdir(parents=True, exist_ok=True)
            lines = [f"{term} :{_HOTWORD_BOOST}" for term in self._hotwords]
            hotwords_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            logger.info("Wrote %d hotwords to %s", len(self._hotwords), hotwords_path)
            return str(hotwords_path)
        except Exception as e:
            logger.warning("Failed to write hotwords file: %s", e)
            return None

    def _create_paraformer_recognizer(self, sherpa_onnx, model_dir: Path):
        """Create a paraformer-based online recognizer."""
        encoder = str(next(model_dir.glob("*encoder*.onnx")))
        decoder = str(next(model_dir.glob("*decoder*.onnx")))
        tokens = str(next(model_dir.glob("tokens.txt")))

        return sherpa_onnx.OnlineRecognizer.from_paraformer(
            encoder=encoder,
            decoder=decoder,
            tokens=tokens,
            num_threads=2,
            sample_rate=16000,
        )

    def transcribe(self, wav_data: bytes, *, hotwords: list[str] | None = None) -> str:
        """Batch transcription: decode entire WAV at once."""
        if not self._initialized:
            self.initialize()

        import io
        import wave

        with wave.open(io.BytesIO(wav_data), "rb") as wf:
            if wf.getnchannels() != 1:
                raise ValueError(f"Expected mono audio, got {wf.getnchannels()} channels")
            if wf.getsampwidth() != 2:
                raise ValueError(f"Expected 16-bit audio, got {wf.getsampwidth() * 8}-bit")

            sample_rate = wf.getframerate()
            raw = wf.readframes(wf.getnframes())

        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)

        # Signal end of utterance
        tail_silence = np.zeros(int(sample_rate * 0.5), dtype=np.float32)
        stream.accept_waveform(sample_rate, tail_silence)

        while self._recognizer.is_ready(stream):
            self._recognizer.decode_stream(stream)

        result = self._recognizer.get_result(stream)
        text = result.text.strip() if hasattr(result, "text") else str(result).strip()
        logger.info("Sherpa batch transcription: %s", text[:100] if text else "(empty)")
        return text

    # ── Streaming interface ───────────────────────────────────────────

    @property
    def supports_streaming(self) -> bool:
        return True

    def start_streaming(self, on_partial: Callable[[str, bool], None]) -> None:
        if not self._initialized:
            self.initialize()

        self._on_partial = on_partial
        self._stream = self._recognizer.create_stream()
        self._stream_stop.clear()
        self._last_text = ""

        self._decode_thread = threading.Thread(target=self._decode_loop, daemon=True)
        self._decode_thread.start()
        logger.info("Sherpa streaming started")

    def feed_audio(self, samples: bytes) -> None:
        stream = self._stream  # local snapshot to avoid TOCTOU with _cleanup_stream
        if stream is None:
            return
        arr = np.frombuffer(samples, dtype=np.int16)
        float_samples = arr.astype(np.float32) / 32768.0
        stream.accept_waveform(16000, float_samples)

    def stop_streaming(self) -> str:
        if self._stream is None:
            return ""

        # Feed a bit of silence to flush any remaining audio
        tail = np.zeros(int(16000 * 0.3), dtype=np.float32)
        self._stream.accept_waveform(16000, tail)

        # Signal decode thread to finish
        self._stream_stop.set()
        if self._decode_thread is not None:
            self._decode_thread.join(timeout=5.0)
            if self._decode_thread.is_alive():
                logger.warning(
                    "Decode thread did not exit within timeout, cleaning up references"
                )
                self._cleanup_stream()
                return self._last_text

        # Final decode pass
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)

        result = self._recognizer.get_result(self._stream)
        text = result.text.strip() if hasattr(result, "text") else str(result).strip()

        if self._on_partial and text and text != self._last_text:
            try:
                self._on_partial(text, True)
            except Exception:
                logger.debug("on_partial callback error", exc_info=True)

        self._cleanup_stream()
        logger.info("Sherpa streaming stopped, result: %s", text[:100] if text else "(empty)")
        return text

    def cancel_streaming(self) -> None:
        self._stream_stop.set()
        if self._decode_thread is not None:
            self._decode_thread.join(timeout=2.0)
            if self._decode_thread.is_alive():
                logger.warning(
                    "Decode thread did not exit within timeout, cleaning up references"
                )
        self._cleanup_stream()
        logger.info("Sherpa streaming cancelled")

    def _decode_loop(self) -> None:
        """Background thread that polls is_ready and emits partial results."""
        while not self._stream_stop.is_set():
            if self._stream is not None and self._recognizer.is_ready(self._stream):
                self._recognizer.decode_stream(self._stream)
                result = self._recognizer.get_result(self._stream)
                text = result.text.strip() if hasattr(result, "text") else str(result).strip()
                if text and text != self._last_text:
                    self._last_text = text
                    if self._on_partial:
                        try:
                            self._on_partial(text, False)
                        except Exception:
                            logger.debug("on_partial callback error", exc_info=True)
            self._stream_stop.wait(DECODE_INTERVAL)

    def _cleanup_stream(self) -> None:
        self._stream = None
        self._decode_thread = None
        self._on_partial = None
        self._last_text = ""
        self._stream_stop.set()  # ensure thread sees stop signal

    def cleanup(self) -> None:
        if self._stream is not None:
            self.cancel_streaming()
        self._recognizer = None
        self._initialized = False
        import gc
        gc.collect()
        # Clean up hotwords file
        try:
            self._hotwords_path().unlink(missing_ok=True)
        except Exception:
            pass
        logger.info("Sherpa-ONNX recognizer cleaned up")
