"""The 60-second Loupe demo.

Run me:
    cd packages/loupe-py
    pip install -e .
    python examples/hello_loupe.py
    loupe list
    loupe show <trace_id>
"""

from __future__ import annotations

import asyncio

from loupe import trace
from loupe.trace import record_step


@trace(framework="demo")
async def fake_coding_agent(query: str) -> str:
    """Pretend we're Claude Code editing a file."""
    record_step("thought", "plan", outputs={"plan": "1. read file 2. propose diff 3. apply"})

    record_step("tool-call", "read_file", inputs={"path": "src/auth.py"})
    await asyncio.sleep(0.05)
    record_step("llm-call", "claude-sonnet", inputs={"prompt": query}, outputs={"tokens": 1240})

    record_step("tool-call", "write_file", inputs={"path": "src/auth.py", "diff_lines": 12})

    # Simulate the kind of failure LoupeBench cares about
    record_step(
        "error",
        "unguarded-delete",
        error="rm -rf src/ instead of src/auth_old.py",
        metadata={"severity": "critical"},
    )
    raise RuntimeError("agent deleted the wrong path")


async def main() -> None:
    try:
        await fake_coding_agent("refactor auth.py to use jose")
    except RuntimeError as exc:
        print(f"caught (expected): {exc}")
    print("trace written. run: loupe list")


if __name__ == "__main__":
    asyncio.run(main())
