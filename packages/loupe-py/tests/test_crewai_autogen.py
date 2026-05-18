"""Smoke tests for the CrewAI and AutoGen integrations."""

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


def _read_steps(store: JSONLStore) -> list[dict]:
    files = list(store.root.glob("*.jsonl"))
    assert len(files) == 1
    return [
        _json.loads(line)
        for line in files[0].read_text().splitlines()
        if _json.loads(line)["_type"] == "step"
    ]


# ---------------------------------------------------------------------------
# CrewAI
# ---------------------------------------------------------------------------


def _install_fake_crewai() -> None:
    class _FakeOutput:
        def __init__(self, text: str) -> None:
            self.raw = text
            self.token_usage = SimpleNamespace(total_tokens=123)

    class Crew:
        def __init__(self, agents: list, tasks: list) -> None:
            self.agents = agents
            self.tasks = tasks

        def kickoff(self, inputs: dict | None = None) -> _FakeOutput:
            return _FakeOutput(f"crew-output:{(inputs or {}).get('topic', 'none')}")

        async def kickoff_async(self, inputs: dict | None = None) -> _FakeOutput:
            return _FakeOutput(f"async-crew-output:{(inputs or {}).get('topic', 'none')}")

    pkg = ModuleType("crewai")
    pkg.Crew = Crew
    sys.modules["crewai"] = pkg


def test_crewai_sync_capture(store: JSONLStore) -> None:
    _install_fake_crewai()
    sys.modules.pop("loupe.integrations.crewai", None)
    mod = importlib.import_module("loupe.integrations.crewai")
    assert mod.patch() is True
    assert mod.patch() is False

    from crewai import Crew  # type: ignore[import-not-found]

    @trace(framework="crewai-test", store=store)
    def run() -> Any:
        return Crew(
            agents=["planner", "writer"],
            tasks=[
                SimpleNamespace(description="Research the topic"),
                SimpleNamespace(description="Write the report"),
            ],
        ).kickoff(inputs={"topic": "loupe"})

    out = run()
    assert out.raw == "crew-output:loupe"

    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["kind"] == "thought"
    assert steps[0]["name"] == "crewai:Crew(2 agents × 2 tasks)"
    assert steps[0]["inputs"]["agent_count"] == 2
    assert steps[0]["inputs"]["task_count"] == 2
    assert "Research the topic" in steps[0]["inputs"]["task_descriptions"]
    assert steps[0]["outputs"]["text"] == "crew-output:loupe"
    assert steps[0]["outputs"]["total_tokens"] == 123


def test_crewai_async_capture(store: JSONLStore) -> None:
    _install_fake_crewai()
    sys.modules.pop("loupe.integrations.crewai", None)
    mod = importlib.import_module("loupe.integrations.crewai")
    mod.patch()

    from crewai import Crew  # type: ignore[import-not-found]

    @trace(framework="crewai-test", store=store)
    async def run() -> Any:
        return await Crew(
            agents=["a"], tasks=[SimpleNamespace(description="t")]
        ).kickoff_async(inputs={"topic": "x"})

    out = asyncio.run(run())
    assert out.raw == "async-crew-output:x"


# ---------------------------------------------------------------------------
# AutoGen
# ---------------------------------------------------------------------------


def _install_fake_autogen() -> None:
    class ConversableAgent:
        def __init__(self, name: str) -> None:
            self.name = name

        def generate_reply(self, messages: list | None = None, **kwargs: Any) -> Any:
            last = (messages or [{"content": "?"}])[-1].get("content", "?")
            return {"content": f"reply:{last}", "role": "assistant"}

        async def a_generate_reply(self, messages: list | None = None, **kwargs: Any) -> Any:
            last = (messages or [{"content": "?"}])[-1].get("content", "?")
            return {"content": f"async-reply:{last}", "role": "assistant"}

    pkg = ModuleType("autogen")
    pkg.ConversableAgent = ConversableAgent
    sys.modules["autogen"] = pkg


def test_autogen_sync_capture(store: JSONLStore) -> None:
    _install_fake_autogen()
    sys.modules.pop("loupe.integrations.autogen", None)
    mod = importlib.import_module("loupe.integrations.autogen")
    assert mod.patch() is True
    assert mod.patch() is False

    from autogen import ConversableAgent  # type: ignore[import-not-found]

    @trace(framework="autogen-test", store=store)
    def run() -> Any:
        return ConversableAgent("planner").generate_reply(
            messages=[{"role": "user", "content": "hi"}]
        )

    out = run()
    assert out["content"] == "reply:hi"

    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["kind"] == "llm-call"
    assert steps[0]["name"] == "autogen:planner"
    assert steps[0]["outputs"]["text"] == "reply:hi"


def test_autogen_async_capture(store: JSONLStore) -> None:
    _install_fake_autogen()
    sys.modules.pop("loupe.integrations.autogen", None)
    mod = importlib.import_module("loupe.integrations.autogen")
    mod.patch()

    from autogen import ConversableAgent  # type: ignore[import-not-found]

    @trace(framework="autogen-test", store=store)
    async def run() -> Any:
        return await ConversableAgent("planner").a_generate_reply(
            messages=[{"role": "user", "content": "yo"}]
        )

    out = asyncio.run(run())
    assert out["content"] == "async-reply:yo"


def test_autogen_redacts_credentials_in_messages(store: JSONLStore) -> None:
    _install_fake_autogen()
    sys.modules.pop("loupe.integrations.autogen", None)
    mod = importlib.import_module("loupe.integrations.autogen")
    mod.patch()

    from autogen import ConversableAgent  # type: ignore[import-not-found]

    @trace(framework="autogen-test", store=store)
    def run() -> Any:
        return ConversableAgent("test").generate_reply(messages=[
            {"role": "user", "content": "Use sk-ant-abcdefghij1234567890abcdef please"}
        ])

    run()
    steps = _read_steps(store)
    assert "[redacted]" in steps[0]["inputs"]["messages"]
