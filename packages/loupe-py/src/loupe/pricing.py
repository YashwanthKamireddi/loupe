"""LLM provider pricing — USD per million tokens, frozen by model.

This module is the single source of truth for cost estimation. Prices are
expressed per **million input** and **million output** tokens so the
numbers stay readable (most rows are small floats — $0.15, $2.50, $15).
A trace's cost is::

    cost_usd = (in_tokens / 1_000_000) * in_price + (out_tokens / 1_000_000) * out_price

Pricing reflects published list prices as of **2026-05** and is updated
alongside model releases. The table is intentionally a flat ``dict`` so
the file is greppable and easy to PR.

Design notes
------------
- We **never** call out to a network pricing API. That would introduce
  flakiness on a hot path. Hand-maintained table; CI catches drift.
- Unknown models (custom fine-tunes, brand-new releases) fall back to
  the provider-default tier; users can override via
  ``[pricing.<model_id>]`` in ``~/.loupe/config.toml`` (future hook).
- All prices in USD. Currency conversion is out of scope — show the
  raw USD figure and let downstream tools localize.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    """Per-million-token pricing for one model."""

    model: str
    in_per_m: float          # USD per 1M input tokens
    out_per_m: float         # USD per 1M output tokens
    provider: str            # "gemini" / "anthropic" / "openai"


# ----------------------------------------------------------------------------
# Pricing table — keep alphabetical by provider, then by model
# ----------------------------------------------------------------------------

def _p(model: str, in_per_m: float, out_per_m: float, provider: str) -> ModelPrice:
    """Short alias just for the table below."""
    return ModelPrice(model, in_per_m, out_per_m, provider)


_PRICES: dict[str, ModelPrice] = {
    # ---- Anthropic ----
    "claude-haiku-4-5-20251001":  _p("claude-haiku-4-5-20251001",  0.80,  4.00, "anthropic"),
    "claude-haiku-4-5":           _p("claude-haiku-4-5",           0.80,  4.00, "anthropic"),
    "claude-sonnet-4-5":          _p("claude-sonnet-4-5",          3.00, 15.00, "anthropic"),
    "claude-sonnet-4-6":          _p("claude-sonnet-4-6",          3.00, 15.00, "anthropic"),
    "claude-opus-4-1":            _p("claude-opus-4-1",           15.00, 75.00, "anthropic"),
    "claude-opus-4-7":            _p("claude-opus-4-7",           15.00, 75.00, "anthropic"),

    # ---- Gemini ----
    "gemini-2.0-flash":           _p("gemini-2.0-flash",           0.10,  0.40, "gemini"),
    "gemini-2.5-flash":           _p("gemini-2.5-flash",           0.30,  2.50, "gemini"),
    "gemini-2.5-pro":             _p("gemini-2.5-pro",             1.25, 10.00, "gemini"),

    # ---- OpenAI ----
    "gpt-4o-mini":                _p("gpt-4o-mini",                0.15,  0.60, "openai"),
    "gpt-4o":                     _p("gpt-4o",                     2.50, 10.00, "openai"),
    "o1-mini":                    _p("o1-mini",                    1.10,  4.40, "openai"),
    "o1":                         _p("o1",                        15.00, 60.00, "openai"),
}

# Provider-default fallbacks for unknown model ids. These are the lowest-
# tier price each provider currently quotes; safer to under-estimate than
# over-estimate when the model is unknown.
_PROVIDER_DEFAULTS: dict[str, ModelPrice] = {
    "anthropic": _PRICES["claude-haiku-4-5"],
    "gemini":    _PRICES["gemini-2.5-flash"],
    "google":    _PRICES["gemini-2.5-flash"],
    "openai":    _PRICES["gpt-4o-mini"],
}


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------


def price_for(model: str | None, provider: str | None = None) -> ModelPrice | None:
    """Lookup pricing for one (model, provider) pair.

    Tries exact match first, then falls back to the provider default if
    the model is unknown but the provider is recognized. Returns ``None``
    if we can't price this call — the caller should display the cost as
    ``"—"`` rather than guess.
    """
    if model:
        # Exact match first.
        hit = _PRICES.get(model)
        if hit is not None:
            return hit
        # Some captures store the provider-prefixed name (e.g.
        # "anthropic:claude-haiku-4-5"). Strip and retry.
        if ":" in model:
            stripped = model.split(":", 1)[1]
            hit = _PRICES.get(stripped)
            if hit is not None:
                return hit
    if provider:
        return _PROVIDER_DEFAULTS.get(provider.lower())
    return None


def estimate_cost_usd(
    in_tokens: int | None, out_tokens: int | None,
    *, model: str | None = None, provider: str | None = None,
) -> float | None:
    """Compute the USD cost for a single LLM call.

    Returns ``None`` if either token count is missing (we won't make
    up data) or no pricing is known for the model/provider pair.
    Otherwise returns a non-negative ``float`` in USD.
    """
    if in_tokens is None or out_tokens is None:
        return None
    p = price_for(model, provider)
    if p is None:
        return None
    return (
        (max(0, int(in_tokens)) / 1_000_000.0) * p.in_per_m
        + (max(0, int(out_tokens)) / 1_000_000.0) * p.out_per_m
    )


def format_usd(cost: float | None) -> str:
    """Render a cost value with sensible precision for the CLI.

    - ``None`` → ``"—"``                         (unknown)
    - exactly 0 → ``"$0.00"``                    (priced, but zero)
    - 0 < x < 0.0001 → ``"<$0.0001"``            (priced, but rounds to nothing)
    - 0.0001 ≤ x < 1 → 4 decimal places           (e.g. ``$0.0023``)
    - else → 2 decimal places with thousands sep  (e.g. ``$1,234.50``)
    """
    if cost is None:
        return "—"
    if cost == 0:
        return "$0.00"
    if cost < 0.0001:
        return "<$0.0001"
    if cost < 1:
        return f"${cost:.4f}"
    return f"${cost:,.2f}"


def known_models() -> list[ModelPrice]:
    """Every model with explicit pricing — used by ``loupe providers``
    and the dashboard's settings panel."""
    return sorted(_PRICES.values(), key=lambda p: (p.provider, p.model))


__all__ = [
    "ModelPrice",
    "estimate_cost_usd",
    "format_usd",
    "known_models",
    "price_for",
]
