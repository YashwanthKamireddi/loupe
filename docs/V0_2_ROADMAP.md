# Loupe v0.2 — roadmap

Two big items, in dependency order. Status as of v0.0.37.

## 1. DuckDB index for fast search across many traces

**Why:** today every `loupe list / stats / verify --all` re-reads every
JSONL file from disk. Fine for ≤1k traces; painful at 10k+.

**Shape:**
- New module `loupe.index` — an embedded DuckDB instance at
  `~/.loupe/index.duckdb` with tables `traces` (header columns) and
  `steps` (denormalized step rows).
- A `loupe.index.upsert(trace_id)` call after every trace is written.
- `loupe list / stats / verify --all` query the index instead of
  walking the disk.
- `loupe index rebuild` to re-index after a crash.

**Why DuckDB and not SQLite:**
- Columnar storage → 10× faster aggregates for `stats`.
- Embedded, zero-config, single-file. No daemon.
- Already a dependency (we use it in tests).

**Estimated effort:** ~3 days of focused work. Self-contained.

## 2. SAE-based circuit attribution per failing step

**This is the research-paper artifact** — the thing that separates
Loupe from every other agent observability tool.

**Why it matters:** when you tag a step as "hallucination" or
"context-loss", we currently store the human label. With SAE
attribution, we additionally store **which interpretable features in
the model fired during that step**, so future researchers have a
mechanistic foothold into agent failures, not just black-box labels.

**Shape (rough):**
1. **Acquire activations.** For each tagged failing step:
   - The captured trace has the exact prompt + model response.
   - Re-run the same prompt through the model with hooks (TransformerLens
     for open models; for closed models, sample probable next-tokens
     and analyze the response token-by-token via the logprobs API).
2. **Project through an SAE.** Use Anthropic's published SAE features
   for Sonnet-class models, or a public Sparse Autoencoder from
   sae-lens for open models.
3. **Top-K feature activations.** Store the top 20 features with
   highest activation as `Annotation.circuit_attribution`.
4. **Cluster across many failures.** Once we have 100+ annotated
   failures, hierarchical-cluster them by their feature activation
   patterns. Goal: surface "failure circuits" — sets of features that
   co-fire across hallucination failures but not loop failures.

**Risks:**
- Closed models have no SAE access (Anthropic publishes some Sonnet
  features but not all). Start with open Llama/Gemma + sae-lens for
  the prototype.
- Activations are gigabytes per long trace. Need a sampling strategy.

**This is the cold-email-to-Neel-Nanda topic.** Ask him for the
sharpest version of step 1-3 before writing code.

**Estimated effort:** 3 months part-time. The publishable artifact.

## 3. Hosted Loupe Cloud (deprioritized)

Originally on the v0.2 list, but reconsidering. The whole pitch of
Loupe is "your traces stay on your laptop." Adding a hosted sync
service muddies that. Defer to v0.3 (or skip entirely).

If we do build it: opt-in only, end-to-end encrypted, minimal server
side ("storage relay" not "trace database"). Strict no-vendor-lock-in
posture.

---

## Sequencing recommendation

1. **Now:** wait for the v0.0.37 cleanup pass to settle (this session)
2. **Week 1:** DuckDB indexer. Concrete, finishable, immediate UX win.
3. **Week 2:** Send the Neel Nanda cold email. While waiting for reply:
4. **Week 2-4:** Pilot SAE attribution on an open Llama model with
   sae-lens. One agent failure → one feature activation report. Iterate.
5. **Month 2-3:** Scale to 100+ failures, cluster, write up.

The DuckDB indexer is a force-multiplier for everything that follows.
The SAE work is the artifact admissions committees + Anthropic care about.
