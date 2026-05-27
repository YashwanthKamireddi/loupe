"""`loupe doctor --fix` self-heal — safe + reversible repairs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from loupe.cli import app


@pytest.fixture()
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    h = tmp_path / "loupe-home"
    monkeypatch.setenv("LOUPE_HOME", str(h))
    return h


def _write_valid_trace(traces_dir: Path, stem: str) -> Path:
    traces_dir.mkdir(parents=True, exist_ok=True)
    p = traces_dir / f"{stem}.jsonl"
    p.write_text(
        json.dumps({
            "_type": "trace",
            "trace_id": stem,
            "name": "x",
            "framework": "test",
            "started_at": 1700000000,
            "ended_at": 1700000001,
            "metadata": {},
        }) + "\n",
        encoding="utf-8",
    )
    return p


def test_creates_missing_dirs(home: Path) -> None:
    assert not (home / "traces").exists()
    assert not (home / "annotations").exists()
    res = CliRunner().invoke(app, ["doctor", "--fix"])
    assert res.exit_code == 0, res.output
    assert (home / "traces").exists()
    assert (home / "annotations").exists()
    assert "created" in res.output


def test_quarantines_corrupt_jsonl(home: Path) -> None:
    traces = home / "traces"
    _write_valid_trace(traces, "good1234567890abcdef")
    # A trace whose first line is garbage — fully corrupt.
    (traces / "bad1234567890abcdef.jsonl").write_text(
        "not json at all\n", encoding="utf-8",
    )

    res = CliRunner().invoke(app, ["doctor", "--fix"])
    assert res.exit_code == 0, res.output

    # bad trace moved to quarantine, good trace untouched.
    assert (home / "quarantine" / "bad1234567890abcdef.jsonl").exists()
    assert (traces / "good1234567890abcdef.jsonl").exists()
    assert not (traces / "bad1234567890abcdef.jsonl").exists()
    assert "quarantined" in res.output


def test_removes_orphan_annotations(home: Path) -> None:
    (home / "traces").mkdir(parents=True)
    (home / "annotations").mkdir(parents=True)
    _write_valid_trace(home / "traces", "live1234567890abcdef")
    # Orphan: annotation whose parent trace doesn't exist.
    orphan = home / "annotations" / "ghost1234567890abcdef.json"
    orphan.write_text("{}", encoding="utf-8")
    # Live annotation that DOES match a real trace — must survive.
    live_ann = home / "annotations" / "live1234567890abcdef.json"
    live_ann.write_text("{}", encoding="utf-8")

    res = CliRunner().invoke(app, ["doctor", "--fix"])
    assert res.exit_code == 0, res.output
    assert not orphan.exists(), "orphan annotation should have been removed"
    assert live_ann.exists(), "live annotation must not be touched"
    assert "orphan annotation" in res.output


def test_idempotent_when_already_clean(home: Path) -> None:
    """Running --fix twice — second run is a no-op."""
    (home / "traces").mkdir(parents=True)
    (home / "annotations").mkdir(parents=True)
    _write_valid_trace(home / "traces", "clean1234567890abcdef")

    first = CliRunner().invoke(app, ["doctor", "--fix"])
    assert first.exit_code == 0
    second = CliRunner().invoke(app, ["doctor", "--fix"])
    assert second.exit_code == 0
    assert "already clean" in second.output


def test_quarantine_name_collisions_get_suffix(home: Path) -> None:
    """Re-quarantining a same-named file appends .1 / .2 instead of overwriting."""
    traces = home / "traces"
    q = home / "quarantine"
    q.mkdir(parents=True)
    # Pre-existing quarantine entry with the same stem we're about to move.
    (q / "dup1234567890abcdef.jsonl").write_text("old\n", encoding="utf-8")
    traces.mkdir(parents=True)
    (traces / "dup1234567890abcdef.jsonl").write_text(
        "not json\n", encoding="utf-8",
    )

    res = CliRunner().invoke(app, ["doctor", "--fix"])
    assert res.exit_code == 0, res.output
    # Both files survive — original quarantine + new suffix.
    assert (q / "dup1234567890abcdef.jsonl").read_text() == "old\n"
    assert (q / "dup1234567890abcdef.1.jsonl").exists()
