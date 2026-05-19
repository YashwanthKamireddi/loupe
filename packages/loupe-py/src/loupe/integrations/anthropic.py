"""Anthropic SDK direct instrumentation — zero-config trace capture.

Usage:
    import anthropic
    from loupe import trace
    from loupe.integrations.anthropic import patch

    patch()                                  # call once at startup

    @trace(framework="anthropic")
    async def my_agent(q: str) -> str:
        client = anthropic.AsyncAnthropic()
        msg = await client.messages.create(model="...", messages=[...])
        return msg.content[0].text

Captures every `messages.create` call — sync + async, streaming + non-streaming.
For streaming calls, the wrapper proxies each event back to the caller in real
time while tee-ing them into an accumulator; the resulting Step is finalized
when the stream is exhausted (or the caller's `for` loop / `async for` exits).
"""

from __future__ import annotations

import functools
import time
import uuid
from typing import Any

from loupe._redact import redact
from loupe.integrations import suppress_http_capture
from loupe.integrations._streaming import TracedAsyncStream, TracedSyncStream
from loupe.trace import Step, current_trace

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


# ---------------------------------------------------------------------------
# Wrappers
# ---------------------------------------------------------------------------


def _wrap_sync(original: Any) -> Any:
    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.time()
        streaming = bool(kwargs.get("stream", False))

        if not streaming:
            error: BaseException | None = None
            result: Any = None
            try:
                with suppress_http_capture():
                    result = original(self, *args, **kwargs)
                return result
            except BaseException as exc:
                error = exc
                raise
            finally:
                _emit_single(kwargs, result, error, started)

        # Streaming path: hand back a transparent proxy that tees into a Step.
        try:
            with suppress_http_capture():
                original_stream = original(self, *args, **kwargs)
        except BaseException as exc:
            _emit_single(kwargs, None, exc, started)
            raise

        consume, finish = _make_accumulator(streaming=True)
        return TracedSyncStream(
            original_stream,
            on_event=consume,
            on_finish=finish,
            step_name=f"anthropic:{kwargs.get('model', 'unknown')}",
            inputs=_input_summary(kwargs),
            started_at=started,
        )

    setattr(wrapper, _PATCHED_FLAG, True)
    return wrapper


def _wrap_async(original: Any) -> Any:
    @functools.wraps(original)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.time()
        streaming = bool(kwargs.get("stream", False))

        if not streaming:
            error: BaseException | None = None
            result: Any = None
            try:
                with suppress_http_capture():
                    result = await original(self, *args, **kwargs)
                return result
            except BaseException as exc:
                error = exc
                raise
            finally:
                _emit_single(kwargs, result, error, started)

        # Streaming path
        try:
            with suppress_http_capture():
                original_stream = await original(self, *args, **kwargs)
        except BaseException as exc:
            _emit_single(kwargs, None, exc, started)
            raise

        consume, finish = _make_accumulator(streaming=True)
        return TracedAsyncStream(
            original_stream,
            on_event=consume,
            on_finish=finish,
            step_name=f"anthropic:{kwargs.get('model', 'unknown')}",
            inputs=_input_summary(kwargs),
            started_at=started,
        )

    setattr(wrapper, _PATCHED_FLAG, True)
    return wrapper


# ---------------------------------------------------------------------------
# Non-streaming Step emission
# ---------------------------------------------------------------------------


def _emit_single(
    kwargs: dict[str, Any],
    result: Any,
    error: BaseException | None,
    started: float,
) -> None:
    t = current_trace()
    if t is None:
        return

    inputs = _input_summary(kwargs)
    outputs: dict[str, Any] = {}
    if result is not None:
        outputs["stop_reason"] = getattr(result, "stop_reason", None)
        outputs["text"] = _extract_text(result)
        usage = getattr(result, "usage", None)
        if usage is not None:
            outputs["input_tokens"] = getattr(usage, "input_tokens", None)
            outputs["output_tokens"] = getattr(usage, "output_tokens", None)

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


# ---------------------------------------------------------------------------
# Streaming accumulator
# ---------------------------------------------------------------------------


def _make_accumulator(*, streaming: bool) -> tuple[Any, Any]:
    """Return (on_event, on_finish) closures for an Anthropic stream.

    Aggregates content_block_delta text deltas, captures usage from
    message_start and message_delta events, and stop_reason from message_delta.
    """
    state: dict[str, Any] = {
        "text_parts": [],
        "input_tokens": None,
        "output_tokens": None,
        "stop_reason": None,
    }

    def on_event(event: Any) -> None:
        etype = getattr(event, "type", None)
        if etype == "content_block_delta":
            delta = getattr(event, "delta", None)
            if delta is not None:
                # text_delta is the most common; input_json_delta etc. ignored
                dtype = getattr(delta, "type", None)
                if dtype == "text_delta":
                    text = getattr(delta, "text", None)
                    if isinstance(text, str):
                        state["text_parts"].append(text)
        elif etype == "message_start":
            msg = getattr(event, "message", None)
            usage = getattr(msg, "usage", None) if msg is not None else None
            if usage is not None:
                state["input_tokens"] = getattr(usage, "input_tokens", None)
        elif etype == "message_delta":
            usage = getattr(event, "usage", None)
            if usage is not None:
                ot = getattr(usage, "output_tokens", None)
                if ot is not None:
                    state["output_tokens"] = ot
            delta = getattr(event, "delta", None)
            if delta is not None:
                sr = getattr(delta, "stop_reason", None)
                if sr is not None:
                    state["stop_reason"] = sr

    def on_finish() -> dict[str, Any]:
        outputs: dict[str, Any] = {
            "text": _truncate("".join(state["text_parts"])),
            "stop_reason": state["stop_reason"],
            "streamed": True,
        }
        if state["input_tokens"] is not None:
            outputs["input_tokens"] = state["input_tokens"]
        if state["output_tokens"] is not None:
            outputs["output_tokens"] = state["output_tokens"]
        return outputs

    return on_event, on_finish


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _input_summary(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": kwargs.get("model"),
        "max_tokens": kwargs.get("max_tokens"),
        "system": _truncate(redact(kwargs.get("system"))),
        "messages": _summarize_messages(kwargs.get("messages")),
        "stream": bool(kwargs.get("stream", False)),
    }


def _summarize_messages(messages: Any) -> Any:
    if not isinstance(messages, list):
        return _truncate(redact(messages))
    return [
        {
            "role": m.get("role") if isinstance(m, dict) else getattr(m, "role", None),
            "content": _truncate(redact(
                m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
            )),
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
