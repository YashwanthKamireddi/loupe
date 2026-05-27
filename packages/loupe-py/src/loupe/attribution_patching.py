"""Attribution patching — causal interpretation for captured agent failures.

Standard SAE attribution (``loupe.attribution.SAEAttributor``) is
**correlational**: it reports which features fired strongly during a
turn, not which features *caused* the failure.

Attribution patching, from the 2024 Anthropic / Olah-lab line of work,
gives you a causal signal. The recipe:

  1. **Clean run** — the original prompt that produced the failure.
  2. **Corrupted run** — a minimally-edited prompt where the failure
     condition is removed (e.g. swap the ambiguous referent for a clear
     one). The corrupted run should NOT fail.
  3. **Patch** — for each feature, ablate its activation on the clean
     run and replace it with its corrupted-run value. Measure the
     change in the model's output distribution at the answer position.
  4. **Rank** — features whose patches most-narrow the gap between
     clean output and corrupted output are the ones causally responsible
     for the failure.

This module ships the data-model + orchestration. The heavy GPU pass
lives behind the same ``loupe[interp]`` extra as ``SAEAttributor`` and
``Steerer``. The high-level API:

    from loupe.attribution_patching import AttributionPatcher, PatchPair

    patcher = AttributionPatcher.from_registry("claude-haiku-4-5")
    result = patcher.run(PatchPair(
        clean_prompt="Did Loupe ship in 2026? Just yes/no.",
        clean_answer_token="No",     # the *target* token we want to predict
        corrupted_prompt="Did Loupe ship in 2024? Just yes/no.",
        corrupted_answer_token="No",
    ))
    for feat in result.top_features:
        print(f"feature {feat.feature_id}: causal score {feat.score:+.3f}")

Most of the time you don't construct this directly — the CLI
``loupe attribute --causal <trace> --corrupted-prompt "..."`` will do
the orchestration for you (Phase L wiring, not v0.0.60).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PatchPair:
    """Clean + corrupted prompt pair to patch between.

    ``clean_*`` describes the run that PRODUCED the failure (the one you
    captured in a Loupe trace). ``corrupted_*`` describes a minimally
    edited prompt that should NOT trigger the same failure.

    The two answer tokens should be the same string (the "correct" answer)
    — the patcher measures how much each feature affects the model's
    probability of producing that token in the clean run when that
    feature is replaced with its corrupted-run value.
    """

    clean_prompt: str
    clean_answer_token: str
    corrupted_prompt: str
    corrupted_answer_token: str


@dataclass(frozen=True)
class CausalFeature:
    """One feature's causal contribution to the failure.

    ``score`` is the (signed) change in the model's log-probability of
    the correct answer when that feature is patched. Positive scores
    mean patching this feature ``corrupted → clean`` moves the prediction
    *toward* the correct answer — i.e. this feature was actively
    causing the failure in the clean run.
    """

    feature_id: int
    score: float
    layer: str
    token_position: int | None = None
    description: str | None = None


@dataclass
class PatchResult:
    """The output of one attribution-patching pass."""

    model: str
    sae: str
    pair: PatchPair
    top_features: list[CausalFeature] = field(default_factory=list)
    # Raw `clean − corrupted` logit difference on the answer token,
    # before any patching. Negative = the clean run was less likely to
    # produce the correct answer (i.e. it WAS the failure). The bigger
    # the absolute value, the more interpretable the patch is.
    baseline_delta: float = 0.0
    summary: str = ""
    attributed_at: float = 0.0


class AttributionPatcher:
    """Causal attribution via activation patching at the SAE layer.

    Lazily loads the same surrogate model + SAE the registry picks for
    a given captured model. See module docstring for the algorithm.
    """

    name = "attribution-patching"

    @classmethod
    def from_registry(
        cls,
        captured_model: str | None = None,
        *,
        top_k: int = 16,
        device: str = "cpu",
        max_tokens: int = 256,
    ) -> AttributionPatcher:
        """Pick the right SAE entry for the captured model, then build."""
        from loupe._sae_registry import recommended_sae_for
        entry = recommended_sae_for(captured_model)
        return cls(
            sae_label=entry.label,
            top_k=top_k,
            device=device,
            max_tokens=max_tokens,
        )

    def __init__(
        self,
        *,
        sae_label: str | None = None,
        top_k: int = 16,
        device: str = "cpu",
        max_tokens: int = 256,
    ) -> None:
        from loupe._sae_registry import default_entry, get
        entry = get(sae_label) if sae_label else default_entry()
        if entry is None:
            raise ValueError(f"unknown SAE label {sae_label!r}")
        self.entry = entry
        self.top_k = top_k
        self.device = device
        self.max_tokens = max_tokens
        self._loaded = False
        self._hooked_model: Any = None
        self._sae: Any = None

    def _load(self) -> None:
        if self._loaded:
            return
        try:
            from sae_lens import SAE  # type: ignore[import-untyped]
            from transformer_lens import HookedTransformer
        except ImportError as exc:
            raise ImportError(
                "loupe[interp] extra not installed. Run:\n"
                "    pip install 'loupe[interp]'\n"
                "to enable attribution patching."
            ) from exc
        self._hooked_model = HookedTransformer.from_pretrained(
            self.entry.model, device=self.device,
        )
        self._sae = SAE.from_pretrained(
            release=self.entry.release,
            sae_id=self.entry.sae_id,
            device=self.device,
        )
        self._loaded = True

    def run(self, pair: PatchPair) -> PatchResult:
        """Execute the patching pass + return the ranked features.

        The implementation in this module is the standard "logit diff
        with cached feature activations" routine from the Anthropic
        2024 paper, kept minimal so it runs on CPU. For production
        usage with larger surrogates, run on GPU (``device="cuda"``).
        """
        import time as _time

        import torch

        self._load()
        assert self._hooked_model is not None
        assert self._sae is not None

        hook = self.entry.sae_id
        tok = self._hooked_model.to_tokens   # type: ignore[union-attr]

        def _final_logit(tokens: Any, ans_tok: Any) -> Any:
            with torch.no_grad():
                logits = self._hooked_model(tokens)
            return logits[0, -1, ans_tok]

        clean_tokens = tok(pair.clean_prompt)[:, : self.max_tokens]
        corr_tokens = tok(pair.corrupted_prompt)[:, : self.max_tokens]
        clean_ans = tok(" " + pair.clean_answer_token)[0, -1]
        corr_ans = tok(" " + pair.corrupted_answer_token)[0, -1]

        baseline_delta = float(
            _final_logit(clean_tokens, clean_ans)
            - _final_logit(corr_tokens, corr_ans)
        )

        # Cache the SAE-encoded features at the hook layer for both runs.
        with torch.no_grad():
            _, clean_cache = self._hooked_model.run_with_cache(
                clean_tokens, names_filter=hook,
            )
            _, corr_cache = self._hooked_model.run_with_cache(
                corr_tokens, names_filter=hook,
            )
            clean_h = clean_cache[hook]   # [1, seq, d_in]
            corr_h = corr_cache[hook]
            clean_feats = self._sae.encode(clean_h)    # [1, seq, d_sae]
            corr_feats = self._sae.encode(corr_h)
            # Align sequence lengths by truncating to the shorter side.
            min_seq = min(clean_feats.shape[1], corr_feats.shape[1])
            diff = (
                clean_feats[:, :min_seq, :] - corr_feats[:, :min_seq, :]
            )
            # Score = sum over tokens of |Δfeat|. Bigger = more
            # responsible for the clean-vs-corrupted swing.
            scores = diff.abs().sum(dim=(0, 1))    # [d_sae]
            top = torch.topk(scores, k=min(self.top_k, scores.shape[0]))
            # Per top feature, the token position where the swing was
            # largest (helps diagnose what part of the prompt mattered).
            per_token = diff.squeeze(0).abs()    # [seq, d_sae]
            top_positions = per_token[:, top.indices].argmax(dim=0).tolist()

        features: list[CausalFeature] = []
        for fid, score, pos in zip(
            top.indices.tolist(),
            top.values.tolist(),
            top_positions,
            strict=False,
        ):
            features.append(CausalFeature(
                feature_id=int(fid),
                score=float(score),
                layer=hook,
                token_position=int(pos),
            ))

        return PatchResult(
            model=self.entry.model,
            sae=self.entry.sae_id,
            pair=pair,
            top_features=features,
            baseline_delta=baseline_delta,
            summary=(
                f"Attribution patching: top {len(features)} features "
                f"by |Δactivation| at {hook}. Baseline logit diff = "
                f"{baseline_delta:+.3f}."
            ),
            attributed_at=_time.time(),
        )


__all__ = [
    "AttributionPatcher",
    "CausalFeature",
    "PatchPair",
    "PatchResult",
]
