"""`loupe init <name>` — scaffold a Loupe-instrumented agent starter.

Drops a runnable agent.py + README.md into the target directory. Zero
dependencies beyond `loupe` itself; user can extend with their preferred
framework.
"""

from __future__ import annotations

from pathlib import Path

AGENT_PY = '''"""A starter agent instrumented with Loupe.

After this script runs:
    1. View the captured trace:  `loupe ui` then open http://localhost:7860
    2. Tag the failing step inline
    3. Export the bundle:  `loupe export --out failures.jsonl`
"""

from __future__ import annotations

import asyncio
import random

from loupe import record_step, trace


@trace(framework="starter", name="{name}")
async def my_agent(query: str) -> str:
    record_step("thought", "plan", outputs={{"plan": f"answer: {{query}}"}})

    record_step("tool-call", "read_input", inputs={{"q": query}})
    await asyncio.sleep(0.02)

    # Pretend to call an LLM; replace with your favourite SDK.
    record_step(
        "llm-call",
        "fake-claude",
        inputs={{"prompt": query}},
        outputs={{"text": f"echo({{query}})", "tokens": len(query)}},
    )

    # Inject a small chance of failure so you have something to tag.
    if random.random() < 0.4:
        record_step("error", "off-task", error="agent returned wrong topic")
        raise RuntimeError("starter agent purposely failed for the demo")

    return f"answered: {{query}}"


async def main() -> None:
    try:
        result = await my_agent("what is loupe?")
        print("done:", result)
    except RuntimeError as exc:
        print(f"caught (expected sometimes): {{exc}}")
    print("\\nrun: loupe ui   # then open http://localhost:7860")


if __name__ == "__main__":
    asyncio.run(main())
'''

README_MD = """# {name}

A starter agent project instrumented with [Loupe](https://loupe.dev).

## Setup

```bash
pip install 'loupe[ui]'
```

## Run

```bash
python agent.py
loupe ui                  # open http://localhost:7860 to inspect the trace
```

## Tag failures, build LoupeBench

In the dashboard, click any failing step and choose **tag this failure**.
Then export every annotated failure as a publishable JSONL bundle:

```bash
loupe export --out my-failures.jsonl
```

## What to do next

- Swap the `fake-claude` step in `agent.py` for a real call (`anthropic`,
  `openai`, LangGraph, Vercel AI SDK — all are auto-instrumented after
  `loupe.integrations.<x>.patch()`).
- Run your agent many times and collect interesting failures.
- Contribute back: see <https://github.com/loupe-ai/loupe/blob/main/CONTRIBUTING.md>.
"""


def scaffold(target: Path, name: str) -> list[Path]:
    """Create the starter project at `target`. Returns list of written files."""
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    agent_path = target / "agent.py"
    agent_path.write_text(AGENT_PY.format(name=name), encoding="utf-8")
    written.append(agent_path)

    readme_path = target / "README.md"
    readme_path.write_text(README_MD.format(name=name), encoding="utf-8")
    written.append(readme_path)

    gitignore_path = target / ".gitignore"
    gitignore_path.write_text(
        "__pycache__/\n.venv/\n*.pyc\n.loupe/\n",
        encoding="utf-8",
    )
    written.append(gitignore_path)

    return written
