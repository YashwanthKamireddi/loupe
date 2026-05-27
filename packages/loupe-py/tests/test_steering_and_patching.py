"""Data-model + constructor tests for ``loupe.steering`` and
``loupe.attribution_patching``.

These verify the import-cheap surface (dataclasses, constructor
validation, registry lookup). The actual GPU forward-pass tests live
in ``test_attribution.py`` behind the ``[interp]`` extra guard.
"""

from __future__ import annotations

import pytest

from loupe._sae_registry import default_entry
from loupe.attribution_patching import (
    AttributionPatcher,
    CausalFeature,
    PatchPair,
    PatchResult,
)
from loupe.steering import Steerer, SteerResult, SteerSpec

# ---------------------------------------------------------------------------
# Steering
# ---------------------------------------------------------------------------


def test_steer_spec_is_frozen() -> None:
    s = SteerSpec(feature_id=42, multiplier=0.5)
    with pytest.raises((AttributeError, TypeError)):  # frozen dataclass
        s.feature_id = 99  # type: ignore[misc]


def test_steerer_default_uses_default_entry() -> None:
    s = Steerer()
    assert s.entry is default_entry()
    assert s.device == "cpu"
    # Not loaded until .run() is called.
    assert s._loaded is False


def test_steerer_with_specific_sae_label() -> None:
    s = Steerer(sae_label="gpt2-small")
    assert s.entry.label == "gpt2-small"


def test_steerer_rejects_unknown_sae_label() -> None:
    with pytest.raises(ValueError, match="unknown SAE label"):
        Steerer(sae_label="not-a-real-sae")


def test_steer_result_carries_metadata() -> None:
    spec = SteerSpec(feature_id=12345, multiplier=0.0)
    res = SteerResult(
        trace_id="abc123",
        original_trace_id="orig456",
        prompt="What's 2 + 2?",
        steered_text="4",
        spec=spec,
        model="gpt2-small",
        sae_release="gpt2-small-res-jb",
        sae_id="blocks.6.hook_resid_pre",
    )
    assert res.trace_id == "abc123"
    assert res.spec.feature_id == 12345
    assert res.spec.multiplier == 0.0


# ---------------------------------------------------------------------------
# Attribution patching
# ---------------------------------------------------------------------------


def test_patch_pair_is_frozen() -> None:
    p = PatchPair(
        clean_prompt="x", clean_answer_token="a",
        corrupted_prompt="y", corrupted_answer_token="a",
    )
    with pytest.raises((AttributeError, TypeError)):  # frozen dataclass
        p.clean_prompt = "z"  # type: ignore[misc]


def test_attribution_patcher_default() -> None:
    p = AttributionPatcher()
    assert p.entry is default_entry()
    assert p.top_k == 16
    assert p.device == "cpu"
    assert p._loaded is False


def test_attribution_patcher_from_registry_routes_correctly() -> None:
    """Constructing from a closed-model label picks the right surrogate."""
    p_claude = AttributionPatcher.from_registry("claude-haiku-4-5")
    p_gemini = AttributionPatcher.from_registry("gemini-2.5-pro")
    p_unknown = AttributionPatcher.from_registry("totally-fake-model")
    assert p_claude.entry.label == "gpt2-small"
    assert p_gemini.entry.label == "gemma-2-2b"
    assert p_unknown.entry is default_entry()


def test_attribution_patcher_rejects_unknown_sae() -> None:
    with pytest.raises(ValueError, match="unknown SAE label"):
        AttributionPatcher(sae_label="nope-not-real")


def test_patch_result_default_shape() -> None:
    pair = PatchPair(
        clean_prompt="A", clean_answer_token="x",
        corrupted_prompt="B", corrupted_answer_token="x",
    )
    res = PatchResult(model="gpt2-small", sae="layer-6", pair=pair)
    assert res.top_features == []
    assert res.baseline_delta == 0.0
    assert res.attributed_at == 0.0


def test_causal_feature_is_frozen_and_has_signed_score() -> None:
    f = CausalFeature(feature_id=7, score=-0.42, layer="blocks.6.hook_resid_pre")
    assert f.feature_id == 7
    assert f.score == -0.42   # negative scores are valid
    with pytest.raises((AttributeError, TypeError)):  # frozen dataclass
        f.score = 0.0  # type: ignore[misc]
