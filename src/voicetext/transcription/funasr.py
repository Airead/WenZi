"""FunASR ONNX speech-to-text transcriber."""

from __future__ import annotations

import gc
import logging
import os
import tempfile
import threading
import time

from voicetext.config import MODEL_REVISION, MODELS
from .base import BaseTranscriber

logger = logging.getLogger(__name__)

# Set ONNX threading before any model import
os.environ.setdefault("OMP_NUM_THREADS", "8")


class FunASRTranscriber(BaseTranscriber):
    """Manages FunASR ONNX models for speech-to-text."""

    def __init__(self, use_vad: bool = False, use_punc: bool = True) -> None:
        self.use_vad = use_vad
        self.use_punc = use_punc
        self._asr_model = None
        self._vad_model = None
        self._punc_restorer = None
        self._initialized = False
        self._transcription_count = 0

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def model_display_name(self) -> str:
        return "FunASR Paraformer"

    def initialize(self) -> None:
        """Load all required models. Call once at startup."""
        if self._initialized:
            return

        logger.info("Initializing FunASR models...")
        start = time.time()

        # Pre-import funasr_onnx submodules to avoid threading deadlocks
        import importlib
        for mod in (
            "funasr_onnx.utils.utils",
            "funasr_onnx.utils.frontend",
            "funasr_onnx.paraformer_bin",
            "funasr_onnx.vad_bin",
            "funasr_onnx.punc_bin",
        ):
            try:
                importlib.import_module(mod)
            except Exception:
                pass

        # Load models in parallel
        results = {}
        threads = []

        def _load(name, func):
            results[name] = func()

        loaders = [("asr", self._load_asr)]
        if self.use_vad:
            loaders.append(("vad", self._load_vad))
        if self.use_punc:
            loaders.append(("punc", self._load_punc_restorer))

        for name, func in loaders:
            t = threading.Thread(target=_load, args=(name, func), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join(timeout=300)

        failed = [n for n, ok in results.items() if not ok]
        if failed:
            raise RuntimeError(f"Failed to load models: {', '.join(failed)}")

        elapsed = time.time() - start
        self._initialized = True
        logger.info("All models loaded in %.1fs", elapsed)

        self._warmup_librosa()

    def cleanup(self) -> None:
        """Release all loaded models and free memory."""
        self._asr_model = None
        self._vad_model = None
        if self._punc_restorer:
            self._punc_restorer.cleanup()
            self._punc_restorer = None
        self._initialized = False
        self._transcription_count = 0
        gc.collect()
        logger.info("FunASR models cleaned up")

    def transcribe(self, wav_data: bytes) -> str:
        """Transcribe WAV audio bytes to text."""
        if not self._initialized:
            self.initialize()

        # Write WAV to temp file (funasr_onnx expects file path)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_data)
            tmp_path = f.name

        try:
            # VAD: skip ASR if no speech segments detected
            if self.use_vad and self._vad_model:
                vad_result = self._vad_model(tmp_path)
                has_speech = self._vad_has_speech(vad_result)
                logger.info("VAD result: %s, has_speech=%s", vad_result, has_speech)
                if not has_speech:
                    logger.info("VAD detected no speech, skipping ASR")
                    return ""

            # ASR
            asr_result = self._asr_model([tmp_path])
            raw_text = self._extract_text(asr_result)

            # Punctuation restoration (optional)
            final_text = raw_text
            if self._punc_restorer and raw_text.strip() and not self.skip_punc:
                final_text = self._punc_restorer.restore(raw_text)

            self._transcription_count += 1
            if self._transcription_count % 10 == 0:
                gc.collect()

            logger.info("Transcription result: %s", final_text[:100])
            return final_text

        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    @staticmethod
    def _vad_has_speech(vad_result) -> bool:
        """Check if VAD result contains any speech segments."""
        if not vad_result:
            return False
        # Fsmn_vad returns list of [[start_ms, end_ms], ...] per audio
        if isinstance(vad_result, list):
            for item in vad_result:
                if isinstance(item, list) and len(item) > 0:
                    return True
        return False

    def _extract_text(self, asr_result) -> str:
        """Extract text from funasr_onnx result format."""
        if isinstance(asr_result, list) and len(asr_result) > 0:
            first = asr_result[0]
            if isinstance(first, dict):
                if "text" in first:
                    return first["text"]
                if "preds" in first:
                    preds = first["preds"]
                    if isinstance(preds, tuple) and len(preds) > 0:
                        return str(preds[0])
                    return str(preds)
            return str(first)
        return str(asr_result)

    def _get_model_dir(self, model_name: str) -> str:
        """Get local model cache path, download if needed."""
        from pathlib import Path

        home = Path.home()
        cache_base = home / ".cache" / "modelscope" / "hub" / "models" / "iic"
        short_name = model_name.split("/")[-1] if "/" in model_name else model_name
        model_dir = cache_base / short_name

        # Check local cache first
        if model_dir.exists():
            if (model_dir / "model_quant.onnx").exists() or (model_dir / "model.onnx").exists():
                logger.info("Using cached model: %s", model_dir)
                return str(model_dir)

        # Download via modelscope
        logger.info("Downloading model: %s", model_name)
        from modelscope.hub.snapshot_download import snapshot_download

        try:
            return snapshot_download(model_name, revision=MODEL_REVISION, local_files_only=True)
        except Exception:
            return snapshot_download(model_name, revision=MODEL_REVISION)

    def _load_asr(self) -> bool:
        try:
            from funasr_onnx.paraformer_bin import Paraformer

            model_dir = self._get_model_dir(MODELS["asr"])
            use_quantize = os.path.exists(os.path.join(model_dir, "model_quant.onnx"))
            num_threads = int(os.environ.get("OMP_NUM_THREADS", "8"))

            self._asr_model = Paraformer(
                model_dir,
                batch_size=1,
                device_id=-1,
                quantize=use_quantize,
                intra_op_num_threads=num_threads,
            )
            logger.info("ASR model loaded")
            return True
        except Exception as e:
            logger.error("Failed to load ASR model: %s", e)
            return False

    def _load_vad(self) -> bool:
        try:
            from funasr_onnx.vad_bin import Fsmn_vad

            model_dir = self._get_model_dir(MODELS["vad"])
            use_quantize = os.path.exists(os.path.join(model_dir, "model_quant.onnx"))
            num_threads = int(os.environ.get("OMP_NUM_THREADS", "8"))

            self._vad_model = Fsmn_vad(
                model_dir,
                batch_size=1,
                device_id=-1,
                quantize=use_quantize,
                intra_op_num_threads=num_threads,
            )
            logger.info("VAD model loaded")
            return True
        except Exception as e:
            logger.error("Failed to load VAD model: %s", e)
            return False

    def _load_punc_restorer(self) -> bool:
        try:
            from .punctuation import PunctuationRestorer

            self._punc_restorer = PunctuationRestorer()
            self._punc_restorer.initialize()
            return True
        except Exception as e:
            logger.error("Failed to load punctuation model: %s", e)
            return False

    def _warmup_librosa(self) -> None:
        """Warm up librosa to avoid first-call latency."""
        try:
            import wave
            import tempfile
            import numpy as np
            import librosa

            samples = np.zeros(int(16000 * 0.01), dtype=np.int16)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp_path = f.name
                with wave.open(tmp_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(samples.tobytes())

            try:
                librosa.load(tmp_path, sr=16000)
            finally:
                os.unlink(tmp_path)

            logger.info("librosa warmup done")
        except Exception as e:
            logger.warning("librosa warmup failed (non-fatal): %s", e)
