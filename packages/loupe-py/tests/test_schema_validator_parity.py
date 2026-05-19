"""Parity test: the custom `loupe.ingest.ingest` validator MUST agree with
the canonical JSON Schema at docs/loupe-trace.schema.json on every payload.

If they ever disagree, the schema (which is part of the public spec) is
wrong, the validator (which is what actually runs in production) is wrong,
or someone added a field on one side without the other. Catch it here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

jsonschema = pytest.importorskip("jsonschema")

from loupe.ingest import IngestError, ingest  # noqa: E402
from loupe.store import JSONLStore  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "docs" / "loupe-trace.schema.json"


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    assert SCHEMA_PATH.exists(), f"schema file missing at {SCHEMA_PATH}"
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validates_against_schema(schema: dict, payload: dict) -> bool:
    try:
        jsonschema.validate(instance=payload, schema=schema)
        return True
    except jsonschema.ValidationError:
        return False


def _validates_against_ingest(payload: dict, tmp_path: Path) -> bool:
    store = JSONLStore(root=tmp_path)
    try:
        ingest(payload, store=store)
        return True
    except IngestError:
        return False


VALID = [
    # minimal
    {"name": "x", "steps": []},
    # full
    {
        "trace_id": "abc",
        "name": "full",
        "framework": "test",
        "started_at": 1.0,
        "ended_at": 2.0,
        "metadata": {"failed": False},
        "steps": [
            {
                "step_id": "s1",
                "kind": "llm-call",
                "name": "anthropic:claude",
                "started_at": 1.1,
                "ended_at": 1.9,
                "inputs": {"prompt": "hi"},
                "outputs": {"text": "hello"},
                "metadata": {},
                "error": None,
            }
        ],
    },
    # every recommended step kind
    *[
        {"name": "x", "steps": [{"kind": k, "name": "n"}]}
        for k in ["llm-call", "tool-call", "io", "thought", "error", "custom"]
    ],
    # user-defined kinds are allowed (free-form strings under 64 chars).
    # Production user code routinely records domain-specific kinds like
    # "plan", "retrieve", "final" — gating on a fixed enum was hostile.
    *[
        {"name": "x", "steps": [{"kind": k, "name": "n"}]}
        for k in ["plan", "retrieve", "final", "step.42", "user-defined"]
    ],
]

INVALID = [
    # missing required name
    {"steps": []},
    # name not a string
    {"name": 123, "steps": []},
    # name empty
    {"name": "", "steps": []},
    # steps not a list
    {"name": "x", "steps": "nope"},
    # step missing kind
    {"name": "x", "steps": [{"name": "n"}]},
    # step missing name
    {"name": "x", "steps": [{"kind": "thought"}]},
    # kind empty string
    {"name": "x", "steps": [{"kind": "", "name": "n"}]},
    # kind too long (>64 chars)
    {"name": "x", "steps": [{"kind": "k" * 65, "name": "n"}]},
    # step name empty
    {"name": "x", "steps": [{"kind": "thought", "name": ""}]},
]


@pytest.mark.parametrize("payload", VALID)
def test_valid_payloads_pass_both(
    payload: dict, schema: dict, tmp_path: Path
) -> None:
    assert _validates_against_schema(schema, payload), (
        f"Schema rejected what ingest would accept: {payload}"
    )
    assert _validates_against_ingest(payload, tmp_path), (
        f"Ingest rejected what schema would accept: {payload}"
    )


@pytest.mark.parametrize("payload", INVALID)
def test_invalid_payloads_fail_both(
    payload: dict, schema: dict, tmp_path: Path
) -> None:
    schema_ok = _validates_against_schema(schema, payload)
    ingest_ok = _validates_against_ingest(payload, tmp_path)
    assert not (schema_ok and ingest_ok), (
        f"Both validators accepted this clearly-invalid payload: {payload}"
    )
