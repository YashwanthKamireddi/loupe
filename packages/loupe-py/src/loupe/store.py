"""Persistent storage for Loupe traces.

Default: append-only JSONL files under ~/.loupe/traces/{trace_id}.jsonl.
A DuckDB-backed store will replace this once we add indexed search; the JSONL
format stays the canonical wire format forever (it's what we publish).
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from loupe.trace import Trace


def _default_dir() -> Path:
    override = os.environ.get("LOUPE_HOME")
    return Path(override) if override else Path.home() / ".loupe"


class Store(Protocol):
    """Anything that can persist a Trace."""

    def save(self, trace: Trace) -> None: ...


class JSONLStore:
    """Append-only JSONL writer. One file per trace.

    Schema (one JSON object per line):
        - line 0: trace header (without `steps`)
        - lines 1..N: one step per line
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (_default_dir() / "traces")
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, trace: Trace) -> None:
        path = self.root / f"{trace.trace_id}.jsonl"
        # Compact separators (no spaces) so the wire format is bit-identical
        # to JSON.stringify(...) in the TypeScript SDK — see SPEC.md §6.
        with path.open("w", encoding="utf-8") as f:
            header: dict[str, Any] = dataclasses.asdict(trace)
            steps = header.pop("steps", [])
            f.write(json.dumps({"_type": "trace", **header}, separators=(",", ":")) + "\n")
            for step in steps:
                f.write(json.dumps({"_type": "step", **step}, separators=(",", ":")) + "\n")

        # Best-effort: schedule a DuckDB index upsert in a daemon background
        # thread so the hot path (trace.save) stays in microseconds. The JSONL
        # file on disk is the source of truth — if the index call fails, the
        # next `loupe index rebuild` catches up.
        #
        # CRITICAL: we pass the store's actual root explicitly. The background
        # thread can outlive a test fixture's `monkeypatch.setenv("LOUPE_HOME")`,
        # and if we relied on the live env var the thread could write to the
        # user's real ~/.loupe/index.duckdb. That bug shipped briefly and
        # corrupted real users' indexes — never again.
        #
        # Set LOUPE_DISABLE_INDEX=1 to opt out entirely (e.g., NFS mounts).
        if not os.environ.get("LOUPE_DISABLE_INDEX"):
            _schedule_index_upsert(path, traces_root=self.root)


def _schedule_index_upsert(path: Path, *, traces_root: Path) -> None:
    """Fire-and-forget background upsert. Never raises.

    `traces_root` is the directory whose sibling `index.duckdb` we will
    write to. Captured explicitly so an env-var change between scheduling
    and execution can't redirect the write to a different home.
    """
    import threading

    # Compute the target index path EAGERLY, before spawning the thread,
    # while the caller's environment is still in scope.
    index_path = traces_root.parent / "index.duckdb"

    def _run() -> None:
        try:
            from loupe.index import JSONLIndex
            JSONLIndex(db_path=index_path, traces_dir=traces_root).upsert_file(path)
        except Exception:  # noqa: BLE001 — best-effort, swallow silently
            pass

    # daemon=True so the upsert thread never blocks interpreter exit. If the
    # process dies mid-upsert, `loupe index rebuild` reconciles on next run.
    threading.Thread(target=_run, daemon=True).start()


_default: Store | None = None


def default_store() -> Store:
    global _default
    if _default is None:
        _default = JSONLStore()
    return _default
