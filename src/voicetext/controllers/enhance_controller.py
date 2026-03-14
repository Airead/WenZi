"""Controller for AI enhancement execution and caching."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import threading
from typing import TYPE_CHECKING, Optional

from voicetext.lru_cache import LRUCache

if TYPE_CHECKING:
    from voicetext.enhance.enhancer import TextEnhancer
    from voicetext.ui.result_window import ResultPreviewPanel
    from voicetext.usage_stats import UsageStats

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class EnhanceCacheEntry:
    """Cached result of an AI enhancement run."""

    display_text: str
    usage: dict | None
    system_prompt: str
    thinking_text: str
    final_text: str | None


class EnhanceController:
    """Manages AI enhancement execution, streaming, and result caching.

    Extracted from VoiceTextApp to reduce the size and complexity of app.py.
    Follows the same pattern as ModelController.
    """

    def __init__(
        self,
        enhancer: Optional[TextEnhancer],
        preview_panel: ResultPreviewPanel,
        usage_stats: UsageStats,
        cache_maxsize: int = 128,
    ) -> None:
        self._enhancer = enhancer
        self._preview_panel = preview_panel
        self._usage_stats = usage_stats
        self._cache: LRUCache[tuple, EnhanceCacheEntry] = LRUCache(
            maxsize=cache_maxsize
        )
        self._cancel_event: threading.Event | None = None
        self._enhance_mode: str = "off"

    @property
    def enhancer(self) -> Optional[TextEnhancer]:
        return self._enhancer

    @enhancer.setter
    def enhancer(self, value: Optional[TextEnhancer]) -> None:
        self._enhancer = value

    @property
    def enhance_mode(self) -> str:
        return self._enhance_mode

    @enhance_mode.setter
    def enhance_mode(self, value: str) -> None:
        self._enhance_mode = value

    @property
    def cancel_event(self) -> threading.Event | None:
        return self._cancel_event

    def cache_key(self) -> tuple:
        """Build cache key from current enhance settings."""
        return (
            self._enhance_mode,
            self._enhancer.provider_name if self._enhancer else "",
            self._enhancer.model_name if self._enhancer else "",
            self._enhancer.thinking if self._enhancer else False,
        )

    def get_cached(self) -> EnhanceCacheEntry | None:
        """Return cached result for current settings, or None."""
        return self._cache.get(self.cache_key())

    def clear_cache(self) -> None:
        """Clear all cached enhancement results."""
        self._cache.clear()

    def cancel(self) -> None:
        """Cancel any in-flight enhancement."""
        if self._cancel_event is not None:
            self._cancel_event.set()

    def run(
        self,
        asr_text: str,
        request_id: int,
        result_holder: dict | None = None,
    ) -> None:
        """Run AI enhancement in a background thread with streaming."""
        if not self._enhancer:
            return

        # Cancel any in-flight enhancement and create a fresh cancel event
        if self._cancel_event is not None:
            self._cancel_event.set()
        cancel_event = threading.Event()
        self._cancel_event = cancel_event

        # Resolve chain steps
        current_mode_def = self._enhancer.get_mode_definition(self._enhance_mode)
        chain_steps: list[str] = []
        if current_mode_def and current_mode_def.steps:
            for step_id in current_mode_def.steps:
                step_def = self._enhancer.get_mode_definition(step_id)
                if step_def:
                    chain_steps.append(step_id)
                else:
                    logger.warning("Chain step '%s' not found, skipping", step_id)

        def _enhance():
            try:
                if chain_steps:
                    self._run_chain(
                        asr_text, request_id, result_holder, cancel_event,
                        chain_steps, current_mode_def.mode_id,
                    )
                else:
                    self._run_single(
                        asr_text, request_id, result_holder, cancel_event,
                    )
            except Exception as e:
                logger.error("AI enhancement failed: %s", e)
                self._preview_panel.set_enhance_result(
                    f"(error: {e})", request_id=request_id
                )

        threading.Thread(target=_enhance, daemon=True).start()

    def _run_single(
        self, asr_text: str, request_id: int,
        result_holder: dict | None, cancel_event: threading.Event,
    ) -> None:
        """Run a single-step streaming enhancement."""
        loop = asyncio.new_event_loop()
        collected: list[str] = []
        usage = None
        cancelled = False

        async def _stream():
            nonlocal usage, cancelled
            gen = self._enhancer.enhance_stream(asr_text)
            completion_tokens = 0
            thinking_tokens = 0
            had_thinking = False
            first_chunk = True
            try:
                async for chunk, chunk_usage, is_thinking in gen:
                    if first_chunk:
                        first_chunk = False
                        self._preview_panel.update_system_prompt(
                            self._enhancer.last_system_prompt
                        )
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        return
                    if is_thinking == "retry" and chunk:
                        had_thinking = True
                        self._preview_panel.append_thinking_text(
                            chunk, request_id=request_id,
                            thinking_tokens=0,
                        )
                        label = chunk.strip().strip("()\n")
                        self._preview_panel.set_enhance_label(
                            f"\u23f3 {label}", request_id=request_id,
                        )
                    elif is_thinking and chunk:
                        had_thinking = True
                        thinking_tokens += 1
                        self._preview_panel.append_thinking_text(
                            chunk, request_id=request_id,
                            thinking_tokens=thinking_tokens,
                        )
                    elif chunk:
                        if had_thinking:
                            had_thinking = False
                            self._preview_panel.clear_enhance_text(
                                request_id=request_id,
                            )
                        collected.append(chunk)
                        completion_tokens += 1
                        self._preview_panel.append_enhance_text(
                            chunk, request_id=request_id,
                            completion_tokens=completion_tokens,
                        )
                    if chunk_usage is not None:
                        usage = chunk_usage
            finally:
                await gen.aclose()

        loop.run_until_complete(_stream())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

        if cancelled:
            logger.info("AI enhancement cancelled by user")
            return

        enhanced = "".join(collected).strip() or asr_text
        if result_holder is not None:
            result_holder["enhanced_text"] = enhanced

        if collected:
            try:
                self._usage_stats.record_token_usage(usage)
            except Exception as e:
                logger.error("Failed to record token usage: %s", e)
            system_prompt = self._enhancer.last_system_prompt
            self._preview_panel.set_enhance_complete(
                request_id=request_id, usage=usage,
                system_prompt=system_prompt,
            )
            display_text = "".join(collected)
            cache_key = self.cache_key()
            self._cache[cache_key] = EnhanceCacheEntry(
                display_text=display_text,
                usage=usage,
                system_prompt=system_prompt,
                thinking_text=self._preview_panel._thinking_text,
                final_text=None,
            )
        else:
            # All retries failed — update label, don't touch Final Result
            self._preview_panel.set_enhance_label(
                "Connection failed", request_id=request_id,
            )

    def _run_chain(
        self, asr_text: str, request_id: int,
        result_holder: dict | None, cancel_event: threading.Event,
        chain_steps: list[str], original_mode_id: str,
    ) -> None:
        """Run a multi-step chain enhancement with streaming."""
        loop = asyncio.new_event_loop()
        total_steps = len(chain_steps)
        input_text = asr_text
        total_usage: dict[str, int] = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }
        cancelled = False

        try:
            for step_idx, step_id in enumerate(chain_steps, 1):
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break

                step_def = self._enhancer.get_mode_definition(step_id)
                step_label = step_def.label if step_def else step_id

                # Show step info in the label
                self._preview_panel.set_enhance_step_info(
                    step_idx, total_steps, step_label,
                )

                # Append separator before non-first steps
                if step_idx > 1:
                    separator = f"\n\n--- {step_label} ---\n\n"
                    self._preview_panel.append_enhance_text(
                        separator, request_id=request_id, completion_tokens=0,
                    )

                # Set enhancer mode to this step
                self._enhancer.mode = step_id

                collected: list[str] = []
                step_usage = None

                async def _stream_step(text_input: str) -> None:
                    nonlocal step_usage, cancelled
                    gen = self._enhancer.enhance_stream(text_input)
                    completion_tokens = 0
                    thinking_tokens = 0
                    had_thinking = False
                    first_chunk = True
                    try:
                        async for chunk, chunk_usage, is_thinking in gen:
                            if first_chunk:
                                first_chunk = False
                                self._preview_panel.update_system_prompt(
                                    self._enhancer.last_system_prompt
                                )
                            if cancel_event is not None and cancel_event.is_set():
                                cancelled = True
                                return
                            if is_thinking == "retry" and chunk:
                                had_thinking = True
                                self._preview_panel.append_thinking_text(
                                    chunk, request_id=request_id,
                                    thinking_tokens=0,
                                )
                                label = chunk.strip().strip("()\n")
                                self._preview_panel.set_enhance_label(
                                    f"\u23f3 {label}", request_id=request_id,
                                )
                            elif is_thinking and chunk:
                                had_thinking = True
                                thinking_tokens += 1
                                self._preview_panel.append_thinking_text(
                                    chunk, request_id=request_id,
                                    thinking_tokens=thinking_tokens,
                                )
                            elif chunk:
                                if had_thinking:
                                    had_thinking = False
                                collected.append(chunk)
                                completion_tokens += 1
                                self._preview_panel.append_enhance_text(
                                    chunk, request_id=request_id,
                                    completion_tokens=completion_tokens,
                                )
                            if chunk_usage is not None:
                                step_usage = chunk_usage
                    finally:
                        await gen.aclose()

                loop.run_until_complete(_stream_step(input_text))

                if cancelled:
                    break

                # This step's output becomes next step's input
                step_result = "".join(collected).strip()
                if step_result:
                    input_text = step_result

                # Accumulate token usage
                if step_usage:
                    total_usage["prompt_tokens"] += step_usage.get(
                        "prompt_tokens", 0,
                    )
                    total_usage["completion_tokens"] += step_usage.get(
                        "completion_tokens", 0,
                    )
                    total_usage["total_tokens"] += step_usage.get(
                        "total_tokens", 0,
                    )
                try:
                    self._usage_stats.record_token_usage(step_usage)
                except Exception as e:
                    logger.error("Failed to record token usage: %s", e)

            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

            if cancelled:
                logger.info("AI enhancement chain cancelled by user")
                return

            # Final result is the last step's output
            enhanced = input_text.strip() or asr_text
            if result_holder is not None:
                result_holder["enhanced_text"] = enhanced
            system_prompt = self._enhancer.last_system_prompt
            self._preview_panel.set_enhance_complete(
                request_id=request_id,
                usage=total_usage if total_usage["total_tokens"] > 0 else None,
                system_prompt=system_prompt,
                final_text=enhanced,
            )
            display_text = (
                str(self._preview_panel._enhance_text_view.string())
                if self._preview_panel._enhance_text_view else ""
            )
            cache_key = (
                original_mode_id,
                self._enhancer.provider_name,
                self._enhancer.model_name,
                self._enhancer.thinking,
            )
            self._cache[cache_key] = EnhanceCacheEntry(
                display_text=display_text,
                usage=total_usage if total_usage["total_tokens"] > 0 else None,
                system_prompt=system_prompt,
                thinking_text=self._preview_panel._thinking_text,
                final_text=enhanced,
            )
        finally:
            # Restore enhancer mode to the original chain mode id
            self._enhancer.mode = original_mode_id
