"""Async callback utilities for the scripting API.

Provides transparent async/await support: user callbacks defined with
``async def`` (or lambdas returning coroutines) are automatically
submitted to the shared asyncio event loop.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import threading
from collections.abc import Callable
from typing import Any

import wenzi.async_loop as _aloop

logger = logging.getLogger(__name__)


# ── Task tracker ──────────────────────────────────────────────────

class ScriptTaskTracker:
    """Track asyncio tasks spawned by user scripts.

    On ``reload()``, all tracked tasks are cancelled so stale coroutines
    do not outlive the script lifecycle.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._lock = threading.Lock()

    def track(self, task: asyncio.Task) -> None:
        """Start tracking an asyncio task."""
        with self._lock:
            self._tasks.add(task)
        task.add_done_callback(self._on_done)

    def _on_done(self, task: asyncio.Task) -> None:
        with self._lock:
            self._tasks.discard(task)

    def cancel_all(self, grace_timeout: float = 2.0) -> None:
        """Cancel all tracked tasks with a grace period for cleanup."""
        with self._lock:
            tasks = list(self._tasks)
            self._tasks.clear()
        if not tasks:
            return
        logger.info("Cancelling %d script async task(s)", len(tasks))
        for t in tasks:
            t.cancel()
        # Wait for tasks to handle CancelledError / run finally blocks.
        try:
            loop = _aloop.get_loop()
        except RuntimeError:
            return
        try:
            async def _wait():
                await asyncio.gather(*tasks, return_exceptions=True)

            future = asyncio.run_coroutine_threadsafe(_wait(), loop)
            future.result(timeout=grace_timeout)
        except Exception:
            logger.debug("Grace-period wait interrupted", exc_info=True)


# Module-level singleton
_tracker = ScriptTaskTracker()


def get_tracker() -> ScriptTaskTracker:
    """Return the global script task tracker."""
    return _tracker


# ── Submit + log ──────────────────────────────────────────────────

def _log_future_exception(fut: Any) -> None:
    """Log unhandled exceptions from a concurrent.futures.Future."""
    try:
        exc = fut.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.error(
            "Unhandled exception in async script callback: %s", exc,
            exc_info=exc,
        )


def _submit_and_log(coro: Any) -> None:
    """Submit a coroutine to the shared loop and log unhandled exceptions."""
    try:
        loop = _aloop.get_loop()
    except RuntimeError:
        logger.error("Async loop unavailable, cannot run coroutine")
        coro.close()
        return

    async def _tracked():
        task = asyncio.current_task()
        if task is not None:
            _tracker.track(task)
        return await coro

    future = asyncio.run_coroutine_threadsafe(_tracked(), loop)
    future.add_done_callback(_log_future_exception)


# ── wrap_async ────────────────────────────────────────────────────

def wrap_async(callback: Callable) -> Callable:
    """Wrap a callback so async functions run on the shared event loop.

    - If *callback* is an ``async def``, returns a sync wrapper that
      submits the coroutine when called.
    - Otherwise, returns a wrapper that calls the original and checks
      whether the return value is a coroutine (handles the
      ``lambda: my_async_fn()`` pattern).

    The wrapper preserves ``__name__`` and ``__doc__`` of the original.
    """
    if asyncio.iscoroutinefunction(callback):
        @functools.wraps(callback)
        def _async_wrapper(*args: Any, **kwargs: Any) -> None:
            _submit_and_log(callback(*args, **kwargs))
        return _async_wrapper

    @functools.wraps(callback)
    def _maybe_async_wrapper(*args: Any, **kwargs: Any) -> Any:
        result = callback(*args, **kwargs)
        if asyncio.iscoroutine(result):
            _submit_and_log(result)
            return None
        return result

    return _maybe_async_wrapper
