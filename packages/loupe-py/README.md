# `loupe` — Python SDK

Forensic observability + circuit attribution for Python LLM agents.

## Install

```bash
pip install -e '.[ui]'           # from this repo (canonical install today)
pip install -e '.[interp]'       # adds the real SAE attribution backend
pip install -e '.[ui,interp,langgraph,anthropic,openai,universal]'
```

## Quickstart

```python
from loupe import trace, record_step
from loupe.integrations import patch_all

patch_all()                                  # auto-capture any installed LLM SDK

@trace(framework="anthropic")
async def my_agent(query: str):
    record_step("plan", "compose request")
    # ...your real agent code; LLM calls captured automatically
    return result

await my_agent("refactor auth.py")
# trace saved to ~/.loupe/traces/{run_id}.jsonl
```

View traces locally:

```bash
loupe ui              # opens http://localhost:7860 — live SSE dashboard
loupe list            # terminal table of every run
loupe attribute <id>  # SAE circuit attribution per llm-call step
loupe cluster         # find features that recur across tagged failures
loupe replay <id>     # re-invoke a captured run for reproducibility testing
```

See [SPEC.md](../../docs/SPEC.md) for the wire format and
[ARCHITECTURE.md](../../docs/ARCHITECTURE.md) for the layering.
