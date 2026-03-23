"""Audio recording using sounddevice."""

from __future__ import annotations

import io
import logging
import queue
import threading
import time
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
    # Brief grace period for a pending background stream close to finish
    # before forcing a PortAudio re-init in start().
    _CLOSE_WAIT_TIMEOUT = 0.5
    # Max seconds _starting may remain True before it is considered stuck
    # and forcibly reset, allowing a new start() to proceed.
    _STARTING_STALE_SECS = 10.0

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
        # Non-None while start() is in progress (value = monotonic timestamp).
        self._starting_since: Optional[float] = None
        self._total_bytes = 0
        self._current_rms: float = 0.0
        self._on_audio_chunk: Optional[callable] = None
        self._last_device_name: Optional[str] = None  # track last used device name
        self._query_device_name_enabled: bool = True
        # Signalled when the background stream-close thread finishes (or
        # on init when there is no pending close).
        self._close_done = threading.Event()
        self._close_done.set()

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def last_device_name(self) -> Optional[str]:
        """Return the last known input device name, or None."""
        return self._last_device_name

    @property
    def current_level(self) -> float:
        """Return current audio level normalized to 0.0-1.0.

        Uses ``_LEVEL_REFERENCE_RMS`` (800) as reference so normal
        speech (~1000-3000 RMS) maps to roughly 0.5-1.0.
        """
        return min(1.0, self._current_rms / self._LEVEL_REFERENCE_RMS)

    def start(self) -> Optional[str]:
        """Start recording. Returns the input device name, or None.

        Stream creation happens **outside** the lock so that a hung
        PortAudio call cannot deadlock subsequent ``stop()`` /
        ``is_recording`` calls.  ``_starting_since`` prevents
        concurrent ``start()`` calls from racing.
        """
        # --- Phase 0: wait for any pending background stream-close ------
        if not self._close_done.wait(timeout=self._CLOSE_WAIT_TIMEOUT):
            logger.warning(
                "Previous stream close still pending, forcing PortAudio re-init"
            )
            self._reinit_portaudio()
            self._close_done.set()

        # --- Phase 1: claim the "starting" slot (lock held briefly) -----
        with self._lock:
            if self._recording:
                return self._last_device_name
            if self._starting_since is not None:
                elapsed = time.monotonic() - self._starting_since
                if elapsed > self._STARTING_STALE_SECS:
                    logger.warning(
                        "Previous start() appears stuck (%.0fs), resetting",
                        elapsed,
                    )
                    self._starting_since = None
                else:
                    return self._last_device_name
            self._starting_since = time.monotonic()
            self._flush()
            self._total_bytes = 0
            device = self.device
            last_name = self._last_device_name

        # --- Phase 2: device query & PortAudio re-init (lock free) ------
        current_name: Optional[str] = None
        if self._query_device_name_enabled:
            current_name = self._query_device_name(device)

            # Re-initialize PortAudio only when the device has changed
            # (e.g. user plugged in a different mic) to avoid the cost
            # of terminate/initialize on every recording.
            if current_name != last_name:
                self._reinit_portaudio()
                current_name = self._query_device_name(device)

            if current_name != last_name:
                logger.info(
                    "Input device changed: %s -> %s",
                    last_name or "(none)",
                    current_name or "unknown",
                )
            else:
                logger.debug("Reusing input device: %s", current_name)

        # --- Phase 3: create & start stream (lock free) -----------------
        base_kwargs = dict(
            samplerate=self.sample_rate,
            blocksize=self._block_size,
            dtype="int16",
            channels=1,
            callback=self._callback,
        )
        try:
            stream = sd.RawInputStream(**base_kwargs, device=device)
            stream.start()
        except Exception:
            try:
                # Fallback to default input device
                stream = sd.RawInputStream(**base_kwargs)
                stream.start()
            except Exception:
                logger.error("Failed to create audio stream", exc_info=True)
                with self._lock:
                    self._starting_since = None
                return None

        # --- Phase 4: commit (lock held briefly) ------------------------
        with self._lock:
            self._stream = stream
            self._recording = True
            self._starting_since = None
            self._last_device_name = current_name
            logger.info("Recording started (sr=%d)", self.sample_rate)
            return current_name

    def stop(self) -> Optional[bytes]:
        """Stop recording and return WAV data as bytes, or None if nothing recorded.

        The callback guard (``if not self._recording``) provides a
        deterministic cutoff — no new frames are enqueued after
        ``_recording`` is set to ``False``.  Stream close is therefore
        fire-and-forget; we never need to block the caller waiting for
        PortAudio to finish.
        """
        with self._lock:
            if not self._recording:
                return None

            self._recording = False
            self._current_rms = 0.0
            stream = self._stream
            self._stream = None

        # Fire-and-forget stream close.  The _close_done event lets a
        # subsequent start() know whether cleanup has finished.
        if stream is not None:
            self._close_done.clear()

            def _close_stream() -> None:
                try:
                    stream.abort()
                    stream.close()
                except Exception as e:
                    logger.warning("Error closing audio stream: %s", e)
                finally:
                    self._close_done.set()

            threading.Thread(target=_close_stream, daemon=True).start()

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
        logger.info(
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

    def set_on_audio_chunk(self, cb: callable) -> None:
        """Set a callback invoked with each audio chunk (np.ndarray int16)."""
        self._on_audio_chunk = cb

    def clear_on_audio_chunk(self) -> None:
        """Remove the audio chunk callback."""
        self._on_audio_chunk = None

    def _callback(self, in_data, frames, time_info, status):
        # Guard: once _recording is False the callback becomes a no-op.
        # This prevents an orphaned (not-yet-closed) stream from
        # polluting the queue, RMS, or chunk callback.
        if not self._recording:
            return

        if status:
            logger.warning("Audio stream status: %s", status)

        frame = np.frombuffer(in_data, dtype=np.int16)
        frame_bytes = len(frame) * 2  # int16 = 2 bytes

        if self._total_bytes + frame_bytes > self.max_session_bytes:
            logger.warning("Max session size reached, dropping frames")
            return

        self._current_rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
        self._total_bytes += frame_bytes
        copied = frame.copy()
        try:
            self._queue.put_nowait(copied)
        except queue.Full:
            logger.warning("Audio queue full, dropping frame")

        cb = self._on_audio_chunk
        if cb is not None:
            try:
                cb(copied)
            except Exception:
                logger.debug("Audio chunk callback error", exc_info=True)

    @staticmethod
    def _reinit_portaudio() -> None:
        """Force-terminate and re-initialize PortAudio."""
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            logger.debug("PortAudio re-init failed, continuing", exc_info=True)

    @staticmethod
    def _query_device_name(device: Optional[str]) -> Optional[str]:
        """Return the name of the given input device, or None on failure."""
        try:
            info = sd.query_devices(device=device, kind="input")
            return info.get("name")
        except Exception:
            return None

    def _flush(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
