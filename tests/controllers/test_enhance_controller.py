"""Tests for EnhanceController."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from voicetext.controllers.enhance_controller import EnhanceController, EnhanceCacheEntry
from voicetext.lru_cache import LRUCache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_enhancer():
    enhancer = MagicMock()
    enhancer.provider_name = "ollama"
    enhancer.model_name = "qwen2.5:7b"
    enhancer.thinking = False
    enhancer.is_active = True
    enhancer.get_mode_definition.return_value = None
    enhancer.last_system_prompt = "system prompt"
    return enhancer


@pytest.fixture
def mock_panel():
    panel = MagicMock()
    panel._thinking_text = ""
    panel._enhance_text_view = MagicMock()
    panel.enhance_request_id = 0
    return panel


@pytest.fixture
def mock_stats():
    return MagicMock()


@pytest.fixture
def controller(mock_enhancer, mock_panel, mock_stats):
    ctrl = EnhanceController(
        enhancer=mock_enhancer,
        preview_panel=mock_panel,
        usage_stats=mock_stats,
        cache_maxsize=10,
    )
    ctrl.enhance_mode = "proofread"
    return ctrl


# ---------------------------------------------------------------------------
# Tests: initialization
# ---------------------------------------------------------------------------


class TestEnhanceControllerInit:
    def test_creates_with_defaults(self, controller):
        assert controller.enhance_mode == "proofread"
        assert isinstance(controller._cache, LRUCache)
        assert controller._cache.maxsize == 10

    def test_enhancer_property(self, controller, mock_enhancer):
        assert controller.enhancer is mock_enhancer

    def test_enhancer_setter(self, controller):
        new_enhancer = MagicMock()
        controller.enhancer = new_enhancer
        assert controller.enhancer is new_enhancer

    def test_none_enhancer(self, mock_panel, mock_stats):
        ctrl = EnhanceController(
            enhancer=None,
            preview_panel=mock_panel,
            usage_stats=mock_stats,
        )
        assert ctrl.enhancer is None


# ---------------------------------------------------------------------------
# Tests: cache operations
# ---------------------------------------------------------------------------


class TestCacheOperations:
    def test_cache_key(self, controller):
        key = controller.cache_key()
        assert key == ("proofread", "ollama", "qwen2.5:7b", False)

    def test_cache_key_changes_with_mode(self, controller):
        key1 = controller.cache_key()
        controller.enhance_mode = "translate"
        key2 = controller.cache_key()
        assert key1 != key2

    def test_cache_key_changes_with_model(self, controller, mock_enhancer):
        key1 = controller.cache_key()
        mock_enhancer.model_name = "llama3:8b"
        key2 = controller.cache_key()
        assert key1 != key2

    def test_cache_key_changes_with_thinking(self, controller, mock_enhancer):
        key1 = controller.cache_key()
        mock_enhancer.thinking = True
        key2 = controller.cache_key()
        assert key1 != key2

    def test_cache_key_no_enhancer(self, mock_panel, mock_stats):
        ctrl = EnhanceController(
            enhancer=None, preview_panel=mock_panel, usage_stats=mock_stats,
        )
        ctrl.enhance_mode = "proofread"
        key = ctrl.cache_key()
        assert key == ("proofread", "", "", False)

    def test_get_cached_miss(self, controller):
        assert controller.get_cached() is None

    def test_get_cached_hit(self, controller):
        entry = EnhanceCacheEntry("text", None, "prompt", "", None)
        controller._cache[controller.cache_key()] = entry
        assert controller.get_cached() is entry

    def test_clear_cache(self, controller):
        entry = EnhanceCacheEntry("text", None, "prompt", "", None)
        controller._cache[controller.cache_key()] = entry
        assert len(controller._cache) == 1
        controller.clear_cache()
        assert len(controller._cache) == 0


# ---------------------------------------------------------------------------
# Tests: cancel
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancel_no_event(self, controller):
        """Cancel when no enhancement is running should not raise."""
        controller.cancel()  # Should not raise

    def test_cancel_sets_event(self, controller):
        event = threading.Event()
        controller._cancel_event = event
        assert not event.is_set()
        controller.cancel()
        assert event.is_set()


# ---------------------------------------------------------------------------
# Tests: run
# ---------------------------------------------------------------------------


class TestRun:
    def test_run_with_none_enhancer(self, mock_panel, mock_stats):
        """Run with no enhancer should be a no-op."""
        ctrl = EnhanceController(
            enhancer=None, preview_panel=mock_panel, usage_stats=mock_stats,
        )
        ctrl.run("text", 1)
        # Should not crash or start any thread

    def test_run_cancels_previous(self, controller):
        """Running a new enhance should cancel the previous one."""
        old_event = threading.Event()
        controller._cancel_event = old_event
        assert not old_event.is_set()

        # Start a new run (will create a thread that we don't wait for)
        controller.run("text", 1)

        # Old event should be cancelled
        assert old_event.is_set()
        # New event should be created
        assert controller._cancel_event is not old_event

    def test_run_creates_cancel_event(self, controller):
        assert controller._cancel_event is None
        controller.run("text", 1)
        assert controller._cancel_event is not None
        assert isinstance(controller._cancel_event, threading.Event)
