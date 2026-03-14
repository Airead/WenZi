"""AI text enhancement using OpenAI-compatible chat completions API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from .mode_loader import (
    MODE_OFF,
    ModeDefinition,
    ensure_default_modes,
    get_sorted_modes,
    load_modes,
)
from .conversation_history import ConversationHistory
from .vocabulary import VocabularyIndex

logger = logging.getLogger(__name__)

# Appended to system prompt when thinking mode is enabled to keep reasoning concise
THINKING_BREVITY_HINT = (
    "Keep your internal reasoning very brief and concise. "
    "Do not over-analyze or deliberate at length. "
    "Quickly arrive at the result with minimal thinking steps."
)


def _is_openai_reasoning_model(model_lower: str) -> bool:
    """Check if model is an OpenAI reasoning model (o1, o3, o4-mini, etc.)."""
    for prefix in ("o1", "o3", "o4-mini"):
        if model_lower.startswith(prefix):
            return True
    return False


def _is_deepseek_reasoning_model(model_lower: str) -> bool:
    """Check if model is a DeepSeek reasoning model."""
    return model_lower.startswith("deepseek-r1") or model_lower.startswith(
        "deepseek-reasoner"
    )


def build_thinking_body(model: str, enabled: bool) -> Dict[str, Any]:
    """Build extra_body parameters to control thinking for a given model.

    Returns model-specific parameters, or empty dict if the model does not
    support a thinking toggle.

    | Model type        | enabled=True                              | enabled=False                             |
    |-------------------|-------------------------------------------|-------------------------------------------|
    | GLM               | {"thinking": {"type": "enabled"}}         | {"thinking": {"type": "disabled"}}        |
    | Qwen              | chat_template_kwargs enable_thinking=True | chat_template_kwargs enable_thinking=False|
    | OpenAI reasoning  | {"reasoning_effort": "low"}               | {} (no param)                             |
    | DeepSeek reasoning| {"reasoning_effort": "low"}               | {} (no param)                             |
    | Other             | {} (no param)                             | {} (no param)                             |
    """
    if not model:
        return {}
    model_lower = model.lower()

    if "glm" in model_lower:
        state = "enabled" if enabled else "disabled"
        return {"thinking": {"type": state}}

    if "qwen" in model_lower:
        return {"chat_template_kwargs": {"enable_thinking": enabled}}

    if _is_openai_reasoning_model(model_lower) or _is_deepseek_reasoning_model(
        model_lower
    ):
        if enabled:
            return {"reasoning_effort": "low"}
        return {}

    # Unknown model: don't send thinking parameters
    return {}


class TextEnhancer:
    """Enhance transcribed text using LLM via OpenAI-compatible API."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._enabled = config.get("enabled", False)
        self._timeout = config.get("timeout", 30)
        self._connection_timeout = config.get("connection_timeout", 10)
        self._max_retries = config.get("max_retries", 2)
        self._thinking = config.get("thinking", False)

        # Debug flags
        self._debug_print_prompt = False
        self._debug_print_request_body = False

        # Last system prompt used in enhance()
        self._last_system_prompt: str = ""
        # Active stream reference for cancellation
        self._active_stream: Any = None
        self._cancel_event = asyncio.Event()

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

        # Conversation history
        history_cfg = config.get("conversation_history", {})
        self._history_enabled = history_cfg.get("enabled", False)
        self._history_max_entries = history_cfg.get("max_entries", 10)
        self._conversation_history = ConversationHistory()

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

    def get_mode_definition(self, mode_id: str) -> Optional["ModeDefinition"]:
        """Return the ModeDefinition for a given mode_id, or None."""
        return self._modes.get(mode_id)

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
    def history_enabled(self) -> bool:
        return self._history_enabled

    @history_enabled.setter
    def history_enabled(self, value: bool) -> None:
        self._history_enabled = value
        logger.info("Conversation history changed to: %s", value)

    @property
    def conversation_history(self) -> ConversationHistory:
        return self._conversation_history

    @property
    def last_system_prompt(self) -> str:
        """Return the system prompt used in the last enhance() call."""
        return self._last_system_prompt

    @property
    def debug_print_prompt(self) -> bool:
        return self._debug_print_prompt

    @debug_print_prompt.setter
    def debug_print_prompt(self, value: bool) -> None:
        self._debug_print_prompt = value
        logger.info("Debug print prompt: %s", value)

    @property
    def debug_print_request_body(self) -> bool:
        return self._debug_print_request_body

    @debug_print_request_body.setter
    def debug_print_request_body(self, value: bool) -> None:
        self._debug_print_request_body = value
        logger.info("Debug print request body: %s", value)

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

    @property
    def providers_with_models(self) -> Dict[str, List[str]]:
        """Return {provider_name: [model_names]} for all providers."""
        return {
            pname: list(data[1])
            for pname, data in self._providers.items()
        }

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
        result: Dict[str, Any] = build_thinking_body(
            self._active_model, self._thinking
        )
        if provider_extra_body:
            result.update(provider_extra_body)
        return result

    def _build_system_content(self, text: str, mode_def: "ModeDefinition") -> str:
        """Build system prompt with vocabulary and history context."""
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

        if self._history_enabled:
            try:
                entries = self._conversation_history.get_recent(
                    max_entries=self._history_max_entries
                )
                if entries:
                    history_context = self._conversation_history.format_for_prompt(
                        entries
                    )
                    system_content = f"{system_content}\n\n{history_context}"
                    logger.info(
                        "Conversation history injected: %d entries",
                        len(entries),
                    )
            except Exception as e:
                logger.warning("Conversation history retrieval failed: %s", e)

        if self._thinking:
            system_content = f"{system_content}\n\n{THINKING_BREVITY_HINT}"

        return system_content

    def _build_request_kwargs(
        self, text: str, system_content: str, **extra_kwargs: Any
    ) -> Dict[str, Any]:
        """Build the request kwargs dict for chat.completions.create."""
        self._last_system_prompt = system_content

        client, _, provider_extra_body = self._providers[self._active_provider]
        extra_body = self._build_extra_body(provider_extra_body)
        kwargs: Dict[str, Any] = {
            "model": self._active_model,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": text.strip()},
            ],
            **extra_kwargs,
        }
        if extra_body:
            kwargs["extra_body"] = extra_body

        if self._debug_print_prompt:
            logger.info("[DEBUG] System prompt:\n%s", system_content)
            logger.info("[DEBUG] User message:\n%s", text.strip())
        if self._debug_print_request_body:
            import json as _json
            logger.info(
                "[DEBUG] Request body:\n%s",
                _json.dumps(kwargs, ensure_ascii=False, default=str, indent=2),
            )

        return kwargs

    async def enhance(self, text: str) -> Tuple[str, Optional[Dict[str, int]]]:
        """Enhance text using LLM.

        Returns (enhanced_text, usage) where usage is a dict with
        prompt_tokens, completion_tokens, total_tokens or None.
        Falls back to original text on failure.
        """
        if not self.is_active or not text or not text.strip():
            return text, None

        if not self._providers or self._active_provider not in self._providers:
            logger.warning("AI enhancer not available, returning original text")
            return text, None

        mode_def = self._modes.get(self._mode)
        if not mode_def:
            return text, None

        try:
            system_content = self._build_system_content(text, mode_def)
            kwargs = self._build_request_kwargs(text, system_content)
            client, _, _ = self._providers[self._active_provider]

            response = await asyncio.wait_for(
                client.chat.completions.create(**kwargs),
                timeout=self._timeout,
            )
            enhanced = response.choices[0].message.content

            # Extract token usage
            usage = None
            if response.usage is not None:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens or 0,
                    "completion_tokens": response.usage.completion_tokens or 0,
                    "total_tokens": response.usage.total_tokens or 0,
                }

            if enhanced and enhanced.strip():
                logger.info(
                    "Text enhanced: '%s' -> '%s'",
                    text.strip()[:50],
                    enhanced.strip()[:50],
                )
                return enhanced.strip(), usage
            else:
                logger.warning("LLM returned empty text, using original")
                return text, usage
        except asyncio.TimeoutError:
            logger.error("AI enhancement timed out after %ds", self._timeout)
            return text, None
        except Exception as e:
            logger.error("AI enhancement failed: %s", e)
            return text, None

    def cancel_stream(self) -> None:
        """Signal the active stream to stop. Thread-safe."""
        self._cancel_event.set()

    async def enhance_stream(
        self, text: str
    ) -> AsyncIterator[Tuple[str, Optional[Dict[str, int]], bool]]:
        """Stream-enhance text using LLM.

        Yields (chunk, None, is_thinking) for each text delta, then a final
        ("", usage, False) with token usage when the stream completes.
        ``is_thinking`` is True for reasoning/thinking tokens.
        Falls back to non-streaming enhance() on error.
        """
        if not self.is_active or not text or not text.strip():
            yield text or "", None, False
            return

        if not self._providers or self._active_provider not in self._providers:
            logger.warning("AI enhancer not available, returning original text")
            yield text, None
            return

        mode_def = self._modes.get(self._mode)
        if not mode_def:
            yield text, None
            return

        try:
            system_content = self._build_system_content(text, mode_def)
            kwargs = self._build_request_kwargs(
                text, system_content,
                stream=True,
                stream_options={"include_usage": True},
            )
            client, _, _ = self._providers[self._active_provider]

            # Retry loop for initial connection
            last_error = None
            stream = None
            for attempt in range(1 + self._max_retries):
                if attempt > 0:
                    yield (
                        f"(Connection timed out, retrying {attempt}/{self._max_retries}...)\n",
                        None,
                        "retry",
                    )
                    logger.warning(
                        "Retrying stream connection (attempt %d/%d)",
                        attempt + 1, 1 + self._max_retries,
                    )

                try:
                    stream = await asyncio.wait_for(
                        client.chat.completions.create(**kwargs),
                        timeout=self._connection_timeout,
                    )
                    break  # Connection succeeded
                except asyncio.TimeoutError:
                    last_error = (
                        f"connection timed out after {self._connection_timeout}s"
                    )
                    logger.warning(
                        "Stream connection attempt %d failed: %s",
                        attempt + 1, last_error,
                    )
                    if attempt >= self._max_retries:
                        yield (
                            f"(Error: {last_error}, all {1 + self._max_retries} attempts failed)\n",
                            None,
                            "retry",
                        )
                        return

            # Expose stream so callers can close it on cancellation
            self._cancel_event.clear()
            self._active_stream = stream

            collected = []
            usage = None
            # Timeout applies between chunks: resets on each received chunk
            aiter = stream.__aiter__()
            try:
                while True:
                    if self._cancel_event.is_set():
                        logger.info("Stream cancelled via cancel_event")
                        break
                    try:
                        chunk = await asyncio.wait_for(
                            aiter.__anext__(), timeout=self._timeout
                        )
                    except StopAsyncIteration:
                        break
                    if chunk.usage is not None:
                        usage = {
                            "prompt_tokens": chunk.usage.prompt_tokens or 0,
                            "completion_tokens": chunk.usage.completion_tokens or 0,
                            "total_tokens": chunk.usage.total_tokens or 0,
                        }
                    if chunk.choices:
                        delta = chunk.choices[0].delta
                        if delta:
                            # Thinking/reasoning tokens (Qwen, GLM, DeepSeek, OpenAI)
                            reasoning = (
                                getattr(delta, "reasoning_content", None)
                                or getattr(delta, "reasoning", None)
                                or getattr(delta, "reasoning_text", None)
                            )
                            if reasoning:
                                yield reasoning, None, True
                            if delta.content:
                                collected.append(delta.content)
                                yield delta.content, None, False
            finally:
                self._active_stream = None
                if hasattr(stream, 'close'):
                    try:
                        await stream.close()
                    except Exception:
                        pass

            full_text = "".join(collected).strip()
            if not full_text:
                logger.warning("LLM stream returned empty text, using original")
                yield text, usage, False
            else:
                logger.info(
                    "Text stream-enhanced: '%s' -> '%s'",
                    text.strip()[:50],
                    full_text[:50],
                )
                # Final yield with usage only
                yield "", usage, False

        except asyncio.TimeoutError:
            logger.error("AI stream enhancement timed out after %ds", self._timeout)
            yield text, None, False
        except Exception as e:
            logger.error("AI stream enhancement failed: %s", e)
            yield f"(error: {e})", None, False


def create_enhancer(config: Dict[str, Any]) -> Optional[TextEnhancer]:
    """Factory function to create a TextEnhancer from app config.

    Returns None if ai_enhance is not configured.
    """
    ai_config = config.get("ai_enhance")
    if ai_config is None:
        return None
    return TextEnhancer(ai_config)
