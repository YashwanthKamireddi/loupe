"""Tests for the universal httpx interceptor — captures any LLM call."""

from __future__ import annotations

import importlib
import json as _json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from loupe import trace
from loupe.store import JSONLStore


@pytest.fixture()
def store(tmp_path: Path) -> JSONLStore:
    return JSONLStore(root=tmp_path)


def _install_fake_httpx() -> None:
    """Plant a fake httpx module that the integration can patch."""
    pkg = ModuleType("httpx")
    state: dict[str, Any] = {"response": None}

    class Client:
        def send(self, request, *args: Any, **kwargs: Any) -> Any:
            return state["response"]

    class AsyncClient:
        async def send(self, request, *args: Any, **kwargs: Any) -> Any:
            return state["response"]

    pkg.Client = Client
    pkg.AsyncClient = AsyncClient
    pkg._state = state  # type: ignore[attr-defined]
    sys.modules["httpx"] = pkg


def _make_request(url: str, body: dict | None = None) -> SimpleNamespace:
    content = _json.dumps(body).encode() if body else b""
    return SimpleNamespace(url=url, content=content)


def _make_response(status: int, body: dict | None = None) -> SimpleNamespace:
    def _json_ok(b: dict = body or {}) -> dict:
        return b

    def _json_err() -> dict:
        raise ValueError("no body")

    return SimpleNamespace(
        status_code=status,
        json=_json_ok if body is not None else _json_err,
    )


def _read_steps(store: JSONLStore) -> list[dict]:
    files = list(store.root.glob("*.jsonl"))
    assert len(files) == 1
    return [
        _json.loads(line)
        for line in files[0].read_text().splitlines()
        if _json.loads(line)["_type"] == "step"
    ]


def test_captures_anthropic_call(store: JSONLStore) -> None:
    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()

    import httpx
    httpx._state["response"] = _make_response(  # type: ignore[attr-defined]
        200,
        {
            "content": [{"type": "text", "text": "Hello back."}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 7, "output_tokens": 5},
        },
    )

    @trace(framework="universal-test", store=store)
    def run() -> Any:
        client = httpx.Client()
        return client.send(_make_request(
            "https://api.anthropic.com/v1/messages",
            {"model": "claude-haiku-4-5", "max_tokens": 64,
             "messages": [{"role": "user", "content": "hi"}]},
        ))

    out = run()
    assert getattr(out, "status_code", None) == 200

    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["kind"] == "llm-call"
    assert steps[0]["name"] == "anthropic:claude-haiku-4-5"
    assert steps[0]["inputs"]["provider"] == "anthropic"
    assert steps[0]["outputs"]["text"] == "Hello back."
    assert steps[0]["outputs"]["stop_reason"] == "end_turn"
    assert steps[0]["outputs"]["input_tokens"] == 7
    assert steps[0]["metadata"]["transport"] == "httpx"


def test_captures_openai_call(store: JSONLStore) -> None:
    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()

    import httpx
    httpx._state["response"] = _make_response(  # type: ignore[attr-defined]
        200,
        {
            "choices": [{
                "message": {"content": "fine, thanks"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        },
    )

    @trace(framework="universal-test", store=store)
    def run() -> Any:
        return httpx.Client().send(_make_request(
            "https://api.openai.com/v1/chat/completions",
            {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        ))

    run()
    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["name"] == "openai:gpt-4o-mini"
    assert steps[0]["outputs"]["text"] == "fine, thanks"
    assert steps[0]["outputs"]["finish_reason"] == "stop"
    assert steps[0]["outputs"]["input_tokens"] == 12
    assert steps[0]["outputs"]["output_tokens"] == 4


def test_ignores_unknown_hosts(store: JSONLStore) -> None:
    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()

    import httpx
    httpx._state["response"] = _make_response(200, {"hello": "world"})  # type: ignore[attr-defined]

    @trace(framework="universal-test", store=store)
    def run() -> Any:
        return httpx.Client().send(_make_request("https://example.com/api"))

    run()
    steps = _read_steps(store)
    # An unknown host doesn't create a step
    assert len(steps) == 0


def test_idempotent_patch() -> None:
    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    assert httpx_mod.patch() is True
    assert httpx_mod.patch() is False


def test_extracts_model_from_gemini_url(store: JSONLStore) -> None:
    """Gemini puts the model in the URL path, not the body. Real-world bug
    found when running `loupe-chat` against a real Gemini API key — the
    captured step said `gemini:unknown` because body.model was None."""
    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()

    import httpx
    httpx._state["response"] = _make_response(
        200,
        {
            "candidates": [
                {"content": {"parts": [{"text": "ok"}]}}
            ]
        },
    )

    @trace(framework="gemini-test", store=store)
    def run() -> None:
        url = (
            "https://generativelanguage.googleapis.com"
            "/v1beta/models/gemini-2.0-flash:generateContent"
        )
        # Gemini body has no "model" key — the model is in the URL.
        httpx.Client().send(_make_request(url, {"contents": [{"parts": []}]}))

    run()
    steps = _read_steps(store)
    assert len(steps) == 1
    step = steps[0]
    # The provider label is whatever the _providers.py registry calls
    # generativelanguage.googleapis.com — currently "gemini". The model
    # extraction is what we're testing here.
    assert ":gemini-2.0-flash" in step["name"]
    assert step["inputs"]["model"] == "gemini-2.0-flash"


def test_direct_capture_suppresses_httpx_layer(store: JSONLStore) -> None:
    """When a direct SDK integration is active, universal-httpx must skip.

    In production, the anthropic/openai SDK integrations call into httpx
    under the hood — without this dedup, every real call would emit two
    Steps (one from the SDK wrapper, one from the http interceptor).
    """
    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()

    import httpx

    httpx._state["response"] = _make_response(200, {"content": [{"text": "hi"}]})

    from loupe.integrations import suppress_http_capture

    @trace(framework="universal-test", store=store)
    def run() -> None:
        # Mimic what the anthropic integration does: claim the HTTP layer
        # so universal-httpx doesn't double-record.
        with suppress_http_capture():
            httpx.Client().send(
                _make_request(
                    "https://api.anthropic.com/v1/messages",
                    body={"messages": [], "model": "claude-haiku"},
                )
            )

    run()
    steps = _read_steps(store)
    # The universal layer saw the call but the direct-capture flag was on,
    # so it must NOT have emitted a step.
    assert len(steps) == 0, "universal-httpx should skip when direct capture active"


def test_captures_error(store: JSONLStore) -> None:
    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()

    import httpx

    def boom(self, request, *args, **kwargs):
        raise RuntimeError("network down")

    httpx.Client.send = boom  # type: ignore[method-assign]
    # Re-patch so our wrapper sits on top of the new bound method
    httpx.Client.send = httpx_mod._wrap_sync(httpx.Client.send)  # type: ignore[attr-defined]

    @trace(framework="universal-test", store=store)
    def run() -> None:
        httpx.Client().send(_make_request("https://api.openai.com/v1/x"))

    with pytest.raises(RuntimeError):
        run()

    steps = _read_steps(store)
    assert len(steps) == 1
    assert "network down" in (steps[0]["error"] or "")
