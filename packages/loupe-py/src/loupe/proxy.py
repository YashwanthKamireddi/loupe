"""``loupe proxy`` — universal local HTTP proxy that captures any LLM call.

This is Loupe's "any language, any agent, zero code" surface. Point your
provider's standard base-URL env var at the local proxy and **every**
HTTP call gets captured as a Loupe trace, no matter which language or
framework made it.

    $ loupe proxy &
    $ set -x ANTHROPIC_BASE_URL  http://127.0.0.1:7878
    $ set -x OPENAI_BASE_URL     http://127.0.0.1:7878/v1
    $ set -x GEMINI_BASE_URL     http://127.0.0.1:7878

    python my_agent.py          # captured
    node my-agent.js            # captured
    go run my-agent.go          # captured
    curl http://127.0.0.1:7878/v1/messages ...  # captured

Forwards everything (headers, method, body, streaming chunks) to the
real upstream provider, byte-for-byte. The provider is inferred from
either:

  1. The `Host` header in the inbound request, when the caller set the
     full base URL (e.g. `https://api.anthropic.com`).
  2. The request *path* — `/v1/messages` → Anthropic, `/v1/chat/...`
     and `/v1/responses` → OpenAI, `/v1beta/...` and `/v1/models/...`
     → Gemini.
  3. The `--provider` flag, which pins everything to one upstream.

Streaming responses are passed through chunk-by-chunk via SSE so the
client sees zero added latency on the first token. Capture happens at
end-of-stream by reassembling the chunks — same code path as the
direct-SDK integrations.
"""

from __future__ import annotations

import json as _json
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from loupe._redact import redact
from loupe._version import __version__
from loupe.integrations._providers import detect_provider_from_host
from loupe.store import Store, default_store
from loupe.trace import Step, Trace, _begin_trace, _finish_trace

# FastAPI is only required when the user actually runs `loupe proxy`. The
# pure helpers (assemble_sse_text, build_step, resolve_upstream) work
# without it, so we keep the import optional. `Request` must still appear
# in the *module-level* namespace so FastAPI's type-hint resolver can find
# it when annotating the catch-all route handler — `from __future__ import
# annotations` turns the annotation into a string and FastAPI then needs
# the real class via the module globals.
try:
    from fastapi import Request as Request  # noqa: F401  (re-exported for annotations)
except ImportError:  # pragma: no cover
    if TYPE_CHECKING:
        from fastapi import Request  # noqa: F401
    else:
        Request = Any  # type: ignore[assignment,misc]


# Default upstream per provider label. We point at the canonical public
# endpoint; users can override per-request via the inbound Host header.
DEFAULT_UPSTREAMS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai":    "https://api.openai.com",
    "gemini":    "https://generativelanguage.googleapis.com",
    "mistral":   "https://api.mistral.ai",
    "groq":      "https://api.groq.com",
    "cohere":    "https://api.cohere.com",
    "together":  "https://api.together.xyz",
    "deepseek":  "https://api.deepseek.com",
    "xai":       "https://api.x.ai",
    "openrouter": "https://openrouter.ai",
    "perplexity": "https://api.perplexity.ai",
}


# Path patterns that identify a provider when we don't have a Host hint.
# Order matters — first match wins.
_PATH_HINTS: list[tuple[str, str]] = [
    ("/v1/messages",            "anthropic"),
    ("/v1/complete",            "anthropic"),
    ("/v1/chat/completions",    "openai"),
    ("/v1/responses",           "openai"),
    ("/v1/embeddings",          "openai"),
    ("/v1beta/models/",         "gemini"),
    ("/v1/models/",             "gemini"),
    ("/openai/v1/chat",         "groq"),
]


def detect_provider_from_path(path: str) -> str | None:
    """Best-effort path → provider mapping. Returns None when ambiguous."""
    for hint, provider in _PATH_HINTS:
        if path.startswith(hint) or hint in path:
            return provider
    return None


def resolve_upstream(
    *,
    inbound_host: str | None,
    inbound_path: str,
    forced_provider: str | None,
) -> tuple[str, str]:
    """Decide the upstream base URL + provider label for a request.

    Returns ``(provider_label, upstream_base_url)``. Raises ``LookupError``
    if we can't infer the provider — the caller should respond 400 with
    a clear message.
    """
    # 1. Explicit --provider flag wins.
    if forced_provider:
        upstream = DEFAULT_UPSTREAMS.get(forced_provider.lower())
        if not upstream:
            raise LookupError(
                f"unknown provider '{forced_provider}'. "
                f"known: {', '.join(sorted(DEFAULT_UPSTREAMS))}"
            )
        return forced_provider.lower(), upstream

    # 2. Inbound Host header — if the caller set a full base URL like
    #    https://api.anthropic.com, httpx still rewrites the request to
    #    point at us but preserves the original Host. Use it.
    if inbound_host:
        matched = detect_provider_from_host(inbound_host)
        if matched is not None:
            up = DEFAULT_UPSTREAMS.get(matched.label)
            if up:
                return matched.label, up

    # 3. Path heuristic — last resort, works for `/v1/messages` etc.
    by_path = detect_provider_from_path(inbound_path)
    if by_path:
        return by_path, DEFAULT_UPSTREAMS[by_path]

    raise LookupError(
        "couldn't infer provider from request. "
        "pass --provider, or set the upstream base URL explicitly."
    )


# ---------------------------------------------------------------------------
# Step extraction — reuses the same shape `integrations/httpx._emit` produces
# so the dashboard, cost report, and attribution don't need a separate code
# path for proxy-captured traces.
# ---------------------------------------------------------------------------


_MAX_BODY_BYTES = 256 * 1024  # cap inputs/outputs we record; full bytes still proxied


def _safe_json(payload: bytes | str | None) -> dict | None:
    if not payload:
        return None
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8", errors="replace")
        except Exception:
            return None
    try:
        parsed = _json.loads(payload)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _truncate(value: Any, *, limit: int = 4000) -> Any:
    """Cap a value's serialized size at ``limit`` bytes.

    Lists + dicts stay as native structures when their JSON form fits, so
    the dashboard sees a real list (not a ``repr`` string it would have
    to re-parse). Only stringifies on actual overflow.
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
            return value
        return text[:limit] + "…[truncated]"
    try:
        text = repr(value)
    except Exception:
        text = f"<{type(value).__name__}>"
    return text if len(text) <= limit else text[:limit] + "…[truncated]"


def extract_model(path: str, body: dict | None) -> str | None:
    """Pull a model name from the request body or, for Gemini, the path."""
    if isinstance(body, dict) and isinstance(body.get("model"), str):
        return body["model"]
    # Gemini: /v1beta/models/gemini-2.5-pro:generateContent
    if "/models/" in path:
        tail = path.split("/models/", 1)[1]
        return tail.split(":", 1)[0].split("/", 1)[0] or None
    return None


def build_step(
    *,
    provider: str,
    method: str,
    path: str,
    request_body: bytes | None,
    response_status: int | None,
    response_body: bytes | None,
    started_at: float,
    ended_at: float,
    error: str | None = None,
    streamed: bool = False,
) -> Step:
    """Turn a captured request/response pair into a Loupe Step.

    Same field layout as the universal-httpx integration so downstream
    consumers (cost, attribution, dashboard) treat proxy steps as
    indistinguishable from in-process captures.
    """
    from loupe._multimodal import (
        extract_tool_calls_from_messages,
        extract_tool_calls_from_response,
        scrub_media,
    )

    req = _safe_json(request_body)
    resp = _safe_json(response_body)
    model = extract_model(path, req)

    inputs: dict[str, Any] = {"provider": provider, "model": model}
    if isinstance(req, dict):
        if "messages" in req:
            scrubbed_msgs = scrub_media(req["messages"])
            inputs["messages"] = _truncate(redact(scrubbed_msgs))
            tcs = extract_tool_calls_from_messages(scrubbed_msgs)
            if tcs:
                inputs["tool_calls"] = _truncate(tcs)
        if "prompt" in req:
            inputs["prompt"] = _truncate(redact(req["prompt"]))
        if "max_tokens" in req:
            inputs["max_tokens"] = req["max_tokens"]
        if req.get("stream"):
            inputs["stream"] = True
        if "contents" in req:
            inputs["contents"] = _truncate(redact(scrub_media(req["contents"])))
    if streamed:
        inputs["stream"] = True

    outputs: dict[str, Any] = {"status": response_status}
    if isinstance(resp, dict):
        outputs.update(_summarize_response(resp))
        tool_calls = extract_tool_calls_from_response(provider, resp)
        if tool_calls:
            outputs["tool_calls"] = _truncate(tool_calls)
    if response_status == 429:
        outputs["rate_limited"] = True
    elif (
        isinstance(resp, dict)
        and isinstance(resp.get("error"), dict)
        and resp["error"].get("code") == 429
    ):
        outputs["rate_limited"] = True
        outputs.setdefault("status", 429)

    return Step(
        step_id=uuid.uuid4().hex[:12],
        parent_step_id=None,
        kind="llm-call",
        name=f"{provider}:{model or 'unknown'}",
        started_at=started_at,
        ended_at=ended_at,
        inputs=inputs,
        outputs=outputs,
        error=error,
        metadata={"transport": "proxy", "method": method, "path": path},
    )


def _summarize_response(body: dict) -> dict[str, Any]:
    """Same vocabulary as integrations/httpx._summarize_response — keep in sync."""
    out: dict[str, Any] = {}
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") or {}
        text = msg.get("content")
        if isinstance(text, str):
            out["text"] = _truncate(text)
        if "finish_reason" in choices[0]:
            out["finish_reason"] = choices[0]["finish_reason"]
    content = body.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        text = content[0].get("text")
        if isinstance(text, str):
            out["text"] = _truncate(text)
        if "stop_reason" in body:
            out["stop_reason"] = body["stop_reason"]
    candidates = body.get("candidates")
    if isinstance(candidates, list) and candidates:
        cnt = candidates[0].get("content") or {}
        parts = cnt.get("parts") or []
        if parts and isinstance(parts[0], dict):
            text = parts[0].get("text")
            if isinstance(text, str):
                out["text"] = _truncate(text)
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
        out.setdefault("input_tokens", gemini_usage.get("promptTokenCount"))
        out.setdefault("output_tokens", gemini_usage.get("candidatesTokenCount"))
    return out


# ---------------------------------------------------------------------------
# Streaming-chunk assembly. SSE / NDJSON streams arrive as many small frames;
# we forward each frame immediately to the client AND keep a running buffer
# so the captured Step shows the assembled final text.
# ---------------------------------------------------------------------------


def assemble_sse_text(chunks: list[bytes], provider: str) -> str:
    """Reassemble streamed token chunks into a single text block.

    Handles three SSE variants in production today:
      - Anthropic:  `data: {"type":"content_block_delta", "delta":{"text":"..."}}`
      - OpenAI:     `data: {"choices":[{"delta":{"content":"..."}}]}`
      - Gemini:     JSON-lines, each line a `candidates[0].content.parts[*].text`

    Unknown shapes are skipped silently — better to show partial output
    than to crash the proxy on a provider format change.
    """
    out: list[str] = []
    raw = b"".join(chunks).decode("utf-8", errors="replace")
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line in ("[DONE]", ""):
            continue
        try:
            obj = _json.loads(line)
        except Exception:
            continue
        # Anthropic
        if obj.get("type") == "content_block_delta":
            delta = obj.get("delta") or {}
            if isinstance(delta.get("text"), str):
                out.append(delta["text"])
        # OpenAI
        choices = obj.get("choices")
        if isinstance(choices, list) and choices:
            delta = choices[0].get("delta") or {}
            if isinstance(delta.get("content"), str):
                out.append(delta["content"])
        # Gemini
        candidates = obj.get("candidates")
        if isinstance(candidates, list) and candidates:
            cnt = candidates[0].get("content") or {}
            parts = cnt.get("parts") or []
            for p in parts:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    out.append(p["text"])
    return "".join(out)


# ---------------------------------------------------------------------------
# Server — built on FastAPI/Starlette so we get streaming responses for free.
# fastapi + uvicorn + httpx are required core deps since v0.0.66.
# ---------------------------------------------------------------------------


# Callback signature for the optional --tail printout. Receives the Step
# we just persisted (post-extraction so token counts are populated) plus
# the trace_id it was saved under, so a future feature can link them up
# in the dashboard.
TailCallback = Callable[["Step", str], None]


def create_app(
    *,
    forced_provider: str | None = None,
    store: Store | None = None,
    upstream_override: str | None = None,
    timeout_seconds: float = 600.0,
    client: Any | None = None,
    tail: TailCallback | None = None,
) -> Any:
    """Build the FastAPI app that powers ``loupe proxy``.

    Args:
        forced_provider: pin every request to this provider; bypasses detection.
        store: where captured traces are saved (defaults to ~/.loupe).
        upstream_override: replace the resolved upstream with this URL.
        timeout_seconds: HTTPX client timeout for forwarded requests.
        client: optional pre-built ``httpx.AsyncClient`` (used by tests to
            inject a mock transport).
        tail: optional callback invoked after every successful capture, used
            by ``loupe proxy --tail`` to print a one-line summary as each
            request lands.
    """
    try:
        from contextlib import asynccontextmanager

        import httpx
        from fastapi import FastAPI
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "loupe proxy needs fastapi + httpx. "
            "reinstall with: pip install --upgrade loupe"
        ) from exc

    target_store: Store = store or default_store()
    if client is None:
        client = httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)

    @asynccontextmanager
    async def _lifespan(_app: Any) -> Any:
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(
        title="Loupe proxy",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )

    @app.get("/_loupe/health")
    async def health() -> dict:
        return {
            "ok": True,
            "version": __version__,
            "provider_pin": forced_provider,
            "upstream_override": upstream_override,
        }

    # Catch-all route — any path, any method, forwards upstream.
    # We deliberately drop the response-model annotation: FastAPI would
    # otherwise try to validate our Starlette Response as a pydantic schema
    # and fail with 422.
    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        response_model=None,
    )
    async def forward(full_path: str, request: Request):  # type: ignore[no-untyped-def]
        return await _handle_forward(
            request=request,
            full_path=full_path,
            client=client,
            store=target_store,
            forced_provider=forced_provider,
            upstream_override=upstream_override,
            tail=tail,
        )

    return app


async def _handle_forward(
    *,
    request: Any,
    full_path: str,
    client: Any,
    store: Store,
    forced_provider: str | None,
    upstream_override: str | None,
    tail: TailCallback | None = None,
) -> Any:
    """Forward one request upstream + capture it as a Loupe trace."""
    from fastapi.responses import Response, StreamingResponse

    inbound_host = request.headers.get("host")
    inbound_path = "/" + full_path if full_path else "/"
    raw_query = request.url.query

    try:
        provider, upstream = resolve_upstream(
            inbound_host=inbound_host,
            inbound_path=inbound_path,
            forced_provider=forced_provider,
        )
    except LookupError as exc:
        return Response(
            content=_json.dumps({
                "error": {
                    "type": "loupe_proxy_unknown_provider",
                    "message": str(exc),
                    "hint": "set --provider on `loupe proxy`, or use a "
                            "provider-aware base URL on the client side.",
                }
            }),
            media_type="application/json",
            status_code=400,
        )

    if upstream_override:
        upstream = upstream_override

    # Build the upstream URL by swapping the base. Preserve path + query.
    upstream_url = _join_url(upstream, inbound_path, raw_query)

    # Read inbound body in full — we need it twice (forward + capture).
    body_bytes = await request.body()

    # Strip hop-by-hop + host headers. Keep auth headers (X-API-Key,
    # Authorization, etc.) — the proxy's job is transparency.
    forward_headers = _strip_hop_headers(dict(request.headers))
    forward_headers.pop("host", None)
    forward_headers.pop("content-length", None)  # httpx recomputes

    body_for_capture = body_bytes if len(body_bytes) <= _MAX_BODY_BYTES else None
    started = time.time()

    # Stream the upstream response straight through to the client so the
    # caller's first-token latency matches a direct call.
    try:
        upstream_req = client.build_request(
            method=request.method,
            url=upstream_url,
            headers=forward_headers,
            content=body_bytes,
        )
        upstream_resp = await client.send(upstream_req, stream=True)
    except Exception as exc:
        # Network failure — record the failed step and respond 502.
        _persist_step(
            store=store,
            provider=provider,
            method=request.method,
            path=inbound_path,
            request_body=body_for_capture,
            response_status=502,
            response_body=None,
            started=started,
            error=repr(exc),
            streamed=False,
            tail=tail,
        )
        return Response(
            content=_json.dumps({
                "error": {
                    "type": "loupe_proxy_upstream_unreachable",
                    "message": f"could not reach {upstream}: {exc}",
                }
            }),
            media_type="application/json",
            status_code=502,
        )

    streamed = _looks_streamed(upstream_resp)
    response_headers = _strip_hop_headers(dict(upstream_resp.headers))

    if streamed:
        captured_chunks: list[bytes] = []
        # If the upstream wrapped pre-buffered bytes (mock transports, some
        # proxies, retried requests), `aiter_raw()` would raise
        # ``StreamConsumed``. Detect that up-front and fall back to a
        # single-yield of the already-read content.
        import httpx as _httpx

        async def relay() -> Any:
            try:
                if upstream_resp.is_stream_consumed:
                    data = upstream_resp.content
                    if data:
                        captured_chunks.append(data)
                        yield data
                else:
                    try:
                        async for chunk in upstream_resp.aiter_raw():
                            if chunk:
                                if sum(len(c) for c in captured_chunks) < _MAX_BODY_BYTES:
                                    captured_chunks.append(chunk)
                                yield chunk
                    except _httpx.StreamConsumed:
                        data = upstream_resp.content
                        if data:
                            captured_chunks.append(data)
                            yield data
            finally:
                await upstream_resp.aclose()
                # Build a synthetic JSON body summarizing the stream so
                # downstream tooling (cost, attribution) treats it like a
                # normal response.
                text = assemble_sse_text(captured_chunks, provider)
                synthetic = _synthesize_streamed_body(provider, text)
                _persist_step(
                    store=store,
                    provider=provider,
                    method=request.method,
                    path=inbound_path,
                    request_body=body_for_capture,
                    response_status=upstream_resp.status_code,
                    response_body=_json.dumps(synthetic).encode("utf-8"),
                    started=started,
                    error=None,
                    streamed=True,
                    tail=tail,
                )

        return StreamingResponse(
            relay(),
            status_code=upstream_resp.status_code,
            headers=response_headers,
            media_type=response_headers.get("content-type"),
        )

    # Non-streaming: buffer the whole body, capture, return.
    full_body = await upstream_resp.aread()
    await upstream_resp.aclose()
    capture_body = full_body if len(full_body) <= _MAX_BODY_BYTES else None
    _persist_step(
        store=store,
        provider=provider,
        method=request.method,
        path=inbound_path,
        request_body=body_for_capture,
        response_status=upstream_resp.status_code,
        response_body=capture_body,
        started=started,
        error=None,
        streamed=False,
        tail=tail,
    )
    return Response(
        content=full_body,
        status_code=upstream_resp.status_code,
        headers=response_headers,
        media_type=response_headers.get("content-type"),
    )


def _synthesize_streamed_body(provider: str, text: str) -> dict:
    """Fabricate a non-streamed response body equivalent for capture only.

    The bytes returned to the client are the real upstream stream; this
    synthetic body is *only* what we store in the trace, so cost +
    attribution can read `outputs.text` exactly like they do for
    non-streamed responses.
    """
    if provider == "anthropic":
        return {"content": [{"type": "text", "text": text}]}
    if provider == "openai":
        return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
    if provider == "gemini":
        return {
            "candidates": [
                {"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}
            ]
        }
    return {"text": text}


def _persist_step(
    *,
    store: Store,
    provider: str,
    method: str,
    path: str,
    request_body: bytes | None,
    response_status: int | None,
    response_body: bytes | None,
    started: float,
    error: str | None,
    streamed: bool,
    tail: TailCallback | None = None,
) -> None:
    """Wrap the captured request/response in a tiny one-step trace + save it.

    If a ``tail`` callback is supplied it is invoked AFTER persistence — any
    exception it raises is swallowed so a broken printer never breaks
    capture (the JSONL is the source of truth, not the printout).
    """
    ended = time.time()
    step = build_step(
        provider=provider,
        method=method,
        path=path,
        request_body=request_body,
        response_status=response_status,
        response_body=response_body,
        started_at=started,
        ended_at=ended,
        error=error,
        streamed=streamed,
    )
    # name = which provider (more useful in `loupe list`), framework = how
    # this trace was captured. Same convention as the autopatch path.
    trace: Trace = _begin_trace(provider, "proxy")
    trace.add_step(step)
    if error or (response_status is not None and response_status >= 500):
        trace.metadata["failed"] = True
        if error:
            trace.metadata["error"] = error
    _finish_trace(trace, store)
    if tail is not None:
        import contextlib
        with contextlib.suppress(Exception):
            tail(step, trace.trace_id)


def _join_url(upstream: str, path: str, query: str) -> str:
    base = upstream.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    url = base + path
    if query:
        url = f"{url}?{query}"
    return url


_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


def _strip_hop_headers(headers: dict[str, str]) -> dict[str, str]:
    """Remove hop-by-hop headers that must not be forwarded across a proxy.

    RFC 7230 §6.1. Otherwise the upstream sees keepalive / chunked-encoding
    state from the wrong side of the hop and breaks streaming.
    """
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _HOP_BY_HOP:
            continue
        out[k] = v
    return out


def _looks_streamed(response: Any) -> bool:
    """Decide whether to relay the response chunk-by-chunk vs buffer it.

    SSE responses use `text/event-stream`. Some providers also return
    `application/x-ndjson`. Anything else is treated as buffered JSON.
    """
    ct = (response.headers.get("content-type") or "").lower()
    if "text/event-stream" in ct:
        return True
    return "application/x-ndjson" in ct


# ---------------------------------------------------------------------------
# Entry point used by the CLI command. Kept here (not in cli.py) so the
# server can also be embedded by other tools — e.g. our test suite.
# ---------------------------------------------------------------------------


def run(
    *,
    host: str = "127.0.0.1",
    port: int = 7878,
    forced_provider: str | None = None,
    upstream_override: str | None = None,
    log_level: str = "warning",
    tail: TailCallback | None = None,
) -> None:
    """Block on a Uvicorn server hosting the proxy app."""
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "loupe proxy needs uvicorn. reinstall with: pip install --upgrade loupe"
        ) from exc

    app = create_app(
        forced_provider=forced_provider,
        upstream_override=upstream_override,
        tail=tail,
    )
    uvicorn.run(app, host=host, port=port, log_level=log_level)
