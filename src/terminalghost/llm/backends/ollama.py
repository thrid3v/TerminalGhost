# terminalghost.llm.backends.ollama
#
# Responsibility:
#   Concrete LLMBackend implementation for the Ollama local HTTP API.
#   Talks to http://localhost:11434 (or a configured URL) using httpx.
#   This is the default backend — no API key required, fully offline.
#
# Ollama API reference (relevant endpoints):
#   POST /api/generate   — non-streaming and streaming generation
#   GET  /api/tags       — list available models (used by is_available)
#   The API returns newline-delimited JSON objects when streaming=True.
#
# Key classes / functions to implement:
#
#   class OllamaBackend(LLMBackend):
#       """
#       LLM backend that sends prompts to a locally running Ollama instance.
#
#       Args:
#           base_url (str): Ollama server URL, default "http://localhost:11434"
#           model (str): model tag to use, e.g. "llama3", "mistral", "codellama"
#           timeout (float): request timeout in seconds
#           retries (int): number of retry attempts on connection error
#       """
#
#       async def query(self, prompt: str) -> str:
#           """
#           POST /api/generate with stream=False.
#           Parse the "response" field from the single JSON response object.
#
#           Args:
#               prompt (str): assembled context prompt
#           Returns:
#               str: model response text
#           Raises:
#               LLMConnectionError: if httpx.ConnectError or connection refused
#               LLMTimeoutError: if httpx.TimeoutException
#               LLMResponseError: if HTTP status != 200 or JSON malformed
#           """
#           pass  # TODO: implement
#
#       async def stream_query(self, prompt: str) -> "AsyncIterator[str]":
#           """
#           POST /api/generate with stream=True.
#           The response body is a series of newline-delimited JSON objects:
#             {"model":"llama3","response":"Hello","done":false}
#             {"model":"llama3","response":"!","done":true,"...":"..."}
#           Yield the "response" field from each object until "done" is True.
#
#           Args:
#               prompt (str): assembled context prompt
#           Yields:
#               str: each incremental text chunk
#           Raises:
#               LLMConnectionError, LLMTimeoutError, LLMResponseError
#           """
#           pass  # TODO: implement
#
#       def is_available(self) -> bool:
#           """
#           Synchronous check: GET /api/tags with a short timeout (1s).
#           Returns True if Ollama is running and the configured model exists
#           in the response. Returns False on any error.
#
#           Note: uses httpx.get() (sync) to avoid requiring an event loop
#           at check time. This is intentionally a fast, cheap probe.
#           """
#           pass  # TODO: implement
#
#       def _build_request_body(self, prompt: str, stream: bool) -> dict:
#           """
#           Construct the JSON body for /api/generate.
#
#           Args:
#               prompt (str): the prompt text
#               stream (bool): whether to enable streaming
#           Returns:
#               dict: request body with model, prompt, stream fields
#           """
#           pass  # TODO: implement
#
#       def _handle_http_error(self, response: "httpx.Response") -> None:
#           """
#           Inspect a non-200 response and raise the appropriate LLMError.
#           Common Ollama error cases:
#             404: model not found → LLMResponseError with helpful message
#             500: Ollama internal error → LLMResponseError
#           """
#           pass  # TODO: implement
#
#       def _with_retry(self, coro_factory) -> "Awaitable":
#           """
#           Retry helper: call coro_factory() up to self.retries times,
#           catching LLMConnectionError between attempts with exponential
#           backoff (0.5s, 1s, ...). Re-raises on final failure.
#
#           Args:
#               coro_factory: zero-arg callable returning an awaitable
#           Returns:
#               Awaitable: the result of the first successful attempt
#           """
#           pass  # TODO: implement
#
# Edge cases:
#   - Ollama not running: ConnectError should produce a friendly message
#     like "Ollama is not running. Start it with: ollama serve" rather than
#     a raw stack trace.
#   - Model not pulled: 404 from /api/generate should suggest
#     `ollama pull <model>`.
#   - Streaming partial JSON: a single iter chunk may contain multiple JSON
#     objects or a split object. Buffer incomplete lines across iterations.
#   - Very long responses: no hard limit, but the terminal output is streamed
#     so memory is bounded by the streaming chunk size.
#   - Context length exceeded: Ollama returns a 400 with a specific error;
#     catch and raise LLMResponseError with the token count details.
#   - Ollama may be running on a non-default port; always use config.base_url.
#
# Imports needed:
#   import json, asyncio, logging
#   import httpx
#   from terminalghost.llm.base import LLMBackend, LLMConnectionError, LLMTimeoutError, LLMResponseError

# TODO: implement OllamaBackend class
