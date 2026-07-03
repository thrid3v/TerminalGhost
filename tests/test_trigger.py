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
    def __init__(self, recap_prompt="RECAP-PROMPT"):
        self.calls: list[tuple[str, str]] = []
        self.last_kwargs: dict = {}
        self.recap_prompt = recap_prompt
        self.recap_since: float | None = None

    def assemble(self, cwd, inline_context="", *, intent="default", prior_exchange=None,
                 pasted=None):
        self.calls.append((cwd, inline_context))
        self.last_kwargs = {"intent": intent, "prior_exchange": prior_exchange,
                            "pasted": pasted}
        return f"PROMPT[{cwd}|{inline_context}]"

    def assemble_recap(self, since):
        self.recap_since = since
        return self.recap_prompt


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
    # Over a socket (send given) only raw answer text is streamed — the client
    # renders its own header.
    assert "hello world" in text
    assert "TerminalGhost" not in text


async def test_foreground_path_emits_plaintext_header(handler, capsys):
    h, _, _ = handler
    # send=None → foreground/dev path writes a plaintext header to stdout.
    await h.handle("??", "/tmp")
    out = capsys.readouterr().out
    assert "TerminalGhost" in out
    assert "hello world" in out


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


async def test_intent_fix_is_parsed(handler):
    h, _, assembler = handler
    _, send = collect_sink()
    await h.handle("?? fix the makefile", "/proj", send=send)
    assert assembler.calls[-1] == ("/proj", "the makefile")
    assert assembler.last_kwargs["intent"] == "fix"


async def test_default_intent_when_no_keyword(handler):
    h, _, assembler = handler
    _, send = collect_sink()
    await h.handle("?? why did it fail", "/proj", send=send)
    assert assembler.last_kwargs["intent"] == "default"
    assert assembler.calls[-1] == ("/proj", "why did it fail")


async def test_followup_exchange_is_passed_next_time(handler):
    h, backend, assembler = handler
    backend.chunks = ("the answer",)
    _, send = collect_sink()
    await h.handle("?? first question", "/proj", send=send)
    # First call has no prior exchange.
    assert h._recent_exchange() == ("first question", "the answer")
    await h.handle("?? follow up", "/proj", send=send)
    prior = assembler.last_kwargs["prior_exchange"]
    assert prior == ("first question", "the answer")


async def test_followup_disabled_when_window_zero():
    import dataclasses

    config = Config()
    config = dataclasses.replace(
        config, llm=dataclasses.replace(config.llm, followup_seconds=0)
    )
    h = TriggerHandler(None, FakeAssembler(), FakeBackend(chunks=("a",)), config)
    _, send = collect_sink()
    await h.handle("??", "/proj", send=send)
    assert h._recent_exchange() is None  # window 0 disables follow-ups


class FakeDB:
    def __init__(self, last_error, recent=None):
        self._last_error = last_error
        self._recent = recent or []

    def get_last_error(self, cwd=None):
        return self._last_error

    def get_recent_commands(self, limit=10):
        return self._recent


async def test_empty_context_emits_tip_not_llm():
    backend = FakeBackend()
    h = TriggerHandler(FakeDB(None, recent=[]), FakeAssembler(), backend, Config())
    out, send = collect_sink()
    await h.handle("??", "/proj", send=send)
    assert "Nothing to fix" in "".join(out)
    assert backend.stream_calls == 0  # tip instead of an LLM call


async def test_non_empty_context_skips_tip():
    import types

    recent = [types.SimpleNamespace(cmd="make build")]
    backend = FakeBackend()
    h = TriggerHandler(FakeDB(None, recent=recent), FakeAssembler(), backend, Config())
    out, send = collect_sink()
    await h.handle("??", "/proj", send=send)
    assert "Nothing to fix" not in "".join(out)
    assert backend.stream_calls == 1


async def test_hint_silent_without_recent_error():
    h = TriggerHandler(FakeDB(None), FakeAssembler(), FakeBackend(), Config())
    out, send = collect_sink()
    await h.hint("/proj", send)
    assert out == []


async def test_hint_streams_when_error_present():
    h = TriggerHandler(
        FakeDB("err"), FakeAssembler(), FakeBackend(chunks=("try --force",)), Config()
    )
    out, send = collect_sink()
    await h.hint("/proj", send)
    assert "try --force" in "".join(out)


async def test_hint_silent_when_backend_down():
    h = TriggerHandler(
        FakeDB("err"), FakeAssembler(), FakeBackend(available=False), Config()
    )
    out, send = collect_sink()
    await h.hint("/proj", send)
    assert out == []


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


# -- blind-failure nudge ------------------------------------------------------


def _fail_event(cmd="make build", output=None):
    import types

    return types.SimpleNamespace(
        cmd=cmd, output=output, exit_code=2, id=1, cwd="/proj",
    )


async def test_blind_failure_nudges_to_tgr():
    # Failing command whose output was never captured → guide the user to tgr.
    db = FakeDB(_fail_event(), recent=[_fail_event()])
    backend = FakeBackend(chunks=("```\nnpm install\n```",))
    h = TriggerHandler(db, FakeAssembler(), backend, Config())
    out, send = collect_sink()
    await h.handle("?? fix", "/proj", send=send)
    text = "".join(out)
    assert "tgr make build" in text
    assert "best guess" in text
    # The nudge's own `tgr ...` must NOT become the applyable suggestion.
    assert h.last_suggestion() == "npm install"


async def test_no_nudge_when_output_present():
    db = FakeDB(_fail_event(output="undefined reference"), recent=[_fail_event()])
    h = TriggerHandler(db, FakeAssembler(), FakeBackend(), Config())
    out, send = collect_sink()
    await h.handle("?? fix", "/proj", send=send)
    assert "tgr" not in "".join(out)


async def test_no_nudge_when_user_pasted_output():
    # `explain` supplies the output itself — don't tell them to capture it.
    db = FakeDB(_fail_event(), recent=[_fail_event()])
    h = TriggerHandler(db, FakeAssembler(), FakeBackend(), Config())
    out, send = collect_sink()
    await h.handle("?? explain this", "/proj", send=send, pasted="ERROR: boom")
    assert "tgr" not in "".join(out)


# -- recap --------------------------------------------------------------------


async def test_recap_streams_summary(handler):
    h, backend, assembler = handler
    out, send = collect_sink()
    await h.recap(1000.0, send)
    assert "hello world" in "".join(out)
    assert assembler.recap_since == 1000.0
    assert backend.prompts == ["RECAP-PROMPT"]


async def test_recap_empty_window_says_so():
    assembler = FakeAssembler(recap_prompt=None)
    backend = FakeBackend()
    h = TriggerHandler(db=None, assembler=assembler, backend=backend, config=Config())
    out, send = collect_sink()
    await h.recap(1000.0, send)
    assert "Nothing captured" in "".join(out)
    assert backend.stream_calls == 0


async def test_recap_backend_unavailable():
    h = TriggerHandler(
        db=None, assembler=FakeAssembler(), backend=FakeBackend(available=False),
        config=Config(),
    )
    out, send = collect_sink()
    await h.recap(1000.0, send)
    assert "not available" in "".join(out)
