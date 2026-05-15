"""Pydantic AI integration — drop-in trace capture for the Pydantic AI agent framework.

Pydantic AI ships its own model abstraction (`pydantic_ai.Model`) and an
EventStream that emits structured events for each step of an agent run. We
attach a Loupe-aware listener via the `instrument=True` option or by patching
the `Agent.run` / `Agent.run_sync` methods on the class.

This integration is opportunistic: if `pydantic_ai` isn't installed,
`patch()` raises `ImportError` with a helpful install hint. If it is
installed, every agent run from then on becomes a Loupe Trace automatically.

Usage:
    from loupe import trace
    from loupe.integrations.pydantic_ai import patch
    patch()

    from pydantic_ai import Agent
    agent = Agent("anthropic:claude-haiku-4-5", system_prompt="Be concise.")

    @trace(framework="pydantic-ai")
    async def my_agent(q: str) -> str:
        r = await agent.run(q)
        return r.output
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
    """Monkey-patch Pydantic AI's Agent.run / .run_sync. Idempotent.

    Returns True if patching happened, False if already patched.
    """
    try:
        from pydantic_ai import Agent  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "loupe.integrations.pydantic_ai.patch() needs the `pydantic-ai` package. "
            "Install with: pip install pydantic-ai"
        ) from exc

    changed = False
    if hasattr(Agent, "run_sync") and not getattr(Agent.run_sync, _PATCHED_FLAG, False):
        Agent.run_sync = _wrap_sync(Agent.run_sync)  # type: ignore[method-assign]
        changed = True
    if hasattr(Agent, "run") and not getattr(Agent.run, _PATCHED_FLAG, False):
        Agent.run = _wrap_async(Agent.run)  # type: ignore[method-assign]
        changed = True
    return changed


# ---------------------------------------------------------------------------
# Wrappers
# ---------------------------------------------------------------------------


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

    # Extract user prompt — first positional arg or `user_prompt` kwarg.
    prompt = kwargs.get("user_prompt")
    if prompt is None and args:
        prompt = args[0]

    model_name = _model_name(agent)
    inputs: dict[str, Any] = {
        "model": model_name,
        "prompt": redact(_short(prompt)),
    }
    system_prompt = getattr(agent, "_system_prompt", None) or getattr(agent, "system_prompt", None)
    if system_prompt:
        inputs["system"] = redact(_short(system_prompt))

    outputs: dict[str, Any] = {}
    if result is not None:
        outputs["text"] = _short(getattr(result, "output", None) or _short(result))
        usage = getattr(result, "usage", None)
        if usage is not None:
            outputs["input_tokens"] = (
                getattr(usage, "input_tokens", None)
                or getattr(usage, "request_tokens", None)
            )
            outputs["output_tokens"] = (
                getattr(usage, "output_tokens", None)
                or getattr(usage, "response_tokens", None)
            )

    t.add_step(
        Step(
            step_id=uuid.uuid4().hex[:12],
            parent_step_id=None,
            kind="llm-call",
            name=f"pydantic-ai:{model_name}",
            started_at=started,
            ended_at=time.time(),
            inputs=inputs,
            outputs=outputs,
            error=repr(error) if error is not None else None,
        )
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _model_name(agent: Any) -> str:
    """Pydantic AI lets you pass either a Model instance or a string id."""
    model = getattr(agent, "model", None)
    if model is None:
        return "unknown"
    # If it's a string-like identifier
    if isinstance(model, str):
        return model
    # If it has a `model_name` attribute
    name = getattr(model, "model_name", None) or getattr(model, "name", None)
    return str(name) if name is not None else type(model).__name__


def _short(value: Any, *, limit: int = 4000) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…[truncated]"
    try:
        text = str(value)
    except Exception:
        text = f"<{type(value).__name__}>"
    return text if len(text) <= limit else text[:limit] + "…[truncated]"
