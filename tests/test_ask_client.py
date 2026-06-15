"""Tests for the `terminalghost ask` client-side rendering helpers."""

from __future__ import annotations

import dataclasses

from terminalghost.config.loader import Config
from terminalghost.cli.client import (
    _answering_model,
    _iter_socket_text,
    _render_answer_plain,
    _render_answer_rich,
)
from terminalghost.ui import get_console


class FakeSocket:
    """Minimal socket stand-in yielding preset byte chunks then EOF."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.sent: list[bytes] = []

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, _t):
        pass

    def recv(self, _n):
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_answering_model_per_backend():
    cfg = Config()
    assert _answering_model(cfg) == cfg.llm.ollama.model
    cfg_claude = dataclasses.replace(cfg, llm=dataclasses.replace(cfg.llm, backend="claude"))
    assert _answering_model(cfg_claude) == cfg.llm.claude.model


def test_iter_socket_text_decodes_until_eof():
    sock = FakeSocket([b"hel", b"lo \xe2\x96\xb6", b""])  # includes a UTF-8 ▶
    assert "".join(_iter_socket_text(sock)) == "hello ▶"


def test_render_answer_plain_writes_header_and_body(capsys):
    sock = FakeSocket([b"the fix is X", b""])
    _render_answer_plain(sock)
    out = capsys.readouterr().out
    assert "TerminalGhost" in out
    assert "the fix is X" in out


def test_render_answer_rich_renders_markdown_and_footer():
    sock = FakeSocket([b"# Title\n", b"some **answer**", b""])
    console = get_console(color="always", record=True)
    cfg = Config()
    _render_answer_rich(cfg, sock, console=console)
    out = console.export_text()
    assert "TerminalGhost" in out  # client-rendered header
    assert "Title" in out
    assert "answer" in out
    assert cfg.llm.ollama.model in out  # footer shows the model


def test_render_answer_rich_handles_empty_response():
    sock = FakeSocket([b""])
    console = get_console(color="always", record=True)
    _render_answer_rich(Config(), sock, console=console)
    out = console.export_text()
    assert "no response" in out
