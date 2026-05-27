<div align="center">

# Loupe

**A magnifying glass for your AI agent.**

Open-source forensics + interpretability for LLM agents. Record every step, replay any failure, and build the public benchmark of *why* agents go wrong.

[![CI](https://github.com/YashwanthKamireddi/loupe/actions/workflows/ci.yml/badge.svg)](https://github.com/YashwanthKamireddi/loupe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](#)
[![Node](https://img.shields.io/badge/node-20%20%7C%2022%20%7C%2024-brightgreen.svg)](#)
[![Tests](https://img.shields.io/badge/tests-546%20passing-success.svg)](#)

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
pip install loupe-ai
```

That single line gives you the CLI, the local dashboard (`loupe ui`), the
universal HTTP proxy (`loupe proxy`), zero-code autopatch capture for any
`httpx`-based LLM client, and the JSONL store. No extras to remember.

Pick one provider SDK to capture from (~10–20 MB each — you only need the
one you actually use):

```bash
pip install anthropic         # or pin in your own requirements
pip install openai
pip install google-genai
```

Or grab a framework integration when you need it:

```bash
pip install 'loupe-ai[langgraph]'     # LangChain / LangGraph callback handler
pip install 'loupe-ai[pydantic-ai]'   # Pydantic AI auto-instrumentation
pip install 'loupe-ai[llama-index]'   # LlamaIndex RAG capture
pip install 'loupe-ai[dspy]'          # DSPy module capture
pip install 'loupe-ai[crewai]'        # CrewAI multi-agent capture
pip install 'loupe-ai[autogen]'       # AutoGen ConversableAgent capture
pip install 'loupe-ai[openhands]'     # OpenHands coding agent capture
pip install 'loupe-ai[interp]'        # SAE attribution + steering (~150 MB; heavy)
```

> Loupe is currently in pre-alpha; the canonical install path is `pip install -e .` from this repo until v0.1.

## What you can do (every command)

```text
Get started
  loupe                                     Smart welcome — pitches Loupe + your next step
  loupe onboard                             Run Loupe on YOUR project: detect your agent,
                                            capture a real trace, open the dashboard
  loupe init <name> [--provider …] [--file] Scaffold a working starter (Gemini / Anthropic / OpenAI)
  loupe setup                               Configure / add / remove a provider key
  loupe status                              One-screen overview of your install

Use it (no code required)
  loupe ask "…"                             One captured LLM call, like ChatGPT in the terminal
  loupe chat                                Interactive REPL — every turn captured
  loupe run <cmd>                           Run ANY command (Python/Node/Go/curl) under capture
  loupe proxy [--provider …]                Universal HTTP capture — any agent, any language

Inspect captured runs
  loupe list                                Every captured trace (adapts to terminal width)
  loupe show <trace-id>                     Step-by-step: prompt → reply → tokens → errors
  loupe ui [--port 7860]                    Launch the local forensic dashboard (auto-opens browser)
  loupe annotations [<trace>]               Tags on one trace — or across every trace
  loupe diff <a> <b>                        Side-by-side step alignment of two traces
  loupe stats                               Counts, framework + failure histograms

Analyze + benchmark
  loupe tag <trace> <step> <category>       Mark a failing step for LoupeBench
  loupe untag <trace> <step>                Remove a tag
  loupe attribute <trace> [--backend sae]   Circuit attribution per llm-call step
  loupe cluster [--category …]              SAE features that recur across tagged failures
  loupe steer <trace> --feature N           Replay with one feature dampened / amplified
  loupe causal <trace> --corrupted … --answer …   Attribution patching (clean vs corrupted)
  loupe export [--format loupebench|otlp|parquet]  LoupeBench JSONL / OTLP / Parquet
  loupe report <trace> [--html]             Shareable markdown / single-file HTML case file
  loupe bench [<corpus>]                    Replay tagged failures against new code/models
  loupe cost                                LLM spend across every captured trace
  loupe replay <trace>                      Re-run a captured agent run

Infrastructure
  loupe doctor [--smoke] [--fix]            Diagnose + self-heal your install
  loupe verify <trace> | --all              Validate trace(s) against the canonical schema
  loupe purge --older-than 7d [--yes]       Delete old traces (dry-run unless --yes)
  loupe index info | rebuild                DuckDB query-index health / rebuild
  loupe config <get|set|list|path>          Edit ~/.loupe/config.toml without an editor
  loupe providers                           Every LLM provider host the proxy auto-detects
  loupe explain <topic>                     Built-in concept explainer (trace, autopatch, …)
  loupe version                             Print Loupe version
```

## 60-second quickstart

**Already have an agent project?** One command does everything — finds your
agent, captures a real run, opens the dashboard:

```bash
cd your-agent-project
loupe onboard
```

**Starting fresh?** Scaffold one and capture it:

```bash
# 1. Get a free Gemini key:  https://aistudio.google.com/apikey
export GEMINI_API_KEY=YOUR_KEY     # bash / zsh
# set -Ux GEMINI_API_KEY YOUR_KEY  # fish
# $env:GEMINI_API_KEY='YOUR_KEY'   # PowerShell

loupe init my-agent                # scaffold (also: --provider anthropic|openai)
cd my-agent
python agent.py "your question here"   # real call, real trace
loupe ui                           # dashboard auto-opens
```

The browser opens at `http://localhost:7860`. You'll see the live SSE
indicator pulsing green and your captured trace in the sidebar. Click it
to inspect every step — the prompt the model saw, the reply it gave, token
counts, the underlying HTTP call, and any error. Run the agent again —
new traces stream in without a refresh.

## Circuit attribution — see *which* features fired

Tagging a step as "hallucination" tells you **what** went wrong.
``loupe attribute --backend sae`` tells you **why** at the level of
mechanism: which SAE features in the model fired during this turn.

```fish
pip install 'loupe-ai[interp]'                           # torch + transformer-lens + sae-lens
loupe attribute <trace-id> --backend sae --explain    # ~7s first time, then ~200ms / step
```

Output:

```
  step d3a6a09 top features:
    # 23123  act=420.087  phrases related to legal documents and rulings
    #   979  act=401.952  phrases related to privatized prison industry…
    #   316  act=349.759  mentions of percentages or numerical values
    #  7496  act=329.776  phrases related to warning signs about alcohol
    # 23111  act=327.353  mentions of specific dates and events
```

Real GPT-2 small features from Joseph Bloom's layer-6 residual SAE,
explanations fetched from [Neuronpedia](https://neuronpedia.org).

Cluster across many tagged failures:

```fish
loupe cluster --category hallucination
loupe cluster --category loop --top-k 25
```

You get a frequency table (which features recur across failures of
this category) **and** a distinctiveness table (features
over-represented here vs every other category, scored by smoothed
log-ratio). That's the analytical primitive of the LoupeBench
research workflow.

> **Honest caveat:** closed frontier models (Claude, GPT-4) don't
> publish their SAEs. The workflow Loupe is built for: capture an
> agent run that uses a closed model, then attribute the *same
> prompt* through an open model that does. The features aren't
> literally what fired inside Claude — they're what an open model
> would use to produce a similar continuation. That correlation is
> what current mech-interp research relies on.

## Instrument your own agent — pick your stack

Loupe works with **any LLM agent in any framework**. Four ways in, ranked by zero-config-ness:

### Option 0 — `loupe proxy`: zero code, any language *(new in 0.0.55)*

Install Loupe. Run the proxy. Point your provider's base-URL env var at it.
Every LLM call from any client in any language is captured — Python, Node,
Go, Rust, even raw `curl`.

```bash
loupe proxy --provider anthropic --port 7878 &
export ANTHROPIC_BASE_URL=http://127.0.0.1:7878

python my_agent.py              # captured
node  my-agent.js               # captured
go run my-agent.go              # captured
curl  http://127.0.0.1:7878/v1/messages -d '{...}'   # captured
```

Streaming SSE is forwarded chunk-by-chunk — first-token latency is unchanged.
Run with no `--provider` to enable path-based auto-detection:
`/v1/messages` → Anthropic, `/v1/chat/...` → OpenAI, `/v1beta/models/...` → Gemini.

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

### TypeScript / Node

Same primitives, same wire format, same dashboard:

```typescript
import { trace, recordStep } from "loupe-ai";

const myAgent = trace({ framework: "vercel-ai-sdk" }, async (q: string) => {
  recordStep("thought", "plan");
  return await generateText({ model, prompt: q });
});
```

Plus universal `fetch` capture — same idea as the Python httpx patch but for the JS/TS ecosystem:

```typescript
import { patchFetch } from "loupe-ai/universal";
patchFetch();                       // once, anywhere, at startup

// now anything that uses global fetch is captured:
//   official anthropic / openai / mistral / @google/generative-ai / groq SDKs
//   Vercel AI SDK, instructor.js, any custom OpenAI-spec client
```

**Zero-code TypeScript** *(new in 0.0.56)* — same one-env-var pattern as Python:

```bash
export LOUPE_AUTOPATCH=1
export NODE_OPTIONS="--require loupe-ai/autopatch"
node my-agent.js          # captured automatically — no imports needed
```

### Any other language — Go, Rust, Ruby, Java, curl, anything

Loupe is **wire-format-first**. The dashboard accepts a `POST /api/traces` from any HTTP client. Run `loupe ui` and your Go/Rust/etc. agent just POSTs:

```bash
curl -X POST http://localhost:7860/api/traces \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-go-agent",
    "framework": "go-anthropic",
    "metadata": {"failed": false},
    "steps": [
      {"kind": "thought",   "name": "plan",          "outputs": {"plan": "..."}},
      {"kind": "llm-call",  "name": "anthropic:claude-haiku-4-5",
         "inputs":  {"prompt": "hi"},
         "outputs": {"text": "hello", "input_tokens": 5, "output_tokens": 2}},
      {"kind": "tool-call", "name": "search",        "inputs":  {"q": "loupe"}}
    ]
  }'
```

That's it — the trace shows up in the dashboard, SSE pushes it to anyone watching, and you can tag it for LoupeBench. The full schema (which fields are required, all the step `kind` values) lives in [`docs/SPEC.md`](docs/SPEC.md).

Both Python and TS write the **same JSONL** to `~/.loupe/traces/` — and the HTTP endpoint writes the same format — so `loupe ui` shows everything side-by-side regardless of which language captured it.

## Ship into your existing observability stack — OTLP export *(new in 0.0.56)*

Captured traces convert to OpenTelemetry OTLP JSON with the **GenAI Semantic Conventions** (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, ...). POST the file to any OTLP/HTTP collector:

```bash
loupe export --format otlp --out loupe.json
curl -X POST $COLLECTOR/v1/traces \
     -H 'content-type: application/json' \
     --data-binary @loupe.json
```

Works with Datadog APM, Honeycomb, Jaeger, Tempo, Grafana Cloud, New Relic, AWS X-Ray, or any self-hosted OTel collector.

## What's in the box

| Piece | What it is | Status |
|---|---|---|
| `loupe` Python SDK | `@trace` decorator + sync/async + JSONL store | ✅ |
| LangChain / LangGraph integration | `LoupeCallbackHandler` for any LangChain runnable | ✅ |
| Local web dashboard | FastAPI + SSE + forensic-dossier SPA — `loupe ui` | ✅ |
| Annotation layer + `loupe tag/export` | Turn captured failures into LoupeBench JSONL | ✅ |
| Anthropic / OpenAI / Google direct integration | Auto-traced via `patch_all()` + universal-httpx | ✅ |
| **TypeScript SDK (`loupe-ai`)** | **Same `trace()` API for Vercel AI SDK / Node — same wire format, same dashboard** | ✅ |
| **DuckDB index** | Sub-millisecond `loupe list / stats` at any scale | ✅ v0.0.38 |
| **Real SAE circuit attribution** | `loupe attribute --backend sae` — GPT-2 small + sae-lens forward pass | ✅ v0.0.41 |
| **Neuronpedia explanations** | `--explain` turns feature ids into readable concepts | ✅ v0.0.42 |
| **`loupe cluster`** | Find SAE features that recur across tagged failures | ✅ v0.0.40 |
| Loupe Cloud (hosted dashboard sync) | Out of scope for v0.x — Loupe's pitch is local-first traces. |

## Two languages, one dashboard

Python and TypeScript SDKs write the **identical JSONL wire format** to `~/.loupe/traces/`. The Python `loupe ui` dashboard reads both transparently — your Vercel AI SDK trace from a Next.js app and your LangGraph trace from a Python notebook appear side-by-side.

## The magic moment

Your agent fails. You open Loupe. You see:

```
Step 4   llm-call    anthropic:claude-sonnet-4-6        2,041 ms    FAILED
   prompt:    "Search the codebase for unguarded rm calls..."
   response:  (model returned a destructive bash command)
   error:     subprocess returned exit 1, agent looped
```

You click **Tag this failure** → category `unguarded-delete`,
notes "model invented a path it shouldn't have access to". Then:

```fish
loupe attribute --all --backend sae --explain
loupe cluster --category unguarded-delete
```

```
  distinctive features  (vs 47 other-category annotation(s))
   feature_id     in    out   score
       #11842      8     1    +2.07     mentions of file system paths
        #3221      7     2    +1.45     destructive verbs (delete, remove)
       #19044      5     0    +1.39     phrases related to operations without confirmation
```

Three features, real, mechanistic, reproducible. Cluster across 100
failures and you have a publishable circuit characterization.

The interpretability work today is staged with a Mock backend so you
can validate the pipeline (`loupe attribute --backend mock`); the
real SAE backend lands when you install `loupe[interp]`.

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
