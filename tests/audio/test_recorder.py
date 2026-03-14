"""Tests for the recorder module."""

import threading
from unittest.mock import patch, MagicMock

import numpy as np

from voicetext.audio.recorder import Recorder


class TestRecorder:
    def test_init_defaults(self):
        r = Recorder()
        assert r.sample_rate == 16000
        assert r.is_recording is False

    def test_stop_without_start_returns_none(self):
        r = Recorder()
        assert r.stop() is None

    @patch("voicetext.audio.recorder.sd.RawInputStream")
    def test_start_stop_cycle(self, mock_stream_cls):
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r.start()
        assert r.is_recording is True

        # Simulate audio frames with enough energy to pass silence check
        frame = np.full(320, 500, dtype=np.int16)
        r._queue.put(frame)
        r._queue.put(frame)

        wav_data = r.stop()
        assert r.is_recording is False
        assert wav_data is not None
        assert len(wav_data) > 0

        mock_stream.start.assert_called_once()
        mock_stream.stop.assert_called_once()

    @patch("voicetext.audio.recorder.sd.RawInputStream")
    def test_silence_detection_discards_quiet_audio(self, mock_stream_cls):
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20, silence_rms=20)
        r.start()

        # Simulate silent audio (all zeros -> RMS=0)
        frame = np.zeros(320, dtype=np.int16)
        r._queue.put(frame)
        r._queue.put(frame)

        wav_data = r.stop()
        assert wav_data is None

    @patch("voicetext.audio.recorder.sd.RawInputStream")
    def test_silence_detection_passes_loud_audio(self, mock_stream_cls):
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20, silence_rms=20)
        r.start()

        # Simulate audio with high energy (RMS=1000)
        frame = np.full(320, 1000, dtype=np.int16)
        r._queue.put(frame)

        wav_data = r.stop()
        assert wav_data is not None

    def test_double_start_is_noop(self):
        r = Recorder()
        r._recording = True
        r.start()  # Should not raise

    def test_current_level_initial_zero(self):
        r = Recorder()
        assert r.current_level == 0.0

    @patch("voicetext.audio.recorder.sd.RawInputStream")
    def test_current_level_after_callback(self, mock_stream_cls):
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r.start()

        # Simulate a callback with known RMS
        # Frame of all 500s → RMS = 500 → level = 500/800 = 0.625
        frame_data = np.full(320, 500, dtype=np.int16).tobytes()
        r._callback(frame_data, 320, None, None)
        assert abs(r.current_level - 0.625) < 0.01

    @patch("voicetext.audio.recorder.sd.RawInputStream")
    def test_current_level_capped_at_one(self, mock_stream_cls):
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r.start()

        # Frame of all 10000s → RMS = 10000 → level = min(1.0, 2.0) = 1.0
        frame_data = np.full(320, 10000, dtype=np.int16).tobytes()
        r._callback(frame_data, 320, None, None)
        assert r.current_level == 1.0

    @patch("voicetext.audio.recorder.sd.RawInputStream")
    def test_rms_calculated_in_callback(self, mock_stream_cls):
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r.start()

        frame_data = np.full(320, 500, dtype=np.int16).tobytes()
        r._callback(frame_data, 320, None, None)
        assert abs(r._current_rms - 500.0) < 1.0

    @patch("voicetext.audio.recorder.sd.RawInputStream")
    def test_max_session_bytes(self, mock_stream_cls):
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20, max_session_bytes=640)
        r.start()

        # Simulate callback with frames that exceed limit
        frame_data = np.zeros(320, dtype=np.int16).tobytes()
        r._callback(frame_data, 320, None, None)  # 640 bytes, at limit
        r._callback(frame_data, 320, None, None)  # Should be dropped

        assert r._queue.qsize() == 1

    @patch("voicetext.audio.recorder.sd.RawInputStream")
    def test_stop_returns_data_when_stream_close_hangs(self, mock_stream_cls):
        """stop() should return audio data even if stream.stop() hangs."""
        mock_stream = MagicMock()
        # Make stream.stop() block forever (simulating a hung PortAudio callback)
        hang_event = threading.Event()
        mock_stream.stop.side_effect = lambda: hang_event.wait()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r.start()

        frame = np.full(320, 500, dtype=np.int16)
        r._queue.put(frame)

        wav_data = r.stop()
        # Should succeed despite the hung stream.stop()
        assert wav_data is not None
        assert r.is_recording is False
        # Unblock the background thread to avoid leaking it
        hang_event.set()
