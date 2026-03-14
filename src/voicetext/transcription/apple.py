"""Apple Speech (SFSpeechRecognizer) transcriber for macOS."""

from __future__ import annotations

import logging
import tempfile
import threading
import time
from typing import Callable, Optional

import numpy as np

from .base import BaseTranscriber

logger = logging.getLogger(__name__)

_LANG_TO_LOCALE = {
    "zh": "zh-CN",
    "en": "en-US",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "fr": "fr-FR",
    "de": "de-DE",
    "es": "es-ES",
    "it": "it-IT",
    "pt": "pt-BR",
    "ru": "ru-RU",
}

RECOGNITION_TIMEOUT = 30  # seconds
STREAMING_FINAL_TIMEOUT = 10  # seconds


def _resolve_locale(language: str) -> str:
    """Convert short language code to BCP-47 locale."""
    if "-" in language or "_" in language:
        return language
    return _LANG_TO_LOCALE.get(language, language)


class AppleSpeechTranscriber(BaseTranscriber):
    """Speech-to-text using macOS built-in SFSpeechRecognizer."""

    skip_punc = True  # Apple Speech produces punctuated output

    def __init__(
        self,
        language: str = "zh",
        on_device: bool = True,
    ) -> None:
        self._language = language
        self._locale_id = _resolve_locale(language)
        self._on_device = on_device
        self._initialized = False
        self._recognizer = None

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def model_display_name(self) -> str:
        mode = "On-Device" if self._on_device else "Server"
        return f"Apple Speech ({mode})"

    def initialize(self) -> None:
        """Request authorization and create the recognizer."""
        if self._initialized:
            return

        logger.info(
            "Initializing Apple Speech recognizer (locale=%s, on_device=%s)",
            self._locale_id,
            self._on_device,
        )

        import Speech
        from Foundation import NSLocale

        # Request authorization (blocking via threading.Event)
        auth_event = threading.Event()
        auth_status = [None]

        def _on_auth(status):
            auth_status[0] = status
            auth_event.set()

        Speech.SFSpeechRecognizer.requestAuthorization_(_on_auth)

        # Drive the RunLoop so the callback fires on this thread
        from CoreFoundation import CFRunLoopRunInMode, kCFRunLoopDefaultMode

        deadline = time.monotonic() + 10
        while not auth_event.is_set() and time.monotonic() < deadline:
            CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.1, False)

        if not auth_event.is_set():
            raise PermissionError(
                "Apple Speech authorization timed out. "
                "Grant access in System Settings > Privacy & Security > Speech Recognition."
            )

        if auth_status[0] != Speech.SFSpeechRecognizerAuthorizationStatusAuthorized:
            raise PermissionError(
                "Apple Speech recognition not authorized. "
                "Grant access in System Settings > Privacy & Security > Speech Recognition."
            )

        locale = NSLocale.alloc().initWithLocaleIdentifier_(self._locale_id)
        recognizer = Speech.SFSpeechRecognizer.alloc().initWithLocale_(locale)

        if recognizer is None or not recognizer.isAvailable():
            raise RuntimeError(
                f"SFSpeechRecognizer is not available for locale {self._locale_id!r}."
            )

        # Check on-device support
        if self._on_device and not recognizer.supportsOnDeviceRecognition():
            logger.warning(
                "On-device recognition not supported for %s, falling back to server mode.",
                self._locale_id,
            )
            self._on_device = False

        self._recognizer = recognizer
        self._initialized = True
        logger.info("Apple Speech recognizer ready (locale=%s)", self._locale_id)

    def transcribe(self, wav_data: bytes) -> str:
        """Transcribe WAV audio bytes using SFSpeechRecognizer."""
        if not self._initialized:
            self.initialize()

        import Speech
        from Foundation import NSURL
        from CoreFoundation import CFRunLoopRunInMode, kCFRunLoopDefaultMode

        # Write WAV to a temporary file for SFSpeechURLRecognitionRequest
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_data)
            tmp_path = tmp.name

        try:
            url = NSURL.fileURLWithPath_(tmp_path)
            request = Speech.SFSpeechURLRecognitionRequest.alloc().initWithURL_(url)

            if self._on_device:
                request.setRequiresOnDeviceRecognition_(True)

            result_holder = [None]
            error_holder = [None]
            done_event = threading.Event()

            def _handler(result, error):
                if error is not None:
                    error_holder[0] = error
                if result is not None and result.isFinal():
                    result_holder[0] = result
                    done_event.set()
                elif error is not None:
                    done_event.set()

            self._recognizer.recognitionTaskWithRequest_resultHandler_(
                request, _handler
            )

            # Drive RunLoop until recognition completes or times out
            deadline = time.monotonic() + RECOGNITION_TIMEOUT
            while not done_event.is_set() and time.monotonic() < deadline:
                CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.1, False)

            if not done_event.is_set():
                logger.warning("Apple Speech recognition timed out after %ds", RECOGNITION_TIMEOUT)
                return ""

            if error_holder[0] is not None and result_holder[0] is None:
                logger.error("Apple Speech recognition error: %s", error_holder[0])
                return ""

            if result_holder[0] is not None:
                text = result_holder[0].bestTranscription().formattedString()
                logger.info("Transcription result: %s", text[:100])
                return text

            return ""
        finally:
            import os
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ── Streaming interface ───────────────────────────────────────────

    @property
    def supports_streaming(self) -> bool:
        return True

    def start_streaming(self, on_partial: Callable[[str, bool], None]) -> None:
        """Begin a streaming recognition session using SFSpeechAudioBufferRecognitionRequest."""
        if not self._initialized:
            self.initialize()

        import Speech
        from AVFoundation import AVAudioFormat

        self._stream_on_partial = on_partial
        self._stream_final_text: Optional[str] = None
        self._stream_best_text: str = ""
        self._stream_done = threading.Event()
        self._stream_cancelled = False

        request = Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
        if self._on_device:
            request.setRequiresOnDeviceRecognition_(True)
        request.setShouldReportPartialResults_(True)

        self._stream_request = request

        # Audio format: mono float32 at 16kHz
        # AVAudioCommonFormat values: 1=Float32, 2=Float64, 3=Int16, 4=Int32
        audio_format = AVAudioFormat.alloc().initWithCommonFormat_sampleRate_channels_interleaved_(
            1,  # AVAudioPCMFormatFloat32
            float(16000),
            1,
            False,
        )
        self._stream_audio_format = audio_format

        def _handler(result, error):
            if self._stream_cancelled:
                return
            if error is not None:
                logger.warning("Streaming recognition error: %s", error)
            if result is not None:
                text = result.bestTranscription().formattedString()
                is_final = result.isFinal()
                logger.debug(
                    "Streaming result: text=%r (len=%d), best=%r (len=%d), final=%s",
                    text[:50] if text else "", len(text) if text else 0,
                    self._stream_best_text[:50], len(self._stream_best_text),
                    is_final,
                )
                # Keep the best (longest) partial text seen so far.
                # Apple Speech may send shorter/empty partials during
                # silence, which would erase the displayed text.
                if text and len(text) >= len(self._stream_best_text):
                    self._stream_best_text = text
                    try:
                        on_partial(text, is_final)
                    except Exception:
                        logger.debug("on_partial callback error", exc_info=True)
                if is_final:
                    self._stream_final_text = self._stream_best_text or text
                    self._stream_done.set()
            elif error is not None:
                self._stream_done.set()

        self._stream_task = self._recognizer.recognitionTaskWithRequest_resultHandler_(
            request, _handler
        )

        # Start a RunLoop thread to drive callbacks
        self._stream_runloop_stop = threading.Event()

        def _run_loop():
            from CoreFoundation import CFRunLoopRunInMode, kCFRunLoopDefaultMode

            while not self._stream_runloop_stop.is_set():
                CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.05, False)

        self._stream_runloop_thread = threading.Thread(target=_run_loop, daemon=True)
        self._stream_runloop_thread.start()

        logger.info("Streaming recognition started")

    def feed_audio(self, samples: np.ndarray) -> None:
        """Feed int16 audio samples to the streaming request.

        Converts int16 → float32 (range -1.0 to 1.0) for AVAudioPCMBuffer.
        """
        if not hasattr(self, "_stream_request") or self._stream_request is None:
            return

        from AVFoundation import AVAudioPCMBuffer

        n_samples = len(samples)
        buffer = AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(
            self._stream_audio_format, n_samples
        )
        buffer.setFrameLength_(n_samples)

        # Convert int16 → float32 and copy into the buffer
        float_samples = samples.astype(np.float32) / 32768.0
        float_channel = buffer.floatChannelData()
        if float_channel is not None:
            ptr = float_channel[0]
            for i in range(n_samples):
                ptr[i] = float(float_samples[i])

        self._stream_request.appendAudioPCMBuffer_(buffer)

    def stop_streaming(self) -> str:
        """End audio input and return the final transcription."""
        if not hasattr(self, "_stream_request") or self._stream_request is None:
            return ""

        self._stream_request.endAudio()
        self._stream_done.wait(timeout=STREAMING_FINAL_TIMEOUT)

        # Cleanup
        self._stream_runloop_stop.set()
        # Prefer the final result; fall back to the best partial seen
        text = self._stream_final_text or self._stream_best_text or ""
        self._cleanup_stream()

        logger.info("Streaming recognition stopped, result: %s", text[:100] if text else "(empty)")
        return text

    def cancel_streaming(self) -> None:
        """Cancel the streaming session."""
        self._stream_cancelled = True
        if hasattr(self, "_stream_task") and self._stream_task is not None:
            self._stream_task.cancel()
        self._cleanup_stream()
        logger.info("Streaming recognition cancelled")

    def _cleanup_stream(self) -> None:
        """Clean up streaming session resources."""
        if hasattr(self, "_stream_runloop_stop"):
            self._stream_runloop_stop.set()
        self._stream_request = None
        self._stream_task = None
        self._stream_audio_format = None
        self._stream_on_partial = None
        self._stream_best_text = ""

    def cleanup(self) -> None:
        """Release the recognizer."""
        if hasattr(self, "_stream_request") and self._stream_request is not None:
            self.cancel_streaming()
        self._recognizer = None
        self._initialized = False
        logger.info("Apple Speech recognizer cleaned up")
