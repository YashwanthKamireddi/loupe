"""OpenAI SDK direct instrumentation — zero-config trace capture.

Usage:
    from openai import OpenAI
    from loupe import trace
    from loupe.integrations.openai import patch

    patch()

    @trace(framework="openai")
    def my_agent(q: str) -> str:
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": q}],
        )
        return resp.choices[0].message.content

Captures Chat Completions (sync + async). Responses API is captured if present.
"""

from __future__ import annotations

import functools
import time
import uuid
from typing import Any

from loupe.trace import Step, current_trace

_PATCHED_FLAG = "__loupe_patched__"


def patch() -> bool:
    """Monkey-patch the OpenAI SDK. Idempotent."""
    try:
        from openai.resources.chat.completions import AsyncCompletions, Completions
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "loupe.integrations.openai.patch() needs the `openai` package. "
            "Install with: pip install openai"
        ) from exc

    changed = False
    if not getattr(Completions.create, _PATCHED_FLAG, False):
        Completions.create = _wrap_sync(Completions.create, "chat")  # type: ignore[method-assign]
        changed = True
    if not getattr(AsyncCompletions.create, _PATCHED_FLAG, False):
        AsyncCompletions.create = _wrap_async(AsyncCompletions.create, "chat")  # type: ignore[method-assign]
        changed = True

    # Best-effort Responses API support (added in openai>=1.50)
    try:
        from openai.resources.responses import AsyncResponses, Responses  # type: ignore

        if not getattr(Responses.create, _PATCHED_FLAG, False):
            Responses.create = _wrap_sync(Responses.create, "responses")  # type: ignore[method-assign]
            changed = True
        if not getattr(AsyncResponses.create, _PATCHED_FLAG, False):
            AsyncResponses.create = _wrap_async(AsyncResponses.create, "responses")  # type: ignore[method-assign]
            changed = True
    except ImportError:
        pass

    return changed


def _wrap_sync(original: Any, kind: str) -> Any:
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
            _emit(kind, kwargs, result, error, started)

    setattr(wrapper, _PATCHED_FLAG, True)
    return wrapper


def _wrap_async(original: Any, kind: str) -> Any:
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
            _emit(kind, kwargs, result, error, started)

    setattr(wrapper, _PATCHED_FLAG, True)
    return wrapper


def _emit(
    kind: str,
    kwargs: dict[str, Any],
    result: Any,
    error: BaseException | None,
    started: float,
) -> None:
    t = current_trace()
    if t is None:
        return

    inputs: dict[str, Any] = {
        "model": kwargs.get("model"),
        "messages": _summarize_messages(kwargs.get("messages") or kwargs.get("input")),
        "temperature": kwargs.get("temperature"),
        "stream": bool(kwargs.get("stream", False)),
    }

    outputs: dict[str, Any] = {}
    if result is not None:
        outputs["text"] = _extract_text(result)
        usage = getattr(result, "usage", None)
        if usage is not None:
            outputs["prompt_tokens"] = getattr(usage, "prompt_tokens", None) or getattr(
                usage, "input_tokens", None
            )
            outputs["completion_tokens"] = getattr(usage, "completion_tokens", None) or getattr(
                usage, "output_tokens", None
            )

    t.add_step(
        Step(
            step_id=uuid.uuid4().hex[:12],
            parent_step_id=None,
            kind="llm-call",
            name=f"openai-{kind}:{kwargs.get('model', 'unknown')}",
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
    # Chat completions: response.choices[0].message.content
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        msg = getattr(choices[0], "message", None)
        if msg is not None:
            text = getattr(msg, "content", None)
            if isinstance(text, str):
                return _truncate(text)
    # Responses API: response.output_text
    text = getattr(response, "output_text", None)
    if isinstance(text, str):
        return _truncate(text)
    return None


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
