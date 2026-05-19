"""Tests for the DuckDB-backed query index.

Coverage:
- `upsert_file` correctly populates traces + steps from a JSONL file
- Re-upserting the same trace replaces (not duplicates) its rows
- `list_traces` returns rows newest-first
- `stats` returns matching aggregates
- `rebuild` reconstructs the index from JSONL on disk
- `remove_trace` cleans up both tables
- Schema-version mismatch triggers an automatic rebuild
- Background indexer in JSONLStore.save() doesn't break trace writes
- LOUPE_DISABLE_INDEX=1 skips the upsert entirely
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

from loupe import record_step, trace  # noqa: E402
from loupe.index import JSONLIndex, default_index, upsert_trace_file  # noqa: E402
from loupe.store import JSONLStore  # noqa: E402


@pytest.fixture()
def loupe_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "loupe-home"
    home.mkdir()
    monkeypatch.setenv("LOUPE_HOME", str(home))
    # Disable the background indexer by default so each test gets a clean
    # state and explicit upserts aren't racing a fire-and-forget thread
    # from an earlier test's @trace decorator. The one test that exercises
    # the background indexer un-sets this on its own.
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
    # Reset the lazily-initialized default store
    from loupe import store as store_mod
    store_mod._default = None
    return home


def _seed_one_trace(home: Path, name: str = "test-agent", fail: bool = False) -> str:
    """Capture one trace and return its trace_id."""
    traces_dir = home / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    before = {p.stem for p in traces_dir.glob("*.jsonl")}
    store = JSONLStore(root=traces_dir)

    @trace(name=name, framework="test", store=store)
    def agent() -> None:
        record_step("plan", "decide")
        record_step("llm-call", "fake-model")
        if fail:
            raise RuntimeError("planned failure")

    if fail:
        with pytest.raises(RuntimeError):
            agent()
    else:
        agent()

    after = {p.stem for p in traces_dir.glob("*.jsonl")}
    new = after - before
    assert len(new) == 1
    return next(iter(new))


# ---------------------------------------------------------------------------
# Direct JSONLIndex API
# ---------------------------------------------------------------------------


def test_upsert_file_populates_index(loupe_home: Path) -> None:
    """One JSONL file in → one row in traces + N rows in steps."""
    trace_id = _seed_one_trace(loupe_home, name="alpha")
    idx = JSONLIndex(
        db_path=loupe_home / "index.duckdb",
        traces_dir=loupe_home / "traces",
    )
    assert idx.upsert_file(loupe_home / "traces" / f"{trace_id}.jsonl") is True

    rows = idx.list_traces()
    assert len(rows) == 1
    assert rows[0].trace_id == trace_id
    assert rows[0].name == "alpha"
    assert rows[0].failed is False
    assert rows[0].step_count == 2


def test_upsert_is_idempotent(loupe_home: Path) -> None:
    """Calling upsert twice on the same file leaves a single row, not two."""
    trace_id = _seed_one_trace(loupe_home)
    path = loupe_home / "traces" / f"{trace_id}.jsonl"
    idx = JSONLIndex(
        db_path=loupe_home / "index.duckdb",
        traces_dir=loupe_home / "traces",
    )
    assert idx.upsert_file(path) is True
    assert idx.upsert_file(path) is True
    rows = idx.list_traces()
    assert len(rows) == 1


def test_failed_trace_is_marked(loupe_home: Path) -> None:
    trace_id = _seed_one_trace(loupe_home, fail=True)
    idx = JSONLIndex(
        db_path=loupe_home / "index.duckdb",
        traces_dir=loupe_home / "traces",
    )
    idx.upsert_file(loupe_home / "traces" / f"{trace_id}.jsonl")
    rows = idx.list_traces()
    assert rows[0].failed is True


def test_list_traces_orders_newest_first(loupe_home: Path) -> None:
    a = _seed_one_trace(loupe_home, name="first")
    time.sleep(0.02)
    b = _seed_one_trace(loupe_home, name="second")
    idx = JSONLIndex(
        db_path=loupe_home / "index.duckdb",
        traces_dir=loupe_home / "traces",
    )
    idx.upsert_file(loupe_home / "traces" / f"{a}.jsonl")
    idx.upsert_file(loupe_home / "traces" / f"{b}.jsonl")
    rows = idx.list_traces()
    assert [r.name for r in rows] == ["second", "first"]


def test_stats_matches_indexed_state(loupe_home: Path) -> None:
    a = _seed_one_trace(loupe_home, name="a", fail=False)
    b = _seed_one_trace(loupe_home, name="b", fail=True)
    idx = JSONLIndex(
        db_path=loupe_home / "index.duckdb",
        traces_dir=loupe_home / "traces",
    )
    idx.upsert_file(loupe_home / "traces" / f"{a}.jsonl")
    idx.upsert_file(loupe_home / "traces" / f"{b}.jsonl")
    s = idx.stats()
    assert s is not None
    assert s["trace_count"] == 2
    assert s["failed_count"] == 1
    assert s["step_count"] == 4    # 2 steps × 2 traces
    assert s["by_framework"] == {"test": 2}


def test_rebuild_reconstructs_index_from_disk(loupe_home: Path) -> None:
    """Even if the index is deleted, rebuild() re-creates it from JSONL."""
    a = _seed_one_trace(loupe_home, name="a")
    _ = _seed_one_trace(loupe_home, name="b")
    idx = JSONLIndex(
        db_path=loupe_home / "index.duckdb",
        traces_dir=loupe_home / "traces",
    )
    # Pretend the index never existed.
    (loupe_home / "index.duckdb").unlink(missing_ok=True)
    indexed, skipped = idx.rebuild()
    assert indexed == 2
    assert skipped == 0
    assert len(idx.list_traces()) == 2
    # Sanity: the rebuilt index has the same content as a fresh upsert.
    rebuilt_ids = {r.trace_id for r in idx.list_traces()}
    assert a in rebuilt_ids


def test_remove_trace_drops_both_tables(loupe_home: Path) -> None:
    trace_id = _seed_one_trace(loupe_home)
    idx = JSONLIndex(
        db_path=loupe_home / "index.duckdb",
        traces_dir=loupe_home / "traces",
    )
    idx.upsert_file(loupe_home / "traces" / f"{trace_id}.jsonl")
    assert len(idx.list_traces()) == 1
    assert idx.remove_trace(trace_id) is True
    assert idx.list_traces() == []


def test_info_reports_health(loupe_home: Path) -> None:
    idx = JSONLIndex(
        db_path=loupe_home / "index.duckdb",
        traces_dir=loupe_home / "traces",
    )
    # Before any upsert: index doesn't exist yet.
    info = idx.info()
    assert info["exists"] is False
    assert info["trace_count"] == 0

    trace_id = _seed_one_trace(loupe_home)
    idx.upsert_file(loupe_home / "traces" / f"{trace_id}.jsonl")
    info = idx.info()
    assert info["exists"] is True
    assert info["trace_count"] == 1
    assert info["size_bytes"] > 0


def test_missing_index_returns_empty_not_error(loupe_home: Path) -> None:
    """If the index file doesn't exist yet, list_traces returns []
    instead of raising — the caller is expected to fall back to disk."""
    idx = JSONLIndex(
        db_path=loupe_home / "no-such-index.duckdb",
        traces_dir=loupe_home / "traces",
    )
    assert idx.list_traces() == []
    assert idx.stats() is None


# ---------------------------------------------------------------------------
# Integration: JSONLStore.save() auto-indexes (background thread)
# ---------------------------------------------------------------------------


def test_jsonlstore_save_auto_indexes_via_background_thread(
    loupe_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a trace is saved, the daemon-thread indexer eventually upserts.

    We poll for up to a second because the upsert runs off the hot path.
    """
    monkeypatch.delenv("LOUPE_DISABLE_INDEX", raising=False)
    trace_id = _seed_one_trace(loupe_home, name="auto-indexed")

    idx = default_index()
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        rows = idx.list_traces()
        if any(r.trace_id == trace_id for r in rows):
            assert next(r.name for r in rows if r.trace_id == trace_id) == "auto-indexed"
            return
        time.sleep(0.05)
    raise AssertionError(
        "background indexer did not pick up the new trace within 1.5s"
    )


def test_disable_index_env_skips_upsert(
    loupe_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LOUPE_DISABLE_INDEX=1 makes upsert_trace_file a no-op."""
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
    trace_id = _seed_one_trace(loupe_home)
    path = loupe_home / "traces" / f"{trace_id}.jsonl"
    assert upsert_trace_file(path) is False
    # And the index file itself wasn't created.
    assert not (loupe_home / "index.duckdb").exists() or \
           len(JSONLIndex(
               db_path=loupe_home / "index.duckdb",
               traces_dir=loupe_home / "traces",
           ).list_traces()) == 0


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


def test_schema_version_mismatch_triggers_rebuild(loupe_home: Path) -> None:
    """If the on-disk index has an older schema_version, opening it must
    transparently rebuild — never throw."""
    import duckdb  # type: ignore[import-untyped]

    trace_id = _seed_one_trace(loupe_home)
    idx = JSONLIndex(
        db_path=loupe_home / "index.duckdb",
        traces_dir=loupe_home / "traces",
    )
    idx.upsert_file(loupe_home / "traces" / f"{trace_id}.jsonl")

    # Simulate an old version on disk.
    conn = duckdb.connect(str(loupe_home / "index.duckdb"))
    conn.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
    conn.close()

    # A new upsert should re-detect the mismatch and rebuild silently.
    idx2 = JSONLIndex(
        db_path=loupe_home / "index.duckdb",
        traces_dir=loupe_home / "traces",
    )
    # Trigger _ensure_schema by upserting again.
    trace_id_2 = _seed_one_trace(loupe_home, name="post-rebuild")
    assert idx2.upsert_file(
        loupe_home / "traces" / f"{trace_id_2}.jsonl"
    ) is True
    # Post-rebuild the index contains only the second trace because
    # the schema reset wiped the table. Old traces will be re-picked
    # up by `loupe index rebuild`, which is the documented recovery path.
    rows = idx2.list_traces()
    assert any(r.trace_id == trace_id_2 for r in rows)
