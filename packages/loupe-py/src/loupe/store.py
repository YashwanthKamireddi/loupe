"""Persistent storage for Loupe traces.

Default: append-only JSONL files under ~/.loupe/traces/{trace_id}.jsonl.
A DuckDB-backed store will replace this once we add indexed search; the JSONL
format stays the canonical wire format forever (it's what we publish).
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from loupe.trace import Trace


def _default_dir() -> Path:
    override = os.environ.get("LOUPE_HOME")
    return Path(override) if override else Path.home() / ".loupe"


def safe_load_jsonl(
    path: Path,
    *,
    decrypt: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """Parse a trace JSONL, tolerating corrupt lines.

    A real-world trace can pick up garbage on any line if the writer
    SIGKILL'd mid-flush, the file landed on a flaky disk, or a user
    hand-edited it. The CLI used to call ``json.loads(line)`` directly
    everywhere; one bad byte and `loupe list` / `show` / `stats` /
    `replay` would crash. This helper is the single tolerant reader.

    Returns ``(records, skipped_line_count)``:
      - ``records`` — every successfully parsed object, in file order.
      - ``skipped_line_count`` — how many lines failed to parse
        (zero in the common case).

    Callers should surface a `⚠ skipped N corrupt line(s)` warning
    when ``skipped > 0`` so users notice degraded reads.

    If ``decrypt=True`` (default) and the file is encrypted with
    ``loupe._crypto.LOUPE-ENC-V1``, the cipher is unwrapped first so
    the rest of the codebase stays unaware of encryption.
    """
    text: str
    if decrypt:
        try:
            from loupe._crypto import read_trace_text
            text = read_trace_text(path)
        except Exception:
            text = path.read_text(encoding="utf-8", errors="replace")
    else:
        text = path.read_text(encoding="utf-8", errors="replace")

    records: list[dict[str, Any]] = []
    skipped = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(obj, dict):
            records.append(obj)
        else:
            skipped += 1
    return records, skipped


def iter_jsonl_records(path: Path) -> Iterator[dict[str, Any]]:
    """Stream parsed JSONL records, silently skipping unparseable lines.

    Thin wrapper over ``safe_load_jsonl`` for callers that don't need
    the skipped-line count (most loops). Same encryption + corruption
    tolerance; iteration order matches file order.
    """
    records, _ = safe_load_jsonl(path)
    yield from records


def read_trace_header(path: Path) -> dict[str, Any] | None:
    """Return the parsed ``_type=trace`` header line, or None.

    Canonical fast-path for "I just need the trace_id / framework /
    started_at / metadata". Replaces every ad-hoc ``_read_header``
    implementation across cli.py and ui/server.py.
    """
    for obj in iter_jsonl_records(path):
        if obj.get("_type") == "trace":
            # Return a copy with _type stripped so callers see only the
            # trace fields, matching the historical _read_header contract.
            out = dict(obj)
            out.pop("_type", None)
            return out
        # Header is line 0 by spec; if the first dict isn't a header,
        # the file is malformed for our purposes.
        return None
    return None


def load_trace_split(
    path: Path,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], int]:
    """Split a trace JSONL into (header, steps, skipped_line_count).

    The canonical reader for any code that wants both pieces. Every
    ``_load_trace`` / ``_load_trace_with_warning`` helper in cli.py is
    a thin shim over this.
    """
    records, skipped = safe_load_jsonl(path)
    header: dict[str, Any] | None = None
    steps: list[dict[str, Any]] = []
    for obj in records:
        kind = obj.pop("_type", None)
        if kind == "trace":
            header = obj
        elif kind == "step":
            steps.append(obj)
    return header, steps, skipped


class Store(Protocol):
    """Anything that can persist a Trace."""

    def save(self, trace: Trace) -> None: ...


class JSONLStore:
    """Append-only JSONL writer. One file per trace.

    Schema (one JSON object per line):
        - line 0: trace header (without `steps`)
        - lines 1..N: one step per line
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (_default_dir() / "traces")
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, trace: Trace) -> None:
        path = self.root / f"{trace.trace_id}.jsonl"
        # Compact separators (no spaces) so the wire format is bit-identical
        # to JSON.stringify(...) in the TypeScript SDK — see SPEC.md §6.
        header: dict[str, Any] = dataclasses.asdict(trace)
        steps = header.pop("steps", [])
        lines: list[str] = [
            json.dumps({"_type": "trace", **header}, separators=(",", ":")),
        ]
        for step in steps:
            lines.append(json.dumps({"_type": "step", **step}, separators=(",", ":")))
        plaintext = "\n".join(lines) + "\n"

        # J3 — opt-in encryption-at-rest. Wrap the whole JSONL doc in the
        # Loupe envelope when the user has [encryption] enabled = true.
        # ``encryption_enabled()`` short-circuits to False on any failure
        # so a broken config never silently drops traces.
        try:
            from loupe._crypto import encrypt_payload, encryption_enabled
            if encryption_enabled():
                # Encrypt the entire document into one envelope line. The
                # Fernet token is base64, ~135% the size of the input —
                # acceptable for trace files (KB range, not MB).
                envelope = encrypt_payload(plaintext, home=self.root.parent)
                path.write_text(envelope + "\n", encoding="utf-8")
            else:
                path.write_text(plaintext, encoding="utf-8")
        except Exception:  # noqa: BLE001 — never lose a trace on a crypto bug
            path.write_text(plaintext, encoding="utf-8")

        # Best-effort: schedule a DuckDB index upsert in a daemon background
        # thread so the hot path (trace.save) stays in microseconds. The JSONL
        # file on disk is the source of truth — if the index call fails, the
        # next `loupe index rebuild` catches up.
        #
        # CRITICAL: we pass the store's actual root explicitly. The background
        # thread can outlive a test fixture's `monkeypatch.setenv("LOUPE_HOME")`,
        # and if we relied on the live env var the thread could write to the
        # user's real ~/.loupe/index.duckdb. That bug shipped briefly and
        # corrupted real users' indexes — never again.
        #
        # Set LOUPE_DISABLE_INDEX=1 to opt out entirely (e.g., NFS mounts).
        if not os.environ.get("LOUPE_DISABLE_INDEX"):
            _schedule_index_upsert(path, traces_root=self.root)


def _schedule_index_upsert(path: Path, *, traces_root: Path) -> None:
    """Fire-and-forget background upsert. Never raises.

    `traces_root` is the directory whose sibling `index.duckdb` we will
    write to. Captured explicitly so an env-var change between scheduling
    and execution can't redirect the write to a different home.
    """
    import threading

    # Compute the target index path EAGERLY, before spawning the thread,
    # while the caller's environment is still in scope.
    index_path = traces_root.parent / "index.duckdb"

    def _run() -> None:
        try:
            from loupe.index import JSONLIndex
            JSONLIndex(db_path=index_path, traces_dir=traces_root).upsert_file(path)
        except Exception:  # noqa: BLE001 — best-effort, swallow silently
            pass

    # daemon=True so the upsert thread never blocks interpreter exit. If the
    # process dies mid-upsert, `loupe index rebuild` reconciles on next run.
    threading.Thread(target=_run, daemon=True).start()


_default: Store | None = None


def default_store() -> Store:
    global _default
    if _default is None:
        _default = JSONLStore()
    return _default
