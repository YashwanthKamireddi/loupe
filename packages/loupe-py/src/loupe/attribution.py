"""Circuit attribution — SAE-based mechanistic foothold per agent step.

This module is the v0.2 research artifact's foundation. The idea:

    Tagging a step as "hallucination" tells us *what* went wrong.
    Attributing the step to its top-K interpretable features tells us
    *why* — at the level of mechanism, not behavior.

For each LLM call captured by Loupe, we can (optionally) re-run the
prompt through the same model with hooks installed, project the hidden
states through a trained Sparse Autoencoder, and record the highest-
activating features. Cluster across many tagged failures, and patterns
emerge — sets of features that co-fire on hallucinations but not on
loops, etc.

The actual SAE / activation extraction is heavy (GBs of weights, GPU
preferred). To keep the import-time and test-time footprint sane:

  1. The data model + the orchestration live here (always available).
  2. A :class:`MockAttributor` returns synthetic-but-valid results so
     CI + every test can exercise the full pipeline.
  3. The real :class:`SAEAttributor` is import-guarded against the
     optional ``[interp]`` extra (``transformer-lens`` + ``sae-lens``).
     Users install it explicitly when they're ready to run the GPU pass.

Public surface
--------------
- :class:`FeatureActivation` — one feature firing during one step.
- :class:`AttributionResult` — the full per-step attribution.
- :class:`Attributor` — Protocol every attributor implements.
- :class:`MockAttributor` — deterministic, no deps, for tests.
- :func:`make_attributor` — factory that picks an attributor by name.
- :func:`attribute_trace` — orchestrate the per-step attribution.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

# Re-export the JSON-stable serialization helpers so callers don't have to
# import dataclasses themselves.
__all__ = [
    "AttributionResult",
    "Attributor",
    "FeatureActivation",
    "MockAttributor",
    "attribute_trace",
    "make_attributor",
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureActivation:
    """One SAE feature firing during one step.

    ``feature_id`` is the index in the SAE's feature dictionary.
    ``activation`` is the post-ReLU magnitude (always >= 0 for sparse SAEs).
    ``layer`` and ``token_position`` are diagnostic metadata for downstream
    cluster analysis.
    """

    feature_id: int
    activation: float
    layer: str
    token_position: int | None = None


@dataclass
class AttributionResult:
    """SAE-based circuit attribution for one ``llm-call`` step.

    Serialized as JSON inside ``Annotation.circuit_attribution``. The wire
    format here is intentionally redundant (we store the model + SAE
    identifier alongside the activations) so that years later a reader
    can still reproduce the analysis without external context.
    """

    model: str
    sae: str
    method: str
    top_features: list[FeatureActivation] = field(default_factory=list)
    summary: str | None = None
    # When the attributor was run (unix seconds). Lets us re-run later and
    # keep a history without overwriting earlier results blindly.
    attributed_at: float = 0.0

    def to_json_dict(self) -> dict[str, Any]:
        """JSON-stable dict — what gets persisted into the annotation."""
        return {
            "model": self.model,
            "sae": self.sae,
            "method": self.method,
            "top_features": [asdict(f) for f in self.top_features],
            "summary": self.summary,
            "attributed_at": self.attributed_at,
        }


# ---------------------------------------------------------------------------
# Attributor protocol — every backend must implement this
# ---------------------------------------------------------------------------


class Attributor(Protocol):
    """Anything that can produce an :class:`AttributionResult` for one step.

    Implementations may be GPU-heavy (real SAEs) or trivial (the mock).
    The protocol is intentionally narrow so the orchestration code stays
    the same regardless of backend.
    """

    name: str
    model: str
    sae: str

    def attribute(
        self,
        *,
        prompt: str,
        response: str,
        step_id: str,
        trace_id: str,
    ) -> AttributionResult:
        """Compute the attribution. Pure function; no I/O side effects."""
        ...


# ---------------------------------------------------------------------------
# Mock attributor — deterministic synthetic features for tests + CI
# ---------------------------------------------------------------------------


class MockAttributor:
    """Deterministic synthetic attribution. No model weights, no GPU.

    Implemented by hashing (prompt + response + step_id) into a fixed-size
    feature-id space. Two identical inputs always produce the same output,
    so tests and the rest of the pipeline can assert reproducibility.

    Used by:
      - The default CLI test path (so we don't need GBs of weights in CI).
      - Real users who want to validate the *plumbing* end-to-end before
        installing the heavy ``[interp]`` extra.
    """

    name = "mock"

    def __init__(
        self,
        *,
        model: str = "mock-model",
        sae: str = "mock-sae",
        top_k: int = 8,
        feature_space: int = 16384,
    ) -> None:
        self.model = model
        self.sae = sae
        self.top_k = top_k
        self.feature_space = feature_space

    def attribute(
        self,
        *,
        prompt: str,
        response: str,
        step_id: str,
        trace_id: str,
    ) -> AttributionResult:
        import time as _time

        # Deterministic seed — same inputs always yield same features.
        # The id + activation pair is derived from the hash; for the
        # mock we want this to be reproducible across machines.
        seed_bytes = f"{trace_id}::{step_id}::{prompt}::{response}".encode()
        digest = hashlib.sha256(seed_bytes).digest()

        features: list[FeatureActivation] = []
        for i in range(self.top_k):
            chunk = digest[(i * 4) % len(digest) : (i * 4) % len(digest) + 4]
            if len(chunk) < 4:
                chunk = digest[:4]
            raw_id = int.from_bytes(chunk, "big")
            feature_id = raw_id % self.feature_space
            # Mock activation magnitude: bounded in [0.5, 5.0], strictly
            # decreasing so the "top" feature is genuinely top.
            activation = round(5.0 - (i * (4.5 / max(1, self.top_k))), 4)
            features.append(
                FeatureActivation(
                    feature_id=feature_id,
                    activation=activation,
                    layer="mock.layer.resid_post",
                    token_position=None,
                )
            )

        return AttributionResult(
            model=self.model,
            sae=self.sae,
            method="mock-hash-topk",
            top_features=features,
            summary=(
                f"Mock attribution: hashed top-{self.top_k} features "
                f"from {len(prompt)}-char prompt + {len(response)}-char response."
            ),
            attributed_at=_time.time(),
        )


# ---------------------------------------------------------------------------
# Real SAE attributor — opt-in, lazily imports the [interp] extra
# ---------------------------------------------------------------------------


def _try_import_sae_lens() -> Any:
    """Return the sae_lens module if installed, else None.

    Lazy so that ``import loupe.attribution`` stays cheap and dependency-free.
    """
    try:
        import sae_lens  # type: ignore[import-not-found]
    except ImportError:
        return None
    return sae_lens


class SAEAttributor:
    """Real SAE-based attribution using sae-lens + transformer-lens.

    Per-step pipeline:

    1. Tokenize ``prompt + " " + response`` with the model's tokenizer.
    2. Run a forward pass that caches hidden states at the SAE's
       configured hook (e.g. ``blocks.6.hook_resid_pre``).
    3. Encode those hidden states through the SAE → post-ReLU
       feature activations of shape ``[batch, seq, d_sae]``.
    4. Sum across the sequence dimension to get per-feature total
       magnitude across the whole prompt+response, then take the
       top-K. Each feature's reported ``token_position`` is the
       position where it fired strongest.

    Model + SAE are loaded **lazily** on the first ``.attribute()``
    call and cached on the instance, so making 100 calls only pays
    the load cost once. CPU-only by default; pass ``device="cuda"``
    if you have a GPU.

    Construction is cheap. It only fails when the ``loupe[interp]``
    extra isn't installed.

    Defaults
    --------
    ``model="gpt2-small"`` + ``sae="blocks.6.hook_resid_pre"`` from
    Joseph Bloom's ``gpt2-small-res-jb`` release on Neuronpedia.
    These are well-studied features at the mid-network residual
    stream — a sensible default for an end-to-end demo.

    Limitations
    -----------
    - Frontier-lab closed models (Claude, GPT-4) have no public SAE,
      so this backend can't attribute their calls directly. The
      workflow Loupe is built for: capture an agent that uses a
      closed model, but run the *same prompt* through an open model
      below for attribution. The features are not literally the ones
      that fired in Claude — they're the ones an open model would
      have used to produce a similar continuation. That correlation
      is what mech-interp research relies on today.
    """

    name = "sae"

    # Reasonable defaults so `loupe attribute --backend sae` works
    # immediately on first install without further config.
    DEFAULT_MODEL = "gpt2-small"
    DEFAULT_RELEASE = "gpt2-small-res-jb"
    DEFAULT_SAE = "blocks.6.hook_resid_pre"

    def __init__(
        self,
        *,
        model: str | None = None,
        sae: str | None = None,
        release: str | None = None,
        top_k: int = 16,
        device: str = "cpu",
        max_tokens: int = 256,
    ) -> None:
        if _try_import_sae_lens() is None:
            raise ImportError(
                "loupe[interp] extra not installed. Run:\n"
                "    pip install 'loupe[interp]'\n"
                "to enable real SAE-based attribution (downloads "
                "transformer-lens + sae-lens + their torch deps)."
            )
        self.model = model or self.DEFAULT_MODEL
        self.sae = sae or self.DEFAULT_SAE
        self.release = release or self.DEFAULT_RELEASE
        self.top_k = top_k
        self.device = device
        # We bound the input length so long traces don't OOM us on CPU.
        self.max_tokens = max_tokens
        # Lazy: weights downloaded on first .attribute() call, then cached.
        self._loaded = False
        self._hooked_model: Any = None
        self._sae_module: Any = None
        self._hook_name: str = ""

    def _load(self) -> None:
        """One-time download + load. Cached on the instance."""
        if self._loaded:
            return
        # Lazy imports keep `import loupe.attribution` dependency-free.
        from sae_lens import SAE
        from transformer_lens import HookedTransformer

        self._hooked_model = HookedTransformer.from_pretrained(
            self.model, device=self.device,
        )
        self._sae_module = SAE.from_pretrained(
            release=self.release,
            sae_id=self.sae,
            device=self.device,
        )
        # In sae-lens v6+ the hook target lives on cfg.metadata; older
        # versions had it directly on cfg. Support both.
        meta = getattr(self._sae_module.cfg, "metadata", None)
        hook_name = (
            getattr(meta, "hook_name", None)
            if meta is not None
            else getattr(self._sae_module.cfg, "hook_name", None)
        )
        if not isinstance(hook_name, str):
            raise RuntimeError(
                f"Could not resolve hook_name for SAE {self.release}/{self.sae}"
            )
        self._hook_name = hook_name
        self._loaded = True

    def attribute(
        self,
        *,
        prompt: str,
        response: str,
        step_id: str,
        trace_id: str,
    ) -> AttributionResult:
        import time as _time

        import torch  # local: torch is only available via [interp] extra

        self._load()
        assert self._hooked_model is not None
        assert self._sae_module is not None

        # Join prompt + response so feature activations cover the full
        # turn, not just the user side. Cap tokens so a long trace can't
        # blow up CPU memory.
        text = (prompt + "\n" + response).strip()
        if not text:
            return AttributionResult(
                model=self.model,
                sae=self.sae,
                method="sae-encode-topk",
                top_features=[],
                summary="empty prompt+response — nothing to attribute",
                attributed_at=_time.time(),
            )

        tokens = self._hooked_model.to_tokens(text)
        if tokens.shape[1] > self.max_tokens:
            tokens = tokens[:, : self.max_tokens]

        # Forward pass, only caching the layer the SAE consumes.
        with torch.no_grad():
            _, cache = self._hooked_model.run_with_cache(
                tokens, names_filter=self._hook_name,
            )
            hidden = cache[self._hook_name]    # [batch, seq, d_in]
            feats = self._sae_module.encode(hidden)   # [batch, seq, d_sae]
            # Aggregate magnitude per feature across all tokens in this turn.
            totals = feats.sum(dim=(0, 1))    # [d_sae]
            top = torch.topk(totals, k=min(self.top_k, totals.shape[0]))
            # Plus, per top feature, the token position where it peaked —
            # useful diagnostic for downstream cluster analysis.
            per_token = feats.squeeze(0)   # [seq, d_sae]
            top_token_positions = per_token[:, top.indices].argmax(dim=0).tolist()

        features = [
            FeatureActivation(
                feature_id=int(top.indices[i].item()),
                activation=round(float(top.values[i].item()), 4),
                layer=self._hook_name,
                token_position=int(top_token_positions[i]),
            )
            for i in range(top.indices.shape[0])
        ]

        return AttributionResult(
            model=self.model,
            sae=f"{self.release}/{self.sae}",
            method="sae-encode-topk",
            top_features=features,
            summary=(
                f"{tokens.shape[1]} tokens through {self.model} → "
                f"{self._sae_module.cfg.d_sae}-dim SAE → top-{self.top_k} "
                f"features at {self._hook_name}."
            ),
            attributed_at=_time.time(),
        )


# ---------------------------------------------------------------------------
# Factory + orchestration
# ---------------------------------------------------------------------------


def make_attributor(
    backend: str = "mock",
    *,
    model: str | None = None,
    sae: str | None = None,
    top_k: int = 8,
) -> Attributor:
    """Pick an attributor by name. Unknown names raise ``ValueError``.

    Always-available backends:
      - ``mock`` — deterministic synthetic, no deps. Default.
      - ``sae``  — real SAE attribution. Requires ``[interp]`` extra,
                   raises ``ImportError`` at construction if missing.
    """
    backend = backend.lower()
    if backend == "mock":
        return MockAttributor(
            model=model or "mock-model",
            sae=sae or "mock-sae",
            top_k=top_k,
        )
    if backend == "sae":
        # Both model + sae can be omitted; SAEAttributor falls back to
        # its bundled defaults (gpt2-small + blocks.6.hook_resid_pre from
        # the gpt2-small-res-jb release). Users override either to point
        # at a different model/SAE pair.
        return SAEAttributor(model=model, sae=sae, top_k=top_k)
    raise ValueError(
        f"Unknown attribution backend {backend!r}. Use 'mock' or 'sae'."
    )


def attribute_trace(
    trace_path: Path,
    attributor: Attributor,
    *,
    only_failing: bool = False,
) -> list[tuple[str, AttributionResult]]:
    """Walk a trace's llm-call steps and attribute each one.

    Args:
      trace_path: path to a JSONL trace file.
      attributor: any Attributor implementation.
      only_failing: if True, only attribute steps where ``error`` is set.

    Returns a list of ``(step_id, AttributionResult)`` pairs. The caller
    decides what to do with them — typically persist into the annotation
    store via :func:`persist_attribution` below.
    """
    results: list[tuple[str, AttributionResult]] = []
    header: dict[str, Any] | None = None
    with trace_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            kind = obj.pop("_type", None)
            if kind == "trace":
                header = obj
                continue
            if kind != "step":
                continue
            if obj.get("kind") != "llm-call":
                continue
            if only_failing and not obj.get("error"):
                continue

            prompt = _extract_prompt(obj.get("inputs") or {})
            response = _extract_response(obj.get("outputs") or {})
            # Skip steps we can't reconstruct prompts/responses for — they'd
            # produce noise in the attribution.
            if not prompt and not response:
                continue

            trace_id = (header or {}).get("trace_id", trace_path.stem)
            result = attributor.attribute(
                prompt=prompt,
                response=response,
                step_id=str(obj.get("step_id", "")),
                trace_id=str(trace_id),
            )
            results.append((str(obj["step_id"]), result))
    return results


def _extract_prompt(inputs: dict[str, Any]) -> str:
    """Best-effort: turn an llm-call step's inputs into a single prompt string.

    Handles Anthropic-style ``messages``, OpenAI-style ``messages``, and
    the simpler ``prompt``/``contents`` shapes. Truncates each part so
    the attributor doesn't get a 100k-char blob.
    """
    if "prompt" in inputs and isinstance(inputs["prompt"], str):
        return inputs["prompt"][:8000]
    if "contents" in inputs:
        # Gemini-style: a single string or list of part dicts.
        c = inputs["contents"]
        if isinstance(c, str):
            return c[:8000]
        if isinstance(c, list):
            return _join_message_parts(c)[:8000]
    if "messages" in inputs:
        msgs = inputs["messages"]
        if isinstance(msgs, str):
            return msgs[:8000]
        if isinstance(msgs, list):
            return _join_message_parts(msgs)[:8000]
    return ""


def _join_message_parts(msgs: list[Any]) -> str:
    parts: list[str] = []
    for m in msgs:
        if isinstance(m, dict):
            content = m.get("content") or m.get("parts")
            role = m.get("role", "?")
            if isinstance(content, str):
                parts.append(f"[{role}] {content}")
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and isinstance(c.get("text"), str):
                        parts.append(f"[{role}] {c['text']}")
                    elif isinstance(c, str):
                        parts.append(f"[{role}] {c}")
    return "\n".join(parts)


def _extract_response(outputs: dict[str, Any]) -> str:
    """Best-effort: turn an llm-call step's outputs into a response string."""
    if isinstance(outputs.get("text"), str):
        return outputs["text"][:8000]
    if isinstance(outputs.get("answer"), str):
        return outputs["answer"][:8000]
    return ""
