"""v0.0.67 promises Loupe never crashes on a corrupt JSONL.

A SIGKILL'd writer, a flaky disk, or a user hand-editing a trace can
all leave a JSONL with garbage on some line. Before v0.0.67 the CLI
would die with an unhandled JSONDecodeError. Now the tolerant reader
in `loupe.store.safe_load_jsonl` skips bad lines and surfaces a
`⚠ skipped N corrupt line(s)` warning.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from loupe.cli import app
from loupe.store import safe_load_jsonl


def _write_mixed_trace(path: Path) -> None:
    """Header line + valid step + garbage + valid step."""
    path.write_text(
        '{"_type":"trace","trace_id":"abc1234567890abcdef","name":"x",'
        '"framework":"test","started_at":1700000000,"ended_at":1700000001,'
        '"metadata":{}}\n'
        '{"_type":"step","step_id":"s001","kind":"thought","name":"plan",'
        '"started_at":1700000000.1,"ended_at":1700000000.2,'
        '"inputs":{},"outputs":{},"metadata":{}}\n'
        "this line is not JSON at all\n"
        '{"_type":"step","step_id":"s002","kind":"llm-call","name":"call",'
        '"started_at":1700000000.3,"ended_at":1700000000.4,'
        '"inputs":{},"outputs":{},"metadata":{}}\n',
        encoding="utf-8",
    )


def test_safe_load_jsonl_skips_bad_lines(tmp_path: Path) -> None:
    p = tmp_path / "trace.jsonl"
    _write_mixed_trace(p)
    records, skipped = safe_load_jsonl(p)
    assert len(records) == 3  # header + 2 steps
    assert skipped == 1
    kinds = [r.get("_type") for r in records]
    assert kinds == ["trace", "step", "step"]


def test_safe_load_jsonl_handles_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    records, skipped = safe_load_jsonl(p)
    assert records == []
    assert skipped == 0


def test_safe_load_jsonl_handles_all_garbage(tmp_path: Path) -> None:
    p = tmp_path / "garbage.jsonl"
    p.write_text("not json\nalso not json\nstill not json\n", encoding="utf-8")
    records, skipped = safe_load_jsonl(p)
    assert records == []
    assert skipped == 3


def test_show_command_doesnt_crash_on_corrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """`loupe show` against a partially-corrupt trace exits 0 with ⚠."""
    home = tmp_path / "loupe-home"
    (home / "traces").mkdir(parents=True)
    _write_mixed_trace(home / "traces" / "abc1234567890abcdef.jsonl")
    monkeypatch.setenv("LOUPE_HOME", str(home))

    runner = CliRunner()
    res = runner.invoke(app, ["show", "abc1234"])
    assert res.exit_code == 0, res.output
    assert "⚠ skipped 1 corrupt line" in res.output or "skipped 1 corrupt" in res.output
    # Still shows the valid content (the step name).
    assert "plan" in res.output or "call" in res.output


def test_list_command_doesnt_crash_on_corrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    home = tmp_path / "loupe-home"
    (home / "traces").mkdir(parents=True)
    _write_mixed_trace(home / "traces" / "abc1234567890abcdef.jsonl")
    monkeypatch.setenv("LOUPE_HOME", str(home))

    runner = CliRunner()
    res = runner.invoke(app, ["list"])
    assert res.exit_code == 0, res.output
    # The corrupt trace's header was valid → trace must appear in the
    # table. `loupe list` truncates ids for column width, so check by
    # name (set to "x" by _write_mixed_trace) rather than by trace_id.
    assert "x" in res.output
    # And the table must have rendered (not bailed out half-way).
    assert "traces" in res.output.lower()
