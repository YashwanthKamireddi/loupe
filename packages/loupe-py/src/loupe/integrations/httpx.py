"""Universal HTTP-level capture — works with ANY Python client that uses httpx.

Most modern Python LLM SDKs (anthropic, openai, mistralai, google-genai,
groq, instructor, dspy, etc.) use httpx under the hood. This integration
patches `httpx.Client.send` / `httpx.AsyncClient.send` once, sniffs the
target URL, and records a Loupe Step for each call to a known provider.

Use this when:
- Your library doesn't have a Loupe direct integration yet (e.g. mistral,
  groq, llama-stack-client, custom internal proxy).
- You want one switch that captures *everything*.

Don't use this *in addition to* the direct anthropic/openai integrations —
you'd double-record. Pick one.
"""

from __future__ import annotations

import functools
import json as _json
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from loupe.trace import Step, current_trace

_PATCHED_FLAG = "__loupe_patched__"

# Map of host suffixes → provider label. Add new providers freely.
_PROVIDERS = {
    "api.anthropic.com": "anthropic",
    "api.openai.com": "openai",
    "api.mistral.ai": "mistral",
    "api.groq.com": "groq",
    "generativelanguage.googleapis.com": "gemini",
    "api.cohere.ai": "cohere",
    "api.together.xyz": "together",
    "openrouter.ai": "openrouter",
    "api.fireworks.ai": "fireworks",
    "api.deepseek.com": "deepseek",
    "api.x.ai": "xai",
    "api.perplexity.ai": "perplexity",
    "localhost": "local",  # Ollama, vLLM, etc.
    "127.0.0.1": "local",
}


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


def _wrap_sync(original: Any) -> Any:
    @functools.wraps(original)
    def wrapper(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.time()
        provider = _detect_provider(request)
        if not provider or current_trace() is None:
            return original(self, request, *args, **kwargs)

        body = _safe_read_request_body(request)
        error: BaseException | None = None
        response = None
        try:
            response = original(self, request, *args, **kwargs)
            return response
        except BaseException as exc:
            error = exc
            raise
        finally:
            _emit(provider, request, body, response, error, started)

    setattr(wrapper, _PATCHED_FLAG, True)
    return wrapper


def _wrap_async(original: Any) -> Any:
    @functools.wraps(original)
    async def wrapper(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.time()
        provider = _detect_provider(request)
        if not provider or current_trace() is None:
            return await original(self, request, *args, **kwargs)

        body = _safe_read_request_body(request)
        error: BaseException | None = None
        response = None
        try:
            response = await original(self, request, *args, **kwargs)
            return response
        except BaseException as exc:
            error = exc
            raise
        finally:
            _emit(provider, request, body, response, error, started)

    setattr(wrapper, _PATCHED_FLAG, True)
    return wrapper


def _detect_provider(request: Any) -> str | None:
    try:
        host = urlparse(str(request.url)).hostname
    except Exception:
        return None
    if not host:
        return None
    host = host.lower()
    for suffix, label in _PROVIDERS.items():
        if host == suffix or host.endswith("." + suffix):
            return label
    return None


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

    model = (body or {}).get("model") if isinstance(body, dict) else None
    inputs: dict[str, Any] = {"provider": provider, "model": model}
    if isinstance(body, dict):
        if "messages" in body:
            inputs["messages"] = _truncate(body["messages"])
        if "prompt" in body:
            inputs["prompt"] = _truncate(body["prompt"])
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
    # Usage
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
