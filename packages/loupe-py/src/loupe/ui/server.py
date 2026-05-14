"""FastAPI app behind `loupe ui`.

Endpoints:
  GET /api/traces          → list traces (header only, newest first)
  GET /api/traces/{id}     → full trace with steps
  GET /                    → static dashboard
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "loupe ui requires fastapi + uvicorn. "
        "Install with `pip install 'loupe[ui]'`."
    ) from exc

from loupe.store import _default_dir

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Loupe", docs_url=None, redoc_url=None)

    traces_dir = _default_dir() / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    @app.get("/api/traces")
    def list_traces() -> JSONResponse:
        items = []
        files = sorted(
            traces_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for file in files:
            header = _read_header(file)
            if header is None:
                continue
            steps_count = max(0, sum(1 for _ in file.open("r", encoding="utf-8")) - 1)
            header["step_count"] = steps_count
            items.append(header)
        return JSONResponse(items)

    @app.get("/api/traces/{trace_id}")
    def get_trace(trace_id: str) -> JSONResponse:
        matches = list(traces_dir.glob(f"{trace_id}*.jsonl"))
        if not matches:
            raise HTTPException(status_code=404, detail="trace not found")
        path = matches[0]

        header: dict = {}
        steps: list[dict] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line)
                kind = obj.pop("_type", None)
                if kind == "trace":
                    header = obj
                elif kind == "step":
                    steps.append(obj)
        header["steps"] = steps
        return JSONResponse(header)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


def _read_header(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as f:
            first = json.loads(next(f))
        if first.get("_type") != "trace":
            return None
        first.pop("_type", None)
        return first
    except (StopIteration, json.JSONDecodeError):
        return None
