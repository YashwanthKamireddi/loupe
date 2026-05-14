"""LangChain / LangGraph instrumentation.

Usage:
    from loupe import trace
    from loupe.integrations.langchain import LoupeCallbackHandler

    @trace(framework="langgraph")
    async def my_agent(query: str):
        handler = LoupeCallbackHandler()
        return await graph.ainvoke({"q": query}, config={"callbacks": [handler]})

The handler subscribes to LangChain's callback events and converts each into a
Loupe Step on the active trace (via ContextVar). It depends on `langchain-core`
only, so it works for plain LangChain chains, LangGraph graphs, and any other
runnable that fires LangChain callbacks.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from typing import TYPE_CHECKING, Any

try:
    from langchain_core.callbacks.base import BaseCallbackHandler
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "loupe.integrations.langchain requires langchain-core. "
        "Install with `pip install 'loupe[langgraph]'`."
    ) from exc

from loupe.trace import Step, current_trace

if TYPE_CHECKING:
    from langchain_core.agents import AgentAction, AgentFinish
    from langchain_core.outputs import LLMResult


class LoupeCallbackHandler(BaseCallbackHandler):
    """Capture LangChain/LangGraph events into the active Loupe trace.

    Steps with a matching run_id are paired across *_start and *_end so each
    Step records duration + outputs. Errors flow through *_error and finalize
    the Step with the error payload.
    """

    raise_error = False
    run_inline = True

    def __init__(self) -> None:
        self._pending: dict[uuid.UUID, Step] = {}

    # -- LLM ---------------------------------------------------------------

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._open(
            run_id,
            parent_run_id,
            kind="llm-call",
            name=_short_name(serialized) or "llm",
            inputs={"prompts": prompts},
        )

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        flat = [_stringify(m) for batch in messages for m in batch]
        self._open(
            run_id,
            parent_run_id,
            kind="llm-call",
            name=_short_name(serialized) or "chat-model",
            inputs={"messages": flat},
        )

    def on_llm_end(self, response: LLMResult, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        outputs: dict[str, Any] = {}
        with contextlib.suppress(AttributeError, IndexError):
            outputs["text"] = response.generations[0][0].text
        if getattr(response, "llm_output", None):
            outputs["llm_output"] = response.llm_output
        self._close(run_id, outputs=outputs)

    def on_llm_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        self._close(run_id, error=repr(error))

    # -- Tools -------------------------------------------------------------

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._open(
            run_id,
            parent_run_id,
            kind="tool-call",
            name=_short_name(serialized) or "tool",
            inputs={"input": input_str},
        )

    def on_tool_end(self, output: Any, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        self._close(run_id, outputs={"output": _stringify(output)})

    def on_tool_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        self._close(run_id, error=repr(error))

    # -- Chains / Graph nodes ---------------------------------------------

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._open(
            run_id,
            parent_run_id,
            kind="thought",
            name=_short_name(serialized) or kwargs.get("name") or "chain",
            inputs={"inputs": _stringify(inputs)},
        )

    def on_chain_end(self, outputs: dict[str, Any], *, run_id: uuid.UUID, **kwargs: Any) -> None:
        self._close(run_id, outputs={"outputs": _stringify(outputs)})

    def on_chain_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        self._close(run_id, error=repr(error))

    # -- Agent decisions ---------------------------------------------------

    def on_agent_action(
        self,
        action: AgentAction,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        t = current_trace()
        if t is None:
            return
        t.add_step(
            Step(
                step_id=uuid.uuid4().hex[:12],
                parent_step_id=str(parent_run_id) if parent_run_id else None,
                kind="thought",
                name=f"action:{action.tool}",
                started_at=time.time(),
                ended_at=time.time(),
                inputs={"tool_input": _stringify(action.tool_input)},
                outputs={"log": getattr(action, "log", "")},
            )
        )

    def on_agent_finish(
        self,
        finish: AgentFinish,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        t = current_trace()
        if t is None:
            return
        t.add_step(
            Step(
                step_id=uuid.uuid4().hex[:12],
                parent_step_id=str(parent_run_id) if parent_run_id else None,
                kind="thought",
                name="finish",
                started_at=time.time(),
                ended_at=time.time(),
                outputs={"return_values": _stringify(finish.return_values)},
            )
        )

    # -- internals --------------------------------------------------------

    def _open(
        self,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None,
        *,
        kind: str,
        name: str,
        inputs: dict[str, Any],
    ) -> None:
        t = current_trace()
        if t is None:
            return
        step = Step(
            step_id=uuid.uuid4().hex[:12],
            parent_step_id=str(parent_run_id) if parent_run_id else None,
            kind=kind,  # type: ignore[arg-type]
            name=name,
            started_at=time.time(),
            inputs=inputs,
            metadata={"langchain_run_id": str(run_id)},
        )
        self._pending[run_id] = step
        t.add_step(step)

    def _close(
        self,
        run_id: uuid.UUID,
        *,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        step = self._pending.pop(run_id, None)
        if step is None:
            return
        step.ended_at = time.time()
        if outputs:
            step.outputs.update(outputs)
        if error:
            step.error = error


# -- helpers --------------------------------------------------------------


def _short_name(serialized: dict[str, Any] | None) -> str | None:
    if not serialized:
        return None
    if "name" in serialized:
        return str(serialized["name"])
    ident = serialized.get("id")
    if isinstance(ident, list) and ident:
        return str(ident[-1])
    return None


def _stringify(value: Any, *, limit: int = 2000) -> Any:
    """Best-effort JSON-friendly conversion, truncated to keep traces small."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…[truncated]"
    if isinstance(value, dict):
        return {k: _stringify(v, limit=limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stringify(v, limit=limit) for v in value]
    try:
        text = repr(value)
    except Exception:
        text = f"<{type(value).__name__}>"
    return text if len(text) <= limit else text[:limit] + "…[truncated]"
