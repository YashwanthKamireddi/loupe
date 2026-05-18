"""Golden-snapshot test for the canonical JSONL wire format.

If anyone changes how a Trace or Step serializes — field order, type
coercion, key naming — this test breaks immediately. The fixture is the
contract. Update it deliberately, never accidentally.

The same fixture is what a future TypeScript snapshot test will assert
against — keeping the two languages bit-identical is the headline promise
of `docs/SPEC.md`.
"""

from __future__ import annotations

from pathlib import Path

from loupe.store import JSONLStore
from loupe.trace import Step, Trace

FIXTURE = Path(__file__).parent / "fixtures" / "canonical_trace.jsonl"


def _build_fixture_trace() -> Trace:
    """Build the exact Trace the fixture file represents.

    Numbers are fractional on purpose: JSON.stringify(1.0) → "1" in JS
    but json.dumps(1.0) → "1.0" in Python. Using non-whole-number values
    sidesteps the disagreement and keeps the wire format bit-identical.
    """
    return Trace(
        trace_id="abc123def456abc123def456abc12345",
        name="snapshot-fixture",
        framework="test",
        started_at=1.001,
        ended_at=2.001,
        steps=[
            Step(
                step_id="s00000000001",
                parent_step_id=None,
                kind="thought",
                name="plan",
                started_at=1.101,
                ended_at=1.201,
                inputs={},
                outputs={"plan": "do thing"},
                metadata={},
                error=None,
            ),
            Step(
                step_id="s00000000002",
                parent_step_id=None,
                kind="llm-call",
                name="anthropic:claude-haiku-4-5",
                started_at=1.301,
                ended_at=1.901,
                inputs={"prompt": "hi", "model": "claude-haiku-4-5"},
                outputs={"text": "hello", "input_tokens": 5, "output_tokens": 2},
                metadata={},
                error=None,
            ),
        ],
        metadata={},
    )


def test_canonical_serialization_is_byte_stable(tmp_path: Path) -> None:
    """Python serialization must match the canonical fixture exactly."""
    store = JSONLStore(root=tmp_path)
    store.save(_build_fixture_trace())

    written = (tmp_path / "abc123def456abc123def456abc12345.jsonl").read_text(encoding="utf-8")
    expected = FIXTURE.read_text(encoding="utf-8")
    assert written == expected, (
        "Wire format drifted!\n\nExpected:\n"
        + expected
        + "\n\nGot:\n"
        + written
        + "\n\nIf this change was intentional, update tests/fixtures/canonical_trace.jsonl "
        "AND mirror it in the TypeScript snapshot fixture."
    )


def test_fixture_is_parseable_by_ingest() -> None:
    """The fixture file is a stable, ingest-shaped payload that must validate."""
    import json

    from loupe.ingest import ingest as ingest_fn
    from loupe.store import JSONLStore

    # Convert the JSONL fixture into the single-object ingest payload shape
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    objs = [json.loads(line) for line in lines]
    header = next(o for o in objs if o["_type"] == "trace")
    steps = [o for o in objs if o["_type"] == "step"]
    payload = {
        **{k: v for k, v in header.items() if k != "_type"},
        "steps": [{k: v for k, v in s.items() if k != "_type"} for s in steps],
    }

    # ingest should round-trip without error, into a tmp store
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = JSONLStore(root=Path(tmp))
        t = ingest_fn(payload, store=store)
        assert t.name == "snapshot-fixture"
        assert len(t.steps) == 2
