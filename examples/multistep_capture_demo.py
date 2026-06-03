"""Real multi-step LLM capture validation — Gemini via OpenAI-compatible endpoint.

Why this demo exists
--------------------
browser-use + native google-genai SDK does NOT route through httpx, so Loupe
can't capture it via autopatch. This demo uses **the same Gemini model**
but through OpenAI's Python SDK pointing at Google's OpenAI-compatible
endpoint — which DOES use httpx, so Loupe captures every call zero-code.

This is the canonical "real LLM, real multi-step reasoning, real Loupe
capture" validation. Run it once to convince yourself Loupe works.

USAGE
-----
    LOUPE_AUTOPATCH=1 \\
    GEMINI_API_KEY=AIza... \\
    python examples/multistep_capture_demo.py

Then:
    loupe list
    loupe show <trace-id>
    loupe watch
"""

from __future__ import annotations

import os
import sys

from openai import OpenAI

# Google publishes an OpenAI-compatible chat-completions endpoint that
# accepts your standard Gemini API key. The OpenAI Python client uses
# httpx under the hood, so Loupe captures every call automatically.
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
MODEL = "gemini-2.5-flash"


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        print("Set GEMINI_API_KEY first (free tier at https://aistudio.google.com/apikey).",
              file=sys.stderr)
        return 2

    if os.environ.get("LOUPE_AUTOPATCH", "0").lower() not in {"1", "true", "yes", "on"}:
        print("warning: LOUPE_AUTOPATCH is not set — this run will NOT be captured.\n"
              "  Re-run with:  LOUPE_AUTOPATCH=1 python examples/multistep_capture_demo.py\n",
              file=sys.stderr)

    client = OpenAI(api_key=key, base_url=GEMINI_OPENAI_BASE_URL)

    # ---- multi-step reasoning: plan -> execute each subtask -> synthesize ----

    # Step 1: planner
    print("▶  step 1/3: planning...")
    plan = client.chat.completions.create(
        model=MODEL,
        messages=[{
            "role": "user",
            "content": (
                "You are a research planner. Given the question below, output "
                "EXACTLY three short sub-questions (one per line, no numbering) "
                "whose answers will let me write a final answer.\n\n"
                "Question: What is mechanistic interpretability and why does it matter?"
            ),
        }],
        max_tokens=200,
    )
    sub_questions = [
        line.strip() for line in plan.choices[0].message.content.splitlines()
        if line.strip()
    ][:3]
    print(f"   plan: {len(sub_questions)} sub-questions")

    # Step 2: answer each sub-question (one LLM call each)
    answers = []
    for i, q in enumerate(sub_questions, 1):
        print(f"▶  step 2.{i}/3: answering {q[:60]}...")
        ans = client.chat.completions.create(
            model=MODEL,
            messages=[{
                "role": "user",
                "content": f"Answer in 1-2 sentences: {q}",
            }],
            max_tokens=200,
        )
        answers.append((q, ans.choices[0].message.content))

    # Step 3: synthesize
    print("▶  step 3/3: synthesizing...")
    synthesis_prompt = "Combine these Q/A pairs into one concise paragraph:\n\n"
    for q, a in answers:
        synthesis_prompt += f"Q: {q}\nA: {a}\n\n"
    final = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": synthesis_prompt}],
        max_tokens=300,
    )

    print("\n──────────────────────────────────────────")
    print(final.choices[0].message.content)
    print("──────────────────────────────────────────")
    print(f"\ndone. {len(answers) + 2} LLM calls captured.")
    print("    loupe list                   # confirm the trace landed")
    print("    loupe show <trace-id>        # see each prompt + reply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
