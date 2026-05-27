"""``loupe export --format parquet`` — columnar export for analytics teams.

One row per Loupe Step, columns flattened into the schema below. Designed
to load straight into Databricks / Snowflake / DuckDB / BigQuery for
SQL-driven analysis of agent behavior over time:

  - cost-by-day, by-provider, by-model
  - p50 / p95 latency per step kind
  - failure-rate over rolling windows
  - tool-call frequency, etc.

Schema (Parquet column → source):

  trace_id          STRING   trace.trace_id
  trace_name        STRING   trace.name
  trace_framework   STRING   trace.framework
  trace_failed      BOOLEAN  trace.metadata.failed
  trace_started_at  TIMESTAMP(seconds since epoch)
  trace_ended_at    TIMESTAMP

  step_id           STRING
  step_index        INTEGER  position in the trace (0-indexed)
  step_kind         STRING   llm-call / tool-call / thought / …
  step_name         STRING
  step_started_at   TIMESTAMP
  step_ended_at     TIMESTAMP
  duration_ms       DOUBLE   computed

  provider          STRING   inputs.provider
  model             STRING   inputs.model
  input_tokens      INTEGER  outputs.input_tokens
  output_tokens     INTEGER  outputs.output_tokens
  finish_reason     STRING
  http_status       INTEGER  outputs.status
  rate_limited      BOOLEAN  outputs.rate_limited
  error             STRING   step.error (NULL when none)
  inputs_json       STRING   full inputs dict, JSON-encoded (queryable in DuckDB)
  outputs_json      STRING   full outputs dict, JSON-encoded

We write through DuckDB so the Parquet file is widely compatible — DuckDB
emits standard Apache Parquet that Spark, pandas, Polars all read.
"""

from __future__ import annotations

import json as _json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def export_traces_to_parquet(
    *,
    traces_dir: Path,
    out: Path,
    trace_id_prefix: str | None = None,
) -> tuple[int, int, Path]:
    """Walk ``traces_dir``, flatten every step, write a single Parquet file.

    Returns ``(trace_count, step_count, written_path)``.

    Raises ``RuntimeError`` if duckdb isn't installed (it's a hard
    dep, so this is mostly a clarity guard).
    """
    try:
        import duckdb  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "loupe export --format parquet needs duckdb. "
            "install with: pip install duckdb"
        ) from exc

    pattern = f"{trace_id_prefix}*.jsonl" if trace_id_prefix else "*.jsonl"
    paths = sorted(traces_dir.glob(pattern))

    rows: list[dict[str, Any]] = []
    trace_count = 0
    for path in paths:
        parsed = _read_trace(path)
        if parsed is None:
            continue
        trace_count += 1
        header = parsed["header"]
        steps = parsed["steps"]
        trace_failed = bool((header.get("metadata") or {}).get("failed"))
        for idx, step in enumerate(steps):
            inputs = step.get("inputs") or {}
            outputs = step.get("outputs") or {}
            started = step.get("started_at")
            ended = step.get("ended_at")
            duration_ms: float | None = None
            if isinstance(started, (int, float)) and isinstance(ended, (int, float)):
                duration_ms = (ended - started) * 1000.0
            rows.append({
                "trace_id":        header.get("trace_id"),
                "trace_name":      header.get("name"),
                "trace_framework": header.get("framework"),
                "trace_failed":    trace_failed,
                "trace_started_at": header.get("started_at"),
                "trace_ended_at":  header.get("ended_at"),
                "step_id":         step.get("step_id"),
                "step_index":      idx,
                "step_kind":       step.get("kind"),
                "step_name":       step.get("name"),
                "step_started_at": started,
                "step_ended_at":   ended,
                "duration_ms":     duration_ms,
                "provider":        inputs.get("provider"),
                "model":           inputs.get("model"),
                "input_tokens":    _safe_int(outputs.get("input_tokens")),
                "output_tokens":   _safe_int(outputs.get("output_tokens")),
                "finish_reason":   outputs.get("finish_reason") or outputs.get("stop_reason"),
                "http_status":     _safe_int(outputs.get("status")),
                "rate_limited":    bool(outputs.get("rate_limited")),
                "error":           step.get("error"),
                "inputs_json":     _json.dumps(inputs, default=str),
                "outputs_json":    _json.dumps(outputs, default=str),
            })

    # Use DuckDB's native Parquet writer — even when rows is empty we
    # still emit a typed Parquet file so downstream pipelines don't
    # break on "missing file".
    out.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(":memory:")
    try:
        # Always create the table with a fixed schema so empty exports
        # still produce a valid Parquet file and downstream consumers
        # see the columns they expect.
        conn.execute(
            """
            CREATE TABLE loupe_steps (
                trace_id        VARCHAR,
                trace_name      VARCHAR,
                trace_framework VARCHAR,
                trace_failed    BOOLEAN,
                trace_started_at DOUBLE,
                trace_ended_at  DOUBLE,
                step_id         VARCHAR,
                step_index      INTEGER,
                step_kind       VARCHAR,
                step_name       VARCHAR,
                step_started_at DOUBLE,
                step_ended_at   DOUBLE,
                duration_ms     DOUBLE,
                provider        VARCHAR,
                model           VARCHAR,
                input_tokens    BIGINT,
                output_tokens   BIGINT,
                finish_reason   VARCHAR,
                http_status     BIGINT,
                rate_limited    BOOLEAN,
                error           VARCHAR,
                inputs_json     VARCHAR,
                outputs_json    VARCHAR
            );
            """
        )
        if rows:
            cols = [
                "trace_id", "trace_name", "trace_framework", "trace_failed",
                "trace_started_at", "trace_ended_at",
                "step_id", "step_index", "step_kind", "step_name",
                "step_started_at", "step_ended_at", "duration_ms",
                "provider", "model", "input_tokens", "output_tokens",
                "finish_reason", "http_status", "rate_limited", "error",
                "inputs_json", "outputs_json",
            ]
            placeholders = ", ".join("?" * len(cols))
            conn.executemany(
                f"INSERT INTO loupe_steps ({', '.join(cols)}) VALUES ({placeholders});",
                [tuple(r.get(c) for c in cols) for r in rows],
            )
        conn.execute(
            f"COPY loupe_steps TO '{out.as_posix()}' (FORMAT PARQUET, "
            "COMPRESSION ZSTD);"
        )
    finally:
        conn.close()
    return trace_count, len(rows), out


def _read_trace(path: Path) -> dict[str, Any] | None:
    """Parse a JSONL trace file (encrypted or plaintext) into ``{header, steps}``.

    Returns None on any parse failure so a malformed file doesn't break
    a bulk export.
    """
    from loupe.store import load_trace_split
    try:
        header, steps, _ = load_trace_split(path)
    except Exception:  # noqa: BLE001 — crypto / IO failure → skip
        return None
    if header is None:
        return None
    return {"header": header, "steps": steps}


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = ["export_traces_to_parquet"]


# Silence the "Iterable imported but unused" warning that ruff might
# raise; Iterable is part of the public type vocabulary even if not
# annotated above.
_ = Iterable
