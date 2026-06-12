# tests.test_trigger — tests for terminalghost.trigger.handler

import asyncio

import pytest

from terminalghost.config.loader import Config
from terminalghost.llm.base import LLMBackend, LLMResponseError
from terminalghost.trigger.handler import TriggerHandler


class FakeBackend(LLMBackend):
    def __init__(self, chunks=("hello", " world"), available=True, delay=0.0):
        self.chunks = chunks
        self.available = available
        self.delay = delay
        self.stream_calls = 0
        self.prompts: list[str] = []

    async def query(self, prompt: str) -> str:
        return "".join(self.chunks)

    async def stream_query(self, prompt: str):
        self.stream_calls += 1
        self.prompts.append(prompt)
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    def is_available(self) -> bool:
        return self.available


class FakeAssembler:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def assemble(self, cwd, inline_context=""):
        self.calls.append((cwd, inline_context))
        return f"PROMPT[{cwd}|{inline_context}]"


@pytest.fixture
def handler():
    backend = FakeBackend()
    assembler = FakeAssembler()
    h = TriggerHandler(db=None, assembler=assembler, backend=backend, config=Config())
    return h, backend, assembler


def collect_sink():
    out: list[str] = []

    async def send(text: str) -> None:
        out.append(text)

    return out, send


# -- is_trigger ----------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd,expected",
    [
        ("??", True),
        ("??  ", True),
        ("?? extra context", True),
        ("?? ", True),
        ("echo ??", False),
        ("??something", False),
        ("? ?", False),
        ("", False),
        ("   ??   ", True),
    ],
)
def test_is_trigger(handler, cmd, expected):
    h, _, _ = handler
    assert h.is_trigger(cmd) is expected


# -- extract_inline_context -------------------------------------------------------


@pytest.mark.parametrize(
    "cmd,expected",
    [
        ("??", ""),
        ("?? why fail", "why fail"),
        ("??   leading ws", "leading ws"),
    ],
)
def test_extract_inline_context(handler, cmd, expected):
    h, _, _ = handler
    assert h.extract_inline_context(cmd) == expected


def test_inline_context_disabled():
    import dataclasses

    config = Config()
    config = dataclasses.replace(
        config, llm=dataclasses.replace(config.llm, allow_inline_context=False)
    )
    h = TriggerHandler(None, FakeAssembler(), FakeBackend(), config)
    assert h.extract_inline_context("?? why fail") == ""


# -- handle pipeline -----------------------------------------------------------------


async def test_non_trigger_is_noop(handler):
    h, backend, assembler = handler
    out, send = collect_sink()
    await h.handle("ls -la", "/tmp", send=send)
    assert out == []
    assert backend.stream_calls == 0
    assert assembler.calls == []


async def test_backend_unavailable_prints_error(handler):
    h, backend, assembler = handler
    backend.available = False
    out, send = collect_sink()
    await h.handle("??", "/tmp", send=send)
    text = "".join(out)
    assert "not available" in text
    assert backend.stream_calls == 0
    assert assembler.calls == []  # no prompt assembled when backend is down


async def test_full_pipeline(handler):
    h, backend, assembler = handler
    out, send = collect_sink()
    await h.handle("?? why did make fail", "/proj", send=send)
    text = "".join(out)
    assert assembler.calls == [("/proj", "why did make fail")]
    assert backend.stream_calls == 1
    assert "TerminalGhost" in text
    assert "hello world" in text


async def test_llm_error_is_reported_not_raised(handler):
    h, backend, _ = handler

    async def failing_stream(prompt):
        raise LLMResponseError("model exploded")
        yield  # pragma: no cover — makes this an async generator

    backend.stream_query = failing_stream
    out, send = collect_sink()
    await h.handle("??", "/tmp", send=send)
    assert "model exploded" in "".join(out)


async def test_cancel_mid_stream_is_clean(handler):
    h, backend, _ = handler
    backend.chunks = tuple(f"c{i}" for i in range(100))
    backend.delay = 0.01
    out, send = collect_sink()
    task = asyncio.create_task(h.handle("??", "/tmp", send=send))
    await asyncio.sleep(0.05)
    task.cancel()
    # the handler swallows the cancel and finishes cleanly
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert "[interrupted]" in "".join(out)


async def test_rapid_triggers_serialized(handler):
    h, backend, _ = handler
    backend.delay = 0.01
    out1, send1 = collect_sink()
    out2, send2 = collect_sink()
    await asyncio.gather(
        h.handle("??", "/tmp", send=send1),
        h.handle("??", "/tmp", send=send2),
    )
    # both ran to completion without interleaving errors
    assert "hello world" in "".join(out1)
    assert "hello world" in "".join(out2)
    assert backend.stream_calls == 2
