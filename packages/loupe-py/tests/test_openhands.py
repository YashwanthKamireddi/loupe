"""OpenHands integration smoke tests against a planted fake."""

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


def _install_fake_openhands(*, async_step: bool) -> None:
    """Plant openhands.controller.agent.Agent."""

    class _CmdRunAction:
        def __init__(self, command: str, thought: str) -> None:
            self.command = command
            self.thought = thought

    if async_step:
        class Agent:
            name = "CodeActAgent"

            async def step(self, state: Any) -> Any:
                return _CmdRunAction(
                    command="ls /tmp",
                    thought=f"Listing files (iter={state.iteration})",
                )
    else:
        class Agent:  # type: ignore[no-redef]
            name = "CodeActAgent"

            def step(self, state: Any) -> Any:
                return _CmdRunAction(
                    command="ls /tmp",
                    thought=f"Listing files (iter={state.iteration})",
                )

    pkg = ModuleType("openhands")
    controller = ModuleType("openhands.controller")
    agent_mod = ModuleType("openhands.controller.agent")
    agent_mod.Agent = Agent
    controller.agent = agent_mod
    pkg.controller = controller
    sys.modules["openhands"] = pkg
    sys.modules["openhands.controller"] = controller
    sys.modules["openhands.controller.agent"] = agent_mod


def _read_steps(store: JSONLStore) -> list[dict]:
    files = list(store.root.glob("*.jsonl"))
    assert len(files) == 1
    return [
        _json.loads(line)
        for line in files[0].read_text().splitlines()
        if _json.loads(line)["_type"] == "step"
    ]


def test_openhands_sync_capture(store: JSONLStore) -> None:
    _install_fake_openhands(async_step=False)
    sys.modules.pop("loupe.integrations.openhands", None)
    mod = importlib.import_module("loupe.integrations.openhands")
    assert mod.patch() is True
    assert mod.patch() is False

    from openhands.controller.agent import Agent  # type: ignore[import-not-found]

    @trace(framework="openhands-test", store=store)
    def run() -> Any:
        return Agent().step(SimpleNamespace(iteration=3))

    out = run()
    assert out.command == "ls /tmp"

    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["kind"] == "thought"
    assert steps[0]["name"] == "openhands:CodeActAgent"
    assert steps[0]["inputs"]["agent"] == "CodeActAgent"
    assert steps[0]["inputs"]["iteration"] == 3
    assert steps[0]["outputs"]["action"] == "_CmdRunAction"
    assert "iter=3" in steps[0]["outputs"]["thought"]
    assert steps[0]["outputs"]["args"]["command"] == "ls /tmp"


def test_openhands_async_capture(store: JSONLStore) -> None:
    _install_fake_openhands(async_step=True)
    sys.modules.pop("loupe.integrations.openhands", None)
    mod = importlib.import_module("loupe.integrations.openhands")
    mod.patch()

    from openhands.controller.agent import Agent  # type: ignore[import-not-found]

    @trace(framework="openhands-test", store=store)
    async def run() -> Any:
        return await Agent().step(SimpleNamespace(iteration=7))

    out = asyncio.run(run())
    assert out.command == "ls /tmp"

    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["inputs"]["iteration"] == 7


def test_openhands_redacts_credentials_in_action_args(store: JSONLStore) -> None:
    _install_fake_openhands(async_step=False)
    sys.modules.pop("loupe.integrations.openhands", None)

    # Replace step to return an Action whose command contains a secret
    import openhands.controller.agent as agent_mod  # type: ignore[import-not-found]

    class _ActionWithSecret:
        def __init__(self) -> None:
            self.command = "curl -H 'Authorization: Bearer sk-ant-abcdefghij1234567890abcdef' https://api.x"
            self.thought = "fetch with sk-ant-abcdefghij1234567890abcdef as the key"

    def step(self: Any, state: Any) -> Any:
        return _ActionWithSecret()

    agent_mod.Agent.step = step  # type: ignore[method-assign]

    mod = importlib.import_module("loupe.integrations.openhands")
    mod.patch()

    @trace(framework="openhands-test", store=store)
    def run() -> Any:
        return agent_mod.Agent().step(SimpleNamespace(iteration=1))

    run()
    steps = _read_steps(store)
    assert "[redacted]" in steps[0]["outputs"]["thought"]
    assert "[redacted]" in steps[0]["outputs"]["args"]["command"]
    assert "sk-ant-abcdefghij" not in steps[0]["outputs"]["thought"]
