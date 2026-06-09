# tests.test_llm
#
# Tests for: terminalghost.llm.base, terminalghost.llm.backends.ollama,
#            terminalghost.llm.backends.cloud
#
# Use httpx mock transport (httpx.MockTransport or pytest-httpx) to avoid
# real network calls in unit tests.
#
# Test cases to implement:
#
#   get_backend():
#     - config.llm.backend="ollama" → returns OllamaBackend instance
#     - config.llm.backend="claude" → returns CloudBackend instance
#     - config.llm.backend="unknown" → raises ValueError
#
#   OllamaBackend.query():
#     - Mock 200 response with {"response": "hello"} → returns "hello"
#     - Mock connection refused → raises LLMConnectionError
#     - Mock 404 (model not found) → raises LLMResponseError with helpful msg
#     - Mock timeout → raises LLMTimeoutError
#     - Retry logic: first call fails, second succeeds → returns response
#
#   OllamaBackend.stream_query():
#     - Mock streaming NDJSON response → yields correct chunks in order
#     - Partial JSON line across two chunks → buffered, yielded complete token
#     - done=True terminates the generator
#
#   OllamaBackend.is_available():
#     - Mock 200 from /api/tags containing the model → True
#     - Mock connection refused → False
#     - Model not in tags list → False
#
#   CloudBackend.is_available():
#     - api_key set in config → True
#     - api_key empty, ANTHROPIC_API_KEY env set → True
#     - both empty → False
#
#   CloudBackend._query_claude():
#     - Mock Anthropic SDK response → returns text correctly
#     - Rate limit exception (429) → raises LLMResponseError

import pytest

# TODO: write test functions
