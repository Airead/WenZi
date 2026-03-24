"""Async demo commands — one command per async feature."""

from __future__ import annotations

import asyncio
import logging
import random
import time

_SOUNDS = ["Glass", "Ping", "Hero", "Purr", "Pop", "Frog", "Funk", "Tink"]

_CONCURRENT_DELAYS = (1.0, 1.5, 0.5)
_ERROR_DELAY = 0.5
_RUN_DELAY = 1.0
_PICK_DELAY = 1.0
_SOURCE_DELAY = 0.5

logger = logging.getLogger(__name__)


def _rand_sound() -> str:
    return random.choice(_SOUNDS)


def register(wz):
    """Register all async demo commands in the launcher."""

    @wz.chooser.command("async-sleep", title="Async Sleep", subtitle="async def callback with await asyncio.sleep")
    async def cmd_sleep(args):
        s = args.strip()
        seconds = float(s) if s else 3.0
        remaining = seconds
        while remaining > 0:
            wz.alert(f"Sleeping... {remaining:.1f}s", duration=1.5)
            step = min(0.1, remaining)
            await asyncio.sleep(step)
            remaining -= step
        wz.alert(f"Woke up after {seconds}s!", duration=2.0)
        wz.notify("Async Sleep", f"Woke up after {seconds}s!", sound=_rand_sound())

    async def _fetch_url(url: str):
        import subprocess
        import tempfile
        import urllib.request

        wz.alert(f"Fetching {url}...", duration=10.0)
        loop = asyncio.get_running_loop()
        start = time.monotonic()
        resp = await loop.run_in_executor(
            None, urllib.request.urlopen, url,
        )
        elapsed = time.monotonic() - start
        status = resp.status
        body = resp.read()
        msg = f"HTTP {status}, {len(body)} bytes in {elapsed:.2f}s"
        wz.alert(msg, duration=3.0)
        wz.notify("Async Fetch", msg, sound=_rand_sound())

        content_type = resp.headers.get("Content-Type", "")
        ext = ".json" if "json" in content_type else ".html" if "html" in content_type else ".txt"
        with tempfile.NamedTemporaryFile(suffix=ext, prefix="async-fetch-", delete=False) as f:
            f.write(body)
            tmp_path = f.name
        await loop.run_in_executor(None, subprocess.run, ["open", "-e", tmp_path])

    wz.chooser.register_command(
        name="async-fetch",
        title="Async Fetch",
        subtitle="lambda returning coroutine (HTTP GET)",
        action=lambda args: _fetch_url(
            args.strip() or "https://httpbin.org/get"
        ),
    )

    _timer_state = {"id": None, "count": 0}
    _STOP_HOTKEY = "ctrl+cmd+t"

    def _stop_timer():
        if _timer_state["id"] is None:
            return
        wz.timer.cancel(_timer_state["id"])
        wz.hotkey.unbind(_STOP_HOTKEY)
        wz.notify("Async Timer", f"Stopped after {_timer_state['count']} ticks", sound=_rand_sound())
        _timer_state["id"] = None
        _timer_state["count"] = 0

    @wz.chooser.command("async-timer", title="Async Timer", subtitle="Start/stop an async repeating timer")
    async def cmd_timer(args):
        if _timer_state["id"] is not None:
            _stop_timer()
            return

        _timer_state["count"] = 0

        async def tick():
            _timer_state["count"] += 1
            n = _timer_state["count"]
            wz.alert(f"Tick #{n} — {_STOP_HOTKEY} to stop", duration=1.5)
            logger.info("Async timer tick #%d", n)

        _timer_state["id"] = wz.timer.every(2.0, tick)
        wz.hotkey.bind(_STOP_HOTKEY, _stop_timer)
        wz.notify("Async Timer", f"Started — {_STOP_HOTKEY} to stop", sound=_rand_sound())

    @wz.on("transcription_done")
    async def on_transcription(data):
        text = data.get("asr_text", "")
        await asyncio.sleep(0.1)
        logger.info("[async-demo] transcription_done: %s", text[:50])
        wz.notify("Async Event", f"transcription_done: {text[:50]}", sound=None)



    @wz.chooser.command("async-concurrent", title="Async Concurrent", subtitle="Run multiple async tasks in parallel")
    async def cmd_concurrent(args):
        wz.alert("Running 3 tasks concurrently...", duration=3.0)

        async def task(name: str, delay: float) -> str:
            await asyncio.sleep(delay)
            return f"{name} done in {delay}s"

        start = time.monotonic()
        results = await asyncio.gather(
            task("A", _CONCURRENT_DELAYS[0]),
            task("B", _CONCURRENT_DELAYS[1]),
            task("C", _CONCURRENT_DELAYS[2]),
        )
        elapsed = time.monotonic() - start
        wz.notify(
            "Async Concurrent",
            f"All done in {elapsed:.1f}s: {', '.join(results)}",
            sound=_rand_sound(),
        )

    @wz.chooser.command("async-error", title="Async Error", subtitle="Raise an exception — check log for error output")
    async def cmd_error(args):
        wz.alert(f"Raising error in {_ERROR_DELAY}s...", duration=2.0)
        await asyncio.sleep(_ERROR_DELAY)
        raise RuntimeError("Intentional async error from async-demo plugin")

    @wz.chooser.command("async-run", title="Async Run", subtitle="Submit a coroutine via wz.run()")
    def cmd_run(args):
        async def background_work():
            wz.alert("wz.run() started...", duration=2.0)
            await asyncio.sleep(_RUN_DELAY)
            wz.notify("Async Run", "wz.run() coroutine completed!", sound=_rand_sound())

        wz.run(background_work())

    @wz.chooser.command("async-pick", title="Async Pick", subtitle="Async callback on chooser.pick() selection")
    def cmd_pick(args):
        items = [
            {"title": "Option A", "subtitle": "First choice"},
            {"title": "Option B", "subtitle": "Second choice"},
            {"title": "Option C", "subtitle": "Third choice"},
        ]

        async def on_picked(item):
            if item is None:
                wz.alert("Pick dismissed", duration=1.5)
                return
            title = item.get("title", "?")
            wz.alert(f"Processing '{title}'...", duration=2.0)
            await asyncio.sleep(_PICK_DELAY)
            wz.notify("Async Pick", f"You picked: {title}", sound=_rand_sound())

        wz.chooser.pick(items, callback=on_picked, placeholder="Pick an option...")

    # ------------------------------------------------------------------
    # Async source — demonstrates async def in @wz.chooser.source()
    # ------------------------------------------------------------------

    @wz.chooser.source(
        "async-search",
        prefix="as",
        description="Async search demo (simulates network delay)",
        search_timeout=3.0,
    )
    async def async_source_search(query):
        """Simulate an async source that fetches results from a remote API."""
        if not query.strip():
            return [
                {
                    "title": "Type to search (async)...",
                    "subtitle": "Results load after 0.5s simulated delay",
                },
            ]
        await asyncio.sleep(_SOURCE_DELAY)  # Simulate network latency
        return [
            {
                "title": f"Async result: {query}",
                "subtitle": "Found after 0.5s delay",
            },
            {
                "title": f"Async result: {query.upper()}",
                "subtitle": "Uppercase variant",
            },
        ]

    logger.info("Async demo plugin loaded")
