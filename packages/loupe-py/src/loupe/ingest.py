"""Language-agnostic HTTP ingest — anything that can POST JSON can ship traces.

The Loupe wire format is documented in docs/SPEC.md. This module validates an
incoming trace document, writes it to the canonical JSONL location, and lets
the SSE watcher in the UI server pick it up like any other trace.

A minimal payload:

    {
      "trace_id": "9af3...",
      "name": "my-go-agent",
      "framework": "go-anthropic",
      "started_at": 1778800000.0,
      "ended_at":   1778800001.2,
      "metadata": {"failed": false},
      "steps": [
        {
          "step_id": "s1",
          "kind": "llm-call",
          "name": "anthropic:claude-haiku-4-5",
          "started_at": 1778800000.1,
          "ended_at":   1778800000.9,
          "inputs":  {"prompt": "hi"},
          "outputs": {"text": "hello"}
        }
      ]
    }
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from loupe.store import JSONLStore
from loupe.trace import Step, Trace

_ALLOWED_KINDS = {"llm-call", "tool-call", "io", "thought", "error", "custom"}


class IngestError(ValueError):
    """Raised when an incoming trace document fails validation."""


def ingest(payload: dict[str, Any], *, store: JSONLStore | None = None) -> Trace:
    """Validate and persist an externally-submitted trace.

    Lenient about missing optional fields — fills sensible defaults so a
    one-line `curl` works. Strict about the required shape: `name` and
    `steps` (list of dicts) MUST be present, and each step needs a `kind`.
    """
    if not isinstance(payload, dict):
        raise IngestError("trace payload must be a JSON object")

    name = payload.get("name")
    if not isinstance(name, str) or not name:
        raise IngestError("trace.name is required and must be a non-empty string")

    steps_raw = payload.get("steps")
    if not isinstance(steps_raw, list):
        raise IngestError("trace.steps must be a list (may be empty)")

    now = time.time()
    trace_id = str(payload.get("trace_id") or uuid.uuid4().hex)
    started_at = float(payload.get("started_at") or now)
    ended_at = (
        float(payload["ended_at"]) if "ended_at" in payload and payload["ended_at"] is not None
        else now
    )

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise IngestError("trace.metadata must be an object")

    steps: list[Step] = []
    for idx, raw in enumerate(steps_raw):
        if not isinstance(raw, dict):
            raise IngestError(f"steps[{idx}] must be an object")
        kind = raw.get("kind")
        if kind not in _ALLOWED_KINDS:
            raise IngestError(
                f"steps[{idx}].kind must be one of {sorted(_ALLOWED_KINDS)}, got {kind!r}"
            )
        step_name = raw.get("name")
        if not isinstance(step_name, str) or not step_name:
            raise IngestError(f"steps[{idx}].name is required")
        s_started = float(raw.get("started_at") or started_at)
        s_ended_raw = raw.get("ended_at")
        s_ended: float | None = float(s_ended_raw) if s_ended_raw is not None else s_started
        steps.append(
            Step(
                step_id=str(raw.get("step_id") or uuid.uuid4().hex[:12]),
                parent_step_id=raw.get("parent_step_id"),
                kind=kind,
                name=step_name,
                started_at=s_started,
                ended_at=s_ended,
                inputs=dict(raw.get("inputs") or {}),
                outputs=dict(raw.get("outputs") or {}),
                metadata=dict(raw.get("metadata") or {}),
                error=raw.get("error"),
            )
        )

    trace = Trace(
        trace_id=trace_id,
        name=name,
        framework=payload.get("framework"),
        started_at=started_at,
        ended_at=ended_at,
        steps=steps,
        metadata=metadata,
    )

    (store or JSONLStore()).save(trace)
    return trace
