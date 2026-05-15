"""The httpx universal capture should fall back to openai-compatible
detection for unknown hosts whose payload looks like OpenAI spec.

This is what makes Loupe work with LiteLLM, internal proxies, custom forks.
"""

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
    if not files:
        return []
    return [
        _json.loads(line)
        for line in files[0].read_text().splitlines()
        if _json.loads(line)["_type"] == "step"
    ]


def test_unknown_host_with_openai_body_is_captured(store: JSONLStore) -> None:
    """A LiteLLM-style proxy or internal gateway is captured even though we
    don't know its host."""
    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()

    import httpx
    httpx._state["response"] = _make_response(  # type: ignore[attr-defined]
        200,
        {
            "choices": [{
                "message": {"content": "ok"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        },
    )

    @trace(framework="proxy-test", store=store)
    def run() -> Any:
        return httpx.Client().send(_make_request(
            "https://my-internal-llm-proxy.example.com/v1/chat/completions",
            {"model": "internal/gpt", "messages": [{"role": "user", "content": "hi"}]},
        ))

    run()
    steps = _read_steps(store)
    assert len(steps) == 1
    # The label includes the host so you can tell what was hit.
    assert steps[0]["name"].startswith("openai-compatible:my-internal-llm-proxy.example.com:")
    assert steps[0]["outputs"]["text"] == "ok"


def test_unknown_host_without_openai_body_is_skipped(store: JSONLStore) -> None:
    """An unrelated HTTP call to a random host with a non-LLM body is left alone."""
    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()

    import httpx
    httpx._state["response"] = _make_response(200, {"unrelated": True})  # type: ignore[attr-defined]

    @trace(framework="proxy-test", store=store)
    def run() -> Any:
        return httpx.Client().send(_make_request("https://example.com/api", {"foo": "bar"}))

    run()
    assert _read_steps(store) == []
