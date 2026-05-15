"""FastAPI app behind `loupe ui`.

Endpoints:
  GET    /api/traces                         List traces (header only, newest first)
  POST   /api/traces                         Ingest a trace from ANY language
  GET    /api/traces/{id}                    Full trace with steps + annotations
  GET    /api/traces/{id}/annotations        List annotations for one trace
  POST   /api/traces/{id}/annotations        Create/update an annotation
  DELETE /api/traces/{id}/annotations/{step} Remove an annotation
  GET    /api/traces/{id}/report             Render trace as markdown case file
  GET    /api/stats                          Aggregate counts for the header banner
  GET    /api/events                         Server-Sent Events stream — new traces
  GET    /                                   Static SPA
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import (
        FileResponse,
        JSONResponse,
        PlainTextResponse,
        StreamingResponse,
    )
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "loupe ui requires fastapi + uvicorn. "
        "Install with `pip install 'loupe[ui]'`."
    ) from exc

from loupe.annotation import Annotation, AnnotationStore
from loupe.ingest import IngestError, ingest
from loupe.report import render_trace_markdown
from loupe.store import _default_dir

# Polling interval (seconds) for the SSE watcher loop
_EVENTS_POLL_INTERVAL = 1.5
# Keep-alive comment interval so reverse proxies don't time the SSE stream out
_EVENTS_KEEPALIVE = 25.0

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

    @app.post("/api/traces", status_code=201)
    async def ingest_trace(request: Request) -> JSONResponse:
        """Accept a trace document from any language and persist it.

        The shape mirrors the canonical JSONL wire format. See docs/SPEC.md
        for the full schema. Returns the trace_id so callers can link to it.
        """
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc
        try:
            trace = ingest(payload)
        except IngestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse(
            {
                "trace_id": trace.trace_id,
                "name": trace.name,
                "framework": trace.framework,
                "step_count": len(trace.steps),
            },
            status_code=201,
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

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        """Server-Sent Events: push `new_trace` and `new_annotation` events
        to connected dashboards. Polled from the local filesystem — no extra
        deps beyond stdlib.
        """

        async def stream() -> AsyncIterator[str]:
            d = traces_dir()
            seen_traces = {f.stem for f in d.glob("*.jsonl")}
            ann_dir = _default_dir() / "annotations"
            seen_annotations = (
                {f.stem for f in ann_dir.glob("*.json")} if ann_dir.exists() else set()
            )
            # Hello frame so the client confirms the connection
            yield f"event: hello\ndata: {json.dumps({'ok': True})}\n\n"

            last_keepalive = asyncio.get_event_loop().time()
            while True:
                if await request.is_disconnected():
                    break

                current = {f.stem for f in d.glob("*.jsonl")}
                new_traces = current - seen_traces
                for trace_id in sorted(new_traces):
                    yield (
                        "event: new_trace\n"
                        f"data: {json.dumps({'trace_id': trace_id})}\n\n"
                    )
                seen_traces = current

                if ann_dir.exists():
                    current_ann = {f.stem for f in ann_dir.glob("*.json")}
                    new_ann = current_ann - seen_annotations
                    for trace_id in sorted(new_ann):
                        yield (
                            "event: annotation_changed\n"
                            f"data: {json.dumps({'trace_id': trace_id})}\n\n"
                        )
                    seen_annotations = current_ann

                # Keep-alive comment frame
                now = asyncio.get_event_loop().time()
                if now - last_keepalive > _EVENTS_KEEPALIVE:
                    yield ": keep-alive\n\n"
                    last_keepalive = now

                await asyncio.sleep(_EVENTS_POLL_INTERVAL)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
            },
        )

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
