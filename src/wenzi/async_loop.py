"""Singleton asyncio event loop running on a dedicated daemon thread.

All async work (LLM streaming, vocabulary building, provider verification)
is submitted to this shared loop via :func:`submit`.
The loop is created lazily on first access and runs until :func:`shutdown`
is called (typically during app quit).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None


def get_loop() -> asyncio.AbstractEventLoop:
    """Return the shared event loop, starting the background thread on first call.

    Thread-safe — may be called from any thread.
    """
    global _loop, _thread
    # Fast path (GIL-protected read).
    if _loop is not None and _loop.is_running():
        return _loop
    with _lock:
        if _loop is not None and _loop.is_running():
            return _loop
        loop = asyncio.new_event_loop()
        _loop = loop
        started = threading.Event()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            # Signal *after* run_forever starts so is_running() is True
            # for any concurrent get_loop() caller.
            loop.call_soon(started.set)
            loop.run_forever()

        _thread = threading.Thread(target=_run, daemon=True, name="wenzi-asyncio")
        _thread.start()
        if not started.wait(timeout=5.0):
            raise RuntimeError("Asyncio event loop failed to start within 5s")
        return loop


def submit[T](coro: Coroutine[Any, Any, T]) -> asyncio.Future[T]:
    """Submit a coroutine to the shared loop (thread-safe).

    Returns a :class:`concurrent.futures.Future` that can be used to
    retrieve the result from a synchronous context.
    """
    return asyncio.run_coroutine_threadsafe(coro, get_loop())


def shutdown_sync(timeout: float = 5.0) -> None:
    """Gracefully cancel all pending tasks and stop the loop.

    Call from any thread (typically the main thread during app quit).
    Blocks until the loop has stopped or *timeout* seconds elapse.
    """
    global _loop, _thread
    _thread_ref = None
    with _lock:
        loop = _loop
        if loop is None or not loop.is_running():
            return
        _loop = None
        _thread_ref = _thread
        _thread = None

    async def _cleanup() -> None:
        tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await loop.shutdown_asyncgens()
        await loop.shutdown_default_executor()

    future = asyncio.run_coroutine_threadsafe(_cleanup(), loop)
    try:
        future.result(timeout=timeout)
    except Exception:
        logger.warning("Shutdown cleanup error", exc_info=True)
    loop.call_soon_threadsafe(loop.stop)
    if _thread_ref is not None:
        _thread_ref.join(timeout=timeout)
