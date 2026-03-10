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
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)
        self._stream: Optional[sd.RawInputStream] = None
        self._lock = threading.Lock()
        self._recording = False
        self._total_bytes = 0

    @property
    def is_recording(self) -> bool:
        return self._recording

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
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None

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
