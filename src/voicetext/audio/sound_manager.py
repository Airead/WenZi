"""Sound feedback manager for VoiceText recording events."""

from __future__ import annotations

import logging
import os

import numpy as np
import soundfile as sf

from voicetext.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)

# Sounds directory under config: ~/.config/VoiceText/sounds/
SOUNDS_DIR = os.path.join(DEFAULT_CONFIG_DIR, "sounds")

# Default sound file name
DEFAULT_START_SOUND = "recording_start.wav"

# Sound generation parameters (Blow.aiff sped up 1.5x, trimmed to 0.3s)
_SAMPLE_RATE = 48000
_DURATION = 0.3
_SPEED_FACTOR = 1.5


def _ensure_sounds_dir(sounds_dir: str = SOUNDS_DIR) -> str:
    """Ensure the sounds directory exists and return its expanded path."""
    expanded = os.path.expanduser(sounds_dir)
    os.makedirs(expanded, exist_ok=True)
    return expanded


def _generate_default_start_sound(path: str) -> None:
    """Generate a short Blow-like start sound.

    Reads the macOS system Blow.aiff, speeds it up 1.5x and trims to 0.3s.
    Falls back to a synthesized chirp if the system sound is unavailable.
    """
    try:
        blow_path = "/System/Library/Sounds/Blow.aiff"
        if os.path.exists(blow_path):
            data, sr = sf.read(blow_path)
            from scipy.signal import resample

            n_out = int(len(data) / _SPEED_FACTOR)
            if data.ndim == 2:
                ch0 = resample(data[:, 0], n_out)
                ch1 = resample(data[:, 1], n_out)
                fast = np.column_stack([ch0, ch1])
            else:
                fast = resample(data, n_out)

            n = min(int(sr * _DURATION), len(fast))
            chunk = fast[:n].copy()
            fade_n = int(n * 0.3)
            fade = (1 + np.cos(np.linspace(0, np.pi, fade_n))) / 2
            if chunk.ndim == 2:
                chunk[-fade_n:, 0] *= fade
                chunk[-fade_n:, 1] *= fade
            else:
                chunk[-fade_n:] *= fade

            sf.write(path, chunk.astype(np.float32), sr)
            return
    except Exception as e:
        logger.debug("Could not process Blow.aiff: %s, using synthesized sound", e)

    # Fallback: synthesized soft chirp
    n = int(_SAMPLE_RATE * _DURATION)
    freq = np.linspace(400, 700, n)
    phase = 2 * np.pi * np.cumsum(freq) / _SAMPLE_RATE
    sig = np.sin(phase) + 0.3 * np.sin(phase * 2)
    sig = sig / np.max(np.abs(sig)) * 0.25
    envelope = (1 - np.cos(np.linspace(0, 2 * np.pi, n))) / 2
    sig *= envelope
    sf.write(path, sig.astype(np.float32), _SAMPLE_RATE)


def ensure_start_sound(config_dir: str | None = None) -> str:
    """Return path to the start sound, generating it if missing.

    Users can replace ~/.config/VoiceText/sounds/recording_start.wav
    with their own sound file.
    """
    if config_dir:
        sdir = os.path.join(config_dir, "sounds")
    else:
        sdir = SOUNDS_DIR
    sounds_dir = _ensure_sounds_dir(sdir)
    path = os.path.join(sounds_dir, DEFAULT_START_SOUND)
    if not os.path.exists(path):
        logger.info("Generating default start sound: %s", path)
        _generate_default_start_sound(path)
    return path


class SoundManager:
    """Play sound feedback for recording events."""

    def __init__(
        self,
        enabled: bool = True,
        volume: float = 0.4,
        config_dir: str | None = None,
    ) -> None:
        self._enabled = enabled
        self._volume = volume
        self._start_sound_path = ensure_start_sound(config_dir)
        self._cached_sound: object = None  # Cached NSSound instance

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def warmup(self) -> None:
        """Pre-load the NSSound object on the main thread.

        Call via AppHelper.callAfter() after the event loop starts to
        eliminate first-play latency.
        """
        if self._cached_sound is not None:
            return
        try:
            from AppKit import NSSound

            if not os.path.exists(self._start_sound_path):
                return
            sound = NSSound.alloc().initWithContentsOfFile_byReference_(
                self._start_sound_path, True
            )
            if sound is not None:
                sound.setVolume_(self._volume)
                self._cached_sound = sound
                logger.debug("NSSound pre-loaded: %s", self._start_sound_path)
        except Exception as e:
            logger.debug("NSSound warmup failed: %s", e)

    def play(self, event: str) -> None:
        """Play the sound for the given event. Only 'start' is supported."""
        if not self._enabled:
            return

        if event != "start":
            return

        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self._play_on_main_thread)
        except Exception as e:
            logger.warning("Failed to schedule sound playback: %s", e)

    def _play_on_main_thread(self) -> None:
        """Actually play the sound file. Must be called on the main thread."""
        try:
            if self._cached_sound is not None:
                # Stop any ongoing playback and replay from the beginning
                self._cached_sound.stop()
                self._cached_sound.play()
                return

            # Fallback: load on demand if warmup was not called
            from AppKit import NSSound

            if not os.path.exists(self._start_sound_path):
                logger.warning("Sound file not found: %s", self._start_sound_path)
                return

            sound = NSSound.alloc().initWithContentsOfFile_byReference_(
                self._start_sound_path, True
            )
            if sound is None:
                logger.warning("Failed to load sound: %s", self._start_sound_path)
                return
            sound.setVolume_(self._volume)
            sound.play()
            self._cached_sound = sound
        except Exception as e:
            logger.warning("Failed to play sound: %s", e)
