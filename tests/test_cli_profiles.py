"""Tests for terminalghost.cli.profiles (shell-profile block management)."""

from __future__ import annotations

from terminalghost.cli import profiles


def test_classify_known_shells():
    assert profiles._classify("zsh") == "zsh"
    assert profiles._classify("/bin/bash") == "bash"  # substring match tolerates paths
    assert profiles._classify("bash") == "bash"
    assert profiles._classify("pwsh.exe") == "powershell"
    assert profiles._classify("powershell.EXE") == "powershell"
    assert profiles._classify("cmd") is None
    assert profiles._classify("") is None


def test_hook_source_line_per_shell():
    assert "hook-path zsh" in profiles.hook_source_line("zsh")
    assert profiles.hook_source_line("zsh").startswith("source ")
    assert profiles.hook_source_line("powershell").startswith(". (")


def test_install_block_fresh_file(tmp_path):
    profile = tmp_path / ".zshrc"
    result = profiles.install_block(profile, "zsh")
    assert result == "installed"
    text = profile.read_text()
    assert profiles.BLOCK_START in text
    assert profiles.BLOCK_END in text
    assert "hook-path zsh" in text


def test_install_block_idempotent(tmp_path):
    profile = tmp_path / ".bashrc"
    profile.write_text("export FOO=1\n")
    profiles.install_block(profile, "bash")
    second = profiles.install_block(profile, "bash")
    assert second == "already"
    # original content preserved, block added exactly once
    text = profile.read_text()
    assert text.count(profiles.BLOCK_START) == 1
    assert "export FOO=1" in text


def test_install_block_updates_stale_block(tmp_path):
    profile = tmp_path / ".zshrc"
    profile.write_text(
        f"{profiles.BLOCK_START}\nsource /old/path/zsh_hooks.sh\n{profiles.BLOCK_END}\n"
    )
    result = profiles.install_block(profile, "zsh")
    assert result == "updated"
    text = profile.read_text()
    assert "/old/path" not in text
    assert text.count(profiles.BLOCK_START) == 1


def test_uninstall_block(tmp_path):
    profile = tmp_path / ".zshrc"
    profile.write_text("line1\n")
    profiles.install_block(profile, "zsh")
    assert profiles.is_installed(profile)
    assert profiles.uninstall_block(profile) == "removed"
    assert not profiles.is_installed(profile)
    assert "line1" in profile.read_text()


def test_uninstall_block_absent(tmp_path):
    profile = tmp_path / ".zshrc"
    assert profiles.uninstall_block(profile) == "absent"
    profile.write_text("nothing here\n")
    assert profiles.uninstall_block(profile) == "absent"
