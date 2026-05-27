"""`loupe init <name>` — scaffold a Loupe-instrumented agent starter.

Drops a runnable agent script + README into the target directory. The
provider (Gemini / Anthropic / OpenAI) and the filename are both
configurable so the scaffold works for any user's stack, not just the
default Gemini-on-`agent.py` happy path.

Every generated agent is a real, runnable program — no fakes, no
placeholder calls. The moment the user has a key for the chosen
provider, `python <file>` produces a real trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Provider template registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderTemplate:
    """Everything the scaffold needs to write a working starter for one
    provider — the SDK import, the API call, the env-var name, the
    pip install hint, the human display name."""

    label: str              # canonical id: "gemini" / "anthropic" / "openai"
    display: str            # human label: "Google Gemini"
    env_var: str            # primary env-var the agent reads
    key_url: str            # browser destination for create-a-key
    install_pkg: str        # extra `pip install` token alongside loupe itself
    default_model: str
    # Body fragments inserted into AGENT_TEMPLATE. Each fragment must:
    #   - import its SDK lazily inside answer()
    #   - read its key from os.environ
    #   - call the SDK
    #   - assign `text` (the model's reply) and `tokens` (dict, may be empty)
    call_block: str


# The call_block strings are substituted as VALUES into AGENT_TEMPLATE.format().
# str.format() doesn't re-process substituted values, so braces here stay as-is
# in the final output — they must be the real Python (single) braces, not doubled.
_GEMINI_BLOCK = """\
    from google import genai
    client = genai.Client()   # reads GEMINI_API_KEY from your environment

    response = client.models.generate_content(model=MODEL, contents=question)
    text = response.text or "(no text returned)"

    usage = getattr(response, "usage_metadata", None)
    tokens = {
        "input":  getattr(usage, "prompt_token_count",     None),
        "output": getattr(usage, "candidates_token_count", None),
    } if usage else {}"""


_ANTHROPIC_BLOCK = """\
    import anthropic
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": question}],
    )
    blocks = response.content or []
    text = "".join(getattr(b, "text", "") for b in blocks) or "(no text returned)"

    usage = getattr(response, "usage", None)
    tokens = {
        "input":  getattr(usage, "input_tokens",  None),
        "output": getattr(usage, "output_tokens", None),
    } if usage else {}"""


_OPENAI_BLOCK = """\
    import openai
    client = openai.OpenAI()  # reads OPENAI_API_KEY

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": question}],
    )
    text = response.choices[0].message.content or "(no text returned)"

    usage = getattr(response, "usage", None)
    tokens = {
        "input":  getattr(usage, "prompt_tokens",     None),
        "output": getattr(usage, "completion_tokens", None),
    } if usage else {}"""


PROVIDERS: dict[str, ProviderTemplate] = {
    "gemini": ProviderTemplate(
        label="gemini",
        display="Google Gemini",
        env_var="GEMINI_API_KEY",
        key_url="https://aistudio.google.com/apikey",
        install_pkg="google-genai",
        default_model="gemini-2.5-flash",
        call_block=_GEMINI_BLOCK,
    ),
    "anthropic": ProviderTemplate(
        label="anthropic",
        display="Anthropic Claude",
        env_var="ANTHROPIC_API_KEY",
        key_url="https://console.anthropic.com/settings/keys",
        install_pkg="anthropic",
        default_model="claude-haiku-4-5-20251001",
        call_block=_ANTHROPIC_BLOCK,
    ),
    "openai": ProviderTemplate(
        label="openai",
        display="OpenAI",
        env_var="OPENAI_API_KEY",
        key_url="https://platform.openai.com/api-keys",
        install_pkg="openai",
        default_model="gpt-4o-mini",
        call_block=_OPENAI_BLOCK,
    ),
}


def _provider_for(label: str) -> ProviderTemplate:
    """Resolve a provider label (case-insensitive). Falls back to gemini."""
    key = (label or "gemini").lower().strip()
    if key not in PROVIDERS:
        known = ", ".join(PROVIDERS.keys())
        raise ValueError(
            f"unknown provider {label!r}; pick one of: {known}"
        )
    return PROVIDERS[key]


def validate_filename(filename: str) -> str:
    """Reject filenames that would escape the target dir or aren't .py."""
    if not filename.endswith(".py"):
        raise ValueError(f"filename must end in .py (got {filename!r})")
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise ValueError(
            f"filename must be a bare name like 'main.py', not a path "
            f"(got {filename!r})"
        )
    return filename


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

AGENT_TEMPLATE = '''"""{name} — a Loupe-instrumented agent that calls a real LLM.

This script:
  1. Calls {display} to answer a question.
  2. Wraps the call in @trace so Loupe records the full agent run.
  3. Turns on patch_all() so the underlying HTTP request is captured too.

Usage:
    export {env_var}=your_key       # bash/zsh
    set -Ux {env_var} your_key      # fish (persists across sessions)
    python {filename} "your question here"

After it runs, open the dashboard:
    loupe ui    # then http://localhost:7860
"""

from __future__ import annotations

import os
import sys

from loupe import record_step, trace
from loupe.integrations import patch_all


MODEL = "{model}"


@trace(framework="{provider}", name="{name}")
def answer(question: str) -> str:
    record_step("plan", "compose prompt", outputs={{"q": question[:200]}})

{call_block}

    record_step("final", "got reply", outputs={{"text": text[:300], **tokens}})
    return text


def main() -> int:
    if not os.environ.get("{env_var}"):
        print(
            "{env_var} is not set.\\n"
            "  Get a key at {key_url},\\n"
            "  then in this shell run:\\n"
            "      set -Ux {env_var} YOUR_KEY     (fish)\\n"
            "      export {env_var}=YOUR_KEY     (bash/zsh)\\n"
            "  Then re-run: python {filename}",
            file=sys.stderr,
        )
        return 1

    patch_all()
    question = " ".join(sys.argv[1:]) or "What is AI agent observability in one sentence?"

    print(f"asking {display}:  {{question}}\\n")
    try:
        text = answer(question)
        print(f"answer:\\n  {{text}}\\n")
        print("trace captured — open  loupe ui  to inspect it")
        return 0
    except Exception as exc:  # noqa: BLE001 — show any API error verbatim
        print(f"{display} API error: {{exc}}", file=sys.stderr)
        print("\\n(the failure was still captured — open loupe ui to see it.)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


README_TEMPLATE = """# {name}

A Loupe-instrumented agent that calls a real LLM ({display}).

## Setup (one time)

```bash
# 1. Get a key:  {key_url}
# 2. Set it in your shell:
export {env_var}=YOUR_KEY                 # bash/zsh
set -Ux {env_var} YOUR_KEY                # fish (persists)
$env:{env_var}='YOUR_KEY'                 # PowerShell

# 3. Install Loupe + the {display} SDK:
pip install loupe-ai {install_pkg}
```

## Run

```bash
python {filename} "what is the capital of France?"
```

Then in another terminal:

```bash
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

- Swap `{provider}` for another provider — `patch_all()` picks up
  whichever SDK is installed; no other change to your code is needed.
- Wire in more `record_step` calls at decision points in your agent so
  the timeline tells the whole story.
- After you've collected interesting failures, run `loupe export` to
  produce a publishable JSONL benchmark of agent regressions.
"""


# ---------------------------------------------------------------------------
# Public scaffold function
# ---------------------------------------------------------------------------


def scaffold(
    target: Path,
    name: str,
    *,
    filename: str = "agent.py",
    provider: str = "gemini",
) -> list[Path]:
    """Create the starter project at ``target``.

    Args:
        target: directory to write into. Created if missing.
        name: human-readable project name; used in headers, decorators.
        filename: name of the entry script (must end in .py). Default agent.py.
        provider: one of gemini | anthropic | openai. Default gemini.

    Returns:
        List of files written.

    Raises:
        ValueError if the filename or provider is invalid.
    """
    filename = validate_filename(filename)
    tmpl = _provider_for(provider)

    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    agent_path = target / filename
    agent_path.write_text(
        AGENT_TEMPLATE.format(
            name=name,
            display=tmpl.display,
            env_var=tmpl.env_var,
            key_url=tmpl.key_url,
            filename=filename,
            model=tmpl.default_model,
            provider=tmpl.label,
            call_block=tmpl.call_block,
        ),
        encoding="utf-8",
    )
    written.append(agent_path)

    readme_path = target / "README.md"
    readme_path.write_text(
        README_TEMPLATE.format(
            name=name,
            display=tmpl.display,
            env_var=tmpl.env_var,
            key_url=tmpl.key_url,
            install_pkg=tmpl.install_pkg,
            filename=filename,
            provider=tmpl.label,
        ),
        encoding="utf-8",
    )
    written.append(readme_path)

    gitignore_path = target / ".gitignore"
    gitignore_path.write_text(
        "__pycache__/\n.venv/\n*.pyc\n.loupe/\n",
        encoding="utf-8",
    )
    written.append(gitignore_path)

    return written
