"""Annotation layer — turn captured traces into LoupeBench entries.

A trace becomes a benchmark candidate when you tag a failing step with:
    - failure category (loop, hallucination, destructive-action, tool-misuse, …)
    - root-cause notes (free text)
    - optional mitigation (what fixed it)

Annotations are stored beside each trace as a sidecar file
~/.loupe/annotations/{trace_id}.json so the original JSONL trace stays
immutable and the canonical wire format never grows new fields.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

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
    circuit_attribution: dict[str, list[int]] = field(default_factory=dict)


class AnnotationStore:
    """JSON sidecar store. One file per trace, all annotations inside."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (_default_dir() / "annotations")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, trace_id: str) -> Path:
        return self.root / f"{trace_id}.json"

    def load(self, trace_id: str) -> list[Annotation]:
        path = self._path(trace_id)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Annotation(**item) for item in data]

    def add(self, ann: Annotation) -> None:
        existing = self.load(ann.trace_id)
        existing = [a for a in existing if a.step_id != ann.step_id]
        existing.append(ann)
        self._path(ann.trace_id).write_text(
            json.dumps([asdict(a) for a in existing], indent=2),
            encoding="utf-8",
        )

    def remove(self, trace_id: str, step_id: str) -> bool:
        existing = self.load(trace_id)
        kept = [a for a in existing if a.step_id != step_id]
        if len(kept) == len(existing):
            return False
        self._path(trace_id).write_text(
            json.dumps([asdict(a) for a in kept], indent=2),
            encoding="utf-8",
        )
        return True

    def all(self) -> dict[str, list[Annotation]]:
        result: dict[str, list[Annotation]] = {}
        for file in self.root.glob("*.json"):
            result[file.stem] = self.load(file.stem)
        return result
