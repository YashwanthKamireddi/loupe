# `loupe-ai` — Python SDK

Forensic observability + circuit attribution for Python LLM agents. Zero-code capture of every LLM call to append-only JSONL traces, with a local dashboard, TUI, and SAE-based interpretability on top.

## Install

```bash
pip install loupe-ai                  # CLI + dashboard + httpx capture, batteries included
pip install 'loupe-ai[interp]'        # adds the real SAE attribution backend (~150MB)
pip install 'loupe-ai[langgraph]'     # framework SDK extras: langgraph, anthropic, openai,
                                      # pydantic-ai, llama-index, dspy, crewai, autogen, openhands
```

From a clone of the repo: `pip install -e '.[dev]'`.

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

See [SPEC.md](https://github.com/YashwanthKamireddi/loupe/blob/main/docs/SPEC.md) for the wire format and
[ARCHITECTURE.md](https://github.com/YashwanthKamireddi/loupe/blob/main/docs/ARCHITECTURE.md) for the layering.
