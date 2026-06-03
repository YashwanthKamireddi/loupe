"""Real ReAct-style agent captured by Loupe as one coherent trace.

This is what Loupe is for. Unlike ``multistep_capture_demo.py`` (which
produces 3 separate one-call traces under autopatch), this script uses
``@trace`` + ``record_step`` to produce a SINGLE trace tree with:

  * the parent reasoning frame
  * each LLM call as a sub-step
  * each tool call as a sub-step
  * the final answer

That tree is what looks beautiful in ``loupe show`` and in the
``loupe ui`` dashboard — branching, real prompts, real replies, real
tool I/O.

USAGE
-----
    GEMINI_API_KEY=AIza... python examples/react_agent_demo.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from openai import OpenAI

from loupe import record_step, trace

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
MODEL = "gemini-2.5-flash"

# The task the agent has to solve. Forces it to use both tools (it
# can't answer the second half without calculating length-of-string).
TASK = (
    "What is 17 multiplied by 23, plus the number of letters in the phrase "
    "'mechanistic interpretability'? Show your work."
)


SYSTEM = """You are a ReAct agent with two tools available:

  - calc(expr: str)         evaluates a Python arithmetic expression
  - count_letters(s: str)   returns the number of letters (a-z, A-Z) in s

Respond in EXACTLY this format (no markdown, no prose outside it):

  THOUGHT: <one sentence>
  ACTION:  <tool_name>(<argument>)

OR — when you have the final answer:

  THOUGHT: <one sentence>
  FINAL:   <the answer>
"""


# ---- the two tools ---------------------------------------------------------


def tool_calc(expr: str) -> str:
    """Evaluate a simple arithmetic expression — Python eval over digits + ops."""
    if not re.fullmatch(r"[\d\s+\-*/().]+", expr):
        return f"error: refusing to eval non-arithmetic input {expr!r}"
    try:
        return str(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307 — guarded above
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


def tool_count_letters(s: str) -> str:
    """Count the alpha characters in the input string."""
    return str(sum(1 for ch in s if ch.isalpha()))


TOOLS: dict[str, Any] = {
    "calc": tool_calc,
    "count_letters": tool_count_letters,
}


# ---- agent loop ------------------------------------------------------------


@trace(framework="react-demo")
def run_agent(task: str, *, max_steps: int = 6) -> str:
    """Drive a tiny ReAct loop and return the final answer.

    Each LLM call and each tool call lands as its own step under one
    parent trace — exactly what makes ``loupe show`` and ``loupe ui``
    informative on a real run.
    """
    client = OpenAI(
        api_key=os.environ["GEMINI_API_KEY"],
        base_url=GEMINI_OPENAI_BASE_URL,
    )

    record_step("thought", "plan", inputs={"task": task})

    transcript: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": task},
    ]

    for step in range(1, max_steps + 1):
        # Loupe's autopatch already records each LLM call as a step via
        # the openai direct integration — we don't need to record_step
        # it ourselves. We just call the SDK; capture is automatic.
        resp = client.chat.completions.create(
            model=MODEL,
            messages=transcript,
            max_tokens=400,
        )
        reply = (resp.choices[0].message.content or "").strip()
        transcript.append({"role": "assistant", "content": reply})

        # Did the model declare it's done?
        if final_match := re.search(r"FINAL:\s*(.+)", reply, re.IGNORECASE | re.DOTALL):
            answer = final_match.group(1).strip()
            record_step("thought", "done", outputs={"answer": answer})
            return answer

        # Otherwise, try to parse + execute one tool call.
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
        if tool is None:
            obs = f"error: unknown tool {tool_name!r}; try calc or count_letters"
        else:
            obs = tool(arg)
        record_step(
            "tool-call",
            tool_name,
            inputs={"arg": arg},
            outputs={"observation": obs},
        )

        transcript.append({
            "role": "user",
            "content": f"OBSERVATION: {obs}",
        })

    record_step("thought", "out-of-steps", outputs={"hit_limit": max_steps})
    return "(agent ran out of steps without producing a FINAL line)"


def main() -> int:
    if not os.environ.get("GEMINI_API_KEY"):
        print("Set GEMINI_API_KEY first (free at https://aistudio.google.com/apikey).",
              file=sys.stderr)
        return 2

    print(f"▶  task: {TASK}\n")
    answer = run_agent(TASK)
    print("\n──────────────────────────────────────────")
    print(f"answer: {answer}")
    print("──────────────────────────────────────────")
    print("\ninspect with:")
    print("    loupe list           # find the trace just created")
    print("    loupe show <id>      # see every step in the ReAct loop")
    print("    loupe watch          # live in-terminal dashboard")
    print("    loupe ui             # full browser dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
