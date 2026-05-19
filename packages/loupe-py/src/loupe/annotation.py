"""Annotation layer — turn captured traces into LoupeBench entries.

A trace becomes a benchmark candidate when you tag a failing step with:
    - failure category (loop, hallucination, destructive-action, tool-misuse, …)
    - root-cause notes (free text)
    - optional mitigation (what fixed it)

Annotations are stored beside each trace as a sidecar file
~/.loupe/annotations/{trace_id}.json so the original JSONL trace stays
immutable and the canonical wire format never grows new fields.

## Concurrency model

`AnnotationStore.add` and `.remove` are **safe to call concurrently** — they
serialize via an OS-level advisory lock on a sibling `.lock` file (POSIX
flock; Windows msvcrt as a fallback). The lock is held for the duration of
the read-modify-write cycle. Writes themselves are atomic: we write to a
sibling `.tmp` file and rename it into place (POSIX `rename(2)` is atomic
on the same filesystem).

Readers don't lock — they either see the previous complete file or the new
complete file, never a partial one. That's a torn-write-free guarantee on
every modern OS.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import IO, Any, Literal

from loupe.store import _default_dir

FailureCategory = Literal[
    "loop",
    "hallucination",
    "destructive-action",
    "tool-misuse",
    "security",
    "format-error",
    "context-loss",
    "unguarded-delete",
    "infinite-retry",
    "wrong-tool",
    "off-task",
    "other",
]


@dataclass
class Annotation:
    """A single annotation on one step of one trace."""

    trace_id: str
    step_id: str
    failure_category: FailureCategory
    notes: str = ""
    mitigation: str = ""
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    annotator: str = ""
    tags: list[str] = field(default_factory=list)
    # Free-form JSON. Today populated by loupe.attribution.AttributionResult.to_json_dict()
    # — keys include model, sae, method, top_features (list of dicts), summary,
    # attributed_at. Kept as dict[str, Any] so the schema can evolve without
    # breaking already-stored annotations.
    circuit_attribution: dict[str, Any] = field(default_factory=dict)


class AnnotationStore:
    """JSON sidecar store. One file per trace, all annotations inside.

    Reads are lock-free and atomic (whole-file replace). Writes acquire a
    per-trace advisory lock so concurrent edits never lose data.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (_default_dir() / "annotations")
        self.root.mkdir(parents=True, exist_ok=True)

    # -- public API -----------------------------------------------------------

    def load(self, trace_id: str) -> list[Annotation]:
        path = self._path(trace_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Defensive: a half-written file produced by an earlier (now-fixed)
            # writer should not crash readers. Treat it as empty.
            return []
        return [Annotation(**item) for item in data]

    def add(self, ann: Annotation) -> None:
        with self._exclusive(ann.trace_id):
            existing = self.load(ann.trace_id)
            existing = [a for a in existing if a.step_id != ann.step_id]
            existing.append(ann)
            self._write_atomic(ann.trace_id, existing)

    def remove(self, trace_id: str, step_id: str) -> bool:
        with self._exclusive(trace_id):
            existing = self.load(trace_id)
            kept = [a for a in existing if a.step_id != step_id]
            if len(kept) == len(existing):
                return False
            self._write_atomic(trace_id, kept)
            return True

    def all(self) -> dict[str, list[Annotation]]:
        result: dict[str, list[Annotation]] = {}
        for file in self.root.glob("*.json"):
            result[file.stem] = self.load(file.stem)
        return result

    # -- internals -----------------------------------------------------------

    def _path(self, trace_id: str) -> Path:
        return self.root / f"{trace_id}.json"

    def _lock_path(self, trace_id: str) -> Path:
        return self.root / f"{trace_id}.lock"

    def _write_atomic(self, trace_id: str, annotations: list[Annotation]) -> None:
        """Whole-file replace via tmp + rename. Atomic on POSIX and Windows."""
        target = self._path(trace_id)
        tmp = target.with_suffix(f".json.tmp.{uuid.uuid4().hex[:8]}")
        try:
            tmp.write_text(
                json.dumps([asdict(a) for a in annotations], indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, target)  # atomic on POSIX + Windows (Python 3.3+)
        finally:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()

    @contextlib.contextmanager
    def _exclusive(self, trace_id: str) -> Iterator[None]:
        """Acquire an OS-level advisory lock for the duration of a write.

        Falls back to a no-op on platforms that support neither flock nor
        msvcrt — single-process usage is still safe via the atomic rename.
        """
        lock_file = self._lock_path(trace_id).open("a+")
        try:
            _acquire(lock_file)
            yield
        finally:
            _release(lock_file)
            lock_file.close()


# -- platform-specific advisory locks ---------------------------------------


def _acquire(fp: IO[str]) -> None:
    if sys.platform == "win32":  # pragma: no cover — exercised only on Windows
        import msvcrt
        with contextlib.suppress(OSError):
            msvcrt.locking(fp.fileno(), msvcrt.LK_LOCK, 1)
        return
    try:
        import fcntl
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
    except (ImportError, OSError):
        # No locking primitive available — fall back to atomic rename only.
        pass


def _release(fp: IO[str]) -> None:
    if sys.platform == "win32":  # pragma: no cover
        import msvcrt
        with contextlib.suppress(OSError):
            msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
        return
    try:
        import fcntl
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
