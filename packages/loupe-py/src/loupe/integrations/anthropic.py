"""Anthropic SDK direct instrumentation — zero-config trace capture.

Usage:
    import anthropic
    from loupe import trace
    from loupe.integrations.anthropic import patch

    patch()                                  # call once at startup

    @trace(framework="anthropic")
    async def my_agent(q: str) -> str:
        client = anthropic.AsyncAnthropic()
        msg = await client.messages.create(...)
        return msg.content[0].text

Every messages.create call (sync + async) becomes a Step on the active trace.
Streaming responses are recorded with stream=True metadata; their token-by-token
output is not yet aggregated (TODO v0.0.5).
"""

from __future__ import annotations

import functools
import time
from typing import TYPE_CHECKING, Any

from loupe.trace import Step, current_trace

if TYPE_CHECKING:
    pass  # avoid importing anthropic at type-check time

_PATCHED_FLAG = "__loupe_patched__"


def patch() -> bool:
    """Monkey-patch the Anthropic SDK so every messages.create is traced.

    Idempotent: safe to call multiple times. Returns True if patching happened,
    False if already patched.
    """
    try:
        from anthropic.resources.messages import AsyncMessages, Messages
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "loupe.integrations.anthropic.patch() needs the `anthropic` package. "
            "Install with: pip install anthropic"
        ) from exc

    changed = False
    if not getattr(Messages.create, _PATCHED_FLAG, False):
        Messages.create = _wrap_sync(Messages.create)  # type: ignore[method-assign]
        changed = True
    if not getattr(AsyncMessages.create, _PATCHED_FLAG, False):
        AsyncMessages.create = _wrap_async(AsyncMessages.create)  # type: ignore[method-assign]
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
            _emit(kwargs, result, error, started)

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
            _emit(kwargs, result, error, started)

    setattr(wrapper, _PATCHED_FLAG, True)
    return wrapper


def _emit(kwargs: dict[str, Any], result: Any, error: BaseException | None, started: float) -> None:
    t = current_trace()
    if t is None:
        return

    inputs: dict[str, Any] = {
        "model": kwargs.get("model"),
        "max_tokens": kwargs.get("max_tokens"),
        "system": _truncate(kwargs.get("system")),
        "messages": _summarize_messages(kwargs.get("messages")),
        "stream": bool(kwargs.get("stream", False)),
    }

    outputs: dict[str, Any] = {}
    if result is not None:
        outputs["stop_reason"] = getattr(result, "stop_reason", None)
        outputs["text"] = _extract_text(result)
        usage = getattr(result, "usage", None)
        if usage is not None:
            outputs["input_tokens"] = getattr(usage, "input_tokens", None)
            outputs["output_tokens"] = getattr(usage, "output_tokens", None)

    import uuid

    t.add_step(
        Step(
            step_id=uuid.uuid4().hex[:12],
            parent_step_id=None,
            kind="llm-call",
            name=f"anthropic:{kwargs.get('model', 'unknown')}",
            started_at=started,
            ended_at=time.time(),
            inputs=inputs,
            outputs=outputs,
            error=repr(error) if error is not None else None,
        )
    )


def _summarize_messages(messages: Any) -> Any:
    if not isinstance(messages, list):
        return _truncate(messages)
    return [
        {
            "role": m.get("role") if isinstance(m, dict) else getattr(m, "role", None),
            "content": _truncate(
                m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
            ),
        }
        for m in messages
    ]


def _extract_text(response: Any) -> str | None:
    content = getattr(response, "content", None)
    if isinstance(content, list) and content:
        block = content[0]
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return _truncate(text)
    return _truncate(str(content)) if content is not None else None


def _truncate(value: Any, *, limit: int = 4000) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…[truncated]"
    if isinstance(value, (list, dict)):
        text = repr(value)
        return text if len(text) <= limit else text[:limit] + "…[truncated]"
    try:
        text = repr(value)
    except Exception:
        text = f"<{type(value).__name__}>"
    return text if len(text) <= limit else text[:limit] + "…[truncated]"
