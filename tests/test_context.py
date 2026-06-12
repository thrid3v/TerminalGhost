# tests.test_context — tests for terminalghost.context.assembler

import dataclasses
import time

import pytest

from terminalghost.config.loader import Config
from terminalghost.context.assembler import ContextAssembler
from terminalghost.storage.db import CommandEvent, Database


def make_event(cmd, exit_code=0, output=None, cwd="/proj"):
    return CommandEvent(
        session_id=1,
        ts=time.time(),
        cwd=cwd,
        cmd=cmd,
        exit_code=exit_code,
        duration_ms=10,
        output=output,
    )


@pytest.fixture
def db():
    database = Database(":memory:", history_size=200)
    database.open()
    database.start_session(shell_pid=1, shell="test")  # session_id 1 for FK
    yield database
    database.close()


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')")
    (tmp_path / ".git").mkdir()  # must be ignored in the tree
    (tmp_path / "Makefile").write_text("all:\n\ttrue")
    return tmp_path


def test_assemble_includes_all_sections(db, project):
    db.insert_command(make_event("make build", exit_code=1, output="Error 1"))
    db.insert_command(make_event("cat Makefile"))
    assembler = ContextAssembler(db, Config())
    prompt = assembler.assemble(str(project), "why did it fail")

    assert "FAILING command" in prompt
    assert "make build" in prompt
    assert "Recent commands" in prompt
    assert "cat Makefile" in prompt
    assert "Makefile" in prompt  # directory tree
    assert "src/" in prompt
    assert "User note" in prompt and "why did it fail" in prompt


def test_assemble_without_error_or_note(db, project):
    db.insert_command(make_event("ls"))
    assembler = ContextAssembler(db, Config())
    prompt = assembler.assemble(str(project))
    assert "FAILING" not in prompt
    assert "User note" not in prompt


def test_assemble_empty_history(db, project):
    assembler = ContextAssembler(db, Config())
    prompt = assembler.assemble(str(project))
    assert "Recent commands" not in prompt
    assert "Current directory" in prompt


def test_missing_cwd_is_handled(db):
    assembler = ContextAssembler(db, Config())
    prompt = assembler.assemble("/definitely/not/a/real/dir-xyz")
    assert "(directory not found)" in prompt


def test_token_budget_trims_oldest_commands(db, project):
    config = Config()
    config = dataclasses.replace(
        config, context=dataclasses.replace(config.context, token_budget=300)
    )
    for i in range(50):
        db.insert_command(make_event(f"command-number-{i:03d} " + "x" * 80))
    assembler = ContextAssembler(db, config)
    prompt = assembler.assemble(str(project))
    assert assembler._estimate_tokens(prompt) <= 300 + 50  # small tolerance
    # newest commands survive, oldest are trimmed
    assert "command-number-000" not in prompt


def test_ignored_dirs_not_in_tree(db, project):
    assembler = ContextAssembler(db, Config())
    tree = assembler._format_directory_tree(str(project))
    assert ".git" not in tree
    assert "Makefile" in tree


def test_tree_depth_respected(db, tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("x")
    config = Config()
    config = dataclasses.replace(
        config, context=dataclasses.replace(config.context, tree_depth=2)
    )
    assembler = ContextAssembler(db, config)
    tree = assembler._format_directory_tree(str(tmp_path))
    assert "a/" in tree and "b/" in tree
    assert "leaf.txt" not in tree


def test_tree_entry_cap(db, tmp_path):
    for i in range(210):
        (tmp_path / f"file-{i:04d}.txt").write_text("x")
    assembler = ContextAssembler(db, Config())
    tree = assembler._format_directory_tree(str(tmp_path))
    assert "(truncated)" in tree
    assert len(tree.splitlines()) <= 202


def test_estimate_tokens(db):
    assembler = ContextAssembler(db, Config())
    assert assembler._estimate_tokens("") == 0
    estimate = assembler._estimate_tokens("a" * 400)
    assert 80 <= estimate <= 120  # ~100, within 20%


def test_history_format_marks_error_and_orders_oldest_first(db):
    db.insert_command(make_event("first-cmd"))
    db.insert_command(make_event("bad-cmd", exit_code=1))
    db.insert_command(make_event("last-cmd"))
    assembler = ContextAssembler(db, Config())
    commands = list(reversed(db.get_recent_commands()))
    section = assembler._format_command_history(commands, db.get_last_error())
    assert section.index("first-cmd") < section.index("bad-cmd") < section.index("last-cmd")
    failed_line = next(line for line in section.splitlines() if "bad-cmd" in line)
    assert "FAILED" in failed_line
