"""``loupe otlp`` — OpenTelemetry OTLP/HTTP JSON export tests.

Validates that:
  1. Identifier conversion is OTLP-compliant (32-hex trace, 16-hex span,
     stringified int64 nanoseconds).
  2. LLM-call steps get the right GenAI Semantic Convention attributes.
  3. HTTP attributes (status, method, path) land for proxy-captured steps.
  4. Error / 5xx steps emit `status.code = ERROR` (2).
  5. Round-tripping a Loupe JSONL trace through ``export_traces`` produces
     a valid OTLP document with the expected resourceSpans / spans nesting.
"""

from __future__ import annotations

import json
from pathlib import Path

from loupe.otlp import (
    STATUS_ERROR,
    STATUS_OK,
    OTLPSpan,
    build_otlp_document,
    export_traces,
    read_trace_jsonl,
    span_to_json,
    step_to_span,
)

# ---------------------------------------------------------------------------
# Identifier + attribute conversion
# ---------------------------------------------------------------------------


def test_step_to_span_normalizes_trace_and_span_ids() -> None:
    step = {
        "step_id":     "abc123def456",          # 12 hex (typical loupe step id)
        "kind":        "llm-call",
        "name":        "anthropic:claude-haiku-4-5",
        "started_at":  1.0,
        "ended_at":    1.5,
        "inputs":      {"provider": "anthropic", "model": "claude-haiku-4-5"},
        "outputs":     {"status": 200, "input_tokens": 4, "output_tokens": 2},
        "metadata":    {"transport": "proxy", "method": "POST", "path": "/v1/messages"},
        "error":       None,
    }
    span: OTLPSpan = step_to_span(trace_id="0123456789abcdef0123456789abcdef", step=step)
    assert len(span.trace_id_hex) == 32
    assert len(span.span_id_hex) == 16
    assert span.span_id_hex == "abc123def4560000"   # 12 hex padded to 16
    assert span.start_unix_nanos == "1000000000"
    assert span.end_unix_nanos == "1500000000"
    assert span.status_code == STATUS_OK


def test_step_to_span_emits_gen_ai_semantic_conventions() -> None:
    step = {
        "step_id":   "stp1",
        "kind":      "llm-call",
        "name":      "openai:gpt-4o-mini",
        "started_at": 0.0,
        "ended_at":   0.5,
        "inputs": {
            "provider":   "openai",
            "model":      "gpt-4o-mini",
            "max_tokens": 256,
            "stream":     True,
        },
        "outputs": {
            "status":         200,
            "input_tokens":   12,
            "output_tokens":  48,
            "finish_reason":  "stop",
            "text":           "Hello world",
        },
        "metadata": {"transport": "fetch"},
    }
    span = step_to_span(trace_id="cafebabecafebabecafebabecafebabe", step=step)
    a = span.attributes
    assert a["gen_ai.system"] == "openai"
    assert a["gen_ai.operation.name"] == "chat"
    assert a["gen_ai.request.model"] == "gpt-4o-mini"
    assert a["gen_ai.response.model"] == "gpt-4o-mini"
    assert a["gen_ai.request.max_tokens"] == 256
    assert a["gen_ai.request.streaming"] is True
    assert a["gen_ai.usage.input_tokens"] == 12
    assert a["gen_ai.usage.output_tokens"] == 48
    assert a["gen_ai.response.finish_reason"] == "stop"


def test_step_to_span_marks_5xx_as_error_status() -> None:
    step = {
        "step_id":    "stp1",
        "kind":       "llm-call",
        "name":       "anthropic:claude",
        "started_at": 0.0, "ended_at": 0.1,
        "inputs":     {"provider": "anthropic"},
        "outputs":    {"status": 502},
        "metadata":   {"transport": "proxy"},
        "error":      None,
    }
    span = step_to_span(trace_id="0" * 32, step=step)
    assert span.status_code == STATUS_ERROR
    assert span.status_message and "502" in span.status_message


def test_step_to_span_marks_429_rate_limit() -> None:
    step = {
        "step_id":    "stp1",
        "kind":       "llm-call",
        "name":       "openai:gpt-4o",
        "started_at": 0.0, "ended_at": 0.1,
        "inputs":     {"provider": "openai"},
        "outputs":    {"status": 429, "rate_limited": True},
        "metadata":   {},
    }
    span = step_to_span(trace_id="0" * 32, step=step)
    assert span.attributes["gen_ai.response.rate_limited"] is True
    assert span.attributes["http.response.status_code"] == 429


def test_step_to_span_uses_anthropic_stop_reason_fallback() -> None:
    step = {
        "step_id":    "stp1",
        "kind":       "llm-call",
        "name":       "anthropic:claude",
        "started_at": 0.0, "ended_at": 0.1,
        "inputs":     {"provider": "anthropic", "model": "claude-haiku-4-5"},
        "outputs":    {"status": 200, "stop_reason": "end_turn"},
        "metadata":   {},
    }
    span = step_to_span(trace_id="0" * 32, step=step)
    assert span.attributes["gen_ai.response.finish_reason"] == "end_turn"


def test_span_to_json_emits_otlp_envelope_keys() -> None:
    step = {
        "step_id":     "abc1",
        "kind":        "llm-call",
        "name":        "anthropic:claude",
        "started_at":  1.0, "ended_at": 1.25,
        "inputs":      {"provider": "anthropic", "model": "claude-haiku-4-5"},
        "outputs":     {"status": 200},
        "metadata":    {},
    }
    span = step_to_span(trace_id="cafe" * 8, step=step)
    js = span_to_json(span)
    # Required by the OTLP/HTTP JSON schema
    assert set(js.keys()) >= {
        "traceId", "spanId", "name", "kind",
        "startTimeUnixNano", "endTimeUnixNano", "attributes", "status",
    }
    # Attributes are an array of {key, value: AnyValue}
    sample = next(a for a in js["attributes"] if a["key"] == "gen_ai.system")
    assert sample["value"]["stringValue"] == "anthropic"


def test_span_to_json_includes_parent_when_present() -> None:
    step = {
        "step_id":         "child0",
        "parent_step_id":  "par123",
        "kind":            "tool-call",
        "name":            "search",
        "started_at": 0.0, "ended_at": 0.1,
    }
    js = span_to_json(step_to_span(trace_id="0" * 32, step=step))
    assert "parentSpanId" in js
    assert len(js["parentSpanId"]) == 16


# ---------------------------------------------------------------------------
# End-to-end — Loupe JSONL → OTLP document
# ---------------------------------------------------------------------------


def _write_trace(traces_dir: Path, *, trace_id: str, framework: str = "test") -> Path:
    """Write a minimal valid Loupe JSONL trace and return its path."""
    p = traces_dir / f"{trace_id}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "_type":      "trace",
        "trace_id":   trace_id,
        "name":       "demo",
        "framework":  framework,
        "started_at": 1.0,
        "ended_at":   1.5,
        "metadata":   {},
    }
    step = {
        "_type":      "step",
        "step_id":    "abc123def456",
        "parent_step_id": None,
        "kind":       "llm-call",
        "name":       "anthropic:claude-haiku-4-5",
        "started_at": 1.0, "ended_at": 1.4,
        "inputs":     {"provider": "anthropic", "model": "claude-haiku-4-5"},
        "outputs":    {"status": 200, "input_tokens": 4, "output_tokens": 2,
                       "text": "hello"},
        "metadata":   {"transport": "proxy", "method": "POST",
                       "path": "/v1/messages"},
        "error":      None,
    }
    with p.open("w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        f.write(json.dumps(step) + "\n")
    return p


def test_export_traces_writes_valid_otlp_document(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    _write_trace(traces, trace_id="a" * 32)
    _write_trace(traces, trace_id="b" * 32)

    out = tmp_path / "exported.json"
    count, path = export_traces(traces_dir=traces, out=out)
    assert count == 2
    assert path == out
    assert out.exists()

    doc = json.loads(out.read_text())
    assert "resourceSpans" in doc
    assert len(doc["resourceSpans"]) == 2

    rs0 = doc["resourceSpans"][0]
    # Resource attributes carry the loupe namespace + service name
    resource_attrs = {a["key"]: a["value"] for a in rs0["resource"]["attributes"]}
    assert resource_attrs["service.name"]["stringValue"] == "loupe"
    assert "loupe.trace.id" in resource_attrs

    span = rs0["scopeSpans"][0]["spans"][0]
    assert span["name"] == "anthropic:claude-haiku-4-5"
    # 32-hex / 16-hex required by OTLP
    assert len(span["traceId"]) == 32
    assert len(span["spanId"]) == 16
    # GenAI attributes were promoted
    attrs = {a["key"]: a["value"] for a in span["attributes"]}
    assert attrs["gen_ai.system"]["stringValue"] == "anthropic"
    assert attrs["gen_ai.usage.input_tokens"]["intValue"] == "4"
    assert attrs["gen_ai.usage.output_tokens"]["intValue"] == "2"


def test_export_traces_filters_by_trace_id_prefix(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    _write_trace(traces, trace_id="aaaa" + "0" * 28)
    _write_trace(traces, trace_id="bbbb" + "0" * 28)

    out = tmp_path / "exported.json"
    count, _ = export_traces(traces_dir=traces, out=out, trace_id_prefix="aaaa")
    assert count == 1
    doc = json.loads(out.read_text())
    assert len(doc["resourceSpans"]) == 1


def test_export_traces_returns_zero_when_no_match(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    traces.mkdir()
    out = tmp_path / "exported.json"
    count, _ = export_traces(traces_dir=traces, out=out)
    assert count == 0


def test_read_trace_jsonl_skips_malformed_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n", encoding="utf-8")
    assert read_trace_jsonl(bad) is None


def test_build_otlp_document_skips_malformed_files_in_input(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    good_path = _write_trace(traces, trace_id="c" * 32)
    bad_path = traces / "bad.jsonl"
    bad_path.write_text("not json\n", encoding="utf-8")

    doc = build_otlp_document([good_path, bad_path])
    assert len(doc["resourceSpans"]) == 1


def test_anthropic_step_kind_is_client_spankind() -> None:
    """OTel SpanKind.CLIENT = 3 — required for proper service-map rendering."""
    step = {
        "step_id":    "stp1",
        "kind":       "llm-call",
        "name":       "anthropic:claude",
        "started_at": 0.0, "ended_at": 0.1,
        "inputs":     {"provider": "anthropic"},
        "outputs":    {},
        "metadata":   {},
    }
    span = step_to_span(trace_id="0" * 32, step=step)
    assert span.kind == 3  # SPAN_KIND_CLIENT


def test_internal_kinds_use_internal_spankind() -> None:
    """thought / io / custom steps should be SPAN_KIND_INTERNAL = 1."""
    for kind in ("thought", "io", "custom"):
        step = {
            "step_id":    "stp1",
            "kind":       kind,
            "name":       "x",
            "started_at": 0.0, "ended_at": 0.0,
        }
        span = step_to_span(trace_id="0" * 32, step=step)
        assert span.kind == 1, f"{kind} should be INTERNAL"


def test_error_step_propagates_to_otlp_status_error(tmp_path: Path) -> None:
    """A step with `error` non-null must export as OTLP status=ERROR with the
    error string as the status message."""
    step = {
        "step_id":    "stp1",
        "kind":       "llm-call",
        "name":       "anthropic:claude",
        "started_at": 0.0, "ended_at": 0.1,
        "inputs":     {"provider": "anthropic"},
        "outputs":    {},
        "metadata":   {},
        "error":      "ConnectionError: pool exhausted",
    }
    span = step_to_span(trace_id="0" * 32, step=step)
    assert span.status_code == STATUS_ERROR
    assert "ConnectionError" in (span.status_message or "")
