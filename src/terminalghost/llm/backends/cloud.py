# terminalghost.llm.backends.cloud
#
# Cloud LLM backend. Primary target is Anthropic Claude via the `anthropic`
# SDK (imported lazily so the dependency stays optional at runtime);
# OpenAI is a stub that raises NotImplementedError.

from __future__ import annotations

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
    ) -> None:
        if provider not in _ENV_VARS:
            raise ValueError(f"unknown cloud provider: {provider!r}")
        self._provider = provider
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens

    # -- public API ----------------------------------------------------------

    async def query(self, prompt: str) -> str:
        if self._provider == "openai":
            return await self._query_openai(prompt)
        return await self._query_claude(prompt)

    async def stream_query(self, prompt: str) -> AsyncIterator[str]:
        if self._provider == "openai":
            await self._query_openai(prompt)  # raises NotImplementedError
            return
        async for chunk in self._stream_claude(prompt):
            yield chunk

    def is_available(self) -> bool:
        """True if an API key is configured (config or env). No network call."""
        try:
            self._resolve_api_key(_ENV_VARS[self._provider])
        except LLMResponseError:
            return False
        return True

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

    # -- OpenAI (stub) ---------------------------------------------------------

    async def _query_openai(self, prompt: str) -> str:
        raise NotImplementedError(
            "the OpenAI backend is not implemented yet; set llm.backend to"
            " 'claude' or 'ollama'"
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
