# Use Loupe on your own agent — three real recipes

> The goal of this page: get you from "what is this?" to "my real agent is captured in the dashboard" in five minutes. No API key required for the first two recipes.

## Setup (once)

```bash
cd /home/yash/Projects/loupe/packages/loupe-py
source .venv/bin/activate

# install the bits you'll need
pip install -e '.[ui,langgraph,universal]'

# open the dashboard in your browser
loupe ui &
xdg-open http://127.0.0.1:7860      # macOS: open  · Linux: xdg-open  · Windows: start
```

Leave the dashboard tab open. Every recipe below makes traces appear there in real time.

---

## Recipe 1 — Capture a real multi-step agent (no API key)

Save this as `my_agent.py` and run it:

```python
from loupe import trace, record_step
import asyncio, random

@trace(framework="langgraph", name="research-agent")
async def research_agent(question: str) -> str:
    record_step("thought", "plan",
                outputs={"plan": f"1. search web 2. summarize 3. answer '{question}'"})

    record_step("tool-call", "web_search",
                inputs={"q": question},
                outputs={"results": ["loupe.dev", "github.com/loupe-ai/loupe"]})

    record_step("llm-call", "claude-sonnet-4-6",
                inputs={"prompt": f"Summarize sources for: {question}"},
                outputs={"text": "Loupe is forensic observability for AI agents.",
                         "input_tokens": 540, "output_tokens": 24})

    # simulate the kind of failure you actually want to catch
    if random.random() < 0.3:
        record_step("error", "off-topic",
                    error="LLM started discussing the band Loupe instead of the project")
        raise RuntimeError("agent went off-topic")

    return "answered"

asyncio.run(research_agent("What is Loupe?"))
```

```bash
python my_agent.py
```

Refresh the dashboard — the trace is there. Click any step to see its inputs/outputs. Click the failing step → **Tag this failure** → fill in the form → it's part of LoupeBench now.

That's the whole loop. Nothing else to learn.

---

## Recipe 2 — Hook into the universal capture (no API key)

If your agent uses a Python LLM SDK (anthropic, openai, mistralai, etc.) — even with a fake/local model — one line captures every API call:

```python
from loupe.integrations.httpx import patch
patch()        # ← captures any LLM call made via httpx

# now do whatever you'd normally do. Every call to api.anthropic.com,
# api.openai.com, api.groq.com, … is recorded automatically.
```

For 49 providers covered out of the box, run `loupe providers`.

---

## Recipe 3 — Use your real Claude / OpenAI key

```bash
pip install 'loupe[anthropic]'        # or loupe[openai]
export ANTHROPIC_API_KEY=sk-ant-...    # paste yours
```

```python
import anthropic
from loupe import trace
from loupe.integrations.anthropic import patch
patch()

@trace(framework="anthropic", name="my-real-agent")
async def real_agent(q: str) -> str:
    client = anthropic.AsyncAnthropic()
    msg = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": q}],
    )
    return msg.content[0].text

import asyncio
print(asyncio.run(real_agent("Explain mechanistic interpretability in two sentences.")))
```

Every call to Anthropic is captured. Token usage, stop reason, latency, full response — all in the dashboard. API key is auto-redacted before it ever hits disk.

---

## Recipe 4 — Any other language (Go, Rust, Ruby, curl)

The dashboard accepts a `POST /api/traces` from anything:

```bash
curl -X POST http://localhost:7860/api/traces \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-go-agent",
    "framework": "go",
    "steps": [
      {"kind": "thought",   "name": "plan"},
      {"kind": "llm-call",  "name": "anthropic:claude-haiku-4-5",
         "outputs": {"text": "hello", "input_tokens": 5, "output_tokens": 2}}
    ]
  }'
```

Full schema: [`docs/SPEC.md`](docs/SPEC.md). Validate programmatically against [`docs/loupe-trace.schema.json`](docs/loupe-trace.schema.json).

---

## When something goes wrong

```bash
loupe doctor          # diagnose your install (one screen of dots)
loupe list            # see your captured traces in the terminal
loupe report <id>     # produce a shareable markdown case-file
loupe export          # bundle all tagged failures into LoupeBench JSONL
```
