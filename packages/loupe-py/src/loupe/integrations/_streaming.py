"""Generic streaming-response wrappers used by integrations/anthropic + openai.

Both `client.messages.create(stream=True)` (Anthropic) and
`client.chat.completions.create(stream=True)` (OpenAI) return an iterable
stream object. We pass that stream through unchanged to the caller, while
tee-ing each event into a Loupe Step that's finalized when iteration ends.

Design notes:
- We never *materialize* the stream up front — caller still gets back-pressured
  events in real time.
- We support both sync (`__iter__`/`__next__`) and async (`__aiter__`/
  `__anext__`) streams.
- Anything we don't override is delegated to the underlying stream via
  __getattr__, so existing SDK conveniences keep working.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Callable
from typing import Any

from loupe.trace import Step, current_trace


class TracedSyncStream:
    """Pass-through wrapper for a synchronous iterable stream."""

    def __init__(
        self,
        original: Any,
        *,
        on_event: Callable[[Any], None],
        on_finish: Callable[[], dict[str, Any]],
        step_name: str,
        inputs: dict[str, Any],
        started_at: float,
    ) -> None:
        self._orig = original
        self._on_event = on_event
        self._on_finish = on_finish
        self._step_name = step_name
        self._inputs = inputs
        self._started_at = started_at
        self._finished = False
        self._error: BaseException | None = None

    # iteration --------------------------------------------------------------

    def __iter__(self) -> TracedSyncStream:
        # Some SDK streams expose __iter__ that *returns* a different iterator;
        # honor that by switching to the returned iterator under the hood.
        sub = self._orig.__iter__() if hasattr(self._orig, "__iter__") else self._orig
        if sub is not self._orig:
            self._orig = sub
        return self

    def __next__(self) -> Any:
        try:
            event = next(self._orig)
        except StopIteration:
            self._finalize()
            raise
        except BaseException as exc:
            self._error = exc
            self._finalize()
            raise
        with contextlib.suppress(Exception):  # never let telemetry crash users
            self._on_event(event)
        return event

    # context manager --------------------------------------------------------

    def __enter__(self) -> TracedSyncStream:
        if hasattr(self._orig, "__enter__"):
            self._orig = self._orig.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> Any:
        if exc is not None:
            self._error = exc
        self._finalize()
        if hasattr(self._orig, "__exit__"):
            return self._orig.__exit__(exc_type, exc, tb)
        return False

    # delegate everything else -----------------------------------------------

    def __getattr__(self, name: str) -> Any:
        return getattr(self._orig, name)

    # finalize ---------------------------------------------------------------

    def _finalize(self) -> None:
        if self._finished:
            return
        self._finished = True
        t = current_trace()
        if t is None:
            return
        outputs: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            outputs = self._on_finish()
        t.add_step(
            Step(
                step_id=uuid.uuid4().hex[:12],
                parent_step_id=None,
                kind="llm-call",
                name=self._step_name,
                started_at=self._started_at,
                ended_at=time.time(),
                inputs=self._inputs,
                outputs=outputs,
                error=repr(self._error) if self._error else None,
            )
        )


class TracedAsyncStream:
    """Pass-through wrapper for an asynchronous iterable stream."""

    def __init__(
        self,
        original: Any,
        *,
        on_event: Callable[[Any], None],
        on_finish: Callable[[], dict[str, Any]],
        step_name: str,
        inputs: dict[str, Any],
        started_at: float,
    ) -> None:
        self._orig = original
        self._on_event = on_event
        self._on_finish = on_finish
        self._step_name = step_name
        self._inputs = inputs
        self._started_at = started_at
        self._finished = False
        self._error: BaseException | None = None

    def __aiter__(self) -> TracedAsyncStream:
        sub = self._orig.__aiter__() if hasattr(self._orig, "__aiter__") else self._orig
        if sub is not self._orig:
            self._orig = sub
        return self

    async def __anext__(self) -> Any:
        try:
            event = await self._orig.__anext__()
        except StopAsyncIteration:
            self._finalize()
            raise
        except BaseException as exc:
            self._error = exc
            self._finalize()
            raise
        with contextlib.suppress(Exception):
            self._on_event(event)
        return event

    async def __aenter__(self) -> TracedAsyncStream:
        if hasattr(self._orig, "__aenter__"):
            self._orig = await self._orig.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> Any:
        if exc is not None:
            self._error = exc
        self._finalize()
        if hasattr(self._orig, "__aexit__"):
            return await self._orig.__aexit__(exc_type, exc, tb)
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._orig, name)

    def _finalize(self) -> None:
        if self._finished:
            return
        self._finished = True
        t = current_trace()
        if t is None:
            return
        outputs: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            outputs = self._on_finish()
        t.add_step(
            Step(
                step_id=uuid.uuid4().hex[:12],
                parent_step_id=None,
                kind="llm-call",
                name=self._step_name,
                started_at=self._started_at,
                ended_at=time.time(),
                inputs=self._inputs,
                outputs=outputs,
                error=repr(self._error) if self._error else None,
            )
        )
