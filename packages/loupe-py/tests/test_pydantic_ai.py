"""Pydantic AI integration tests — verifies the wrapper without requiring
the real pydantic-ai package to be installed.

We plant a tiny stub `pydantic_ai` module that mimics the surface area the
real package exposes (Agent class with run / run_sync), patch over it, and
exercise it end-to-end.
"""

from __future__ import annotations

import asyncio
import importlib
import json as _json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from loupe import trace
from loupe.store import JSONLStore


@pytest.fixture()
def store(tmp_path: Path) -> JSONLStore:
    return JSONLStore(root=tmp_path)


def _install_fake_pydantic_ai() -> None:
    """Plant a fake `pydantic_ai` module with the surface area we patch."""

    class _FakeResult:
        def __init__(self, text: str) -> None:
            self.output = text
            self.usage = SimpleNamespace(input_tokens=12, output_tokens=8)

    class Agent:
        def __init__(self, model: str, system_prompt: str = "") -> None:
            self.model = model
            self.system_prompt = system_prompt

        def run_sync(self, user_prompt: str, **kwargs: Any) -> _FakeResult:
            return _FakeResult(f"sync-reply:{user_prompt}")

        async def run(self, user_prompt: str, **kwargs: Any) -> _FakeResult:
            return _FakeResult(f"async-reply:{user_prompt}")

    pkg = ModuleType("pydantic_ai")
    pkg.Agent = Agent
    sys.modules["pydantic_ai"] = pkg


def _read_steps(store: JSONLStore) -> list[dict]:
    files = list(store.root.glob("*.jsonl"))
    assert len(files) == 1
    return [
        _json.loads(line)
        for line in files[0].read_text().splitlines()
        if _json.loads(line)["_type"] == "step"
    ]


def test_pydantic_ai_sync_capture(store: JSONLStore) -> None:
    _install_fake_pydantic_ai()
    sys.modules.pop("loupe.integrations.pydantic_ai", None)
    mod = importlib.import_module("loupe.integrations.pydantic_ai")
    assert mod.patch() is True
    assert mod.patch() is False  # idempotent

    from pydantic_ai import Agent  # type: ignore[import-not-found]

    @trace(framework="pydantic-ai-test", store=store)
    def run() -> str:
        agent = Agent("anthropic:claude-haiku-4-5", system_prompt="Be concise.")
        r = agent.run_sync("hi")
        return r.output

    out = run()
    assert out == "sync-reply:hi"

    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["kind"] == "llm-call"
    assert steps[0]["name"] == "pydantic-ai:anthropic:claude-haiku-4-5"
    assert steps[0]["inputs"]["prompt"] == "hi"
    assert steps[0]["inputs"]["system"] == "Be concise."
    assert steps[0]["outputs"]["text"] == "sync-reply:hi"
    assert steps[0]["outputs"]["input_tokens"] == 12
    assert steps[0]["outputs"]["output_tokens"] == 8


def test_pydantic_ai_async_capture(store: JSONLStore) -> None:
    _install_fake_pydantic_ai()
    sys.modules.pop("loupe.integrations.pydantic_ai", None)
    mod = importlib.import_module("loupe.integrations.pydantic_ai")
    mod.patch()

    from pydantic_ai import Agent  # type: ignore[import-not-found]

    @trace(framework="pydantic-ai-test", store=store)
    async def run() -> str:
        agent = Agent("openai:gpt-4o-mini")
        r = await agent.run("hello")
        return r.output

    out = asyncio.run(run())
    assert out == "async-reply:hello"

    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["name"] == "pydantic-ai:openai:gpt-4o-mini"
    assert steps[0]["outputs"]["text"] == "async-reply:hello"


def test_pydantic_ai_redacts_credentials_in_prompt(store: JSONLStore) -> None:
    """Credentials embedded in the prompt should be scrubbed before storage."""
    _install_fake_pydantic_ai()
    sys.modules.pop("loupe.integrations.pydantic_ai", None)
    mod = importlib.import_module("loupe.integrations.pydantic_ai")
    mod.patch()

    from pydantic_ai import Agent  # type: ignore[import-not-found]

    @trace(framework="pydantic-ai-test", store=store)
    def run() -> str:
        agent = Agent("openai:gpt-4o-mini")
        return agent.run_sync(
            "Hey, use sk-ant-abcdefghij1234567890abcdef to fetch the data."
        ).output

    run()
    steps = _read_steps(store)
    assert "[redacted]" in steps[0]["inputs"]["prompt"]
    assert "sk-ant-abcdefghij1234567890" not in steps[0]["inputs"]["prompt"]


def test_pydantic_ai_captures_error(store: JSONLStore) -> None:
    _install_fake_pydantic_ai()
    sys.modules.pop("loupe.integrations.pydantic_ai", None)

    # Replace Agent.run_sync to raise
    import pydantic_ai  # type: ignore[import-not-found]

    def boom(self: Any, user_prompt: str, **kwargs: Any) -> None:
        raise RuntimeError("model overloaded")

    pydantic_ai.Agent.run_sync = boom  # type: ignore[method-assign]

    mod = importlib.import_module("loupe.integrations.pydantic_ai")
    mod.patch()

    from pydantic_ai import Agent  # type: ignore[import-not-found]

    @trace(framework="pydantic-ai-test", store=store)
    def run() -> str:
        Agent("openai:gpt-4o-mini").run_sync("hi")
        return "unreachable"

    with pytest.raises(RuntimeError, match="model overloaded"):
        run()

    steps = _read_steps(store)
    assert len(steps) == 1
    assert "model overloaded" in (steps[0]["error"] or "")
