# Loupe — public launch drafts

Three ready-to-paste posts for the v0.0.75 announcement. They share one
narrative: **zero-code instrumentation that already captures the most
popular open-source agents.**

Replace `<IMG:...>` placeholders with screenshots from
`docs/showcase/cli/` and `docs/showcase/dashboard/` after the Phase C
harvest. Replace `<DEMO_URL>` with the asciinema / vhs link once the
demo.tape renders.

---

## 1. Hacker News (Show HN)

**Title** (≤ 80 chars):

> Show HN: Loupe — forensic observability for AI agents (works zero-code on any)

**Body** (~300 words):

I shipped Loupe — open-source forensic observability + interpretability
for LLM agents. The pitch: **`pip install loupe-ai` and `LOUPE_AUTOPATCH=1`
in front of any Python agent, and every LLM call your code makes —
prompt, response, tokens, latency, errors, retries — gets captured to
local JSONL with zero code changes.**

It works because Loupe patches `httpx` (every modern LLM SDK rides on
it), not the SDKs themselves. So OpenAI, Anthropic, Gemini, Mistral,
Groq, LangChain, LangGraph, CrewAI, OpenHands — all captured uniformly.
No integration code per provider.

To prove it, v0.0.75 ships against two of the most-starred OSS agents:

- **browser-use** (96.9k ★) — captured a real browsing run, each LLM
  call's DOM observation + chosen action recorded side-by-side.
- **gpt-researcher** (27.5k ★) — captured the planner → sub-researcher
  → writer fan-out, retry chains and all. ~30 LLM calls per query.

What you get after one capture:

- `loupe watch` — live forensic dashboard in your terminal (Textual)
- `loupe ui` — full forensic dashboard in your browser (FastAPI)
- `loupe show <id>` — every step, prompt, reply, token count, latency
- `loupe cost` — actual USD spend per provider/model
- `loupe attribute <id>` — SAE-feature-level interpretability for *why*
  a step generated what it did (mock attributor in OSS; real one with
  the `[interp]` extra)
- `loupe bench` — a public benchmark of curated failures (LoupeBench)

It's MIT, local-first, no SaaS lock-in. Built solo over the last six
weeks; published to both PyPI and npm under `loupe-ai`. Provenance is
signed end-to-end via GitHub OIDC.

Try it: `pip install loupe-ai` → `LOUPE_AUTOPATCH=1 python examples/browser_use_demo.py`

Repo: https://github.com/YashwanthKamireddi/loupe

Would love your feedback — especially on what's missing for production
use.

---

## 2. X / Twitter thread (5 tweets)

**Tweet 1 — Hook**

> just shipped loupe-ai v0.0.75
>
> open-source forensic observability for AI agents that captures every
> LLM call, every retry, every selector hallucination — with ZERO code
> changes
>
> `pip install loupe-ai && LOUPE_AUTOPATCH=1 python your_agent.py`
>
> <IMG: loupe watch screenshot tailing browser-use>

**Tweet 2 — The trick**

> the trick: loupe patches httpx, not the SDKs
>
> every modern LLM client (openai, anthropic, gemini, langchain,
> litellm, crewai) rides on httpx
>
> so loupe captures all of them with the same code path. no integration
> per provider. no decorators required.

**Tweet 3 — Real-world demo**

> to prove it, v0.0.75 ships demos against the two most-starred OSS
> agents:
>
> • browser-use (96.9k ★)
> • gpt-researcher (27.5k ★)
>
> both run unmodified. loupe captures the entire trace tree —
> branching, retries, tool calls.
>
> <IMG: loupe show on gpt-researcher branching>

**Tweet 4 — The new CLI**

> v0.0.75 also rebuilt the CLI for max signal:
>
> • `loupe watch` — live in-terminal dashboard (Textual)
> • `loupe cost` — 14-day spend sparklines
> • `loupe setup` — arrow-key provider picker
> • first-run animated banner (one-shot, gated on TTY+!NO_COLOR)
>
> <IMG: loupe cost with sparkline>

**Tweet 5 — Call to action**

> it's MIT. local-first. signed-provenance npm + pypi releases.
>
> built for forensics + circuit-level interpretability (SAE attribution
> via the [interp] extra).
>
> repo: github.com/YashwanthKamireddi/loupe
> demo: <DEMO_URL>
>
> would love feedback — especially from people running real agents in
> production.

---

## 3. LinkedIn long-form (~400 words)

**Title**:

> I open-sourced Loupe — forensic observability for AI agents, zero
> code required

**Body**:

For the past six weeks I've been building Loupe, an open-source
forensics platform for LLM agents. Today I shipped v0.0.75 — and I want
to share what it does, how I built it, and why it matters.

**The problem.** Modern AI agents are a black box. When a LangGraph
workflow calls 50 LLM endpoints, hallucinates a selector, retries on a
rate-limit, then silently degrades — most teams have no way to see the
trace. Existing observability tools either require per-provider
integration code, only support one framework, or live on someone else's
SaaS.

**The Loupe approach.** Loupe captures every LLM call by patching
`httpx` — the HTTP library underneath every modern Python LLM SDK
(OpenAI, Anthropic, Google, Mistral, LangChain, CrewAI, OpenHands,
gpt-researcher, browser-use). One `LOUPE_AUTOPATCH=1` env var, no
decorators, no provider-specific integration code. Captures land in
local JSONL files under `~/.loupe/traces/` — no SaaS, no lock-in.

On top of that core: a full forensic dashboard (`loupe ui`, FastAPI in
your browser), a live terminal dashboard (`loupe watch`, new in v0.0.75
via Textual), USD cost tracking with 14-day sparklines, replay against
real APIs, and SAE-feature-level interpretability via the `[interp]`
extra (transformer-lens + sae-lens under the hood).

**Real-world proof.** v0.0.75 ships demo scripts that run two of the
most-starred OSS agents — browser-use (96.9k stars) and gpt-researcher
(27.5k stars) — and captures them end-to-end without touching their
code. The trace tree, retry chains, hallucinated selectors, and cost
per call are all there.

**The release pipeline.** Published to both PyPI and npm under the same
name (`loupe-ai`). Both releases carry signed sigstore provenance
attestations bound to the GitHub source repo — supply-chain verifiable
end to end. Cut a release by pushing a `vX.Y.Z` tag; GitHub Actions
gates on the full test suite, version-parity, and tag↔package match
before anything publishes.

**The roadmap.** Loupe Cloud (hosted dashboard sync) is next; SAE
attribution at scale (LoupeBench v0.1, 1k curated failures) follows.

Repo: https://github.com/YashwanthKamireddi/loupe
PyPI: https://pypi.org/project/loupe-ai/
npm:  https://www.npmjs.com/package/loupe-ai

If you're running agents in production and have feedback — what's
missing for you to drop this in tomorrow? — I'd love to hear it.

#opensource #aiagents #observability #python #typescript

---

## Posting checklist

- [ ] All screenshots replaced (delete `<IMG:...>` placeholders)
- [ ] Demo URL replaced (delete `<DEMO_URL>` placeholder)
- [ ] Tweet 1 image attached
- [ ] Tweet 3 image attached
- [ ] Tweet 4 image attached
- [ ] HN body trimmed if > 4000 chars
- [ ] LinkedIn formatted with line breaks (LinkedIn flattens multi-line
      paragraphs aggressively — use a single blank line between blocks)
