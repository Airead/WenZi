"""VoiceText macOS menubar application."""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Optional

import rumps
from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions
from CoreFoundation import kCFBooleanTrue

from .config import load_config
from .hotkey import HoldHotkeyListener
from .input import type_text
from .recorder import Recorder
from .transcriber import Transcriber


logger = logging.getLogger(__name__)


class VoiceTextApp(rumps.App):
    """Menubar app: hold hotkey to record, release to transcribe and type."""

    def __init__(self, config_path: Optional[str] = None) -> None:
        super().__init__("VoiceText", icon=None, title="VT")

        self._config = load_config(config_path)
        self._setup_logging()

        audio_cfg = self._config["audio"]
        self._recorder = Recorder(
            sample_rate=audio_cfg["sample_rate"],
            block_ms=audio_cfg["block_ms"],
            device=audio_cfg.get("device"),
            max_session_bytes=audio_cfg["max_session_bytes"],
            silence_rms=audio_cfg.get("silence_rms", Recorder.DEFAULT_SILENCE_RMS),
        )

        asr_cfg = self._config["asr"]
        self._transcriber = Transcriber(
            use_vad=asr_cfg["use_vad"],
            use_punc=asr_cfg["use_punc"],
        )

        self._output_method = self._config["output"]["method"]
        self._append_newline = self._config["output"]["append_newline"]
        self._hotkey_listener: Optional[HoldHotkeyListener] = None
        self._busy = False

        # Menu items
        self._status_item = rumps.MenuItem("Ready")
        self._status_item.set_callback(None)
        hotkey_name = self._config["hotkey"]
        self._hotkey_item = rumps.MenuItem(f"Hotkey: {hotkey_name}")
        self._hotkey_item.set_callback(None)
        self.menu = [self._status_item, self._hotkey_item, None]

    def _setup_logging(self) -> None:
        level = self._config["logging"]["level"]
        logging.basicConfig(
            level=getattr(logging, level, logging.INFO),
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        )

    def _set_status(self, text: str) -> None:
        """Update menu bar title and status menu item."""
        self.title = text
        self._status_item.title = text

    def _on_hotkey_press(self) -> None:
        """Called when hotkey is pressed down - start recording."""
        if self._busy:
            return
        logger.info("Hotkey pressed, starting recording")
        self._set_status("Recording...")
        self._recorder.start()

    def _on_hotkey_release(self) -> None:
        """Called when hotkey is released - stop recording and transcribe."""
        if not self._recorder.is_recording:
            return
        logger.info("Hotkey released, stopping recording")
        wav_data = self._recorder.stop()
        if not wav_data:
            self._set_status("VT")
            return

        self._busy = True
        self._set_status("Transcribing...")

        # Run transcription in background to keep UI responsive
        def _do_transcribe():
            try:
                text = self._transcriber.transcribe(wav_data)
                if text and text.strip():
                    type_text(
                        text.strip(),
                        append_newline=self._append_newline,
                        method=self._output_method,
                    )
                    self._set_status("VT")
                else:
                    self._set_status("(empty)")
                    logger.warning("Transcription returned empty text")
            except Exception as e:
                logger.error("Transcription failed: %s", e)
                self._set_status("Error")
            finally:
                self._busy = False

        threading.Thread(target=_do_transcribe, daemon=True).start()

    @rumps.clicked("Quit")
    def on_quit(self, _) -> None:
        if self._hotkey_listener:
            self._hotkey_listener.stop()
        rumps.quit_application()

    @staticmethod
    def _ensure_accessibility() -> bool:
        """Check and prompt for accessibility permission if needed."""
        if AXIsProcessTrusted():
            logger.info("Accessibility permission granted")
            return True
        # Only prompt if not yet trusted
        options = {"AXTrustedCheckOptionPrompt": kCFBooleanTrue}
        AXIsProcessTrustedWithOptions(options)
        logger.warning("Accessibility permission not granted, prompting user")
        return False

    def run(self, **kwargs) -> None:
        """Initialize models and start the app."""
        self._ensure_accessibility()

        # Load models in background
        def _init_models():
            try:
                self._set_status("Loading...")
                self._transcriber.initialize()
                self._set_status("VT")
                logger.info("Models loaded, app ready")
            except Exception as e:
                logger.error("Model initialization failed: %s", e)
                self._set_status("Error")

        threading.Thread(target=_init_models, daemon=True).start()

        # Start hotkey listener
        hotkey_name = self._config["hotkey"]
        self._hotkey_listener = HoldHotkeyListener(
            key_name=hotkey_name,
            on_press=self._on_hotkey_press,
            on_release=self._on_hotkey_release,
        )
        self._hotkey_listener.start()

        super().run(**kwargs)


def main() -> None:
    """Entry point."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    app = VoiceTextApp(config_path=config_path)
    app.run()


if __name__ == "__main__":
    main()
