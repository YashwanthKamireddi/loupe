"""The @trace decorator — Loupe's entry point.

Wraps any async or sync function that runs an LLM agent and captures every step
to a local Trace object. Zero configuration: traces land in ~/.loupe/traces/
unless a Store is passed explicitly.

This is intentionally small. Framework-specific instrumentation (LangGraph,
OpenHands, Vercel AI SDK) lives in loupe.integrations.* and registers via
callback hooks. The core trace primitive must stay stable forever.
"""

from __future__ import annotations

import functools
import inspect
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from loupe.store import Store, default_store

StepKind = Literal["llm-call", "tool-call", "io", "thought", "error", "custom"]

T = TypeVar("T")


@dataclass
class Step:
    """A single observable event during an agent run."""

    step_id: str
    parent_step_id: str | None
    kind: StepKind
    name: str
    started_at: float
    ended_at: float | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def duration_ms(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at) * 1000


@dataclass
class Trace:
    """A full agent run — a tree of steps with metadata."""

    trace_id: str
    name: str
    framework: str | None
    started_at: float
    ended_at: float | None = None
    steps: list[Step] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_step(self, step: Step) -> None:
        self.steps.append(step)

    @property
    def duration_ms(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at) * 1000


# Stack of currently-active traces (per asyncio task / thread)
_current_trace: ContextVar[Trace | None] = ContextVar("loupe_current_trace", default=None)


def current_trace() -> Trace | None:
    """Return the trace running in the current context, if any."""
    return _current_trace.get()


def trace(
    fn: Callable[..., T] | None = None,
    *,
    name: str | None = None,
    framework: str | None = None,
    store: Store | None = None,
) -> Callable[..., T]:
    """Decorator: wrap an agent function so every run is captured.

    Usage:
        @trace
        async def my_agent(query: str) -> str:
            ...

        # or with options
        @trace(framework="langgraph", name="auth-refactor-agent")
        async def my_agent(query: str) -> str:
            ...
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        trace_name = name or func.__name__
        is_async = inspect.iscoroutinefunction(func)

        if is_async:

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                t = _begin_trace(trace_name, framework)
                token = _current_trace.set(t)
                try:
                    result = await func(*args, **kwargs)  # type: ignore[misc]
                    return result
                except Exception as exc:
                    t.metadata["failed"] = True
                    t.metadata["error"] = repr(exc)
                    raise
                finally:
                    _finish_trace(t, store)
                    _current_trace.reset(token)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            t = _begin_trace(trace_name, framework)
            token = _current_trace.set(t)
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                t.metadata["failed"] = True
                t.metadata["error"] = repr(exc)
                raise
            finally:
                _finish_trace(t, store)
                _current_trace.reset(token)

        return sync_wrapper  # type: ignore[return-value]

    if fn is None:
        return decorator  # type: ignore[return-value]
    return decorator(fn)


def record_step(
    kind: StepKind,
    name: str,
    *,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    error: str | None = None,
    parent_step_id: str | None = None,
) -> Step | None:
    """Append a step to the trace running in the current context.

    Returns None if no trace is active (i.e., called outside an @trace function).
    Framework integrations call this from their own hooks.
    """
    t = current_trace()
    if t is None:
        return None
    step = Step(
        step_id=uuid.uuid4().hex[:12],
        parent_step_id=parent_step_id,
        kind=kind,
        name=name,
        started_at=time.time(),
        ended_at=time.time(),
        inputs=inputs or {},
        outputs=outputs or {},
        metadata=metadata or {},
        error=error,
    )
    t.add_step(step)
    return step


def _begin_trace(name: str, framework: str | None) -> Trace:
    return Trace(
        trace_id=uuid.uuid4().hex,
        name=name,
        framework=framework,
        started_at=time.time(),
    )


def _finish_trace(t: Trace, store: Store | None) -> None:
    t.ended_at = time.time()
    (store or default_store()).save(t)
