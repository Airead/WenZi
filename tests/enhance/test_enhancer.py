"""Tests for the AI text enhancer module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voicetext.enhance.conversation_history import ConversationHistory
from voicetext.enhance.enhancer import (
    TextEnhancer,
    build_thinking_body,
    _is_deepseek_reasoning_model,
    _is_openai_reasoning_model,
    create_enhancer,
)
from voicetext.enhance.mode_loader import ModeDefinition
from voicetext.enhance.vocabulary import VocabularyEntry, VocabularyIndex


# --- TextEnhancer tests ---

# Default modes used in tests
_TEST_MODES = {
    "proofread": ModeDefinition("proofread", "纠错润色", "proofread prompt", 10),
    "format": ModeDefinition("format", "格式化", "format prompt", 20),
    "complete": ModeDefinition("complete", "智能补全", "complete prompt", 30),
    "enhance": ModeDefinition("enhance", "全面增强", "enhance prompt", 40),
    "translate_en": ModeDefinition("translate_en", "翻译为英文", "translate prompt", 50),
}


@pytest.fixture(autouse=True)
def _patch_mode_loading():
    """Auto-patch mode loading for all enhancer tests."""
    with patch("voicetext.enhance.enhancer.ensure_default_modes"), \
         patch("voicetext.enhance.enhancer.load_modes", return_value=dict(_TEST_MODES)):
        yield


def _make_config(**overrides):
    """Helper to create a valid enhancer config."""
    cfg = {
        "enabled": True,
        "mode": "proofread",
        "default_provider": "ollama",
        "default_model": "qwen2.5:7b",
        "providers": {
            "ollama": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "ollama",
                "models": ["qwen2.5:7b"],
            },
        },
        "thinking": False,
        "timeout": 30,
    }
    cfg.update(overrides)
    return cfg


def _make_multi_provider_config(**overrides):
    """Helper to create a config with multiple providers."""
    cfg = {
        "enabled": True,
        "mode": "proofread",
        "default_provider": "ollama",
        "default_model": "qwen2.5:7b",
        "providers": {
            "ollama": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "ollama",
                "models": ["qwen2.5:7b", "llama3:8b"],
            },
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-test",
                "models": ["gpt-4o", "gpt-4o-mini"],
            },
        },
        "timeout": 30,
    }
    cfg.update(overrides)
    return cfg


class TestTextEnhancerIsActive:
    def test_active_when_enabled_and_mode_not_off(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
        assert enhancer.is_active is True

    def test_inactive_when_disabled(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=False, mode="proofread"))
        assert enhancer.is_active is False

    def test_inactive_when_mode_off(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="off"))
        assert enhancer.is_active is False

    def test_inactive_when_disabled_and_mode_off(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=False, mode="off"))
        assert enhancer.is_active is False


class TestTextEnhancerMode:
    def test_mode_getter(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(mode="format"))
        assert enhancer.mode == "format"

    def test_mode_setter(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(mode="proofread"))
        enhancer.mode = "enhance"
        assert enhancer.mode == "enhance"

    def test_unknown_mode_falls_back(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(mode="nonexistent"))
        # Should fall back to first available mode
        assert enhancer.mode in _TEST_MODES

    def test_available_modes(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
        modes = enhancer.available_modes
        assert len(modes) == 5
        # Should be sorted by order
        assert modes[0] == ("proofread", "纠错润色")
        assert modes[-1] == ("translate_en", "翻译为英文")


class TestTextEnhancerProviderModel:
    """Tests for multi-provider and model switching."""

    def test_provider_names(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_multi_provider_config())
            # Simulate providers being initialized
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b", "llama3:8b"]),
                "openai": (MagicMock(), ["gpt-4o", "gpt-4o-mini"]),
            }
        assert set(enhancer.provider_names) == {"ollama", "openai"}

    def test_model_names_for_active_provider(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_multi_provider_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b", "llama3:8b"]),
                "openai": (MagicMock(), ["gpt-4o", "gpt-4o-mini"]),
            }
            enhancer._active_provider = "ollama"
        assert enhancer.model_names == ["qwen2.5:7b", "llama3:8b"]

    def test_model_names_after_provider_switch(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_multi_provider_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b", "llama3:8b"]),
                "openai": (MagicMock(), ["gpt-4o", "gpt-4o-mini"]),
            }
            enhancer._active_provider = "ollama"
        enhancer.provider_name = "openai"
        assert enhancer.model_names == ["gpt-4o", "gpt-4o-mini"]

    def test_provider_switch_auto_selects_first_model(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_multi_provider_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b", "llama3:8b"]),
                "openai": (MagicMock(), ["gpt-4o", "gpt-4o-mini"]),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"
        enhancer.provider_name = "openai"
        assert enhancer.model_name == "gpt-4o"

    def test_provider_switch_keeps_model_if_available(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_multi_provider_config())
            enhancer._providers = {
                "provider_a": (MagicMock(), ["shared-model", "model-a"]),
                "provider_b": (MagicMock(), ["shared-model", "model-b"]),
            }
            enhancer._active_provider = "provider_a"
            enhancer._active_model = "shared-model"
        enhancer.provider_name = "provider_b"
        assert enhancer.model_name == "shared-model"

    def test_model_setter(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b", "llama3:8b"]),
            }
            enhancer._active_provider = "ollama"
        enhancer.model_name = "llama3:8b"
        assert enhancer.model_name == "llama3:8b"

    def test_unknown_provider_ignored(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
            }
            enhancer._active_provider = "ollama"
        enhancer.provider_name = "nonexistent"
        assert enhancer.provider_name == "ollama"

    def test_model_names_empty_when_no_providers(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {}
            enhancer._active_provider = "missing"
        assert enhancer.model_names == []

    def test_providers_with_models(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_multi_provider_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b", "llama3:8b"]),
                "openai": (MagicMock(), ["gpt-4o", "gpt-4o-mini"]),
            }
        result = enhancer.providers_with_models
        assert result == {
            "ollama": ["qwen2.5:7b", "llama3:8b"],
            "openai": ["gpt-4o", "gpt-4o-mini"],
        }

    def test_providers_with_models_empty(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {}
        assert enhancer.providers_with_models == {}

    def test_default_provider_fallback(self):
        """If default_provider is not in providers, fallback to first available."""
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            cfg = _make_config(default_provider="nonexistent")
            enhancer = TextEnhancer(cfg)
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
            }
            # Re-run validation logic
            if enhancer._active_provider not in enhancer._providers:
                enhancer._active_provider = next(iter(enhancer._providers))
        assert enhancer.provider_name == "ollama"


class TestTextEnhancerAddRemoveProvider:
    """Tests for adding and removing providers dynamically."""

    def test_add_provider_success(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        with patch(
            "voicetext.enhance.enhancer.TextEnhancer._init_single_provider"
        ) as mock_init:
            def fake_init(name, pcfg):
                enhancer._providers[name] = (MagicMock(), pcfg["models"])

            mock_init.side_effect = fake_init
            result = enhancer.add_provider(
                "openai", "https://api.openai.com/v1", "sk-test", ["gpt-4o"]
            )

        assert result is True
        assert "openai" in enhancer.provider_names

    def test_add_provider_empty_name_rejected(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {}
        result = enhancer.add_provider("", "http://localhost", "key", ["model"])
        assert result is False

    def test_add_provider_empty_models_rejected(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {}
        result = enhancer.add_provider("test", "http://localhost", "key", [])
        assert result is False

    def test_add_first_provider_auto_selects(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {}
            enhancer._active_provider = ""
            enhancer._active_model = ""

        with patch(
            "voicetext.enhance.enhancer.TextEnhancer._init_single_provider"
        ) as mock_init:
            def fake_init(name, pcfg):
                enhancer._providers[name] = (MagicMock(), pcfg["models"])

            mock_init.side_effect = fake_init
            enhancer.add_provider(
                "new_provider", "http://localhost", "key", ["model-a"]
            )

        assert enhancer.provider_name == "new_provider"
        assert enhancer.model_name == "model-a"

    def test_add_provider_init_failure(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {}

        with patch(
            "voicetext.enhance.enhancer.TextEnhancer._init_single_provider"
        ):
            # _init_single_provider does nothing, so provider won't be added
            result = enhancer.add_provider(
                "bad", "http://localhost", "key", ["model"]
            )

        assert result is False
        assert "bad" not in enhancer.provider_names

    def test_remove_provider_success(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_multi_provider_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
                "openai": (MagicMock(), ["gpt-4o"]),
            }
            enhancer._active_provider = "openai"
            enhancer._active_model = "gpt-4o"

        result = enhancer.remove_provider("openai")
        assert result is True
        assert "openai" not in enhancer.provider_names
        # Should auto-switch to remaining provider
        assert enhancer.provider_name == "ollama"
        assert enhancer.model_name == "qwen2.5:7b"

    def test_remove_nonexistent_provider(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
            }
        result = enhancer.remove_provider("nonexistent")
        assert result is False

    def test_remove_inactive_provider(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_multi_provider_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
                "openai": (MagicMock(), ["gpt-4o"]),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        result = enhancer.remove_provider("openai")
        assert result is True
        # Active provider should remain unchanged
        assert enhancer.provider_name == "ollama"
        assert enhancer.model_name == "qwen2.5:7b"

    def test_remove_last_provider(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
            }
            enhancer._active_provider = "ollama"

        result = enhancer.remove_provider("ollama")
        assert result is True
        assert enhancer.provider_names == []
        assert enhancer.provider_name == ""
        assert enhancer.model_name == ""


class TestTextEnhancerVerifyProvider:
    """Tests for verify_provider."""

    def _make_mock_openai(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=MagicMock())
        return MagicMock(return_value=mock_client)

    def test_verify_success(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())

        with patch("openai.AsyncOpenAI", self._make_mock_openai()):
            result = asyncio.run(
                enhancer.verify_provider(
                    "http://localhost:11434/v1", "ollama", "qwen2.5:7b"
                )
            )
        assert result is None

    def test_verify_timeout(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        mock_openai = MagicMock(return_value=mock_client)
        with patch("openai.AsyncOpenAI", mock_openai):
            result = asyncio.run(
                enhancer.verify_provider(
                    "http://localhost:11434/v1", "ollama", "qwen2.5:7b", timeout=5
                )
            )
        assert "timed out" in result

    def test_verify_connection_error(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("Connection refused")
        )
        mock_openai = MagicMock(return_value=mock_client)
        with patch("openai.AsyncOpenAI", mock_openai):
            result = asyncio.run(
                enhancer.verify_provider(
                    "http://localhost:99999/v1", "bad", "bad-model"
                )
            )
        assert "Connection refused" in result


class TestParseProviderText:
    """Tests for parse_provider_text."""

    @staticmethod
    def _parse(text):
        from voicetext.controllers.model_controller import parse_provider_text
        return parse_provider_text(text)

    def test_valid_config(self):
        text = """\
name: openai
base_url: https://api.openai.com/v1
api_key: sk-test
models:
  gpt-4o
  gpt-4o-mini"""
        result = self._parse(text)
        assert result == (
            "openai",
            "https://api.openai.com/v1",
            "sk-test",
            ["gpt-4o", "gpt-4o-mini"],
            {},
        )

    def test_single_model(self):
        text = """\
name: ollama
base_url: http://localhost:11434/v1
api_key: ollama
models:
  qwen2.5:7b"""
        result = self._parse(text)
        assert isinstance(result, tuple)
        assert result[3] == ["qwen2.5:7b"]

    def test_inline_model(self):
        text = """\
name: test
base_url: http://localhost/v1
api_key: key
models: single-model"""
        result = self._parse(text)
        assert isinstance(result, tuple)
        assert result[3] == ["single-model"]

    def test_missing_name(self):
        text = """\
base_url: http://localhost/v1
api_key: key
models:
  model"""
        result = self._parse(text)
        assert isinstance(result, str)
        assert "name" in result

    def test_missing_models(self):
        text = """\
name: test
base_url: http://localhost/v1
api_key: key"""
        result = self._parse(text)
        assert isinstance(result, str)
        assert "model" in result

    def test_extra_body(self):
        text = """\
name: qwen
base_url: http://localhost:8000/v1
api_key: sk-test
models:
  qwen3:8b
extra_body: {"chat_template_kwargs": {"enable_thinking": false}}"""
        result = self._parse(text)
        assert isinstance(result, tuple)
        assert result[4] == {"chat_template_kwargs": {"enable_thinking": False}}

    def test_invalid_extra_body(self):
        text = """\
name: test
base_url: http://localhost/v1
api_key: key
models:
  model
extra_body: not-json"""
        result = self._parse(text)
        assert isinstance(result, str)
        assert "extra_body" in result

    def test_empty_text(self):
        result = self._parse("")
        assert isinstance(result, str)


def _make_mock_client(content="enhanced text", usage=None):
    """Create a mock AsyncOpenAI client that returns given content."""
    mock_choice = MagicMock()
    mock_choice.message.content = content
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    if usage is not None:
        mock_response.usage.prompt_tokens = usage.get("prompt_tokens", 0)
        mock_response.usage.completion_tokens = usage.get("completion_tokens", 0)
        mock_response.usage.total_tokens = usage.get("total_tokens", 0)
    else:
        mock_response.usage = None

    mock_client = MagicMock()
    mock_create = AsyncMock(return_value=mock_response)
    mock_client.chat.completions.create = mock_create
    return mock_client


class TestTextEnhancerEnhance:
    def test_returns_original_when_inactive(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=False))
        text, usage = asyncio.run(
            enhancer.enhance("hello")
        )
        assert text == "hello"
        assert usage is None

    def test_returns_original_when_empty_input(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True))
        text, usage = asyncio.run(enhancer.enhance(""))
        assert text == ""
        assert usage is None

    def test_returns_original_when_whitespace_input(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True))
        text, usage = asyncio.run(enhancer.enhance("   "))
        assert text == "   "
        assert usage is None

    def test_returns_original_when_no_providers(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {}
        text, usage = asyncio.run(
            enhancer.enhance("hello")
        )
        assert text == "hello"
        assert usage is None

    def test_successful_enhancement(self):
        mock_client = _make_mock_client("enhanced text")
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        text, usage = asyncio.run(
            enhancer.enhance("original text")
        )
        assert text == "enhanced text"
        assert usage is None

    def test_successful_enhancement_with_usage(self):
        mock_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        mock_client = _make_mock_client("enhanced text", usage=mock_usage)
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        text, usage = asyncio.run(
            enhancer.enhance("original text")
        )
        assert text == "enhanced text"
        assert usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    def test_fallback_on_empty_llm_response(self):
        mock_client = _make_mock_client("")
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"

        text, usage = asyncio.run(
            enhancer.enhance("original text")
        )
        assert text == "original text"

    def test_fallback_on_none_llm_response(self):
        mock_client = _make_mock_client(None)
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"

        text, usage = asyncio.run(
            enhancer.enhance("original text")
        )
        assert text == "original text"

    @patch("voicetext.enhance.enhancer.asyncio.wait_for", side_effect=Exception("LLM error"))
    def test_fallback_on_exception(self, mock_wait_for):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"

        text, usage = asyncio.run(
            enhancer.enhance("original text")
        )
        assert text == "original text"
        assert usage is None

    @patch(
        "voicetext.enhance.enhancer.asyncio.wait_for",
        side_effect=asyncio.TimeoutError(),
    )
    def test_fallback_on_timeout(self, mock_wait_for):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"

        text, usage = asyncio.run(
            enhancer.enhance("original text")
        )
        assert text == "original text"
        assert usage is None


# --- Thinking / extra_body tests ---


class TestThinkingAndExtraBody:
    def test_thinking_defaults_to_false(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
        assert enhancer.thinking is False

    def test_thinking_can_be_enabled(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(thinking=True))
        assert enhancer.thinking is True

    def test_thinking_setter(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
        enhancer.thinking = True
        assert enhancer.thinking is True

    def test_build_extra_body_thinking_off_qwen(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(thinking=False))
            enhancer._active_model = "qwen2.5:7b"
        result = enhancer._build_extra_body({})
        assert result == {"chat_template_kwargs": {"enable_thinking": False}}

    def test_build_extra_body_thinking_off_glm(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(thinking=False))
            enhancer._active_model = "glm-4-flash"
        result = enhancer._build_extra_body({})
        assert result == {"thinking": {"type": "disabled"}}

    def test_build_extra_body_thinking_on_qwen(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(thinking=True))
            enhancer._active_model = "qwen2.5:7b"
        result = enhancer._build_extra_body({})
        assert result == {"chat_template_kwargs": {"enable_thinking": True}}

    def test_build_extra_body_thinking_on_glm(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(thinking=True))
            enhancer._active_model = "glm-4-flash"
        result = enhancer._build_extra_body({})
        assert result == {"thinking": {"type": "enabled"}}

    def test_build_extra_body_thinking_on_unknown_model(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(thinking=True))
            enhancer._active_model = "llama-3.1:8b"
        result = enhancer._build_extra_body({})
        assert result == {}

    def test_build_extra_body_thinking_off_unknown_model(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(thinking=False))
            enhancer._active_model = "llama-3.1:8b"
        result = enhancer._build_extra_body({})
        assert result == {}

    def test_build_extra_body_thinking_on_openai_reasoning(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(thinking=True))
            enhancer._active_model = "o3-mini"
        result = enhancer._build_extra_body({})
        assert result == {"reasoning_effort": "low"}

    def test_build_extra_body_thinking_off_openai_reasoning(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(thinking=False))
            enhancer._active_model = "o3-mini"
        result = enhancer._build_extra_body({})
        assert result == {}

    def test_build_extra_body_provider_overrides(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(thinking=False))
            enhancer._active_model = "qwen2.5:7b"
        provider_extra = {"chat_template_kwargs": {"enable_thinking": True}}
        result = enhancer._build_extra_body(provider_extra)
        # Provider-level extra_body overrides thinking toggle
        assert result["chat_template_kwargs"]["enable_thinking"] is True

    def test_build_extra_body_provider_overrides_glm(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(thinking=False))
            enhancer._active_model = "glm-4-flash"
        provider_extra = {"thinking": {"type": "enabled"}}
        result = enhancer._build_extra_body(provider_extra)
        assert result["thinking"]["type"] == "enabled"

    def test_build_extra_body_merges_provider_fields(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(thinking=False))
            enhancer._active_model = "qwen2.5:7b"
        provider_extra = {"custom_field": "value"}
        result = enhancer._build_extra_body(provider_extra)
        assert result["chat_template_kwargs"] == {"enable_thinking": False}
        assert result["custom_field"] == "value"

    # --- build_thinking_body tests ---

    def test_build_thinking_body_qwen_disabled(self):
        result = build_thinking_body("qwen2.5:7b", enabled=False)
        assert result == {"chat_template_kwargs": {"enable_thinking": False}}

    def test_build_thinking_body_qwen_enabled(self):
        result = build_thinking_body("qwen2.5:7b", enabled=True)
        assert result == {"chat_template_kwargs": {"enable_thinking": True}}

    def test_build_thinking_body_glm_disabled(self):
        result = build_thinking_body("glm-4-flash", enabled=False)
        assert result == {"thinking": {"type": "disabled"}}

    def test_build_thinking_body_glm_enabled(self):
        result = build_thinking_body("glm-4-flash", enabled=True)
        assert result == {"thinking": {"type": "enabled"}}

    def test_build_thinking_body_glm_case_insensitive(self):
        result = build_thinking_body("GLM-4", enabled=False)
        assert result == {"thinking": {"type": "disabled"}}

    def test_build_thinking_body_openai_o1_enabled(self):
        result = build_thinking_body("o1-preview", enabled=True)
        assert result == {"reasoning_effort": "low"}

    def test_build_thinking_body_openai_o3_enabled(self):
        result = build_thinking_body("o3-mini", enabled=True)
        assert result == {"reasoning_effort": "low"}

    def test_build_thinking_body_openai_o4_mini_enabled(self):
        result = build_thinking_body("o4-mini", enabled=True)
        assert result == {"reasoning_effort": "low"}

    def test_build_thinking_body_openai_reasoning_disabled(self):
        result = build_thinking_body("o3-mini", enabled=False)
        assert result == {}

    def test_build_thinking_body_deepseek_r1_enabled(self):
        result = build_thinking_body("deepseek-r1", enabled=True)
        assert result == {"reasoning_effort": "low"}

    def test_build_thinking_body_deepseek_reasoner_enabled(self):
        result = build_thinking_body("deepseek-reasoner", enabled=True)
        assert result == {"reasoning_effort": "low"}

    def test_build_thinking_body_deepseek_reasoning_disabled(self):
        result = build_thinking_body("deepseek-r1", enabled=False)
        assert result == {}

    def test_build_thinking_body_unknown_model(self):
        result = build_thinking_body("llama-3.1:8b", enabled=False)
        assert result == {}

    def test_build_thinking_body_unknown_model_enabled(self):
        result = build_thinking_body("llama-3.1:8b", enabled=True)
        assert result == {}

    def test_build_thinking_body_empty_model(self):
        result = build_thinking_body("", enabled=False)
        assert result == {}

    # --- helper function tests ---

    def test_is_openai_reasoning_model(self):
        assert _is_openai_reasoning_model("o1") is True
        assert _is_openai_reasoning_model("o1-preview") is True
        assert _is_openai_reasoning_model("o3-mini") is True
        assert _is_openai_reasoning_model("o4-mini") is True
        assert _is_openai_reasoning_model("gpt-4o") is False
        assert _is_openai_reasoning_model("qwen") is False

    def test_is_deepseek_reasoning_model(self):
        assert _is_deepseek_reasoning_model("deepseek-r1") is True
        assert _is_deepseek_reasoning_model("deepseek-r1-distill") is True
        assert _is_deepseek_reasoning_model("deepseek-reasoner") is True
        assert _is_deepseek_reasoning_model("deepseek-chat") is False
        assert _is_deepseek_reasoning_model("deepseek-v3") is False

    def test_enhance_passes_extra_body_when_thinking_off(self):
        mock_client = _make_mock_client("enhanced")
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, thinking=False))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        asyncio.run(enhancer.enhance("hello"))
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("extra_body") == {
            "chat_template_kwargs": {"enable_thinking": False}
        }

    def test_enhance_passes_extra_body_when_thinking_off_glm(self):
        mock_client = _make_mock_client("enhanced")
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, thinking=False))
            enhancer._providers = {
                "zhipu": (mock_client, ["glm-4-flash"], {}),
            }
            enhancer._active_provider = "zhipu"
            enhancer._active_model = "glm-4-flash"

        asyncio.run(enhancer.enhance("hello"))
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("extra_body") == {
            "thinking": {"type": "disabled"}
        }

    def test_enhance_extra_body_when_thinking_on_qwen(self):
        mock_client = _make_mock_client("enhanced")
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, thinking=True))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        asyncio.run(enhancer.enhance("hello"))
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("extra_body") == {
            "chat_template_kwargs": {"enable_thinking": True}
        }

    def test_enhance_no_extra_body_when_thinking_on_unknown(self):
        mock_client = _make_mock_client("enhanced")
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, thinking=True))
            enhancer._providers = {
                "ollama": (mock_client, ["llama-3.1:8b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "llama-3.1:8b"

        asyncio.run(enhancer.enhance("hello"))
        call_kwargs = mock_client.chat.completions.create.call_args
        assert "extra_body" not in call_kwargs.kwargs

    def test_enhance_no_extra_body_when_thinking_off_unknown(self):
        mock_client = _make_mock_client("enhanced")
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, thinking=False))
            enhancer._providers = {
                "ollama": (mock_client, ["llama-3.1:8b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "llama-3.1:8b"

        asyncio.run(enhancer.enhance("hello"))
        call_kwargs = mock_client.chat.completions.create.call_args
        assert "extra_body" not in call_kwargs.kwargs


# --- create_enhancer factory tests ---


class TestCreateEnhancer:
    def test_returns_none_when_no_config(self):
        assert create_enhancer({}) is None

    def test_returns_none_when_ai_enhance_missing(self):
        assert create_enhancer({"asr": {}}) is None

    def test_returns_enhancer_when_configured(self):
        config = {"ai_enhance": _make_config(enabled=True)}
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = create_enhancer(config)
        assert enhancer is not None
        assert isinstance(enhancer, TextEnhancer)

    def test_returns_enhancer_when_disabled(self):
        config = {"ai_enhance": _make_config(enabled=False)}
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = create_enhancer(config)
        assert enhancer is not None
        assert enhancer.is_active is False


# --- Vocabulary integration tests ---


class TestVocabularyIntegration:
    def test_vocab_disabled_by_default(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
        assert enhancer.vocab_enabled is False
        assert enhancer.vocab_index is None

    def test_vocab_enabled_creates_index(self):
        cfg = _make_config(vocabulary={"enabled": True, "top_k": 3})
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(cfg)
        assert enhancer.vocab_enabled is True
        assert enhancer.vocab_index is not None

    def test_vocab_toggle(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
        assert enhancer.vocab_enabled is False
        enhancer.vocab_enabled = True
        assert enhancer.vocab_enabled is True
        assert enhancer.vocab_index is not None

    def test_enhance_with_vocab_injects_context(self):
        mock_client = _make_mock_client("enhanced text")
        mock_vocab = MagicMock(spec=VocabularyIndex)
        mock_vocab.is_loaded = True
        mock_vocab.retrieve.return_value = [
            VocabularyEntry(term="Python", context="编程语言"),
        ]
        mock_vocab.format_for_prompt.return_value = (
            "---\n用户词库中与本次输入相关的专有名词，ASR 常将其误写为同音近音词。\n"
            "仅当输入中确实存在对应误写时才替换，不要强行套用：\n\n"
            "- Python（编程语言）\n---"
        )

        cfg = _make_config(enabled=True, vocabulary={"enabled": True, "top_k": 5})
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(cfg)
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"
            enhancer._vocab_index = mock_vocab

        asyncio.run(enhancer.enhance("派森编程"))

        # Verify system prompt includes vocab context
        call_kwargs = mock_client.chat.completions.create.call_args
        system_msg = call_kwargs.kwargs["messages"][0]["content"]
        assert "Python（编程语言）" in system_msg

    def test_enhance_without_vocab_no_injection(self):
        mock_client = _make_mock_client("enhanced text")

        cfg = _make_config(enabled=True)
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(cfg)
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        asyncio.run(enhancer.enhance("hello"))

        call_kwargs = mock_client.chat.completions.create.call_args
        system_msg = call_kwargs.kwargs["messages"][0]["content"]
        assert "从用户个人词库中检索到的" not in system_msg

    def test_enhance_vocab_retrieval_failure_graceful(self):
        mock_client = _make_mock_client("enhanced text")
        mock_vocab = MagicMock(spec=VocabularyIndex)
        mock_vocab.is_loaded = True
        mock_vocab.retrieve.side_effect = RuntimeError("embedding error")

        cfg = _make_config(enabled=True, vocabulary={"enabled": True, "top_k": 5})
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(cfg)
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"
            enhancer._vocab_index = mock_vocab

        text, usage = asyncio.run(
            enhancer.enhance("hello")
        )
        # Should still enhance successfully
        assert text == "enhanced text"

    def test_enhance_vocab_empty_results_no_injection(self):
        mock_client = _make_mock_client("enhanced text")
        mock_vocab = MagicMock(spec=VocabularyIndex)
        mock_vocab.is_loaded = True
        mock_vocab.retrieve.return_value = []

        cfg = _make_config(enabled=True, vocabulary={"enabled": True, "top_k": 5})
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(cfg)
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"
            enhancer._vocab_index = mock_vocab

        asyncio.run(enhancer.enhance("hello"))

        call_kwargs = mock_client.chat.completions.create.call_args
        system_msg = call_kwargs.kwargs["messages"][0]["content"]
        assert "从用户个人词库中检索到的" not in system_msg


# --- Debug flags tests ---


class TestDebugFlags:
    def test_debug_print_prompt_defaults_false(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
        assert enhancer.debug_print_prompt is False

    def test_debug_print_request_body_defaults_false(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
        assert enhancer.debug_print_request_body is False

    def test_debug_print_prompt_toggle(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
        enhancer.debug_print_prompt = True
        assert enhancer.debug_print_prompt is True
        enhancer.debug_print_prompt = False
        assert enhancer.debug_print_prompt is False

    def test_debug_print_request_body_toggle(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
        enhancer.debug_print_request_body = True
        assert enhancer.debug_print_request_body is True
        enhancer.debug_print_request_body = False
        assert enhancer.debug_print_request_body is False

    def test_enhance_logs_prompt_when_enabled(self):
        mock_client = _make_mock_client("enhanced")
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"
            enhancer.debug_print_prompt = True

        with patch("voicetext.enhance.enhancer.logger") as mock_logger:
            asyncio.run(
                enhancer.enhance("test input")
            )
            info_calls = [c for c in mock_logger.info.call_args_list
                          if "[DEBUG] System prompt:" in str(c) or
                          "[DEBUG] User message:" in str(c)]
            assert len(info_calls) == 2

    def test_enhance_logs_request_body_when_enabled(self):
        mock_client = _make_mock_client("enhanced")
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"
            enhancer.debug_print_request_body = True

        with patch("voicetext.enhance.enhancer.logger") as mock_logger:
            asyncio.run(
                enhancer.enhance("test input")
            )
            info_calls = [c for c in mock_logger.info.call_args_list
                          if "[DEBUG] Request body:" in str(c)]
            assert len(info_calls) == 1

    def test_enhance_no_debug_logs_when_disabled(self):
        mock_client = _make_mock_client("enhanced")
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        with patch("voicetext.enhance.enhancer.logger") as mock_logger:
            asyncio.run(
                enhancer.enhance("test input")
            )
            info_calls = [c for c in mock_logger.info.call_args_list
                          if "[DEBUG]" in str(c)]
            assert len(info_calls) == 0


# --- Conversation history integration tests ---


class TestConversationHistoryIntegration:
    def test_history_disabled_by_default(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
        assert enhancer.history_enabled is False

    def test_history_enabled_from_config(self):
        cfg = _make_config(conversation_history={"enabled": True, "max_entries": 5})
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(cfg)
        assert enhancer.history_enabled is True

    def test_history_toggle(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
        assert enhancer.history_enabled is False
        enhancer.history_enabled = True
        assert enhancer.history_enabled is True

    def test_conversation_history_property(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
        assert isinstance(enhancer.conversation_history, ConversationHistory)

    def test_enhance_with_history_injects_context(self):
        mock_client = _make_mock_client("enhanced text")
        mock_history = MagicMock(spec=ConversationHistory)
        mock_history.get_recent.return_value = [
            {"asr_text": "你好", "enhanced_text": "你好。", "final_text": "你好。"},
        ]
        mock_history.format_for_prompt.return_value = (
            "---\n以下是用户近期的对话记录，用于学习纠错偏好和话题上下文。\n"
            "若 ASR 识别与最终确认不同则用→分隔（识别→确认），相同则表示无需纠错：\n\n- 你好 → 你好。\n---"
        )

        cfg = _make_config(
            enabled=True,
            conversation_history={"enabled": True, "max_entries": 10},
        )
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(cfg)
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"
            enhancer._conversation_history = mock_history

        asyncio.run(enhancer.enhance("新输入"))

        call_kwargs = mock_client.chat.completions.create.call_args
        system_msg = call_kwargs.kwargs["messages"][0]["content"]
        assert "对话记录" in system_msg

    def test_enhance_without_history_no_injection(self):
        mock_client = _make_mock_client("enhanced text")

        cfg = _make_config(enabled=True)
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(cfg)
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        asyncio.run(enhancer.enhance("hello"))

        call_kwargs = mock_client.chat.completions.create.call_args
        system_msg = call_kwargs.kwargs["messages"][0]["content"]
        assert "对话历史记录" not in system_msg

    def test_enhance_history_retrieval_failure_graceful(self):
        mock_client = _make_mock_client("enhanced text")
        mock_history = MagicMock(spec=ConversationHistory)
        mock_history.get_recent.side_effect = RuntimeError("read error")

        cfg = _make_config(
            enabled=True,
            conversation_history={"enabled": True, "max_entries": 10},
        )
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(cfg)
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"
            enhancer._conversation_history = mock_history

        text, usage = asyncio.run(
            enhancer.enhance("hello")
        )
        assert text == "enhanced text"

    def test_enhance_history_empty_results_no_injection(self):
        mock_client = _make_mock_client("enhanced text")
        mock_history = MagicMock(spec=ConversationHistory)
        mock_history.get_recent.return_value = []

        cfg = _make_config(
            enabled=True,
            conversation_history={"enabled": True, "max_entries": 10},
        )
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(cfg)
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"
            enhancer._conversation_history = mock_history

        asyncio.run(enhancer.enhance("hello"))

        call_kwargs = mock_client.chat.completions.create.call_args
        system_msg = call_kwargs.kwargs["messages"][0]["content"]
        assert "对话历史记录" not in system_msg


class TestLastSystemPrompt:
    """Test last_system_prompt property."""

    def test_last_system_prompt_empty_by_default(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
        assert enhancer.last_system_prompt == ""

    def test_last_system_prompt_set_after_enhance(self):
        mock_client = _make_mock_client("enhanced text")
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        asyncio.run(enhancer.enhance("hello"))

        assert enhancer.last_system_prompt == "proofread prompt"

    def test_last_system_prompt_includes_vocab_context(self):
        mock_client = _make_mock_client("enhanced")
        mock_vocab = MagicMock(spec=VocabularyIndex)
        mock_vocab.is_loaded = True
        entries = [VocabularyEntry(term="API", context="Application Programming Interface")]
        mock_vocab.retrieve.return_value = entries
        mock_vocab.format_for_prompt.return_value = "# Vocabulary\n- API: Application Programming Interface"

        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(
                enabled=True, mode="proofread",
                vocabulary={"enabled": True, "top_k": 5},
            ))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"
            enhancer._vocab_index = mock_vocab

        asyncio.run(enhancer.enhance("test API"))

        assert "proofread prompt" in enhancer.last_system_prompt
        assert "Vocabulary" in enhancer.last_system_prompt


def _make_mock_stream_client(chunks, usage=None):
    """Create a mock AsyncOpenAI client that returns a streaming response."""
    async def _async_iter():
        for text in chunks:
            chunk = MagicMock()
            chunk.usage = None
            delta = MagicMock(spec=["content", "reasoning_content"])
            delta.content = text
            delta.reasoning_content = None
            choice = MagicMock()
            choice.delta = delta
            chunk.choices = [choice]
            yield chunk
        # Final chunk with usage
        final = MagicMock()
        if usage is not None:
            final.usage.prompt_tokens = usage.get("prompt_tokens", 0)
            final.usage.completion_tokens = usage.get("completion_tokens", 0)
            final.usage.total_tokens = usage.get("total_tokens", 0)
        else:
            final.usage = None
        final.choices = []
        yield final

    mock_client = MagicMock()
    mock_create = AsyncMock(return_value=_async_iter())
    mock_client.chat.completions.create = mock_create
    return mock_client


class TestTextEnhancerEnhanceStream:
    def test_returns_original_when_inactive(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=False))

        results = []
        async def collect():
            async for chunk, usage, is_thinking in enhancer.enhance_stream("hello"):
                results.append((chunk, usage, is_thinking))

        asyncio.run(collect())
        assert len(results) == 1
        assert results[0] == ("hello", None, False)

    def test_returns_original_when_empty(self):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"

        results = []
        async def collect():
            async for chunk, usage, is_thinking in enhancer.enhance_stream(""):
                results.append((chunk, usage, is_thinking))

        asyncio.run(collect())
        assert len(results) == 1
        assert results[0] == ("", None, False)

    def test_successful_streaming(self):
        mock_client = _make_mock_stream_client(
            ["enhanced", " ", "text"],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        results = []
        async def collect():
            async for chunk, usage, is_thinking in enhancer.enhance_stream("original text"):
                results.append((chunk, usage, is_thinking))

        asyncio.run(collect())
        # 3 content chunks + 1 final empty with usage
        text_chunks = [r[0] for r in results if r[0]]
        assert "".join(text_chunks) == "enhanced text"
        # Last result should have usage
        final = results[-1]
        assert final[1] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        # All chunks should have is_thinking=False (no reasoning_content in mock)
        assert all(r[2] is False for r in results)

    def test_fallback_on_empty_stream(self):
        mock_client = _make_mock_stream_client([], usage=None)
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        results = []
        async def collect():
            async for chunk, usage, is_thinking in enhancer.enhance_stream("original text"):
                results.append((chunk, usage, is_thinking))

        asyncio.run(collect())
        # Should yield original text as fallback
        text_chunks = [r[0] for r in results if r[0]]
        assert "".join(text_chunks) == "original text"

    @patch("voicetext.enhance.enhancer.asyncio.wait_for", side_effect=Exception("stream error"))
    def test_fallback_on_exception(self, mock_wait_for):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"

        results = []
        async def collect():
            async for chunk, usage, is_thinking in enhancer.enhance_stream("original text"):
                results.append((chunk, usage, is_thinking))

        asyncio.run(collect())
        assert len(results) == 1
        assert "(error:" in results[0][0]

    @patch("voicetext.enhance.enhancer.asyncio.wait_for", side_effect=asyncio.TimeoutError)
    def test_fallback_on_timeout(self, mock_wait_for):
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(
                enabled=True, mode="proofread", max_retries=0,
            ))
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"

        results = []
        async def collect():
            async for chunk, usage, is_thinking in enhancer.enhance_stream("original text"):
                results.append((chunk, usage, is_thinking))

        asyncio.run(collect())
        # With max_retries=0, single attempt fails and yields error thinking text
        assert len(results) == 1
        assert results[0][2] == "retry"  # is_thinking
        assert "all 1 attempts failed" in results[0][0]


class TestConnectionTimeoutRetry:
    """Tests for connection timeout retry logic in enhance_stream."""

    def test_connection_timeout_config(self):
        """Verify connection_timeout and max_retries are read from config."""
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(
                connection_timeout=5, max_retries=3,
            ))
        assert enhancer._connection_timeout == 5
        assert enhancer._max_retries == 3

    def test_connection_timeout_defaults(self):
        """Verify defaults when config keys are absent."""
        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
        assert enhancer._connection_timeout == 10
        assert enhancer._max_retries == 2

    def test_enhance_stream_connection_timeout_retry_success(self):
        """First 2 attempts timeout, third succeeds — verify retry yields and content."""
        mock_stream_iter = _make_mock_stream_client(
            ["ok", " text"],
            usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        )

        call_count = 0

        async def _create_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise asyncio.TimeoutError()
            return mock_stream_iter.chat.completions.create.return_value

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=_create_side_effect)

        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(
                enabled=True, mode="proofread",
                connection_timeout=1, max_retries=2,
            ))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        results = []

        async def collect():
            async for chunk, usage, is_thinking in enhancer.enhance_stream("hello"):
                results.append((chunk, usage, is_thinking))

        asyncio.run(collect())

        # Should have 2 retry yields, then content chunks, then final usage
        retry_results = [r for r in results if r[2] == "retry"]
        assert len(retry_results) == 2
        assert "retrying 1/2" in retry_results[0][0]
        assert "retrying 2/2" in retry_results[1][0]

        content_chunks = [r[0] for r in results if r[2] is False and r[0]]
        assert "".join(content_chunks) == "ok text"

    def test_enhance_stream_all_retries_exhausted(self):
        """All 3 attempts timeout — verify error yield, no content."""
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=asyncio.TimeoutError(),
        )

        with patch("voicetext.enhance.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(
                enabled=True, mode="proofread",
                connection_timeout=1, max_retries=2,
            ))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"], {}),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        results = []

        async def collect():
            async for chunk, usage, is_thinking in enhancer.enhance_stream("hello"):
                results.append((chunk, usage, is_thinking))

        asyncio.run(collect())

        # All yields should be retry (retry status + final error)
        assert all(r[2] == "retry" for r in results)
        # Should have 2 retry messages + 1 final error = 3 retry yields
        assert len(results) == 3
        assert "retrying 1/2" in results[0][0]
        assert "retrying 2/2" in results[1][0]
        assert "all 3 attempts failed" in results[2][0]
        # No content yields
        content = [r for r in results if r[2] is False]
        assert len(content) == 0
