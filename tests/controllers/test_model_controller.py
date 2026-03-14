"""Tests for model_controller module-level functions and ModelController draft methods."""

from __future__ import annotations

import os
from unittest.mock import MagicMock


from voicetext.controllers.model_controller import (
    _get_dir_size,
    migrate_asr_config,
    parse_asr_provider_text,
    parse_provider_text,
    ModelController,
)


# ---------------------------------------------------------------------------
# parse_asr_provider_text
# ---------------------------------------------------------------------------


class TestParseAsrProviderText:
    """Tests for parse_asr_provider_text()."""

    def test_valid_config_returns_tuple(self):
        text = (
            "name: my-asr\n"
            "base_url: https://api.example.com/v1\n"
            "api_key: sk-test\n"
            "models:\n"
            "  whisper-large-v3\n"
        )
        result = parse_asr_provider_text(text)
        assert isinstance(result, tuple)
        name, base_url, api_key, models = result
        assert name == "my-asr"
        assert base_url == "https://api.example.com/v1"
        assert api_key == "sk-test"
        assert models == ["whisper-large-v3"]

    def test_multiple_indented_models(self):
        text = (
            "name: groq\n"
            "base_url: https://api.groq.com/openai/v1\n"
            "api_key: gsk-xxx\n"
            "models:\n"
            "  whisper-large-v3-turbo\n"
            "  whisper-large-v3\n"
        )
        result = parse_asr_provider_text(text)
        assert isinstance(result, tuple)
        _, _, _, models = result
        assert models == ["whisper-large-v3-turbo", "whisper-large-v3"]

    def test_inline_model_on_models_line(self):
        """Model placed directly after 'models:' on the same line is accepted."""
        text = (
            "name: provider\n"
            "base_url: https://api.example.com/v1\n"
            "api_key: key\n"
            "models: whisper-large-v3\n"
        )
        result = parse_asr_provider_text(text)
        assert isinstance(result, tuple)
        _, _, _, models = result
        assert "whisper-large-v3" in models

    def test_missing_name_returns_error(self):
        text = (
            "base_url: https://api.example.com/v1\n"
            "api_key: sk-test\n"
            "models:\n"
            "  whisper-large-v3\n"
        )
        result = parse_asr_provider_text(text)
        assert isinstance(result, str)
        assert "name" in result

    def test_missing_base_url_returns_error(self):
        text = (
            "name: my-asr\n"
            "api_key: sk-test\n"
            "models:\n"
            "  whisper-large-v3\n"
        )
        result = parse_asr_provider_text(text)
        assert isinstance(result, str)
        assert "base_url" in result

    def test_missing_api_key_returns_error(self):
        text = (
            "name: my-asr\n"
            "base_url: https://api.example.com/v1\n"
            "models:\n"
            "  whisper-large-v3\n"
        )
        result = parse_asr_provider_text(text)
        assert isinstance(result, str)
        assert "api_key" in result

    def test_missing_models_returns_error(self):
        text = (
            "name: my-asr\n"
            "base_url: https://api.example.com/v1\n"
            "api_key: sk-test\n"
        )
        result = parse_asr_provider_text(text)
        assert isinstance(result, str)
        assert "model" in result

    def test_multiple_missing_fields_error_contains_all(self):
        result = parse_asr_provider_text("")
        assert isinstance(result, str)
        assert "name" in result
        assert "base_url" in result
        assert "api_key" in result

    def test_extra_fields_after_models_block_do_not_break_parsing(self):
        """A non-indented key after models list ends the models block."""
        text = (
            "name: provider\n"
            "models:\n"
            "  model-a\n"
            "base_url: https://api.example.com/v1\n"
            "api_key: key\n"
        )
        result = parse_asr_provider_text(text)
        assert isinstance(result, tuple)
        _, _, _, models = result
        assert models == ["model-a"]

    def test_blank_lines_are_ignored(self):
        text = (
            "\n"
            "name: my-asr\n"
            "\n"
            "base_url: https://api.example.com/v1\n"
            "api_key: sk-test\n"
            "models:\n"
            "  whisper-large-v3\n"
        )
        result = parse_asr_provider_text(text)
        assert isinstance(result, tuple)


# ---------------------------------------------------------------------------
# parse_provider_text
# ---------------------------------------------------------------------------


class TestParseProviderText:
    """Tests for parse_provider_text() (LLM provider)."""

    def test_valid_config_returns_tuple(self):
        text = (
            "name: openai\n"
            "base_url: https://api.openai.com/v1\n"
            "api_key: sk-xxx\n"
            "models:\n"
            "  gpt-4o\n"
            "  gpt-4o-mini\n"
        )
        result = parse_provider_text(text)
        assert isinstance(result, tuple)
        name, base_url, api_key, models, extra_body = result
        assert name == "openai"
        assert base_url == "https://api.openai.com/v1"
        assert api_key == "sk-xxx"
        assert models == ["gpt-4o", "gpt-4o-mini"]
        assert extra_body == {}

    def test_valid_extra_body_json(self):
        text = (
            "name: provider\n"
            "base_url: https://api.example.com/v1\n"
            "api_key: key\n"
            "extra_body: {\"enable_thinking\": true}\n"
            "models:\n"
            "  model-x\n"
        )
        result = parse_provider_text(text)
        assert isinstance(result, tuple)
        _, _, _, _, extra_body = result
        assert extra_body == {"enable_thinking": True}

    def test_invalid_extra_body_json_returns_error(self):
        text = (
            "name: provider\n"
            "base_url: https://api.example.com/v1\n"
            "api_key: key\n"
            "extra_body: not-valid-json\n"
            "models:\n"
            "  model-x\n"
        )
        result = parse_provider_text(text)
        assert isinstance(result, str)
        assert "extra_body" in result

    def test_extra_body_non_object_json_returns_error(self):
        """extra_body must be a JSON object, not an array or scalar."""
        text = (
            "name: provider\n"
            "base_url: https://api.example.com/v1\n"
            "api_key: key\n"
            "extra_body: [1, 2, 3]\n"
            "models:\n"
            "  model-x\n"
        )
        result = parse_provider_text(text)
        assert isinstance(result, str)
        assert "extra_body" in result

    def test_missing_name_returns_error(self):
        text = (
            "base_url: https://api.example.com/v1\n"
            "api_key: key\n"
            "models:\n"
            "  model-x\n"
        )
        result = parse_provider_text(text)
        assert isinstance(result, str)
        assert "name" in result

    def test_missing_base_url_returns_error(self):
        text = (
            "name: provider\n"
            "api_key: key\n"
            "models:\n"
            "  model-x\n"
        )
        result = parse_provider_text(text)
        assert isinstance(result, str)
        assert "base_url" in result

    def test_missing_api_key_returns_error(self):
        text = (
            "name: provider\n"
            "base_url: https://api.example.com/v1\n"
            "models:\n"
            "  model-x\n"
        )
        result = parse_provider_text(text)
        assert isinstance(result, str)
        assert "api_key" in result

    def test_missing_models_returns_error(self):
        text = (
            "name: provider\n"
            "base_url: https://api.example.com/v1\n"
            "api_key: key\n"
        )
        result = parse_provider_text(text)
        assert isinstance(result, str)
        assert "model" in result

    def test_no_extra_body_field_returns_empty_dict(self):
        text = (
            "name: provider\n"
            "base_url: https://api.example.com/v1\n"
            "api_key: key\n"
            "models:\n"
            "  model-x\n"
        )
        result = parse_provider_text(text)
        assert isinstance(result, tuple)
        _, _, _, _, extra_body = result
        assert extra_body == {}

    def test_tab_indented_models(self):
        text = "name: p\nbase_url: https://u\napi_key: k\nmodels:\n\tmodel-a\n\tmodel-b\n"
        result = parse_provider_text(text)
        assert isinstance(result, tuple)
        _, _, _, models, _ = result
        assert models == ["model-a", "model-b"]


# ---------------------------------------------------------------------------
# migrate_asr_config
# ---------------------------------------------------------------------------


class TestMigrateAsrConfig:
    """Tests for migrate_asr_config()."""

    def test_groq_url_creates_groq_provider(self):
        cfg = {
            "base_url": "https://api.groq.com/openai/v1",
            "api_key": "gsk-test",
            "backend": "whisper-api",
        }
        migrate_asr_config(cfg)
        assert "groq" in cfg["providers"]
        assert cfg["providers"]["groq"]["base_url"] == "https://api.groq.com/openai/v1"
        assert cfg["providers"]["groq"]["models"] == ["whisper-large-v3-turbo"]
        assert cfg["default_provider"] == "groq"
        assert cfg["default_model"] == "whisper-large-v3-turbo"

    def test_generic_url_creates_migrated_provider(self):
        cfg = {
            "base_url": "https://custom.api.com/v1",
            "api_key": "mykey",
            "model": "my-model",
        }
        migrate_asr_config(cfg)
        assert "migrated" in cfg["providers"]
        assert cfg["providers"]["migrated"]["models"] == ["my-model"]

    def test_generic_url_without_model_uses_default_model(self):
        cfg = {
            "base_url": "https://custom.api.com/v1",
            "api_key": "mykey",
        }
        migrate_asr_config(cfg)
        assert cfg["providers"]["migrated"]["models"] == ["whisper-large-v3-turbo"]

    def test_no_base_url_does_nothing(self):
        cfg = {"api_key": "mykey"}
        migrate_asr_config(cfg)
        assert "providers" not in cfg

    def test_no_api_key_does_nothing(self):
        cfg = {"base_url": "https://api.groq.com/openai/v1"}
        migrate_asr_config(cfg)
        assert "providers" not in cfg

    def test_existing_providers_not_overwritten(self):
        """If providers dict is already populated, migration should not overwrite it."""
        cfg = {
            "base_url": "https://api.groq.com/openai/v1",
            "api_key": "gsk-test",
            "providers": {"existing": {"base_url": "x", "api_key": "y", "models": ["m"]}},
        }
        migrate_asr_config(cfg)
        # The existing provider must remain; groq must NOT be added
        assert "existing" in cfg["providers"]
        assert "groq" not in cfg["providers"]

    def test_base_url_and_api_key_removed_after_migration(self):
        cfg = {
            "base_url": "https://api.groq.com/openai/v1",
            "api_key": "gsk-test",
        }
        migrate_asr_config(cfg)
        assert "base_url" not in cfg
        assert "api_key" not in cfg

    def test_non_whisper_api_backend_does_not_set_default_provider(self):
        """default_provider/model should only be set when backend == 'whisper-api'."""
        cfg = {
            "base_url": "https://custom.api.com/v1",
            "api_key": "mykey",
            "backend": "funasr",
        }
        migrate_asr_config(cfg)
        assert "default_provider" not in cfg
        assert "default_model" not in cfg


# ---------------------------------------------------------------------------
# _get_dir_size
# ---------------------------------------------------------------------------


class TestGetDirSize:
    """Tests for _get_dir_size()."""

    def test_empty_dir_returns_zero(self, tmp_path):
        size = _get_dir_size(tmp_path)
        assert size == 0

    def test_nonexistent_dir_returns_zero(self, tmp_path):
        missing = tmp_path / "nonexistent"
        assert _get_dir_size(missing) == 0

    def test_single_file_returns_file_size(self, tmp_path):
        data = b"hello world"
        (tmp_path / "file.bin").write_bytes(data)
        size = _get_dir_size(tmp_path)
        assert size == len(data)

    def test_multiple_files_returns_total(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"aaa")
        (tmp_path / "b.txt").write_bytes(b"bbbbb")
        size = _get_dir_size(tmp_path)
        assert size == 8

    def test_nested_files_are_counted(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.bin").write_bytes(b"x" * 100)
        (tmp_path / "top.bin").write_bytes(b"y" * 50)
        size = _get_dir_size(tmp_path)
        assert size == 150


# ---------------------------------------------------------------------------
# ModelController draft methods
# ---------------------------------------------------------------------------


def _make_controller(config_path: str) -> ModelController:
    """Build a ModelController backed by a minimal mock app."""
    app = MagicMock()
    app._config_path = config_path
    ctrl = ModelController.__new__(ModelController)
    ctrl._app = app
    return ctrl


class TestAsrProviderDraft:
    """Tests for ASR provider draft load/save/remove."""

    def test_load_returns_template_when_no_draft_file(self, tmp_path):
        config_path = str(tmp_path / "config.json")
        ctrl = _make_controller(config_path)
        content = ctrl._load_asr_provider_draft()
        assert content == ModelController._ADD_ASR_PROVIDER_TEMPLATE

    def test_save_and_load_draft(self, tmp_path):
        config_path = str(tmp_path / "config.json")
        ctrl = _make_controller(config_path)
        ctrl._save_asr_provider_draft("custom draft content")
        loaded = ctrl._load_asr_provider_draft()
        assert loaded == "custom draft content"

    def test_load_returns_template_when_draft_is_blank(self, tmp_path):
        config_path = str(tmp_path / "config.json")
        ctrl = _make_controller(config_path)
        ctrl._save_asr_provider_draft("   \n   ")
        content = ctrl._load_asr_provider_draft()
        assert content == ModelController._ADD_ASR_PROVIDER_TEMPLATE

    def test_remove_draft_deletes_file(self, tmp_path):
        config_path = str(tmp_path / "config.json")
        ctrl = _make_controller(config_path)
        ctrl._save_asr_provider_draft("something")
        draft_path = ctrl._get_asr_provider_draft_path()
        assert os.path.exists(draft_path)
        ctrl._remove_asr_provider_draft()
        assert not os.path.exists(draft_path)

    def test_remove_draft_does_not_raise_when_file_missing(self, tmp_path):
        config_path = str(tmp_path / "config.json")
        ctrl = _make_controller(config_path)
        # No file written; removal should silently succeed
        ctrl._remove_asr_provider_draft()

    def test_draft_stored_in_same_dir_as_config(self, tmp_path):
        """Draft file lives in the same directory as the config file."""
        config_path = str(tmp_path / "VoiceText" / "config.json")
        ctrl = _make_controller(config_path)
        draft_path = ctrl._get_asr_provider_draft_path()
        expected_dir = str(tmp_path / "VoiceText")
        assert os.path.dirname(draft_path) == expected_dir
        assert os.path.basename(draft_path) == ModelController._ASR_PROVIDER_DRAFT_FILENAME


class TestLlmProviderDraft:
    """Tests for LLM provider draft load/save/remove."""

    def test_load_returns_template_when_no_draft_file(self, tmp_path):
        config_path = str(tmp_path / "config.json")
        ctrl = _make_controller(config_path)
        content = ctrl._load_provider_draft()
        assert content == ModelController._ADD_PROVIDER_TEMPLATE

    def test_save_and_load_draft(self, tmp_path):
        config_path = str(tmp_path / "config.json")
        ctrl = _make_controller(config_path)
        ctrl._save_provider_draft("llm draft content")
        loaded = ctrl._load_provider_draft()
        assert loaded == "llm draft content"

    def test_load_returns_template_when_draft_is_blank(self, tmp_path):
        config_path = str(tmp_path / "config.json")
        ctrl = _make_controller(config_path)
        ctrl._save_provider_draft("\t\n")
        content = ctrl._load_provider_draft()
        assert content == ModelController._ADD_PROVIDER_TEMPLATE

    def test_remove_draft_deletes_file(self, tmp_path):
        config_path = str(tmp_path / "config.json")
        ctrl = _make_controller(config_path)
        ctrl._save_provider_draft("something")
        draft_path = ctrl._get_provider_draft_path()
        assert os.path.exists(draft_path)
        ctrl._remove_provider_draft()
        assert not os.path.exists(draft_path)

    def test_remove_draft_does_not_raise_when_file_missing(self, tmp_path):
        config_path = str(tmp_path / "config.json")
        ctrl = _make_controller(config_path)
        ctrl._remove_provider_draft()

    def test_draft_stored_in_same_dir_as_config(self, tmp_path):
        """Draft file lives in the same directory as the config file."""
        config_path = str(tmp_path / "VoiceText" / "config.json")
        ctrl = _make_controller(config_path)
        draft_path = ctrl._get_provider_draft_path()
        expected_dir = str(tmp_path / "VoiceText")
        assert os.path.dirname(draft_path) == expected_dir
        assert os.path.basename(draft_path) == ModelController._PROVIDER_DRAFT_FILENAME

    def test_asr_and_llm_drafts_use_different_filenames(self, tmp_path):
        """ASR and LLM drafts must not collide with each other."""
        config_path = str(tmp_path / "config.json")
        ctrl = _make_controller(config_path)
        assert ctrl._get_asr_provider_draft_path() != ctrl._get_provider_draft_path()
