"""Tests for the rotating discovery tips."""

from __future__ import annotations

import dataclasses

from terminalghost.cli import doctor
from terminalghost.cli.tips import TIPS, random_tip
from terminalghost.config.loader import Config


def test_tips_are_nonempty_one_liners():
    assert len(TIPS) >= 10
    for tip in TIPS:
        assert isinstance(tip, str) and tip.strip()
        assert "\n" not in tip
        assert len(tip) < 100  # keeps to one terminal line


def test_random_tip_comes_from_the_list():
    assert random_tip() in TIPS


def test_doctor_prints_tip_when_healthy(capsys, monkeypatch):
    # Force every check green so the tip path runs.
    monkeypatch.setattr(
        doctor, "gather_checks", lambda config: [doctor.Check(True, "All", "ok")]
    )
    cfg = dataclasses.replace(
        Config(), ui=dataclasses.replace(Config().ui, color="never")
    )
    assert doctor.cmd_doctor(cfg) == 0
    assert "tip:" in capsys.readouterr().out
