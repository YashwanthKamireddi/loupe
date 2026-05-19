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
        # Set LOUPE_DISABLE_INDEX=1 to opt out entirely (e.g., NFS mounts).
        if not os.environ.get("LOUPE_DISABLE_INDEX"):
            _schedule_index_upsert(path)


def _schedule_index_upsert(path: Path) -> None:
    """Fire-and-forget background upsert. Never raises."""
    import threading

    def _run() -> None:
        try:
            from loupe.index import upsert_trace_file
            upsert_trace_file(path)
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
