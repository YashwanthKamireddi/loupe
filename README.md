<div align="center">

# Loupe

**A magnifying glass for your AI agent.**

Open-source forensics + interpretability for LLM agents. Record every step, replay any failure, and see *why* it went wrong — at the level of individual neural circuits.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-pre--alpha-orange.svg)](#)
[![GitHub stars](https://img.shields.io/github/stars/loupe-ai/loupe?style=social)](#)

</div>

---

## Why Loupe

AI agents fail. Constantly. Datadog's *State of AI Engineering 2026* reports that **5% of all LLM call spans fail silently in production**, and **43% of AI-generated code changes require manual debugging after passing QA**. Today, debugging an agent looks like this:

1. Agent does something wrong
2. You stare at a chat log
3. You guess what went wrong
4. You rewrite the prompt and hope

That's not engineering. That's superstition.

**Loupe makes the invisible visible.** Drop it into your agent, replay any failure, and see exactly which *internal reasoning circuit* fired when things went sideways.

## What's in the box

| Piece | What it is | Status |
|---|---|---|
| [`@loupe/py`](packages/loupe-py) | Python SDK — drop-in trace capture for LangGraph / OpenHands / Claude Agent SDK | 🚧 building |
| [`@loupe/ts`](packages/loupe-ts) | TypeScript SDK — same, for Vercel AI SDK / AI Agents JS frameworks | 🚧 building |
| [LoupeBench](bench) | Public benchmark — 1,000+ annotated agent failures with circuit-level causes | 🚧 building |
| [Loupe Cloud](apps/web) | Hosted dashboard — frame-by-frame replay with SAE-feature attribution | 🚧 building |

## The magic moment (60 seconds)

```python
from loupe import trace

@trace
async def my_agent(query: str):
    # your existing LangGraph / OpenHands / whatever agent
    return await graph.ainvoke({"query": query})
```

Your agent fails on a task. You open Loupe and see:

> **Step 4 → activated circuit `form-validation-loop` (SAE feature #8842)**
> This circuit fires in 73% of failed multi-step web tasks.
> Suggested mitigation: add a checkpoint prompt that breaks the loop.

That's it. That's the product.

## Roadmap

- [ ] **May 2026** — Project spec + 100 hand-annotated failures + 3 framework integrations chosen
- [ ] **June 2026** — `loupe` library v0.1, first SAE probe runs end-to-end
- [ ] **July 2026** — LoupeBench v0.1 (1,000 failures) + public Show HN launch
- [ ] **Aug 2026** — Loupe Cloud MVP open beta
- [ ] **Sep 2026** — arXiv preprint + NeurIPS 2026 Datasets & Benchmarks submission
- [ ] **Oct 2026** — 5 case-study posts + paid plan launch
- [ ] **Nov 2026** — 5k GitHub stars target

## Built on

[SAELens](https://github.com/jbloomAus/SAELens) · [neuronpedia](https://www.neuronpedia.org) · [OpenTelemetry](https://opentelemetry.io) · [LangGraph](https://www.langchain.com/langgraph) · [OpenHands](https://github.com/All-Hands-AI/OpenHands)

## License

MIT — see [LICENSE](LICENSE). Use it. Fork it. Ship it.

---

<div align="center">
<sub>Loupe is open research. If you're working on agent observability, mech interp, or eval — please open an issue and say hi.</sub>
</div>
