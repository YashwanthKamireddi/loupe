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

    def save(self, trace: "Trace") -> None: ...


class JSONLStore:
    """Append-only JSONL writer. One file per trace.

    Schema (one JSON object per line):
        - line 0: trace header (without `steps`)
        - lines 1..N: one step per line
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (_default_dir() / "traces")
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, trace: "Trace") -> None:
        path = self.root / f"{trace.trace_id}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            header: dict[str, Any] = dataclasses.asdict(trace)
            steps = header.pop("steps", [])
            f.write(json.dumps({"_type": "trace", **header}) + "\n")
            for step in steps:
                f.write(json.dumps({"_type": "step", **step}) + "\n")


_default: Store | None = None


def default_store() -> Store:
    global _default
    if _default is None:
        _default = JSONLStore()
    return _default
