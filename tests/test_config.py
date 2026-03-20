"""Tests for configuration module."""

import json
import os
import stat
import tempfile
from unittest.mock import MagicMock, patch


from wenzi.config import (
    DEFAULT_CONFIG,
    ConfigError,
    load_config,
    resolve_config_dir,
    save_config,
    set_config_readonly,
    validate_config,
    _merge_dict,
    _strip_jsonc,
)


class TestMergeDict:
    def test_flat_merge(self):
        base = {"a": 1, "b": 2}
        overrides = {"b": 3, "c": 4}
        result = _merge_dict(base, overrides)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        overrides = {"a": {"y": 99}}
        result = _merge_dict(base, overrides)
        assert result == {"a": {"x": 1, "y": 99}, "b": 3}

    def test_override_nested_with_scalar(self):
        base = {"a": {"x": 1}}
        overrides = {"a": "replaced"}
        result = _merge_dict(base, overrides)
        assert result == {"a": "replaced"}

    def test_empty_overrides(self):
        base = {"a": 1}
        assert _merge_dict(base, {}) == {"a": 1}


class TestStripJsonc:
    def test_no_comments(self):
        text = '{"a": 1, "b": "hello"}'
        assert json.loads(_strip_jsonc(text)) == {"a": 1, "b": "hello"}

    def test_single_line_comment(self):
        text = '{\n  "a": 1, // this is a comment\n  "b": 2\n}'
        assert json.loads(_strip_jsonc(text)) == {"a": 1, "b": 2}

    def test_block_comment(self):
        text = '{\n  /* block comment */\n  "a": 1\n}'
        assert json.loads(_strip_jsonc(text)) == {"a": 1}

    def test_multiline_block_comment(self):
        text = '{\n  /* block\n  comment\n  */\n  "a": 1\n}'
        assert json.loads(_strip_jsonc(text)) == {"a": 1}

    def test_trailing_comma_object(self):
        text = '{"a": 1, "b": 2,}'
        assert json.loads(_strip_jsonc(text)) == {"a": 1, "b": 2}

    def test_trailing_comma_array(self):
        text = '{"a": [1, 2, 3,]}'
        assert json.loads(_strip_jsonc(text)) == {"a": [1, 2, 3]}

    def test_comment_like_string_preserved(self):
        text = '{"url": "https://example.com", "note": "a // b"}'
        result = json.loads(_strip_jsonc(text))
        assert result["url"] == "https://example.com"
        assert result["note"] == "a // b"

    def test_slash_star_in_string_preserved(self):
        text = '{"pattern": "/* not a comment */"}'
        result = json.loads(_strip_jsonc(text))
        assert result["pattern"] == "/* not a comment */"

    def test_escaped_quote_in_string(self):
        text = r'{"msg": "say \"hello\" // world"}'
        result = json.loads(_strip_jsonc(text))
        assert result["msg"] == 'say "hello" // world'

    def test_comment_at_start_of_line(self):
        text = '{\n// comment line\n  "a": 1\n}'
        assert json.loads(_strip_jsonc(text)) == {"a": 1}

    def test_trailing_comma_with_whitespace(self):
        text = '{"a": 1 ,  \n  }'
        assert json.loads(_strip_jsonc(text)) == {"a": 1}

    def test_mixed_comments_and_trailing_commas(self):
        text = """{
  // Top comment
  "name": "test",  // inline
  "items": [
    1,
    2, /* block */ 3,
  ],
  /* "removed": true, */
}"""
        result = json.loads(_strip_jsonc(text))
        assert result == {"name": "test", "items": [1, 2, 3]}


class TestLoadConfig:
    def test_default_config_creates_file(self, tmp_path):
        config_file = tmp_path / "config.json"
        config, error = load_config(str(config_file))
        assert error is None
        assert config["hotkeys"] == {"fn": True}
        assert config["audio"]["sample_rate"] == 16000
        # File should be created
        assert config_file.exists()
        written = json.loads(config_file.read_text())
        assert written["hotkeys"] == {"fn": True}
        assert written["asr"]["backend"] == "funasr"

    def test_default_config_creates_parent_dirs(self, tmp_path):
        config_file = tmp_path / "sub" / "dir" / "config.json"
        config, error = load_config(str(config_file))
        assert error is None
        assert config_file.exists()
        assert config["hotkeys"] == {"fn": True}

    def test_load_from_file(self):
        overrides = {"hotkeys": {"f5": True}, "audio": {"sample_rate": 44100}}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(overrides, f)
            tmp_path = f.name

        try:
            config, error = load_config(tmp_path)
            assert error is None
            assert config["hotkeys"]["f5"] is True
            assert config["audio"]["sample_rate"] == 44100
            # Defaults should be preserved for unset keys
            assert config["audio"]["block_ms"] == 20
        finally:
            os.unlink(tmp_path)

    def test_migrate_legacy_hotkey(self):
        """Old 'hotkey' string auto-migrates to 'hotkeys' dict."""
        overrides = {"hotkey": "f5"}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(overrides, f)
            tmp_path = f.name

        try:
            config, error = load_config(tmp_path)
            assert error is None
            assert "hotkey" not in config
            assert config["hotkeys"] == {"f5": True}
            # File should be updated on disk
            written = json.loads(open(tmp_path).read())
            assert "hotkey" not in written
            assert written["hotkeys"] == {"f5": True}
        finally:
            os.unlink(tmp_path)

    def test_explicit_missing_file_creates_default(self, tmp_path):
        config_file = tmp_path / "nonexistent.json"
        config, error = load_config(str(config_file))
        assert error is None
        assert config_file.exists()
        assert config == DEFAULT_CONFIG

    def test_default_config_has_preset_field(self):
        assert "preset" in DEFAULT_CONFIG["asr"]
        assert DEFAULT_CONFIG["asr"]["preset"] is None

    def test_load_jsonc_with_comments(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text("""{
  // Audio settings
  "audio": {
    "sample_rate": 44100  /* custom rate */
  },
  "hotkeys": {"f5": true,}  // trailing comma
}""")
        config, error = load_config(str(config_file))
        assert error is None
        assert config["audio"]["sample_rate"] == 44100
        assert config["hotkeys"]["f5"] is True

    def test_syntax_error_returns_config_error(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text('{"bad": syntax}')
        config, error = load_config(str(config_file))
        assert error is not None
        assert isinstance(error, ConfigError)
        assert "Syntax error" in error.message
        # Should fall back to default config
        assert config == dict(DEFAULT_CONFIG)

    def test_non_object_returns_config_error(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text("[1, 2, 3]")
        config, error = load_config(str(config_file))
        assert error is not None
        assert "JSON object" in error.message
        assert config == dict(DEFAULT_CONFIG)

    def test_unreadable_file_returns_config_error(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text("{}")
        config_file.chmod(0o000)
        config, error = load_config(str(config_file))
        assert error is not None
        assert "Cannot read" in error.message
        assert config == dict(DEFAULT_CONFIG)
        # Restore permissions for cleanup
        config_file.chmod(0o644)


class TestSaveConfig:
    def test_save_and_reload(self, tmp_path):
        config_file = tmp_path / "config.json"
        config = dict(DEFAULT_CONFIG)
        config["asr"] = dict(config["asr"])
        config["asr"]["preset"] = "mlx-whisper-tiny"
        save_config(config, str(config_file))

        assert config_file.exists()
        loaded, error = load_config(str(config_file))
        assert error is None
        assert loaded["asr"]["preset"] == "mlx-whisper-tiny"

    def test_save_creates_parent_dirs(self, tmp_path):
        config_file = tmp_path / "sub" / "dir" / "config.json"
        save_config(DEFAULT_CONFIG, str(config_file))
        assert config_file.exists()

    def test_save_overwrites_existing(self, tmp_path):
        config_file = tmp_path / "config.json"
        save_config(DEFAULT_CONFIG, str(config_file))

        modified = dict(DEFAULT_CONFIG)
        modified["hotkeys"] = {"f5": True}
        save_config(modified, str(config_file))

        loaded, error = load_config(str(config_file))
        assert error is None
        assert loaded["hotkeys"]["f5"] is True

    def test_save_sets_owner_only_permissions(self, tmp_path):
        """Config files should be readable only by owner (0o600)."""
        config_file = tmp_path / "config.json"
        save_config(DEFAULT_CONFIG, str(config_file))

        mode = stat.S_IMODE(config_file.stat().st_mode)
        assert mode == 0o600

    def test_load_default_sets_owner_only_permissions(self, tmp_path):
        """Default config created by load_config should be 0o600."""
        config_file = tmp_path / "config.json"
        load_config(str(config_file))

        mode = stat.S_IMODE(config_file.stat().st_mode)
        assert mode == 0o600

    def test_readonly_prevents_save(self, tmp_path):
        config_file = tmp_path / "config.json"
        save_config(DEFAULT_CONFIG, str(config_file))
        original_content = config_file.read_text()

        set_config_readonly(True)
        try:
            modified = dict(DEFAULT_CONFIG)
            modified["hotkeys"] = {"f5": True}
            save_config(modified, str(config_file))
            # File should NOT have changed
            assert config_file.read_text() == original_content
        finally:
            set_config_readonly(False)

    def test_readonly_clear_allows_save(self, tmp_path):
        config_file = tmp_path / "config.json"
        save_config(DEFAULT_CONFIG, str(config_file))

        set_config_readonly(True)
        set_config_readonly(False)

        modified = dict(DEFAULT_CONFIG)
        modified["hotkeys"] = {"f5": True}
        save_config(modified, str(config_file))
        loaded, _ = load_config(str(config_file))
        assert loaded["hotkeys"]["f5"] is True


class TestValidateConfig:
    def _make_config(self, overrides=None):
        """Create a valid config with optional overrides."""
        import copy
        config = copy.deepcopy(DEFAULT_CONFIG)
        if overrides:
            for dotted_path, value in overrides.items():
                keys = dotted_path.split(".")
                d = config
                for k in keys[:-1]:
                    d = d[k]
                d[keys[-1]] = value
        return config

    def test_valid_config_unchanged(self):
        config = self._make_config()
        validated = validate_config(config)
        assert validated["audio"]["sample_rate"] == 16000
        assert validated["output"]["method"] == "auto"

    def test_invalid_sample_rate_type(self):
        config = self._make_config({"audio.sample_rate": "not_int"})
        validate_config(config)
        assert config["audio"]["sample_rate"] == DEFAULT_CONFIG["audio"]["sample_rate"]

    def test_negative_sample_rate(self):
        config = self._make_config({"audio.sample_rate": -1})
        validate_config(config)
        assert config["audio"]["sample_rate"] == DEFAULT_CONFIG["audio"]["sample_rate"]

    def test_zero_sample_rate(self):
        config = self._make_config({"audio.sample_rate": 0})
        validate_config(config)
        assert config["audio"]["sample_rate"] == DEFAULT_CONFIG["audio"]["sample_rate"]

    def test_valid_sample_rate_preserved(self):
        config = self._make_config({"audio.sample_rate": 44100})
        validate_config(config)
        assert config["audio"]["sample_rate"] == 44100

    def test_invalid_output_method(self):
        config = self._make_config({"output.method": "invalid"})
        validate_config(config)
        assert config["output"]["method"] == "auto"

    def test_valid_output_methods(self):
        for method in ("auto", "paste", "clipboard"):
            config = self._make_config({"output.method": method})
            validate_config(config)
            assert config["output"]["method"] == method

    def test_invalid_asr_backend(self):
        config = self._make_config({"asr.backend": "unknown"})
        validate_config(config)
        assert config["asr"]["backend"] == "funasr"

    def test_valid_asr_backends(self):
        for backend in ("funasr", "mlx-whisper", "mlx_whisper", "whisper-api", "apple", "sherpa-onnx"):
            config = self._make_config({"asr.backend": backend})
            validate_config(config)
            assert config["asr"]["backend"] == backend

    def test_invalid_log_level(self):
        config = self._make_config({"logging.level": "TRACE"})
        validate_config(config)
        assert config["logging"]["level"] == "INFO"

    def test_volume_out_of_range(self):
        config = self._make_config({"feedback.sound_volume": 1.5})
        validate_config(config)
        assert config["feedback"]["sound_volume"] == DEFAULT_CONFIG["feedback"]["sound_volume"]

    def test_negative_volume(self):
        config = self._make_config({"feedback.sound_volume": -0.1})
        validate_config(config)
        assert config["feedback"]["sound_volume"] == DEFAULT_CONFIG["feedback"]["sound_volume"]

    def test_volume_at_boundaries(self):
        for vol in (0.0, 1.0):
            config = self._make_config({"feedback.sound_volume": vol})
            validate_config(config)
            assert config["feedback"]["sound_volume"] == vol

    def test_bool_field_wrong_type(self):
        config = self._make_config({"output.append_newline": "yes"})
        validate_config(config)
        assert config["output"]["append_newline"] is False

    def test_silence_rms_negative(self):
        config = self._make_config({"audio.silence_rms": -5})
        validate_config(config)
        assert config["audio"]["silence_rms"] == DEFAULT_CONFIG["audio"]["silence_rms"]

    def test_silence_rms_zero_valid(self):
        config = self._make_config({"audio.silence_rms": 0})
        validate_config(config)
        assert config["audio"]["silence_rms"] == 0

    def test_empty_language(self):
        config = self._make_config({"asr.language": ""})
        validate_config(config)
        assert config["asr"]["language"] == "zh"

    def test_missing_section_does_not_crash(self):
        """Validation should not crash if a section is missing."""
        config = {"hotkeys": {"fn": True}}
        validate_config(config)  # should not raise

    def test_timeout_invalid(self):
        config = self._make_config({"ai_enhance.timeout": -1})
        validate_config(config)
        assert config["ai_enhance"]["timeout"] == DEFAULT_CONFIG["ai_enhance"]["timeout"]

    def test_max_retries_negative(self):
        config = self._make_config({"ai_enhance.max_retries": -1})
        validate_config(config)
        assert config["ai_enhance"]["max_retries"] == DEFAULT_CONFIG["ai_enhance"]["max_retries"]

    def test_max_retries_zero_valid(self):
        config = self._make_config({"ai_enhance.max_retries": 0})
        validate_config(config)
        assert config["ai_enhance"]["max_retries"] == 0

    def test_invalid_restart_key(self):
        config = self._make_config({"feedback.restart_key": "nonsense"})
        validate_config(config)
        assert config["feedback"]["restart_key"] == "cmd"

    def test_valid_restart_keys(self):
        for key in ("space", "cmd", "ctrl", "alt", "shift", "esc"):
            config = self._make_config({"feedback.restart_key": key})
            validate_config(config)
            assert config["feedback"]["restart_key"] == key

    def test_invalid_cancel_key(self):
        config = self._make_config({"feedback.cancel_key": 123})
        validate_config(config)
        assert config["feedback"]["cancel_key"] == "space"

    def test_valid_cancel_keys(self):
        for key in ("space", "cmd", "ctrl", "alt", "shift", "esc"):
            config = self._make_config({"feedback.cancel_key": key})
            validate_config(config)
            assert config["feedback"]["cancel_key"] == key

    def test_max_dynamic_hotwords_valid(self):
        config = self._make_config({"ai_enhance.vocabulary.max_dynamic_hotwords": 20})
        validate_config(config)
        assert config["ai_enhance"]["vocabulary"]["max_dynamic_hotwords"] == 20

    def test_max_dynamic_hotwords_invalid_zero(self):
        config = self._make_config({"ai_enhance.vocabulary.max_dynamic_hotwords": 0})
        validate_config(config)
        assert config["ai_enhance"]["vocabulary"]["max_dynamic_hotwords"] == 10

    def test_max_dynamic_hotwords_invalid_negative(self):
        config = self._make_config({"ai_enhance.vocabulary.max_dynamic_hotwords": -5})
        validate_config(config)
        assert config["ai_enhance"]["vocabulary"]["max_dynamic_hotwords"] == 10

    def test_max_dynamic_hotwords_invalid_type(self):
        config = self._make_config({"ai_enhance.vocabulary.max_dynamic_hotwords": "abc"})
        validate_config(config)
        assert config["ai_enhance"]["vocabulary"]["max_dynamic_hotwords"] == 10

    def test_max_static_hotwords_valid(self):
        config = self._make_config({"ai_enhance.vocabulary.max_static_hotwords": 100})
        validate_config(config)
        assert config["ai_enhance"]["vocabulary"]["max_static_hotwords"] == 100

    def test_max_static_hotwords_invalid_zero(self):
        config = self._make_config({"ai_enhance.vocabulary.max_static_hotwords": 0})
        validate_config(config)
        assert config["ai_enhance"]["vocabulary"]["max_static_hotwords"] == 50

    def test_max_static_hotwords_invalid_type(self):
        config = self._make_config({"ai_enhance.vocabulary.max_static_hotwords": "abc"})
        validate_config(config)
        assert config["ai_enhance"]["vocabulary"]["max_static_hotwords"] == 50


class TestResolveConfigDir:
    """Tests for resolve_config_dir with NSUserDefaults priority."""

    def test_explicit_argument_takes_priority(self):
        result = resolve_config_dir("/custom/path")
        assert result == "/custom/path"

    def test_explicit_argument_with_tilde(self):
        result = resolve_config_dir("~/custom")
        assert result == os.path.expanduser("~/custom")

    @patch("wenzi.config._read_user_defaults_config_dir")
    def test_user_defaults_used_when_no_argument(self, mock_read):
        mock_read.return_value = "/from/defaults"
        result = resolve_config_dir(None)
        assert result == "/from/defaults"
        mock_read.assert_called_once()

    @patch("wenzi.config._read_user_defaults_config_dir")
    def test_user_defaults_tilde_expanded(self, mock_read):
        mock_read.return_value = "~/from/defaults"
        result = resolve_config_dir(None)
        assert result == os.path.expanduser("~/from/defaults")

    @patch("wenzi.config._read_user_defaults_config_dir")
    def test_falls_back_to_default_when_no_preference(self, mock_read):
        mock_read.return_value = None
        result = resolve_config_dir(None)
        # DEFAULT_CONFIG_DIR may be patched by conftest; read the live value
        import wenzi.config as _cfg
        assert result == os.path.expanduser(_cfg.DEFAULT_CONFIG_DIR)

    @patch("wenzi.config._read_user_defaults_config_dir")
    def test_explicit_argument_overrides_user_defaults(self, mock_read):
        mock_read.return_value = "/from/defaults"
        result = resolve_config_dir("/explicit")
        assert result == "/explicit"
        mock_read.assert_not_called()


class TestConfigDirPreference:
    """Tests for save/reset config_dir via NSUserDefaults."""

    def test_save_config_dir_preference(self):
        mock_defaults = MagicMock()
        mock_cls = MagicMock()
        mock_cls.alloc.return_value.initWithSuiteName_.return_value = mock_defaults
        with patch.dict("sys.modules", {"Foundation": MagicMock(NSUserDefaults=mock_cls)}):
            # Re-import to pick up the patched Foundation
            import importlib
            import wenzi.config as cfg_mod
            importlib.reload(cfg_mod)
            cfg_mod.save_config_dir_preference("/new/path")
            mock_defaults.setObject_forKey_.assert_called_once_with(
                "/new/path", "config_dir"
            )
            mock_defaults.synchronize.assert_called_once()
            # Reload again to restore original module
            importlib.reload(cfg_mod)

    def test_reset_config_dir_preference(self):
        mock_defaults = MagicMock()
        mock_cls = MagicMock()
        mock_cls.alloc.return_value.initWithSuiteName_.return_value = mock_defaults
        with patch.dict("sys.modules", {"Foundation": MagicMock(NSUserDefaults=mock_cls)}):
            import importlib
            import wenzi.config as cfg_mod
            importlib.reload(cfg_mod)
            cfg_mod.reset_config_dir_preference()
            mock_defaults.removeObjectForKey_.assert_called_once_with("config_dir")
            mock_defaults.synchronize.assert_called_once()
            importlib.reload(cfg_mod)

    def test_read_user_defaults_returns_none_when_no_value(self):
        """Returns None when NSUserDefaults has no config_dir set."""
        mock_defaults = MagicMock()
        mock_defaults.stringForKey_.return_value = None
        mock_cls = MagicMock()
        mock_cls.alloc.return_value.initWithSuiteName_.return_value = mock_defaults
        with patch.dict("sys.modules", {"Foundation": MagicMock(NSUserDefaults=mock_cls)}):
            import importlib
            import wenzi.config as cfg_mod
            importlib.reload(cfg_mod)
            result = cfg_mod._read_user_defaults_config_dir()
            assert result is None
            importlib.reload(cfg_mod)

    def test_read_user_defaults_returns_value_when_set(self):
        """Returns the stored path when NSUserDefaults has config_dir."""
        mock_defaults = MagicMock()
        mock_defaults.stringForKey_.return_value = "/custom/config"
        mock_cls = MagicMock()
        mock_cls.alloc.return_value.initWithSuiteName_.return_value = mock_defaults
        with patch.dict("sys.modules", {"Foundation": MagicMock(NSUserDefaults=mock_cls)}):
            import importlib
            import wenzi.config as cfg_mod
            importlib.reload(cfg_mod)
            result = cfg_mod._read_user_defaults_config_dir()
            assert result == "/custom/config"
            importlib.reload(cfg_mod)


def test_validate_input_context_invalid_falls_back():
    config = {"ai_enhance": {"input_context": "invalid_value"}}
    result = validate_config(config)
    assert result["ai_enhance"]["input_context"] == "basic"


def test_validate_input_context_valid_values():
    for level in ("off", "basic", "detailed"):
        config = {"ai_enhance": {"input_context": level}}
        result = validate_config(config)
        assert result["ai_enhance"]["input_context"] == level
