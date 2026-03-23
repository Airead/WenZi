"""Tests for the recorder module."""

import threading
import time
from unittest.mock import patch, MagicMock

import numpy as np

from wenzi.audio.recorder import Recorder


class TestRecorder:
    def test_init_defaults(self):
        r = Recorder()
        assert r.sample_rate == 16000
        assert r.is_recording is False

    def test_stop_without_start_returns_none(self):
        r = Recorder()
        assert r.stop() is None

    @patch("wenzi.audio.recorder.sd.RawInputStream")
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
        mock_stream.abort.assert_called_once()

    @patch("wenzi.audio.recorder.sd.RawInputStream")
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

    @patch("wenzi.audio.recorder.sd.RawInputStream")
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

    @patch("wenzi.audio.recorder.sd.RawInputStream")
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

    @patch("wenzi.audio.recorder.sd.RawInputStream")
    def test_current_level_capped_at_one(self, mock_stream_cls):
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r.start()

        # Frame of all 10000s → RMS = 10000 → level = min(1.0, 2.0) = 1.0
        frame_data = np.full(320, 10000, dtype=np.int16).tobytes()
        r._callback(frame_data, 320, None, None)
        assert r.current_level == 1.0

    @patch("wenzi.audio.recorder.sd.RawInputStream")
    def test_rms_calculated_in_callback(self, mock_stream_cls):
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r.start()

        frame_data = np.full(320, 500, dtype=np.int16).tobytes()
        r._callback(frame_data, 320, None, None)
        assert abs(r._current_rms - 500.0) < 1.0

    @patch("wenzi.audio.recorder.sd.RawInputStream")
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

    @patch("wenzi.audio.recorder.sd.RawInputStream")
    def test_start_skips_device_query_when_disabled(self, mock_stream_cls):
        """start() should skip _query_device_name when disabled."""
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r._query_device_name_enabled = False

        with patch.object(r, "_query_device_name") as mock_query:
            r.start()
            mock_query.assert_not_called()

        assert r.is_recording is True
        assert r._last_device_name is None
        r.stop()

    @patch("wenzi.audio.recorder.sd.RawInputStream")
    def test_start_queries_device_name_when_enabled(self, mock_stream_cls):
        """start() should call _query_device_name when enabled (default)."""
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        assert r._query_device_name_enabled is True

        with patch.object(r, "_query_device_name", return_value="TestMic") as mock_query:
            name = r.start()
            assert mock_query.called
            assert name == "TestMic"
        r.stop()

    @patch("wenzi.audio.recorder.sd.RawInputStream")
    def test_stop_returns_data_when_stream_close_hangs(self, mock_stream_cls):
        """stop() is non-blocking and returns audio data even if stream close hangs."""
        mock_stream = MagicMock()
        hang_event = threading.Event()
        mock_stream.abort.side_effect = lambda: hang_event.wait()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r.start()

        frame = np.full(320, 500, dtype=np.int16)
        r._queue.put(frame)

        wav_data = r.stop()
        # stop() returns immediately without waiting for stream close
        assert wav_data is not None
        assert r.is_recording is False
        assert not r._close_done.is_set()  # close still pending
        # Unblock the background thread to avoid leaking it
        hang_event.set()
        r._close_done.wait(timeout=1.0)
        assert r._close_done.is_set()

    def test_callback_guard_blocks_when_not_recording(self):
        """_callback should be a no-op when _recording is False."""
        r = Recorder(sample_rate=16000, block_ms=20)
        # _recording is False by default
        frame_data = np.full(320, 500, dtype=np.int16).tobytes()
        r._callback(frame_data, 320, None, None)
        # Nothing should be queued
        assert r._queue.empty()
        assert r._current_rms == 0.0

    @patch("wenzi.audio.recorder.sd.RawInputStream")
    def test_callback_guard_stops_after_stop(self, mock_stream_cls):
        """After stop(), the callback should no longer enqueue frames."""
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r.start()

        frame_data = np.full(320, 500, dtype=np.int16).tobytes()
        r._callback(frame_data, 320, None, None)
        assert r._queue.qsize() == 1

        r.stop()

        # Simulate orphaned stream still calling callback
        r._callback(frame_data, 320, None, None)
        # Queue was drained by stop(), and guard prevents new frames
        assert r._queue.empty()

    @patch("wenzi.audio.recorder.sd.RawInputStream")
    def test_start_waits_for_pending_close(self, mock_stream_cls, monkeypatch):
        """start() should wait briefly for a pending close before proceeding."""
        monkeypatch.setattr(Recorder, "_CLOSE_WAIT_TIMEOUT", 0.05)
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r.start()
        r._queue.put(np.full(320, 500, dtype=np.int16))
        r.stop()
        assert r._close_done.wait(timeout=1.0)  # normal close finishes

        # Second start should proceed without reinit
        r.start()
        assert r.is_recording is True
        r.stop()

    @patch("wenzi.audio.recorder.sd.RawInputStream")
    @patch("wenzi.audio.recorder.sd._terminate")
    @patch("wenzi.audio.recorder.sd._initialize")
    def test_start_reinits_on_close_timeout(
        self, mock_init, mock_term, mock_stream_cls, monkeypatch
    ):
        """start() should force PortAudio re-init when previous close is still pending."""
        monkeypatch.setattr(Recorder, "_CLOSE_WAIT_TIMEOUT", 0.01)
        mock_stream = MagicMock()
        hang_event = threading.Event()
        mock_stream.abort.side_effect = lambda: hang_event.wait()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r._query_device_name_enabled = False
        r.start()
        r._queue.put(np.full(320, 500, dtype=np.int16))
        r.stop()
        # close is still pending (stream.abort() hangs)
        assert not r._close_done.is_set()

        # Second start should detect pending close and reinit
        r.start()
        assert r.is_recording is True
        mock_term.assert_called()
        mock_init.assert_called()
        assert r._close_done.is_set()
        # Cleanup
        hang_event.set()
        r.stop()

    @patch("wenzi.audio.recorder.sd.RawInputStream")
    def test_starting_flag_prevents_concurrent_start(self, mock_stream_cls):
        """A second start() should return immediately when _starting_since is set."""
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r._starting_since = time.monotonic()
        result = r.start()
        # Should return without creating a stream
        assert result is r._last_device_name
        assert not r._recording

    @patch("wenzi.audio.recorder.sd.RawInputStream")
    def test_stale_starting_flag_is_reset(self, mock_stream_cls, monkeypatch):
        """A stuck _starting_since should be reset after the staleness timeout."""
        monkeypatch.setattr(Recorder, "_STARTING_STALE_SECS", 0.0)
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r._query_device_name_enabled = False
        r._starting_since = time.monotonic() - 1.0  # already stale
        r.start()
        # Should have reset _starting_since and proceeded
        assert r.is_recording is True
        r.stop()

    @patch("wenzi.audio.recorder.sd.RawInputStream")
    def test_stop_is_nonblocking(self, mock_stream_cls):
        """stop() should return without waiting for stream close."""
        mock_stream = MagicMock()

        # Make close take 10 seconds (we should NOT wait for it)
        def slow_close():
            time.sleep(10)
        mock_stream.close.side_effect = slow_close
        mock_stream_cls.return_value = mock_stream

        r = Recorder(sample_rate=16000, block_ms=20)
        r.start()
        r._queue.put(np.full(320, 500, dtype=np.int16))

        t0 = time.monotonic()
        r.stop()
        elapsed = time.monotonic() - t0
        # stop() should return in well under 1 second
        assert elapsed < 0.5
        # Cleanup: unblock by setting _close_done (stream.close will
        # eventually finish or be abandoned as daemon thread)
        r._close_done.set()
