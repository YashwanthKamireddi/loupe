"""LangChain / LangGraph integration smoke tests.

Exercise LoupeCallbackHandler against fabricated LangChain callback events.
Mirrors the real LangChain runtime contract (uuid run_ids, on_*_start /
on_*_end pairs, on_*_error sealing) without requiring a real LangChain
chain to be constructed.

LangChain itself (langchain-core) IS imported because the handler subclasses
BaseCallbackHandler — the test is skipped automatically if langchain-core
isn't installed.
"""

from __future__ import annotations

import json as _json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from loupe import trace
from loupe.store import JSONLStore

langchain_core = pytest.importorskip("langchain_core")


@pytest.fixture()
def store(tmp_path: Path) -> JSONLStore:
    return JSONLStore(root=tmp_path)


def _read_steps(store: JSONLStore) -> list[dict[str, Any]]:
    files = list(store.root.glob("*.jsonl"))
    assert len(files) == 1
    return [
        _json.loads(line)
        for line in files[0].read_text().splitlines()
        if _json.loads(line).get("_type") == "step"
    ]


def test_llm_call_pair_captured(store: JSONLStore) -> None:
    """An on_llm_start/on_llm_end pair lands as one llm-call step."""
    from langchain_core.outputs import Generation, LLMResult

    from loupe.integrations.langchain import LoupeCallbackHandler

    @trace(framework="langchain-test", store=store)
    def run() -> None:
        h = LoupeCallbackHandler()
        run_id = uuid.uuid4()
        h.on_llm_start(
            serialized={"name": "ChatAnthropic"},
            prompts=["hello"],
            run_id=run_id,
        )
        h.on_llm_end(
            response=LLMResult(generations=[[Generation(text="hi back")]]),
            run_id=run_id,
        )

    run()
    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["kind"] == "llm-call"
    assert steps[0]["name"] == "ChatAnthropic"
    assert steps[0]["inputs"]["prompts"] == ["hello"]
    assert steps[0]["outputs"]["text"] == "hi back"


def test_tool_call_pair_captured(store: JSONLStore) -> None:
    """on_tool_start/on_tool_end lands as one tool-call step."""
    from loupe.integrations.langchain import LoupeCallbackHandler

    @trace(framework="langchain-test", store=store)
    def run() -> None:
        h = LoupeCallbackHandler()
        run_id = uuid.uuid4()
        h.on_tool_start(
            serialized={"name": "search"},
            input_str="latest AI news",
            run_id=run_id,
        )
        h.on_tool_end(output="three results found", run_id=run_id)

    run()
    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["kind"] == "tool-call"
    assert steps[0]["name"] == "search"
    assert steps[0]["outputs"]["output"] == "three results found"


def test_chain_error_sealed_as_step_error(store: JSONLStore) -> None:
    """on_chain_error finalizes the open Step with the error payload."""
    from loupe.integrations.langchain import LoupeCallbackHandler

    @trace(framework="langchain-test", store=store)
    def run() -> None:
        h = LoupeCallbackHandler()
        run_id = uuid.uuid4()
        h.on_chain_start(
            serialized={"name": "QAChain"},
            inputs={"question": "what?"},
            run_id=run_id,
        )
        h.on_chain_error(RuntimeError("boom"), run_id=run_id)

    run()
    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["kind"] == "thought"
    assert steps[0]["name"] == "QAChain"
    assert "boom" in steps[0]["error"]


def test_agent_action_emits_immediate_step(store: JSONLStore) -> None:
    """on_agent_action writes a self-contained step (no start/end pair)."""
    from loupe.integrations.langchain import LoupeCallbackHandler

    @trace(framework="langchain-test", store=store)
    def run() -> None:
        h = LoupeCallbackHandler()
        action = SimpleNamespace(
            tool="calculator",
            tool_input="2+2",
            log="thinking about math",
        )
        h.on_agent_action(action, run_id=uuid.uuid4())

    run()
    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["kind"] == "thought"
    assert steps[0]["name"] == "action:calculator"
    assert steps[0]["inputs"]["tool_input"] == "2+2"
    assert steps[0]["outputs"]["log"] == "thinking about math"


def test_no_active_trace_is_safe_noop() -> None:
    """LoupeCallbackHandler outside any @trace context never raises."""
    from loupe.integrations.langchain import LoupeCallbackHandler

    h = LoupeCallbackHandler()
    h.on_llm_start(serialized={"name": "x"}, prompts=["p"], run_id=uuid.uuid4())
    h.on_tool_start(serialized={"name": "t"}, input_str="i", run_id=uuid.uuid4())
    h.on_chain_start(serialized={"name": "c"}, inputs={}, run_id=uuid.uuid4())
