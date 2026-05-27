"""LoupeBench export + corpus import — public-dataset mechanics.

Three flows live here:

  1. ``export_jsonl(out)`` — turn your locally tagged failures into a
     publishable JSONL corpus. One record per annotation. Output matches
     ``bench/loupebench-v0.1.schema.json``.
  2. ``load_corpus(source)`` — read a corpus file (local path or HTTP URL),
     return the list of records for replay or inspection.
  3. ``corpus_to_leaderboard_entry(...)`` — given a list of (record, ok,
     trace_id_or_error) tuples, produce a Leaderboard JSON entry that
     matches ``bench/loupebench-leaderboard.schema.json``.

The CLI surface is in ``loupe.cli``:

  - ``loupe export --format loupebench``  → uses ``export_jsonl``
  - ``loupe bench --corpus <src>``        → uses ``load_corpus`` +
                                            ``corpus_to_leaderboard_entry``
"""

from __future__ import annotations

import datetime as _dt
import json
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from loupe._version import __version__
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
    from loupe.store import load_trace_split
    header, steps, _ = load_trace_split(path)
    return header, steps


# ---------------------------------------------------------------------------
# Corpus loading — local file, HTTP(S) URL, or one of the bundled corpora.
# ---------------------------------------------------------------------------


# Resolves `loupebench-v0.1` (no scheme, no .jsonl) to the bundled corpus
# that ships in the repo root's `bench/` directory.
_BUNDLED_CORPORA = {
    "loupebench-v0.1": "bench/loupebench-v0.1.jsonl",
}


class CorpusError(ValueError):
    """Raised when a corpus can't be loaded or parsed."""


def load_corpus(source: str) -> list[dict[str, Any]]:
    """Load a LoupeBench corpus from a local file, a URL, or a bundled name.

    ``source`` is one of:
      - ``loupebench-v0.1`` (bundled name → resolves to ``bench/...``)
      - ``/abs/or/rel/path.jsonl``
      - ``https://github.com/.../loupebench-v0.1.jsonl``

    Returns a list of records (each a dict matching the v0.1 schema).
    Raises ``CorpusError`` on any IO / parse failure with a one-line reason.
    """
    text = _fetch_corpus_text(source)
    records: list[dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusError(
                f"corpus line {i} is not valid JSON: {exc.msg}"
            ) from exc
        # Minimal sanity check — full schema validation lives in the
        # validator tests. Reject obviously malformed records up front
        # so a typo doesn't waste a billable replay.
        if not isinstance(obj, dict) or "id" not in obj or "step" not in obj:
            raise CorpusError(
                f"corpus line {i} missing required fields: {sorted(obj or {})}"
            )
        records.append(obj)
    if not records:
        raise CorpusError("corpus is empty")
    return records


def _fetch_corpus_text(source: str) -> str:
    """Get the corpus text from disk, HTTP, or the bundled corpora dir.

    HTTP downloads are capped at 10 MB so a malicious URL can't bust
    memory. We trust local files (the user supplied the path).
    """
    src = source.strip()
    # Bundled name?
    if src in _BUNDLED_CORPORA:
        bundled = _find_bundled_corpus(_BUNDLED_CORPORA[src])
        if bundled is None:
            raise CorpusError(
                f"bundled corpus {src!r} not found — checked the repo "
                "bench/ directory and the installed package data"
            )
        return bundled.read_text(encoding="utf-8")
    # HTTP(S) URL?
    if src.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(src, timeout=30) as resp:  # noqa: S310 — user-supplied URL
                data = resp.read(10 * 1024 * 1024 + 1)
            if len(data) > 10 * 1024 * 1024:
                raise CorpusError(
                    f"corpus at {src} exceeds 10 MB — refusing to download"
                )
            return data.decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise CorpusError(f"could not fetch {src}: {exc}") from exc
    # Local file?
    path = Path(src).expanduser()
    if not path.exists():
        raise CorpusError(f"no such corpus file: {src}")
    return path.read_text(encoding="utf-8")


def _find_bundled_corpus(rel: str) -> Path | None:
    """Walk up from this module looking for a ``bench/...`` directory.

    Works in three contexts:
      - dev source tree: ``packages/loupe-py/src/loupe/bench.py`` →
        ``../../../bench/...``
      - installed wheel: the corpus file isn't shipped (intentional —
        keeps wheels small). Returns None; user must pass a path/URL.
      - editable install / monorepo: same walk as dev.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / rel
        if candidate.exists():
            return candidate
    return None


def corpus_to_leaderboard_entry(
    *,
    corpus_source: str,
    corpus_size: int,
    provider: str,
    model: str,
    results: list[dict[str, Any]],
    record_categories: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Aggregate per-record replay results into one Leaderboard entry.

    ``results`` is a list of ``{id, ok, trace_id?, error?}`` dicts —
    typically produced by the CLI as it iterates the corpus.

    The output matches ``bench/loupebench-leaderboard.schema.json``.
    """
    total = len(results)
    errors_n = sum(1 for r in results if not r.get("ok"))
    replayed_n = total - errors_n

    by_cat: dict[str, dict[str, int]] = defaultdict(lambda: {"replayed": 0, "errors": 0})
    for r in results:
        cat = (record_categories or {}).get(r.get("id", ""), "unknown")
        bucket = by_cat[cat]
        if r.get("ok"):
            bucket["replayed"] += 1
        else:
            bucket["errors"] += 1

    return {
        "corpus":       corpus_source,
        "corpus_size":  corpus_size,
        "provider":     provider,
        "model":        model,
        "total":        total,
        "replayed":     replayed_n,
        "errors":       errors_n,
        "fail_rate":    (errors_n / total) if total else 0.0,
        "categories":   dict(by_cat),
        "records":      results,
        "timestamp":    _dt.datetime.now(_dt.UTC).isoformat(),
        "loupe_version": __version__,
    }


__all__ = [
    "CorpusError",
    "corpus_to_leaderboard_entry",
    "export_jsonl",
    "load_corpus",
]
