"""Core @trace decorator tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from loupe import current_trace, record_step, trace
from loupe.store import JSONLStore


@pytest.fixture()
def store(tmp_path: Path) -> JSONLStore:
    return JSONLStore(root=tmp_path)


def test_sync_trace_captures(store: JSONLStore) -> None:
    @trace(name="sync_agent", framework="test", store=store)
    def agent(q: str) -> str:
        record_step("thought", "plan", outputs={"plan": "1. think 2. answer"})
        record_step("llm-call", "model", outputs={"text": f"echo: {q}"})
        return q.upper()

    out = agent("hello")
    assert out == "HELLO"

    files = list(store.root.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().splitlines()
    header = json.loads(lines[0])
    steps = [json.loads(line) for line in lines[1:]]
    assert header["name"] == "sync_agent"
    assert header["framework"] == "test"
    assert header["ended_at"] is not None
    assert header.get("metadata", {}).get("failed") is not True
    assert [s["name"] for s in steps] == ["plan", "model"]
    assert steps[0]["kind"] == "thought"
    assert steps[1]["kind"] == "llm-call"


def test_async_trace_captures(store: JSONLStore) -> None:
    @trace(framework="test", store=store)
    async def agent(q: str) -> str:
        await asyncio.sleep(0.001)
        record_step("tool-call", "search", inputs={"q": q})
        return "ok"

    out = asyncio.run(agent("hi"))
    assert out == "ok"

    files = list(store.root.glob("*.jsonl"))
    assert len(files) == 1
    header = json.loads(files[0].read_text().splitlines()[0])
    assert header["ended_at"] >= header["started_at"]


def test_trace_records_failure(store: JSONLStore) -> None:
    @trace(framework="test", store=store)
    def boom() -> None:
        record_step("error", "boom-step", error="planned failure")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        boom()

    header = json.loads(list(store.root.glob("*.jsonl"))[0].read_text().splitlines()[0])
    assert header["metadata"]["failed"] is True
    assert "RuntimeError" in header["metadata"]["error"]


def test_record_step_outside_trace_returns_none() -> None:
    assert current_trace() is None
    assert record_step("thought", "outside") is None


def test_nested_traces_isolated(store: JSONLStore) -> None:
    @trace(name="inner", framework="test", store=store)
    def inner() -> str:
        record_step("thought", "inner-step")
        return "i"

    @trace(name="outer", framework="test", store=store)
    def outer() -> str:
        record_step("thought", "outer-step")
        inner()
        record_step("thought", "outer-after")
        return "o"

    outer()
    files = sorted(store.root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    assert len(files) == 2

    inner_steps = [json.loads(line) for line in files[0].read_text().splitlines()[1:]]
    outer_steps = [json.loads(line) for line in files[1].read_text().splitlines()[1:]]
    assert [s["name"] for s in inner_steps] == ["inner-step"]
    # outer_steps must include the inner record_step calls too? No — because
    # _current_trace was switched to inner during inner(), then restored. Verify:
    assert [s["name"] for s in outer_steps] == ["outer-step", "outer-after"]


def test_decorator_used_without_parens(store: JSONLStore, monkeypatch: pytest.MonkeyPatch) -> None:
    # When used as bare @trace (no parens), default_store() is used; redirect it.
    from loupe import store as store_mod

    monkeypatch.setattr(store_mod, "_default", store)

    @trace
    def agent() -> int:
        record_step("thought", "ok")
        return 1

    assert agent() == 1
    files = list(store.root.glob("*.jsonl"))
    assert len(files) == 1
