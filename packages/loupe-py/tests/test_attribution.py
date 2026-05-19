"""Tests for the circuit-attribution pipeline.

Coverage:
- :class:`MockAttributor` is deterministic, sorted by activation
- :func:`make_attributor` factory: known + unknown backends, ImportError path
- :func:`attribute_trace` walks llm-call steps, skips others, honors
  ``only_failing``
- ``loupe attribute`` CLI persists results into the annotation store,
  preserves existing tags, and prints a useful preview
- The persisted JSON shape is stable and round-trips
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from loupe import record_step, trace
from loupe.annotation import AnnotationStore
from loupe.attribution import (
    AttributionResult,
    FeatureActivation,
    MockAttributor,
    attribute_trace,
    make_attributor,
)
from loupe.cli import app
from loupe.store import JSONLStore


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def loupe_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "loupe-home"
    home.mkdir()
    monkeypatch.setenv("LOUPE_HOME", str(home))
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("FORCE_COLOR", "0")
    from loupe import store as store_mod
    store_mod._default = None
    return home


def _seed_trace_with_llm_call(home: Path) -> str:
    """Capture one trace that contains a real-looking llm-call step."""
    traces_dir = home / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    before = {p.stem for p in traces_dir.glob("*.jsonl")}
    store = JSONLStore(root=traces_dir)

    @trace(name="attr-test", framework="test", store=store)
    def agent() -> None:
        record_step("plan", "plan-step")
        record_step(
            "llm-call",
            "fake:claude",
            inputs={"messages": [{"role": "user", "content": "what is 2+2?"}]},
            outputs={"text": "2+2 is 4."},
        )

    agent()
    after = {p.stem for p in traces_dir.glob("*.jsonl")}
    return next(iter(after - before))


# ---------------------------------------------------------------------------
# MockAttributor
# ---------------------------------------------------------------------------


def test_mock_attributor_is_deterministic() -> None:
    """Same inputs → same features. Critical for reproducibility."""
    a = MockAttributor(top_k=6)
    r1 = a.attribute(prompt="p", response="r", step_id="s", trace_id="t")
    r2 = a.attribute(prompt="p", response="r", step_id="s", trace_id="t")
    assert [f.feature_id for f in r1.top_features] == [f.feature_id for f in r2.top_features]
    assert [f.activation for f in r1.top_features] == [f.activation for f in r2.top_features]


def test_mock_attributor_sorted_by_activation() -> None:
    a = MockAttributor(top_k=10)
    r = a.attribute(prompt="hello", response="world", step_id="s1", trace_id="t1")
    acts = [f.activation for f in r.top_features]
    assert acts == sorted(acts, reverse=True), "top features must be activation-sorted"


def test_mock_attributor_different_inputs_diverge() -> None:
    a = MockAttributor(top_k=5)
    r1 = a.attribute(prompt="A", response="x", step_id="s", trace_id="t")
    r2 = a.attribute(prompt="B", response="x", step_id="s", trace_id="t")
    assert [f.feature_id for f in r1.top_features] != [f.feature_id for f in r2.top_features]


def test_attribution_result_to_json_round_trips() -> None:
    a = MockAttributor(top_k=3)
    r = a.attribute(prompt="p", response="r", step_id="s", trace_id="t")
    d = r.to_json_dict()
    # JSON serialization must work — this is what gets stored in annotations.
    serialized = json.dumps(d)
    restored = json.loads(serialized)
    assert restored["model"] == "mock-model"
    assert len(restored["top_features"]) == 3


# ---------------------------------------------------------------------------
# make_attributor factory
# ---------------------------------------------------------------------------


def test_make_attributor_defaults_to_mock() -> None:
    a = make_attributor()
    assert isinstance(a, MockAttributor)


def test_make_attributor_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="Unknown attribution backend"):
        make_attributor("nonexistent")


def test_make_attributor_sae_requires_model_and_sae() -> None:
    """The real SAE backend needs explicit model + sae names. The check
    runs before any heavy import."""
    pytest.importorskip("sae_lens", reason="real SAE backend opt-in")
    with pytest.raises(ValueError, match="requires explicit"):
        make_attributor("sae")


# ---------------------------------------------------------------------------
# attribute_trace
# ---------------------------------------------------------------------------


def test_attribute_trace_processes_only_llm_calls(loupe_home: Path) -> None:
    """plan/thought/io steps are skipped — only llm-call steps get attributed."""
    trace_id = _seed_trace_with_llm_call(loupe_home)
    path = loupe_home / "traces" / f"{trace_id}.jsonl"
    results = attribute_trace(path, MockAttributor(top_k=4))
    assert len(results) == 1
    step_id, result = results[0]
    assert result.top_features
    assert len(result.top_features) == 4


def test_attribute_trace_only_failing_skips_success(loupe_home: Path) -> None:
    """--only-failing must skip llm-call steps without an error field."""
    trace_id = _seed_trace_with_llm_call(loupe_home)
    path = loupe_home / "traces" / f"{trace_id}.jsonl"
    results = attribute_trace(path, MockAttributor(), only_failing=True)
    assert results == []   # the seeded llm-call succeeded → skipped


# ---------------------------------------------------------------------------
# CLI: loupe attribute <trace-id>
# ---------------------------------------------------------------------------


def test_attribute_cli_persists_into_annotation_store(
    runner: CliRunner, loupe_home: Path
) -> None:
    """End-to-end: run loupe attribute → annotation row appears."""
    trace_id = _seed_trace_with_llm_call(loupe_home)
    result = runner.invoke(
        app, ["attribute", trace_id[:12], "--top-k", "4"]
    )
    assert result.exit_code == 0, result.output
    assert "attributed 1 step(s)" in result.output

    # Annotation was created with the mock attribution stored under
    # circuit_attribution. Existing annotators must be honored.
    items = AnnotationStore().load(trace_id)
    assert len(items) == 1
    attr = items[0].circuit_attribution
    assert attr["model"] == "mock-model"
    assert attr["method"] == "mock-hash-topk"
    assert len(attr["top_features"]) == 4
    assert items[0].annotator == "loupe-attribute"


def test_attribute_cli_preserves_existing_tag(
    runner: CliRunner, loupe_home: Path
) -> None:
    """If a step already has a human tag, attribute updates the
    circuit_attribution field but leaves category/notes/severity alone."""
    trace_id = _seed_trace_with_llm_call(loupe_home)

    # First: tag the llm-call step manually.
    # Need its step id from the trace file.
    path = loupe_home / "traces" / f"{trace_id}.jsonl"
    for line in path.read_text().splitlines():
        obj = json.loads(line)
        if obj.get("kind") == "llm-call":
            step_id = obj["step_id"]
            break

    runner.invoke(
        app,
        ["tag", trace_id[:12], step_id[:8], "hallucination",
         "--notes", "model invented fake fact", "--severity", "high"],
    )

    # Now run attribute. Existing tag must survive; circuit_attribution must be added.
    runner.invoke(app, ["attribute", trace_id[:12]])
    items = AnnotationStore().load(trace_id)
    assert len(items) == 1
    a = items[0]
    assert a.failure_category == "hallucination"
    assert a.severity == "high"
    assert a.notes == "model invented fake fact"
    assert a.circuit_attribution["model"] == "mock-model"


def test_attribute_cli_unknown_trace_exits_one(
    runner: CliRunner, loupe_home: Path
) -> None:
    res = runner.invoke(app, ["attribute", "deadbeefdead"])
    assert res.exit_code == 1


def test_attribute_cli_unknown_backend_exits_clean(
    runner: CliRunner, loupe_home: Path
) -> None:
    """A bad --backend value should error cleanly, no Python traceback."""
    trace_id = _seed_trace_with_llm_call(loupe_home)
    res = runner.invoke(
        app, ["attribute", trace_id[:12], "--backend", "bogus"]
    )
    assert res.exit_code == 1
    assert "Unknown attribution backend" in res.output
    assert "Traceback" not in res.output


def test_attribute_cli_idempotent(
    runner: CliRunner, loupe_home: Path
) -> None:
    """Re-running attribute on the same trace doesn't duplicate annotations."""
    trace_id = _seed_trace_with_llm_call(loupe_home)
    runner.invoke(app, ["attribute", trace_id[:12]])
    runner.invoke(app, ["attribute", trace_id[:12]])
    items = AnnotationStore().load(trace_id)
    assert len(items) == 1, "re-running should update in place, not duplicate"


# ---------------------------------------------------------------------------
# Public data model
# ---------------------------------------------------------------------------


def test_feature_activation_is_immutable() -> None:
    """FeatureActivation is frozen — accidental mutation should fail."""
    f = FeatureActivation(feature_id=1, activation=0.5, layer="x")
    with pytest.raises((AttributeError, TypeError)):
        f.activation = 1.0  # type: ignore[misc]


def test_attribution_result_default_method_is_set() -> None:
    """method is required to identify the attributor that produced this row."""
    r = AttributionResult(model="m", sae="s", method="mock-hash-topk")
    assert r.method
    assert r.top_features == []
