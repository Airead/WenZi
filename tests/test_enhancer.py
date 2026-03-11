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


class TestTextEnhancerAddRemoveProvider:
    """Tests for adding and removing providers dynamically."""

    def test_add_provider_success(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        with patch(
            "voicetext.enhancer.TextEnhancer._init_single_provider"
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
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {}
        result = enhancer.add_provider("", "http://localhost", "key", ["model"])
        assert result is False

    def test_add_provider_empty_models_rejected(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {}
        result = enhancer.add_provider("test", "http://localhost", "key", [])
        assert result is False

    def test_add_first_provider_auto_selects(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {}
            enhancer._active_provider = ""
            enhancer._active_model = ""

        with patch(
            "voicetext.enhancer.TextEnhancer._init_single_provider"
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
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {}

        with patch(
            "voicetext.enhancer.TextEnhancer._init_single_provider"
        ):
            # _init_single_provider does nothing, so provider won't be added
            result = enhancer.add_provider(
                "bad", "http://localhost", "key", ["model"]
            )

        assert result is False
        assert "bad" not in enhancer.provider_names

    def test_remove_provider_success(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
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
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())
            enhancer._providers = {
                "ollama": (MagicMock(), ["qwen2.5:7b"]),
            }
        result = enhancer.remove_provider("nonexistent")
        assert result is False

    def test_remove_inactive_provider(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
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
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
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

    def test_verify_success(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())

        mock_resp = MagicMock()
        with patch("voicetext.enhancer.asyncio.wait_for", return_value=mock_resp):
            result = asyncio.get_event_loop().run_until_complete(
                enhancer.verify_provider(
                    "http://localhost:11434/v1", "ollama", "qwen2.5:7b"
                )
            )
        assert result is None

    def test_verify_timeout(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())

        with patch(
            "voicetext.enhancer.asyncio.wait_for",
            side_effect=asyncio.TimeoutError(),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                enhancer.verify_provider(
                    "http://localhost:11434/v1", "ollama", "qwen2.5:7b", timeout=5
                )
            )
        assert "timed out" in result

    def test_verify_connection_error(self):
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config())

        with patch(
            "voicetext.enhancer.asyncio.wait_for",
            side_effect=Exception("Connection refused"),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                enhancer.verify_provider(
                    "http://localhost:99999/v1", "bad", "bad-model"
                )
            )
        assert "Connection refused" in result


class TestParseProviderText:
    """Tests for VoiceTextApp._parse_provider_text."""

    @staticmethod
    def _parse(text):
        from voicetext.app import VoiceTextApp
        return VoiceTextApp._parse_provider_text(text)

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

    def test_empty_text(self):
        result = self._parse("")
        assert isinstance(result, str)


def _make_mock_client(content="enhanced text"):
    """Create a mock AsyncOpenAI client that returns given content."""
    mock_choice = MagicMock()
    mock_choice.message.content = content
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    mock_client = MagicMock()
    mock_create = AsyncMock(return_value=mock_response)
    mock_client.chat.completions.create = mock_create
    return mock_client


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

    def test_successful_enhancement(self):
        mock_client = _make_mock_client("enhanced text")
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"]),
            }
            enhancer._active_provider = "ollama"
            enhancer._active_model = "qwen2.5:7b"

        result = asyncio.get_event_loop().run_until_complete(
            enhancer.enhance("original text")
        )
        assert result == "enhanced text"

    def test_fallback_on_empty_llm_response(self):
        mock_client = _make_mock_client("")
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"]),
            }
            enhancer._active_provider = "ollama"

        result = asyncio.get_event_loop().run_until_complete(
            enhancer.enhance("original text")
        )
        assert result == "original text"

    def test_fallback_on_none_llm_response(self):
        mock_client = _make_mock_client(None)
        with patch("voicetext.enhancer.TextEnhancer._init_providers"):
            enhancer = TextEnhancer(_make_config(enabled=True, mode="proofread"))
            enhancer._providers = {
                "ollama": (mock_client, ["qwen2.5:7b"]),
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
