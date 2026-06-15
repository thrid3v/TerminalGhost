# terminalghost.cli.client
#
# The client side of the daemon protocol: the commands that run in the user's
# terminal and talk to the daemon over the loopback socket (ask / hint / exec /
# apply, plus their rendering helpers). Split out of daemon.process so the
# daemon (server) and the clients stay separate concerns.

from __future__ import annotations

import json
import os
import socket
import sys
import time
from typing import TYPE_CHECKING

from terminalghost.runtime import effective_port, read_token

if TYPE_CHECKING:
    from terminalghost.config.loader import Config

# Client read timeout for `ask`: large enough for a cold model's first token,
# small enough that a wedged daemon doesn't hang the prompt forever.
ASK_IDLE_TIMEOUT = 300.0
# Proactive hint: short read timeout so a cold/slow model just yields no hint
# rather than stalling the user's prompt.
HINT_TIMEOUT = 12.0
# Cap on captured output the `exec` wrapper keeps (tail); the daemon trims more.
EXEC_CAPTURE_BYTES = 64 * 1024


def _answering_model(config: "Config") -> str:
    """The model name the configured backend will answer with (for the footer)."""
    backend = config.llm.backend
    if backend == "ollama":
        return config.llm.ollama.model
    if backend == "claude":
        return config.llm.claude.model
    if backend == "openai":
        return config.llm.openai.model
    return backend


def _iter_socket_text(sock: socket.socket):
    """Yield decoded text chunks from the daemon's streamed answer until EOF."""
    while True:
        data = sock.recv(4096)
        if not data:
            return
        yield data.decode("utf-8", errors="replace")


def _cmd_ask(config: "Config", text: str, copy: bool = False) -> int:
    """Send a ?? query to the daemon and render the streamed answer.

    This is the client end of the response channel: it runs in the user's
    terminal, so writing to its stdout is what makes the answer visible. With
    a TTY it renders the answer as live markdown with a spinner + footer;
    otherwise it streams plain text (pipes, NO_COLOR, ui.markdown = false).
    """
    # Windows consoles may default to a legacy codepage (cp1252) that cannot
    # encode characters the LLM (or our header) emits; force UTF-8 output.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass  # non-reconfigurable stream (e.g. pipe wrapper) — best effort

    from terminalghost.ui import resolve_color

    cmd = "??" if not text else f"?? {text}"
    payload = json.dumps(
        {"type": "query", "cmd": cmd, "cwd": os.getcwd(), "token": read_token()}
    ) + "\n"
    use_rich = config.ui.markdown and resolve_color(config.ui.color, sys.stdout)

    try:
        sock = socket.create_connection(
            (config.general.host, effective_port(config)), timeout=5
        )
    except (ConnectionRefusedError, socket.timeout, OSError):
        _print_unreachable(config)
        return 1

    try:
        with sock:
            sock.sendall(payload.encode("utf-8"))
            # Generous idle timeout: a cold model can take a while to first
            # token, but a wedged daemon shouldn't freeze the prompt forever.
            sock.settimeout(ASK_IDLE_TIMEOUT)
            if use_rich:
                answer = _render_answer_rich(config, sock)
            else:
                answer = _render_answer_plain(sock)
        if copy and answer.strip():
            _copy_to_clipboard(answer.strip(), config)
    except KeyboardInterrupt:
        print()
        return 130
    except socket.timeout:
        from terminalghost.ui import get_console

        get_console(color=config.ui.color, stderr=True).print(
            "[tg.error]TerminalGhost timed out waiting for the daemon.[/]"
        )
        return 1
    except (ConnectionError, OSError):
        _print_unreachable(config)
        return 1
    return 0


def _write_raw(data: bytes) -> None:
    """Write bytes to stdout, tolerating streams without a binary buffer."""
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        buf.write(data)
        buf.flush()
    else:
        sys.stdout.write(data.decode("utf-8", errors="replace"))
        sys.stdout.flush()


def _build_cmdline(argv: list[str]) -> str:
    """Quote argv into a shell command line, preserving args that contain spaces.

    We run via the shell (so Windows .cmd shims like npm and shell features work),
    but quote per-platform so `tgr pytest -k "foo bar"` isn't re-split.
    """
    import subprocess

    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    import shlex

    return shlex.join(argv)


def _cmd_exec(config: "Config", cmdline: str) -> int:
    """Run a command line, mirror its output live, and ship the captured output
    to the daemon so a following ?? sees the real error text. Cross-platform."""
    import subprocess

    if not cmdline.strip():
        print("usage: terminalghost exec <command> [args...]", file=sys.stderr)
        return 2
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmdline, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
    except OSError as exc:
        print(f"terminalghost exec: cannot run command: {exc}", file=sys.stderr)
        return 1

    captured = bytearray()
    fd = proc.stdout.fileno()  # type: ignore[union-attr]
    try:
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            _write_raw(chunk)
            captured += chunk
            if len(captured) > EXEC_CAPTURE_BYTES:
                del captured[:-EXEC_CAPTURE_BYTES]
    except KeyboardInterrupt:
        proc.terminate()
    rc = proc.wait()
    duration = int((time.monotonic() - start) * 1000)
    text = captured.decode("utf-8", errors="replace")
    _send_run_event(config, cmdline, rc, duration, text)
    return rc


def _request(config: "Config", obj: dict, connect_timeout: float = 3.0,
             read_timeout: float = 5.0) -> str:
    """Send one JSON request to the daemon and return the full text response.

    Returns "" on any connection problem — callers treat that as "no answer".
    """
    payload = (json.dumps({**obj, "token": read_token()}) + "\n").encode("utf-8")
    data = b""
    try:
        with socket.create_connection(
            (config.general.host, effective_port(config)), timeout=connect_timeout
        ) as sock:
            sock.sendall(payload)
            sock.settimeout(read_timeout)
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
    except (OSError, KeyboardInterrupt):
        return ""
    return data.decode("utf-8", errors="replace").strip()


def _fetch_suggestion(config: "Config") -> str:
    """Ask the daemon for the last suggested command (empty string if none)."""
    return _request(config, {"type": "suggestion"})


def _cmd_apply(config: "Config", assume_yes: bool = False) -> int:
    """Run the command TerminalGhost last suggested, after confirmation."""
    from rich.markup import escape

    from terminalghost.ui import get_console

    console = get_console(color=config.ui.color)
    command = _fetch_suggestion(config)
    if not command:
        console.print(
            "[tg.muted]Nothing to apply yet — ask a [tg.key]??[/] first "
            "(works best with [tg.key]?? fix[/]).[/]"
        )
        return 0

    console.print(f"[tg.header]Suggested:[/] [tg.cmd]{escape(command)}[/]")
    if not assume_yes:
        if not sys.stdin.isatty():
            console.print(
                "[tg.warn]Refusing to run without confirmation.[/] "
                "Re-run with [tg.key]--yes[/]."
            )
            return 1
        try:
            answer = input("Run it? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 130
        if answer not in ("y", "yes"):
            console.print("[tg.muted]Skipped.[/]")
            return 0
    # The suggestion is already a shell-ready command line; run it as-is.
    return _cmd_exec(config, command)


def _send_run_event(config: "Config", cmd: str, exit_code: int, duration_ms: int,
                    output: str) -> None:
    """Fire-and-forget a run-sourced command event (best-effort)."""
    payload = json.dumps({
        "cmd": cmd,
        "exit": exit_code & 0xFF,
        "cwd": os.getcwd(),
        "duration": max(0, duration_ms),
        "ts": time.time(),
        "shell": "exec",
        "source": "run",
        "output": output,
        "token": read_token(),
    }) + "\n"
    try:
        with socket.create_connection(
            (config.general.host, effective_port(config)), timeout=1
        ) as sock:
            sock.sendall(payload.encode("utf-8"))
    except OSError:
        pass  # daemon not running — capture is best-effort, never block the user


def _cmd_hint(config: "Config") -> int:
    """Print a one-line proactive hint for the last failure (silent otherwise).

    Runs from the shell prompt when TG_HINTS is enabled, so it must never error
    or hang: any problem (daemon down, slow model, no error) yields no output.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    text = _request(
        config, {"type": "hint", "cwd": os.getcwd()},
        connect_timeout=2, read_timeout=HINT_TIMEOUT,
    )
    if not text:
        return 0  # daemon down / no hint — never disrupt the prompt
    from rich.markup import escape

    from terminalghost.ui import get_console
    from terminalghost.ui.theme import GHOST_GLYPH

    line = text.splitlines()[0][:200]
    console = get_console(color=config.ui.color)
    console.print(f"[tg.muted]{GHOST_GLYPH} hint:[/] [tg.muted]{escape(line)}[/]")
    return 0


def _print_unreachable(config: "Config") -> None:
    from terminalghost.ui import get_console

    console = get_console(color=config.ui.color, stderr=True)
    console.print(
        "[tg.error]TerminalGhost daemon is not reachable.[/] "
        "Start it with: [tg.key]terminalghost start[/]"
    )


def _render_answer_plain(sock: socket.socket) -> str:
    """Stream the raw answer to stdout (pipe / no-color path). Returns the text."""
    sys.stdout.write("TerminalGhost ▶\n\n")
    sys.stdout.flush()
    collected: list[str] = []
    for chunk in _iter_socket_text(sock):
        collected.append(chunk)
        sys.stdout.write(chunk)
        sys.stdout.flush()
    sys.stdout.write("\n")
    sys.stdout.flush()
    return "".join(collected)


def _render_answer_rich(config: "Config", sock: socket.socket, console=None) -> str:
    """Render the streamed answer as live markdown with a spinner + footer.

    Returns the plain answer text (for --copy)."""
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.spinner import Spinner
    from rich.text import Text

    from terminalghost.ui import get_console
    from terminalghost.ui.theme import GHOST_GLYPH

    if console is None:
        console = get_console(color=config.ui.color)

    header = Text()
    header.append(f"{GHOST_GLYPH} ", style="tg.accent")
    header.append("TerminalGhost", style="tg.brand")
    console.print(header)

    start = time.monotonic()
    buf: list[str] = []
    spinner = Spinner("dots", text=Text(" thinking…", style="tg.muted"))
    with Live(
        spinner,
        console=console,
        refresh_per_second=12,
        vertical_overflow="visible",
    ) as live:
        for chunk in _iter_socket_text(sock):
            buf.append(chunk)
            live.update(Markdown("".join(buf).strip()))
        if not buf:
            live.update(Text("(no response)", style="tg.muted"))

    elapsed = time.monotonic() - start
    footer = Text(
        f"{config.llm.backend} · {_answering_model(config)} · {elapsed:.1f}s",
        style="tg.footer",
    )
    console.print(footer)
    return "".join(buf)


def _copy_to_clipboard(text: str, config: "Config") -> None:
    """Best-effort copy to the system clipboard, with a small confirmation."""
    import shutil
    import subprocess

    from terminalghost.ui import get_console

    data = text.encode("utf-8", errors="replace")
    ok = False
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=data, check=True)
            ok = True
        elif os.name == "nt":
            subprocess.run(["clip"], input=data, check=True)
            ok = True
        else:
            for tool in (
                ["wl-copy"],
                ["xclip", "-selection", "clipboard"],
                ["xsel", "--clipboard", "--input"],
            ):
                if shutil.which(tool[0]):
                    subprocess.run(tool, input=data, check=True)
                    ok = True
                    break
    except (OSError, subprocess.CalledProcessError):
        ok = False

    console = get_console(color=config.ui.color)
    if ok:
        console.print("[tg.muted]copied to clipboard[/]")
    else:
        console.print("[tg.muted](could not access a clipboard tool)[/]")
