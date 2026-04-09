"""Tests for model registry module."""

from pathlib import Path
from unittest.mock import patch

from wenzi.transcription.model_registry import (
    PRESET_BY_ID,
    PRESETS,
    find_fallback_preset,
    get_model_cache_dir,
    is_backend_available,
    is_model_cached,
    resolve_preset_from_config,
)


class TestPresets:
    def test_all_ids_unique(self):
        ids = [p.id for p in PRESETS]
        assert len(ids) == len(set(ids))

    def test_preset_by_id_matches(self):
        for preset in PRESETS:
            assert preset.id in PRESET_BY_ID
            assert PRESET_BY_ID[preset.id] is preset

    def test_preset_by_id_count(self):
        assert len(PRESET_BY_ID) == len(PRESETS)

    def test_all_presets_have_required_fields(self):
        for preset in PRESETS:
            assert preset.id
            assert preset.display_name
            assert preset.backend in ("funasr", "mlx-whisper", "apple", "sherpa-onnx")

    def test_funasr_preset_exists(self):
        assert "funasr-paraformer" in PRESET_BY_ID
        p = PRESET_BY_ID["funasr-paraformer"]
        assert p.backend == "funasr"
        assert p.model is None

    def test_mlx_presets_have_model(self):
        for preset in PRESETS:
            if preset.backend == "mlx-whisper":
                assert preset.model is not None
                assert preset.model.startswith("mlx-community/")

    def test_apple_speech_presets_exist(self):
        assert "apple-speech-ondevice" in PRESET_BY_ID
        assert "apple-speech-server" in PRESET_BY_ID
        p = PRESET_BY_ID["apple-speech-ondevice"]
        assert p.backend == "apple"
        assert p.model == "on-device"
        p2 = PRESET_BY_ID["apple-speech-server"]
        assert p2.model == "server"


    def test_sherpa_presets_exist(self):
        assert "sherpa-zipformer-zh" in PRESET_BY_ID
        assert "sherpa-paraformer-zh" in PRESET_BY_ID
        p = PRESET_BY_ID["sherpa-zipformer-zh"]
        assert p.backend == "sherpa-onnx"
        assert p.model == "zipformer-zh"

    def test_sherpa_presets_have_model(self):
        for preset in PRESETS:
            if preset.backend == "sherpa-onnx":
                assert preset.model is not None
                assert preset.language is not None


class TestResolvePresetFromConfig:
    def test_resolve_funasr_default(self):
        result = resolve_preset_from_config("funasr")
        assert result == "funasr-paraformer"

    def test_resolve_mlx_whisper_large_v3_turbo(self):
        result = resolve_preset_from_config(
            "mlx-whisper", "mlx-community/whisper-large-v3-turbo"
        )
        assert result == "mlx-whisper-large-v3-turbo"

    def test_resolve_mlx_whisper_medium(self):
        result = resolve_preset_from_config(
            "mlx-whisper", "mlx-community/whisper-medium"
        )
        assert result == "mlx-whisper-medium"

    def test_resolve_unknown_model(self):
        result = resolve_preset_from_config("mlx-whisper", "some/unknown-model")
        assert result is None

    def test_resolve_unknown_backend(self):
        result = resolve_preset_from_config("unknown-backend")
        assert result is None

    def test_resolve_normalizes_backend(self):
        result = resolve_preset_from_config("MLX_Whisper", "mlx-community/whisper-medium")
        assert result == "mlx-whisper-medium"

    def test_resolve_apple_speech_ondevice(self):
        result = resolve_preset_from_config("apple", "on-device")
        assert result == "apple-speech-ondevice"

    def test_resolve_apple_speech_server(self):
        result = resolve_preset_from_config("apple", "server")
        assert result == "apple-speech-server"

    def test_resolve_sherpa_zipformer(self):
        result = resolve_preset_from_config("sherpa-onnx", "zipformer-zh")
        assert result == "sherpa-zipformer-zh"

    def test_resolve_sherpa_paraformer(self):
        result = resolve_preset_from_config("sherpa-onnx", "paraformer-zh")
        assert result == "sherpa-paraformer-zh"


class TestGetModelCacheDir:
    def test_sherpa_cache_dir(self):
        preset = PRESET_BY_ID["sherpa-zipformer-zh"]
        cache_dir = get_model_cache_dir(preset)
        assert isinstance(cache_dir, Path)
        assert "sherpa-onnx-models" in str(cache_dir)


class TestIsBackendAvailable:
    def test_funasr_available(self):
        # funasr_onnx should be installed in test env
        assert is_backend_available("funasr") is True

    def test_unknown_backend_not_available(self):
        assert is_backend_available("nonexistent") is False

    def test_result_is_cached(self):
        # Call twice to exercise caching
        r1 = is_backend_available("funasr")
        r2 = is_backend_available("funasr")
        assert r1 == r2


class TestFindFallbackPreset:
    def test_skips_apple_presets(self):
        with patch(
            "wenzi.transcription.model_registry.is_model_cached",
            return_value=False,
        ):
            result = find_fallback_preset()
        if result is not None:
            assert result.backend != "apple"

    def test_returns_none_when_nothing_available(self):
        with patch(
            "wenzi.transcription.model_registry.is_backend_available",
            return_value=False,
        ):
            assert find_fallback_preset() is None

    def test_prefers_cached_model(self):
        def _available(backend):
            return backend in ("funasr", "mlx-whisper")

        def _cached(preset):
            return preset.backend == "mlx-whisper"

        with (
            patch(
                "wenzi.transcription.model_registry.is_backend_available",
                side_effect=_available,
            ),
            patch(
                "wenzi.transcription.model_registry.is_model_cached",
                side_effect=_cached,
            ),
        ):
            result = find_fallback_preset()
            assert result is not None
            assert result.backend == "mlx-whisper"

    def test_falls_back_to_available_if_none_cached(self):
        def _available(backend):
            return backend == "funasr"

        with (
            patch(
                "wenzi.transcription.model_registry.is_backend_available",
                side_effect=_available,
            ),
            patch(
                "wenzi.transcription.model_registry.is_model_cached",
                return_value=False,
            ),
        ):
            result = find_fallback_preset()
            assert result is not None
            assert result.backend == "funasr"


class TestIsModelCached:
    def test_apple_speech_always_cached(self):
        for preset_id in ("apple-speech-ondevice", "apple-speech-server"):
            preset = PRESET_BY_ID[preset_id]
            assert is_model_cached(preset) is True
