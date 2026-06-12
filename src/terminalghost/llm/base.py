# terminalghost.llm.base
#
# Abstract base class all LLM backends implement, the LLMError hierarchy,
# and the get_backend factory. No HTTP calls or model-specific code here.

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from terminalghost.config.loader import Config


class LLMError(Exception):
    """Base class for all LLM-related errors."""


class LLMConnectionError(LLMError):
    """Backend is unreachable (connection refused, DNS failure, etc.)."""


class LLMTimeoutError(LLMError):
    """Request exceeded the configured timeout."""


class LLMResponseError(LLMError):
    """Backend returned an unexpected status or malformed body."""


class LLMBackend(ABC):
    """Abstract interface for an LLM backend (Ollama, Claude, ...)."""

    @abstractmethod
    async def query(self, prompt: str) -> str:
        """Send `prompt` and return the complete response text.

        Raises LLMConnectionError / LLMTimeoutError / LLMResponseError.
        """

    @abstractmethod
    def stream_query(self, prompt: str) -> AsyncIterator[str]:
        """Send `prompt` and yield response text chunks as they arrive.

        Async generator. Raises the same LLMError subclasses as query().
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap synchronous probe: can this backend be used right now?

        Ollama: short HTTP request to the local server. Cloud: an API key is
        configured. Used by the trigger handler to fail fast before
        assembling a full prompt. Note this may block briefly — call it via
        run_in_executor from async code.
        """


def get_backend(config: "Config") -> LLMBackend:
    """Factory: return the LLMBackend instance selected by config.llm.backend."""
    # Imported lazily so `import terminalghost.llm.base` stays lightweight and
    # the anthropic SDK is only touched when actually selected.
    backend = config.llm.backend
    if backend == "ollama":
        from terminalghost.llm.backends.ollama import OllamaBackend

        ollama = config.llm.ollama
        return OllamaBackend(
            base_url=ollama.base_url,
            model=ollama.model,
            timeout=float(ollama.timeout_seconds),
            retries=ollama.retries,
        )
    if backend == "claude":
        from terminalghost.llm.backends.cloud import CloudBackend

        claude = config.llm.claude
        return CloudBackend(
            provider="claude",
            api_key=claude.api_key,
            model=claude.model,
            max_tokens=claude.max_tokens,
        )
    if backend == "openai":
        from terminalghost.llm.backends.cloud import CloudBackend

        openai = config.llm.openai
        return CloudBackend(provider="openai", api_key=openai.api_key, model=openai.model)
    raise ValueError(f"unknown LLM backend: {backend!r}")
