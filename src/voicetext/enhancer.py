"""AI text enhancement using OpenAI-compatible chat completions API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from .mode_loader import (
    MODE_OFF,
    ModeDefinition,
    ensure_default_modes,
    get_sorted_modes,
    load_modes,
)
from .vocabulary import VocabularyIndex

logger = logging.getLogger(__name__)


class TextEnhancer:
    """Enhance transcribed text using LLM via OpenAI-compatible API."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._enabled = config.get("enabled", False)
        self._timeout = config.get("timeout", 30)
        self._thinking = config.get("thinking", False)

        # Load enhancement modes from external files
        ensure_default_modes()
        self._modes: Dict[str, ModeDefinition] = load_modes()

        raw_mode = config.get("mode", "proofread")
        if raw_mode != MODE_OFF and raw_mode not in self._modes:
            # Fallback to first available mode
            first = next(iter(self._modes)) if self._modes else MODE_OFF
            logger.warning(
                "Unknown mode '%s', falling back to '%s'", raw_mode, first
            )
            raw_mode = first
        self._mode: str = raw_mode

        # Multi-provider support: name -> (AsyncOpenAI client, models list, extra_body)
        self._providers: Dict[str, Tuple[Any, List[str], Dict[str, Any]]] = {}
        self._active_provider: str = config.get("default_provider", "")
        self._active_model: str = config.get("default_model", "")

        self._providers_config = config.get("providers", {})
        self._init_providers()

        # Vocabulary retrieval
        vocab_cfg = config.get("vocabulary", {})
        self._vocab_enabled = vocab_cfg.get("enabled", False)
        self._vocab_top_k = vocab_cfg.get("top_k", 5)
        self._vocab_index: Optional[VocabularyIndex] = None
        if self._vocab_enabled:
            self._vocab_index = VocabularyIndex(vocab_cfg)

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
        """Initialize a single provider and cache its AsyncOpenAI client."""
        try:
            from openai import AsyncOpenAI

            base_url = pcfg.get("base_url", "http://localhost:11434/v1")
            api_key = pcfg.get("api_key", "ollama")
            models = pcfg.get("models", [])
            extra_body = pcfg.get("extra_body", {})

            client = AsyncOpenAI(base_url=base_url, api_key=api_key)
            self._providers[name] = (client, models, extra_body)
            logger.info(
                "AI provider initialized: %s (models=%s, base_url=%s)",
                name,
                models,
                base_url,
            )
        except ImportError as e:
            logger.warning("Failed to initialize AI provider %s: %s", name, e)

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        self._mode = value
        logger.info("AI enhance mode changed to: %s", value)

    @property
    def is_active(self) -> bool:
        return self._enabled and self._mode != MODE_OFF

    @property
    def available_modes(self) -> List[Tuple[str, str]]:
        """Return (mode_id, label) pairs sorted by order."""
        return get_sorted_modes(self._modes)

    def reload_modes(self) -> None:
        """Reload mode definitions from external files."""
        self._modes = load_modes()

    @property
    def thinking(self) -> bool:
        return self._thinking

    @thinking.setter
    def thinking(self, value: bool) -> None:
        self._thinking = value
        logger.info("AI thinking changed to: %s", value)

    @property
    def vocab_enabled(self) -> bool:
        return self._vocab_enabled

    @vocab_enabled.setter
    def vocab_enabled(self, value: bool) -> None:
        self._vocab_enabled = value
        if value and self._vocab_index is None:
            vocab_cfg = self._config_raw.get("vocabulary", {}) if hasattr(self, "_config_raw") else {}
            self._vocab_index = VocabularyIndex(vocab_cfg)
        logger.info("Vocabulary changed to: %s", value)

    @property
    def vocab_index(self) -> Optional[VocabularyIndex]:
        return self._vocab_index

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

    async def verify_provider(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 10,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Verify a provider by sending a test request.

        Returns None on success, or an error message string on failure.
        """
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(base_url=base_url, api_key=api_key)
            kwargs: Dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }
            if extra_body:
                kwargs["extra_body"] = extra_body
            await asyncio.wait_for(
                client.chat.completions.create(**kwargs),
                timeout=timeout,
            )
            return None
        except asyncio.TimeoutError:
            return f"Connection timed out after {timeout}s"
        except Exception as e:
            return str(e)

    def add_provider(
        self,
        name: str,
        base_url: str,
        api_key: str,
        models: List[str],
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Add a new provider and initialize it.

        Returns True on success, False if initialization fails.
        """
        if not name or not models:
            return False
        pcfg: Dict[str, Any] = {
            "base_url": base_url,
            "api_key": api_key,
            "models": models,
        }
        if extra_body:
            pcfg["extra_body"] = extra_body
        self._providers_config[name] = pcfg
        self._init_single_provider(name, pcfg)
        if name not in self._providers:
            # init failed, clean up config
            del self._providers_config[name]
            return False
        # If this is the first provider, auto-select it
        if len(self._providers) == 1:
            self._active_provider = name
            self._active_model = models[0]
        return True

    def remove_provider(self, name: str) -> bool:
        """Remove a provider. Returns True if removed, False otherwise."""
        if name not in self._providers:
            return False
        del self._providers[name]
        self._providers_config.pop(name, None)
        # If removed the active provider, switch to another
        if self._active_provider == name:
            if self._providers:
                self._active_provider = next(iter(self._providers))
                models = self._providers[self._active_provider][1]
                self._active_model = models[0] if models else ""
            else:
                self._active_provider = ""
                self._active_model = ""
        logger.info("Removed AI provider: %s", name)
        return True

    def _build_extra_body(self, provider_extra_body: Dict[str, Any]) -> Dict[str, Any]:
        """Build the final extra_body by merging thinking control with provider config.

        Provider-level extra_body takes precedence over thinking toggle.
        """
        result: Dict[str, Any] = {}
        if not self._thinking:
            result["chat_template_kwargs"] = {"enable_thinking": False}
        if provider_extra_body:
            result.update(provider_extra_body)
        return result

    async def enhance(self, text: str) -> str:
        """Enhance text using LLM. Returns original text on failure."""
        if not self.is_active or not text or not text.strip():
            return text

        if not self._providers or self._active_provider not in self._providers:
            logger.warning("AI enhancer not available, returning original text")
            return text

        mode_def = self._modes.get(self._mode)
        if not mode_def:
            return text

        try:
            # Retrieve vocabulary context if enabled
            system_content = mode_def.prompt
            if self._vocab_enabled and self._vocab_index is not None:
                try:
                    if not self._vocab_index.is_loaded:
                        self._vocab_index.load()
                    entries = self._vocab_index.retrieve(
                        text.strip(), top_k=self._vocab_top_k
                    )
                    if entries:
                        vocab_context = self._vocab_index.format_for_prompt(entries)
                        system_content = f"{mode_def.prompt}\n\n{vocab_context}"
                        logger.info(
                            "Vocabulary matched: %s",
                            ", ".join(e.term for e in entries),
                        )
                except Exception as e:
                    logger.warning("Vocabulary retrieval failed: %s", e)

            client, _, provider_extra_body = self._providers[self._active_provider]
            extra_body = self._build_extra_body(provider_extra_body)
            kwargs: Dict[str, Any] = {
                "model": self._active_model,
                "messages": [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": text.strip()},
                ],
            }
            if extra_body:
                kwargs["extra_body"] = extra_body
            response = await asyncio.wait_for(
                client.chat.completions.create(**kwargs),
                timeout=self._timeout,
            )
            enhanced = response.choices[0].message.content
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
