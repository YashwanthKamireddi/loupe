"""Loupe — a magnifying glass for your AI agent.

Drop-in forensics + interpretability for LLM agents.

Quickstart:
    from loupe import trace

    @trace
    async def my_agent(query: str):
        return await some_llm_call(query)

Traces are written to ~/.loupe/traces/ by default. View them with `loupe ui`.
"""

from loupe._version import __version__
from loupe.store import Store
from loupe.trace import Step, Trace, trace

__all__ = ["__version__", "Step", "Store", "Trace", "trace"]
