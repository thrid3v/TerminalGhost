# terminalghost.llm.base
#
# Responsibility:
#   Defines the abstract base class that all LLM backends must implement,
#   plus the factory function `get_backend` that reads config and returns
#   the appropriate concrete backend instance.
#   No HTTP calls, no model-specific code lives here.
#
# Key classes / functions to implement:
#
#   class LLMBackend(ABC):
#       """
#       Abstract interface for an LLM backend.
#       All backends (Ollama, Claude, future providers) must implement this.
#       """
#
#       @abstractmethod
#       async def query(self, prompt: str) -> str:
#           """
#           Send `prompt` to the model and return the complete response as a
#           single string. Blocks until the full response is received.
#
#           Args:
#               prompt (str): the assembled context prompt
#           Returns:
#               str: full model response text
#           Raises:
#               LLMConnectionError: if the backend is unreachable
#               LLMTimeoutError: if the request exceeds the configured timeout
#               LLMResponseError: if the backend returns an unexpected status
#           """
#           pass  # TODO: implement in subclass
#
#       @abstractmethod
#       async def stream_query(self, prompt: str) -> "AsyncIterator[str]":
#           """
#           Send `prompt` and yield response tokens as they arrive.
#           The caller is responsible for printing each chunk.
#           Must be an async generator.
#
#           Args:
#               prompt (str): the assembled context prompt
#           Yields:
#               str: next text chunk from the model
#           Raises:
#               LLMConnectionError, LLMTimeoutError, LLMResponseError
#           """
#           pass  # TODO: implement in subclass
#
#       @abstractmethod
#       def is_available(self) -> bool:
#           """
#           Lightweight synchronous check: can the backend be reached right now?
#           For Ollama: try a HEAD request to localhost:11434.
#           For cloud: check that an API key is configured.
#           Used by the trigger handler to give a clear error if the backend
#           is down before sending the full prompt.
#
#           Returns:
#               bool: True if the backend appears reachable/configured
#           """
#           pass  # TODO: implement in subclass
#
#   class LLMError(Exception):
#       """Base class for all LLM-related errors."""
#       pass  # TODO: implement
#
#   class LLMConnectionError(LLMError):
#       """Backend is unreachable (connection refused, DNS failure, etc.)."""
#       pass  # TODO: implement
#
#   class LLMTimeoutError(LLMError):
#       """Request exceeded the configured timeout."""
#       pass  # TODO: implement
#
#   class LLMResponseError(LLMError):
#       """Backend returned an unexpected HTTP status or malformed body."""
#       pass  # TODO: implement
#
#   def get_backend(config: "Config") -> LLMBackend:
#       """
#       Factory: read config.llm.backend and return the right LLMBackend
#       instance, fully configured.
#
#       Supported values of config.llm.backend:
#         "ollama"  → OllamaBackend(config.llm.ollama)
#         "claude"  → CloudBackend(config.llm.claude, provider="claude")
#         "openai"  → CloudBackend(config.llm.openai, provider="openai")
#
#       Args:
#           config (Config): loaded config object
#       Returns:
#           LLMBackend: the configured backend instance
#       Raises:
#           ValueError: if config.llm.backend is not a recognized value
#       """
#       pass  # TODO: implement
#
# Imports needed:
#   from abc import ABC, abstractmethod
#   from typing import AsyncIterator
#   from terminalghost.config.loader import Config

# TODO: implement LLMBackend ABC, error classes, and get_backend factory
