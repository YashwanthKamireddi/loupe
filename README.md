<div align="center">

# Loupe

**A magnifying glass for your AI agent.**

Open-source forensics + interpretability for LLM agents. Record every step, replay any failure, and build the public benchmark of *why* agents go wrong.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-pre--alpha-orange.svg)](#)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](#)

</div>

---

## Why Loupe

AI agents fail. Constantly. Datadog's *State of AI Engineering 2026* reports that **5% of all LLM call spans fail silently in production**, and **43% of AI-generated code changes require manual debugging after passing QA**. Today, debugging an agent looks like this:

1. Agent does something wrong
2. You stare at a chat log
3. You guess what went wrong
4. You rewrite the prompt and hope

That's not engineering. That's superstition.

**Loupe makes the invisible visible.** Drop it into your agent, replay any failure, tag the root cause, and ship every annotated failure into a public benchmark that researchers and labs can compete on.

## Install (free for everyone)

```bash
pip install loupe                # core + CLI
pip install 'loupe[ui]'          # adds the local web dashboard
pip install 'loupe[langgraph]'   # adds LangChain / LangGraph integration
pip install 'loupe[anthropic]'   # adds Anthropic SDK auto-instrumentation
pip install 'loupe[openai]'      # adds OpenAI SDK auto-instrumentation
```

> Loupe is currently in pre-alpha; the canonical install path is `pip install -e .` from this repo until v0.1.

## 30-second quickstart

```bash
loupe start         # seeds sample traces + opens the dashboard
```

That's it. The browser pops up at `http://localhost:7860` with three real-looking traces, one of them already tagged as a `unguarded-delete` failure, so you can play with the UI immediately.

## Instrument your own agent — pick your stack

Loupe works with **any LLM agent in any framework**. Three ways in, ranked by zero-config-ness:

### Option 1 — Universal HTTP capture (any Python LLM client)

```python
from loupe import trace
from loupe.integrations.httpx import patch
patch()                                        # one line. anywhere. once.

@trace(framework="universal")
def my_agent(query: str):
    # use whatever client you want — mistral, groq, anthropic, openai,
    # google-genai, cohere, together, deepseek, perplexity, even local Ollama.
    return some_llm_client.generate(query)
```

Any HTTP call to a known LLM provider becomes a Step automatically.

### Option 2 — Direct SDK integration (zero-config)

```python
from loupe.integrations.anthropic import patch as patch_anthropic
from loupe.integrations.openai    import patch as patch_openai
patch_anthropic(); patch_openai()              # one line each.

import anthropic
client = anthropic.Anthropic()                 # already traced.
```

### Option 3 — Manual capture (works with literally anything)

```python
from loupe import trace, record_step

@trace(framework="dspy")        # any string — pydantic-ai, instructor, llamaindex, your own…
def my_agent(query: str):
    record_step("thought",   "plan",    outputs={"plan": "..."})
    record_step("tool-call", "search",  inputs={"q": query})
    record_step("llm-call",  "claude",  outputs={"text": "..."})
    return "..."
```

No framework integration needed — `@trace` + `record_step` covers 100% of cases.

### TypeScript / Node?

Same primitives, same wire format, same dashboard:

```typescript
import { trace, recordStep } from "@loupe/sdk";

const myAgent = trace({ framework: "vercel-ai-sdk" }, async (q: string) => {
  recordStep("thought", "plan");
  return await generateText({ model, prompt: q });
});
```

Both Python and TS write the **same JSONL** to `~/.loupe/traces/` — `loupe ui` shows them side-by-side.

## What's in the box

| Piece | What it is | Status |
|---|---|---|
| `loupe` Python SDK | `@trace` decorator + sync/async + JSONL store | ✅ v0.0.1 |
| LangChain / LangGraph integration | `LoupeCallbackHandler` for any LangChain runnable | ✅ v0.0.2 |
| Local web dashboard | FastAPI + forensic-dossier SPA — `loupe ui` | ✅ v0.0.3 |
| Annotation layer + `loupe tag/export` | Turn captured failures into LoupeBench JSONL | ✅ v0.0.4 |
| Anthropic + OpenAI direct integration | `patch()` once, all SDK calls auto-traced | ✅ v0.0.4 |
| **TypeScript SDK (`@loupe/sdk`)** | **Same `trace()` API for Vercel AI SDK / Node — same wire format, same dashboard** | ✅ v0.0.4 |
| SAE-based circuit attribution | Surface which neural circuits fired on failure | 🚧 v0.2 |
| Loupe Cloud (hosted) | Share traces with your team, dashboards online | 🚧 v0.2 |

## Two languages, one dashboard

Python and TypeScript SDKs write the **identical JSONL wire format** to `~/.loupe/traces/`. The Python `loupe ui` dashboard reads both transparently — your Vercel AI SDK trace from a Next.js app and your LangGraph trace from a Python notebook appear side-by-side.

## CLI

```text
loupe list                        list traces (newest first, with tag counts)
loupe show <trace-id>             print step-by-step content of one trace
loupe ui [--port 7860]            launch local dashboard at http://localhost:7860
loupe tag <trace> <step> <cat>    mark a step as a benchmark-worthy failure
loupe untag <trace> <step>        remove a tag
loupe annotations <trace>         list tags on one trace
loupe export [--out FILE]         bundle annotated failures into LoupeBench JSONL
loupe doctor                      diagnose your install
loupe version                     print Loupe version
```

## The magic moment

Your agent fails. You open Loupe. You see:

```
Step 4 → activated circuit `unguarded-delete`
This circuit fires in 41% of all destructive failures.
Suggested mitigation: add a path-prefix guard before any rm.
```

(Circuit-level attribution arrives in v0.2 with SAE integration. Today you'll see the failing step in the timeline, the error stack, inputs/outputs, and tag it for the benchmark.)

## LoupeBench — public dataset

Every tagged failure becomes a candidate for LoupeBench, a CC-BY-4.0 dataset of agent failure modes with reproducible traces. Schema lives in [`bench/README.md`](bench/README.md). Contribute via the [CONTRIBUTING guide](CONTRIBUTING.md).

## Built on

[SAELens](https://github.com/jbloomAus/SAELens) · [neuronpedia](https://www.neuronpedia.org) · [LangChain](https://github.com/langchain-ai/langchain) · [LangGraph](https://www.langchain.com/langgraph) · [FastAPI](https://fastapi.tiangolo.com) · [DuckDB](https://duckdb.org)

## License

MIT — see [LICENSE](LICENSE). Use it. Fork it. Ship it.

---

<div align="center">
<sub>Loupe is open research. If you work on agent observability, mech interp, or eval — please open an issue and say hi.</sub>
</div>
