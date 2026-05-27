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
    """Decide whether the wrapper should auto-create an implicit
    one-call trace when no @trace context is active.

    Resolution order (first match wins):

      1. ``LOUPE_AUTOPATCH=0`` / ``false`` / ``no`` / ``off`` → OFF
         (explicit opt-out; always honored)
      2. ``LOUPE_AUTOPATCH=1`` / ``true`` / ``yes`` / ``on``  → ON
         (explicit opt-in; useful before ``loupe setup`` has run)
      3. Env var unset:
           - If ``~/.loupe/config.toml`` exists → ON (user ran setup;
             they want capture)
           - Otherwise → OFF (probably a transitive install; don't
             surprise people)

    This rule keeps the install path frictionless — ``pip install loupe
    && loupe setup`` and every LLM call from any Python script captures
    automatically — while staying safe for libraries that depend on
    Loupe but don't expect it to be active.
    """
    raw = os.environ.get("LOUPE_AUTOPATCH")
    if raw is not None:
        norm = raw.strip().lower()
        if norm in ("1", "true", "yes", "on"):
            return True
        if norm in ("0", "false", "no", "off", ""):
            return False
        # Unknown value → treat as opt-out (safer default)
        return False
    # Env var unset → defer to whether the user has set up Loupe.
    try:
        from loupe.config import config_path
        return config_path().exists()
    except Exception:
        return False


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
    import sys
    from pathlib import Path

    from loupe.store import default_store
    from loupe.trace import _begin_trace, _current_trace, _finish_trace

    # Name the implicit trace after the script that triggered it so
    # `loupe list` distinguishes captures across many invocations.
    # Falls back to "auto" if argv[0] isn't a useful filename (REPL,
    # `python -c`, embedded interpreter).
    script_name = "auto"
    try:
        if sys.argv and sys.argv[0] and sys.argv[0] not in ("-c", "-"):
            stem = Path(sys.argv[0]).stem
            if stem and stem not in ("python", "python3"):
                script_name = stem
    except Exception:  # noqa: BLE001 — best-effort naming, never break capture
        pass
    # name = what was captured, framework = how. TS side uses the same
    # convention so cross-language traces feel coherent in the dashboard.
    t = _begin_trace(script_name, "autopatch")
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

    from loupe._multimodal import (
        extract_tool_calls_from_messages,
        extract_tool_calls_from_response,
        scrub_media,
    )

    model = _extract_model(request, body)
    inputs: dict[str, Any] = {"provider": provider, "model": model}
    if isinstance(body, dict):
        if "messages" in body:
            # scrub_media → strip inline image/audio bytes BEFORE redact +
            # truncate. Keeps the JSONL small + the structure intact.
            scrubbed_msgs = scrub_media(body["messages"])
            inputs["messages"] = _truncate(redact(scrubbed_msgs))
            tcs = extract_tool_calls_from_messages(scrubbed_msgs)
            if tcs:
                inputs["tool_calls"] = _truncate(tcs)
        if "prompt" in body:
            inputs["prompt"] = _truncate(redact(body["prompt"]))
        if "max_tokens" in body:
            inputs["max_tokens"] = body["max_tokens"]
        if body.get("stream"):
            inputs["stream"] = True
        # Gemini-style multimodal content sits under `contents`, not
        # `messages` — scrub it the same way.
        if "contents" in body:
            inputs["contents"] = _truncate(redact(scrub_media(body["contents"])))

    outputs: dict[str, Any] = {}
    # When the HTTP call failed (4xx/5xx) the provider returns an error
    # BODY ("API key not valid", "rate limit exceeded", "context length
    # exceeded", …) — that message is the whole point of a forensic
    # capture. Surface it both in outputs and as the step's error so
    # `loupe show` / the dashboard lead with the cause, not just a code.
    step_error: str | None = repr(error) if error is not None else None
    if response is not None:
        status = getattr(response, "status_code", None)
        outputs["status"] = status
        # Only try to decode JSON for non-streaming responses
        if not bool(inputs.get("stream")):
            try:
                payload = response.json()
                outputs.update(_summarize_response(provider, payload))
                tool_calls = extract_tool_calls_from_response(provider, payload)
                if tool_calls:
                    outputs["tool_calls"] = _truncate(tool_calls)
                if isinstance(status, int) and status >= 400:
                    msg = _extract_error_message(payload)
                    if msg:
                        outputs["error"] = _truncate(msg)
                        if step_error is None:
                            step_error = f"HTTP {status}: {msg}"[:500]
            except Exception:
                pass
        # Even when the body isn't JSON, a failed status is itself an error.
        if step_error is None and isinstance(status, int) and status >= 400:
            step_error = f"HTTP {status}"

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
            error=step_error,
            metadata={"transport": "httpx"},
        )
    )


def _extract_error_message(body: Any) -> str | None:
    """Pull a human-readable error message out of a provider error body.

    Handles the common shapes — all three major providers nest the
    message under ``error``:
      OpenAI/Gemini : {"error": {"message": "...", "code": 400, ...}}
      Anthropic     : {"type": "error", "error": {"message": "..."}}
    Falls back to a stringified body so nothing is ever lost.
    """
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
        if isinstance(err, str) and err.strip():
            return err.strip()
        # Some providers put the message at the top level.
        msg = body.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
    if body:
        return str(body)
    return None


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
    """Cap an arbitrary value's serialized size at ``limit`` bytes.

    Lists + dicts are preserved as native structures whenever their JSON
    form fits — this keeps the wire format honest (a list stays a JSON
    list, not a Python ``repr`` string the dashboard would have to parse
    back). They're only stringified when they actually exceed ``limit``.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…[truncated]"
    if isinstance(value, (list, dict)):
        try:
            text = _json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = repr(value)
        if len(text) <= limit:
            # Native structure round-trips cleanly through json.dumps.
            return value
        return text[:limit] + "…[truncated]"
    try:
        text = repr(value)
    except Exception:
        text = f"<{type(value).__name__}>"
    return text if len(text) <= limit else text[:limit] + "…[truncated]"
