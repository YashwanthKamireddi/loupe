"""CrewAI integration — capture every Crew.kickoff() multi-agent run.

CrewAI organizes agents into a "Crew" that executes a series of Tasks. The
top-level entry point is `Crew.kickoff()` (sync) and `Crew.kickoff_async()`.
Patching those gives one Step per crew run with the task descriptions
captured. Future versions could break this down per-Task for finer telemetry.

Usage:
    from loupe import trace
    from loupe.integrations.crewai import patch
    patch()

    from crewai import Agent, Task, Crew
    crew = Crew(agents=[...], tasks=[...])

    @trace(framework="crewai")
    def run():
        return crew.kickoff(inputs={"topic": "loupe"})
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
    """Monkey-patch crewai.Crew.kickoff / .kickoff_async. Idempotent."""
    try:
        from crewai import Crew  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "loupe.integrations.crewai.patch() needs the `crewai` package. "
            "Install with: pip install crewai"
        ) from exc

    changed = False
    kickoff = getattr(Crew, "kickoff", None)
    if kickoff is not None and not getattr(kickoff, _PATCHED_FLAG, False):
        Crew.kickoff = _wrap_sync(kickoff)  # type: ignore[method-assign]
        changed = True
    kickoff_async = getattr(Crew, "kickoff_async", None)
    if kickoff_async is not None and not getattr(kickoff_async, _PATCHED_FLAG, False):
        Crew.kickoff_async = _wrap_async(kickoff_async)  # type: ignore[method-assign]
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
            _emit(self, kwargs, result, error, started)

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
            _emit(self, kwargs, result, error, started)

    setattr(wrapper, _PATCHED_FLAG, True)
    return wrapper


def _emit(
    crew: Any,
    kwargs: dict[str, Any],
    result: Any,
    error: BaseException | None,
    started: float,
) -> None:
    t = current_trace()
    if t is None:
        return

    agents = getattr(crew, "agents", []) or []
    tasks = getattr(crew, "tasks", []) or []

    inputs: dict[str, Any] = {
        "agent_count": len(agents),
        "task_count": len(tasks),
        "task_descriptions": redact(
            [_short(getattr(t_, "description", str(t_))) for t_ in tasks][:8]
        ),
        "kickoff_inputs": redact(kwargs.get("inputs") or {}),
    }

    outputs: dict[str, Any] = {}
    if result is not None:
        # CrewOutput exposes .raw (final string) and .token_usage on newer versions
        outputs["text"] = _short(getattr(result, "raw", None) or str(result))
        usage = getattr(result, "token_usage", None)
        if usage is not None:
            outputs["total_tokens"] = (
                getattr(usage, "total_tokens", None)
                or getattr(usage, "total", None)
            )

    t.add_step(
        Step(
            step_id=uuid.uuid4().hex[:12],
            parent_step_id=None,
            kind="thought",  # a crew run is a meta-step over many LLM calls
            name=f"crewai:Crew({len(agents)} agents × {len(tasks)} tasks)",
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
