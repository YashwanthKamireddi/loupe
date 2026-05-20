"""Universal HTTP-level capture — works with ANY Python client that uses httpx.

Most modern Python LLM SDKs (anthropic, openai, mistralai, google-genai,
groq, instructor, dspy, etc.) use httpx under the hood. This integration
patches `httpx.Client.send` / `httpx.AsyncClient.send` once, sniffs the
target URL, and records a Loupe Step for each call to a known provider.

Coverage in 2026:
- 50+ providers across frontier labs, inference services, aggregators,
  enterprise clouds, embedding APIs, and local servers (see _providers.py)
- Fallback: unknown hosts whose payload *looks like* an OpenAI-spec call
  (messages + model) are captured as `openai-compatible:<host>` — this
  catches LiteLLM, internal proxies, OpenAI-compatible forks, etc.

Use this when:
- Your library doesn't have a Loupe direct integration yet.
- You want one switch that captures *everything*.

Don't use this *in addition to* the direct anthropic/openai integrations —
you'd double-record. Pick one.
"""

from __future__ import annotations

import contextlib
import functools
import json as _json
import os
import re
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from loupe._redact import redact
from loupe.integrations import direct_capture_active
from loupe.integrations._providers import (
    detect_provider_from_host,
    looks_like_openai_compatible,
)
from loupe.trace import Step, current_trace

_PATCHED_FLAG = "__loupe_patched__"


def patch() -> bool:
    """Monkey-patch httpx.Client.send + httpx.AsyncClient.send. Idempotent."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "loupe.integrations.httpx.patch() needs the `httpx` package. "
            "Install with: pip install httpx"
        ) from exc

    changed = False
    if not getattr(httpx.Client.send, _PATCHED_FLAG, False):
        httpx.Client.send = _wrap_sync(httpx.Client.send)  # type: ignore[method-assign]
        changed = True
    if not getattr(httpx.AsyncClient.send, _PATCHED_FLAG, False):
        httpx.AsyncClient.send = _wrap_async(httpx.AsyncClient.send)  # type: ignore[method-assign]
        changed = True
    return changed


def _autopatch_enabled() -> bool:
    """``LOUPE_AUTOPATCH=1`` makes the wrapper auto-create an implicit
    one-call trace when no @trace context is active — the zero-code path.
    """
    return os.environ.get("LOUPE_AUTOPATCH") in ("1", "true", "yes", "on")


@contextlib.contextmanager
def _implicit_trace_context() -> Any:
    """Create a one-call anonymous trace on the current asyncio task.

    Yields nothing; the caller does its normal capture work inside the
    block. On exit, the trace is finalized and written to the default
    store. Designed to be cheap — runs only when a real LLM call is
    about to happen and no parent trace exists.
    """
    # Lazy imports so the universal-httpx module stays cheap when nobody
    # uses autopatch mode.
    from loupe.store import default_store
    from loupe.trace import _begin_trace, _current_trace, _finish_trace

    t = _begin_trace("autopatch", "auto")
    token = _current_trace.set(t)
    try:
        yield
    except BaseException as exc:
        t.metadata["failed"] = True
        t.metadata["error"] = repr(exc)
        raise
    finally:
        _finish_trace(t, default_store())
        _current_trace.reset(token)


def _wrap_sync(original: Any) -> Any:
    @functools.wraps(original)
    def wrapper(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        if direct_capture_active.get():
            return original(self, request, *args, **kwargs)

        # Classify FIRST — if this isn't an LLM call we recognize, skip
        # the cost of starting a trace entirely.
        body = _safe_read_request_body(request)
        provider_label = _classify(request, body)
        if provider_label is None:
            return original(self, request, *args, **kwargs)

        if current_trace() is None:
            if not _autopatch_enabled():
                return original(self, request, *args, **kwargs)
            # Autopatch: wrap in an implicit one-call trace.
            with _implicit_trace_context():
                return _emit_around(
                    self, request, args, kwargs,
                    original, provider_label, body,
                )

        return _emit_around(
            self, request, args, kwargs, original, provider_label, body,
        )

    setattr(wrapper, _PATCHED_FLAG, True)
    return wrapper


def _wrap_async(original: Any) -> Any:
    @functools.wraps(original)
    async def wrapper(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        if direct_capture_active.get():
            return await original(self, request, *args, **kwargs)

        body = _safe_read_request_body(request)
        provider_label = _classify(request, body)
        if provider_label is None:
            return await original(self, request, *args, **kwargs)

        if current_trace() is None:
            if not _autopatch_enabled():
                return await original(self, request, *args, **kwargs)
            with _implicit_trace_context():
                return await _emit_around_async(
                    self, request, args, kwargs,
                    original, provider_label, body,
                )

        return await _emit_around_async(
            self, request, args, kwargs, original, provider_label, body,
        )

    setattr(wrapper, _PATCHED_FLAG, True)
    return wrapper


def _emit_around(
    self: Any, request: Any, args: tuple, kwargs: dict,
    original: Any, provider_label: str, body: Any,
) -> Any:
    """Sync invoke + emit step. Extracted so the autopatch + normal paths
    share one capture body."""
    started = time.time()
    error: BaseException | None = None
    response = None
    try:
        response = original(self, request, *args, **kwargs)
        return response
    except BaseException as exc:
        error = exc
        raise
    finally:
        _emit(provider_label, request, body, response, error, started)


async def _emit_around_async(
    self: Any, request: Any, args: tuple, kwargs: dict,
    original: Any, provider_label: str, body: Any,
) -> Any:
    """Async invoke + emit step."""
    started = time.time()
    error: BaseException | None = None
    response = None
    try:
        response = await original(self, request, *args, **kwargs)
        return response
    except BaseException as exc:
        error = exc
        raise
    finally:
        _emit(provider_label, request, body, response, error, started)


def _host_of(request: Any) -> str | None:
    try:
        return urlparse(str(request.url)).hostname
    except Exception:
        return None


def _classify(request: Any, body: Any) -> str | None:
    """Return a provider label, falling back to openai-compatible detection."""
    host = _host_of(request)
    matched = detect_provider_from_host(host)
    if matched is not None:
        return matched.label
    # Fallback: it walks like OpenAI? Capture it anyway.
    if looks_like_openai_compatible(body) and host:
        return f"openai-compatible:{host}"
    return None


_GEMINI_URL_MODEL = re.compile(r"/models/([^/:?]+)(?:[:/?].*)?$")


def _extract_model(request: Any, body: dict | None) -> str | None:
    """Pull the model name out of the request — body first, then URL.

    Different providers put the model in different places:
      - Anthropic / OpenAI / most: in the JSON body `model` field
      - Google Gemini: in the URL path `/v1beta/models/<name>:generateContent`
    """
    if isinstance(body, dict) and isinstance(body.get("model"), str):
        return body["model"]
    try:
        path = urlparse(str(request.url)).path
    except Exception:
        return None
    m = _GEMINI_URL_MODEL.search(path)
    return m.group(1) if m else None


def _safe_read_request_body(request: Any) -> dict | None:
    """Best-effort parse of a JSON request body. Returns None on any failure."""
    try:
        content = getattr(request, "content", None)
        if not content:
            return None
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        return _json.loads(content)
    except Exception:
        return None


def _emit(
    provider: str,
    request: Any,
    body: dict | None,
    response: Any,
    error: BaseException | None,
    started: float,
) -> None:
    t = current_trace()
    if t is None:
        return

    model = _extract_model(request, body)
    inputs: dict[str, Any] = {"provider": provider, "model": model}
    if isinstance(body, dict):
        if "messages" in body:
            inputs["messages"] = _truncate(redact(body["messages"]))
        if "prompt" in body:
            inputs["prompt"] = _truncate(redact(body["prompt"]))
        if "max_tokens" in body:
            inputs["max_tokens"] = body["max_tokens"]
        if body.get("stream"):
            inputs["stream"] = True

    outputs: dict[str, Any] = {}
    if response is not None:
        outputs["status"] = getattr(response, "status_code", None)
        # Only try to decode JSON for non-streaming responses
        if not bool(inputs.get("stream")):
            try:
                payload = response.json()
                outputs.update(_summarize_response(provider, payload))
            except Exception:
                pass

    t.add_step(
        Step(
            step_id=uuid.uuid4().hex[:12],
            parent_step_id=None,
            kind="llm-call",
            name=f"{provider}:{model or 'unknown'}",
            started_at=started,
            ended_at=time.time(),
            inputs=inputs,
            outputs=outputs,
            error=repr(error) if error is not None else None,
            metadata={"transport": "httpx"},
        )
    )


def _summarize_response(provider: str, body: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    # OpenAI-style choices
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") or {}
        text = msg.get("content")
        if isinstance(text, str):
            out["text"] = _truncate(text)
        if "finish_reason" in choices[0]:
            out["finish_reason"] = choices[0]["finish_reason"]
    # Anthropic-style content
    content = body.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        text = content[0].get("text")
        if isinstance(text, str):
            out["text"] = _truncate(text)
        if "stop_reason" in body:
            out["stop_reason"] = body["stop_reason"]
    # Gemini-style candidates
    candidates = body.get("candidates")
    if isinstance(candidates, list) and candidates:
        cnt = candidates[0].get("content") or {}
        parts = cnt.get("parts") or []
        if parts and isinstance(parts[0], dict):
            text = parts[0].get("text")
            if isinstance(text, str):
                out["text"] = _truncate(text)
    # Usage — three different shapes in 2026:
    #   Anthropic:  body.usage.input_tokens / output_tokens
    #   OpenAI:     body.usage.prompt_tokens / completion_tokens
    #   Gemini:     body.usageMetadata.promptTokenCount / candidatesTokenCount
    # We accept all three so cost + attribution work cross-provider.
    usage = body.get("usage")
    if isinstance(usage, dict):
        out.setdefault(
            "input_tokens",
            usage.get("input_tokens") or usage.get("prompt_tokens"),
        )
        out.setdefault(
            "output_tokens",
            usage.get("output_tokens") or usage.get("completion_tokens"),
        )
    gemini_usage = body.get("usageMetadata")
    if isinstance(gemini_usage, dict):
        out.setdefault(
            "input_tokens",
            gemini_usage.get("promptTokenCount"),
        )
        out.setdefault(
            "output_tokens",
            gemini_usage.get("candidatesTokenCount"),
        )
    # Detect 429 / rate-limit responses as a first-class signal — this
    # was 60 % of agent errors in early 2026 (Datadog State of AI 2026).
    # We tag the step so the dashboard + `loupe list` can surface it.
    status = out.get("status")
    if status == 429:
        out["rate_limited"] = True
    elif (
        isinstance(body.get("error"), dict)
        and body["error"].get("code") == 429
    ):
        # Gemini returns 429 inside the body, not the HTTP status.
        out["rate_limited"] = True
        out.setdefault("status", 429)
    return out


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
