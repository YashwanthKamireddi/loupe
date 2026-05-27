"""``loupe proxy`` — universal HTTP capture tests.

These verify that the proxy:
  1. Forwards the request body, method, headers, and query string upstream.
  2. Resolves the upstream provider correctly (from --provider, Host, or path).
  3. Captures a Loupe trace with the same Step shape as the universal-httpx
     integration so the dashboard + cost + attribution see one consistent layout.
  4. Streams SSE chunks through unmodified, then reassembles the text for capture.
  5. Handles upstream failure cleanly (502 + a failed trace, not a crash).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from loupe.proxy import (  # noqa: E402
    assemble_sse_text,
    build_step,
    create_app,
    detect_provider_from_path,
    resolve_upstream,
)
from loupe.store import JSONLStore  # noqa: E402

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def loupe_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "loupe-home"
    home.mkdir()
    monkeypatch.setenv("LOUPE_HOME", str(home))
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
    from loupe import store as store_mod
    store_mod._default = None
    return home


def _read_one_trace(home: Path) -> dict:
    """Read the single captured trace file as {header, [step,...]}.

    Raises if zero or more than one trace was captured.
    """
    files = list((home / "traces").glob("*.jsonl"))
    assert len(files) == 1, f"expected 1 trace, found {len(files)}: {files}"
    lines = files[0].read_text().splitlines()
    header = json.loads(lines[0])
    steps = [json.loads(line) for line in lines[1:]]
    return {"header": header, "steps": steps}


def _store_for(home: Path) -> JSONLStore:
    """Construct a JSONLStore rooted at the test home, bypassing the global default."""
    return JSONLStore(root=home / "traces")


# ---------------------------------------------------------------------------
# Pure helpers — no server needed.
# ---------------------------------------------------------------------------


def test_detect_provider_from_path_anthropic() -> None:
    assert detect_provider_from_path("/v1/messages") == "anthropic"


def test_detect_provider_from_path_openai_chat() -> None:
    assert detect_provider_from_path("/v1/chat/completions") == "openai"


def test_detect_provider_from_path_gemini() -> None:
    assert detect_provider_from_path("/v1beta/models/gemini-2.5-pro:generateContent") == "gemini"


def test_detect_provider_from_path_unknown() -> None:
    assert detect_provider_from_path("/random/path") is None


def test_resolve_upstream_explicit_provider_wins() -> None:
    provider, url = resolve_upstream(
        inbound_host="api.openai.com",
        inbound_path="/v1/chat/completions",
        forced_provider="anthropic",
    )
    assert provider == "anthropic"
    assert url == "https://api.anthropic.com"


def test_resolve_upstream_unknown_provider_raises() -> None:
    with pytest.raises(LookupError, match="unknown provider"):
        resolve_upstream(
            inbound_host=None,
            inbound_path="/v1/messages",
            forced_provider="not-real",
        )


def test_resolve_upstream_by_path() -> None:
    provider, url = resolve_upstream(
        inbound_host="127.0.0.1:7878",
        inbound_path="/v1/messages",
        forced_provider=None,
    )
    assert provider == "anthropic"
    assert url == "https://api.anthropic.com"


def test_resolve_upstream_ambiguous_raises() -> None:
    with pytest.raises(LookupError, match="couldn't infer provider"):
        resolve_upstream(
            inbound_host="127.0.0.1",
            inbound_path="/health",
            forced_provider=None,
        )


def test_build_step_extracts_anthropic_model_from_body() -> None:
    step = build_step(
        provider="anthropic",
        method="POST",
        path="/v1/messages",
        request_body=json.dumps({
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
        }).encode(),
        response_status=200,
        response_body=json.dumps({
            "content": [{"type": "text", "text": "hello"}],
            "usage": {"input_tokens": 4, "output_tokens": 2},
        }).encode(),
        started_at=1.0,
        ended_at=1.5,
    )
    assert step.name == "anthropic:claude-haiku-4-5"
    assert step.inputs["model"] == "claude-haiku-4-5"
    assert step.inputs["max_tokens"] == 10
    assert step.outputs["text"] == "hello"
    assert step.outputs["input_tokens"] == 4
    assert step.outputs["output_tokens"] == 2
    assert step.metadata["transport"] == "proxy"


def test_build_step_extracts_gemini_model_from_path() -> None:
    step = build_step(
        provider="gemini",
        method="POST",
        path="/v1beta/models/gemini-2.5-pro:generateContent",
        request_body=json.dumps({"contents": [{"parts": [{"text": "hi"}]}]}).encode(),
        response_status=200,
        response_body=json.dumps({
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1},
        }).encode(),
        started_at=0.0,
        ended_at=0.1,
    )
    assert step.name == "gemini:gemini-2.5-pro"
    assert step.outputs["text"] == "ok"
    assert step.outputs["input_tokens"] == 3
    assert step.outputs["output_tokens"] == 1


def test_build_step_marks_429_rate_limited() -> None:
    step = build_step(
        provider="openai",
        method="POST",
        path="/v1/chat/completions",
        request_body=b"{}",
        response_status=429,
        response_body=b'{"error":"rate limit"}',
        started_at=0.0,
        ended_at=0.05,
    )
    assert step.outputs["rate_limited"] is True
    assert step.outputs["status"] == 429


def test_assemble_sse_text_anthropic() -> None:
    chunks = [
        b'data: {"type":"content_block_delta","delta":{"text":"hel"}}\n\n',
        b'data: {"type":"content_block_delta","delta":{"text":"lo"}}\n\n',
        b"data: [DONE]\n\n",
    ]
    assert assemble_sse_text(chunks, "anthropic") == "hello"


def test_assemble_sse_text_openai() -> None:
    chunks = [
        b'data: {"choices":[{"delta":{"content":"hi "}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"there"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    assert assemble_sse_text(chunks, "openai") == "hi there"


def test_assemble_sse_text_gemini_jsonlines() -> None:
    chunks = [
        b'{"candidates":[{"content":{"parts":[{"text":"foo"}]}}]}\n',
        b'{"candidates":[{"content":{"parts":[{"text":"bar"}]}}]}\n',
    ]
    assert assemble_sse_text(chunks, "gemini") == "foobar"


def test_assemble_sse_text_skips_malformed_frames() -> None:
    chunks = [
        b'data: not-json\n\n',
        b'data: {"type":"content_block_delta","delta":{"text":"ok"}}\n\n',
    ]
    assert assemble_sse_text(chunks, "anthropic") == "ok"


# ---------------------------------------------------------------------------
# End-to-end — proxy with a MockTransport upstream.
# ---------------------------------------------------------------------------


def _mock_client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_proxy_forwards_anthropic_post_and_captures_step(loupe_home: Path) -> None:
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "hi back"}],
                "usage": {"input_tokens": 3, "output_tokens": 2},
                "stop_reason": "end_turn",
            },
        )

    app = create_app(
        forced_provider="anthropic",
        store=_store_for(loupe_home),
        client=_mock_client(handler),
    )
    client = TestClient(app)

    body = {
        "model": "claude-haiku-4-5",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 32,
    }
    response = client.post(
        "/v1/messages",
        json=body,
        headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
    )

    assert response.status_code == 200
    assert response.json()["content"][0]["text"] == "hi back"

    # Upstream call check: URL, headers preserved.
    assert len(captured_requests) == 1
    upstream_req = captured_requests[0]
    assert str(upstream_req.url) == "https://api.anthropic.com/v1/messages"
    assert upstream_req.method == "POST"
    assert upstream_req.headers.get("x-api-key") == "test-key"
    assert upstream_req.headers.get("anthropic-version") == "2023-06-01"
    assert json.loads(upstream_req.content) == body

    # Captured trace check: one Step, anthropic provider, model + tokens parsed.
    captured = _read_one_trace(loupe_home)
    # name = provider (so `loupe list` distinguishes captures), framework = how.
    assert captured["header"]["name"] == "anthropic"
    assert captured["header"]["framework"] == "proxy"
    assert len(captured["steps"]) == 1
    step = captured["steps"][0]
    assert step["kind"] == "llm-call"
    assert step["name"] == "anthropic:claude-haiku-4-5"
    assert step["inputs"]["model"] == "claude-haiku-4-5"
    assert step["outputs"]["status"] == 200
    assert step["outputs"]["text"] == "hi back"
    assert step["outputs"]["input_tokens"] == 3
    assert step["outputs"]["output_tokens"] == 2
    assert step["metadata"]["transport"] == "proxy"
    assert step["metadata"]["path"] == "/v1/messages"
    # Regression test (v0.0.58): captured `messages` must be a native JSON
    # list, NOT a Python `repr()` string. Earlier `_truncate` stringified
    # every list with single-quote repr, breaking JSON downstream.
    assert step["inputs"]["messages"] == [{"role": "user", "content": "hi"}]
    assert isinstance(step["inputs"]["messages"], list)


def test_proxy_autodetect_provider_from_path(loupe_home: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": [
            {"content": {"parts": [{"text": "ok"}]}}
        ]})

    app = create_app(
        store=_store_for(loupe_home),
        client=_mock_client(handler),
    )
    client = TestClient(app)
    response = client.post(
        "/v1beta/models/gemini-2.5-pro:generateContent",
        json={"contents": [{"parts": [{"text": "hi"}]}]},
    )
    assert response.status_code == 200
    captured = _read_one_trace(loupe_home)
    step = captured["steps"][0]
    assert step["name"] == "gemini:gemini-2.5-pro"


def test_proxy_unknown_path_returns_400_without_capturing(loupe_home: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream must not be called for unknown paths")

    app = create_app(
        store=_store_for(loupe_home),
        client=_mock_client(handler),
    )
    client = TestClient(app)
    response = client.get("/totally/unknown")
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "loupe_proxy_unknown_provider"
    # No trace file should have been written.
    assert list((loupe_home / "traces").glob("*.jsonl")) == []


def test_proxy_upstream_failure_returns_502_and_records_failed_trace(
    loupe_home: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network error", request=request)

    app = create_app(
        forced_provider="anthropic",
        store=_store_for(loupe_home),
        client=_mock_client(handler),
    )
    client = TestClient(app)
    response = client.post("/v1/messages", json={"model": "x", "messages": []})
    assert response.status_code == 502
    captured = _read_one_trace(loupe_home)
    assert captured["header"]["metadata"]["failed"] is True


def test_proxy_streamed_sse_passes_through_and_captures_assembled_text(
    loupe_home: Path,
) -> None:
    """Streaming responses should be relayed chunk-by-chunk and captured
    as a normal step with `outputs.text` containing the reassembled stream."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            b'data: {"type":"content_block_delta","delta":{"text":"hello "}}\n\n'
            b'data: {"type":"content_block_delta","delta":{"text":"world"}}\n\n'
            b"data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/event-stream"},
        )

    app = create_app(
        forced_provider="anthropic",
        store=_store_for(loupe_home),
        client=_mock_client(handler),
    )
    client = TestClient(app)
    response = client.post(
        "/v1/messages",
        json={"model": "claude-haiku-4-5", "messages": [], "stream": True},
    )
    assert response.status_code == 200
    # Body is forwarded verbatim — caller sees the same SSE bytes.
    assert b"hello " in response.content
    assert b"world" in response.content

    captured = _read_one_trace(loupe_home)
    step = captured["steps"][0]
    assert step["inputs"]["stream"] is True
    assert step["outputs"]["text"] == "hello world"


def test_proxy_tail_callback_fires_for_each_capture(loupe_home: Path) -> None:
    """--tail mode hooks every persisted Step. The callback receives the
    Step + the trace_id it was saved under, and exceptions in the callback
    must not break capture."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
        )

    seen: list[tuple[str, str]] = []

    def tail(step, trace_id):  # type: ignore[no-untyped-def]
        seen.append((step.name, trace_id))
        raise RuntimeError("tail callback errors must NOT break capture")

    app = create_app(
        forced_provider="anthropic",
        store=_store_for(loupe_home),
        client=_mock_client(handler),
        tail=tail,
    )
    client = TestClient(app)
    for _ in range(3):
        r = client.post(
            "/v1/messages",
            json={"model": "claude-haiku-4-5", "messages": []},
        )
        assert r.status_code == 200

    assert len(seen) == 3
    for name, trace_id in seen:
        assert name == "anthropic:claude-haiku-4-5"
        assert isinstance(trace_id, str) and len(trace_id) >= 12

    # All three traces must still have landed on disk despite the
    # exception in the tail callback.
    assert len(list((loupe_home / "traces").glob("*.jsonl"))) == 3


def test_proxy_health_endpoint_no_capture(loupe_home: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream must not be called for /_loupe/health")

    app = create_app(
        store=_store_for(loupe_home),
        client=_mock_client(handler),
    )
    client = TestClient(app)
    res = client.get("/_loupe/health")
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is True
    assert "version" in payload
    assert list((loupe_home / "traces").glob("*.jsonl")) == []
