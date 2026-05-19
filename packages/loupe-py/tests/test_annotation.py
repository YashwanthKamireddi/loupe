"""Annotation store + LoupeBench export tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loupe import record_step, trace
from loupe.annotation import Annotation, AnnotationStore
from loupe.bench import export_jsonl
from loupe.store import JSONLStore


@pytest.fixture()
def loupe_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "loupe-home"
    home.mkdir()
    monkeypatch.setenv("LOUPE_HOME", str(home))
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
    from loupe import store as store_mod

    store_mod._default = None
    return home


def _make_failed_trace(home: Path) -> tuple[str, str]:
    store = JSONLStore(root=home / "traces")

    captured: dict[str, str] = {}

    @trace(name="t", framework="test", store=store)
    def agent() -> None:
        s1 = record_step("thought", "plan")
        s2 = record_step("error", "boom", error="kaboom")
        assert s1 and s2
        captured["s1"] = s1.step_id
        captured["s2"] = s2.step_id
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        agent()

    trace_id = list((home / "traces").glob("*.jsonl"))[0].stem
    return trace_id, captured["s2"]


def test_annotation_round_trip(loupe_home: Path) -> None:
    trace_id, step_id = _make_failed_trace(loupe_home)
    store = AnnotationStore()
    ann = Annotation(
        trace_id=trace_id,
        step_id=step_id,
        failure_category="unguarded-delete",
        notes="agent deleted src/ instead of src/old",
        mitigation="add a path-prefix guard",
        severity="critical",
        annotator="yk",
        tags=["coding", "file-io"],
    )
    store.add(ann)
    loaded = store.load(trace_id)
    assert len(loaded) == 1
    assert loaded[0].failure_category == "unguarded-delete"
    assert loaded[0].severity == "critical"
    assert loaded[0].tags == ["coding", "file-io"]


def test_annotation_replaces_same_step(loupe_home: Path) -> None:
    trace_id, step_id = _make_failed_trace(loupe_home)
    store = AnnotationStore()
    store.add(Annotation(trace_id=trace_id, step_id=step_id, failure_category="loop", notes="v1"))
    store.add(
        Annotation(
            trace_id=trace_id, step_id=step_id, failure_category="loop", notes="v2 (corrected)"
        )
    )
    items = store.load(trace_id)
    assert len(items) == 1
    assert items[0].notes == "v2 (corrected)"


def test_annotation_remove(loupe_home: Path) -> None:
    trace_id, step_id = _make_failed_trace(loupe_home)
    store = AnnotationStore()
    store.add(Annotation(trace_id=trace_id, step_id=step_id, failure_category="loop"))
    assert store.remove(trace_id, step_id) is True
    assert store.load(trace_id) == []
    assert store.remove(trace_id, step_id) is False


def test_export_jsonl(loupe_home: Path, tmp_path: Path) -> None:
    trace_id, step_id = _make_failed_trace(loupe_home)
    AnnotationStore().add(
        Annotation(
            trace_id=trace_id,
            step_id=step_id,
            failure_category="unguarded-delete",
            notes="real bug",
            mitigation="guard the path",
            severity="critical",
        )
    )
    out = tmp_path / "bench.jsonl"
    n = export_jsonl(out)
    assert n == 1
    line = out.read_text().strip()
    record = json.loads(line)
    assert record["id"].startswith("lb-")
    assert record["framework"] == "test"
    assert record["step"]["step_id"] == step_id
    assert record["annotation"]["failure_category"] == "unguarded-delete"
    assert record["license"] == "CC-BY-4.0"


def test_export_skips_untagged(loupe_home: Path, tmp_path: Path) -> None:
    _make_failed_trace(loupe_home)
    out = tmp_path / "bench.jsonl"
    n = export_jsonl(out)
    assert n == 0
    assert out.exists()
    assert out.read_text() == ""
