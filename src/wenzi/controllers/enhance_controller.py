"""Controller for AI enhancement execution and caching."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING, Optional

from wenzi import async_loop
from wenzi.lru_cache import LRUCache

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from wenzi.enhance.enhancer import TextEnhancer
    from wenzi.enhance.manual_vocabulary import ManualVocabularyStore
    from wenzi.input_context import InputContext
    from wenzi.ui.result_window_web import ResultPreviewPanel
    from wenzi.usage_stats import UsageStats

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class _StreamResult:
    """Accumulated output from a single streaming enhancement pass."""

    collected: list[str]
    usage: dict | None


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

    Enhancement coroutines run on the shared asyncio event loop
    (see :mod:`wenzi.async_loop`).  Cancellation is driven by
    ``asyncio.Task.cancel()`` instead of ``threading.Event``.
    """

    def __init__(
        self,
        enhancer: Optional[TextEnhancer],
        preview_panel: ResultPreviewPanel,
        usage_stats: UsageStats,
        manual_vocab_store: "ManualVocabularyStore | None" = None,
        cache_maxsize: int = 128,
    ) -> None:
        self._enhancer = enhancer
        self._preview_panel = preview_panel
        self._usage_stats = usage_stats
        self._manual_vocab_store = manual_vocab_store
        self._cache: LRUCache[tuple, EnhanceCacheEntry] = LRUCache(
            maxsize=cache_maxsize
        )
        self._current_task: asyncio.Task | None = None
        self._enhance_mode: str = "off"
        self._last_pushed_asr_text: str = ""

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
        self._refresh_diffs_for_mode(value)

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

    def _refresh_diffs_for_mode(self, mode_id: str) -> None:
        """Refresh diff panel content based on the mode's track_corrections flag.

        Called on mode switch to immediately show or clear diffs.
        Display-only: does not record vocab hits to avoid inflating counts.
        """
        if not self._enhancer:
            return
        mode_def = self._enhancer.get_mode_definition(mode_id)
        if mode_def and mode_def.track_corrections:
            asr_text = self._last_pushed_asr_text
            enhanced = self._preview_panel.enhanced_text
            if asr_text and enhanced:
                self._push_diffs_display_only(asr_text, enhanced)
        else:
            self._preview_panel.clear_diffs()

    def _push_diffs_display_only(self, asr_text: str, enhanced: str) -> None:
        """Push diffs to the panel without recording vocab hits."""
        from wenzi.enhance.text_diff import extract_word_pairs

        try:
            pairs = extract_word_pairs(asr_text, enhanced)
            self._preview_panel.set_asr_diffs(pairs if pairs else [])
        except Exception as e:
            logger.warning("Failed to compute ASR diffs: %s", e)

        if self._manual_vocab_store is not None:
            try:
                self._preview_panel.set_manual_vocab_state(
                    self._manual_vocab_store.get_all_for_state(),
                )
            except Exception as e:
                logger.warning("Failed to sync manual vocab state: %s", e)

        self._push_vocab_hits_display_only(asr_text, enhanced)

    def _push_diffs_and_hits(self, asr_text: str, enhanced: str) -> None:
        """Compute word-level diffs and vocab hits, push to the preview panel."""
        from wenzi.enhance.text_diff import extract_word_pairs

        self._last_pushed_asr_text = asr_text
        self._preview_panel.cache_enhanced_text(enhanced)

        # ASR → Enhanced diffs
        try:
            pairs = extract_word_pairs(asr_text, enhanced)
            if pairs:
                self._preview_panel.set_asr_diffs(pairs)
        except Exception as e:
            logger.warning("Failed to compute ASR diffs: %s", e)

        # Manual vocab state sync
        if self._manual_vocab_store is not None:
            try:
                self._preview_panel.set_manual_vocab_state(
                    self._manual_vocab_store.get_all_for_state(),
                )
            except Exception as e:
                logger.warning("Failed to sync manual vocab state: %s", e)

        # Vocab hit detection
        self._push_vocab_hits(asr_text, enhanced)

    def _find_vocab_hits(self, asr_text: str, enhanced: str) -> list[dict]:
        """Find vocabulary hits without recording them."""
        hits: list[dict] = []
        if self._manual_vocab_store is None:
            return hits
        enhanced_lower = enhanced.lower()
        try:
            manual_hits = self._manual_vocab_store.find_hits_in_text(asr_text)
            for h in manual_hits:
                if h.term.lower() in enhanced_lower:
                    hits.append({
                        "variant": h.variant, "term": h.term,
                        "source": "manual",
                        "hitCount": h.hit_count,
                        "frequency": h.frequency,
                    })
        except Exception as e:
            logger.warning("Failed to detect manual vocab hits: %s", e)
        return hits

    def _push_vocab_hits_display_only(self, asr_text: str, enhanced: str) -> None:
        """Push vocab hits to the panel without recording them."""
        hits = self._find_vocab_hits(asr_text, enhanced)
        if hits:
            self._preview_panel.set_vocab_hits(hits)

    def _push_vocab_hits(self, asr_text: str, enhanced: str) -> None:
        """Detect, record, and push vocabulary hits to the side panel."""
        hits = self._find_vocab_hits(asr_text, enhanced)
        if hits:
            try:
                self._manual_vocab_store.record_hits([
                    (h["variant"], h["term"]) for h in hits
                ])
            except Exception as e:
                logger.warning("Failed to record vocab hits: %s", e)
        if hits:
            self._preview_panel.set_vocab_hits(hits)

    def cancel(self) -> None:
        """Cancel any in-flight enhancement.  Thread-safe."""
        if self._current_task is not None and not self._current_task.done():
            self._current_task.cancel()

    def run(
        self,
        asr_text: str,
        request_id: int,
        result_holder: dict | None = None,
        input_context: "InputContext | None" = None,
    ) -> None:
        """Submit AI enhancement as a coroutine on the shared event loop."""
        if not self._enhancer:
            return

        self.cancel()

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

        should_diff = current_mode_def is not None and current_mode_def.track_corrections

        if chain_steps:
            coro = self._run_chain_async(
                asr_text, request_id, result_holder,
                chain_steps, current_mode_def.mode_id,
                input_context=input_context,
                track_corrections=should_diff,
            )
        else:
            coro = self._run_single_async(
                asr_text, request_id, result_holder,
                input_context=input_context,
                track_corrections=should_diff,
            )

        wrapper = self._run_wrapper(coro, request_id)
        try:
            async_loop.submit(wrapper)
        except Exception:
            coro.close()
            wrapper.close()
            raise

    async def _run_wrapper(
        self, coro, request_id: int,
    ) -> None:
        """Capture the asyncio.Task reference and handle errors/cancellation."""
        self._current_task = asyncio.current_task()
        try:
            await coro
        except asyncio.CancelledError:
            logger.info("AI enhancement cancelled by user")
        except Exception as e:
            logger.error("AI enhancement failed: %s", e)
            self._preview_panel.set_enhance_result(
                f"(error: {e})", request_id=request_id
            )

    # ------------------------------------------------------------------
    # Shared streaming loop
    # ------------------------------------------------------------------

    async def _consume_stream(
        self,
        gen: AsyncGenerator,
        request_id: int,
        extra_collected: list[str] | None = None,
    ) -> _StreamResult:
        """Consume an enhance_stream generator, updating the preview panel.

        Returns the collected text chunks and final usage dict.
        *extra_collected*, when provided, also receives each content chunk
        (used by chain mode to build the combined display text).
        """
        collected: list[str] = []
        usage = None
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
                    self._preview_panel.set_llm_vocab(
                        self._enhancer.last_llm_vocab
                    )
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
                    thinking_tokens += len(chunk)
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
                    if extra_collected is not None:
                        extra_collected.append(chunk)
                    completion_tokens += len(chunk)
                    self._preview_panel.append_enhance_text(
                        chunk, request_id=request_id,
                        completion_tokens=completion_tokens,
                    )
                if chunk_usage is not None:
                    usage = chunk_usage
        finally:
            await gen.aclose()

        return _StreamResult(collected=collected, usage=usage)

    # ------------------------------------------------------------------
    # Single-step enhancement
    # ------------------------------------------------------------------

    async def _run_single_async(
        self, asr_text: str, request_id: int,
        result_holder: dict | None,
        input_context: "InputContext | None" = None,
        track_corrections: bool = False,
    ) -> None:
        """Run a single-step streaming enhancement as a coroutine."""
        gen = self._enhancer.enhance_stream(asr_text, input_context=input_context)
        result = await self._consume_stream(gen, request_id)

        display_text = "".join(result.collected)
        enhanced = display_text.strip() or asr_text
        system_prompt = self._enhancer.last_system_prompt
        if result_holder is not None:
            result_holder["enhanced_text"] = enhanced
            result_holder["system_prompt"] = system_prompt
            result_holder["thinking_text"] = self._preview_panel._thinking_text
            result_holder["token_usage"] = result.usage

        if result.collected:
            try:
                self._usage_stats.record_token_usage(result.usage)
            except Exception as e:
                logger.error("Failed to record token usage: %s", e)
            self._preview_panel.set_enhance_complete(
                request_id=request_id, usage=result.usage,
                system_prompt=system_prompt,
                final_text=enhanced,
            )
            self._cache[self.cache_key()] = EnhanceCacheEntry(
                display_text=display_text,
                usage=result.usage,
                system_prompt=system_prompt,
                thinking_text=self._preview_panel._thinking_text,
                final_text=enhanced,
            )
            if track_corrections:
                self._push_diffs_and_hits(asr_text, enhanced)
        else:
            self._preview_panel.set_enhance_label(
                "Connection failed", request_id=request_id,
            )

    # ------------------------------------------------------------------
    # Multi-step chain enhancement
    # ------------------------------------------------------------------

    async def _run_chain_async(
        self, asr_text: str, request_id: int,
        result_holder: dict | None,
        chain_steps: list[str], original_mode_id: str,
        input_context: "InputContext | None" = None,
        track_corrections: bool = False,
    ) -> None:
        """Run a multi-step chain enhancement as a coroutine."""
        total_steps = len(chain_steps)
        input_text = asr_text
        total_usage: dict[str, int] = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }
        all_display_parts: list[str] = []

        try:
            for step_idx, step_id in enumerate(chain_steps, 1):
                step_def = self._enhancer.get_mode_definition(step_id)
                step_label = step_def.label if step_def else step_id

                self._preview_panel.set_enhance_step_info(
                    step_idx, total_steps, step_label,
                )

                if step_idx > 1:
                    separator = f"\n\n--- {step_label} ---\n\n"
                    all_display_parts.append(separator)
                    self._preview_panel.append_enhance_text(
                        separator, request_id=request_id, completion_tokens=0,
                    )

                self._enhancer.mode = step_id

                gen = self._enhancer.enhance_stream(
                    input_text, input_context=input_context,
                )
                result = await self._consume_stream(
                    gen, request_id, extra_collected=all_display_parts,
                )

                step_result = "".join(result.collected).strip()
                if step_result:
                    input_text = step_result

                if result.usage:
                    total_usage["prompt_tokens"] += result.usage.get(
                        "prompt_tokens", 0,
                    )
                    total_usage["completion_tokens"] += result.usage.get(
                        "completion_tokens", 0,
                    )
                    total_usage["total_tokens"] += result.usage.get(
                        "total_tokens", 0,
                    )
                    try:
                        self._usage_stats.record_token_usage(result.usage)
                    except Exception as e:
                        logger.error("Failed to record token usage: %s", e)

            enhanced = input_text.strip() or asr_text
            system_prompt = self._enhancer.last_system_prompt
            final_usage = total_usage if total_usage["total_tokens"] > 0 else None
            if result_holder is not None:
                result_holder["enhanced_text"] = enhanced
                result_holder["system_prompt"] = system_prompt
                result_holder["thinking_text"] = self._preview_panel._thinking_text
                result_holder["is_chain"] = True
                result_holder["token_usage"] = final_usage
            self._preview_panel.set_enhance_complete(
                request_id=request_id,
                usage=final_usage,
                system_prompt=system_prompt,
                final_text=enhanced,
            )
            cache_key = (
                original_mode_id,
                self._enhancer.provider_name,
                self._enhancer.model_name,
                self._enhancer.thinking,
            )
            self._cache[cache_key] = EnhanceCacheEntry(
                display_text="".join(all_display_parts),
                usage=final_usage,
                system_prompt=system_prompt,
                thinking_text=self._preview_panel._thinking_text,
                final_text=enhanced,
            )
            if track_corrections:
                self._push_diffs_and_hits(asr_text, enhanced)
        finally:
            self._enhancer.mode = original_mode_id
