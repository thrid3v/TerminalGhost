# tests.test_capture — tests for terminalghost.capture.shell_hooks
#
# HookReceiver is exercised over a real TCP loopback socket on an ephemeral
# port (port=0). PTYCapture is still a stub (Unix-only deep capture path),
# so its tests are deferred.

import asyncio
import json

import pytest

from terminalghost.capture.shell_hooks import HookReceiver
from terminalghost.storage.db import CommandEvent


def valid_payload(**overrides):
    payload = {
        "cmd": "make build",
        "exit": 1,
        "cwd": "/home/user/project",
        "duration": 4231,
        "ts": 1718000000.123,
        "pid": 4242,
        "shell": "zsh",
    }
    payload.update(overrides)
    return payload


class Collector:
    def __init__(self):
        self.events: list[tuple[CommandEvent, int | None, str | None]] = []
        self.queries: list[tuple[str, str]] = []
        self.got_event = asyncio.Event()

    async def on_event(self, event, shell_pid, shell):
        self.events.append((event, shell_pid, shell))
        self.got_event.set()

    async def on_query(self, cmd, cwd, send, context=None):
        self.queries.append((cmd, cwd))
        await send("ANSWER for ")
        await send(cmd)


@pytest.fixture
async def receiver():
    collector = Collector()
    recv = HookReceiver("127.0.0.1", 0, collector.on_event, collector.on_query)
    await recv.start()
    yield recv, collector
    await recv.stop()


async def connect(recv: HookReceiver):
    return await asyncio.open_connection("127.0.0.1", recv.port)


# -- auth token ------------------------------------------------------------------


def test_token_ok():
    async def noop(*args):
        pass

    recv = HookReceiver("127.0.0.1", 0, noop, auth_token="abc")
    assert recv._token_ok("abc") is True
    assert recv._token_ok("xyz") is False
    assert recv._token_ok(None) is False
    # auth disabled (no token configured) accepts anything
    assert HookReceiver("127.0.0.1", 0, noop)._token_ok(None) is True


async def test_auth_token_enforced():
    collector = Collector()
    recv = HookReceiver("127.0.0.1", 0, collector.on_event, auth_token="s3cret")
    await recv.start()
    try:
        # Missing token → rejected, no event recorded.
        _, writer = await asyncio.open_connection("127.0.0.1", recv.port)
        writer.write((json.dumps(valid_payload()) + "\n").encode())
        await writer.drain()
        writer.close()
        await asyncio.sleep(0.05)
        assert collector.events == []

        # Correct token → processed.
        _, writer = await asyncio.open_connection("127.0.0.1", recv.port)
        writer.write((json.dumps(valid_payload(token="s3cret")) + "\n").encode())
        await writer.drain()
        await asyncio.wait_for(collector.got_event.wait(), timeout=2)
        assert len(collector.events) == 1
        writer.close()
    finally:
        await recv.stop()


# -- _parse_payload -------------------------------------------------------------


def make_receiver():
    async def noop(*args):
        pass

    return HookReceiver("127.0.0.1", 0, noop)


def test_parse_valid_payload():
    event, pid, shell = make_receiver()._parse_payload(json.dumps(valid_payload()))
    assert event.cmd == "make build"
    assert event.exit_code == 1
    assert event.cwd == "/home/user/project"
    assert event.duration_ms == 4231
    assert event.ts == pytest.approx(1718000000.123)
    assert pid == 4242 and shell == "zsh"


def test_parse_missing_cmd():
    payload = valid_payload()
    del payload["cmd"]
    with pytest.raises(ValueError, match="cmd"):
        make_receiver()._parse_payload(json.dumps(payload))


def test_parse_exit_out_of_range():
    with pytest.raises(ValueError, match="exit"):
        make_receiver()._parse_payload(json.dumps(valid_payload(exit=256)))


def test_parse_long_cmd_truncated():
    event, _, _ = make_receiver()._parse_payload(
        json.dumps(valid_payload(cmd="x" * 5000))
    )
    assert len(event.cmd) == 4096


def test_parse_negative_duration():
    with pytest.raises(ValueError, match="duration"):
        make_receiver()._parse_payload(json.dumps(valid_payload(duration=-1)))


def test_parse_non_json_raises():
    with pytest.raises(json.JSONDecodeError):
        make_receiver()._parse_payload("not json at all")


# -- socket integration ------------------------------------------------------------


async def test_event_received(receiver):
    recv, collector = receiver
    reader, writer = await connect(recv)
    writer.write((json.dumps(valid_payload()) + "\n").encode())
    await writer.drain()
    writer.close()
    await asyncio.wait_for(collector.got_event.wait(), timeout=2)
    event, pid, shell = collector.events[0]
    assert event.cmd == "make build"
    assert pid == 4242


async def test_partial_payload_buffered(receiver):
    recv, collector = receiver
    line = (json.dumps(valid_payload()) + "\n").encode()
    reader, writer = await connect(recv)
    writer.write(line[:20])
    await writer.drain()
    await asyncio.sleep(0.05)  # ensure two separate TCP segments
    writer.write(line[20:])
    await writer.drain()
    writer.close()
    await asyncio.wait_for(collector.got_event.wait(), timeout=2)
    assert collector.events[0][0].cmd == "make build"


async def test_disconnect_mid_payload_is_clean(receiver):
    recv, collector = receiver
    reader, writer = await connect(recv)
    writer.write(b'{"cmd": "incompl')  # no newline, then vanish
    await writer.drain()
    writer.close()
    await asyncio.sleep(0.1)
    assert collector.events == []  # partial buffer discarded, no crash

    # receiver still works for the next client
    reader, writer = await connect(recv)
    writer.write((json.dumps(valid_payload()) + "\n").encode())
    await writer.drain()
    writer.close()
    await asyncio.wait_for(collector.got_event.wait(), timeout=2)


async def test_malformed_line_skipped_then_valid_processed(receiver):
    recv, collector = receiver
    reader, writer = await connect(recv)
    writer.write(b"this is not json\n")
    writer.write((json.dumps(valid_payload()) + "\n").encode())
    await writer.drain()
    writer.close()
    await asyncio.wait_for(collector.got_event.wait(), timeout=2)
    assert len(collector.events) == 1


async def test_query_streams_response_back(receiver):
    recv, collector = receiver
    reader, writer = await connect(recv)
    query = {"type": "query", "cmd": "?? why fail", "cwd": "/proj"}
    writer.write((json.dumps(query) + "\n").encode())
    await writer.drain()
    response = await asyncio.wait_for(reader.read(), timeout=2)  # until EOF
    writer.close()
    assert response.decode() == "ANSWER for ?? why fail"
    assert collector.queries == [("?? why fail", "/proj")]
