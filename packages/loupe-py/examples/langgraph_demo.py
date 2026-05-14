"""A real LangGraph agent traced by Loupe — no Anthropic/OpenAI key needed.

Demonstrates @trace + LoupeCallbackHandler on a tiny two-node graph that
purposely fails (the second node raises). The full event tree — chain start,
LLM call, chain end, error — is captured to ~/.loupe/traces/.

Run me:
    cd packages/loupe-py
    pip install -e '.[langgraph]'
    python examples/langgraph_demo.py
    loupe list
"""

from __future__ import annotations

import asyncio
from typing import TypedDict

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.graph import END, StateGraph

from loupe import trace
from loupe.integrations.langchain import LoupeCallbackHandler


class AgentState(TypedDict):
    query: str
    plan: str
    answer: str


# A scripted fake LLM — no API key required, perfectly deterministic.
fake_llm = FakeListChatModel(
    responses=[
        "Plan: 1) read the auth module, 2) write a small refactor diff",
        "I will now delete the old file. rm -rf src/",  # the bad action
    ]
)


async def plan_node(state: AgentState) -> AgentState:
    msg = await fake_llm.ainvoke(f"Plan how to solve: {state['query']}")
    return {**state, "plan": msg.content}


async def act_node(state: AgentState) -> AgentState:
    msg = await fake_llm.ainvoke(f"Act on plan: {state['plan']}")
    # Simulate the kind of destructive failure LoupeBench is built to surface.
    if "rm -rf" in msg.content:
        raise RuntimeError(f"unguarded-delete: agent attempted `{msg.content.strip()}`")
    return {**state, "answer": msg.content}


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("plan", plan_node)
    g.add_node("act", act_node)
    g.set_entry_point("plan")
    g.add_edge("plan", "act")
    g.add_edge("act", END)
    return g.compile()


@trace(framework="langgraph", name="auth-refactor-agent")
async def run_agent(query: str) -> AgentState:
    graph = build_graph()
    handler = LoupeCallbackHandler()
    return await graph.ainvoke(
        {"query": query, "plan": "", "answer": ""},
        config={"callbacks": [handler]},
    )


async def main() -> None:
    try:
        result = await run_agent("refactor auth.py to use jose")
        print("done:", result)
    except RuntimeError as exc:
        print(f"caught failure (expected): {exc}")
    print("\nrun: loupe list")


if __name__ == "__main__":
    asyncio.run(main())
