"""`loupe init <name>` — scaffold a Loupe-instrumented agent starter.

Drops a runnable agent.py + README.md into the target directory. The agent
calls Gemini's free tier so a user can run it the moment they have a key,
with no fakes anywhere — every captured Step represents a real LLM call.
"""

from __future__ import annotations

from pathlib import Path

AGENT_PY = '''"""{name} — a Loupe-instrumented agent that calls a real LLM.

This script:
  1. Calls Gemini (free tier) to answer a question.
  2. Wraps the call in @trace so Loupe records the full agent run.
  3. Turns on patch_all() so the underlying HTTP request is captured too.

Usage:
    export GEMINI_API_KEY=your_key       # bash/zsh
    set -Ux GEMINI_API_KEY your_key      # fish (persists across sessions)
    python agent.py "your question here"

After it runs, open the dashboard:
    loupe ui    # then http://localhost:7860
"""

from __future__ import annotations

import os
import sys

from loupe import record_step, trace
from loupe.integrations import patch_all


MODEL = "gemini-2.5-flash"


@trace(framework="gemini", name="{name}")
def answer(question: str) -> str:
    record_step("plan", "compose prompt", outputs={{"q": question[:200]}})

    from google import genai
    client = genai.Client()   # reads GEMINI_API_KEY from your environment

    response = client.models.generate_content(model=MODEL, contents=question)
    text = response.text or "(no text returned)"

    usage = getattr(response, "usage_metadata", None)
    tokens = {{
        "input":  getattr(usage, "prompt_token_count",     None),
        "output": getattr(usage, "candidates_token_count", None),
    }} if usage else {{}}

    record_step("final", "got reply", outputs={{"text": text[:300], **tokens}})
    return text


def main() -> int:
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        print(
            "GEMINI_API_KEY is not set.\\n"
            "  Get a free key at https://aistudio.google.com/apikey,\\n"
            "  then in this shell run:\\n"
            "      set -Ux GEMINI_API_KEY YOUR_KEY     (fish)\\n"
            "      export GEMINI_API_KEY=YOUR_KEY     (bash/zsh)\\n"
            "  Then re-run: python agent.py",
            file=sys.stderr,
        )
        return 1

    patch_all()
    question = " ".join(sys.argv[1:]) or "What is AI agent observability in one sentence?"

    print(f"asking gemini:  {{question}}\\n")
    try:
        text = answer(question)
        print(f"answer:\\n  {{text}}\\n")
        print("trace captured — open  loupe ui  to inspect it")
        return 0
    except Exception as exc:  # noqa: BLE001 — show any API error verbatim
        print(f"gemini API error: {{exc}}", file=sys.stderr)
        print("\\n(the failure was still captured — open loupe ui to see it.)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
'''

README_MD = """# {name}

A Loupe-instrumented agent that calls a real LLM (Gemini's free tier).

## Setup (one time)

```fish
# 1. Get a free Gemini key:  https://aistudio.google.com/apikey
# 2. Set it permanently in your shell:
set -Ux GEMINI_API_KEY YOUR_KEY            # fish
# (or)  export GEMINI_API_KEY=YOUR_KEY     # bash/zsh

# 3. Install Loupe + the Gemini SDK + the dashboard:
pip install 'loupe[ui]' google-genai
```

## Run

```fish
python agent.py "what is the capital of France?"
```

Then in another terminal:

```fish
loupe ui    # opens dashboard at http://localhost:7860
```

Every run becomes a trace. Click any trace in the sidebar to see the
prompt, the model's response, the token counts, the underlying HTTP
call, and timings. Click **Tag for LoupeBench** on a failing step to
start a benchmark dataset.

## What's actually happening

- `@trace` wraps `answer()` so Loupe knows this is one agent run.
- `record_step()` adds your own custom checkpoints (plan, final).
- `patch_all()` monkey-patches every installed LLM SDK so their calls
  are captured automatically — your business logic stays uncluttered.
- The captured JSONL lives at `~/.loupe/traces/{{id}}.jsonl`.

## Where to go from here

- Swap `gemini` for `anthropic` / `openai` — `patch_all()` picks up
  whichever SDK is installed; no other change to your code is needed.
- Wire in more `record_step` calls at decision points in your agent so
  the timeline tells the whole story.
- After you've collected interesting failures, run `loupe export` to
  produce a publishable JSONL benchmark of agent regressions.
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
