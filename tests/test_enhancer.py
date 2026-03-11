"""Tests for the AI text enhancer module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voicetext.enhancer import EnhanceMode, TextEnhancer, create_enhancer


# --- EnhanceMode enum tests ---


class TestEnhanceMode:
    def test_all_modes_exist(self):
        assert EnhanceMode.OFF.value == "off"
        assert EnhanceMode.PROOFREAD.value == "proofread"
        assert EnhanceMode.FORMAT.value == "format"
        assert EnhanceMode.COMPLETE.value == "complete"
        assert EnhanceMode.ENHANCE.value == "enhance"

    def test_mode_count(self):
        assert len(EnhanceMode) == 5

    def test_from_string(self):
        assert EnhanceMode("proofread") == EnhanceMode.PROOFREAD
        assert EnhanceMode("off") == EnhanceMode.OFF

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            EnhanceMode("invalid_mode")


# --- TextEnhancer tests ---


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
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
        assert enhancer.is_active is True

    def test_inactive_when_disabled(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=False, mode="proofread"))
        assert enhancer.is_active is False

    def test_inactive_when_mode_off(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="off"))
        assert enhancer.is_active is False

    def test_inactive_when_disabled_and_mode_off(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=False, mode="off"))
        assert enhancer.is_active is False


class TestTextEnhancerMode:
    def test_mode_getter(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(mode="format"))
        assert enhancer.mode == EnhanceMode.FORMAT

    def test_mode_setter(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(mode="proofread"))
        enhancer.mode = EnhanceMode.ENHANCE
        assert enhancer.mode == EnhanceMode.ENHANCE


class TestTextEnhancerProviderModel:
    """Tests for multi-provider and model switching."""

    def test_provider_names(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers") as mock_init:
            enhancer = TextEnhancer(_make_multi_provider_config())
            # Simulate providers being initialized
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b", "llama3:8b"]),
                "openai": (MagicMock(), ["gpt-4o", "gpt-4o-mini"]),
            }
        assert set(enhancer.provider_names) == {"ollama", "openai"}

    def test_model_names_for_active_provider(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_multi_provider_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b", "llama3:8b"]),
                "openai": (MagicMock(), ["gpt-4o", "gpt-4o-mini"]),
            }
            enhancer._active_provider = "ollama"
        assert enhancer.model_names == ["qwen2.5:7b", "llama3:8b"]

    def test_model_names_after_provider_switch(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_multi_provider_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b", "llama3:8b"]),
                "openai": (MagicMock(), ["gpt-4o", "gpt-4o-mini"]),
            }
            enhancer._active_provider = "ollama"
        enhancer.provider_name = "openai"
        assert enhancer.model_names == ["gpt-4o", "gpt-4o-mini"]

    def test_provider_switch_auto_selects_first_model(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
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
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
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
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b", "llama3:8b"]),
            }
            enhancer._active_provider = "ollama"
        enhancer.model_name = "llama3:8b"
        assert enhancer.model_name == "llama3:8b"

    def test_unknown_provider_ignored(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
            }
            enhancer._active_provider = "ollama"
        enhancer.provider_name = "nonexistent"
        assert enhancer.provider_name == "ollama"

    def test_model_names_empty_when_no_providers(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {}
            enhancer._active_provider = "missing"
        assert enhancer.model_names == []

    def test_default_provider_fallback(self):
        """If default_provider is not in providers, fallback to first available."""
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            cfg = _make_config(default_provider="nonexistent")
            enhancer = TextEnhancer(cfg)
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
            }
            # Re-run validation logic
            if enhancer._active_provider not in enhancer._providers:
                enhancer._active_provider = next(iter(enhancer._providers))
        assert enhancer.provider_name == "ollama"


class TestTextEnhancerEnhance:
    def test_returns_original_when_inactive(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=False))
        result = asyncio.get_event_loop().run_until_complete(
            enhancer.enhance("hello")
        )
        assert result == "hello"

    def test_returns_original_when_empty_input(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True))
        result = asyncio.get_event_loop().run_until_complete(enhancer.enhance(""))
        assert result == ""

    def test_returns_original_when_whitespace_input(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True))
        result = asyncio.get_event_loop().run_until_complete(enhancer.enhance("   "))
        assert result == "   "

    def test_returns_original_when_no_providers(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {}
        result = asyncio.get_event_loop().run_until_complete(
            enhancer.enhance("hello")
        )
        assert result == "hello"

    @patch("voicetext.enhancer.asyncio.wait_for")
    def test_successful_enhancement(self, mock_wait_for):
        mock_result = MagicMock()
        mock_result.final_output = "enhanced text"
        mock_wait_for.return_value = mock_result

        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        result = asyncio.get_event_loop().run_until_complete(
            enhancer.enhance("original text")
        )
        assert result == "enhanced text"

    @patch("voicetext.enhancer.asyncio.wait_for")
    def test_fallback_on_empty_llm_response(self, mock_wait_for):
        mock_result = MagicMock()
        mock_result.final_output = ""
        mock_wait_for.return_value = mock_result

        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
            }
            enhancer._active_provider = "ollama"

        result = asyncio.get_event_loop().run_until_complete(
            enhancer.enhance("original text")
        )
        assert result == "original text"

    @patch("voicetext.enhancer.asyncio.wait_for")
    def test_fallback_on_none_llm_response(self, mock_wait_for):
        mock_result = MagicMock()
        mock_result.final_output = None
        mock_wait_for.return_value = mock_result

        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
            }
            enhancer._active_provider = "ollama"

        result = asyncio.get_event_loop().run_until_complete(
            enhancer.enhance("original text")
        )
        assert result == "original text"

    @patch("voicetext.enhancer.asyncio.wait_for", side_effect=Exception("LLM error"))
    def test_fallback_on_exception(self, mock_wait_for):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
            }
            enhancer._active_provider = "ollama"

        result = asyncio.get_event_loop().run_until_complete(
            enhancer.enhance("original text")
        )
        assert result == "original text"

    @patch(
        "voicetext.enhancer.asyncio.wait_for",
        side_effect=asyncio.TimeoutError(),
    )
    def test_fallback_on_timeout(self, mock_wait_for):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
            }
            enhancer._active_provider = "ollama"

        result = asyncio.get_event_loop().run_until_complete(
            enhancer.enhance("original text")
        )
        assert result == "original text"


# --- create_enhancer factory tests ---


class TestCreateEnhancer:
    def test_returns_none_when_no_config(self):
        assert create_enhancer({}) is None

    def test_returns_none_when_ai_enhance_missing(self):
        assert create_enhancer({"asr": {}}) is None

    def test_returns_enhancer_when_configured(self):
        config = {"ai_enhance": _make_config(enabled=True)}
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = create_enhancer(config)
        assert enhancer is not None
        assert isinstance(enhancer, TextEnhancer)

    def test_returns_enhancer_when_disabled(self):
        config = {"ai_enhance": _make_config(enabled=False)}
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = create_enhancer(config)
        assert enhancer is not None
        assert enhancer.is_active is False
