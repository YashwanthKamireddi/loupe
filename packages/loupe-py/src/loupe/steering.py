"""Feature steering — replay a captured turn with edited SAE features.

Mech-interp 101 question: "what if SAE feature 12345 hadn't fired so
strongly on this turn? Would the model still have looped/hallucinated?"

This module gives you the data-plumbing to answer that:

  1. Take a captured trace's failing step.
  2. Re-run the prompt through the same surrogate model used for
     attribution, but install a forward hook on the SAE's layer that
     either DAMPENS (multiplier < 1.0) or AMPLIFIES (> 1.0) a specific
     feature's activation before letting the residual stream continue.
  3. Capture the steered continuation as a NEW Loupe trace whose
     ``metadata.steered_from = <original_trace_id>`` and
     ``metadata.steer = {feature_id, multiplier}``.

The result is the same Trace + Step shape every other capture produces,
so the dashboard, ``loupe diff``, and ``loupe attribute`` all work
unchanged on steered runs.

Heavy lifting (forward hooks, tensor ops) lives behind the
``loupe[interp]`` import guard, exactly like :class:`SAEAttributor`.
The data-model + orchestration in this file is import-cheap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SteerSpec:
    """One feature edit to apply during a steered replay.

    ``feature_id`` is an index into the SAE feature dictionary (matches
    the ``FeatureActivation.feature_id`` recorded by attribution).
    ``multiplier`` is the scaling factor applied to that feature's
    post-encode activation:

        - ``0.0``  → ablate (feature cannot fire)
        - ``0.5``  → halve its contribution
        - ``1.0``  → identity (no-op, useful for testing the pipeline)
        - ``2.0``  → amplify

    The model otherwise runs identically — same prompt, same temperature,
    same sampler. The only difference is the dampened/amplified feature.
    """

    feature_id: int
    multiplier: float


@dataclass
class SteerResult:
    """What a steered run produced. Returned by :meth:`Steerer.run`.

    The captured continuation lives at ``trace_id`` (a new trace file on
    disk under ``~/.loupe/traces/``). Everything else here is metadata
    the dashboard + CLI use to render the comparison.
    """

    trace_id: str
    original_trace_id: str
    prompt: str
    steered_text: str
    spec: SteerSpec
    model: str
    sae_release: str
    sae_id: str


class Steerer:
    """Run a captured prompt through a surrogate model with one SAE
    feature dampened / amplified.

    Construction is cheap (no model loaded). The first :meth:`run` call
    downloads + caches model + SAE weights, same as
    :class:`SAEAttributor`. Subsequent calls reuse the cached state.

    Limitations
    -----------
    - Like attribution, only OPEN-WEIGHT surrogate models can be steered.
      You can't reach into Claude or GPT-4 and edit a feature there —
      no public SAE exists. The steered run uses an open surrogate;
      results are correlational, not causal proof for the closed model.
    - Forward hooks add ~10–30% latency to each generation call. Fine
      for offline analysis, not a hot path.
    """

    name = "steerer"

    def __init__(
        self,
        *,
        sae_label: str | None = None,
        device: str = "cpu",
        max_new_tokens: int = 200,
    ) -> None:
        from loupe._sae_registry import default_entry, get
        if sae_label:
            entry = get(sae_label)
            if entry is None:
                raise ValueError(
                    f"unknown SAE label {sae_label!r}; pick one from "
                    "`loupe explain attribution`"
                )
        else:
            entry = default_entry()
        self.entry = entry
        self.device = device
        self.max_new_tokens = max_new_tokens
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
                "to enable feature steering."
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

    # The real generation path is intentionally kept import-light: the
    # tensor ops are gated behind the [interp] extra and only run when
    # someone calls ``Steerer.run(...)``. Tests can mock around this.
    def run(
        self,
        *,
        prompt: str,
        spec: SteerSpec,
        original_trace_id: str = "",
        store: Any = None,
    ) -> SteerResult:
        """Generate a steered continuation + persist it as a new trace.

        Args:
            prompt: the input the original turn was given.
            spec: which feature to edit and by how much.
            original_trace_id: link back to the trace this was steered
                from (recorded in trace.metadata so the dashboard can
                render side-by-side).
            store: optional Loupe Store; defaults to the JSONL store.
        """
        import torch  # local — needs the [interp] extra

        from loupe import record_step
        from loupe import trace as trace_decorator
        from loupe.store import default_store as _default_store
        from loupe.trace import _current_trace as _ctx_var

        self._load()
        assert self._hooked_model is not None
        assert self._sae is not None

        hook_name = self.entry.sae_id   # sae-lens hook target

        def _edit_hook(activation: Any, hook: Any) -> Any:
            """Forward hook: encode → scale one feature → decode."""
            with torch.no_grad():
                feats = self._sae.encode(activation)
                feats[..., spec.feature_id] = (
                    feats[..., spec.feature_id] * spec.multiplier
                )
                recon = self._sae.decode(feats)
            return recon

        @trace_decorator(
            name=f"steer:{spec.feature_id}",
            framework="loupe-steer",
            store=store or _default_store(),
        )
        def _execute() -> str:
            record_step(
                "plan", "steered replay",
                outputs={
                    "feature_id": spec.feature_id,
                    "multiplier": spec.multiplier,
                    "model": self.entry.model,
                    "sae_release": self.entry.release,
                    "original_trace_id": original_trace_id or None,
                },
            )
            with self._hooked_model.hooks(fwd_hooks=[(hook_name, _edit_hook)]):
                output = self._hooked_model.generate(
                    prompt,
                    max_new_tokens=self.max_new_tokens,
                    do_sample=False,
                )
            # transformer-lens returns the prompt+continuation; we strip
            # the prompt so the recorded `text` is just the steered tail.
            steered_text = (
                output[len(prompt):] if isinstance(output, str) and output.startswith(prompt)
                else str(output)
            )
            record_step(
                "llm-call",
                f"{self.entry.model}:steered",
                inputs={"provider": "open-surrogate", "model": self.entry.model,
                        "prompt": prompt[:200], "steer": {
                            "feature_id": spec.feature_id,
                            "multiplier": spec.multiplier,
                        }},
                outputs={"text": steered_text[:600]},
            )
            return steered_text

        text: str = _execute()

        # After @trace exits, the current-trace ContextVar is back to
        # None — but the trace was written. Find it by mtime.

        from loupe.store import _default_dir
        traces_dir = _default_dir() / "traces"
        latest = max(
            (p for p in traces_dir.glob("*.jsonl")),
            key=lambda p: p.stat().st_mtime,
            default=None,
        )
        new_id = latest.stem if latest else ""

        # Best-effort: stamp the new trace's header with the steer spec
        # so the dashboard can show "steered from X" without re-running
        # attribution. We do this by rewriting the trace file's first
        # line.
        if latest is not None:
            try:
                import json as _json
                lines = latest.read_text(encoding="utf-8").splitlines()
                hdr = _json.loads(lines[0])
                hdr.setdefault("metadata", {})
                hdr["metadata"]["steered_from"] = original_trace_id
                hdr["metadata"]["steer"] = {
                    "feature_id": spec.feature_id,
                    "multiplier": spec.multiplier,
                    "sae_release": self.entry.release,
                    "sae_id": self.entry.sae_id,
                }
                lines[0] = _json.dumps(hdr, separators=(",", ":"))
                latest.write_text("\n".join(lines) + "\n", encoding="utf-8")
            except Exception:  # noqa: BLE001 — best-effort metadata stamp
                pass

        # Silence unused import warning from a closure-captured value.
        del _ctx_var

        return SteerResult(
            trace_id=new_id,
            original_trace_id=original_trace_id,
            prompt=prompt,
            steered_text=text,
            spec=spec,
            model=self.entry.model,
            sae_release=self.entry.release,
            sae_id=self.entry.sae_id,
        )


__all__ = ["SteerResult", "SteerSpec", "Steerer"]
