# terminalghost.llm.backends.cloud
#
# Responsibility:
#   Cloud LLM backend stub. Implements LLMBackend for remote API providers.
#   Primary target is Anthropic Claude; OpenAI is a secondary stub.
#   Designed so adding a new provider requires only a small subclass or a
#   provider config block — the wiring stays identical.
#
# Key classes / functions to implement:
#
#   class CloudBackend(LLMBackend):
#       """
#       LLM backend that calls a cloud provider API.
#
#       Args:
#           provider (str): "claude" | "openai"
#           api_key (str): API key. If empty, falls back to environment
#               variable (ANTHROPIC_API_KEY or OPENAI_API_KEY).
#           model (str): model identifier, e.g. "claude-opus-4-8"
#           max_tokens (int): max response tokens
#       """
#
#       async def query(self, prompt: str) -> str:
#           """
#           Send the prompt to the cloud provider and return the full response.
#           Delegates to _query_claude or _query_openai based on self.provider.
#
#           Args:
#               prompt (str): assembled context prompt
#           Returns:
#               str: model response text
#           Raises:
#               LLMConnectionError, LLMTimeoutError, LLMResponseError
#           """
#           pass  # TODO: implement
#
#       async def stream_query(self, prompt: str) -> "AsyncIterator[str]":
#           """
#           Stream the response from the cloud provider.
#           For Claude: use the Anthropic Python SDK streaming context manager.
#           For OpenAI: use the openai SDK streaming iterator.
#           Yield each text delta as it arrives.
#
#           Args:
#               prompt (str): assembled context prompt
#           Yields:
#               str: each incremental text chunk
#           """
#           pass  # TODO: implement
#
#       def is_available(self) -> bool:
#           """
#           Return True only if an API key is configured (either from config
#           or from the appropriate environment variable). Does NOT make a
#           network call — just checks that a key exists and is non-empty.
#           """
#           pass  # TODO: implement
#
#       async def _query_claude(self, prompt: str) -> str:
#           """
#           Use the `anthropic` Python SDK to send a messages API request.
#
#           Claude API usage notes:
#             - Wrap the assembled prompt as a single "user" message.
#             - Set max_tokens from config.
#             - Model IDs: "claude-opus-4-8", "claude-sonnet-4-6",
#               "claude-haiku-4-5-20251001"
#             - The response is in message.content[0].text
#
#           Args:
#               prompt (str): assembled context prompt
#           Returns:
#               str: response text from Claude
#           """
#           pass  # TODO: implement
#
#       async def _stream_claude(self, prompt: str) -> "AsyncIterator[str]":
#           """
#           Use the Anthropic SDK streaming context manager:
#             async with client.messages.stream(...) as stream:
#                 async for text in stream.text_stream:
#                     yield text
#
#           Args:
#               prompt (str): assembled context prompt
#           Yields:
#               str: streamed text delta
#           """
#           pass  # TODO: implement
#
#       async def _query_openai(self, prompt: str) -> str:
#           """
#           STUB — OpenAI support is not implemented yet.
#           Raise NotImplementedError with a message directing the user to
#           use the "claude" or "ollama" backend for now.
#           """
#           pass  # TODO: implement
#
#       def _resolve_api_key(self, env_var: str) -> str:
#           """
#           Return self.api_key if non-empty, otherwise read `env_var` from
#           the environment.
#
#           Args:
#               env_var (str): environment variable name, e.g. "ANTHROPIC_API_KEY"
#           Returns:
#               str: the resolved API key
#           Raises:
#               LLMResponseError: if no key is found anywhere
#           """
#           pass  # TODO: implement
#
# Edge cases:
#   - API key in config is checked into git accidentally: document in README
#     that env var is preferred; never log the key value.
#   - Rate limiting (429): catch and raise LLMResponseError with a message
#     suggesting a retry delay or switching to the Ollama backend.
#   - anthropic SDK not installed (user removed it from deps): wrap the import
#     in a try/except ImportError and raise a clear error at runtime.
#   - Network timeout to cloud API: wrap in LLMTimeoutError.
#   - Claude content policy refusal: the SDK may raise BadRequestError;
#     map to LLMResponseError and surface the message to the user.
#   - Max tokens reached mid-response (stop_reason="max_tokens"): warn the
#     user that the response was truncated; suggest increasing max_tokens.
#
# Imports needed:
#   import os, logging
#   import httpx
#   # anthropic imported lazily to avoid hard failure if not installed
#   from terminalghost.llm.base import (
#       LLMBackend, LLMConnectionError, LLMTimeoutError, LLMResponseError
#   )

# TODO: implement CloudBackend class
