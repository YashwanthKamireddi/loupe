"""Annotation store concurrent-write safety.

Two writers racing on the same trace must not lose annotations. We model the
contention by spawning N processes (true OS-level concurrency, no GIL) that
each call .add(), and assert all N annotations land in the final file.
"""

from __future__ import annotations

import json
import multiprocessing as mp
from pathlib import Path

from loupe.annotation import Annotation, AnnotationStore


def _add_one(args: tuple[str, str, str]) -> None:
    root, trace_id, step_id = args
    store = AnnotationStore(root=Path(root))
    store.add(Annotation(
        trace_id=trace_id,
        step_id=step_id,
        failure_category="loop",
        notes=f"annotation from {step_id}",
    ))


def test_concurrent_adds_do_not_lose_annotations(tmp_path: Path) -> None:
    """30 processes each add an annotation for a distinct step. All 30 land."""
    trace_id = "concurrent-test-trace"
    step_ids = [f"s{i:03d}" for i in range(30)]

    # Use multiprocessing.spawn so workers reinitialise cleanly across platforms.
    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=8) as pool:
        pool.map(_add_one, [(str(tmp_path), trace_id, sid) for sid in step_ids])

    # The file must exist and contain all 30 annotations.
    target = tmp_path / f"{trace_id}.json"
    assert target.exists(), "no annotation file written"
    data = json.loads(target.read_text(encoding="utf-8"))
    assert len(data) == len(step_ids), (
        f"expected {len(step_ids)} annotations, got {len(data)} — "
        f"missing: {set(step_ids) - {a['step_id'] for a in data}}"
    )
    written_ids = {a["step_id"] for a in data}
    assert written_ids == set(step_ids)


def test_concurrent_add_remove_consistent(tmp_path: Path) -> None:
    """A series of add+remove on the same step never produces a corrupt file."""
    store = AnnotationStore(root=tmp_path)
    trace_id = "rm-test"
    step_id = "step1"

    for i in range(20):
        ann = Annotation(
            trace_id=trace_id, step_id=step_id,
            failure_category="other", notes=f"iter {i}",
        )
        store.add(ann)
        # Verify file is parseable after every write.
        target = tmp_path / f"{trace_id}.json"
        json.loads(target.read_text(encoding="utf-8"))

    assert store.remove(trace_id, step_id) is True
    assert store.load(trace_id) == []


def test_write_atomicity_no_partial_files(tmp_path: Path) -> None:
    """After a successful add, no orphaned .tmp files should remain."""
    store = AnnotationStore(root=tmp_path)
    for i in range(10):
        store.add(Annotation(
            trace_id="atomic", step_id=f"s{i}",
            failure_category="loop", notes="x",
        ))
    tmp_files = list(tmp_path.glob("*.tmp.*"))
    assert tmp_files == [], f"orphaned tmp files: {tmp_files}"


def test_recovery_from_corrupt_file(tmp_path: Path) -> None:
    """If the on-disk file is malformed, the loader returns [] instead of crashing."""
    store = AnnotationStore(root=tmp_path)
    target = tmp_path / "corrupt.json"
    target.write_text("{not valid json", encoding="utf-8")

    # load() should treat this as empty, not raise
    assert store.load("corrupt") == []

    # add() should succeed and overwrite with a valid file
    store.add(Annotation(
        trace_id="corrupt", step_id="s1",
        failure_category="other", notes="recovered",
    ))
    assert len(store.load("corrupt")) == 1
