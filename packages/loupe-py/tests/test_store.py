"""Persistence layer tests."""

from __future__ import annotations

import json
from pathlib import Path

from loupe.store import JSONLStore
from loupe.trace import Step, Trace


def test_jsonl_store_round_trip(tmp_path: Path) -> None:
    store = JSONLStore(root=tmp_path)
    t = Trace(trace_id="abc123", name="t1", framework="test", started_at=1.0, ended_at=2.0)
    t.add_step(
        Step(
            step_id="s1",
            parent_step_id=None,
            kind="thought",
            name="plan",
            started_at=1.1,
            ended_at=1.2,
            inputs={"q": "x"},
            outputs={"plan": "do thing"},
        )
    )
    store.save(t)

    path = tmp_path / "abc123.jsonl"
    assert path.exists()
    lines = path.read_text().splitlines()
    assert len(lines) == 2

    header = json.loads(lines[0])
    step = json.loads(lines[1])
    assert header["_type"] == "trace"
    assert "steps" not in header
    assert header["name"] == "t1"
    assert step["_type"] == "step"
    assert step["name"] == "plan"
    assert step["outputs"]["plan"] == "do thing"


def test_jsonl_store_creates_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "traces"
    store = JSONLStore(root=target)
    t = Trace(trace_id="xyz", name="t", framework=None, started_at=0.0, ended_at=0.0)
    store.save(t)
    assert (target / "xyz.jsonl").exists()
