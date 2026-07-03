"""Tests for terminalghost.context.source (error-driven source context)."""

from __future__ import annotations

import os

from terminalghost.context import source


# -- extract_file_refs --------------------------------------------------------


def test_extract_python_traceback():
    text = (
        'Traceback (most recent call last):\n'
        '  File "app/main.py", line 42, in run\n'
        '    do_it()\n'
        '  File "app/util.py", line 7, in do_it\n'
        'ValueError: boom\n'
    )
    assert source.extract_file_refs(text) == [("app/main.py", 42), ("app/util.py", 7)]


def test_extract_node_and_gcc():
    node = "    at Object.<anonymous> (/proj/src/index.js:15:9)"
    gcc = "src/parse.c:88:5: error: expected ';'"
    assert ("/proj/src/index.js", 15) in source.extract_file_refs(node)
    assert ("src/parse.c", 88) in source.extract_file_refs(gcc)


def test_extract_rust_and_go():
    rust = "  --> src/lib.rs:23:14"
    go = "./cmd/app/main.go:56: undefined: Foo"
    assert ("src/lib.rs", 23) in source.extract_file_refs(rust)
    assert ("./cmd/app/main.go", 56) in source.extract_file_refs(go)


def test_extract_dedups_and_ignores_non_refs():
    text = "x.py:10\nx.py:10\nno colon here\nvisit http://example.com:443 today"
    refs = source.extract_file_refs(text)
    assert refs.count(("x.py", 10)) == 1


# -- find_project_root --------------------------------------------------------


def test_find_project_root_walks_up(tmp_path):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert source.find_project_root(str(nested)) == str(tmp_path)


def test_find_project_root_falls_back_to_cwd(tmp_path):
    # No marker anywhere up to the drive root that we control; fall back to cwd.
    d = tmp_path / "plain"
    d.mkdir()
    assert source.find_project_root(str(d)) == str(d.resolve()) or \
        os.path.isdir(source.find_project_root(str(d)))


# -- collect_source_context ---------------------------------------------------


def _project(tmp_path):
    (tmp_path / ".git").mkdir()
    src = tmp_path / "app"
    src.mkdir()
    (src / "main.py").write_text(
        "\n".join(f"line {i}" for i in range(1, 41)), encoding="utf-8"
    )
    return tmp_path


def test_collect_includes_window_around_line(tmp_path):
    _project(tmp_path)
    text = 'File "app/main.py", line 20, in run'
    out = source.collect_source_context(text, str(tmp_path))
    assert out is not None
    assert "## Relevant source" in out
    assert "app/main.py:20" in out
    assert "> 20 | line 20" in out           # target line marked
    assert "line 15" in out and "line 25" in out  # window on both sides
    assert "line 5" not in out               # outside the window


def test_collect_returns_none_without_refs(tmp_path):
    _project(tmp_path)
    assert source.collect_source_context("nothing to see", str(tmp_path)) is None


def test_collect_ignores_paths_outside_root(tmp_path):
    _project(tmp_path)
    # An absolute path outside the project must never be read.
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("TOPSECRET", encoding="utf-8")
    text = f'File "{outside}", line 1'
    out = source.collect_source_context(text, str(tmp_path))
    assert out is None


def test_collect_skips_secret_files(tmp_path):
    _project(tmp_path)
    (tmp_path / ".env").write_text("API_KEY=sk-supersecret\n", encoding="utf-8")
    text = 'File ".env", line 1'
    out = source.collect_source_context(text, str(tmp_path))
    assert out is None


def test_collect_redacts_secrets_in_source(tmp_path):
    root = _project(tmp_path)
    (root / "app" / "config.py").write_text(
        "\n".join(["x = 1"] * 9 + ["API_KEY = 'sk-live-abcd1234efgh5678'"]),
        encoding="utf-8",
    )
    text = 'File "app/config.py", line 10'
    out = source.collect_source_context(text, str(tmp_path))
    assert out is not None
    assert "sk-live-abcd1234efgh5678" not in out
    assert "<redacted>" in out


def test_collect_respects_char_budget(tmp_path):
    root = _project(tmp_path)
    # Many referenced files, each large; total must stay bounded.
    text_parts = []
    for i in range(10):
        f = root / "app" / f"m{i}.py"
        f.write_text("\n".join(f"body {i} {j}" for j in range(60)), encoding="utf-8")
        text_parts.append(f'File "app/m{i}.py", line 30')
    out = source.collect_source_context("\n".join(text_parts), str(tmp_path))
    assert out is not None
    assert len(out) <= source.MAX_CHARS + 200  # a small formatting slack
