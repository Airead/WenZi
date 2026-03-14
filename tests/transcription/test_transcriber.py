"""Tests for the transcriber module."""

import pytest

from voicetext.transcription.base import BaseTranscriber, create_transcriber
from voicetext.transcription.funasr import FunASRTranscriber


class TestVadHasSpeech:
    def test_empty_result(self):
        assert FunASRTranscriber._vad_has_speech(None) is False
        assert FunASRTranscriber._vad_has_speech([]) is False

    def test_no_speech_segments(self):
        # VAD returns empty segment lists when no speech detected
        assert FunASRTranscriber._vad_has_speech([[]]) is False

    def test_has_speech_segments(self):
        # VAD returns [[start_ms, end_ms], ...] per audio
        assert FunASRTranscriber._vad_has_speech([[[0, 1000]]]) is True
        assert FunASRTranscriber._vad_has_speech([[[100, 500], [800, 1200]]]) is True

    def test_non_list_result(self):
        assert FunASRTranscriber._vad_has_speech("unexpected") is False
        assert FunASRTranscriber._vad_has_speech(0) is False


class TestCreateTranscriber:
    def test_create_funasr_backend(self):
        t = create_transcriber(backend="funasr")
        assert isinstance(t, FunASRTranscriber)
        assert isinstance(t, BaseTranscriber)

    def test_create_mlx_backend(self):
        try:
            t = create_transcriber(backend="mlx-whisper")
            assert isinstance(t, BaseTranscriber)
        except ImportError:
            pytest.skip("mlx-whisper not installed")

    def test_create_mlx_aliases(self):
        """'mlx' and 'whisper' are aliases for 'mlx-whisper'."""
        try:
            for alias in ("mlx", "whisper"):
                t = create_transcriber(backend=alias)
                assert isinstance(t, BaseTranscriber)
        except ImportError:
            pytest.skip("mlx-whisper not installed")

    def test_create_apple_speech_backend(self):
        from voicetext.transcription.apple import AppleSpeechTranscriber

        t = create_transcriber(backend="apple-speech", model="on-device")
        assert isinstance(t, AppleSpeechTranscriber)
        assert isinstance(t, BaseTranscriber)
        assert t._on_device is True

    def test_create_apple_speech_server(self):
        from voicetext.transcription.apple import AppleSpeechTranscriber

        t = create_transcriber(backend="apple-speech", model="server")
        assert isinstance(t, AppleSpeechTranscriber)
        assert t._on_device is False

    def test_create_apple_speech_alias(self):
        from voicetext.transcription.apple import AppleSpeechTranscriber

        t = create_transcriber(backend="apple")
        assert isinstance(t, AppleSpeechTranscriber)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown ASR backend"):
            create_transcriber(backend="unknown")


class TestModelDisplayName:
    def test_funasr_display_name(self):
        t = FunASRTranscriber(use_vad=False, use_punc=False)
        assert t.model_display_name == "FunASR Paraformer"

    def test_mlx_display_name_default(self):
        try:
            from voicetext.transcription.mlx import MLXWhisperTranscriber
        except ImportError:
            pytest.skip("mlx-whisper not installed")

        t = MLXWhisperTranscriber()
        assert t.model_display_name == "whisper-large-v3-turbo"

    def test_mlx_display_name_custom(self):
        try:
            from voicetext.transcription.mlx import MLXWhisperTranscriber
        except ImportError:
            pytest.skip("mlx-whisper not installed")

        t = MLXWhisperTranscriber(model="mlx-community/whisper-tiny")
        assert t.model_display_name == "whisper-tiny"

    def test_mlx_display_name_no_slash(self):
        try:
            from voicetext.transcription.mlx import MLXWhisperTranscriber
        except ImportError:
            pytest.skip("mlx-whisper not installed")

        t = MLXWhisperTranscriber(model="custom-model")
        assert t.model_display_name == "custom-model"

    def test_whisper_api_display_name(self):
        from voicetext.transcription.whisper_api import WhisperAPITranscriber

        t = WhisperAPITranscriber(
            base_url="https://api.example.com",
            api_key="test-key",
            model="whisper-large-v3",
        )
        assert t.model_display_name == "whisper-large-v3"


class TestWavDurationSeconds:
    def test_valid_wav(self):
        import io
        import struct
        import wave

        sample_rate = 16000
        num_samples = sample_rate * 2  # 2 seconds
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(struct.pack(f"<{num_samples}h", *([0] * num_samples)))
        wav_data = buf.getvalue()

        duration = BaseTranscriber.wav_duration_seconds(wav_data)
        assert abs(duration - 2.0) < 0.01

    def test_invalid_data_returns_zero(self):
        assert BaseTranscriber.wav_duration_seconds(b"not a wav") == 0.0

    def test_empty_data_returns_zero(self):
        assert BaseTranscriber.wav_duration_seconds(b"") == 0.0


class TestSkipPunc:
    def test_skip_punc_default_false(self):
        t = FunASRTranscriber(use_vad=False, use_punc=True)
        assert t.skip_punc is False

    def test_skip_punc_toggleable(self):
        t = FunASRTranscriber(use_vad=False, use_punc=True)
        t.skip_punc = True
        assert t.skip_punc is True
        t.skip_punc = False
        assert t.skip_punc is False

    def test_funasr_skips_punc_when_flag_set(self):
        """When skip_punc=True, punctuation restoration should be skipped."""
        t = FunASRTranscriber(use_vad=False, use_punc=True)
        t._initialized = True
        t._asr_model = lambda paths: [{"text": "hello world"}]
        # Create a mock punc restorer that would modify text
        class MockPunc:
            def restore(self, text):
                return text + "。"
        t._punc_restorer = MockPunc()

        import io
        import wave
        import struct
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(struct.pack("<160h", *([0] * 160)))
        wav_data = buf.getvalue()

        # Without skip_punc: punctuation should be applied
        t.skip_punc = False
        result = t.transcribe(wav_data)
        assert result == "hello world。"

        # With skip_punc: punctuation should be skipped
        t.skip_punc = True
        result = t.transcribe(wav_data)
        assert result == "hello world"

    def test_mlx_skip_punc_attribute(self):
        try:
            from voicetext.transcription.mlx import MLXWhisperTranscriber
        except ImportError:
            pytest.skip("mlx-whisper not installed")

        t = MLXWhisperTranscriber(use_punc=True)
        assert t.skip_punc is False
        t.skip_punc = True
        assert t.skip_punc is True


class TestCleanup:
    def test_funasr_cleanup(self):
        t = FunASRTranscriber(use_vad=False, use_punc=False)
        # Simulate initialized state
        t._initialized = True
        t._asr_model = "fake_model"
        t.cleanup()
        assert t.initialized is False
        assert t._asr_model is None
        assert t._vad_model is None
        assert t._punc_restorer is None

    def test_mlx_cleanup(self):
        try:
            from voicetext.transcription.mlx import MLXWhisperTranscriber
        except ImportError:
            pytest.skip("mlx-whisper not installed")

        t = MLXWhisperTranscriber()
        t._initialized = True
        t._mlx_whisper = "fake_module"
        t.cleanup()
        assert t.initialized is False
        assert t._mlx_whisper is None
