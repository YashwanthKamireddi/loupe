"""``loupe otlp`` — convert captured Loupe traces to OpenTelemetry OTLP JSON.

Lets Loupe drop into any OTel-compatible observability pipeline (Datadog APM,
Honeycomb, Jaeger, Tempo, Grafana Cloud, New Relic, AWS X-Ray, …) without
custom integration code. We emit:

  - One OTLP resource span group per Loupe trace.
  - One OTLP span per Loupe step (kind=CLIENT for llm-call/tool-call,
    INTERNAL otherwise).
  - GenAI Semantic Convention attributes (``gen_ai.system``,
    ``gen_ai.request.model``, ``gen_ai.usage.input_tokens``, etc.) on
    llm-call spans so OTel-aware viewers render them as first-class
    AI spans.

The JSON shape matches the OTLP/HTTP body format
(``opentelemetry-proto`` repo) — POST it directly to an OTLP collector.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Identifier helpers — OTLP spans need 16-byte trace IDs + 8-byte span IDs.
# Loupe uses 32-hex trace IDs (16 bytes) and 12-hex step IDs (6 bytes), so
# we left-pad/truncate to match.
# ---------------------------------------------------------------------------


def _trace_id_hex(loupe_trace_id: str) -> str:
    """Normalize a Loupe trace_id to a 32-hex OTLP traceId.

    Loupe ids are already 32 hex chars (uuid4 stripped of dashes). If the
    id is shorter (legacy or hand-crafted) we left-pad with zeros; if it's
    longer we truncate from the right. Either way the result is 32 chars.
    """
    cleaned = "".join(c for c in loupe_trace_id.lower() if c in "0123456789abcdef")
    if len(cleaned) >= 32:
        return cleaned[:32]
    return cleaned.rjust(32, "0")


def _span_id_hex(loupe_step_id: str | None) -> str:
    """Normalize a Loupe step_id to a 16-hex OTLP spanId.

    Loupe step ids are typically 12 hex chars. Pad right with zeros to 16.
    If None, generate a fresh random id (so the OTLP doc still validates).
    """
    if not loupe_step_id:
        return uuid.uuid4().hex[:16]
    cleaned = "".join(c for c in loupe_step_id.lower() if c in "0123456789abcdef")
    if len(cleaned) >= 16:
        return cleaned[:16]
    return cleaned.ljust(16, "0")


def _to_unix_nanos(seconds: float | None) -> str:
    """OTLP times are unsigned int64 nanoseconds, serialized as strings.

    We always return strings (even for 0) because protobuf JSON encodes
    int64 as strings and many collectors are strict about this.
    """
    if seconds is None:
        return "0"
    return str(int(seconds * 1_000_000_000))


# ---------------------------------------------------------------------------
# Attribute encoders — OTLP "AnyValue" uses tagged unions.
# ---------------------------------------------------------------------------


def _any_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if value is None:
        return {"stringValue": ""}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_any_value(v) for v in value]}}
    if isinstance(value, dict):
        return {
            "kvlistValue": {
                "values": [
                    {"key": str(k), "value": _any_value(v)} for k, v in value.items()
                ]
            }
        }
    # Last resort — render via repr so the attribute is still searchable.
    return {"stringValue": repr(value)}


def _attr(key: str, value: Any) -> dict[str, Any]:
    return {"key": key, "value": _any_value(value)}


# ---------------------------------------------------------------------------
# Step → OTLP span
# ---------------------------------------------------------------------------


# OTLP SpanKind enum values (per opentelemetry-proto/trace/v1/trace.proto).
SPAN_KIND_INTERNAL = 1
SPAN_KIND_CLIENT = 3


@dataclass(frozen=True)
class OTLPSpan:
    """Lightweight intermediate representation — easier to test than the
    nested JSON dict."""

    trace_id_hex: str
    span_id_hex: str
    parent_span_id_hex: str | None
    name: str
    kind: int
    start_unix_nanos: str
    end_unix_nanos: str
    attributes: dict[str, Any]
    status_code: int
    status_message: str | None


# OTLP Status codes
STATUS_UNSET = 0
STATUS_OK = 1
STATUS_ERROR = 2


def step_to_span(*, trace_id: str, step: dict[str, Any]) -> OTLPSpan:
    """Convert one Loupe step (a dict, as serialized in JSONL) to an OTLPSpan.

    The step is expected to follow the wire format documented in
    docs/SPEC.md — ``step_id``, ``kind``, ``name``, ``started_at``,
    ``ended_at``, ``inputs``, ``outputs``, ``metadata``, ``error``.
    """
    kind = step.get("kind") or "custom"
    span_kind = SPAN_KIND_CLIENT if kind in ("llm-call", "tool-call") else SPAN_KIND_INTERNAL

    inputs = step.get("inputs") or {}
    outputs = step.get("outputs") or {}
    metadata = step.get("metadata") or {}

    attributes: dict[str, Any] = {"loupe.kind": kind}

    # GenAI Semantic Conventions (stable in OTel 2026) for LLM calls.
    if kind == "llm-call":
        if isinstance(inputs.get("provider"), str):
            attributes["gen_ai.system"] = inputs["provider"]
            attributes["gen_ai.operation.name"] = "chat"
        if isinstance(inputs.get("model"), str):
            attributes["gen_ai.request.model"] = inputs["model"]
            attributes["gen_ai.response.model"] = inputs["model"]
        if isinstance(inputs.get("max_tokens"), int):
            attributes["gen_ai.request.max_tokens"] = inputs["max_tokens"]
        if inputs.get("stream"):
            attributes["gen_ai.request.streaming"] = True
        if isinstance(outputs.get("input_tokens"), int):
            attributes["gen_ai.usage.input_tokens"] = outputs["input_tokens"]
        if isinstance(outputs.get("output_tokens"), int):
            attributes["gen_ai.usage.output_tokens"] = outputs["output_tokens"]
        if isinstance(outputs.get("finish_reason"), str):
            attributes["gen_ai.response.finish_reason"] = outputs["finish_reason"]
        elif isinstance(outputs.get("stop_reason"), str):
            attributes["gen_ai.response.finish_reason"] = outputs["stop_reason"]
        if outputs.get("rate_limited"):
            attributes["gen_ai.response.rate_limited"] = True

    # HTTP attributes for proxy-captured + universal-httpx steps.
    if isinstance(metadata.get("transport"), str):
        attributes["loupe.transport"] = metadata["transport"]
    if isinstance(metadata.get("method"), str):
        attributes["http.request.method"] = metadata["method"]
    if isinstance(metadata.get("path"), str):
        attributes["url.path"] = metadata["path"]
    if isinstance(outputs.get("status"), int):
        attributes["http.response.status_code"] = outputs["status"]

    # Tool-call attributes
    if kind == "tool-call" and isinstance(step.get("name"), str):
        attributes["loupe.tool.name"] = step["name"]

    error = step.get("error")
    if error:
        status_code = STATUS_ERROR
        status_msg: str | None = str(error)
    elif isinstance(outputs.get("status"), int) and outputs["status"] >= 500:
        status_code = STATUS_ERROR
        status_msg = f"upstream returned {outputs['status']}"
    else:
        status_code = STATUS_OK
        status_msg = None

    parent_step_id = step.get("parent_step_id")
    parent_span_id = _span_id_hex(parent_step_id) if parent_step_id else None

    return OTLPSpan(
        trace_id_hex=_trace_id_hex(trace_id),
        span_id_hex=_span_id_hex(step.get("step_id")),
        parent_span_id_hex=parent_span_id,
        name=step.get("name") or kind,
        kind=span_kind,
        start_unix_nanos=_to_unix_nanos(step.get("started_at")),
        end_unix_nanos=_to_unix_nanos(step.get("ended_at") or step.get("started_at")),
        attributes=attributes,
        status_code=status_code,
        status_message=status_msg,
    )


def span_to_json(span: OTLPSpan) -> dict[str, Any]:
    """Render an OTLPSpan as the dict expected inside ``resourceSpans[].scopeSpans[].spans[]``."""
    out: dict[str, Any] = {
        "traceId": span.trace_id_hex,
        "spanId": span.span_id_hex,
        "name": span.name,
        "kind": span.kind,
        "startTimeUnixNano": span.start_unix_nanos,
        "endTimeUnixNano": span.end_unix_nanos,
        "attributes": [_attr(k, v) for k, v in span.attributes.items()],
        "status": {"code": span.status_code},
    }
    if span.parent_span_id_hex:
        out["parentSpanId"] = span.parent_span_id_hex
    if span.status_message:
        out["status"]["message"] = span.status_message
    return out


# ---------------------------------------------------------------------------
# JSONL file → OTLP document
# ---------------------------------------------------------------------------


def read_trace_jsonl(path: Path) -> dict[str, Any] | None:
    """Parse a Loupe JSONL file into ``{header, steps}`` or return None
    on a malformed file. The proxy and the @trace decorator both write
    line-0 = trace header and lines 1..N = steps.

    Routes through :func:`loupe._crypto.read_trace_text` so opt-in
    encrypted traces decrypt transparently.
    """
    from loupe.store import load_trace_split
    try:
        header, steps, _ = load_trace_split(path)
    except OSError:
        return None
    if header is None:
        return None
    # Re-attach the _type marker load_trace_split strips, so the OTLP
    # builder downstream keeps seeing the canonical wire shape.
    header = {"_type": "trace", **header}
    return {"header": header, "steps": [{"_type": "step", **s} for s in steps]}


def trace_to_resource_spans(
    *,
    header: dict[str, Any],
    steps: Iterable[dict[str, Any]],
    service_name: str = "loupe",
) -> dict[str, Any]:
    """Convert one Loupe trace (header + steps) to an OTLP ``resourceSpans`` entry."""
    trace_id = header.get("trace_id") or ""
    spans = [
        span_to_json(step_to_span(trace_id=trace_id, step=step))
        for step in steps
    ]

    resource_attrs: dict[str, Any] = {
        "service.name": service_name,
        "service.namespace": "loupe",
        "loupe.trace.name": header.get("name") or "",
        "loupe.trace.id": header.get("trace_id") or "",
    }
    if isinstance(header.get("framework"), str):
        resource_attrs["loupe.trace.framework"] = header["framework"]
    if header.get("metadata") and isinstance(header["metadata"], dict):
        if header["metadata"].get("failed"):
            resource_attrs["loupe.trace.failed"] = True
        if isinstance(header["metadata"].get("error"), str):
            resource_attrs["loupe.trace.error"] = header["metadata"]["error"]

    return {
        "resource": {
            "attributes": [_attr(k, v) for k, v in resource_attrs.items()],
        },
        "scopeSpans": [
            {
                "scope": {"name": "loupe", "version": _scope_version()},
                "spans": spans,
            }
        ],
    }


def _scope_version() -> str:
    try:
        from loupe._version import __version__
        return __version__
    except ImportError:
        return "unknown"


def build_otlp_document(
    paths: Iterable[Path],
    *,
    service_name: str = "loupe",
) -> dict[str, Any]:
    """Build a complete OTLP/HTTP JSON document from a list of trace files.

    Skips files that don't parse. Returns ``{"resourceSpans": [...]}`` —
    POST this body to ``<otlp-collector>/v1/traces`` with header
    ``content-type: application/json``.
    """
    resource_spans: list[dict[str, Any]] = []
    for path in paths:
        parsed = read_trace_jsonl(path)
        if parsed is None:
            continue
        resource_spans.append(
            trace_to_resource_spans(
                header=parsed["header"],
                steps=parsed["steps"],
                service_name=service_name,
            )
        )
    return {"resourceSpans": resource_spans}


def export_traces(
    *,
    traces_dir: Path,
    out: Path | None,
    service_name: str = "loupe",
    trace_id_prefix: str | None = None,
) -> tuple[int, Path | None]:
    """Walk ``traces_dir`` and write an OTLP JSON file.

    Args:
        traces_dir: directory containing ``*.jsonl`` files.
        out: where to write. If None, returns the doc without writing
            (used by tests + callers who want to POST directly).
        trace_id_prefix: if set, only files whose stem starts with this
            prefix are exported. Lets users select a subset.

    Returns:
        ``(trace_count, written_path or None)``.
    """
    pattern = f"{trace_id_prefix}*.jsonl" if trace_id_prefix else "*.jsonl"
    paths = sorted(traces_dir.glob(pattern))
    doc = build_otlp_document(paths, service_name=service_name)
    count = len(doc["resourceSpans"])
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            json.dump(doc, f, separators=(",", ":"))
            f.write(os.linesep)
    return count, out
