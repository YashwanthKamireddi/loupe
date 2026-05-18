"""Property-based fuzz of the ingest validator.

Verifies the contract: `ingest()` either succeeds and returns a Trace, or
raises `IngestError` with an informative message — it must NEVER raise an
uncaught exception of a different type. That guarantee is what lets the
HTTP endpoint turn validation errors into clean 422 responses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from loupe.ingest import IngestError, ingest  # noqa: E402
from loupe.store import JSONLStore  # noqa: E402

# Sharing a single tmp_path across all generated inputs is fine here — we never
# read from it; we only verify ingest doesn't crash. Suppress the health check.
_SETTINGS = settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Strategy: generate a fuzz of plausible-looking-but-possibly-malformed payloads.
# Some fields are correct, some are not — that's the point.
ingest_payload = st.fixed_dictionaries(
    {},
    optional={
        "trace_id": st.text(max_size=40),
        "name": st.one_of(
            st.text(min_size=1, max_size=40),
            st.text(max_size=40),
            st.integers(),
            st.none(),
        ),
        "framework": st.one_of(st.none(), st.text(max_size=40)),
        "started_at": st.one_of(st.floats(allow_nan=False), st.integers()),
        "ended_at": st.one_of(st.none(), st.floats(allow_nan=False), st.integers()),
        "metadata": st.one_of(
            st.dictionaries(st.text(min_size=1), st.text(), max_size=4),
            st.none(),
            st.text(),
        ),
        "steps": st.one_of(
            st.lists(
                st.fixed_dictionaries(
                    {},
                    optional={
                        "kind": st.one_of(
                            st.sampled_from(
                                ["llm-call", "tool-call", "io",
                                 "thought", "error", "custom"]
                            ),
                            st.text(max_size=20),
                        ),
                        "name": st.one_of(st.text(min_size=1, max_size=40),
                                          st.text(max_size=40)),
                        "started_at": st.floats(allow_nan=False),
                        "ended_at": st.one_of(st.none(), st.floats(allow_nan=False)),
                        "inputs": st.dictionaries(st.text(min_size=1), st.text(), max_size=4),
                        "outputs": st.dictionaries(st.text(min_size=1), st.text(), max_size=4),
                    },
                ),
                max_size=6,
            ),
            st.text(),
            st.none(),
        ),
    },
)


@given(payload=ingest_payload)
@_SETTINGS
def test_ingest_either_succeeds_or_raises_ingest_error(
    payload: dict, tmp_path: Path
) -> None:
    store = JSONLStore(root=tmp_path)
    try:
        t = ingest(payload, store=store)
        # If it succeeded, the on-disk file should exist
        assert (tmp_path / f"{t.trace_id}.jsonl").exists()
    except IngestError as exc:
        # Validation errors are EXPECTED — they're the whole point of the contract
        # The message should not be empty.
        assert str(exc), "IngestError raised with empty message"
    # No other exception type may escape — that's the test.


@given(
    name=st.text(min_size=1, max_size=80),
    framework=st.one_of(st.none(), st.text(max_size=40)),
    n_steps=st.integers(min_value=0, max_value=10),
)
@settings(
    max_examples=80,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_ingest_round_trips_well_formed_payloads(
    name: str, framework: str | None, n_steps: int, tmp_path: Path
) -> None:
    """Well-formed payloads always round-trip cleanly."""
    payload: dict[str, Any] = {
        "name": name,
        "framework": framework,
        "steps": [
            {"kind": "thought", "name": f"s{i}"} for i in range(n_steps)
        ],
    }
    store = JSONLStore(root=tmp_path)
    t = ingest(payload, store=store)
    assert t.name == name
    assert len(t.steps) == n_steps
    assert (tmp_path / f"{t.trace_id}.jsonl").exists()
