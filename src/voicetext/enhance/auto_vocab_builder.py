"""Automatic vocabulary building triggered by correction count threshold."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Dict, Optional

from voicetext.statusbar import send_notification

logger = logging.getLogger(__name__)


class AutoVocabBuilder:
    """Silently builds vocabulary in the background when enough corrections accumulate."""

    def __init__(
        self,
        config: Dict[str, Any],
        enabled: bool = True,
        threshold: int = 10,
        on_build_done: Optional[Callable[[], None]] = None,
        conversation_history: Any = None,
    ) -> None:
        self._config = config
        self._enabled = enabled
        self._threshold = threshold
        self._counter = 0
        self._building = False
        self._lock = threading.Lock()
        self._enhancer = None
        self._on_build_done = on_build_done
        self._conversation_history = conversation_history

    def set_enhancer(self, enhancer: Any) -> None:
        """Bind the TextEnhancer instance (needed for vocab_index reload)."""
        self._enhancer = enhancer

    def on_correction_logged(self) -> None:
        """Increment counter and trigger build if threshold is reached."""
        if not self._enabled:
            return

        with self._lock:
            self._counter += 1
            if self._counter < self._threshold:
                return
            if self._building:
                return
            self._counter = 0
            self._building = True

        self._run_silent_build()

    def is_building(self) -> bool:
        """Return True if a background build is in progress."""
        return self._building

    def _run_silent_build(self) -> None:
        """Start a background thread to build vocabulary silently."""
        t = threading.Thread(target=self._build, daemon=True)
        t.start()

    def _build(self) -> None:
        """Execute incremental vocabulary build, reload index, and notify."""
        try:
            from .vocabulary_builder import VocabularyBuilder

            ai_cfg = self._config.get("ai_enhance", {})
            builder = VocabularyBuilder(
                ai_cfg, conversation_history=self._conversation_history,
            )

            loop = asyncio.new_event_loop()
            summary = loop.run_until_complete(builder.build())
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

            # Reload vocabulary index
            if self._enhancer and self._enhancer.vocab_index is not None:
                self._enhancer.vocab_index.reload()

            new_entries = summary.get("new_entries", 0)
            total_entries = summary.get("total_entries", 0)
            logger.info(
                "Auto vocabulary build completed: %d new, %d total",
                new_entries,
                total_entries,
            )

            if self._on_build_done:
                try:
                    self._on_build_done()
                except Exception:
                    logger.debug("on_build_done callback failed", exc_info=True)

            if new_entries > 0:
                try:
                    send_notification(
                        "VoiceText",
                        "Vocabulary Auto-Built",
                        f"{new_entries} new entries ({total_entries} total)",
                    )
                except Exception:
                    logger.debug("Notification center unavailable")
        except Exception as e:
            logger.error("Auto vocabulary build failed: %s", e)
        finally:
            with self._lock:
                self._building = False
