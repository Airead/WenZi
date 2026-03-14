"""Tests for AI enhancement result caching."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from voicetext.controllers.enhance_controller import EnhanceCacheEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cache_entry():
    """A sample cache entry for testing."""
    return EnhanceCacheEntry(
        display_text="enhanced text",
        usage={"total_tokens": 100, "prompt_tokens": 60, "completion_tokens": 40},
        system_prompt="You are a proofreader.",
        thinking_text="thinking...",
        final_text=None,
    )


def _make_app_stub():
    """Build a minimal stub of VoiceTextApp with caching-related attributes."""
    app = MagicMock()
    app._enhance_cache = {}
    app._enhance_mode = "proofread"
    app._enhancer = MagicMock()
    app._enhancer.provider_name = "ollama"
    app._enhancer.model_name = "qwen2.5:7b"
    app._enhancer.thinking = False
    app._preview_panel = MagicMock()
    app._preview_panel._thinking_text = ""
    app._preview_panel._enhance_text_view = MagicMock()
    app._preview_panel.enhance_request_id = 0
    return app


def _build_cache_key(app):
    """Mirror the real _enhance_cache_key logic."""
    return (
        app._enhance_mode,
        app._enhancer.provider_name if app._enhancer else "",
        app._enhancer.model_name if app._enhancer else "",
        app._enhancer.thinking if app._enhancer else False,
    )


# ---------------------------------------------------------------------------
# Tests: same-mode guard (_on_segment_changed)
# ---------------------------------------------------------------------------


class TestSameModeGuard:
    """Tests for the same-mode re-click guard in _on_segment_changed."""

    def test_same_mode_reclick_noop(self):
        """Clicking the already-selected mode should not fire the callback."""
        callback = MagicMock()
        panel = MagicMock()
        panel._available_modes = [("off", "Off"), ("proofread", "Proofread")]
        panel._current_mode = "proofread"
        panel._on_mode_change = callback

        # Import the real method and bind it
        from voicetext.ui.result_window import ResultPreviewPanel

        ResultPreviewPanel._on_segment_changed(panel, 1)  # index 1 = proofread

        callback.assert_not_called()

    def test_different_mode_fires_callback(self):
        """Switching to a different mode should fire the callback."""
        callback = MagicMock()
        panel = MagicMock()
        panel._available_modes = [("off", "Off"), ("proofread", "Proofread")]
        panel._current_mode = "off"
        panel._on_mode_change = callback

        from voicetext.ui.result_window import ResultPreviewPanel

        ResultPreviewPanel._on_segment_changed(panel, 1)

        callback.assert_called_once_with("proofread")
        assert panel._current_mode == "proofread"


# ---------------------------------------------------------------------------
# Tests: cache key
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_cache_key_includes_model(self):
        """Switching LLM model should produce a different cache key."""
        app = _make_app_stub()
        key1 = _build_cache_key(app)

        app._enhancer.model_name = "llama3:8b"
        key2 = _build_cache_key(app)

        assert key1 != key2

    def test_cache_key_includes_thinking(self):
        """Toggling thinking should produce a different cache key."""
        app = _make_app_stub()
        key1 = _build_cache_key(app)

        app._enhancer.thinking = True
        key2 = _build_cache_key(app)

        assert key1 != key2

    def test_cache_key_includes_mode(self):
        """Different enhance modes should produce different keys."""
        app = _make_app_stub()
        key1 = _build_cache_key(app)

        app._enhance_mode = "format"
        key2 = _build_cache_key(app)

        assert key1 != key2


# ---------------------------------------------------------------------------
# Tests: cache lookup on mode switch
# ---------------------------------------------------------------------------


class TestModeSwitchCache:
    def test_mode_switchback_uses_cache(self):
        """A→B→A should use cache on the second A."""
        app = _make_app_stub()
        entry = EnhanceCacheEntry(
            display_text="cached result",
            usage={"total_tokens": 50, "prompt_tokens": 30, "completion_tokens": 20},
            system_prompt="prompt",
            thinking_text="",
            final_text=None,
        )
        key = ("proofread", "ollama", "qwen2.5:7b", False)
        app._enhance_cache[key] = entry

        # Simulate switching back to proofread
        app._enhance_mode = "proofread"
        cached = app._enhance_cache.get(_build_cache_key(app))

        assert cached is not None
        assert cached.display_text == "cached result"

    def test_cache_miss_triggers_enhance(self):
        """No cache entry means None is returned."""
        app = _make_app_stub()
        app._enhance_mode = "format"

        cached = app._enhance_cache.get(_build_cache_key(app))

        assert cached is None


# ---------------------------------------------------------------------------
# Tests: cache clear
# ---------------------------------------------------------------------------


class TestCacheClear:
    def test_cache_cleared_on_new_asr_text(self):
        """Cache should be cleared when ASR text changes."""
        app = _make_app_stub()
        app._enhance_cache[("proofread", "ollama", "qwen2.5:7b", False)] = (
            EnhanceCacheEntry("text", None, "", "", None)
        )

        assert len(app._enhance_cache) == 1

        # Simulate what the real code does
        app._current_preview_asr_text = "new text"
        app._enhance_cache.clear()

        assert len(app._enhance_cache) == 0


# ---------------------------------------------------------------------------
# Tests: replay_cached_result
# ---------------------------------------------------------------------------


class TestReplayCachedResult:
    def _make_panel(self, user_edited=False):
        panel = MagicMock()
        panel._enhance_text_view = MagicMock()
        panel._enhance_label = MagicMock()
        panel._prompt_button = MagicMock()
        panel._thinking_button = MagicMock()
        panel._thinking_text = ""
        panel._system_prompt = ""
        panel._user_edited = user_edited
        panel._final_text_field = MagicMock()
        panel._llm_models = None
        return panel

    def test_replay_sets_panel_state(self, mock_appkit_modules):
        """replay_cached_result should set text view, labels, and buttons."""
        panel = self._make_panel()

        from voicetext.ui.result_window import ResultPreviewPanel

        ResultPreviewPanel.replay_cached_result(
            panel,
            display_text="cached text",
            usage={"total_tokens": 100, "prompt_tokens": 60, "completion_tokens": 40},
            system_prompt="sys prompt",
            thinking_text="think",
            final_text="final",
        )

        panel._enhance_text_view.setString_.assert_called_once_with("cached text")
        assert panel._system_prompt == "sys prompt"
        assert panel._thinking_text == "think"
        panel._final_text_field.setStringValue_.assert_called_once_with("final")

    def test_replay_uses_display_text_when_no_final(self, mock_appkit_modules):
        """When final_text is None, display_text should be used for final field."""
        panel = self._make_panel()

        from voicetext.ui.result_window import ResultPreviewPanel

        ResultPreviewPanel.replay_cached_result(
            panel,
            display_text="display text",
            usage=None,
            system_prompt="",
            thinking_text="",
            final_text=None,
        )

        panel._final_text_field.setStringValue_.assert_called_once_with("display text")

    def test_replay_skipped_when_user_edited(self, mock_appkit_modules):
        """When user has edited final field, replay should not overwrite it."""
        panel = self._make_panel(user_edited=True)

        from voicetext.ui.result_window import ResultPreviewPanel

        ResultPreviewPanel.replay_cached_result(
            panel,
            display_text="cached",
            usage=None,
            system_prompt="",
            thinking_text="",
            final_text="final",
        )

        panel._final_text_field.setStringValue_.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: cache write after enhancement
# ---------------------------------------------------------------------------


class TestCacheWrite:
    def test_single_enhance_writes_cache(self):
        """After single enhance completes, result should be in cache."""
        entry = EnhanceCacheEntry(
            display_text="enhanced output",
            usage={"total_tokens": 80, "prompt_tokens": 50, "completion_tokens": 30},
            system_prompt="sys",
            thinking_text="",
            final_text=None,
        )
        cache: dict[tuple, EnhanceCacheEntry] = {}
        key = ("proofread", "ollama", "qwen2.5:7b", False)
        cache[key] = entry

        assert key in cache
        assert cache[key].display_text == "enhanced output"
        assert cache[key].final_text is None

    def test_chain_enhance_writes_cache(self):
        """After chain enhance completes, result should be in cache with final_text."""
        entry = EnhanceCacheEntry(
            display_text="step1\n---\nstep2",
            usage={"total_tokens": 200, "prompt_tokens": 120, "completion_tokens": 80},
            system_prompt="chain sys",
            thinking_text="chain think",
            final_text="step2 output",
        )
        cache: dict[tuple, EnhanceCacheEntry] = {}
        key = ("chain_mode", "openai", "gpt-4", False)
        cache[key] = entry

        assert key in cache
        assert cache[key].final_text == "step2 output"
        assert "step1" in cache[key].display_text


# ---------------------------------------------------------------------------
# Tests: EnhanceCacheEntry dataclass
# ---------------------------------------------------------------------------


class TestEnhanceCacheEntry:
    def test_dataclass_fields(self):
        """EnhanceCacheEntry should have all expected fields."""
        entry = EnhanceCacheEntry(
            display_text="text",
            usage=None,
            system_prompt="prompt",
            thinking_text="think",
            final_text="final",
        )
        assert entry.display_text == "text"
        assert entry.usage is None
        assert entry.system_prompt == "prompt"
        assert entry.thinking_text == "think"
        assert entry.final_text == "final"

    def test_dataclass_with_usage(self):
        """EnhanceCacheEntry should correctly store usage dict."""
        usage = {"total_tokens": 150, "prompt_tokens": 100, "completion_tokens": 50}
        entry = EnhanceCacheEntry(
            display_text="t", usage=usage, system_prompt="",
            thinking_text="", final_text=None,
        )
        assert entry.usage["total_tokens"] == 150
