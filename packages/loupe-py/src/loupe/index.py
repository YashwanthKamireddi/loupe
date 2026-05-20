"""Embedded DuckDB index over the JSONL trace store.

Why
---
Every `loupe list / stats / verify --all` historically re-reads every
JSONL header from disk. Fine for a handful of traces; quadratic-feeling
at 10k+. This module maintains an embedded DuckDB index at
``~/.loupe/index.duckdb`` so those queries become millisecond-level
regardless of how many traces are on disk.

Design rules
------------
1. **Never block a trace write.** If the index is unavailable (locked,
   schema-incompatible, disk-full, anything), the caller still
   succeeds. The index is a derived view, not the source of truth.

2. **Always recoverable.** ``loupe index rebuild`` re-creates the
   index from scratch by walking the on-disk JSONL files. Index can
   be deleted at any time without data loss.

3. **Single writer, many readers.** All write paths acquire a
   per-process file lock around the DuckDB transaction. Readers
   open a fresh connection, query, close.

4. **Schema-versioned.** A ``meta`` table holds the schema version.
   On version mismatch the index transparently rebuilds itself.

Public surface
--------------
- :class:`JSONLIndex` — open one against any directory of JSONL files.
- :func:`default_index` — opens the index for ``~/.loupe`` (or whatever
  ``LOUPE_HOME`` points to).
- :func:`upsert_trace_file` — best-effort: index one freshly-written
  JSONL file. Used by :class:`loupe.store.JSONLStore` after each save.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import duckdb
except ImportError:  # pragma: no cover — duckdb is a hard dep but this keeps
    duckdb = None  # type: ignore[assignment]   # mypy + import-time safety

from loupe.store import _default_dir

logger = logging.getLogger("loupe.index")

# Bump when the schema below changes; existing indexes auto-rebuild.
SCHEMA_VERSION = 1

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS traces (
    trace_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    framework     TEXT,
    started_at    DOUBLE NOT NULL,
    ended_at      DOUBLE,
    failed        BOOLEAN DEFAULT FALSE,
    error         TEXT,
    step_count    INTEGER DEFAULT 0,
    file_mtime    DOUBLE NOT NULL,
    indexed_at    DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    trace_id      TEXT NOT NULL,
    step_id       TEXT NOT NULL,
    kind          TEXT NOT NULL,
    name          TEXT NOT NULL,
    started_at    DOUBLE NOT NULL,
    ended_at      DOUBLE,
    error         TEXT,
    PRIMARY KEY (trace_id, step_id)
);

CREATE INDEX IF NOT EXISTS idx_traces_started   ON traces(started_at);
CREATE INDEX IF NOT EXISTS idx_traces_framework ON traces(framework);
CREATE INDEX IF NOT EXISTS idx_steps_kind       ON steps(kind);
"""

# In-process lock so concurrent writers in the same Python process serialize.
# DuckDB itself rejects concurrent writers across processes — that's surfaced
# as a swallowed IOError by the upsert helpers.
_write_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Row dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TraceRow:
    """One row in the ``traces`` table."""

    trace_id: str
    name: str
    framework: str | None
    started_at: float
    ended_at: float | None
    failed: bool
    error: str | None
    step_count: int


# ---------------------------------------------------------------------------
# JSONLIndex — the main class
# ---------------------------------------------------------------------------


class JSONLIndex:
    """A DuckDB-backed index over a directory of JSONL trace files.

    The constructor is cheap (no connection opened). Each public method
    opens a connection, does its work, and closes it — so a single
    long-running ``loupe ui`` process and a separate ``loupe list``
    invocation can share the same index without stepping on each other.
    """

    def __init__(self, db_path: Path, traces_dir: Path) -> None:
        self.db_path = db_path
        self.traces_dir = traces_dir

    # ------------------------------------------------------------------ schema

    def _connect(self, *, read_only: bool = False) -> Any:
        if duckdb is None:
            raise RuntimeError("duckdb is not installed")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return duckdb.connect(str(self.db_path), read_only=read_only)

    def _ensure_schema(self, conn: Any) -> None:
        """Apply DDL + check version; auto-rebuild on mismatch."""
        for stmt in _SCHEMA_DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                [str(SCHEMA_VERSION)],
            )
        elif int(row[0]) != SCHEMA_VERSION:
            # Schema drift — drop everything and rebuild. Safe because the
            # JSONL files on disk are the source of truth.
            conn.execute("DROP TABLE IF EXISTS steps")
            conn.execute("DROP TABLE IF EXISTS traces")
            conn.execute("DELETE FROM meta WHERE key = 'schema_version'")
            for stmt in _SCHEMA_DDL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(stmt)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                [str(SCHEMA_VERSION)],
            )

    # ------------------------------------------------------------------ upsert

    def upsert_file(self, jsonl_path: Path) -> bool:
        """Index (or re-index) one JSONL trace file. Returns True on success.

        Best-effort: any failure is logged and returns False. The caller
        MUST treat False as a soft fail — never propagate it as an error
        for the user.
        """
        if duckdb is None or not jsonl_path.exists():
            return False
        try:
            header, steps = _parse_jsonl(jsonl_path)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("index upsert_file: unreadable %s (%s)", jsonl_path, exc)
            return False
        if header is None:
            return False

        file_mtime = jsonl_path.stat().st_mtime
        import time as _time
        indexed_at = _time.time()
        trace_id = header["trace_id"]

        with _write_lock:
            try:
                conn = self._connect()
            except Exception as exc:  # noqa: BLE001 — index is best-effort
                logger.debug("index connect failed: %s", exc)
                return False
            try:
                self._ensure_schema(conn)
                conn.execute("BEGIN")
                conn.execute("DELETE FROM steps  WHERE trace_id = ?", [trace_id])
                conn.execute("DELETE FROM traces WHERE trace_id = ?", [trace_id])
                conn.execute(
                    """
                    INSERT INTO traces
                        (trace_id, name, framework, started_at, ended_at,
                         failed, error, step_count, file_mtime, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        trace_id,
                        header.get("name", "<unnamed>"),
                        header.get("framework"),
                        float(header.get("started_at", 0.0)),
                        _opt_float(header.get("ended_at")),
                        bool((header.get("metadata") or {}).get("failed", False)),
                        (header.get("metadata") or {}).get("error"),
                        len(steps),
                        file_mtime,
                        indexed_at,
                    ],
                )
                for step in steps:
                    conn.execute(
                        """
                        INSERT INTO steps
                            (trace_id, step_id, kind, name, started_at,
                             ended_at, error)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            trace_id,
                            str(step["step_id"]),
                            str(step["kind"]),
                            str(step["name"]),
                            float(step.get("started_at", 0.0)),
                            _opt_float(step.get("ended_at")),
                            step.get("error"),
                        ],
                    )
                conn.execute("COMMIT")
                return True
            except Exception as exc:  # noqa: BLE001 — index is best-effort
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                logger.debug("index upsert_file failed for %s: %s", trace_id, exc)
                return False
            finally:
                with contextlib.suppress(Exception):
                    conn.close()

    def remove_trace(self, trace_id: str) -> bool:
        """Drop one trace from the index. Used by `loupe purge`."""
        if duckdb is None:
            return False
        with _write_lock:
            try:
                conn = self._connect()
            except Exception:  # noqa: BLE001
                return False
            try:
                self._ensure_schema(conn)
                conn.execute("BEGIN")
                conn.execute("DELETE FROM steps  WHERE trace_id = ?", [trace_id])
                conn.execute("DELETE FROM traces WHERE trace_id = ?", [trace_id])
                conn.execute("COMMIT")
                return True
            except Exception as exc:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                logger.debug("index remove_trace(%s) failed: %s", trace_id, exc)
                return False
            finally:
                with contextlib.suppress(Exception):
                    conn.close()

    # ------------------------------------------------------------------ rebuild

    def rebuild(self) -> tuple[int, int]:
        """Drop the index and re-walk every JSONL file on disk.

        Returns (indexed, skipped). Skipped covers files that were
        unreadable or didn't contain a valid trace header.
        """
        if duckdb is None:
            return (0, 0)
        # Drop + recreate is simpler than DELETE — DuckDB doesn't reclaim
        # space on DELETE alone, and a rebuild is an explicit op.
        with _write_lock, contextlib.suppress(FileNotFoundError):
            self.db_path.unlink()

        indexed = 0
        skipped = 0
        if not self.traces_dir.exists():
            return (0, 0)
        for path in sorted(self.traces_dir.glob("*.jsonl")):
            if self.upsert_file(path):
                indexed += 1
            else:
                skipped += 1
        return (indexed, skipped)

    # ------------------------------------------------------------------ queries

    def list_traces(self, *, limit: int = 100) -> list[TraceRow]:
        """Return every indexed trace, newest first.

        On any error (missing index, locked, schema drift) we return ``[]``
        — the caller should detect that and fall back to a disk scan.

        Self-healing: if the index has rows for traces whose JSONL file no
        longer exists on disk (e.g. someone ran ``rm ~/.loupe/traces/*.jsonl``
        bypassing ``loupe purge``), we auto-rebuild the index and re-run
        the query. This stops the dashboard / CLI from showing phantom rows.
        """
        rows = self._list_raw(limit=limit)
        if rows and self._is_polluted(rows):
            self.rebuild()
            rows = self._list_raw(limit=limit)
        return rows

    def _list_raw(self, *, limit: int) -> list[TraceRow]:
        """One DuckDB query → list[TraceRow]. No self-healing. Used by
        the public :meth:`list_traces` and by health checks."""
        if duckdb is None or not self.db_path.exists():
            return []
        try:
            conn = self._connect(read_only=True)
        except Exception:  # noqa: BLE001
            return []
        try:
            raw = conn.execute(
                """
                SELECT trace_id, name, framework, started_at, ended_at,
                       failed, error, step_count
                FROM traces
                ORDER BY started_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        except Exception:  # noqa: BLE001
            return []
        finally:
            with contextlib.suppress(Exception):
                conn.close()
        return [
            TraceRow(
                trace_id=r[0],
                name=r[1],
                framework=r[2],
                started_at=r[3],
                ended_at=r[4],
                failed=bool(r[5]),
                error=r[6],
                step_count=r[7],
            )
            for r in raw
        ]

    def _is_polluted(self, rows: list[TraceRow]) -> bool:
        """Heuristic: more than 25 % of the indexed rows point at files
        that don't exist on disk → the index is stale (often because
        tests / a partial purge / a manual ``rm`` left it inconsistent).

        Sampling the first 20 rows keeps this O(1) regardless of total
        index size; if the head is rotten, the tail is too.
        """
        if not rows or not self.traces_dir.exists():
            return False
        sample = rows[: min(20, len(rows))]
        on_disk = {p.stem for p in self.traces_dir.glob("*.jsonl")}
        missing = sum(1 for r in sample if r.trace_id not in on_disk)
        return missing / max(1, len(sample)) > 0.25

    def stats(self) -> dict[str, Any] | None:
        """Aggregate counts, framework breakdown, median duration.

        Returns ``None`` if the index isn't usable (caller falls back).
        """
        if duckdb is None or not self.db_path.exists():
            return None
        try:
            conn = self._connect(read_only=True)
        except Exception:  # noqa: BLE001
            return None
        try:
            traces, failed, total_steps = conn.execute(
                """
                SELECT COUNT(*),
                       SUM(CASE WHEN failed THEN 1 ELSE 0 END),
                       COALESCE(SUM(step_count), 0)
                FROM traces
                """,
            ).fetchone() or (0, 0, 0)
            by_fw = conn.execute(
                """
                SELECT COALESCE(framework, '—') AS fw, COUNT(*) AS n
                FROM traces GROUP BY fw ORDER BY n DESC
                """,
            ).fetchall()
            durations = conn.execute(
                """
                SELECT (ended_at - started_at) * 1000 AS dur_ms
                FROM traces
                WHERE ended_at IS NOT NULL
                """
            ).fetchall()
        except Exception:  # noqa: BLE001
            return None
        finally:
            with contextlib.suppress(Exception):
                conn.close()
        median = _median([d[0] for d in durations]) if durations else None
        return {
            "trace_count": int(traces or 0),
            "failed_count": int(failed or 0),
            "step_count": int(total_steps or 0),
            "median_duration_ms": median,
            "by_framework": {row[0]: int(row[1]) for row in by_fw},
        }

    def info(self) -> dict[str, Any]:
        """Human-readable index health summary for `loupe index info`."""
        out: dict[str, Any] = {
            "db_path": str(self.db_path),
            "exists": self.db_path.exists(),
            "size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "schema_version": SCHEMA_VERSION,
        }
        if not self.db_path.exists():
            out["trace_count"] = 0
            out["step_count"] = 0
            return out
        try:
            conn = self._connect(read_only=True)
        except Exception as exc:  # noqa: BLE001
            out["error"] = repr(exc)
            return out
        try:
            tc = conn.execute("SELECT COUNT(*) FROM traces").fetchone()
            sc = conn.execute("SELECT COUNT(*) FROM steps").fetchone()
            out["trace_count"] = int(tc[0]) if tc else 0
            out["step_count"] = int(sc[0]) if sc else 0
        except Exception as exc:  # noqa: BLE001
            out["error"] = repr(exc)
        finally:
            with contextlib.suppress(Exception):
                conn.close()
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_jsonl(path: Path) -> tuple[dict | None, list[dict]]:
    """Read a JSONL trace file. Returns (header, steps)."""
    header: dict | None = None
    steps: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            kind = obj.pop("_type", None)
            if kind == "trace":
                header = obj
            elif kind == "step":
                steps.append(obj)
    return header, steps


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


# ---------------------------------------------------------------------------
# Default / module-level convenience
# ---------------------------------------------------------------------------


def default_index() -> JSONLIndex:
    """Open the index for the current LOUPE_HOME."""
    home = _default_dir()
    return JSONLIndex(
        db_path=home / "index.duckdb",
        traces_dir=home / "traces",
    )


def upsert_trace_file(path: Path) -> bool:
    """Module-level shortcut — used by JSONLStore after every save."""
    # Respect LOUPE_DISABLE_INDEX so users on weird filesystems (NFS without
    # locking, etc.) can opt out without code changes.
    if os.environ.get("LOUPE_DISABLE_INDEX"):
        return False
    try:
        idx = default_index()
    except Exception as exc:  # noqa: BLE001
        logger.debug("default_index() failed: %s", exc)
        return False
    return idx.upsert_file(path)
