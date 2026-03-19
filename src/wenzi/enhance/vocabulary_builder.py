"""Build structured vocabulary from correction logs using LLM extraction."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from pathlib import Path

from wenzi.config import DEFAULT_DATA_DIR, DEFAULT_LOG_DIR
from .conversation_history import ConversationHistory
from .enhancer import build_thinking_body
from .repetition import detect_repetition, truncate_repeated
from .text_diff import inline_diff

logger = logging.getLogger(__name__)

# Bundled word lists for filtering common words that LLMs already know.
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_ENGLISH_WORDS_PATH = os.path.join(_DATA_DIR, "english_words.txt")
_CHINESE_WORDS_PATH = os.path.join(_DATA_DIR, "chinese_words.txt")


def _load_common_words() -> set[str]:
    """Load bundled English + Chinese word lists. Caller is responsible for releasing."""
    words: set[str] = set()
    for path in (_ENGLISH_WORDS_PATH, _CHINESE_WORDS_PATH):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                words.update(line.strip().lower() for line in f if line.strip())
            logger.debug("Loaded words from %s (total so far: %d)", path, len(words))
        except OSError:
            logger.warning("Word list not found at %s, skipping", path)
    return words


@dataclass
class BuildCallbacks:
    """Callbacks for vocabulary build progress reporting."""

    on_progress_init: Optional[Callable[[int, int], None]] = None  # (total_records, batch_size)
    on_batch_start: Optional[Callable[[int, int], None]] = None  # (batch_idx, total)
    on_stream_chunk: Optional[Callable[[str], None]] = None  # (chunk_text)
    on_batch_done: Optional[Callable[[int, int, int], None]] = None  # (batch_idx, total, entries_count)
    on_batch_retry: Optional[Callable[[int, int], None]] = None  # (batch_idx, total)
    on_usage_update: Optional[Callable[[int, int, int, int], None]] = None  # (input, cached, output, total)


class VocabularyBuilder:
    """Extract vocabulary entries from user corrections using LLM."""

    def __init__(
        self,
        config: Dict[str, Any],
        data_dir: str = DEFAULT_DATA_DIR,
        conversation_history: Optional[ConversationHistory] = None,
    ) -> None:
        self._config = config
        self._data_dir = os.path.expanduser(data_dir)
        self._conversation_history = conversation_history or ConversationHistory(data_dir=self._data_dir)
        self._vocab_path = os.path.join(self._data_dir, "vocabulary.json")
        self._batch_size = config.get("vocabulary", {}).get("batch_size", 60)
        self._batch_timeout = config.get("vocabulary", {}).get("build_timeout", 600)
        self._english_words: set[str] = set()

    async def build(
        self,
        full_rebuild: bool = False,
        cancel_event: Optional[threading.Event] = None,
        callbacks: Optional[BuildCallbacks] = None,
    ) -> Dict[str, Any]:
        """Build or update the vocabulary from correction logs.

        Uses a multi-turn session when records exceed batch_size to leverage
        KV cache: the system prompt and prior turns are cached by the
        provider, and the LLM naturally avoids duplicate extractions by
        seeing its previous responses.

        Args:
            full_rebuild: If True, reprocess all corrections from scratch.
            cancel_event: If set, the build will stop after the current batch.
            callbacks: Optional callbacks for progress reporting and streaming.

        Returns a summary dict with counts.
        """
        file_handler = self._setup_build_log()
        try:
            return await self._build_inner(
                full_rebuild=full_rebuild,
                cancel_event=cancel_event,
                callbacks=callbacks,
            )
        finally:
            self._english_words = set()
            self._teardown_build_log(file_handler)

    def _setup_build_log(self) -> Optional[logging.FileHandler]:
        """Attach a DEBUG file handler so the entire build is captured."""
        log_dir = Path(os.path.expanduser(DEFAULT_LOG_DIR))
        log_path = log_dir / "vocab_build.log"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(str(log_path), mode="w", encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-5s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            logger.addHandler(fh)
            logger.debug("Build log: %s", log_path)
            return fh
        except OSError as e:
            logger.warning("Failed to create build log at %s: %s", log_path, e)
            return None

    @staticmethod
    def _teardown_build_log(file_handler: Optional[logging.FileHandler]) -> None:
        """Remove the build-specific file handler."""
        if file_handler is not None:
            logger.removeHandler(file_handler)
            file_handler.close()

    async def _build_inner(
        self,
        full_rebuild: bool = False,
        cancel_event: Optional[threading.Event] = None,
        callbacks: Optional[BuildCallbacks] = None,
    ) -> Dict[str, Any]:
        """Core build logic, wrapped by :meth:`build` for log management."""
        self._english_words = _load_common_words()
        logger.info(
            "Starting vocabulary build (full_rebuild=%s)",
            full_rebuild,
        )
        existing = self._load_existing_vocabulary()

        # Read ALL correction records once — used for both batch processing
        # and final frequency counting.
        all_corrections = self._read_corrections(since=None)

        since = None
        if not full_rebuild and existing:
            since = existing.get("last_processed_timestamp")
            logger.info("Incremental build since: %s", since)

        # Filter to new records for LLM batch processing, capped to avoid
        # excessive LLM calls when correction history is large.
        max_records = self._config.get("vocabulary", {}).get("max_build_records", 300)
        if since:
            records = [r for r in all_corrections if r.get("timestamp", "") > since]
        else:
            records = list(all_corrections)
        if len(records) > max_records:
            logger.info(
                "Capping LLM processing to most recent %d of %d records",
                max_records, len(records),
            )
            records = records[-max_records:]

        if not records:
            logger.info("No new correction records to process")
            return {"new_records": 0, "new_entries": 0, "total_entries": len(existing.get("entries", []))}

        batches = self._batch_records(records, self._batch_size)
        logger.info("Processing %d records in %d batches", len(records), len(batches))
        all_new_entries: List[Dict[str, Any]] = []
        total_usage: Dict[str, int] = {
            "input_tokens": 0, "cached_tokens": 0,
            "output_tokens": 0, "total_tokens": 0,
        }
        merged_entries = list(existing.get("entries", []))
        records_processed = 0
        last_processed_ts = existing.get("last_processed_timestamp", "")
        cancelled = False
        aborted = False

        provider_cfg = self._get_provider_config()
        existing_terms = [e.get("term", "") for e in merged_entries if e.get("term")]
        logger.debug(
            "Provider config: provider=%s, model=%s",
            self._get_active_provider_name(),
            provider_cfg["model"] if provider_cfg else "N/A",
        )
        logger.debug("Existing vocabulary: %d terms", len(existing_terms))

        if callbacks and callbacks.on_progress_init:
            callbacks.on_progress_init(len(records), self._batch_size)

        # Create a single client for all batches to avoid connection pool leaks
        from openai import AsyncOpenAI

        client = None
        if provider_cfg:
            client = AsyncOpenAI(
                base_url=provider_cfg["base_url"],
                api_key=provider_cfg["api_key"],
            )

        # Static system prompt — cacheable across all batches
        system_prompt = self._build_system_prompt()
        logger.debug("System prompt (%d chars):\n%s", len(system_prompt), system_prompt)

        try:
            for i, batch in enumerate(batches, 1):
                if cancel_event is not None and cancel_event.is_set():
                    logger.info("Build cancelled before batch %d/%d", i, len(batches))
                    cancelled = True
                    break

                # Each batch is independent — system + user only
                user_prompt = self._build_user_prompt(batch, existing_terms=existing_terms)
                logger.debug(
                    "Batch %d/%d user prompt (%d chars):\n%s",
                    i, len(batches), len(user_prompt), user_prompt,
                )
                messages: List[Dict[str, str]] = [
                    {"role": "system", "content": system_prompt},
                ]
                on_chunk = callbacks.on_stream_chunk if callbacks else None

                # Try extraction with one retry on failure
                extracted = None
                batch_usage: Dict[str, int] = {}
                response_text = ""
                for attempt in range(2):
                    try:
                        if attempt == 0:
                            if callbacks and callbacks.on_batch_start:
                                callbacks.on_batch_start(i, len(batches))
                        else:
                            if cancel_event is not None and cancel_event.is_set():
                                logger.info("Build cancelled before retry of batch %d/%d", i, len(batches))
                                break
                            logger.info("Retrying batch %d/%d...", i, len(batches))
                            if callbacks and callbacks.on_batch_retry:
                                callbacks.on_batch_retry(i, len(batches))

                        logger.info(
                            "Extracting batch %d/%d (%d records, attempt %d)...",
                            i, len(batches), len(batch), attempt + 1,
                        )
                        extracted, batch_usage, response_text = await self._extract_batch(
                            messages, user_prompt, client=client,
                            on_stream_chunk=on_chunk, cancel_event=cancel_event,
                        )
                        break
                    except Exception as e:
                        if attempt == 0:
                            logger.warning(
                                "Batch %d/%d failed (attempt 1), will retry: %s",
                                i, len(batches), e,
                            )
                        else:
                            logger.error(
                                "Batch %d/%d failed after retry, aborting build: %s",
                                i, len(batches), e,
                            )

                if extracted is None:
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                    else:
                        aborted = True
                    break

                # Mid-stream cancel: _extract_batch returned empty results
                if cancel_event is not None and cancel_event.is_set() and not extracted:
                    logger.info("Build cancelled mid-stream at batch %d/%d", i, len(batches))
                    cancelled = True
                    break

                # Cross-validate: term must appear in corrected texts
                # (right side of diffs).  Terms only found on the ASR
                # side are misrecognitions, not real vocabulary entries.
                # Use word-boundary regex for ASCII terms to avoid
                # substring false positives (e.g. "Git" inside "GitHub").
                final_texts = " ".join(
                    r.get("final_text", "") for r in batch
                ).lower()
                before_count = len(extracted)
                extracted = [
                    e for e in extracted
                    if self._term_in_texts(e["term"].lower(), final_texts)
                ]
                dropped = before_count - len(extracted)
                if dropped:
                    logger.debug(
                        "Cross-validation dropped %d entries not found in corrected texts",
                        dropped,
                    )

                # Batch succeeded — accumulate results
                for key in total_usage:
                    total_usage[key] += batch_usage.get(key, 0)
                if callbacks and callbacks.on_usage_update:
                    callbacks.on_usage_update(
                        total_usage["input_tokens"],
                        total_usage["cached_tokens"],
                        total_usage["output_tokens"],
                        total_usage["total_tokens"],
                    )
                logger.info("Batch %d/%d: extracted %d entries", i, len(batches), len(extracted))
                if batch_usage:
                    logger.debug(
                        "Batch %d/%d tokens: input=%d, cached=%d, output=%d, total=%d",
                        i, len(batches),
                        batch_usage.get("input_tokens", 0),
                        batch_usage.get("cached_tokens", 0),
                        batch_usage.get("output_tokens", 0),
                        batch_usage.get("total_tokens", 0),
                    )
                for entry in extracted:
                    logger.debug(
                        "  + %s [%s] variants=%s context=%s",
                        entry.get("term"), entry.get("category"),
                        entry.get("variants"), entry.get("context"),
                    )
                all_new_entries.extend(extracted)
                records_processed += len(batch)

                if callbacks and callbacks.on_batch_done:
                    callbacks.on_batch_done(i, len(batches), len(extracted))

                # Persist progress after each successful batch
                merged_entries = self._merge_entries(merged_entries, extracted)
                # Update existing_terms for next batch's dedup hint
                existing_terms = [e.get("term", "") for e in merged_entries if e.get("term")]
                last_processed_ts = batch[-1].get("timestamp", datetime.now(timezone.utc).isoformat())
                self._save_vocabulary(self._build_vocab_dict(
                    last_processed_ts, provider_cfg, total_usage, merged_entries,
                ))
        finally:
            if client is not None:
                await client.close()

        # Recount frequencies using ALL correction records for accuracy.
        # This replaces the batch-extraction count with actual correction counts.
        if merged_entries and all_corrections and records_processed > 0:
            self._count_frequencies(merged_entries, all_corrections)
            self._save_vocabulary(self._build_vocab_dict(
                last_processed_ts, provider_cfg, total_usage, merged_entries,
            ))
            logger.info("Frequency recount complete using %d correction records", len(all_corrections))

        summary: Dict[str, Any] = {
            "new_records": records_processed,
            "new_entries": len(all_new_entries),
            "total_entries": len(merged_entries),
            "usage": total_usage,
        }
        if cancelled:
            summary["cancelled"] = True
        if aborted:
            summary["aborted"] = True

        status_parts = []
        if cancelled:
            status_parts.append("cancelled")
        if aborted:
            status_parts.append("aborted")
        status_suffix = f" ({', '.join(status_parts)})" if status_parts else ""

        logger.info(
            "Vocabulary built: %d/%d records, %d new entries, %d total entries, "
            "tokens(input=%d, cached=%d, output=%d, total=%d)%s",
            records_processed,
            len(records),
            summary["new_entries"],
            summary["total_entries"],
            total_usage["input_tokens"],
            total_usage["cached_tokens"],
            total_usage["output_tokens"],
            total_usage["total_tokens"],
            status_suffix,
        )
        return summary

    def _read_corrections(
        self, since: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Read correction records from conversation history, optionally filtered by timestamp."""
        return self._conversation_history.get_corrections(since=since)

    def _batch_records(
        self, records: List[Dict[str, Any]], batch_size: int = 20
    ) -> List[List[Dict[str, Any]]]:
        """Split records into batches."""
        return [
            records[i : i + batch_size]
            for i in range(0, len(records), batch_size)
        ]

    def _build_system_prompt(self) -> str:
        """Build the system prompt (static, cacheable across batches)."""
        return (
            "你是一个词汇提取助手。从语音识别(ASR)纠错记录中提取ASR容易误识别的专有名词。\n\n"
            "每条纠错记录中，[旧文本→新文本] 标注了ASR识别错误被纠正的部分，"
            "方括号外的文本未发生变化。\n\n"
            "## 提取规则（严格遵守）\n\n"
            "1. **必须有 variants**：只提取在方括号左侧（旧文本）中出现了误识别形式的词汇。"
            "variants 必须是方括号左侧实际出现的错误写法，不要自己编造同音字。"
            "没有明确误识别证据的词汇一律不提取。\n"
            "2. **只提取专有名词**：人名、产品名、工具名、项目名、领域专业术语等。"
            "不要提取通用英文词汇（如 Off, Low, Help, Plan, Random, Feature, "
            "Command, Label, Config, Delete, Copy, Cancel 等），"
            "这些词不需要词库辅助即可正确处理。\n"
            "3. **不要提取版本号或过于具体的短语**："
            "如 \"Python 3.13.5\"、\"v0.0.6\"、\"Default 50\"。"
            "只提取核心术语（如 Python），版本信息不属于词库范畴。\n"
            "4. **合并同一词汇的不同形式**：单复数（Snippet/Snippets）、"
            "带前缀（.gitignore/gitignore）视为同一个词条，只提取一次。\n"
            "5. **避免 variant 歧义**：如果同一个ASR误识别文本在不同记录中对应不同词汇，"
            "只为最可能的那个词汇添加此 variant。\n"
            "6. **只提取单个词**：不要提取多词短语（如 \"git push\"、\"Final Result\"）。"
            "每个词条应该是单个词或不可分割的专有名词。\n\n"
            "## 输出格式\n\n"
            "以管道符分隔的文本，第一行为表头，之后每行一个词条：\n"
            "term|category|variants|context\n"
            "- category 取值：tech, name, place, domain, other\n"
            "- variants 多个值用逗号分隔（不可留空，这是必填字段）\n"
            "- context 为简短语境说明（2-4个字）\n"
            "- 字段内容中不要包含管道符\n\n"
            "示例：\n"
            "term|category|variants|context\n"
            "Kubernetes|tech|库伯尼特斯,酷伯|容器编排\n"
            "Claude|tech|cloud,克劳德|AI模型\n\n"
            "只输出表头和数据行，不要输出其他内容。\n"
            "如果当前批次中没有值得提取的词汇，只输出表头行即可。\n"
        )

    @staticmethod
    def _term_in_texts(term_lower: str, texts_lower: str) -> bool:
        """Check if *term_lower* appears in *texts_lower* as a whole token.

        For ASCII terms, ensures the match is not a substring of a
        longer ASCII word (e.g. ``"git"`` must not match inside
        ``"github"``).  Checks that characters immediately before/after
        the match are not ASCII alphanumeric.
        """
        start = 0
        while True:
            idx = texts_lower.find(term_lower, start)
            if idx < 0:
                return False
            # Check character before the match
            if idx > 0 and texts_lower[idx - 1].isascii() and texts_lower[idx - 1].isalnum():
                start = idx + 1
                continue
            # Check character after the match
            end = idx + len(term_lower)
            if end < len(texts_lower) and texts_lower[end].isascii() and texts_lower[end].isalnum():
                start = idx + 1
                continue
            return True

    @staticmethod
    def _build_user_prompt(
        batch: List[Dict[str, Any]],
        existing_terms: Optional[List[str]] = None,
    ) -> str:
        """Build the user prompt with inline-diff correction records.

        Records with no replacements (only insertions/deletions or no
        changes) are skipped — they carry no ASR misrecognition info.
        If *existing_terms* is provided, a dedup hint is appended so
        the LLM avoids re-extracting known entries.
        """
        lines = []
        for r in batch:
            asr = r.get("asr_text", "").replace("\n", "\u23ce")
            final = r.get("final_text", "").replace("\n", "\u23ce")
            diff = inline_diff(asr, final)
            if "[" not in diff:
                continue
            lines.append(diff)

        prompt = "\n".join(lines)

        if existing_terms:
            terms_list = ", ".join(existing_terms)
            prompt += (
                f"\n\n以下词条已存在于词库中，无需重复提取：\n"
                f"{terms_list}"
            )

        return prompt

    @staticmethod
    def _extract_usage(usage_obj: Any) -> Dict[str, int]:
        """Extract token usage from an OpenAI-compatible usage object.

        Handles both prompt_tokens_details.cached_tokens (OpenAI) and
        providers that don't support cached token reporting.
        """
        if usage_obj is None:
            return {}
        input_tokens = usage_obj.prompt_tokens or 0
        output_tokens = usage_obj.completion_tokens or 0
        total_tokens = usage_obj.total_tokens or 0

        # Extract cached tokens from prompt_tokens_details if available
        cached_tokens = 0
        details = getattr(usage_obj, "prompt_tokens_details", None)
        if details is not None:
            cached_tokens = getattr(details, "cached_tokens", 0) or 0

        return {
            "input_tokens": input_tokens,
            "cached_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }

    async def _extract_batch(
        self,
        messages: List[Dict[str, str]],
        user_prompt: str,
        *,
        client: Any = None,
        on_stream_chunk: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> tuple[List[Dict[str, Any]], Dict[str, int], str]:
        """Call LLM to extract vocabulary entries from a batch of records.

        Each batch is sent independently (system prompt + user message)
        so context stays small and LLM attention is focused on the
        extraction rules.

        Args:
            messages: Messages list (typically just the system prompt).
            user_prompt: The user prompt for this batch.
            client: AsyncOpenAI client to use for API calls.
            on_stream_chunk: If provided, use streaming mode and call this
                with each text chunk as it arrives.
            cancel_event: If set during streaming, the extraction is
                interrupted and returns empty results.

        Returns:
            A tuple of (entries, usage, response_text).
        """
        provider_cfg = self._get_provider_config()
        if not provider_cfg or client is None:
            logger.warning("No AI provider configured for vocabulary extraction")
            return [], {}, ""

        model = provider_cfg["model"]
        extra_body = build_thinking_body(model, enabled=False)
        max_tokens = self._config.get("vocabulary", {}).get("max_output_tokens", 4096)
        usage: Dict[str, int] = {}

        # Build messages for this turn: session history + new user message
        turn_messages = messages + [{"role": "user", "content": user_prompt}]
        logger.debug(
            "LLM request: model=%s, messages=%d, max_tokens=%d, extra_body=%s",
            model, len(turn_messages), max_tokens, extra_body,
        )
        for idx, msg in enumerate(turn_messages):
            logger.debug(
                "  message[%d] role=%s len=%d",
                idx, msg["role"], len(msg["content"]),
            )

        if on_stream_chunk is not None:
            # Streaming path — request usage in final chunk
            stream_options = {"include_usage": True}
            cancelled = False
            async with asyncio.timeout(self._batch_timeout):
                async with await client.chat.completions.create(
                    model=model,
                    messages=turn_messages,
                    max_tokens=max_tokens,
                    stream=True,
                    stream_options=stream_options,
                    extra_body=extra_body,
                ) as stream:
                    parts: List[str] = []
                    repetition_aborted = False
                    chars_since_check = 0
                    async for chunk in stream:
                        if cancel_event is not None and cancel_event.is_set():
                            logger.info("Streaming cancelled mid-batch")
                            cancelled = True
                            break
                        if chunk.choices and chunk.choices[0].delta.content:
                            delta = chunk.choices[0].delta.content
                            parts.append(delta)
                            on_stream_chunk(delta)
                            chars_since_check += len(delta)
                            if chars_since_check >= 200:
                                chars_since_check = 0
                                if detect_repetition("".join(parts)):
                                    repetition_aborted = True
                                    break
                        if chunk.usage is not None:
                            usage = self._extract_usage(chunk.usage)
                if cancelled:
                    return [], usage, ""
                content = "".join(parts)
                if repetition_aborted:
                    content = truncate_repeated(content)
        else:
            # Non-streaming path
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=turn_messages,
                    max_tokens=max_tokens,
                    extra_body=extra_body,
                ),
                timeout=self._batch_timeout,
            )
            content = response.choices[0].message.content
            usage = self._extract_usage(response.usage)
            if content:
                content = truncate_repeated(content)

        if not content:
            logger.debug("LLM returned empty response")
            return [], usage, ""

        logger.debug("LLM response (%d chars):\n%s", len(content), content)
        if usage:
            logger.debug(
                "LLM usage: input=%d, cached=%d, output=%d, total=%d",
                usage.get("input_tokens", 0), usage.get("cached_tokens", 0),
                usage.get("output_tokens", 0), usage.get("total_tokens", 0),
            )

        entries = self._parse_llm_response(content)
        logger.debug("Parsed %d entries from LLM response", len(entries))
        return entries, usage, content

    def _resolve_provider_and_model(self) -> tuple[str, str]:
        """Resolve the effective provider name and model for vocab building.

        Checks vocabulary-specific build_provider/build_model first (treated
        as a pair — both must be set), then falls back to default_provider/
        default_model.
        """
        vocab_cfg = self._config.get("vocabulary", {})
        build_provider = vocab_cfg.get("build_provider", "")
        build_model = vocab_cfg.get("build_model", "")

        # Treat as a pair: only use vocab-specific if both are set
        if build_provider and build_model:
            providers = self._config.get("providers", {})
            if build_provider in providers:
                return build_provider, build_model

        return (
            self._config.get("default_provider", ""),
            self._config.get("default_model", ""),
        )

    def _get_active_provider_name(self) -> str:
        """Get the name of the active provider."""
        provider_name, _ = self._resolve_provider_and_model()
        providers = self._config.get("providers", {})
        if provider_name and provider_name in providers:
            return provider_name
        if providers:
            return next(iter(providers))
        return "N/A"

    def _get_provider_config(self) -> Optional[Dict[str, Any]]:
        """Get the active provider config for LLM calls."""
        provider_name, model = self._resolve_provider_and_model()
        providers = self._config.get("providers", {})
        pcfg = providers.get(provider_name, {})
        if not pcfg:
            # Try first available
            if providers:
                provider_name = next(iter(providers))
                pcfg = providers[provider_name]
            else:
                return None

        models = pcfg.get("models", [])
        if model not in models and models:
            model = models[0]

        return {
            "base_url": pcfg.get("base_url", ""),
            "api_key": pcfg.get("api_key", ""),
            "model": model,
        }

    def _parse_llm_response(self, content: str) -> List[Dict[str, Any]]:
        """Parse LLM response as pipe-separated text of vocabulary entries."""
        content = content.strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)

        lines = content.split("\n")
        if not lines:
            return []

        # Skip header line (term|category|variants|context)
        start = 0
        if lines[0].strip().startswith("term"):
            start = 1

        valid: List[Dict[str, Any]] = []
        english = self._english_words
        for line in lines[start:]:
            line = line.strip()
            if not line:
                continue

            parts = line.split("|", 3)
            if len(parts) < 4:
                logger.warning("Skipping line with fewer than 4 fields: %s", line)
                continue

            term = parts[0].strip()
            if not term:
                continue

            category = parts[1].strip() or "other"
            variants_raw = parts[2].strip()
            variants = [v.strip() for v in variants_raw.split(",") if v.strip()] if variants_raw else []
            context = parts[3].strip()

            # Remove self-referencing variants (term == variant, case-insensitive)
            term_lower = term.lower()
            variants = [v for v in variants if v.lower().strip() != term_lower]

            if not variants:
                logger.debug("Skipping entry without variants: %s", term)
                continue

            # Filter common words — LLMs already know these
            if english and term_lower in english:
                logger.debug("Skipping common word: %s", term)
                continue

            # Filter multi-word terms — ASR errors are word-level,
            # useful proper nouns should be standalone entries
            if " " in term:
                logger.debug("Skipping multi-word term: %s", term)
                continue

            valid.append({
                "term": term,
                "category": category,
                "variants": variants,
                "context": context,
            })

        return valid

    def _merge_entries(
        self,
        existing: List[Dict[str, Any]],
        new_entries: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Merge new entries into existing, deduplicating by term.

        Deduplication uses case-insensitive, stripped term as the key.
        When merging, the preferred term form is chosen (non-all-lowercase
        wins over all-lowercase), variants are deduplicated
        case-insensitively, and self-referencing variants are removed.
        """
        by_term: Dict[str, Dict[str, Any]] = {}

        for entry in existing:
            self._merge_into(by_term, entry)

        for entry in new_entries:
            self._merge_into(by_term, entry)

        # Remove self-referencing variants (variant matches term, case-insensitive)
        for entry in by_term.values():
            term_key = entry["term"].lower().strip()
            entry["variants"] = [
                v for v in entry["variants"]
                if v.lower().strip() != term_key
            ]

        return list(by_term.values())

    @staticmethod
    def _merge_into(
        by_term: Dict[str, Dict[str, Any]],
        entry: Dict[str, Any],
    ) -> None:
        """Merge a single entry into the by_term index.

        Uses term.lower().strip() as the dedup key. Frequencies are summed,
        variants are merged case-insensitively (first-seen form kept), and
        context is filled in when the existing one is empty.
        If the incoming term is not all-lowercase but the stored one is,
        the stored term form is upgraded.
        """
        term = entry.get("term", "")
        if not term:
            return
        key = term.lower().strip()
        if not key:
            return

        if key in by_term:
            existing_entry = by_term[key]

            # Upgrade term form: prefer non-all-lowercase over all-lowercase
            if existing_entry["term"].islower() and not term.islower():
                existing_entry["term"] = term

            # Merge variants case-insensitively, keeping first-seen form
            seen: Dict[str, str] = {}
            for v in existing_entry.get("variants", []):
                v_stripped = v.strip()
                if not v_stripped:
                    continue
                v_key = v_stripped.lower()
                if v_key not in seen:
                    seen[v_key] = v_stripped
            for v in entry.get("variants", []):
                v_stripped = v.strip()
                if not v_stripped:
                    continue
                v_key = v_stripped.lower()
                if v_key not in seen:
                    seen[v_key] = v_stripped
            existing_entry["variants"] = sorted(seen.values())

            # Sum frequencies
            existing_entry["frequency"] = (
                existing_entry.get("frequency", 1) + entry.get("frequency", 1)
            )

            # Update context if new one is non-empty and existing is empty
            if entry.get("context") and not existing_entry.get("context"):
                existing_entry["context"] = entry["context"]
        else:
            by_term[key] = {
                "term": term,
                "category": entry.get("category", "other"),
                "variants": [v.strip() for v in entry.get("variants", []) if v.strip()],
                "context": entry.get("context", ""),
                "frequency": entry.get("frequency", 1),
            }

    def _count_frequencies(
        self,
        entries: List[Dict[str, Any]],
        records: List[Dict[str, Any]],
    ) -> None:
        """Recount entry frequencies based on actual correction evidence.

        For each entry, counts correction records where:
        1. A known variant appears in ``asr_text`` (known misrecognition), OR
        2. The term appears in ``final_text`` but NOT in ``asr_text``
           (unknown misrecognition or ASR omission).

        Mutates each entry's ``"frequency"`` in place.
        """
        # Pre-compute lowercased texts once (avoids redundant .lower() per entry)
        record_texts = [
            (r.get("asr_text", "").lower(), r.get("final_text", "").lower())
            for r in records
        ]

        for entry in entries:
            term_lower = entry.get("term", "").lower()
            if not term_lower:
                continue
            variant_lowers = [
                v.lower() for v in entry.get("variants", []) if v.strip()
            ]

            count = 0
            for asr, final in record_texts:
                # Condition 1: known variant in ASR text
                if any(self._term_in_texts(v, asr) for v in variant_lowers):
                    count += 1
                    continue

                # Condition 2: term in final but not in ASR (unknown misrecognition)
                if (
                    self._term_in_texts(term_lower, final)
                    and not self._term_in_texts(term_lower, asr)
                ):
                    count += 1

            entry["frequency"] = max(count, 1)

    def _load_existing_vocabulary(self) -> Dict[str, Any]:
        """Load existing vocabulary.json if it exists."""
        if not os.path.exists(self._vocab_path):
            return {}

        try:
            with open(self._vocab_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load existing vocabulary: %s", e)
            return {}

    def _build_vocab_dict(
        self,
        last_processed_ts: str,
        provider_cfg: Optional[Dict[str, Any]],
        total_usage: Dict[str, int],
        entries: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build the vocabulary dict for saving to JSON."""
        return {
            "last_processed_timestamp": last_processed_ts,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "built_with": {
                "provider": self._get_active_provider_name(),
                "model": provider_cfg["model"] if provider_cfg else "N/A",
                "usage": total_usage,
            },
            "entries": entries,
        }

    def _save_vocabulary(self, vocabulary: Dict[str, Any]) -> None:
        """Save vocabulary to JSON file."""
        os.makedirs(self._data_dir, exist_ok=True)
        with open(self._vocab_path, "w", encoding="utf-8") as f:
            json.dump(vocabulary, f, indent=2, ensure_ascii=False)
            f.write("\n")
        logger.info("Vocabulary saved: %s", self._vocab_path)
