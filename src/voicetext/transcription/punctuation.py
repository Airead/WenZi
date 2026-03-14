"""Shared punctuation restoration using FunASR CT_Transformer."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class PunctuationRestorer:
    """Restore punctuation using FunASR CT_Transformer ONNX model."""

    def __init__(self) -> None:
        self._model = None
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    def initialize(self) -> None:
        """Load the punctuation model."""
        if self._initialized:
            return

        from voicetext.config import MODEL_REVISION, MODELS

        model_name = MODELS["punc"]
        model_dir = self._get_model_dir(model_name, MODEL_REVISION)

        from funasr_onnx.punc_bin import CT_Transformer

        use_quantize = os.path.exists(os.path.join(model_dir, "model_quant.onnx"))
        num_threads = int(os.environ.get("OMP_NUM_THREADS", "8"))

        self._model = CT_Transformer(
            model_dir,
            batch_size=1,
            device_id=-1,
            quantize=use_quantize,
            intra_op_num_threads=num_threads,
        )
        self._initialized = True
        logger.info("Punctuation model loaded")

    def restore(self, text: str) -> str:
        """Add punctuation to text. Returns original text on failure."""
        if not text.strip():
            return text
        if not self._initialized:
            self.initialize()

        try:
            result = self._model(text)
            if isinstance(result, tuple) and len(result) > 0:
                return str(result[0])
            return str(result)
        except Exception as e:
            logger.warning("Punctuation restoration failed: %s", e)
            return text

    def cleanup(self) -> None:
        """Release model resources."""
        self._model = None
        self._initialized = False

    @staticmethod
    def _get_model_dir(model_name: str, revision: str) -> str:
        """Get local model cache path, download if needed."""
        from pathlib import Path

        home = Path.home()
        cache_base = home / ".cache" / "modelscope" / "hub" / "models" / "iic"
        short_name = model_name.split("/")[-1] if "/" in model_name else model_name
        model_dir = cache_base / short_name

        if model_dir.exists():
            if (model_dir / "model_quant.onnx").exists() or (
                model_dir / "model.onnx"
            ).exists():
                logger.info("Using cached punc model: %s", model_dir)
                return str(model_dir)

        logger.info("Downloading punc model: %s", model_name)
        from modelscope.hub.snapshot_download import snapshot_download

        try:
            return snapshot_download(model_name, revision=revision, local_files_only=True)
        except Exception:
            return snapshot_download(model_name, revision=revision)
