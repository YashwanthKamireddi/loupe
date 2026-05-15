"""Tests for `loupe report` markdown rendering and `loupe init` scaffolding."""

from __future__ import annotations

from pathlib import Path

import pytest

from loupe import record_step, trace
from loupe.annotation import Annotation, AnnotationStore
from loupe.report import render_trace_markdown
from loupe.scaffold import scaffold
from loupe.store import JSONLStore


@pytest.fixture()
def loupe_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "loupe-home"
    home.mkdir()
    monkeypatch.setenv("LOUPE_HOME", str(home))
    from loupe import store as store_mod

    store_mod._default = None
    return home


def test_report_markdown_renders(loupe_home: Path) -> None:
    store = JSONLStore(root=loupe_home / "traces")
    captured: dict[str, str] = {}

    @trace(name="case-001", framework="test", store=store)
    def agent() -> None:
        record_step("thought", "plan")
        s = record_step("error", "boom", error="oops")
        assert s
        captured["sid"] = s.step_id
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        agent()

    trace_path = next((loupe_home / "traces").glob("*.jsonl"))
    AnnotationStore().add(
        Annotation(
            trace_id=trace_path.stem,
            step_id=captured["sid"],
            failure_category="other",
            notes="something went wrong",
            severity="critical",
        )
    )

    md = render_trace_markdown(trace_path)
    assert "# Case File · case-001" in md
    assert "## Annotations" in md
    assert "## Steps" in md
    assert "## Failure detail" in md
    assert "something went wrong" in md
    assert "## Top-level error" in md
    assert "RuntimeError" in md
    # Step count matches
    assert "| 1 |" in md and "| 2 |" in md


def test_report_for_successful_trace(loupe_home: Path) -> None:
    store = JSONLStore(root=loupe_home / "traces")

    @trace(name="happy", framework="test", store=store)
    def agent() -> int:
        record_step("thought", "plan")
        return 42

    agent()
    trace_path = next((loupe_home / "traces").glob("*.jsonl"))
    md = render_trace_markdown(trace_path)
    assert "**✓ ok**" in md
    assert "## Failure detail" not in md
    assert "## Annotations" not in md


def test_scaffold_creates_runnable_starter(tmp_path: Path) -> None:
    target = tmp_path / "demo-agent"
    files = scaffold(target, "demo-agent")
    assert {f.name for f in files} == {"agent.py", "README.md", ".gitignore"}
    agent_src = (target / "agent.py").read_text()
    assert "from loupe import record_step, trace" in agent_src
    assert 'framework="starter"' in agent_src
    assert "demo-agent" in (target / "README.md").read_text()
