"""LoupeBench export — turn local traces + annotations into a publishable dataset.

`loupe export --out my-bench.jsonl` produces one JSONL per annotated failure,
matching the schema documented in bench/README.md. Unannotated traces are
skipped. Each record bundles the trace header, the failing step, and the
annotation so it's self-contained.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from loupe.annotation import AnnotationStore
from loupe.store import _default_dir


def export_jsonl(out: Path, *, license: str = "CC-BY-4.0") -> int:
    """Write one record per annotated step. Returns number of records."""
    traces_dir = _default_dir() / "traces"
    ann_store = AnnotationStore()
    count = 0

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for trace_path in sorted(traces_dir.glob("*.jsonl")):
            header, steps = _read_trace_file(trace_path)
            if header is None:
                continue
            annotations = ann_store.load(header["trace_id"])
            if not annotations:
                continue
            for ann in annotations:
                step = next((s for s in steps if s["step_id"] == ann.step_id), None)
                if step is None:
                    continue
                record = {
                    "id": f"lb-{header['trace_id'][:8]}-{ann.step_id[:6]}",
                    "framework": header.get("framework"),
                    "trace": {
                        "trace_id": header["trace_id"],
                        "name": header["name"],
                        "started_at": header["started_at"],
                        "ended_at": header.get("ended_at"),
                    },
                    "step": step,
                    "annotation": asdict(ann),
                    "license": license,
                }
                f.write(json.dumps(record) + "\n")
                count += 1
    return count


def _read_trace_file(path: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    header: dict[str, Any] | None = None
    steps: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            kind = obj.pop("_type", None)
            if kind == "trace":
                header = obj
            elif kind == "step":
                steps.append(obj)
    return header, steps
