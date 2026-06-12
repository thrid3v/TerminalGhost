# tests.test_llm — tests for llm.base, backends.ollama, backends.cloud
#
# Network is mocked via httpx.MockTransport (injected into OllamaBackend).

import dataclasses
import json

import httpx
import pytest

from terminalghost.config.loader import Config
from terminalghost.llm.backends.cloud import CloudBackend
from terminalghost.llm.backends.ollama import OllamaBackend
from terminalghost.llm.base import (
    LLMConnectionError,
    LLMResponseError,
    LLMTimeoutError,
    get_backend,
)


def config_with_backend(name: str) -> Config:
    config = Config()
    return dataclasses.replace(config, llm=dataclasses.replace(config.llm, backend=name))


# -- get_backend ---------------------------------------------------------------


def test_get_backend_ollama():
    assert isinstance(get_backend(config_with_backend("ollama")), OllamaBackend)


def test_get_backend_claude():
    backend = get_backend(config_with_backend("claude"))
    assert isinstance(backend, CloudBackend)


def test_get_backend_unknown_raises():
    config = config_with_backend("ollama")
    config = dataclasses.replace(
        config, llm=dataclasses.replace(config.llm, backend="nonsense")
    )
    with pytest.raises(ValueError):
        get_backend(config)


# -- OllamaBackend.query ----------------------------------------------------------


def ollama(handler, retries=0):
    return OllamaBackend(
        model="llama3", retries=retries, transport=httpx.MockTransport(handler)
    )


async def test_query_returns_response():
    def handler(request):
        assert json.loads(request.content)["stream"] is False
        return httpx.Response(200, json={"response": "hello"})

    assert await ollama(handler).query("hi") == "hello"


async def test_query_connection_refused():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMConnectionError, match="ollama serve"):
        await ollama(handler).query("hi")


async def test_query_model_not_found():
    def handler(request):
        return httpx.Response(404, json={"error": "model not found"})

    with pytest.raises(LLMResponseError, match="ollama pull"):
        await ollama(handler).query("hi")


async def test_query_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(LLMTimeoutError):
        await ollama(handler).query("hi")


async def test_query_retries_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"response": "second try"})

    assert await ollama(handler, retries=2).query("hi") == "second try"
    assert calls["n"] == 2


# -- OllamaBackend.stream_query ------------------------------------------------------


async def test_stream_yields_chunks_in_order():
    lines = [
        json.dumps({"response": "Hel", "done": False}),
        json.dumps({"response": "lo", "done": False}),
        json.dumps({"response": "!", "done": True}),
    ]

    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content="\n".join(lines) + "\n")

    chunks = [c async for c in ollama(handler).stream_query("hi")]
    assert chunks == ["Hel", "lo", "!"]


async def test_stream_stops_at_done():
    lines = [
        json.dumps({"response": "a", "done": True}),
        json.dumps({"response": "never", "done": False}),
    ]

    def handler(request):
        return httpx.Response(200, content="\n".join(lines) + "\n")

    chunks = [c async for c in ollama(handler).stream_query("hi")]
    assert chunks == ["a"]


# -- OllamaBackend.is_available -------------------------------------------------------


def test_is_available_true(monkeypatch):
    def fake_get(url, timeout):
        return httpx.Response(
            200, json={"models": [{"name": "llama3:latest"}, {"name": "mistral"}]}
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    assert OllamaBackend(model="llama3").is_available() is True


def test_is_available_model_missing(monkeypatch):
    def fake_get(url, timeout):
        return httpx.Response(200, json={"models": [{"name": "mistral"}]})

    monkeypatch.setattr(httpx, "get", fake_get)
    assert OllamaBackend(model="llama3").is_available() is False


def test_is_available_connection_refused(monkeypatch):
    def fake_get(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", fake_get)
    assert OllamaBackend(model="llama3").is_available() is False


# -- CloudBackend ---------------------------------------------------------------------


def test_cloud_available_with_config_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert CloudBackend("claude", api_key="sk-test").is_available() is True


def test_cloud_available_with_env_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert CloudBackend("claude", api_key="").is_available() is True


def test_cloud_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert CloudBackend("claude", api_key="").is_available() is False


def test_cloud_unknown_provider_raises():
    with pytest.raises(ValueError):
        CloudBackend("gemini")


async def test_openai_stub_raises():
    with pytest.raises(NotImplementedError):
        await CloudBackend("openai", api_key="sk-x").query("hi")


def test_resolve_api_key_prefers_config(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    backend = CloudBackend("claude", api_key="sk-config")
    assert backend._resolve_api_key("ANTHROPIC_API_KEY") == "sk-config"


def test_resolve_api_key_missing_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    backend = CloudBackend("claude", api_key="")
    with pytest.raises(LLMResponseError, match="ANTHROPIC_API_KEY"):
        backend._resolve_api_key("ANTHROPIC_API_KEY")
