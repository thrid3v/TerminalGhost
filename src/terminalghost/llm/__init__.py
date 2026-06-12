# terminalghost.llm — model-agnostic LLM interface
#
# Responsibility:
#   Re-exports the abstract base class and the factory function so callers
#   never need to import a specific backend directly.
#
#   Usage pattern:
#     from terminalghost.llm import get_backend
#     backend = get_backend(config)
#     response = await backend.query(prompt)

from terminalghost.llm.base import (
    LLMBackend,
    LLMConnectionError,
    LLMError,
    LLMResponseError,
    LLMTimeoutError,
    get_backend,
)

__all__ = [
    "LLMBackend",
    "LLMConnectionError",
    "LLMError",
    "LLMResponseError",
    "LLMTimeoutError",
    "get_backend",
]
