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

from voicetext.config import DEFAULT_CONFIG_DIR
from .conversation_history import ConversationHistory
from .enhancer import build_thinking_body

logger = logging.getLogger(__name__)


@dataclass
class BuildCallbacks:
    """Callbacks for vocabulary build progress reporting."""

    on_batch_start: Optional[Callable[[int, int], None]] = None  # (batch_idx, total)
    on_stream_chunk: Optional[Callable[[str], None]] = None  # (chunk_text)
    on_batch_done: Optional[Callable[[int, int, int], None]] = None  # (batch_idx, total, entries_count)
    on_usage_update: Optional[Callable[[int, int, int], None]] = None  # (prompt, completion, total)


class VocabularyBuilder:
    """Extract vocabulary entries from user corrections using LLM."""

    def __init__(
        self,
        config: Dict[str, Any],
        log_dir: str = DEFAULT_CONFIG_DIR,
        conversation_history: Optional[ConversationHistory] = None,
    ) -> None:
        self._config = config
        self._log_dir = os.path.expanduser(log_dir)
        self._conversation_history = conversation_history or ConversationHistory(config_dir=self._log_dir)
        self._vocab_path = os.path.join(self._log_dir, "vocabulary.json")
        self._batch_size = 20
        self._batch_timeout = config.get("vocabulary", {}).get("build_timeout", 600)

    async def build(
        self,
        full_rebuild: bool = False,
        cancel_event: Optional[threading.Event] = None,
        callbacks: Optional[BuildCallbacks] = None,
    ) -> Dict[str, Any]:
        """Build or update the vocabulary from correction logs.

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
        total_usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        cancelled = False

        for i, batch in enumerate(batches, 1):
            if cancel_event is not None and cancel_event.is_set():
                logger.info("Build cancelled before batch %d/%d", i, len(batches))
                cancelled = True
                break

            try:
                if callbacks and callbacks.on_batch_start:
                    callbacks.on_batch_start(i, len(batches))

                logger.info("Extracting batch %d/%d (%d records)...", i, len(batches), len(batch))
                on_chunk = callbacks.on_stream_chunk if callbacks else None
                extracted, usage = await self._extract_batch(batch, on_stream_chunk=on_chunk)
                for key in total_usage:
                    total_usage[key] += usage.get(key, 0)
                if callbacks and callbacks.on_usage_update:
                    callbacks.on_usage_update(
                        total_usage["prompt_tokens"],
                        total_usage["completion_tokens"],
                        total_usage["total_tokens"],
                    )
                logger.info("Batch %d/%d: extracted %d entries", i, len(batches), len(extracted))
                all_new_entries.extend(extracted)

                if callbacks and callbacks.on_batch_done:
                    callbacks.on_batch_done(i, len(batches), len(extracted))
            except Exception as e:
                logger.warning("Failed to extract batch %d/%d: %s", i, len(batches), e)

        existing_entries = existing.get("entries", [])
        merged = self._merge_entries(existing_entries, all_new_entries)

        # Determine the latest timestamp from processed records
        last_ts = records[-1].get("timestamp", datetime.now(timezone.utc).isoformat())

        provider_cfg = self._get_provider_config()
        vocabulary = {
            "last_processed_timestamp": last_ts,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "built_with": {
                "provider": self._get_active_provider_name(),
                "model": provider_cfg["model"] if provider_cfg else "N/A",
                "usage": total_usage,
            },
            "entries": merged,
        }
        self._save_vocabulary(vocabulary)

        summary: Dict[str, Any] = {
            "new_records": len(records),
            "new_entries": len(all_new_entries),
            "total_entries": len(merged),
            "usage": total_usage,
        }
        if cancelled:
            summary["cancelled"] = True

        logger.info(
            "Vocabulary built: %d new records, %d new entries, %d total entries, "
            "%d tokens used%s",
            summary["new_records"],
            summary["new_entries"],
            summary["total_entries"],
            total_usage["total_tokens"],
            " (cancelled)" if cancelled else "",
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

    def _build_extraction_prompt(self, batch: List[Dict[str, Any]]) -> str:
        """Build the LLM prompt for vocabulary extraction."""
        records_text = ""
        for r in batch:
            asr = r.get("asr_text", "")
            final = r.get("final_text", "")
            records_text += f"asr_text: {asr}\nfinal_text: {final}\n\n"

        return (
            "你是一个词汇提取助手。请从以下语音识别纠错记录中提取有价值的词汇。\n\n"
            "每条记录包含：asr_text（ASR原始结果，可能有错）和 final_text（用户确认的正确文本）。\n\n"
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
            "只输出表头和数据行，不要输出其他内容。\n\n"
            "纠错记录：\n"
            f"{records_text}"
        )

    async def _extract_batch(
        self,
        batch: List[Dict[str, Any]],
        on_stream_chunk: Optional[Callable[[str], None]] = None,
    ) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
        """Call LLM to extract vocabulary entries from a batch of records.

        Args:
            batch: Records to process.
            on_stream_chunk: If provided, use streaming mode and call this
                with each text chunk as it arrives.

        Returns:
            A tuple of (entries, usage) where usage has prompt_tokens,
            completion_tokens, total_tokens.
        """
        from openai import AsyncOpenAI

        provider_cfg = self._get_provider_config()
        if not provider_cfg:
            logger.warning("No AI provider configured for vocabulary extraction")
            return [], {}

        client = AsyncOpenAI(
            base_url=provider_cfg["base_url"],
            api_key=provider_cfg["api_key"],
        )
        model = provider_cfg["model"]
        prompt = self._build_extraction_prompt(batch)
        extra_body = build_thinking_body(model, enabled=False)
        usage: Dict[str, int] = {}

        if on_stream_chunk is not None:
            # Streaming path — request usage in final chunk
            stream_options = {"include_usage": True}
            async with asyncio.timeout(self._batch_timeout):
                async with await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    stream=True,
                    stream_options=stream_options,
                    extra_body=extra_body,
                ) as stream:
                    parts: List[str] = []
                    async for chunk in stream:
                        if chunk.choices and chunk.choices[0].delta.content:
                            delta = chunk.choices[0].delta.content
                            parts.append(delta)
                            on_stream_chunk(delta)
                        if chunk.usage is not None:
                            usage = {
                                "prompt_tokens": chunk.usage.prompt_tokens or 0,
                                "completion_tokens": chunk.usage.completion_tokens or 0,
                                "total_tokens": chunk.usage.total_tokens or 0,
                            }
                content = "".join(parts)
        else:
            # Non-streaming path (backward compatible)
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    extra_body=extra_body,
                ),
                timeout=self._batch_timeout,
            )
            content = response.choices[0].message.content
            if response.usage is not None:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens or 0,
                    "completion_tokens": response.usage.completion_tokens or 0,
                    "total_tokens": response.usage.total_tokens or 0,
                }

        if not content:
            return [], usage

        return self._parse_llm_response(content), usage

    def _get_active_provider_name(self) -> str:
        """Get the name of the active provider."""
        provider_name = self._config.get("default_provider", "")
        providers = self._config.get("providers", {})
        if provider_name and provider_name in providers:
            return provider_name
        if providers:
            return next(iter(providers))
        return "N/A"

    def _get_provider_config(self) -> Optional[Dict[str, Any]]:
        """Get the active provider config for LLM calls."""
        provider_name = self._config.get("default_provider", "")
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
        model = self._config.get("default_model", "")
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
        """Merge new entries into existing, deduplicating by term."""
        # Index existing by term
        by_term: Dict[str, Dict[str, Any]] = {}
        for entry in existing:
            term = entry.get("term", "")
            if term:
                by_term[term] = entry

        for entry in new_entries:
            term = entry.get("term", "")
            if not term:
                continue

            if term in by_term:
                # Merge: union variants, accumulate frequency
                existing_entry = by_term[term]
                existing_variants = set(existing_entry.get("variants", []))
                new_variants = set(entry.get("variants", []))
                existing_entry["variants"] = sorted(
                    existing_variants | new_variants
                )
                existing_entry["frequency"] = existing_entry.get("frequency", 1) + 1
                # Update context if new one is non-empty and existing is empty
                if entry.get("context") and not existing_entry.get("context"):
                    existing_entry["context"] = entry["context"]
            else:
                by_term[term] = {
                    "term": term,
                    "category": entry.get("category", "other"),
                    "variants": entry.get("variants", []),
                    "context": entry.get("context", ""),
                    "frequency": 1,
                }

        return list(by_term.values())

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
        os.makedirs(self._log_dir, exist_ok=True)
        with open(self._vocab_path, "w", encoding="utf-8") as f:
            json.dump(vocabulary, f, indent=2, ensure_ascii=False)
            f.write("\n")
        logger.info("Vocabulary saved: %s", self._vocab_path)
