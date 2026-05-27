"""Registry of supported SAE / model combinations.

The original ``SAEAttributor`` hardcoded GPT-2 small + a single SAE
release. This registry generalizes the picker:

  - Frontier-lab closed models (Claude, GPT-4, Gemini) have no public
    SAE, so they're attributed by analogy through an *open* surrogate
    model. The registry maps each closed model family to a recommended
    surrogate (e.g. ``claude-haiku-4-5`` → Llama-3.1-8B-Instruct).
  - Open-weight models with their own published SAEs use those directly.

The mapping is data, not code — ``loupe attribute --model X`` resolves
through ``recommended_sae_for(captured_model)`` and the user gets
attribution without needing to know the SAE plumbing.

The supported releases here are conservative — only well-documented
sae-lens releases known to load on CPU. ``loupe explain attribution``
shows the table to end users.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SAEEntry:
    """One (model, release, sae) tuple known to ``sae-lens``."""

    label: str            # short canonical id used in CLI flags
    display: str          # human label
    model: str            # transformer-lens ``HookedTransformer`` model id
    release: str          # sae-lens release name
    sae_id: str           # sae-lens SAE id within the release
    tagline: str          # one-line description for the picker / docs


# Conservative starter set — each entry confirmed to load with CPU-only
# torch + sae-lens. Order = best default first.
SAE_ENTRIES: tuple[SAEEntry, ...] = (
    SAEEntry(
        label="gpt2-small",
        display="GPT-2 small · Joseph Bloom residual stream",
        model="gpt2-small",
        release="gpt2-small-res-jb",
        sae_id="blocks.6.hook_resid_pre",
        tagline="fast · CPU friendly · well-studied features (default)",
    ),
    SAEEntry(
        label="gemma-2-2b",
        display="Gemma-2 2B · GemmaScope L20 residual",
        model="gemma-2-2b",
        release="gemma-scope-2b-pt-res",
        sae_id="layer_20/width_16k/average_l0_71",
        tagline="open-weight 2B model · GemmaScope features",
    ),
    SAEEntry(
        label="pythia-70m",
        display="Pythia 70M · Bloom deduped residual stream",
        model="pythia-70m-deduped",
        release="pythia-70m-deduped-res-sm",
        sae_id="blocks.4.hook_resid_pre",
        tagline="tiny · the lightest surrogate for closed-model traces",
    ),
)


_BY_LABEL: dict[str, SAEEntry] = {e.label: e for e in SAE_ENTRIES}


# Closed-model families → recommended surrogate label. When a captured
# trace used a closed model, this picks the best open SAE entry to run
# the prompt through for *correlational* attribution.
#
# The mapping is intentionally conservative — picking gpt2-small as the
# universal default means "fast + well-documented" rather than "best
# possible correlation". Users with a GPU + interest in larger surrogates
# can pick gemma-2-2b explicitly.
_CLOSED_MODEL_SURROGATES: dict[str, str] = {
    # Anthropic Claude family — every Claude variant routes through gpt2.
    "claude":      "gpt2-small",
    "claude-haiku": "gpt2-small",
    "claude-sonnet": "gpt2-small",
    "claude-opus": "gpt2-small",
    # OpenAI GPT family — same surrogate (the architecture lineage is GPT-2).
    "gpt-4":       "gpt2-small",
    "gpt-4o":      "gpt2-small",
    "gpt-3.5":     "gpt2-small",
    "o1":          "gpt2-small",
    "o3":          "gpt2-small",
    # Google Gemini — Gemma is the open-weight cousin, so we use a
    # Gemma-trained SAE as the surrogate.
    "gemini":      "gemma-2-2b",
    "gemini-pro":  "gemma-2-2b",
    "gemini-flash": "gemma-2-2b",
}


def get(label: str) -> SAEEntry | None:
    """Look up an entry by its short label (case-insensitive)."""
    return _BY_LABEL.get(label.lower().strip())


def labels() -> list[str]:
    """All registered short labels, in picker order."""
    return [e.label for e in SAE_ENTRIES]


def default_entry() -> SAEEntry:
    """The recommended entry when the caller has no preference."""
    return SAE_ENTRIES[0]


def recommended_sae_for(captured_model: str | None) -> SAEEntry:
    """Pick an SAE entry that's appropriate for ``captured_model``.

    Strategy:
      1. Exact label match in the SAE registry (open-weight model, has
         its own SAE → use it directly).
      2. Closed-model family detection (``claude-…`` / ``gpt-…`` /
         ``gemini-…``) → use the registered surrogate.
      3. Fallback: default entry.

    Always returns an entry — never None — so callers can rely on it
    in the hot path of ``loupe attribute``.
    """
    if not captured_model:
        return default_entry()
    name = captured_model.lower()

    # 1. Exact open-weight match (e.g. user captured a "gemma-2-2b" run)
    direct = get(name)
    if direct is not None:
        return direct
    for entry in SAE_ENTRIES:
        if entry.model.lower() in name or name in entry.model.lower():
            return entry

    # 2. Closed-model family — pick the most specific prefix match.
    for key in sorted(_CLOSED_MODEL_SURROGATES, key=len, reverse=True):
        if key in name:
            surrogate = get(_CLOSED_MODEL_SURROGATES[key])
            if surrogate is not None:
                return surrogate

    # 3. Fallback
    return default_entry()


__all__ = [
    "SAE_ENTRIES",
    "SAEEntry",
    "default_entry",
    "get",
    "labels",
    "recommended_sae_for",
]
