"""Tests for the transcriber module."""

from voicetext.transcriber import Transcriber


class TestVadHasSpeech:
    def test_empty_result(self):
        assert Transcriber._vad_has_speech(None) is False
        assert Transcriber._vad_has_speech([]) is False

    def test_no_speech_segments(self):
        # VAD returns empty segment lists when no speech detected
        assert Transcriber._vad_has_speech([[]]) is False

    def test_has_speech_segments(self):
        # VAD returns [[start_ms, end_ms], ...] per audio
        assert Transcriber._vad_has_speech([[[0, 1000]]]) is True
        assert Transcriber._vad_has_speech([[[100, 500], [800, 1200]]]) is True

    def test_non_list_result(self):
        assert Transcriber._vad_has_speech("unexpected") is False
        assert Transcriber._vad_has_speech(0) is False
