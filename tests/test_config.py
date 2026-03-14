"""Tests for configuration module."""

import json
import os
import stat
import tempfile


from voicetext.config import DEFAULT_CONFIG, load_config, save_config, validate_config, _merge_dict


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


class TestLoadConfig:
    def test_default_config_creates_file(self, tmp_path):
        config_file = tmp_path / "config.json"
        config = load_config(str(config_file))
        assert config["hotkeys"] == {"fn": True}
        assert config["audio"]["sample_rate"] == 16000
        # File should be created
        assert config_file.exists()
        written = json.loads(config_file.read_text())
        assert written["hotkeys"] == {"fn": True}
        assert written["asr"]["backend"] == "funasr"

    def test_default_config_creates_parent_dirs(self, tmp_path):
        config_file = tmp_path / "sub" / "dir" / "config.json"
        config = load_config(str(config_file))
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
            config = load_config(tmp_path)
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
            config = load_config(tmp_path)
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
        config = load_config(str(config_file))
        assert config_file.exists()
        assert config == DEFAULT_CONFIG

    def test_default_config_has_preset_field(self):
        assert "preset" in DEFAULT_CONFIG["asr"]
        assert DEFAULT_CONFIG["asr"]["preset"] is None


class TestSaveConfig:
    def test_save_and_reload(self, tmp_path):
        config_file = tmp_path / "config.json"
        config = dict(DEFAULT_CONFIG)
        config["asr"] = dict(config["asr"])
        config["asr"]["preset"] = "mlx-whisper-tiny"
        save_config(config, str(config_file))

        assert config_file.exists()
        loaded = load_config(str(config_file))
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

        loaded = load_config(str(config_file))
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
        for backend in ("funasr", "mlx_whisper", "apple", "api"):
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
