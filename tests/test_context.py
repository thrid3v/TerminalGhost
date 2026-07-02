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
    db.insert_command(
        make_event("make build", exit_code=1, output="Error 1", cwd=str(project))
    )
    db.insert_command(make_event("cat Makefile", cwd=str(project)))
    assembler = ContextAssembler(db, Config())
    prompt = assembler.assemble(str(project), "why did it fail")

    assert "FAILING command" in prompt
    assert "make build" in prompt
    assert "Recent commands" in prompt
    assert "cat Makefile" in prompt
    assert "Makefile" in prompt  # directory tree
    assert "src/" in prompt
    assert "User note" in prompt and "why did it fail" in prompt


def test_assemble_scopes_error_to_cwd(db, project):
    # A failure in another directory must NOT surface for a ?? asked here.
    db.insert_command(
        make_event("make build", exit_code=1, output="Error 1", cwd="/elsewhere")
    )
    assembler = ContextAssembler(db, Config())
    prompt = assembler.assemble(str(project))
    assert "FAILING" not in prompt


def test_assemble_includes_pasted_output(db, project):
    assembler = ContextAssembler(db, Config())
    prompt = assembler.assemble(str(project), pasted="ERROR: boom at line 5")
    assert "Output to explain" in prompt
    assert "boom at line 5" in prompt


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


# -- recap prompt --------------------------------------------------------------


def test_assemble_recap_includes_commands_in_window(db):
    old = make_event("ancient-cmd")
    old.ts = time.time() - 9999
    db.insert_command(old)
    db.insert_command(make_event("recent-fail", exit_code=1))
    db.insert_command(make_event("recent-fix"))
    assembler = ContextAssembler(db, Config())
    prompt = assembler.assemble_recap(since=time.time() - 3600)
    assert prompt is not None
    assert "Summarize this terminal session" in prompt
    assert "recent-fail" in prompt and "recent-fix" in prompt
    assert "ancient-cmd" not in prompt


def test_assemble_recap_none_when_empty(db):
    assembler = ContextAssembler(db, Config())
    assert assembler.assemble_recap(since=time.time() - 3600) is None


def test_assemble_recap_respects_budget(db):
    config = Config()
    config = dataclasses.replace(
        config, context=dataclasses.replace(config.context, token_budget=200)
    )
    for i in range(60):
        db.insert_command(make_event(f"recap-cmd-{i:03d} " + "x" * 60))
    assembler = ContextAssembler(db, config)
    prompt = assembler.assemble_recap(since=time.time() - 3600)
    assert assembler._estimate_tokens(prompt) <= 250
    assert "recap-cmd-059" in prompt  # newest survives
    assert "recap-cmd-000" not in prompt


# -- auto project context -----------------------------------------------------


def test_project_context_package_json(db, tmp_path):
    import json

    (tmp_path / "package.json").write_text(json.dumps({
        "engines": {"node": ">=20"},
        "dependencies": {"react": "^18.2.0", "left-pad": "1.0.0"},
    }), encoding="utf-8")
    assembler = ContextAssembler(db, Config())
    prompt = assembler.assemble(str(tmp_path))
    assert "## Project" in prompt
    assert "react@^18.2.0" in prompt
    assert "node" in prompt


def test_project_context_pyproject(db, tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.11"\n'
        'dependencies = ["httpx>=0.27", "rich>=13.7"]\n',
        encoding="utf-8",
    )
    assembler = ContextAssembler(db, Config())
    prompt = assembler.assemble(str(tmp_path))
    assert "requires-python >=3.11" in prompt
    assert "httpx>=0.27" in prompt


def test_project_context_requirements_capped(db, tmp_path):
    reqs = "\n".join(f"pkg{i}==1.0" for i in range(15))
    (tmp_path / "requirements.txt").write_text(reqs, encoding="utf-8")
    assembler = ContextAssembler(db, Config())
    prompt = assembler.assemble(str(tmp_path))
    assert "pkg0==1.0" in prompt
    assert "(+3 more)" in prompt


def test_project_context_disabled_by_config(db, tmp_path):
    (tmp_path / "requirements.txt").write_text("flask==3.0\n", encoding="utf-8")
    config = Config()
    config = dataclasses.replace(
        config, context=dataclasses.replace(config.context, project_context=False)
    )
    assembler = ContextAssembler(db, config)
    prompt = assembler.assemble(str(tmp_path))
    assert "## Project" not in prompt


def test_project_context_survives_malformed_manifest(db, tmp_path):
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")
    assembler = ContextAssembler(db, Config())
    prompt = assembler.assemble(str(tmp_path))  # must not raise
    assert "Current directory" in prompt


def test_git_summary_parses(monkeypatch):
    from terminalghost.context import assembler as mod

    class FakeResult:
        def __init__(self, out):
            self.returncode = 0
            self.stdout = out

    def fake_run(argv, **kwargs):
        if "rev-parse" in argv:
            return FakeResult("feat/thing\n")
        return FakeResult(" M a.py\n M b.py\n")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod._git_summary("/repo") == "git: branch feat/thing, 2 changed file(s)"


def test_git_summary_none_outside_repo(monkeypatch):
    from terminalghost.context import assembler as mod

    class FakeResult:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: FakeResult())
    assert mod._git_summary("/not-a-repo") is None


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
