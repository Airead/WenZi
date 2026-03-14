"""Audio recording using sounddevice."""

from __future__ import annotations

import io
import logging
import queue
import threading
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf


logger = logging.getLogger(__name__)


class Recorder:
    """Record audio from the microphone. Thread-safe start/stop."""

    # RMS threshold for silence detection (int16 range: 0-32768).
    # Typical quiet room noise is ~100-300, speech is ~1000+.
    DEFAULT_SILENCE_RMS = 20
    # Reference RMS for normalizing current_level to 0.0-1.0 range.
    # Normal speech (~1000-3000 RMS) maps to roughly 0.5-1.0.
    _LEVEL_REFERENCE_RMS = 800.0
    # Timeout for stream stop/close to prevent blocking on hung PortAudio.
    _STREAM_CLOSE_TIMEOUT = 2.0

    def __init__(
        self,
        sample_rate: int = 16000,
        block_ms: int = 20,
        device: Optional[str] = None,
        max_session_bytes: int = 20 * 1024 * 1024,
        silence_rms: int = DEFAULT_SILENCE_RMS,
    ) -> None:
        self.sample_rate = sample_rate
        self.block_ms = block_ms
        self.device = device
        self.max_session_bytes = max_session_bytes
        self.silence_rms = silence_rms

        self._block_size = int(sample_rate * block_ms / 1000)
        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: Optional[sd.RawInputStream] = None
        self._lock = threading.Lock()
        self._recording = False
        self._total_bytes = 0
        self._current_rms: float = 0.0

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def current_level(self) -> float:
        """Return current audio level normalized to 0.0-1.0.

        Uses 2000 as reference so normal speech (~1000-3000 RMS) maps
        to roughly 0.5-1.0.
        """
        return min(1.0, self._current_rms / self._LEVEL_REFERENCE_RMS)

    def start(self) -> None:
        """Start recording."""
        with self._lock:
            if self._recording:
                return

            self._flush()
            self._total_bytes = 0

            try:
                self._stream = sd.RawInputStream(
                    samplerate=self.sample_rate,
                    blocksize=self._block_size,
                    dtype="int16",
                    channels=1,
                    callback=self._callback,
                    device=self.device,
                )
                self._stream.start()
            except Exception:
                # Fallback to default input device
                self._stream = sd.RawInputStream(
                    samplerate=self.sample_rate,
                    blocksize=self._block_size,
                    dtype="int16",
                    channels=1,
                    callback=self._callback,
                )
                self._stream.start()

            self._recording = True
            logger.info("Recording started (sr=%d)", self.sample_rate)

    def stop(self) -> Optional[bytes]:
        """Stop recording and return WAV data as bytes, or None if nothing recorded."""
        with self._lock:
            if not self._recording:
                return None

            self._recording = False
            stream = self._stream
            self._stream = None

        # Stop/close stream outside the lock with a timeout so a hung
        # PortAudio callback cannot block the caller (e.g. the Quartz
        # event-tap thread) forever.
        if stream is not None:
            done = threading.Event()

            def _close_stream() -> None:
                try:
                    stream.stop()
                    stream.close()
                except Exception as e:
                    logger.warning("Error closing audio stream: %s", e)
                finally:
                    done.set()

            threading.Thread(target=_close_stream, daemon=True).start()
            if not done.wait(timeout=self._STREAM_CLOSE_TIMEOUT):
                logger.error(
                    "Audio stream stop/close timed out after %.1fs, "
                    "continuing without waiting",
                    self._STREAM_CLOSE_TIMEOUT,
                )

        # Collect all buffered frames
        frames = []
        while not self._queue.empty():
            try:
                frames.append(self._queue.get_nowait())
            except queue.Empty:
                break

        if not frames:
            logger.warning("No audio frames captured")
            return None

        audio = np.concatenate(frames)
        duration = len(audio) / self.sample_rate
        rms = int(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
        logger.warning(
            "Recording stopped, captured %d samples (%.1fs), RMS=%d",
            len(audio), duration, rms,
        )

        if rms < self.silence_rms:
            logger.warning("Audio below silence threshold (RMS=%d < %d), discarding",
                           rms, self.silence_rms)
            return None

        # Encode as WAV in memory
        buf = io.BytesIO()
        sf.write(buf, audio, self.sample_rate, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    def _callback(self, in_data, frames, time_info, status):
        if status:
            logger.warning("Audio stream status: %s", status)

        frame = np.frombuffer(in_data, dtype=np.int16)
        frame_bytes = len(frame) * 2  # int16 = 2 bytes

        if self._total_bytes + frame_bytes > self.max_session_bytes:
            logger.warning("Max session size reached, dropping frames")
            return

        self._current_rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
        self._total_bytes += frame_bytes
        try:
            self._queue.put_nowait(frame.copy())
        except queue.Full:
            logger.warning("Audio queue full, dropping frame")

    def _flush(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
