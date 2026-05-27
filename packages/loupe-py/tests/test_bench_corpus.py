"""LoupeBench corpus loader + leaderboard aggregator tests.

The shape this module covers:

  1. ``load_corpus`` reads from a bundled name, local file, or HTTP URL.
  2. Malformed records raise ``CorpusError`` with a precise reason.
  3. ``corpus_to_leaderboard_entry`` aggregates per-record outcomes into
     the schema defined in ``bench/loupebench-leaderboard.schema.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loupe.bench import (
    CorpusError,
    corpus_to_leaderboard_entry,
    load_corpus,
)


def _write_corpus(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def _valid_record(rec_id: str = "lb-test-001") -> dict:
    return {
        "id": rec_id,
        "framework": "anthropic",
        "trace": {
            "trace_id": "t" + rec_id,
            "name": "fixture",
            "started_at": 1.0,
            "ended_at": 1.5,
        },
        "step": {
            "step_id": "stp1",
            "kind": "llm-call",
            "name": "anthropic:claude-haiku-4-5",
            "started_at": 1.0,
            "ended_at": 1.4,
            "inputs": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "hi"}],
            },
            "outputs": {"status": 200, "text": "bad output"},
            "metadata": {},
            "error": None,
        },
        "annotation": {
            "step_id": "stp1",
            "failure_category": "hallucination",
            "severity": "high",
            "notes": "fixture failure",
        },
        "license": "CC-BY-4.0",
    }


# ---------------------------------------------------------------------------
# load_corpus
# ---------------------------------------------------------------------------


def test_load_corpus_from_local_path(tmp_path: Path) -> None:
    corpus_path = tmp_path / "corpus.jsonl"
    _write_corpus(corpus_path, [
        _valid_record("lb-a"),
        _valid_record("lb-b"),
    ])
    records = load_corpus(str(corpus_path))
    assert [r["id"] for r in records] == ["lb-a", "lb-b"]


def test_load_corpus_raises_on_missing_file() -> None:
    with pytest.raises(CorpusError, match="no such corpus file"):
        load_corpus("/no/such/path.jsonl")


def test_load_corpus_raises_on_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    with pytest.raises(CorpusError, match="corpus is empty"):
        load_corpus(str(p))


def test_load_corpus_raises_on_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.jsonl"
    p.write_text("this is not json\n", encoding="utf-8")
    with pytest.raises(CorpusError, match="not valid JSON"):
        load_corpus(str(p))


def test_load_corpus_raises_on_missing_fields(tmp_path: Path) -> None:
    p = tmp_path / "incomplete.jsonl"
    # Missing required `step`
    p.write_text('{"id":"lb-x"}\n', encoding="utf-8")
    with pytest.raises(CorpusError, match="missing required fields"):
        load_corpus(str(p))


def test_load_corpus_skips_blank_lines(tmp_path: Path) -> None:
    """A trailing newline / blank line in the middle of the file must
    not produce an empty record."""
    p = tmp_path / "with-blanks.jsonl"
    p.write_text(
        json.dumps(_valid_record("lb-1")) + "\n\n"
        + json.dumps(_valid_record("lb-2")) + "\n",
        encoding="utf-8",
    )
    records = load_corpus(str(p))
    assert len(records) == 2


def test_load_corpus_bundled_loupebench_v0_1_resolves() -> None:
    """The bundled corpus must be discoverable from any working directory
    during dev. Skipped automatically if the file isn't there (e.g. in
    a pure-wheel install)."""
    try:
        records = load_corpus("loupebench-v0.1")
    except CorpusError as exc:
        if "not found" in str(exc):
            pytest.skip("bundled corpus not in this build")
        raise
    # The shipped corpus has 5 records (1 per category).
    assert len(records) >= 5
    categories = {
        (r["annotation"]["failure_category"]) for r in records
    }
    assert {"hallucination", "loop", "tool-misuse", "off-task",
            "context-drop"}.issubset(categories)


# ---------------------------------------------------------------------------
# corpus_to_leaderboard_entry
# ---------------------------------------------------------------------------


def test_leaderboard_entry_aggregates_pass_fail() -> None:
    results = [
        {"id": "lb-a", "ok": True, "trace_id": "newtraceA"},
        {"id": "lb-b", "ok": False, "error": "API rate-limited"},
        {"id": "lb-c", "ok": True, "trace_id": "newtraceC"},
    ]
    cats = {"lb-a": "hallucination", "lb-b": "loop", "lb-c": "hallucination"}
    out = corpus_to_leaderboard_entry(
        corpus_source="test-corpus",
        corpus_size=3,
        provider="anthropic",
        model="claude-haiku-4-5",
        results=results,
        record_categories=cats,
    )
    assert out["total"] == 3
    assert out["replayed"] == 2
    assert out["errors"] == 1
    assert abs(out["fail_rate"] - (1 / 3)) < 1e-9
    # Per-category breakdown
    assert out["categories"]["hallucination"] == {"replayed": 2, "errors": 0}
    assert out["categories"]["loop"] == {"replayed": 0, "errors": 1}


def test_leaderboard_entry_handles_empty_results() -> None:
    """An empty result list (nothing replayed) yields fail_rate 0, not NaN."""
    out = corpus_to_leaderboard_entry(
        corpus_source="empty-test",
        corpus_size=0,
        provider="x", model="y",
        results=[],
    )
    assert out["total"] == 0
    assert out["replayed"] == 0
    assert out["errors"] == 0
    assert out["fail_rate"] == 0.0


def test_leaderboard_entry_carries_metadata() -> None:
    out = corpus_to_leaderboard_entry(
        corpus_source="loupebench-v0.1",
        corpus_size=5,
        provider="anthropic",
        model="claude-haiku-4-5",
        results=[{"id": "lb-x", "ok": True}],
    )
    assert out["corpus"] == "loupebench-v0.1"
    assert out["corpus_size"] == 5
    assert out["provider"] == "anthropic"
    assert out["model"] == "claude-haiku-4-5"
    assert "timestamp" in out
    assert "loupe_version" in out
    # records carried verbatim
    assert out["records"] == [{"id": "lb-x", "ok": True}]
