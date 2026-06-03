"""Loupe demo against browser-use (96.9k stars on GitHub).

This is the headline showcase: a popular open-source web agent run
end-to-end with ZERO code changes — Loupe's universal httpx interceptor
captures every LLM call, every DOM observation, every action choice,
and every selector-not-found retry, all in the background.

Run it
------
1. Install (one-time):

       pip install loupe-ai browser-use
       playwright install chromium     # ~150 MB

2. Set ONE provider key (free Gemini tier works):

       export OPENAI_API_KEY=sk-...
       # or  export ANTHROPIC_API_KEY=sk-ant-...
       # or  export GEMINI_API_KEY=AIza...

3. Turn on Loupe's zero-code capture and run:

       LOUPE_AUTOPATCH=1 python examples/browser_use_demo.py

4. Inspect what was captured:

       loupe list                 # the trace just captured
       loupe show <trace_id>      # every LLM call + DOM + action
       loupe watch                # live dashboard (v0.0.75+)
       loupe ui                   # full forensic dashboard

Why this is a good demo
-----------------------
browser-use loops {screenshot + DOM -> LLM picks selector + action ->
Playwright executes -> verify}. One task fires 20-50 LLM calls.
Hallucinated selectors are common, so retry chains appear naturally —
exactly what Loupe is designed to surface.
"""

from __future__ import annotations

import asyncio
import os
import sys

TASK = (
    "Go to news.ycombinator.com, find the top 3 stories on the front "
    "page, and report each headline plus its current point count."
)


def _check_provider_key() -> str | None:
    """Return None if a provider key is set, else a friendly error message."""
    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        if os.environ.get(env):
            return None
    return (
        "No provider key found. Set ONE of OPENAI_API_KEY, "
        "ANTHROPIC_API_KEY, or GEMINI_API_KEY, then rerun."
    )


def _warn_if_autopatch_off() -> None:
    if os.environ.get("LOUPE_AUTOPATCH", "0").lower() not in {"1", "true", "yes", "on"}:
        print(
            "warning: LOUPE_AUTOPATCH is not set — Loupe will NOT capture this run.\n"
            "  Re-run with:  LOUPE_AUTOPATCH=1 python examples/browser_use_demo.py\n",
            file=sys.stderr,
        )


async def main() -> int:
    err = _check_provider_key()
    if err:
        print(err, file=sys.stderr)
        return 2

    _warn_if_autopatch_off()

    try:
        from browser_use import Agent
    except ImportError:
        print(
            "browser-use is not installed. Install with:\n"
            "    pip install browser-use\n"
            "    playwright install chromium\n",
            file=sys.stderr,
        )
        return 2

    print(f"▶  task: {TASK}\n")
    print("   (Loupe is silently capturing every LLM call.)\n")

    agent = Agent(task=TASK)
    await agent.run()

    print("\n──────────────────────────────────────────")
    print("done. inspect the capture with:")
    print("    loupe list")
    print("    loupe show <trace_id>")
    print("    loupe watch        # live dashboard (v0.0.75+)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
