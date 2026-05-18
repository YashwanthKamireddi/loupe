"""OpenHands integration — capture every Controller.step() in an OSS coding agent run.

OpenHands (formerly OpenDevin) is an open-source autonomous coding agent
framework. Its core loop is a `Controller` that drives one or more `Agent`
instances, calling `agent.step(state)` to get the next action. We patch
the top-level `Agent.step` (and async variant if present) so every
decision lands as a Loupe Step.

Usage:
    from loupe import trace
    from loupe.integrations.openhands import patch
    patch()

    @trace(framework="openhands")
    async def run_task(...):
        # ... your usual openhands setup ...
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
    """Monkey-patch openhands.controller.agent.Agent.step. Idempotent.

    OpenHands has moved modules a few times across versions. We try the
    current path first and fall back to legacy locations.
    """
    agent_cls: type | None = None
    for module_path in (
        "openhands.controller.agent",          # current (>= 0.10)
        "opendevin.controller.agent",           # legacy
    ):
        try:
            mod = __import__(module_path, fromlist=["Agent"])
            agent_cls = getattr(mod, "Agent", None)
            if agent_cls is not None:
                break
        except ImportError:
            continue
    if agent_cls is None:
        raise ImportError(  # pragma: no cover
            "loupe.integrations.openhands.patch() needs the `openhands-ai` "
            "package. Install with: pip install openhands-ai"
        )

    changed = False
    step_fn = getattr(agent_cls, "step", None)
    if step_fn is not None and not getattr(step_fn, _PATCHED_FLAG, False):
        agent_cls.step = _wrap(step_fn)  # type: ignore[method-assign]
        changed = True
    return changed


def _wrap(original: Any) -> Any:
    import inspect

    is_async = inspect.iscoroutinefunction(original)

    if is_async:
        @functools.wraps(original)
        async def async_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
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

        setattr(async_wrapper, _PATCHED_FLAG, True)
        return async_wrapper

    @functools.wraps(original)
    def sync_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
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

    setattr(sync_wrapper, _PATCHED_FLAG, True)
    return sync_wrapper


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

    agent_name = (
        getattr(agent, "name", None)
        or type(agent).__name__
    )

    # OpenHands Agents receive a State object as the first positional arg.
    state = args[0] if args else kwargs.get("state")
    iteration = getattr(state, "iteration", None) or getattr(state, "iteration_count", None)

    inputs: dict[str, Any] = {
        "agent": agent_name,
        "iteration": iteration,
    }

    outputs: dict[str, Any] = {}
    if result is not None:
        # The return is an Action — capture its class name + thought
        outputs["action"] = type(result).__name__
        thought = getattr(result, "thought", None) or getattr(result, "_thought", None)
        if thought:
            outputs["thought"] = redact(_short(thought))
        # Action arguments (e.g. CmdRunAction.command, FileEditAction.path)
        common_args = {}
        for attr in ("command", "path", "url", "code", "content"):
            val = getattr(result, attr, None)
            if val is not None:
                common_args[attr] = _short(val)
        if common_args:
            outputs["args"] = redact(common_args)

    t.add_step(
        Step(
            step_id=uuid.uuid4().hex[:12],
            parent_step_id=None,
            kind="thought",
            name=f"openhands:{agent_name}",
            started_at=started,
            ended_at=time.time(),
            inputs=inputs,
            outputs=outputs,
            error=repr(error) if error is not None else None,
        )
    )


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
