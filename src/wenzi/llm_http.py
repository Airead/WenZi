"""Lightweight OpenAI-compatible HTTP client using only Python stdlib.

Provides :class:`ChatClient` (async, for chat completions) and
:class:`TranscriptionClient` (sync, for audio transcription) without
depending on the ``openai`` package or any third-party HTTP library.

All HTTP is done via :mod:`http.client`; async operations run blocking
I/O in :func:`asyncio.get_running_loop().run_in_executor`.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import ssl
import threading
import uuid
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class APIError(Exception):
    """Non-success HTTP response from an OpenAI-compatible API."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class RateLimitError(APIError):
    """HTTP 429 — rate limited by the API provider."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_ssl_ctx: ssl.SSLContext | None = None
_ssl_lock = threading.Lock()


def _get_ssl_context() -> ssl.SSLContext:
    """Return a cached default SSL context (respects SSL_CERT_FILE env)."""
    global _ssl_ctx  # noqa: PLW0603
    if _ssl_ctx is None:
        with _ssl_lock:
            if _ssl_ctx is None:
                _ssl_ctx = ssl.create_default_context()
    return _ssl_ctx


def _connect(
    base_url: str, timeout: float = 30.0,
) -> tuple[http.client.HTTPConnection, str]:
    """Open an HTTP(S) connection and return ``(conn, path_prefix)``.

    *path_prefix* is the path portion of *base_url* (e.g. ``/v1``).
    """
    parsed = urlparse(base_url)
    host = parsed.hostname or "localhost"
    path_prefix = (parsed.path or "").rstrip("/")

    if parsed.scheme == "https":
        port = parsed.port or 443
        conn = http.client.HTTPSConnection(
            host, port, context=_get_ssl_context(), timeout=timeout,
        )
    else:
        port = parsed.port or 80
        conn = http.client.HTTPConnection(host, port, timeout=timeout)

    return conn, path_prefix


def _read_error(response: http.client.HTTPResponse) -> str:
    """Read the response body and return a human-readable error string."""
    try:
        raw = response.read()
        text = raw.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
            err = data.get("error", {})
            if isinstance(err, dict):
                return err.get("message", text)
            return str(err) or text
        except (json.JSONDecodeError, AttributeError):
            return text
    except Exception:
        return f"HTTP {response.status}"


def _raise_for_status(response: http.client.HTTPResponse) -> None:
    """Raise :class:`APIError` or :class:`RateLimitError` on non-2xx."""
    if 200 <= response.status < 300:
        return
    msg = _read_error(response)
    if response.status == 429:
        raise RateLimitError(msg, status_code=429, body=msg)
    raise APIError(msg, status_code=response.status, body=msg)


# ---------------------------------------------------------------------------
# Multipart form-data encoder (for audio transcription)
# ---------------------------------------------------------------------------


def _encode_multipart(
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes, str]],
) -> tuple[bytes, str]:
    """Build a ``multipart/form-data`` body.

    Parameters
    ----------
    fields:
        ``{name: value}`` text fields.
    files:
        ``{name: (filename, data, content_type)}`` binary file fields.

    Returns
    -------
    (body_bytes, content_type_header)
    """
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []

    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            f"\r\n"
            f"{value}\r\n".encode()
        )

    for name, (filename, data, ctype) in files.items():
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n"
            f"\r\n"
        ).encode()
        parts.append(header + data + b"\r\n")

    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


# ---------------------------------------------------------------------------
# SSEStream — async iterator over server-sent events
# ---------------------------------------------------------------------------


class SSEStream:
    """Async iterator that reads SSE ``data:`` lines from an HTTP response.

    Each :meth:`__anext__` call runs a blocking ``readline()`` in the
    default thread-pool executor, so it never blocks the event loop.
    """

    def __init__(
        self,
        conn: http.client.HTTPConnection,
        response: http.client.HTTPResponse,
    ) -> None:
        self._conn = conn
        self._response = response
        self._done = False
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- async iterator protocol -------------------------------------------

    def __aiter__(self) -> SSEStream:
        return self

    async def __anext__(self) -> dict[str, Any]:
        if self._done:
            raise StopAsyncIteration
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        chunk = await self._loop.run_in_executor(None, self._read_next)
        if chunk is None:
            raise StopAsyncIteration
        return chunk

    # -- blocking reader (runs in executor) --------------------------------

    def _read_next(self) -> dict[str, Any] | None:
        """Read lines until the next ``data: {...}`` event."""
        while True:
            try:
                raw = self._response.readline()
            except Exception:
                self._done = True
                return None
            if not raw:
                self._done = True
                return None
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue  # empty line (SSE event separator)
            if line.startswith("data:"):
                data = line[5:].strip()
                if data == "[DONE]":
                    self._done = True
                    return None
                try:
                    return json.loads(data)
                except json.JSONDecodeError:
                    logger.debug("SSE: failed to parse data line: %s", line)
                    continue
            # Other SSE fields (event:, id:, retry:) — skip

    # -- cleanup -----------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP connection (safe to call multiple times)."""
        self._done = True
        loop = self._loop or asyncio.get_running_loop()
        await loop.run_in_executor(None, self._close_sync)

    def _close_sync(self) -> None:
        try:
            self._response.close()
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ChatClient — async client for /chat/completions
# ---------------------------------------------------------------------------


class ChatClient:
    """Async HTTP client for the ``/chat/completions`` endpoint.

    Drop-in replacement for ``openai.AsyncOpenAI`` — stores *base_url*
    and *api_key*, uses per-request ``http.client`` connections.
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self.api_key = api_key

    async def create(self, **kwargs: Any) -> dict[str, Any] | SSEStream:
        """Send a chat-completion request.

        Accepts the same keyword arguments as the OpenAI SDK's
        ``chat.completions.create``:

        - **model**, **messages**, **max_tokens** (required)
        - **stream** (bool) — if True, returns an :class:`SSEStream`
        - **stream_options** — e.g. ``{"include_usage": True}``
        - **extra_body** — merged into the top-level JSON body

        For non-streaming requests the call is run in a thread executor
        so it never blocks the event loop.
        """
        stream = kwargs.get("stream", False)
        if stream:
            return await self._create_stream(**kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._create_sync, kwargs)

    # -- non-streaming (blocking, called via executor) ---------------------

    def _create_sync(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        conn, resp = self._send_request(kwargs)
        try:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise APIError(
                    f"Invalid JSON in response: {raw[:200]}",
                    status_code=resp.status,
                    body=raw,
                ) from exc
        finally:
            conn.close()

    # -- streaming ---------------------------------------------------------

    async def _create_stream(self, **kwargs: Any) -> SSEStream:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._open_stream, kwargs,
        )

    def _open_stream(self, kwargs: dict[str, Any]) -> SSEStream:
        """Blocking: open connection, send request, return SSEStream."""
        conn, resp = self._send_request(kwargs)
        return SSEStream(conn, resp)

    # -- helpers -----------------------------------------------------------

    def _send_request(
        self, kwargs: dict[str, Any],
    ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        """Build body, open connection, POST, check status. Return (conn, resp).

        On error the connection is closed before re-raising.
        """
        kw = dict(kwargs)
        extra = kw.pop("extra_body", None)
        if extra:
            kw.update(extra)
        conn, prefix = _connect(self.base_url)
        try:
            payload = json.dumps(kw).encode()
            conn.request(
                "POST",
                f"{prefix}/chat/completions",
                body=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Length": str(len(payload)),
                },
            )
            resp = conn.getresponse()
            _raise_for_status(resp)
            return conn, resp
        except Exception:
            conn.close()
            raise

    # -- lifecycle ---------------------------------------------------------

    async def close(self) -> None:
        """No-op — connections are per-request."""


# ---------------------------------------------------------------------------
# TranscriptionClient — sync client for /audio/transcriptions
# ---------------------------------------------------------------------------


class TranscriptionClient:
    """Synchronous HTTP client for the ``/audio/transcriptions`` endpoint.

    Drop-in replacement for ``openai.OpenAI`` in transcription use-cases.
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self.api_key = api_key

    def create(self, **kwargs: Any) -> str:
        """Send an audio transcription request.

        Parameters (passed as keyword arguments)
        -----------------------------------------
        model : str
        file : io.BytesIO (must have a ``.name`` attribute)
        temperature : float, optional
        language : str, optional
        prompt : str, optional

        Returns the transcribed text string.
        """
        fields: dict[str, str] = {}
        if "model" in kwargs:
            fields["model"] = kwargs["model"]
        if "temperature" in kwargs:
            fields["temperature"] = str(kwargs["temperature"])
        if "language" in kwargs:
            fields["language"] = kwargs["language"]
        if "prompt" in kwargs:
            fields["prompt"] = kwargs["prompt"]

        file_obj = kwargs["file"]
        filename = getattr(file_obj, "name", "audio.wav")
        file_data = file_obj.read()

        body, content_type = _encode_multipart(
            fields=fields,
            files={"file": (filename, file_data, "application/octet-stream")},
        )

        conn, prefix = _connect(self.base_url)
        try:
            conn.request(
                "POST",
                f"{prefix}/audio/transcriptions",
                body=body,
                headers={
                    "Content-Type": content_type,
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Length": str(len(body)),
                },
            )
            resp = conn.getresponse()
            _raise_for_status(resp)
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise APIError(
                    f"Invalid JSON in response: {raw[:200]}",
                    status_code=resp.status,
                    body=raw,
                ) from exc
            return data.get("text", "")
        finally:
            conn.close()

    def close(self) -> None:
        """No-op — connections are per-request."""
