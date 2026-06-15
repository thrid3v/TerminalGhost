# terminalghost.llm.backends.cloud
#
# Cloud LLM backend. Primary target is Anthropic Claude via the `anthropic`
# SDK (imported lazily so the dependency stays optional at runtime);
# OpenAI is a stub that raises NotImplementedError.

from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator

from terminalghost.llm.base import (
    LLMBackend,
    LLMConnectionError,
    LLMResponseError,
    LLMTimeoutError,
)

log = logging.getLogger(__name__)

_ENV_VARS = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}

# Sentinel returned by the SSE parser when the stream's [DONE] marker is seen.
_SSE_DONE = "\x00__SSE_DONE__"

_TRUNCATION_NOTE = (
    "\n\n[response truncated: max_tokens reached — increase llm.claude.max_tokens]"
)


def _import_anthropic():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise LLMResponseError(
            "the anthropic SDK is not installed; run: pip install anthropic"
        ) from exc
    return anthropic


class CloudBackend(LLMBackend):
    """LLM backend that calls a cloud provider API (Claude; OpenAI stub)."""

    def __init__(
        self,
        provider: str,
        api_key: str = "",
        model: str = "claude-opus-4-8",
        max_tokens: int = 1024,
        base_url: str = "https://api.openai.com/v1",
        transport=None,
    ) -> None:
        if provider not in _ENV_VARS:
            raise ValueError(f"unknown cloud provider: {provider!r}")
        self._provider = provider
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._base_url = base_url.rstrip("/")
        # Injectable httpx transport so tests can use httpx.MockTransport.
        self._transport = transport

    # -- public API ----------------------------------------------------------

    async def query(self, prompt: str) -> str:
        if self._provider == "openai":
            return await self._query_openai(prompt)
        return await self._query_claude(prompt)

    async def stream_query(self, prompt: str) -> AsyncIterator[str]:
        if self._provider == "openai":
            async for chunk in self._stream_openai(prompt):
                yield chunk
            return
        async for chunk in self._stream_claude(prompt):
            yield chunk

    def is_available(self) -> bool:
        """True if usable without a network call.

        Cloud (Claude/OpenAI): an API key is configured. Exception: an OpenAI-
        compatible server on localhost (LM Studio, llama.cpp, Ollama's compat
        API) usually needs no key, so a local base_url counts as available.
        """
        if self._provider == "openai" and self._is_local_base_url():
            return True
        try:
            self._resolve_api_key(_ENV_VARS[self._provider])
        except LLMResponseError:
            return False
        return True

    def _is_local_base_url(self) -> bool:
        return "://localhost" in self._base_url or "://127.0.0.1" in self._base_url

    # -- Claude --------------------------------------------------------------

    async def _query_claude(self, prompt: str) -> str:
        anthropic = _import_anthropic()
        client = anthropic.AsyncAnthropic(
            api_key=self._resolve_api_key("ANTHROPIC_API_KEY")
        )
        try:
            message = await client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError(f"Claude request timed out: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMConnectionError(f"cannot reach the Anthropic API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise self._map_status_error(exc) from exc

        text = "".join(
            block.text for block in message.content if block.type == "text"
        )
        if message.stop_reason == "max_tokens":
            text += _TRUNCATION_NOTE
        return text

    async def _stream_claude(self, prompt: str) -> AsyncIterator[str]:
        anthropic = _import_anthropic()
        client = anthropic.AsyncAnthropic(
            api_key=self._resolve_api_key("ANTHROPIC_API_KEY")
        )
        try:
            async with client.messages.stream(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
                if final.stop_reason == "max_tokens":
                    yield _TRUNCATION_NOTE
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError(f"Claude request timed out: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMConnectionError(f"cannot reach the Anthropic API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise self._map_status_error(exc) from exc

    @staticmethod
    def _map_status_error(exc) -> LLMResponseError:
        if exc.status_code == 429:
            return LLMResponseError(
                "Anthropic API rate limit hit (429); wait and retry, or switch"
                " to the ollama backend"
            )
        # 400 includes content-policy refusals; surface the API message.
        return LLMResponseError(f"Anthropic API error {exc.status_code}: {exc.message}")

    # -- OpenAI (and OpenAI-compatible servers) --------------------------------

    def _openai_headers(self) -> dict:
        # Local compat servers may not require a key; send one only if we have it.
        key = self._api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key and not self._is_local_base_url():
            self._resolve_api_key("OPENAI_API_KEY")  # raises LLMResponseError
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _openai_body(self, prompt: str, stream: bool) -> dict:
        return {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self._max_tokens,
            "stream": stream,
        }

    async def _query_openai(self, prompt: str) -> str:
        import httpx

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=60.0, transport=self._transport
            ) as client:
                resp = await client.post(
                    "/chat/completions",
                    json=self._openai_body(prompt, stream=False),
                    headers=self._openai_headers(),
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"OpenAI request timed out: {exc}") from exc
        except httpx.ConnectError as exc:
            raise LLMConnectionError(
                f"cannot reach the OpenAI-compatible API at {self._base_url}"
            ) from exc
        if resp.status_code != 200:
            self._raise_openai_status(resp)
        try:
            return resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMResponseError(f"malformed OpenAI response: {exc}") from exc

    async def _stream_openai(self, prompt: str) -> AsyncIterator[str]:
        import httpx

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=60.0, transport=self._transport
            ) as client:
                async with client.stream(
                    "POST",
                    "/chat/completions",
                    json=self._openai_body(prompt, stream=True),
                    headers=self._openai_headers(),
                ) as resp:
                    if resp.status_code != 200:
                        await resp.aread()
                        self._raise_openai_status(resp)
                    async for line in resp.aiter_lines():
                        piece = self._parse_sse_line(line)
                        if piece == _SSE_DONE:
                            return
                        if piece:
                            yield piece
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"OpenAI request timed out: {exc}") from exc
        except httpx.ConnectError as exc:
            raise LLMConnectionError(
                f"cannot reach the OpenAI-compatible API at {self._base_url}"
            ) from exc

    @staticmethod
    def _parse_sse_line(line: str) -> str | None:
        """Extract the text delta from one SSE line, or _SSE_DONE / None."""
        line = line.strip()
        if not line or not line.startswith("data:"):
            return None
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            return _SSE_DONE
        try:
            obj = json.loads(data)
            return obj["choices"][0]["delta"].get("content") or None
        except (json.JSONDecodeError, KeyError, IndexError):
            return None

    def _raise_openai_status(self, resp) -> None:
        if resp.status_code == 429:
            raise LLMResponseError(
                "OpenAI API rate limit hit (429); wait and retry, or switch backend"
            )
        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except (json.JSONDecodeError, ValueError, AttributeError):
            detail = resp.text[:200]
        raise LLMResponseError(
            f"OpenAI API error {resp.status_code}: {detail or 'unknown error'}"
        )

    # -- helpers ---------------------------------------------------------------

    def _resolve_api_key(self, env_var: str) -> str:
        """Config key if non-empty, else the environment variable."""
        key = self._api_key or os.environ.get(env_var, "")
        if not key:
            raise LLMResponseError(
                f"no API key configured: set {env_var} or llm.{self._provider}.api_key"
            )
        return key
