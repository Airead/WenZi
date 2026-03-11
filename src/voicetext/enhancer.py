"""AI text enhancement using OpenAI Agents SDK."""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class EnhanceMode(Enum):
    """Available enhancement modes."""

    OFF = "off"
    PROOFREAD = "proofread"
    FORMAT = "format"
    COMPLETE = "complete"
    ENHANCE = "enhance"


_MODE_PROMPTS: Dict[EnhanceMode, str] = {
    EnhanceMode.PROOFREAD: (
        "你是一个文本纠错润色助手。请修正用户输入中的错别字、语法错误和标点符号问题。"
        "保持原文的语义和风格不变，只做必要的修正。"
        "直接输出修正后的文本，不要添加任何解释或说明。"
    ),
    EnhanceMode.FORMAT: (
        "你是一个文本格式化助手。请将用户输入的口语化文本转换为书面语，"
        "并适当调整结构使其更加清晰易读。"
        "保持原文的核心语义不变。"
        "直接输出格式化后的文本，不要添加任何解释或说明。"
    ),
    EnhanceMode.COMPLETE: (
        "你是一个智能文本补全助手。请补全用户输入中不完整的句子，"
        "使其成为完整、通顺的表达。"
        "保持原文的语义和风格不变，只补全缺失的部分。"
        "直接输出补全后的文本，不要添加任何解释或说明。"
    ),
    EnhanceMode.ENHANCE: (
        "你是一个全面的文本增强助手。请对用户输入进行以下处理：\n"
        "1. 修正错别字和语法错误\n"
        "2. 修正标点符号\n"
        "3. 将口语化表达转换为书面语\n"
        "4. 补全不完整的句子\n"
        "5. 适当调整结构使其更加清晰\n"
        "保持原文的核心语义不变。"
        "直接输出增强后的文本，不要添加任何解释或说明。"
    ),
}


class TextEnhancer:
    """Enhance transcribed text using LLM via OpenAI Agents SDK."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._enabled = config.get("enabled", False)
        self._mode = EnhanceMode(config.get("mode", "proofread"))
        self._timeout = config.get("timeout", 30)

        # Multi-provider support: name -> (provider_instance, models_list)
        self._providers: Dict[str, Tuple[Any, List[str]]] = {}
        self._active_provider: str = config.get("default_provider", "")
        self._active_model: str = config.get("default_model", "")

        self._providers_config = config.get("providers", {})
        self._init_providers()

        # Validate active provider/model
        if self._active_provider not in self._providers and self._providers:
            self._active_provider = next(iter(self._providers))
        if self._providers:
            models = self._providers[self._active_provider][1]
            if self._active_model not in models and models:
                self._active_model = models[0]

    def _init_providers(self) -> None:
        """Initialize all configured providers."""
        for name, pcfg in self._providers_config.items():
            self._init_single_provider(name, pcfg)

    def _init_single_provider(self, name: str, pcfg: Dict[str, Any]) -> None:
        """Initialize a single provider and cache it."""
        try:
            from agents import ModelProvider
            from agents.models.openai_chat_completions import (
                OpenAIChatCompletionsModel,
            )
            from openai import AsyncOpenAI

            base_url = pcfg.get("base_url", "http://localhost:11434/v1")
            api_key = pcfg.get("api_key", "ollama")
            models = pcfg.get("models", [])

            client = AsyncOpenAI(base_url=base_url, api_key=api_key)

            class _CustomModelProvider(ModelProvider):
                def get_model(self, model_name_override: str | None = None):
                    return OpenAIChatCompletionsModel(
                        model=model_name_override or (models[0] if models else ""),
                        openai_client=client,
                    )

            self._providers[name] = (_CustomModelProvider(), models)
            logger.info(
                "AI provider initialized: %s (models=%s, base_url=%s)",
                name,
                models,
                base_url,
            )
        except ImportError as e:
            logger.warning("Failed to initialize AI provider %s: %s", name, e)

    @property
    def mode(self) -> EnhanceMode:
        return self._mode

    @mode.setter
    def mode(self, value: EnhanceMode) -> None:
        self._mode = value
        logger.info("AI enhance mode changed to: %s", value.value)

    @property
    def is_active(self) -> bool:
        return self._enabled and self._mode != EnhanceMode.OFF

    @property
    def provider_name(self) -> str:
        return self._active_provider

    @provider_name.setter
    def provider_name(self, value: str) -> None:
        if value not in self._providers:
            logger.warning("Unknown provider: %s", value)
            return
        self._active_provider = value
        # Auto-select first model if current model not in new provider
        models = self._providers[value][1]
        if self._active_model not in models and models:
            self._active_model = models[0]
        logger.info("AI provider changed to: %s, model: %s", value, self._active_model)

    @property
    def model_name(self) -> str:
        return self._active_model

    @model_name.setter
    def model_name(self, value: str) -> None:
        self._active_model = value
        logger.info("AI model changed to: %s", value)

    @property
    def provider_names(self) -> List[str]:
        return list(self._providers.keys())

    @property
    def model_names(self) -> List[str]:
        if self._active_provider in self._providers:
            return list(self._providers[self._active_provider][1])
        return []

    async def enhance(self, text: str) -> str:
        """Enhance text using LLM. Returns original text on failure."""
        if not self.is_active or not text or not text.strip():
            return text

        if not self._providers or self._active_provider not in self._providers:
            logger.warning("AI enhancer not available, returning original text")
            return text

        prompt = _MODE_PROMPTS.get(self._mode)
        if not prompt:
            return text

        try:
            from agents import Agent, RunConfig, Runner

            provider_instance = self._providers[self._active_provider][0]

            agent = Agent(
                name="text_enhancer",
                instructions=prompt,
                model=self._active_model,
            )
            run_config = RunConfig(model_provider=provider_instance)
            result = await asyncio.wait_for(
                Runner.run(agent, input=text.strip(), run_config=run_config),
                timeout=self._timeout,
            )
            enhanced = result.final_output
            if enhanced and enhanced.strip():
                logger.info(
                    "Text enhanced: '%s' -> '%s'",
                    text.strip()[:50],
                    enhanced.strip()[:50],
                )
                return enhanced.strip()
            else:
                logger.warning("LLM returned empty text, using original")
                return text
        except asyncio.TimeoutError:
            logger.error("AI enhancement timed out after %ds", self._timeout)
            return text
        except Exception as e:
            logger.error("AI enhancement failed: %s", e)
            return text


def create_enhancer(config: Dict[str, Any]) -> Optional[TextEnhancer]:
    """Factory function to create a TextEnhancer from app config.

    Returns None if ai_enhance is not configured.
    """
    ai_config = config.get("ai_enhance")
    if ai_config is None:
        return None
    return TextEnhancer(ai_config)
