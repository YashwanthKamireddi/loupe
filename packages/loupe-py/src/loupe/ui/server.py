"""FastAPI app behind `loupe ui`.

Endpoints:
  GET    /api/traces                         List traces (header only, newest first)
  GET    /api/traces/{id}                    Full trace with steps + annotations
  GET    /api/traces/{id}/annotations        List annotations for one trace
  POST   /api/traces/{id}/annotations        Create/update an annotation
  DELETE /api/traces/{id}/annotations/{step} Remove an annotation
  GET    /api/stats                          Aggregate counts for the header banner
  GET    /                                   Static SPA
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "loupe ui requires fastapi + uvicorn. "
        "Install with `pip install 'loupe[ui]'`."
    ) from exc

from loupe.annotation import Annotation, AnnotationStore
from loupe.report import render_trace_markdown
from loupe.store import _default_dir

STATIC_DIR = Path(__file__).parent / "static"


class AnnotationIn(BaseModel):
    step_id: str
    failure_category: str
    notes: str = ""
    mitigation: str = ""
    severity: str = "medium"
    annotator: str = ""
    tags: list[str] = []


def create_app() -> FastAPI:
    app = FastAPI(title="Loupe", docs_url=None, redoc_url=None)

    def traces_dir() -> Path:
        d = _default_dir() / "traces"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def ann_store() -> AnnotationStore:
        return AnnotationStore()

    @app.get("/api/stats")
    def stats() -> JSONResponse:
        files = list(traces_dir().glob("*.jsonl"))
        failed = 0
        total_steps = 0
        for f in files:
            header = _read_header(f)
            if header is None:
                continue
            if header.get("metadata", {}).get("failed"):
                failed += 1
            total_steps += max(0, sum(1 for _ in f.open("r", encoding="utf-8")) - 1)
        annotations = sum(len(v) for v in ann_store().all().values())
        return JSONResponse(
            {
                "trace_count": len(files),
                "failed_count": failed,
                "step_count": total_steps,
                "annotation_count": annotations,
            }
        )

    @app.get("/api/traces")
    def list_traces() -> JSONResponse:
        items: list[dict[str, Any]] = []
        files = sorted(traces_dir().glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        all_annotations = ann_store().all()
        for file in files:
            header = _read_header(file)
            if header is None:
                continue
            steps_count = max(0, sum(1 for _ in file.open("r", encoding="utf-8")) - 1)
            header["step_count"] = steps_count
            header["annotation_count"] = len(all_annotations.get(header["trace_id"], []))
            items.append(header)
        return JSONResponse(items)

    @app.get("/api/traces/{trace_id}")
    def get_trace(trace_id: str) -> JSONResponse:
        path = _find_trace(traces_dir(), trace_id)
        if path is None:
            raise HTTPException(status_code=404, detail="trace not found")

        header: dict[str, Any] = {}
        steps: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line)
                kind = obj.pop("_type", None)
                if kind == "trace":
                    header = obj
                elif kind == "step":
                    steps.append(obj)
        header["steps"] = steps
        header["annotations"] = [asdict(a) for a in ann_store().load(header["trace_id"])]
        return JSONResponse(header)

    @app.get("/api/traces/{trace_id}/annotations")
    def get_annotations(trace_id: str) -> JSONResponse:
        path = _find_trace(traces_dir(), trace_id)
        if path is None:
            raise HTTPException(status_code=404, detail="trace not found")
        return JSONResponse([asdict(a) for a in ann_store().load(path.stem)])

    @app.post("/api/traces/{trace_id}/annotations")
    def add_annotation(trace_id: str, payload: AnnotationIn) -> JSONResponse:
        path = _find_trace(traces_dir(), trace_id)
        if path is None:
            raise HTTPException(status_code=404, detail="trace not found")
        ann = Annotation(
            trace_id=path.stem,
            step_id=payload.step_id,
            failure_category=payload.failure_category,  # type: ignore[arg-type]
            notes=payload.notes,
            mitigation=payload.mitigation,
            severity=payload.severity,  # type: ignore[arg-type]
            annotator=payload.annotator,
            tags=payload.tags,
        )
        ann_store().add(ann)
        return JSONResponse(asdict(ann))

    @app.delete("/api/traces/{trace_id}/annotations/{step_id}")
    def remove_annotation(trace_id: str, step_id: str) -> JSONResponse:
        path = _find_trace(traces_dir(), trace_id)
        if path is None:
            raise HTTPException(status_code=404, detail="trace not found")
        removed = ann_store().remove(path.stem, step_id)
        return JSONResponse({"removed": removed})

    @app.get("/api/traces/{trace_id}/report")
    def trace_report(trace_id: str) -> PlainTextResponse:
        """Render the trace as a shareable markdown case file."""
        path = _find_trace(traces_dir(), trace_id)
        if path is None:
            raise HTTPException(status_code=404, detail="trace not found")
        md = render_trace_markdown(path)
        return PlainTextResponse(md, media_type="text/markdown")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


def _find_trace(traces_dir: Path, trace_id: str) -> Path | None:
    matches = list(traces_dir.glob(f"{trace_id}*.jsonl"))
    return matches[0] if matches else None


def _read_header(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as f:
            first = json.loads(next(f))
        if first.get("_type") != "trace":
            return None
        first.pop("_type", None)
        return first
    except (StopIteration, json.JSONDecodeError):
        return None
