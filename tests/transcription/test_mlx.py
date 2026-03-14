"""Tests for MLXWhisperTranscriber."""

from __future__ import annotations

import io
import struct
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from voicetext.transcription.mlx import MLXWhisperTranscriber, DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wav(num_samples: int = 160, sample_rate: int = 16000) -> bytes:
    """Return a minimal valid WAV byte string (mono, 16-bit)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{num_samples}h", *([0] * num_samples)))
    return buf.getvalue()


def _make_mock_mlx_whisper(text: str = "hello") -> MagicMock:
    mock = MagicMock()
    mock.transcribe.return_value = {"text": text}
    return mock


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_defaults(self):
        t = MLXWhisperTranscriber()
        assert t._model_name == DEFAULT_MODEL
        assert t._language is None
        assert t._use_punc is False
        assert t._temperature == 0.0
        assert t._initialized is False
        assert t._mlx_whisper is None
        assert t._punc_restorer is None

    def test_custom_params(self):
        t = MLXWhisperTranscriber(
            language="zh",
            model="mlx-community/whisper-tiny",
            use_punc=True,
            temperature=0.5,
        )
        assert t._model_name == "mlx-community/whisper-tiny"
        assert t._language == "zh"
        assert t._use_punc is True
        assert t._temperature == 0.5

    def test_temperature_none_defaults_to_zero(self):
        t = MLXWhisperTranscriber(temperature=None)
        assert t._temperature == 0.0


# ---------------------------------------------------------------------------
# model_display_name
# ---------------------------------------------------------------------------

class TestModelDisplayName:
    def test_strips_prefix_with_slash(self):
        t = MLXWhisperTranscriber(model="mlx-community/whisper-large-v3-turbo")
        assert t.model_display_name == "whisper-large-v3-turbo"

    def test_no_slash_returns_full_name(self):
        t = MLXWhisperTranscriber(model="custom-model")
        assert t.model_display_name == "custom-model"

    def test_default_model_display_name(self):
        t = MLXWhisperTranscriber()
        assert t.model_display_name == "whisper-large-v3-turbo"


# ---------------------------------------------------------------------------
# initialized property
# ---------------------------------------------------------------------------

class TestInitializedProperty:
    def test_initially_false(self):
        t = MLXWhisperTranscriber()
        assert t.initialized is False

    def test_true_after_set(self):
        t = MLXWhisperTranscriber()
        t._initialized = True
        assert t.initialized is True


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------

class TestInitialize:
    def test_already_initialized_returns_early(self):
        t = MLXWhisperTranscriber()
        t._initialized = True
        original_mlx = object()
        t._mlx_whisper = original_mlx
        t.initialize()  # should be a no-op
        assert t._mlx_whisper is original_mlx

    def test_import_error_propagates(self):
        t = MLXWhisperTranscriber()
        with patch.dict("sys.modules", {"mlx_whisper": None}):
            with pytest.raises(ImportError, match="mlx-whisper"):
                t.initialize()

    def test_successful_initialize_sets_flag(self):
        t = MLXWhisperTranscriber(use_punc=False)
        mock_mlx = _make_mock_mlx_whisper()

        with patch.dict("sys.modules", {"mlx_whisper": mock_mlx}), \
             patch.object(t, "_warmup"):
            t.initialize()

        assert t._initialized is True
        assert t._mlx_whisper is mock_mlx

    def test_initialize_calls_warmup(self):
        t = MLXWhisperTranscriber(use_punc=False)
        mock_mlx = _make_mock_mlx_whisper()

        with patch.dict("sys.modules", {"mlx_whisper": mock_mlx}), \
             patch.object(t, "_warmup") as mock_warmup:
            t.initialize()

        mock_warmup.assert_called_once()

    def test_initialize_loads_punc_restorer_when_requested(self):
        t = MLXWhisperTranscriber(use_punc=True)
        mock_mlx = _make_mock_mlx_whisper()
        mock_punc_instance = MagicMock()
        mock_punc_cls = MagicMock(return_value=mock_punc_instance)
        mock_punc_module = MagicMock(PunctuationRestorer=mock_punc_cls)

        with patch.dict("sys.modules", {
            "mlx_whisper": mock_mlx,
            "voicetext.transcription.punctuation": mock_punc_module,
        }), patch.object(t, "_warmup"):
            t.initialize()

        mock_punc_instance.initialize.assert_called_once()

    def test_initialize_skips_punc_when_not_requested(self):
        t = MLXWhisperTranscriber(use_punc=False)
        mock_mlx = _make_mock_mlx_whisper()

        with patch.dict("sys.modules", {"mlx_whisper": mock_mlx}), \
             patch.object(t, "_warmup"):
            t.initialize()

        assert t._punc_restorer is None


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_cleanup_resets_state(self):
        t = MLXWhisperTranscriber()
        t._initialized = True
        t._mlx_whisper = MagicMock()
        t.cleanup()
        assert t._initialized is False
        assert t._mlx_whisper is None

    def test_cleanup_calls_punc_restorer_cleanup(self):
        t = MLXWhisperTranscriber()
        t._initialized = True
        mock_punc = MagicMock()
        t._punc_restorer = mock_punc
        t.cleanup()
        mock_punc.cleanup.assert_called_once()
        assert t._punc_restorer is None

    def test_cleanup_clears_metal_cache(self):
        """cleanup() calls mx.metal.clear_cache() when mlx.core is available."""
        import sys

        t = MLXWhisperTranscriber()
        t._initialized = True

        mock_mx = MagicMock()
        # The source does `import mlx.core as mx; mx.metal.clear_cache()`.
        # Inject both "mlx" and "mlx.core" so the import machinery resolves to our mock.
        mock_mlx_pkg = MagicMock()
        mock_mlx_pkg.core = mock_mx

        # Remove pre-existing entries so patch.dict starts fresh for this test
        prev_mlx = sys.modules.pop("mlx", None)
        prev_mlx_core = sys.modules.pop("mlx.core", None)
        try:
            with patch.dict("sys.modules", {"mlx": mock_mlx_pkg, "mlx.core": mock_mx},
                            clear=False):
                t.cleanup()
        finally:
            # Restore whatever was there before
            if prev_mlx is not None:
                sys.modules["mlx"] = prev_mlx
            if prev_mlx_core is not None:
                sys.modules["mlx.core"] = prev_mlx_core

        mock_mx.metal.clear_cache.assert_called_once()

    def test_cleanup_suppresses_mlx_import_error(self):
        """cleanup() should not raise if mlx.core is unavailable."""
        import sys

        t = MLXWhisperTranscriber()
        t._initialized = True

        # Remove real mlx entries so import fails cleanly
        prev_mlx = sys.modules.pop("mlx", None)
        prev_mlx_core = sys.modules.pop("mlx.core", None)
        try:
            with patch.dict("sys.modules", {"mlx": None, "mlx.core": None}, clear=False):
                t.cleanup()  # should not raise
        finally:
            if prev_mlx is not None:
                sys.modules["mlx"] = prev_mlx
            if prev_mlx_core is not None:
                sys.modules["mlx.core"] = prev_mlx_core

        assert t._initialized is False

    def test_cleanup_calls_gc(self):
        """gc.collect() is called during cleanup."""
        import sys

        t = MLXWhisperTranscriber()
        t._initialized = True

        # Suppress the mlx.core import so cleanup doesn't crash when gc is patched
        prev_mlx = sys.modules.pop("mlx", None)
        prev_mlx_core = sys.modules.pop("mlx.core", None)
        try:
            with patch.dict("sys.modules", {"mlx": None, "mlx.core": None}, clear=False), \
                 patch("voicetext.transcription.mlx.gc.collect") as mock_gc:
                t.cleanup()
        finally:
            if prev_mlx is not None:
                sys.modules["mlx"] = prev_mlx
            if prev_mlx_core is not None:
                sys.modules["mlx.core"] = prev_mlx_core

        mock_gc.assert_called_once()


# ---------------------------------------------------------------------------
# _warmup
# ---------------------------------------------------------------------------

class TestWarmup:
    def test_warmup_calls_transcribe_with_silent_audio(self):
        t = MLXWhisperTranscriber(language="zh")
        mock_mlx = _make_mock_mlx_whisper()
        t._mlx_whisper = mock_mlx

        t._warmup()

        mock_mlx.transcribe.assert_called_once()
        args, kwargs = mock_mlx.transcribe.call_args
        audio_arg = args[0]
        assert hasattr(audio_arg, "__len__")  # numpy array
        assert kwargs.get("path_or_hf_repo") == t._model_name
        assert kwargs.get("language") == "zh"

    def test_warmup_suppresses_exception(self):
        """Warmup failure must not raise (non-fatal)."""
        t = MLXWhisperTranscriber()
        mock_mlx = MagicMock()
        mock_mlx.transcribe.side_effect = Exception("model not downloaded")
        t._mlx_whisper = mock_mlx

        t._warmup()  # should not raise


# ---------------------------------------------------------------------------
# _wav_bytes_to_float32
# ---------------------------------------------------------------------------

class TestWavBytesToFloat32:
    def test_returns_float32_array(self):
        wav = _make_wav(num_samples=160)
        audio = MLXWhisperTranscriber._wav_bytes_to_float32(wav)
        assert audio.dtype == np.float32
        assert len(audio) == 160

    def test_silent_audio_is_zeros(self):
        wav = _make_wav(num_samples=320)
        audio = MLXWhisperTranscriber._wav_bytes_to_float32(wav)
        assert np.all(audio == 0.0)

    def test_normalizes_to_minus_one_one(self):
        """16-bit int max (32767) should map to ~1.0 in float32."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(struct.pack("<h", 32767))
        wav = buf.getvalue()
        audio = MLXWhisperTranscriber._wav_bytes_to_float32(wav)
        assert abs(audio[0] - (32767 / 32768.0)) < 1e-4


# ---------------------------------------------------------------------------
# transcribe
# ---------------------------------------------------------------------------

class TestTranscribe:
    def _make_initialized(self, text="transcribed text", language=None) -> MLXWhisperTranscriber:
        t = MLXWhisperTranscriber(language=language)
        t._initialized = True
        t._mlx_whisper = _make_mock_mlx_whisper(text=text)
        return t

    def test_transcribe_returns_text(self):
        t = self._make_initialized(text="hello world")
        result = t.transcribe(_make_wav())
        assert result == "hello world"

    def test_transcribe_calls_initialize_when_not_ready(self):
        t = MLXWhisperTranscriber()
        t._initialized = False

        def fake_init():
            t._initialized = True
            t._mlx_whisper = _make_mock_mlx_whisper(text="ok")

        with patch.object(t, "initialize", side_effect=fake_init):
            result = t.transcribe(_make_wav())
        assert result == "ok"

    def test_transcribe_passes_language_to_mlx(self):
        t = self._make_initialized(language="zh")
        t.transcribe(_make_wav())
        _, kwargs = t._mlx_whisper.transcribe.call_args
        assert kwargs.get("language") == "zh"

    def test_transcribe_passes_temperature(self):
        t = self._make_initialized()
        t._temperature = 0.3
        t.transcribe(_make_wav())
        _, kwargs = t._mlx_whisper.transcribe.call_args
        assert kwargs.get("temperature") == 0.3

    def test_transcribe_condition_on_previous_text_false(self):
        t = self._make_initialized()
        t.transcribe(_make_wav())
        _, kwargs = t._mlx_whisper.transcribe.call_args
        assert kwargs.get("condition_on_previous_text") is False

    def test_transcribe_applies_punc_restorer(self):
        t = self._make_initialized(text="hello world")
        mock_punc = MagicMock()
        mock_punc.restore.return_value = "hello world."
        t._punc_restorer = mock_punc

        result = t.transcribe(_make_wav())
        assert result == "hello world."
        mock_punc.restore.assert_called_once_with("hello world")

    def test_transcribe_skips_punc_when_skip_punc_true(self):
        t = self._make_initialized(text="hello world")
        mock_punc = MagicMock()
        mock_punc.restore.return_value = "hello world."
        t._punc_restorer = mock_punc
        t.skip_punc = True

        result = t.transcribe(_make_wav())
        assert result == "hello world"
        mock_punc.restore.assert_not_called()

    def test_transcribe_skips_punc_for_whitespace_only_text(self):
        t = self._make_initialized(text="   ")
        mock_punc = MagicMock()
        t._punc_restorer = mock_punc

        t.transcribe(_make_wav())
        # text.strip() is empty → punc skipped
        mock_punc.restore.assert_not_called()

    def test_transcribe_returns_empty_string_when_result_missing_text(self):
        t = MLXWhisperTranscriber()
        t._initialized = True
        mock_mlx = MagicMock()
        mock_mlx.transcribe.return_value = {}  # no "text" key
        t._mlx_whisper = mock_mlx

        result = t.transcribe(_make_wav())
        assert result == ""
