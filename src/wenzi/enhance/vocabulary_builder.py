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

from wenzi.config import DEFAULT_DATA_DIR
from .conversation_history import ConversationHistory
from .enhancer import build_thinking_body

logger = logging.getLogger(__name__)


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
        logger.info(
            "Starting vocabulary build (full_rebuild=%s)",
            full_rebuild,
        )
        existing = self._load_existing_vocabulary()
        since = None
        if not full_rebuild and existing:
            since = existing.get("last_processed_timestamp")
            logger.info("Incremental build since: %s", since)

        records = self._read_corrections(since=since)
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
        cancelled = False
        aborted = False

        provider_cfg = self._get_provider_config()
        existing_terms = [e.get("term", "") for e in merged_entries if e.get("term")]

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

        # Multi-turn session: system prompt is shared, conversation accumulates
        system_prompt = self._build_system_prompt(existing_terms)
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]

        try:
            for i, batch in enumerate(batches, 1):
                if cancel_event is not None and cancel_event.is_set():
                    logger.info("Build cancelled before batch %d/%d", i, len(batches))
                    cancelled = True
                    break

                # Prepare once before retry loop
                user_prompt = self._build_user_prompt(batch)
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

                # Append this turn to the session for KV cache continuity
                # (skip empty responses to avoid polluting session history)
                if response_text:
                    messages.append({"role": "user", "content": user_prompt})
                    messages.append({"role": "assistant", "content": response_text})

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
                all_new_entries.extend(extracted)
                records_processed += len(batch)

                if callbacks and callbacks.on_batch_done:
                    callbacks.on_batch_done(i, len(batches), len(extracted))

                # Persist progress after each successful batch
                merged_entries = self._merge_entries(merged_entries, extracted)
                batch_last_ts = batch[-1].get("timestamp", datetime.now(timezone.utc).isoformat())
                vocabulary = {
                    "last_processed_timestamp": batch_last_ts,
                    "built_at": datetime.now(timezone.utc).isoformat(),
                    "built_with": {
                        "provider": self._get_active_provider_name(),
                        "model": provider_cfg["model"] if provider_cfg else "N/A",
                        "usage": total_usage,
                    },
                    "entries": merged_entries,
                }
                self._save_vocabulary(vocabulary)
        finally:
            if client is not None:
                await client.close()

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

    def _build_system_prompt(self, existing_terms: List[str]) -> str:
        """Build the system prompt (shared across all batches for KV cache)."""
        prompt = (
            "你是一个词汇提取助手。请从语音识别纠错记录中提取有价值的词汇。\n\n"
            "每条纠错记录格式为 ASR原文 → 正确文本，一行一条。\n\n"
            "请提取专有名词、技术术语、常用短语，以及ASR容易识别错误的词汇。\n"
            "以管道符分隔的文本格式输出，第一行为表头，之后每行一个词条。\n"
            "字段：term|category|variants|context\n"
            "- category 取值：tech, name, place, domain, other\n"
            "- variants 多个值用逗号分隔，无则留空\n"
            "- context 为简短语境说明\n"
            "- 字段内容中不要包含管道符\n\n"
            "示例：\n"
            "term|category|variants|context\n"
            "Python|tech|派森|编程语言\n"
            "Kubernetes|tech|库伯尼特斯,酷伯|容器编排\n\n"
            "只输出表头和数据行，不要输出其他内容。\n"
        )

        if existing_terms:
            terms_list = ", ".join(existing_terms)
            prompt += (
                "\n以下词条已存在于词库中，无需重复提取：\n"
                f"{terms_list}\n"
            )

        return prompt

    @staticmethod
    def _build_user_prompt(batch: List[Dict[str, Any]]) -> str:
        """Build the user prompt with correction records for a single batch."""
        lines = []
        for r in batch:
            asr = r.get("asr_text", "")
            final = r.get("final_text", "")
            lines.append(f"{asr} → {final}")
        return "\n".join(lines)

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

        Uses the accumulated messages list (multi-turn session) to leverage
        KV cache on the shared prefix (system prompt + prior turns).

        Args:
            messages: Accumulated session messages (system + prior turns).
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
        usage: Dict[str, int] = {}

        # Build messages for this turn: session history + new user message
        turn_messages = messages + [{"role": "user", "content": user_prompt}]

        if on_stream_chunk is not None:
            # Streaming path — request usage in final chunk
            stream_options = {"include_usage": True}
            cancelled = False
            async with asyncio.timeout(self._batch_timeout):
                async with await client.chat.completions.create(
                    model=model,
                    messages=turn_messages,
                    stream=True,
                    stream_options=stream_options,
                    extra_body=extra_body,
                ) as stream:
                    parts: List[str] = []
                    async for chunk in stream:
                        if cancel_event is not None and cancel_event.is_set():
                            logger.info("Streaming cancelled mid-batch")
                            cancelled = True
                            break
                        if chunk.choices and chunk.choices[0].delta.content:
                            delta = chunk.choices[0].delta.content
                            parts.append(delta)
                            on_stream_chunk(delta)
                        if chunk.usage is not None:
                            usage = self._extract_usage(chunk.usage)
                if cancelled:
                    return [], usage, ""
                content = "".join(parts)
        else:
            # Non-streaming path
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=turn_messages,
                    extra_body=extra_body,
                ),
                timeout=self._batch_timeout,
            )
            content = response.choices[0].message.content
            usage = self._extract_usage(response.usage)

        if not content:
            return [], usage, ""

        return self._parse_llm_response(content), usage, content

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

    def _save_vocabulary(self, vocabulary: Dict[str, Any]) -> None:
        """Save vocabulary to JSON file."""
        os.makedirs(self._data_dir, exist_ok=True)
        with open(self._vocab_path, "w", encoding="utf-8") as f:
            json.dump(vocabulary, f, indent=2, ensure_ascii=False)
            f.write("\n")
        logger.info("Vocabulary saved: %s", self._vocab_path)
