"""Tests for WhisperAPITranscriber."""

from __future__ import annotations

import io
import struct
import wave
from unittest.mock import MagicMock, patch

from wenzi.transcription.whisper_api import WhisperAPITranscriber

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wav(num_samples: int = 160, sample_rate: int = 16000) -> bytes:
    """Return a minimal valid WAV byte string."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{num_samples}h", *([0] * num_samples)))
    return buf.getvalue()


def _make_transcriber(**kwargs) -> WhisperAPITranscriber:
    defaults = dict(
        base_url="https://api.example.com/v1",
        api_key="test-api-key",
        model="whisper-large-v3",
    )
    defaults.update(kwargs)
    return WhisperAPITranscriber(**defaults)


def _make_initialized_transcriber(text="hello", **kwargs) -> WhisperAPITranscriber:
    """Create an initialized WhisperAPITranscriber with a mock client."""
    t = _make_transcriber(**kwargs)
    t._initialized = True
    mock_client = MagicMock()
    mock_client.create.return_value = text
    t._client = mock_client
    return t


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_defaults(self):
        t = _make_transcriber()
        assert t._base_url == "https://api.example.com/v1"
        assert t._api_key == "test-api-key"
        assert t._model == "whisper-large-v3"
        assert t._language is None
        assert t._temperature == 0.0
        assert t._client is None
        assert t._initialized is False

    def test_custom_language_and_temperature(self):
        t = _make_transcriber(language="zh", temperature=0.2)
        assert t._language == "zh"
        assert t._temperature == 0.2

    def test_temperature_none_defaults_to_zero(self):
        t = _make_transcriber(temperature=None)
        assert t._temperature == 0.0


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class TestProperties:
    def test_initialized_initially_false(self):
        t = _make_transcriber()
        assert t.initialized is False

    def test_initialized_after_set(self):
        t = _make_transcriber()
        t._initialized = True
        assert t.initialized is True

    def test_model_display_name(self):
        t = _make_transcriber(model="whisper-large-v3-turbo")
        assert t.model_display_name == "whisper-large-v3-turbo"


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------

class TestInitialize:
    def test_initialize_creates_client(self):
        t = _make_transcriber()
        mock_client = MagicMock()

        with patch("wenzi.llm_http.TranscriptionClient", return_value=mock_client) as mock_cls:
            t.initialize()

        mock_cls.assert_called_once_with(
            base_url="https://api.example.com/v1",
            api_key="test-api-key",
        )
        assert t._client is mock_client
        assert t._initialized is True

    def test_initialize_idempotent(self):
        """Calling initialize() twice should only create the client once."""
        t = _make_transcriber()
        mock_client = MagicMock()

        with patch("wenzi.llm_http.TranscriptionClient", return_value=mock_client) as mock_cls:
            t.initialize()
            t.initialize()

        mock_cls.assert_called_once()

    def test_initialize_sets_initialized_flag(self):
        t = _make_transcriber()
        with patch("wenzi.llm_http.TranscriptionClient"):
            t.initialize()
        assert t._initialized is True


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_cleanup_resets_state(self):
        t = _make_transcriber()
        t._initialized = True
        t._client = MagicMock()

        t.cleanup()

        assert t._client is None
        assert t._initialized is False

    def test_cleanup_when_not_initialized(self):
        t = _make_transcriber()
        t.cleanup()  # should not raise
        assert t._initialized is False


# ---------------------------------------------------------------------------
# transcribe
# ---------------------------------------------------------------------------

class TestTranscribe:
    def test_transcribe_returns_stripped_text(self):
        t = _make_initialized_transcriber(text="  hello world  ")
        result = t.transcribe(_make_wav())
        assert result == "hello world"

    def test_transcribe_calls_initialize_when_not_ready(self):
        t = _make_transcriber()
        mock_client = MagicMock()
        mock_client.create.return_value = "ok"

        def fake_init():
            t._initialized = True
            t._client = mock_client

        with patch.object(t, "initialize", side_effect=fake_init):
            result = t.transcribe(_make_wav())
        assert result == "ok"

    def test_transcribe_sends_model_and_temperature(self):
        t = _make_initialized_transcriber(temperature=0.3)
        t.transcribe(_make_wav())

        _, kwargs = t._client.create.call_args
        assert kwargs["model"] == "whisper-large-v3"
        assert kwargs["temperature"] == 0.3

    def test_transcribe_includes_language_when_set(self):
        t = _make_initialized_transcriber(language="zh")
        t.transcribe(_make_wav())

        _, kwargs = t._client.create.call_args
        assert kwargs.get("language") == "zh"

    def test_transcribe_omits_language_when_none(self):
        t = _make_initialized_transcriber(language=None)
        t.transcribe(_make_wav())

        _, kwargs = t._client.create.call_args
        assert "language" not in kwargs

    def test_transcribe_sends_file_with_wav_name(self):
        t = _make_initialized_transcriber()
        t.transcribe(_make_wav())

        _, kwargs = t._client.create.call_args
        audio_file = kwargs["file"]
        assert hasattr(audio_file, "read")  # BytesIO-like
        assert audio_file.name == "audio.wav"

    def test_transcribe_sends_correct_audio_bytes(self):
        t = _make_initialized_transcriber()
        wav_data = _make_wav()
        t.transcribe(wav_data)

        _, kwargs = t._client.create.call_args
        audio_file = kwargs["file"]
        assert audio_file.read() == wav_data


# ---------------------------------------------------------------------------
# verify_provider (static method)
# ---------------------------------------------------------------------------

class TestVerifyProvider:
    def test_returns_none_on_success(self):
        mock_client = MagicMock()
        mock_client.create.return_value = ""

        with patch("wenzi.llm_http.TranscriptionClient", return_value=mock_client):
            result = WhisperAPITranscriber.verify_provider(
                base_url="https://api.example.com/v1",
                api_key="key",
                model="whisper-large-v3",
            )

        assert result is None

    def test_returns_error_string_on_failure(self):
        with patch(
            "wenzi.llm_http.TranscriptionClient",
            side_effect=Exception("connection refused"),
        ):
            result = WhisperAPITranscriber.verify_provider(
                base_url="https://bad.url/v1",
                api_key="key",
                model="whisper-large-v3",
            )

        assert result == "connection refused"

    def test_returns_error_string_on_api_error(self):
        mock_client = MagicMock()
        mock_client.create.side_effect = Exception("401 Unauthorized")

        with patch("wenzi.llm_http.TranscriptionClient", return_value=mock_client):
            result = WhisperAPITranscriber.verify_provider(
                base_url="https://api.example.com/v1",
                api_key="bad-key",
                model="whisper-large-v3",
            )

        assert "401" in result

    def test_sends_silent_wav(self):
        """verify_provider must send a valid WAV file to the API."""
        mock_client = MagicMock()
        mock_client.create.return_value = ""

        with patch("wenzi.llm_http.TranscriptionClient", return_value=mock_client):
            WhisperAPITranscriber.verify_provider(
                base_url="https://api.example.com/v1",
                api_key="key",
                model="whisper-large-v3",
            )

        _, kwargs = mock_client.create.call_args
        audio_file = kwargs["file"]
        # Verify we sent a proper WAV
        audio_file.seek(0)
        with wave.open(audio_file, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getframerate() == 16000
            assert wf.getsampwidth() == 2
            assert wf.getnframes() == 8000  # 0.5s at 16kHz

    def test_creates_client_with_given_credentials(self):
        mock_client = MagicMock()
        mock_client.create.return_value = ""

        with patch("wenzi.llm_http.TranscriptionClient", return_value=mock_client) as mock_cls:
            WhisperAPITranscriber.verify_provider(
                base_url="https://custom.api/v1",
                api_key="mykey",
                model="my-model",
            )

        mock_cls.assert_called_once_with(
            base_url="https://custom.api/v1",
            api_key="mykey",
        )

    def test_passes_correct_model(self):
        mock_client = MagicMock()
        mock_client.create.return_value = ""

        with patch("wenzi.llm_http.TranscriptionClient", return_value=mock_client):
            WhisperAPITranscriber.verify_provider(
                base_url="https://api.example.com/v1",
                api_key="key",
                model="distil-whisper",
            )

        _, kwargs = mock_client.create.call_args
        assert kwargs["model"] == "distil-whisper"


# ---------------------------------------------------------------------------
# Hotwords
# ---------------------------------------------------------------------------

class TestHotwords:
    def test_hotwords_stored(self):
        t = _make_transcriber(hotwords=["Python", "Kubernetes"])
        assert t._hotwords == ["Python", "Kubernetes"]

    def test_hotwords_default_none(self):
        t = _make_transcriber()
        assert t._hotwords is None

    def test_transcribe_with_hotwords_adds_prompt(self):
        t = _make_initialized_transcriber(hotwords=["Python", "Kubernetes"])
        t.transcribe(_make_wav())
        _, kwargs = t._client.create.call_args
        assert kwargs["prompt"] == "Python, Kubernetes"

    def test_transcribe_without_hotwords_no_prompt(self):
        t = _make_initialized_transcriber()
        t.transcribe(_make_wav())
        _, kwargs = t._client.create.call_args
        assert "prompt" not in kwargs

    def test_dynamic_hotwords_override_static(self):
        t = _make_initialized_transcriber(hotwords=["StaticWord"])
        t.transcribe(_make_wav(), hotwords=["DynamicWord"])
        _, kwargs = t._client.create.call_args
        assert kwargs["prompt"] == "DynamicWord"

    def test_fallback_to_static_when_dynamic_none(self):
        t = _make_initialized_transcriber(hotwords=["StaticWord"])
        t.transcribe(_make_wav(), hotwords=None)
        _, kwargs = t._client.create.call_args
        assert kwargs["prompt"] == "StaticWord"

    def test_transcribe_accepts_hotwords_kwarg(self):
        t = _make_initialized_transcriber()
        result = t.transcribe(_make_wav(), hotwords=["Python"])
        assert result == "hello"
