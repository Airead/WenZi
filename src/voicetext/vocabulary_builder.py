"""Build structured vocabulary from correction logs using LLM extraction."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)


class VocabularyBuilder:
    """Extract vocabulary entries from corrections.jsonl using LLM."""

    def __init__(
        self,
        config: Dict[str, Any],
        log_dir: str = DEFAULT_CONFIG_DIR,
    ) -> None:
        self._config = config
        self._log_dir = os.path.expanduser(log_dir)
        self._corrections_path = os.path.join(self._log_dir, "corrections.jsonl")
        self._vocab_path = os.path.join(self._log_dir, "vocabulary.json")
        self._batch_size = 20
        self._batch_timeout = config.get("vocabulary", {}).get("build_timeout", 600)

    async def build(self, full_rebuild: bool = False) -> Dict[str, Any]:
        """Build or update the vocabulary from correction logs.

        Returns a summary dict with counts.
        """
        existing = self._load_existing_vocabulary()
        since = None
        if not full_rebuild and existing:
            since = existing.get("last_processed_timestamp")

        records = self._read_corrections(since=since)
        if not records:
            logger.info("No new correction records to process")
            return {"new_records": 0, "new_entries": 0, "total_entries": len(existing.get("entries", []))}

        batches = self._batch_records(records, self._batch_size)
        all_new_entries: List[Dict[str, Any]] = []

        for batch in batches:
            try:
                extracted = await self._extract_batch(batch)
                all_new_entries.extend(extracted)
            except Exception as e:
                logger.warning("Failed to extract batch: %s", e)

        existing_entries = existing.get("entries", [])
        merged = self._merge_entries(existing_entries, all_new_entries)

        # Determine the latest timestamp from processed records
        last_ts = records[-1].get("timestamp", datetime.now(timezone.utc).isoformat())

        vocabulary = {
            "last_processed_timestamp": last_ts,
            "entries": merged,
        }
        self._save_vocabulary(vocabulary)

        summary = {
            "new_records": len(records),
            "new_entries": len(all_new_entries),
            "total_entries": len(merged),
        }
        logger.info(
            "Vocabulary built: %d new records, %d new entries, %d total entries",
            summary["new_records"],
            summary["new_entries"],
            summary["total_entries"],
        )
        return summary

    def _read_corrections(
        self, since: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Read correction records from JSONL file, optionally filtered by timestamp."""
        if not os.path.exists(self._corrections_path):
            return []

        records: List[Dict[str, Any]] = []
        try:
            with open(self._corrections_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if since and record.get("timestamp", "") <= since:
                            continue
                        records.append(record)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning("Failed to read corrections: %s", e)

        return records

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
            "对每个词条输出：\n"
            '{"term": "正确写法", "category": "tech|name|place|domain|other", '
            '"variants": ["ASR错误形式"], "context": "简短语境"}\n\n'
            "以JSON数组格式返回，只输出JSON。\n\n"
            "纠错记录：\n"
            f"{records_text}"
        )

    async def _extract_batch(
        self, batch: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Call LLM to extract vocabulary entries from a batch of records."""
        from openai import AsyncOpenAI

        provider_cfg = self._get_provider_config()
        if not provider_cfg:
            logger.warning("No AI provider configured for vocabulary extraction")
            return []

        client = AsyncOpenAI(
            base_url=provider_cfg["base_url"],
            api_key=provider_cfg["api_key"],
        )
        model = provider_cfg["model"]

        prompt = self._build_extraction_prompt(batch)
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=self._batch_timeout,
        )

        content = response.choices[0].message.content
        if not content:
            return []

        return self._parse_llm_response(content)

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
        """Parse LLM response as JSON array of vocabulary entries."""
        content = content.strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            # Remove first and last fence lines
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)

        try:
            entries = json.loads(content)
            if not isinstance(entries, list):
                return []
            # Validate each entry has at least 'term'
            valid = []
            for entry in entries:
                if isinstance(entry, dict) and "term" in entry:
                    valid.append(entry)
            return valid
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse LLM response as JSON: %s", e)
            return []

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
