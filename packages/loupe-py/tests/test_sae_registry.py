"""SAE registry tests — picker logic for ``loupe attribute``.

These tests verify the *routing* of a captured-model string to an SAE
entry. They do NOT exercise the actual sae-lens forward pass — that
lives in ``test_attribution.py`` (which is gated by the [interp] extra).
"""

from __future__ import annotations

from loupe._sae_registry import (
    SAE_ENTRIES,
    SAEEntry,
    default_entry,
    get,
    labels,
    recommended_sae_for,
)


def test_registry_has_at_least_three_entries() -> None:
    """One open-default, one Gemma surrogate, one tiny — covers the
    three slots `loupe attribute` advertises."""
    assert len(SAE_ENTRIES) >= 3


def test_each_entry_has_required_fields() -> None:
    for e in SAE_ENTRIES:
        assert isinstance(e, SAEEntry)
        assert e.label and e.display and e.model
        assert e.release and e.sae_id and e.tagline


def test_labels_unique_and_picker_ordered() -> None:
    labs = labels()
    assert len(labs) == len(set(labs))
    # `gpt2-small` is the recommended default; must be first.
    assert labs[0] == "gpt2-small"


def test_default_entry_is_first() -> None:
    assert default_entry() is SAE_ENTRIES[0]


def test_get_is_case_insensitive() -> None:
    assert get("GPT2-SMALL") is get("gpt2-small")
    assert get("Gemma-2-2B") is get("gemma-2-2b")


def test_get_returns_none_for_unknown_label() -> None:
    assert get("nonsense-llm") is None


def test_recommended_for_claude_picks_gpt2_small() -> None:
    """Closed Anthropic models route through gpt2-small as the surrogate."""
    assert recommended_sae_for("claude-haiku-4-5").label == "gpt2-small"
    assert recommended_sae_for("claude-3-5-sonnet").label == "gpt2-small"
    assert recommended_sae_for("claude-opus-4").label == "gpt2-small"


def test_recommended_for_openai_picks_gpt2_small() -> None:
    """OpenAI's GPT lineage suggests gpt2-small as the surrogate."""
    assert recommended_sae_for("gpt-4o-mini").label == "gpt2-small"
    assert recommended_sae_for("gpt-4-turbo").label == "gpt2-small"
    assert recommended_sae_for("o3").label == "gpt2-small"


def test_recommended_for_gemini_picks_gemma() -> None:
    """Gemini's open cousin is Gemma, so route to the Gemma SAE."""
    assert recommended_sae_for("gemini-2.5-pro").label == "gemma-2-2b"
    assert recommended_sae_for("gemini-flash").label == "gemma-2-2b"


def test_recommended_for_open_weight_models_uses_them_directly() -> None:
    """If the captured model is itself an entry in the registry, use it."""
    assert recommended_sae_for("gemma-2-2b").label == "gemma-2-2b"


def test_recommended_for_none_falls_back_to_default() -> None:
    assert recommended_sae_for(None) is default_entry()
    assert recommended_sae_for("") is default_entry()


def test_recommended_for_unknown_falls_back_to_default() -> None:
    """An exotic local model should fall through to the default rather
    than raising — better to do *some* attribution than nothing."""
    result = recommended_sae_for("some-random-model-name-7b")
    assert result is default_entry()
