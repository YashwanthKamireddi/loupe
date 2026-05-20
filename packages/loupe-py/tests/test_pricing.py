"""Tests for loupe.pricing — the LLM cost source of truth."""

from __future__ import annotations

from loupe.pricing import (
    estimate_cost_usd,
    format_usd,
    known_models,
    price_for,
)


def test_price_for_exact_match() -> None:
    p = price_for("gemini-2.5-flash")
    assert p is not None
    assert p.provider == "gemini"
    assert p.in_per_m == 0.30
    assert p.out_per_m == 2.50


def test_price_for_strips_provider_prefix() -> None:
    """Captured step names look like ``anthropic:claude-haiku-4-5`` —
    we must strip the prefix before lookup."""
    p = price_for("anthropic:claude-haiku-4-5")
    assert p is not None
    assert p.model == "claude-haiku-4-5"


def test_price_for_falls_back_to_provider_default() -> None:
    """Unknown model + known provider → cheap fallback."""
    p = price_for("brand-new-experimental-claude", provider="anthropic")
    assert p is not None
    assert p.provider == "anthropic"


def test_price_for_unknown_returns_none() -> None:
    """No model, no provider → we refuse to guess."""
    assert price_for(None, None) is None
    assert price_for("unknown-model", "unknown-provider") is None


def test_estimate_cost_usd_basic_math() -> None:
    """1M input + 1M output of gpt-4o-mini = $0.15 + $0.60 = $0.75."""
    cost = estimate_cost_usd(1_000_000, 1_000_000, model="gpt-4o-mini")
    assert cost == 0.75


def test_estimate_cost_usd_handles_missing_tokens() -> None:
    """If we don't know how many tokens, refuse to estimate."""
    assert estimate_cost_usd(None, 100, model="gpt-4o-mini") is None
    assert estimate_cost_usd(100, None, model="gpt-4o-mini") is None
    assert estimate_cost_usd(None, None, model="gpt-4o-mini") is None


def test_estimate_cost_usd_unknown_model_returns_none() -> None:
    """No pricing → no cost, never zero (so unpriced steps don't get
    silently swept into 'total: $0')."""
    assert estimate_cost_usd(1000, 500, model="unknown-fictional-model-99") is None


def test_estimate_cost_usd_negative_tokens_clamped_to_zero() -> None:
    """Defensive: a bogus negative token count should NOT make us bill
    a negative dollar amount."""
    assert estimate_cost_usd(-100, 500, model="gpt-4o-mini") == (
        (500 / 1_000_000) * 0.60
    )


def test_format_usd_precision_tiers() -> None:
    assert format_usd(None) == "—"
    assert format_usd(0) == "$0.00"
    # 0.00001 rounds to nothing → show the <$0.0001 sentinel
    assert format_usd(0.00001) == "<$0.0001"
    # Real-but-tiny cost: 4-decimal precision
    assert format_usd(0.0023) == "$0.0023"
    # Banker's rounding can land on either side of half — accept both.
    assert format_usd(12.345) in {"$12.35", "$12.34"}
    assert format_usd(1_234.5) == "$1,234.50"


def test_known_models_includes_all_three_providers() -> None:
    models = known_models()
    providers = {p.provider for p in models}
    assert "gemini" in providers
    assert "anthropic" in providers
    assert "openai" in providers
