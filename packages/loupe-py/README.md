# `loupe` — Python SDK

Drop-in trace capture and circuit-attribution for Python LLM agents.

```bash
pip install loupe   # not yet published — coming June 2026
```

## Quickstart

```python
from loupe import trace

@trace(framework="langgraph")
async def my_agent(query: str):
    return await graph.ainvoke({"query": query})

result = await my_agent("refactor auth.py")
# trace saved to ~/.loupe/traces/{run_id}.jsonl
```

View traces locally:

```bash
loupe ui   # opens http://localhost:7860
```

## Status

🚧 Pre-alpha. Targeting first public release **June 2026**.

See [SPEC.md](../../docs/SPEC.md) for design and roadmap.
