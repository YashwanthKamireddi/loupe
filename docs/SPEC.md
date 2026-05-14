# Loupe — Project Spec (v0.0)

> *A living document. Updated as we build. Last edit: 2026-05-14.*

## 1. Vision

Make agent failures **legible**. Anyone running an LLM agent should be able to ask *"why did it fail?"* and get a real answer — not a guess, not a chat log, but a circuit-level explanation grounded in mechanistic interpretability.

## 2. Three artifacts

### 2.1 `loupe` library (Python + TypeScript)
- Drop-in `@trace` decorator + auto-instrumentation
- Captures: prompts, completions, tool calls, file I/O, DOM state (web agents), reasoning chains
- Local-first: writes to DuckDB by default; cloud sync optional
- Zero-config integrations for LangGraph, OpenHands, Claude Agent SDK, Vercel AI SDK

### 2.2 LoupeBench (public dataset)
- 1,000+ real agent failures, each annotated with:
  - **Failure category** (loop, hallucination, tool misuse, deletion, security, etc.)
  - **Root-cause circuit** — which SAE features fired during the failure
  - **Mitigation** — what fixed it (prompt change, tool guard, model swap)
- Hosted on HuggingFace Datasets + GitHub
- CC-BY-4.0 license

### 2.3 Loupe Cloud
- Hosted dashboard for teams
- Frame-by-frame replay with circuit overlay
- Alerts when a circuit known to cause failures fires in prod
- $50/seat/month, free for OSS projects

## 3. Non-goals (saying no makes it shippable)
- ❌ Not a framework — we never tell you *how* to write your agent
- ❌ Not a chat-log viewer — Langfuse / Helicone already do that
- ❌ Not closed-source — the core + bench are MIT forever
- ❌ Not enterprise SSO / SOC2 in 2026 — focus on the 100-team midmarket

## 4. Open questions (decide before June)
- [ ] Self-host vs. hosted-only for Cloud MVP?
- [ ] Which SAE family to standardize on (Anthropic's? Apollo's? own training?)?
- [ ] Naming convention for circuits — borrow from neuronpedia, or invent?
- [ ] Pricing freemium tier — how many traces/month free?

## 5. Research artifact (the paper)

**Working title:** *LoupeBench: A Circuit-Level Benchmark for Agent Failure Modes*
**Target venue:** NeurIPS 2026 Datasets & Benchmarks Track
**Deadline:** ~Sep 2026 full track CFP

**Claims we want to defend:**
1. Agent failures cluster into N reproducible behavioral circuits.
2. Circuit-level attribution predicts future failures better than prompt-level heuristics.
3. The benchmark transfers across model families (Claude / Llama / Qwen).

## 6. Cold-email targets (in priority order)
1. Neel Nanda — DeepMind/Apollo, runs MATS
2. Chris Olah — Anthropic interp lead (Fellows funnel)
3. Jacob Andreas — MIT CSAIL LINGO
4. Dylan Hadfield-Menell — MIT Algorithmic Alignment
5. Dawn Song — Berkeley RDI
6. Percy Liang — Stanford CRFM

## 7. Stack

| Layer | Choice | Why |
|---|---|---|
| SDK (lang 1) | Python 3.11+ | required by SAELens / ML ecosystem |
| SDK (lang 2) | TypeScript / Node 22 | required by Vercel AI SDK |
| Local store | DuckDB | embedded, zero-deploy |
| Cloud store | Neon Postgres + ClickHouse | free tier, scales |
| SAE | SAELens + neuronpedia | de-facto standard |
| Dashboard | Next.js 16 + Tailwind + shadcn/ui | matches 2026 best practice |
| Hosting | Vercel (web) + Modal (SAE compute) | both have free tiers |
| CI | GitHub Actions | free for public repos |

## 8. The "magic moment" demo plan
A 60-second screencast that goes viral on Twitter:
1. Show a real LangGraph agent trying to fix a bug
2. Agent fails — deletes wrong file
3. Cut to Loupe dashboard
4. Scrub through the trace; circuit-attribution panel lights up at step 4
5. Loupe: "circuit `unguarded-delete` activated. This fires in 41% of all destructive failures."
6. Show suggested mitigation, apply it, agent succeeds
7. End card: `pip install loupe`

## 9. Today's task list
- [ ] Reserve `loupe.dev` (or `useloupe.com` if taken)
- [ ] Create `loupe-ai` GitHub org
- [ ] Push this repo public
- [ ] Pick 3 framework integrations to support first (probably LangGraph + OpenHands + Claude Agent SDK)
- [ ] Start a SCRATCH.md of agent failures we've personally seen — seed for the dataset
