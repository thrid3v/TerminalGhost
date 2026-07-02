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
# Cap on text sent with `explain` (the daemon's token budget trims further).
EXPLAIN_MAX_CHARS = 8000


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
    """Send a ?? query to the daemon and render the streamed answer."""
    cmd = "??" if not text else f"?? {text}"
    payload = {"type": "query", "cmd": cmd, "cwd": os.getcwd()}
    return _run_query(config, payload, copy=copy)


def _cmd_explain(config: "Config", file: str | None) -> int:
    """Explain piped output or a file: `make 2>&1 | tg explain` / `tg explain log`."""
    text = _read_input(file)
    if not text.strip():
        print(
            "terminalghost explain: nothing to explain — pipe output or pass a file",
            file=sys.stderr,
        )
        return 2
    payload = {
        "type": "query",
        "cmd": "?? explain this output",
        "cwd": os.getcwd(),
        "context": text[:EXPLAIN_MAX_CHARS],
    }
    return _run_query(config, payload)


def _read_input(file: str | None) -> str:
    if file:
        try:
            with open(file, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError as exc:
            print(f"terminalghost explain: cannot read {file}: {exc}", file=sys.stderr)
            return ""
    try:
        data = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read()
    except (OSError, ValueError):
        return ""
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data


def _run_query(config: "Config", payload: dict, copy: bool = False) -> int:
    """Send a query payload to the daemon and render the streamed answer.

    The client runs in the user's terminal, so writing to stdout is what makes
    the answer visible. With a TTY it renders a live-markdown card + an inline
    apply action bar; otherwise it streams plain text.
    """
    # Windows consoles may default to cp1252 which can't encode what the LLM
    # emits; force UTF-8 output.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    from terminalghost.ui import resolve_color

    payload = {**payload, "token": read_token()}
    line = (json.dumps(payload) + "\n").encode("utf-8")
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
            sock.sendall(line)
            # Generous idle timeout: a cold model can take a while to first token,
            # but a wedged daemon shouldn't freeze the prompt forever.
            sock.settimeout(ASK_IDLE_TIMEOUT)
            if use_rich:
                answer = _render_answer_rich(config, sock)
            else:
                answer = _render_answer_plain(sock)
        if copy and answer.strip():
            _copy_to_clipboard(answer.strip(), config)
        # Close the loop: offer to run/copy/edit the suggested command inline.
        if use_rich and _stdin_is_tty():
            _post_answer_actions(config)
    except KeyboardInterrupt:
        print()
        return 130
    except socket.timeout:
        from terminalghost.ui import console_for

        console_for(config, stderr=True).print(
            "[tg.error]TerminalGhost timed out waiting for the daemon.[/]"
        )
        return 1
    except (ConnectionError, OSError):
        _print_unreachable(config)
        return 1
    return 0


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _post_answer_actions(config: "Config") -> None:
    """Show the suggested command and an inline action bar; act on one keypress."""
    from rich.panel import Panel
    from rich.text import Text

    from terminalghost.cli.risk import assess
    from terminalghost.ui import console_for

    command = _fetch_suggestion(config)
    if not command:
        return
    console = console_for(config)
    warning = assess(command)
    console.print(
        Panel(
            Text(command, style="tg.cmd"),
            title="⚠ run this (risky)" if warning else "▶ run this",
            title_align="left",
            border_style="tg.warn" if warning else "tg.glow",
            padding=(0, 1),
            expand=False,
        )
    )
    if warning:
        console.print(f"  [tg.warn]⚠ This command {warning}.[/]")
    console.print(
        "  [tg.key]R[/] run   [tg.key]C[/] copy   [tg.key]E[/] edit"
        "   [tg.muted]· any other key dismiss[/]"
    )
    try:
        key = _read_key().lower()
    except (OSError, EOFError, KeyboardInterrupt):
        print()
        return
    if key == "r":
        print()
        if _run_confirmed(config, command):
            _cmd_exec(config, command)
    elif key == "c":
        _copy_to_clipboard(command, config)
    elif key == "e":
        edited = _edit_command(command).strip()
        if edited and _run_confirmed(config, edited):
            _cmd_exec(config, edited)
    else:
        print()  # dismiss — leave a clean line


def _run_confirmed(config: "Config", command: str) -> bool:
    """Gate risky commands behind a typed confirmation. True = go ahead.

    Safe commands pass straight through — one-keypress speed is the point.
    Destructive-looking ones (see cli.risk) must be confirmed by typing "yes".
    """
    from terminalghost.cli.risk import assess
    from terminalghost.ui import console_for

    warning = assess(command)
    if warning is None:
        return True
    console = console_for(config)
    console.print(f"[tg.warn]⚠ This command {warning}.[/]")
    try:
        answer = input('Type "yes" to run it: ').strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if answer != "yes":
        console.print("[tg.muted]Skipped.[/]")
        return False
    return True


def _read_key() -> str:
    """Read one keypress from a TTY (no Enter needed). Cross-platform."""
    if os.name == "nt":
        import msvcrt

        return msvcrt.getwch()
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _edit_command(command: str) -> str:
    """Let the user edit `command` before running. Prefilled on POSIX."""
    print()
    if os.name != "nt":
        try:
            import readline

            readline.set_startup_hook(lambda: readline.insert_text(command))
            try:
                return input("edit ▸ ")
            finally:
                readline.set_startup_hook(None)
        except Exception:  # noqa: BLE001 — readline unavailable; fall through
            pass
    print(f"current: {command}")
    edited = input("edit ▸ ")
    return edited or command


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
    """Run the command TerminalGhost last suggested, after confirmation.

    Destructive-looking suggestions (see cli.risk) always show a plain-language
    warning and, unless --yes was given, need a typed "yes" instead of a
    one-letter confirmation.
    """
    from rich.markup import escape

    from terminalghost.cli.risk import assess
    from terminalghost.ui import console_for

    console = console_for(config)
    command = _fetch_suggestion(config)
    if not command:
        console.print(
            "[tg.muted]Nothing to apply yet — ask a [tg.key]??[/] first "
            "(works best with [tg.key]?? fix[/]).[/]"
        )
        return 0

    console.print(f"[tg.header]Suggested:[/] [tg.cmd]{escape(command)}[/]")
    warning = assess(command)
    if warning:
        console.print(f"[tg.warn]⚠ This command {warning}.[/]")
    if not assume_yes:
        if not sys.stdin.isatty():
            console.print(
                "[tg.warn]Refusing to run without confirmation.[/] "
                "Re-run with [tg.key]--yes[/]."
            )
            return 1
        prompt = 'Type "yes" to run it: ' if warning else "Run it? [y/N] "
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 130
        accepted = ("yes",) if warning else ("y", "yes")
        if answer not in accepted:
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

    from terminalghost.ui import console_for
    from terminalghost.ui.theme import GHOST_GLYPH

    line = text.splitlines()[0][:200]
    console = console_for(config)
    console.print(f"[tg.muted]{GHOST_GLYPH} hint:[/] [tg.muted]{escape(line)}[/]")
    return 0


def _print_unreachable(config: "Config") -> None:
    from terminalghost.ui import console_for

    console = console_for(config, stderr=True)
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
    from rich.panel import Panel
    from rich.spinner import Spinner
    from rich.text import Text

    from terminalghost.ui import console_for
    from terminalghost.ui.theme import GHOST_GLYPH

    if console is None:
        console = console_for(config)

    title = f"{GHOST_GLYPH} TerminalGhost"

    def card(content):
        # The answer lives in a bordered card so it reads as one intentional
        # reply rather than blending into shell scrollback.
        return Panel(
            content, title=title, title_align="left",
            border_style="tg.glow", padding=(0, 1),
        )

    start = time.monotonic()
    buf: list[str] = []
    spinner = Spinner("dots", text=Text(" thinking…", style="tg.muted"))
    with Live(
        card(spinner),
        console=console,
        refresh_per_second=12,
        vertical_overflow="visible",
    ) as live:
        for chunk in _iter_socket_text(sock):
            buf.append(chunk)
            live.update(card(Markdown("".join(buf).strip())))
        if not buf:
            live.update(card(Text("(no response)", style="tg.muted")))

    elapsed = time.monotonic() - start
    footer = Text(
        f"  {config.llm.backend} · {_answering_model(config)} · {elapsed:.1f}s",
        style="tg.footer",
    )
    console.print(footer)
    return "".join(buf)


def _copy_to_clipboard(text: str, config: "Config") -> None:
    """Best-effort copy to the system clipboard, with a small confirmation."""
    import shutil
    import subprocess

    from terminalghost.ui import console_for

    data = text.encode("utf-8", errors="replace")
    ok = False
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=data, check=True)
            ok = True
        elif os.name == "nt":
            # clip.exe treats input as the ANSI codepage unless it sees a
            # UTF-16LE BOM — encode UTF-16 so non-ASCII survives.
            subprocess.run(["clip"], input=text.encode("utf-16"), check=True)
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

    console = console_for(config)
    if ok:
        console.print("[tg.muted]copied to clipboard[/]")
    else:
        console.print("[tg.muted](could not access a clipboard tool)[/]")
