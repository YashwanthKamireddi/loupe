"""Loupe demo against gpt-researcher (27.5k stars on GitHub).

The secondary showcase: a pure-text deep-research agent that fans one
query into ~30 LLM calls (planner -> N sub-researchers -> writer) plus
Tavily search tool calls and the occasional 429 retry. Where the
browser-use demo is visual, this one shows the *reasoning chain* —
which is what Loupe's branching trace tree was built for.

Run it
------
1. Install (one-time):

       pip install loupe-ai gpt-researcher python-dotenv

2. Set keys:

       export OPENAI_API_KEY=sk-...
       export TAVILY_API_KEY=tvly-...    # free 1k searches/mo, no card

3. Turn on Loupe's zero-code capture and run:

       LOUPE_AUTOPATCH=1 python examples/gpt_researcher_demo.py

4. Inspect:

       loupe list
       loupe show <trace_id>      # see the planner -> sub-researchers fan-out
       loupe stats                # capture rate sparkline (v0.0.75+)
       loupe cost                 # spend breakdown
       loupe watch                # live dashboard (v0.0.75+)

Why this is a good demo
-----------------------
- ~30 LLM calls per run -> dense trace tree to render in `loupe show`.
- Tavily free tier rate-limits at ~5 req/min -> reliably produces a
  429 retry chain inside one trace, the kind of failure Loupe is
  uniquely good at surfacing.
- LangChain underneath -> pure httpx -> autopatch is zero-config.
"""

from __future__ import annotations

import asyncio
import os
import sys

QUERY = "What killed the Concorde?"


def _check_keys() -> str | None:
    missing = [k for k in ("OPENAI_API_KEY", "TAVILY_API_KEY") if not os.environ.get(k)]
    if missing:
        return f"Missing env vars: {', '.join(missing)}. Set them and rerun."
    return None


def _warn_if_autopatch_off() -> None:
    if os.environ.get("LOUPE_AUTOPATCH", "0").lower() not in {"1", "true", "yes", "on"}:
        print(
            "warning: LOUPE_AUTOPATCH is not set — Loupe will NOT capture this run.\n"
            "  Re-run with:  LOUPE_AUTOPATCH=1 python examples/gpt_researcher_demo.py\n",
            file=sys.stderr,
        )


async def main() -> int:
    err = _check_keys()
    if err:
        print(err, file=sys.stderr)
        return 2

    _warn_if_autopatch_off()

    try:
        from gpt_researcher import GPTResearcher
    except ImportError:
        print(
            "gpt-researcher is not installed. Install with:\n"
            "    pip install gpt-researcher\n",
            file=sys.stderr,
        )
        return 2

    print(f"▶  query: {QUERY!r}\n")
    print("   (Loupe is silently capturing every LLM + Tavily call.)\n")

    researcher = GPTResearcher(query=QUERY, report_type="research_report")
    await researcher.conduct_research()
    report = await researcher.write_report()

    print("\n────── report excerpt ──────")
    print(report[:500] + ("..." if len(report) > 500 else ""))
    print("\n──────────────────────────────────────────")
    print("done. inspect the capture with:")
    print("    loupe list")
    print("    loupe show <trace_id>")
    print("    loupe stats        # branching trace count + sparkline (v0.0.75+)")
    print("    loupe watch        # live dashboard (v0.0.75+)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
