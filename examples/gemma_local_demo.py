"""Loupe + local Gemma-2-2b (via Ollama) — REAL SAE attribution.

This is the demo where the SAE features Loupe attributes are the
SAME features the model actually used. Not a proxy through a smaller
open model — the actual model the agent ran against has a published
SAE (Joseph Bloom / GemmaScope) that Loupe can encode the prompt
through directly.

Prereqs (one-time)
------------------
    ~/.local/bin/ollama serve &              # daemon, background
    ~/.local/bin/ollama pull gemma2:2b       # ~1.6 GB

Run
---
    LOUPE_AUTOPATCH=1 python examples/gemma_local_demo.py

Then
    loupe list
    loupe show <trace-id>
    loupe attribute <trace-id> --backend sae --sae gemma-2-2b --explain

    # ↑ those features ARE what fired inside Gemma-2-2b on this prompt.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

from openai import OpenAI

from loupe import record_step, trace

OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
MODEL = "gemma2:2b"


TASK = (
    "What is 17 multiplied by 23, plus the number of letters in the phrase "
    "'mechanistic interpretability'? Show your work step by step."
)

SYSTEM = """You are a careful agent. Use the two tools available to you:

  - calc(expr)             evaluates a Python arithmetic expression
  - count_letters(s)       returns the number of letters in s

Format every response as:
  THOUGHT: <one sentence>
  ACTION:  <tool_name>(<argument>)

OR when you have the answer:
  THOUGHT: <one sentence>
  FINAL:   <the answer>

You MUST use the tools — never compute in your head.
"""


def tool_calc(expr: str) -> str:
    if not re.fullmatch(r"[\d\s+\-*/().]+", expr):
        return f"error: refusing to eval {expr!r}"
    try:
        return str(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


def tool_count_letters(s: str) -> str:
    return str(sum(1 for ch in s if ch.isalpha()))


TOOLS: dict[str, Any] = {"calc": tool_calc, "count_letters": tool_count_letters}


@trace(framework="gemma-local")
def run_agent(task: str, *, max_steps: int = 8) -> str:
    client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")  # any string works
    record_step("thought", "plan", inputs={"task": task, "model": MODEL})

    transcript: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": task},
    ]

    for _step in range(1, max_steps + 1):
        resp = client.chat.completions.create(
            model=MODEL,
            messages=transcript,
            max_tokens=300,
            temperature=0.2,
        )
        reply = (resp.choices[0].message.content or "").strip()
        transcript.append({"role": "assistant", "content": reply})

        if final_match := re.search(r"FINAL:\s*(.+)", reply, re.IGNORECASE | re.DOTALL):
            answer = final_match.group(1).strip()
            record_step("thought", "done", outputs={"answer": answer})
            return answer

        action_match = re.search(
            r"ACTION:\s*(\w+)\s*\(\s*(?:'([^']*)'|\"([^\"]*)\"|([^)]*))\s*\)",
            reply,
            re.IGNORECASE,
        )
        if not action_match:
            transcript.append({
                "role": "user",
                "content": "I couldn't parse an ACTION or FINAL line. Try again.",
            })
            continue

        tool_name = action_match.group(1).lower()
        arg = next((g for g in action_match.groups()[1:] if g is not None), "").strip()
        tool = TOOLS.get(tool_name)
        obs = tool(arg) if tool else f"error: unknown tool {tool_name!r}"
        record_step("tool-call", tool_name, inputs={"arg": arg}, outputs={"observation": obs})
        transcript.append({"role": "user", "content": f"OBSERVATION: {obs}"})

    record_step("thought", "out-of-steps", outputs={"hit_limit": max_steps})
    return "(agent ran out of steps)"


def main() -> int:
    if os.environ.get("LOUPE_AUTOPATCH", "0").lower() not in {"1", "true", "yes", "on"}:
        print(
            "warning: LOUPE_AUTOPATCH is not set — this run will NOT be captured.\n"
            "  Re-run with:  LOUPE_AUTOPATCH=1 python examples/gemma_local_demo.py\n",
            file=sys.stderr,
        )

    print(f"▶  task: {TASK}\n")
    print(f"   (model: {MODEL} via Ollama at {OLLAMA_BASE_URL} — Loupe captures via httpx)\n")
    try:
        answer = run_agent(TASK)
    except Exception as exc:  # noqa: BLE001
        if "Connection refused" in str(exc) or "ConnectError" in type(exc).__name__:
            print(
                "Could not reach Ollama at http://127.0.0.1:11434.\n"
                "Start it with:\n"
                "    ~/.local/bin/ollama serve &\n"
                "Then make sure gemma2:2b is pulled:\n"
                "    ~/.local/bin/ollama pull gemma2:2b\n",
                file=sys.stderr,
            )
            return 2
        raise

    print("\n──────────────────────────────────────────")
    print(f"answer: {answer}")
    print("──────────────────────────────────────────")
    print("\nnext — attribute the trace with REAL Gemma-2-2b SAE features:")
    print("    loupe list")
    print("    loupe attribute <trace-id> --backend sae --sae gemma-2-2b --explain")
    print("\nthese features ARE what fired inside the model that ran your agent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
