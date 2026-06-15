# terminalghost.llm.backends.ollama
#
# Concrete LLMBackend for the Ollama local HTTP API (default backend —
# no API key, fully offline). Endpoints used:
#   POST /api/generate  — generation (stream and non-stream)
#   GET  /api/tags      — model listing (used by is_available)

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Awaitable, Callable

import httpx

from terminalghost.llm.base import (
    LLMBackend,
    LLMConnectionError,
    LLMResponseError,
    LLMTimeoutError,
)

log = logging.getLogger(__name__)

_NOT_RUNNING_MSG = "Ollama is not running. Start it with: ollama serve"


class OllamaBackend(LLMBackend):
    """LLM backend talking to a locally running Ollama instance."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3",
        timeout: float = 60.0,
        retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._retries = max(0, retries)
        # Injectable transport so tests can use httpx.MockTransport.
        self._transport = transport
        # Short cache for is_available() so rapid ?? don't each pay an HTTP probe.
        self._avail_cache: tuple[float, bool] | None = None
        self._avail_ttl = 5.0

    # -- public API ----------------------------------------------------------

    async def query(self, prompt: str) -> str:
        body = self._build_request_body(prompt, stream=False)

        async def attempt() -> str:
            try:
                async with self._client() as client:
                    response = await client.post("/api/generate", json=body)
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(
                    f"Ollama request timed out after {self._timeout}s"
                ) from exc
            except httpx.ConnectError as exc:
                raise LLMConnectionError(_NOT_RUNNING_MSG) from exc
            if response.status_code != 200:
                self._handle_http_error(response)
            try:
                return response.json()["response"]
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                raise LLMResponseError(f"malformed Ollama response: {exc}") from exc

        return await self._with_retry(attempt)

    async def stream_query(self, prompt: str) -> AsyncIterator[str]:
        body = self._build_request_body(prompt, stream=True)
        attempt = 0
        yielded = False
        while True:
            try:
                async with self._client() as client:
                    async with client.stream("POST", "/api/generate", json=body) as response:
                        if response.status_code != 200:
                            await response.aread()
                            self._handle_http_error(response)
                        buffer = ""
                        async for chunk in response.aiter_text():
                            buffer += chunk
                            # A chunk may contain several JSON lines or a split one;
                            # only complete (newline-terminated) lines are parsed.
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                if not line.strip():
                                    continue
                                try:
                                    obj = json.loads(line)
                                except json.JSONDecodeError as exc:
                                    raise LLMResponseError(
                                        f"malformed Ollama stream line: {exc}"
                                    ) from exc
                                piece = obj.get("response", "")
                                if piece:
                                    yielded = True
                                    yield piece
                                if obj.get("done"):
                                    return
                return
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(
                    f"Ollama request timed out after {self._timeout}s"
                ) from exc
            except httpx.ConnectError as exc:
                # Never retry mid-stream — that would duplicate output.
                attempt += 1
                if yielded or attempt > self._retries:
                    raise LLMConnectionError(_NOT_RUNNING_MSG) from exc
                await asyncio.sleep(0.5 * 2 ** (attempt - 1))

    def is_available(self) -> bool:
        """Fast sync probe: server up AND the configured model is pulled.

        Result is cached for a few seconds so back-to-back ?? don't each pay
        the HTTP round-trip.
        """
        now = time.monotonic()
        if self._avail_cache is not None and now - self._avail_cache[0] < self._avail_ttl:
            return self._avail_cache[1]
        try:
            response = httpx.get(f"{self._base_url}/api/tags", timeout=1.0)
            if response.status_code != 200:
                result = False
            else:
                names = [m.get("name", "") for m in response.json().get("models", [])]
                result = any(
                    name == self._model or name.split(":", 1)[0] == self._model
                    for name in names
                )
        except Exception:  # noqa: BLE001 — any failure means "not available"
            result = False
        self._avail_cache = (now, result)
        return result

    # -- internals -----------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
        )

    def _build_request_body(self, prompt: str, stream: bool) -> dict:
        return {"model": self._model, "prompt": prompt, "stream": stream}

    def _handle_http_error(self, response: httpx.Response) -> None:
        if response.status_code == 404:
            raise LLMResponseError(
                f"model {self._model!r} not found. Pull it with: ollama pull {self._model}"
            )
        detail = ""
        try:
            detail = response.json().get("error", "")
        except (json.JSONDecodeError, ValueError):
            detail = response.text[:200]
        raise LLMResponseError(
            f"Ollama returned HTTP {response.status_code}: {detail or 'unknown error'}"
        )

    async def _with_retry(self, coro_factory: Callable[[], Awaitable]):
        """Retry on connection errors with exponential backoff (0.5s, 1s, ...)."""
        for attempt in range(self._retries + 1):
            try:
                return await coro_factory()
            except LLMConnectionError:
                if attempt == self._retries:
                    raise
                await asyncio.sleep(0.5 * 2**attempt)
