"""AI text enhancement using OpenAI-compatible chat completions API."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional, Tuple

from wenzi import async_loop

if TYPE_CHECKING:
    from wenzi.enhance.manual_vocabulary import ManualVocabularyStore
    from wenzi.input_context import InputContext

from .mode_loader import (
    MODE_OFF,
    ModeDefinition,
    ensure_default_modes,
    get_sorted_modes,
    load_modes,
)
from .conversation_history import ConversationHistory
from .pool_monitor import PoolMonitor
from .repetition import detect_repetition, truncate_repeated

logger = logging.getLogger(__name__)

@dataclass
class _ModeHistoryCache:
    """Per-mode incremental history cache for prompt caching optimization."""

    entry_lines: List[str] = field(default_factory=list)
    last_ts: str = ""
    total_chars: int = 0
    last_log_count: int = 0
    last_context_level: str = "off"


# Appended to system prompt when thinking mode is enabled to keep reasoning concise
THINKING_BREVITY_HINT = (
    "Keep your internal reasoning very brief and concise. "
    "Do not over-analyze or deliberate at length. "
    "Quickly arrive at the result with minimal thinking steps."
)


def _extract_cache_read_tokens(usage: Any) -> int:
    """Extract cached input tokens from a usage object.

    Tries prompt_tokens_details.cached_tokens first (OpenAI standard),
    then falls back to prompt_cache_hit_tokens (DeepSeek).
    Returns 0 if the provider does not report cache info.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", None)
        if cached:
            return int(cached)
    # DeepSeek fallback
    hit = getattr(usage, "prompt_cache_hit_tokens", None)
    if hit:
        return int(hit)
    return 0


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


def _is_deepseek_thinking_model(model_lower: str) -> bool:
    """Check if model is a DeepSeek model that supports enable_thinking."""
    lower = model_lower.lower()
    return "deepseek" in lower and not _is_deepseek_reasoning_model(lower)


def build_thinking_body(model: str, enabled: bool) -> Dict[str, Any]:
    """Build extra_body parameters to control thinking for a given model.

    Returns model-specific parameters, or empty dict if the model does not
    support a thinking toggle.

    | Model type        | enabled=True                              | enabled=False                             |
    |-------------------|-------------------------------------------|-------------------------------------------|
    | GLM               | {"thinking": {"type": "enabled"}}         | {"thinking": {"type": "disabled"}}        |
    | Qwen              | chat_template_kwargs enable_thinking=True | chat_template_kwargs enable_thinking=False|
    | DeepSeek (V3 etc) | {"enable_thinking": true}                 | {"enable_thinking": false}                |
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

    if _is_deepseek_thinking_model(model_lower):
        return {"enable_thinking": enabled}

    if _is_openai_reasoning_model(model_lower) or _is_deepseek_reasoning_model(
        model_lower
    ):
        if enabled:
            return {"reasoning_effort": "low"}
        return {}

    # Unknown model: don't send thinking parameters
    return {}


_THINK_TAG_RE = re.compile(r"</?think>", re.IGNORECASE)


def strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks from text (non-streaming)."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


class ThinkTagParser:
    """Incremental parser that splits streaming chunks at <think>/<​/think> boundaries.

    Yields ``(text, is_thinking)`` pairs.  Handles tags split across chunks
    and buffering of partial tag candidates (e.g. receiving ``<`` then ``think>``).
    """

    def __init__(self) -> None:
        self._inside_think = False
        self._buf = ""
        self._just_exited_think = False

    def feed(self, text: str) -> list[tuple[str, bool]]:
        """Feed a chunk and return a list of (text, is_thinking) segments."""
        self._buf += text
        results: list[tuple[str, bool]] = []

        while self._buf:
            m = _THINK_TAG_RE.search(self._buf)
            if m is None:
                # No complete tag found — check if buffer ends with a partial
                # tag candidate (starts with '<' that could become <think> or </think>).
                partial_pos = self._buf.rfind("<")
                if partial_pos != -1 and partial_pos > len(self._buf) - len("</think>"):
                    # Flush everything before the partial, keep the rest buffered
                    if partial_pos > 0:
                        results.append((self._buf[:partial_pos], self._inside_think))
                    self._buf = self._buf[partial_pos:]
                else:
                    # No partial tag — flush everything
                    results.append((self._buf, self._inside_think))
                    self._buf = ""
                break

            # Flush text before the tag
            if m.start() > 0:
                results.append((self._buf[:m.start()], self._inside_think))

            # Toggle state based on tag
            tag = m.group().lower()
            if tag == "<think>":
                self._inside_think = True
                self._just_exited_think = False
            else:  # </think>
                self._inside_think = False
                self._just_exited_think = True

            self._buf = self._buf[m.end():]

        # Strip leading whitespace from the first content segment after </think>
        # to avoid blank lines at the top of the enhance text area.
        stripped: list[tuple[str, bool]] = []
        for t, th in results:
            if not t:
                continue
            if self._just_exited_think and not th:
                t = t.lstrip()
                self._just_exited_think = False
                if not t:
                    continue
            elif not th:
                self._just_exited_think = False
            stripped.append((t, th))
        return stripped


class TextEnhancer:
    """Enhance transcribed text using LLM via OpenAI-compatible API."""

    def __init__(
        self,
        config: Dict[str, Any],
        config_dir: str | None = None,
        data_dir: str | None = None,
        cache_dir: str | None = None,
        conversation_history: Optional[ConversationHistory] = None,
        manual_vocab_store: Optional[ManualVocabularyStore] = None,
    ) -> None:
        self._enabled = config.get("enabled", False)
        self._timeout = config.get("timeout", 30)
        self._connection_timeout = config.get("connection_timeout", 30)
        self._max_retries = config.get("max_retries", 2)
        self._max_output_tokens = config.get("max_output_tokens", 4096)
        self._thinking = config.get("thinking", False)
        self._input_context_level: str = config.get("input_context", "basic")
        self._config_dir = config_dir
        self._data_dir = data_dir
        self._cache_dir = cache_dir

        # Debug flags
        self._debug_print_prompt = False
        self._debug_print_request_body = False

        # Last system prompt used in enhance()
        self._last_system_prompt: str = ""

        # Load enhancement modes from external files
        modes_dir = os.path.join(config_dir, "enhance_modes") if config_dir else None
        self._modes_dir = modes_dir
        ensure_default_modes(modes_dir)
        self._modes: Dict[str, ModeDefinition] = load_modes(modes_dir)

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

        self._manual_vocab_store = manual_vocab_store

        # Conversation history
        history_cfg = config.get("conversation_history", {})
        self._history_enabled = history_cfg.get("enabled", False)
        self._history_max_entries = history_cfg.get("max_entries", 10)
        if conversation_history is not None:
            self._conversation_history = conversation_history
        else:
            self._conversation_history = ConversationHistory(
                **({"data_dir": data_dir} if data_dir else {})
            )

        # Per-mode incremental history cache for prompt caching optimization.
        # Each enhancement mode maintains its own history cache so that
        # switching modes does not invalidate another mode's cache prefix.
        self._history_caches: Dict[str, _ModeHistoryCache] = {}
        self._history_refresh_threshold: int = history_cfg.get(
            "refresh_threshold", 50
        )
        self._max_history_chars: int = history_cfg.get(
            "max_history_chars", 6000
        )
        # Guard: threshold must exceed base size to avoid rebuild-every-request
        if self._history_refresh_threshold <= self._history_max_entries:
            logger.warning(
                "refresh_threshold (%d) must be greater than max_entries (%d), "
                "using %d",
                self._history_refresh_threshold,
                self._history_max_entries,
                self._history_max_entries * 5,
            )
            self._history_refresh_threshold = self._history_max_entries * 5

        # Validate active provider/model
        if self._active_provider not in self._providers and self._providers:
            self._active_provider = next(iter(self._providers))
        if self._providers:
            models = self._providers[self._active_provider][1]
            if self._active_model not in models and models:
                self._active_model = models[0]

        # Connection pool monitor
        self._pool_monitor = PoolMonitor(self._providers, self._providers_config)
        if self._providers:
            self._pool_monitor.start_periodic(interval=60.0)

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

            client = AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=0)
            self._providers[name] = (client, models, extra_body)
            logger.info(
                "AI provider initialized: %s (models=%s, base_url=%s)",
                name,
                models,
                base_url,
            )
        except ImportError as e:
            logger.warning("Failed to initialize AI provider %s: %s", name, e)

    async def close(self) -> None:
        """Close all cached provider clients to release connection pools.

        This is a teardown method — after calling close(), the enhancer
        can no longer make API calls. Only call during application shutdown.
        """
        self._pool_monitor.stop_periodic()
        self._pool_monitor.log_stats("shutdown")
        for name, (client, _, _) in list(self._providers.items()):
            try:
                await client.close()
            except Exception:
                pass
        self._providers.clear()

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
        self._modes = load_modes(self._modes_dir)

    @property
    def thinking(self) -> bool:
        return self._thinking

    @thinking.setter
    def thinking(self, value: bool) -> None:
        self._thinking = value
        logger.info("AI thinking changed to: %s", value)

    @property
    def history_enabled(self) -> bool:
        return self._history_enabled

    @history_enabled.setter
    def history_enabled(self, value: bool) -> None:
        self._history_enabled = value
        logger.info("Conversation history changed to: %s", value)

    @property
    def history_max_entries(self) -> int:
        return self._history_max_entries

    @history_max_entries.setter
    def history_max_entries(self, value: int) -> None:
        self._history_max_entries = max(1, value)
        logger.info("History max_entries changed to: %d", self._history_max_entries)

    @property
    def history_refresh_threshold(self) -> int:
        return self._history_refresh_threshold

    @history_refresh_threshold.setter
    def history_refresh_threshold(self, value: int) -> None:
        self._history_refresh_threshold = max(
            self._history_max_entries + 1, value
        )
        logger.info(
            "History refresh_threshold changed to: %d",
            self._history_refresh_threshold,
        )

    @property
    def input_context_level(self) -> str:
        return self._input_context_level

    @input_context_level.setter
    def input_context_level(self, value: str) -> None:
        self._input_context_level = value
        logger.info("Input context level changed to: %s", value)

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
        from openai import AsyncOpenAI, RateLimitError

        client = AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=0)
        try:
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
        except RateLimitError:
            return "Rate limited (429) — provider is temporarily throttling requests"
        except Exception as e:
            return str(e)
        finally:
            await client.close()

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
        client, _, _ = self._providers.pop(name)
        try:
            async_loop.submit(client.close())
        except Exception:
            # Loop may not be running during shutdown; connection cleanup
            # is best-effort — the provider is already removed from use.
            pass
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

    def _build_system_content(
        self, text: str, mode_def: "ModeDefinition",
        input_context: "InputContext | None" = None,
    ) -> str:
        """Build system prompt with vocabulary and history context.

        Components are ordered by stability (most stable first) so that
        LLM API-level prompt caching can match the longest possible prefix:

        1. mode prompt  — static per mode
        2. thinking hint — static within a session
        3. combined context section — merged history & vocab instructions
           as a unified header (static), followed by history entries
           (append-only) and vocab entries (dynamic per request)
        """
        system_content = mode_def.prompt

        # 1. Static: thinking brevity hint
        if self._thinking:
            system_content = f"{system_content}\n\n{THINKING_BREVITY_HINT}"

        # 2. Combined context section (history + vocab + input context)
        context_section = self._build_context_section(text, input_context)
        if context_section:
            system_content = f"{system_content}\n\n{context_section}"

        return system_content

    def _build_context_section(
        self, text: str, input_context: "InputContext | None" = None,
    ) -> str:
        """Build the combined context section with history, vocabulary, and input context.

        Merges history and vocabulary instruction headers into one static
        block at the top, maximizing the cacheable prompt prefix.  History
        entries follow (append-only), then vocabulary entries (dynamic),
        then input context (dynamic per request).

        Structure::

            ---
            <combined instructions>

            对话记录：
            - entry1
            - entry2

            词库：
            - "variant1" → "term1"

            当前输入环境：
            - iTerm2 — "窗口标题" — AXTextArea
            ---
        """
        history_context = ""
        if self._history_enabled:
            try:
                history_context = self._build_history_context()
            except Exception as e:
                logger.warning("Conversation history retrieval failed: %s", e)

        app_bundle_id = getattr(input_context, "bundle_id", None)

        # Manual vocabulary (user-curated correction pairs)
        vocab_lines = ""
        if self._manual_vocab_store is not None:
            try:
                manual_entries = self._manual_vocab_store.get_llm_vocab(
                    app_bundle_id=app_bundle_id,
                )
                if manual_entries:
                    vocab_lines = "\n".join(
                        f'- "{e.variant}" → "{e.term}"' for e in manual_entries
                    )
                    logger.info(
                        "Manual vocab injected: %s",
                        ", ".join(e.term for e in manual_entries),
                    )
            except Exception as e:
                logger.warning("Manual vocab retrieval failed: %s", e)

        env_line = None
        if input_context is not None:
            env_line = input_context.format_for_prompt(self._input_context_level)

        if (
            not history_context
            and not vocab_lines
            and not env_line
        ):
            return ""

        # Build the combined section — header uses enabled flags for stability
        parts = [self._context_section_header()]

        if history_context:
            parts.append(f"对话记录：\n{history_context}")

        if vocab_lines:
            parts.append(f"词库：\n{vocab_lines}")

        if env_line:
            parts.append(f"当前输入环境：\n- {env_line}")

        parts.append("---")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Incremental history context builder
    # ------------------------------------------------------------------

    def _get_mode_cache(self) -> _ModeHistoryCache:
        """Return the history cache for the current enhancement mode."""
        mode = self._mode
        if mode not in self._history_caches:
            self._history_caches[mode] = _ModeHistoryCache()
        return self._history_caches[mode]

    def _build_history_context(self) -> str:
        """Build history section incrementally for better API cache hit rate.

        Each enhancement mode maintains its own history cache so that
        switching modes does not invalidate another mode's cached prefix.

        Rebuild triggers (resets to ``_history_max_entries`` base entries):
        - Entry count reaches ``_history_refresh_threshold``
        - Total character count reaches ``_max_history_chars``
        - Anchor timestamp not found (rotation, deletion, etc.)
        """
        ch = self._conversation_history
        mc = self._get_mode_cache()

        # Invalidate cache if context level changed — replace with a fresh
        # instance to force a full rebuild (resets last_log_count etc.).
        if mc.last_context_level != self._input_context_level:
            mc = _ModeHistoryCache(last_context_level=self._input_context_level)
            self._history_caches[self._mode] = mc

        # Fast path: no new log() calls since last build — return cached.
        # last_log_count is per-mode so that one mode's update does not
        # cause another mode to skip its own new entries.
        lc = ch.log_count
        if lc == mc.last_log_count and mc.entry_lines:
            return self._format_history_section()

        # Fetch up to threshold entries for the current mode.
        # NOTE: last_log_count is updated only AFTER successful processing,
        # so that an exception here does not cause future calls to skip
        # new entries via the fast path.
        entries = ch.get_recent(
            max_entries=self._history_refresh_threshold,
            enhance_mode=self._mode,
        )
        if not entries:
            mc.entry_lines = []
            mc.total_chars = 0
            mc.last_ts = ""
            mc.last_log_count = lc
            return ""

        latest_ts = entries[-1].get("timestamp", "")

        # No qualifying entries were added for this mode
        if latest_ts == mc.last_ts and mc.entry_lines:
            mc.last_log_count = lc
            return self._format_history_section()

        # First build
        if not mc.entry_lines:
            result = self._full_rebuild_history(entries)
            mc.last_log_count = lc
            return result

        # Find new entries by walking backwards from the end
        new_entries: list = []
        for e in reversed(entries):
            if e.get("timestamp") == mc.last_ts:
                break
            new_entries.append(e)
        else:
            # Anchor not found — structural change (rotation, deletion)
            logger.info("History anchor not found, performing full rebuild")
            result = self._full_rebuild_history(entries)
            mc.last_log_count = lc
            return result

        if not new_entries:
            mc.last_log_count = lc
            return self._format_history_section()

        new_entries.reverse()

        # Pre-check: would appending exceed thresholds?
        new_lines = [ch.format_entry_line(e, context_level=self._input_context_level) for e in new_entries]
        # Each new line adds: 1 separator (\n) + line length
        new_chars = sum(len(line) + 1 for line in new_lines)
        projected_count = len(mc.entry_lines) + len(new_lines)
        projected_chars = mc.total_chars + new_chars

        if (
            projected_count >= self._history_refresh_threshold
            or projected_chars >= self._max_history_chars
        ):
            logger.info(
                "History threshold reached (entries=%d, chars=%d), rebuilding",
                projected_count,
                projected_chars,
            )
            result = self._full_rebuild_history(entries)
            mc.last_log_count = lc
            return result

        # Safe to append
        mc.entry_lines.extend(new_lines)
        mc.total_chars = projected_chars
        mc.last_ts = latest_ts
        mc.last_log_count = lc
        logger.info(
            "History cache [%s] appended %d entries (total %d, chars %d)",
            self._mode,
            len(new_lines),
            len(mc.entry_lines),
            mc.total_chars,
        )

        return self._format_history_section()

    def _full_rebuild_history(
        self, entries: List[Dict[str, Any]]
    ) -> str:
        """Rebuild history cache from scratch with the most recent base entries."""
        ch = self._conversation_history
        mc = self._get_mode_cache()
        base = entries[-self._history_max_entries:]
        mc.entry_lines = [ch.format_entry_line(e, context_level=self._input_context_level) for e in base]
        # Total chars of "\n".join(lines): sum of line lengths + (N-1) separators
        n = len(mc.entry_lines)
        mc.total_chars = (
            sum(len(line) for line in mc.entry_lines) + max(n - 1, 0)
        )
        mc.last_ts = entries[-1].get("timestamp", "") if entries else ""
        logger.info(
            "History cache [%s] rebuilt with %d entries (chars %d)",
            self._mode,
            len(mc.entry_lines),
            mc.total_chars,
        )
        return self._format_history_section()

    def _format_history_section(self) -> str:
        """Format the cached entry lines (without header/footer).

        Returns just the joined entry lines, e.g.::

            - 我试着说一句话，看有没有[热慈→热词]被导入
            - 这是一条无纠错的记录

        The combined context header and footer are managed by
        :meth:`_build_context_section`.
        """
        mc = self._get_mode_cache()
        if not mc.entry_lines:
            return ""
        return "\n".join(mc.entry_lines)

    def _context_section_header(self) -> str:
        """Build the combined instruction header for the context section.

        Uses ``_history_enabled`` and ``_manual_vocab_store`` flags (not
        per-request content) so the header stays **stable within a session**
        and contributes to the cacheable prompt prefix.  The subsection labels
        (``对话记录：`` / ``词库：``) are only emitted when actual content
        exists, so the LLM never sees a label with no data beneath it.
        """
        lines = ["---", "以下是辅助纠错的参考上下文："]

        if self._history_enabled:
            hist_hint = (
                "- 对话记录（优先参考）：反映用户真实的纠错偏好和话题上下文，"
                "差异部分以[误→正]标注，无标注表示该部分无需纠错。"
            )
            if self._input_context_level != "off":
                hist_hint += (
                    "每条记录前的应用名称表示该条是在哪个应用中输入的，"
                    "这是系统自动采集的元数据，不是用户输入的内容。"
                )
            lines.append(hist_hint)

        if self._manual_vocab_store is not None:
            lines.append(
                "- 词库（仅供辅助）：以下专有名词 ASR 常误写为同音近音词，"
                "仅当输入中确实存在对应误写时才替换，不要强行套用。"
                "当词库与对话记录冲突时，以对话记录为准。"
            )

        if self._input_context_level != "off":
            lines.append(
                "- 当前输入环境：标注用户正在使用的应用和窗口信息，"
                "这是系统自动采集的元数据，不是用户输入的内容。"
            )

        return "\n".join(lines)

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
            "max_tokens": self._max_output_tokens,
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

    async def enhance(
        self, text: str, input_context: "InputContext | None" = None,
    ) -> Tuple[str, Optional[Dict[str, int]]]:
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

        from openai import RateLimitError

        try:
            system_content = self._build_system_content(text, mode_def, input_context=input_context)
            kwargs = self._build_request_kwargs(text, system_content)
            client, _, _ = self._providers[self._active_provider]

            self._pool_monitor.log_stats("enhance:before_request", self._active_provider)
            response = await asyncio.wait_for(
                client.chat.completions.create(**kwargs),
                timeout=self._timeout,
            )
            self._pool_monitor.log_stats("enhance:after_response", self._active_provider)
            enhanced = response.choices[0].message.content
            if enhanced:
                enhanced = strip_think_tags(enhanced)

            # Extract token usage
            usage = None
            if response.usage is not None:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens or 0,
                    "completion_tokens": response.usage.completion_tokens or 0,
                    "total_tokens": response.usage.total_tokens or 0,
                    "cache_read_tokens": _extract_cache_read_tokens(response.usage),
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
            self._pool_monitor.log_stats("enhance:timeout", self._active_provider)
            logger.error("AI enhancement timed out after %ds", self._timeout)
            return text, None
        except Exception as e:
            if isinstance(e, RateLimitError):
                self._pool_monitor.log_stats("enhance:rate_limited", self._active_provider)
                logger.error("AI enhancement rate limited (429): %s", e)
            else:
                self._pool_monitor.log_stats("enhance:error", self._active_provider)
                logger.error("AI enhancement failed: %s", e)
            return text, None

    async def enhance_stream(
        self, text: str, input_context: "InputContext | None" = None,
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
            yield text, None, False
            return

        mode_def = self._modes.get(self._mode)
        if not mode_def:
            yield text, None, False
            return

        from openai import RateLimitError

        try:
            system_content = self._build_system_content(text, mode_def, input_context=input_context)
            kwargs = self._build_request_kwargs(
                text, system_content,
                stream=True,
                stream_options={"include_usage": True},
            )
            client, _, _ = self._providers[self._active_provider]

            # Retry loop for initial connection
            last_error = None
            stream = None
            self._pool_monitor.log_stats("stream:before_create", self._active_provider)
            for attempt in range(1 + self._max_retries):
                if attempt > 0:
                    delay = min(2 ** attempt, 8)  # 2s, 4s, 8s...
                    yield (
                        f"(Connection timed out, retrying in {delay}s — {attempt}/{self._max_retries}...)\n",
                        None,
                        "retry",
                    )
                    logger.warning(
                        "Retrying stream connection in %ds (attempt %d/%d)",
                        delay, attempt + 1, 1 + self._max_retries,
                    )
                    await asyncio.sleep(delay)

                try:
                    stream = await asyncio.wait_for(
                        client.chat.completions.create(**kwargs),
                        timeout=self._connection_timeout,
                    )
                    self._pool_monitor.log_stats("stream:after_create", self._active_provider)
                    break  # Connection succeeded
                except RateLimitError as e:
                    self._pool_monitor.log_stats("stream:rate_limited", self._active_provider)
                    logger.error("Stream rate limited (429), not retrying: %s", e)
                    yield (
                        f"(Rate limited — {e})\n",
                        None,
                        "retry",
                    )
                    return
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

            collected = []
            usage = None
            think_parser = ThinkTagParser()
            chars_since_check = 0
            repetition_aborted = False
            # Timeout applies between chunks: resets on each received chunk
            aiter = stream.__aiter__()
            try:
                while True:
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
                            "cache_read_tokens": _extract_cache_read_tokens(chunk.usage),
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
                                # Parse <think> tags inline (MiniMax and similar models)
                                for segment, is_thinking in think_parser.feed(delta.content):
                                    if is_thinking:
                                        yield segment, None, True
                                    else:
                                        collected.append(segment)
                                        yield segment, None, False
                                chars_since_check += len(delta.content)
                                if chars_since_check >= 200:
                                    chars_since_check = 0
                                    if detect_repetition("".join(collected)):
                                        repetition_aborted = True
                                        break
            finally:
                if hasattr(stream, 'close'):
                    try:
                        await stream.close()
                        logger.debug("LLM stream closed")
                    except Exception:
                        pass
                self._pool_monitor.log_stats("stream:after_close", self._active_provider)

            full_text = "".join(collected).strip()
            if repetition_aborted:
                full_text = truncate_repeated(full_text).strip()
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
            self._pool_monitor.log_stats("stream:timeout", self._active_provider)
            logger.error("AI stream enhancement timed out after %ds", self._timeout)
            yield text, None, False
        except Exception as e:
            if isinstance(e, RateLimitError):
                self._pool_monitor.log_stats("stream:rate_limited", self._active_provider)
                logger.error("AI stream rate limited (429): %s", e)
            else:
                self._pool_monitor.log_stats("stream:error", self._active_provider)
                logger.error("AI stream enhancement failed: %s", e)
            yield f"(error: {e})", None, False


def create_enhancer(
    config: Dict[str, Any],
    config_dir: str | None = None,
    data_dir: str | None = None,
    cache_dir: str | None = None,
    conversation_history: Optional[ConversationHistory] = None,
    manual_vocab_store: Optional[ManualVocabularyStore] = None,
) -> Optional[TextEnhancer]:
    """Factory function to create a TextEnhancer from app config.

    Returns None if ai_enhance is not configured.
    """
    ai_config = config.get("ai_enhance")
    if ai_config is None:
        return None
    return TextEnhancer(
        ai_config,
        config_dir=config_dir,
        data_dir=data_dir,
        cache_dir=cache_dir,
        conversation_history=conversation_history,
        manual_vocab_store=manual_vocab_store,
    )
