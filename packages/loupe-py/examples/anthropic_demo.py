"""Trace a raw Anthropic SDK call — without LangChain.

Requires an Anthropic API key in ANTHROPIC_API_KEY. If you don't have one yet,
skip this example and use the LangGraph one (no key needed).

    cd packages/loupe-py
    pip install -e '.[anthropic]'
    export ANTHROPIC_API_KEY=sk-ant-...
    python examples/anthropic_demo.py
"""

from __future__ import annotations

import asyncio
import os

from loupe import trace
from loupe.integrations.anthropic import patch

patch()  # monkey-patch the Anthropic SDK once


@trace(framework="anthropic")
async def ask(question: str) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic()
    msg = await client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=128,
        messages=[{"role": "user", "content": question}],
    )
    return msg.content[0].text


async def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Skipping: set ANTHROPIC_API_KEY to run this example.")
        return
    text = await ask("Give me one sentence on mechanistic interpretability.")
    print(text)
    print("\nrun: loupe list")


if __name__ == "__main__":
    asyncio.run(main())
