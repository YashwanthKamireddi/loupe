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


def test_background_indexer_targets_store_root_not_env(
    tmp_path: Path, monkeypatch
) -> None:
    """REGRESSION: the background indexer must write to a directory derived
    from the JSONLStore's own root, not from the LOUPE_HOME env var.

    Earlier, the thread captured no state from its caller; it later called
    ``default_index()`` which reads LOUPE_HOME at thread-run time. If a
    test's `monkeypatch.setenv("LOUPE_HOME")` got torn down between
    scheduling and execution, the thread saw the live env and wrote to
    the user's real ``~/.loupe/index.duckdb`` — corrupting it with
    test-fixture rows.

    Deterministic check: patch ``JSONLIndex.__init__`` to capture the
    db_path the background thread tries to use, then assert it's
    derived from the STORE's root, not LOUPE_HOME.
    """
    import time

    from loupe import index as index_mod

    decoy_home = tmp_path / "decoy-loupe-home"
    decoy_home.mkdir()
    real_root = tmp_path / "real-store"
    real_root.mkdir()

    monkeypatch.setenv("LOUPE_HOME", str(decoy_home))
    monkeypatch.delenv("LOUPE_DISABLE_INDEX", raising=False)

    captured_paths: list[Path] = []
    orig_init = index_mod.JSONLIndex.__init__

    def spy_init(self, db_path, traces_dir):  # type: ignore[no-untyped-def]
        captured_paths.append(db_path)
        orig_init(self, db_path, traces_dir)

    monkeypatch.setattr(index_mod.JSONLIndex, "__init__", spy_init)

    store = JSONLStore(root=real_root)
    t = Trace(trace_id="abc", name="t", framework="test",
              started_at=0.0, ended_at=0.0)
    store.save(t)

    # Give the daemon thread a fair window to fire.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if captured_paths:
            break
        time.sleep(0.02)

    assert captured_paths, \
        "background indexer must have constructed a JSONLIndex"
    expected = real_root.parent / "index.duckdb"
    decoy_index = decoy_home / "index.duckdb"
    assert captured_paths[0] == expected, (
        f"index path must be derived from the store root; got "
        f"{captured_paths[0]} expected {expected}"
    )
    assert captured_paths[0] != decoy_index, \
        "background indexer must NEVER write to LOUPE_HOME when the store's root is elsewhere"
