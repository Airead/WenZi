"""Apple Speech (SFSpeechRecognizer) transcriber for macOS."""

from __future__ import annotations

import logging
import tempfile
import threading
import time

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

    def cleanup(self) -> None:
        """Release the recognizer."""
        self._recognizer = None
        self._initialized = False
        logger.info("Apple Speech recognizer cleaned up")
