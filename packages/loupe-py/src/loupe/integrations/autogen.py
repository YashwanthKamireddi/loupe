"""AutoGen integration — capture every ConversableAgent.generate_reply call.

Microsoft's AutoGen framework models agents that exchange messages. Every
turn flows through `ConversableAgent.generate_reply()` (sync) or
`a_generate_reply()` (async). Patching those captures each agent reply as
a Loupe Step.

Usage:
    from loupe import trace
    from loupe.integrations.autogen import patch
    patch()

    from autogen import ConversableAgent
    agent = ConversableAgent(name="planner", ...)

    @trace(framework="autogen")
    def run():
        return agent.generate_reply(messages=[...])
"""

from __future__ import annotations

import functools
import time
import uuid
from typing import Any

from loupe._redact import redact
from loupe.trace import Step, current_trace

_PATCHED_FLAG = "__loupe_patched__"


def patch() -> bool:
    """Monkey-patch ConversableAgent.generate_reply / .a_generate_reply.

    Idempotent. Returns True if patching happened, False if no-op.
    """
    try:
        # AutoGen rebrand: pyautogen ships `autogen` and `autogen.agentchat`
        try:
            from autogen.agentchat.conversable_agent import (
                ConversableAgent,  # type: ignore[import-not-found]
            )
        except ImportError:
            from autogen import ConversableAgent  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "loupe.integrations.autogen.patch() needs the `pyautogen` package. "
            "Install with: pip install pyautogen"
        ) from exc

    changed = False
    sync_reply = getattr(ConversableAgent, "generate_reply", None)
    if sync_reply is not None and not getattr(sync_reply, _PATCHED_FLAG, False):
        ConversableAgent.generate_reply = _wrap_sync(sync_reply)  # type: ignore[method-assign]
        changed = True
    async_reply = getattr(ConversableAgent, "a_generate_reply", None)
    if async_reply is not None and not getattr(async_reply, _PATCHED_FLAG, False):
        ConversableAgent.a_generate_reply = _wrap_async(async_reply)  # type: ignore[method-assign]
        changed = True
    return changed


def _wrap_sync(original: Any) -> Any:
    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.time()
        error: BaseException | None = None
        result: Any = None
        try:
            result = original(self, *args, **kwargs)
            return result
        except BaseException as exc:
            error = exc
            raise
        finally:
            _emit(self, args, kwargs, result, error, started)

    setattr(wrapper, _PATCHED_FLAG, True)
    return wrapper


def _wrap_async(original: Any) -> Any:
    @functools.wraps(original)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.time()
        error: BaseException | None = None
        result: Any = None
        try:
            result = await original(self, *args, **kwargs)
            return result
        except BaseException as exc:
            error = exc
            raise
        finally:
            _emit(self, args, kwargs, result, error, started)

    setattr(wrapper, _PATCHED_FLAG, True)
    return wrapper


def _emit(
    agent: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    result: Any,
    error: BaseException | None,
    started: float,
) -> None:
    t = current_trace()
    if t is None:
        return

    messages = kwargs.get("messages")
    if messages is None and args:
        messages = args[0]

    agent_name = getattr(agent, "name", None) or type(agent).__name__

    inputs: dict[str, Any] = {
        "agent": agent_name,
        "messages": redact(_short(messages)),
    }

    outputs: dict[str, Any] = {}
    if result is not None:
        outputs["text"] = _short(result if isinstance(result, str) else _extract_content(result))

    t.add_step(
        Step(
            step_id=uuid.uuid4().hex[:12],
            parent_step_id=None,
            kind="llm-call",
            name=f"autogen:{agent_name}",
            started_at=started,
            ended_at=time.time(),
            inputs=inputs,
            outputs=outputs,
            error=repr(error) if error is not None else None,
        )
    )


def _extract_content(reply: Any) -> str | None:
    """AutoGen replies can be dicts ({content, role, ...}) or plain strings."""
    if isinstance(reply, dict):
        c = reply.get("content")
        if isinstance(c, str):
            return c
        return str(reply)
    return str(reply) if reply is not None else None


def _short(value: Any, *, limit: int = 2000) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…[truncated]"
    try:
        text = str(value)
    except Exception:
        text = f"<{type(value).__name__}>"
    return text if len(text) <= limit else text[:limit] + "…[truncated]"
