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


def test_localhost_only_captured_when_body_looks_llm_shaped(store: JSONLStore) -> None:
    """Loopback (Ollama/vLLM/LM Studio) hosts must NOT capture non-LLM traffic.

    Playwright's Chrome DevTools Protocol, local dev servers, mDNS, health
    checks — all hit 127.0.0.1 / localhost — and any of them would land as
    bogus ``llm-call`` rows without a body-shape gate. Regression captured
    from running Loupe against the browser-use OSS agent (96.9k *).
    """
    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()

    import httpx
    httpx._state["response"] = _make_response(200, {"ok": True})  # type: ignore[attr-defined]

    # 1. CDP-shaped payload (no `messages`/`model` field) to 127.0.0.1 →
    #    NOT captured (the regression we just fixed).
    @trace(framework="universal-test", store=store)
    def cdp_call() -> Any:
        cdp_body = {"id": 1, "method": "Page.navigate", "params": {"url": "x"}}
        return httpx.Client().send(_make_request("http://127.0.0.1:9222/json", body=cdp_body))

    cdp_call()
    assert len(_read_steps(store)) == 0, "Playwright CDP traffic must NOT be captured as llm-call"

    # 2. Genuine local-LLM call (Ollama-shaped chat completion) to
    #    127.0.0.1 → MUST still be captured (Ollama users depend on it).
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()
    httpx._state["response"] = _make_response(200, {"choices": [{"message": {"content": "hi"}}]})  # type: ignore[attr-defined]

    other_store = JSONLStore(root=store.root.parent / "ollama-traces")

    @trace(framework="universal-test", store=other_store)
    def ollama_call() -> Any:
        llm_body = {"model": "llama3", "messages": [{"role": "user", "content": "hi"}]}
        url = "http://127.0.0.1:11434/v1/chat/completions"
        return httpx.Client().send(_make_request(url, body=llm_body))

    ollama_call()
    ollama_steps = [
        _json.loads(line)
        for line in next((other_store.root).glob("*.jsonl")).read_text().splitlines()
        if _json.loads(line)["_type"] == "step"
    ]
    assert len(ollama_steps) == 1, "Genuine local-LLM Ollama call MUST still be captured"
    assert ollama_steps[0]["inputs"]["provider"] == "local-ip"


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


def test_autopatch_creates_implicit_trace_when_no_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION FENCE for the zero-code path.

    With LOUPE_AUTOPATCH=1 and NO @trace context active, calling a
    universal-httpx-patched send() must still produce a captured trace
    on disk. This is the architectural promise: developers don't need
    to write @trace or call patch_all in their code — just set the
    env var and every LLM call captures automatically."""
    monkeypatch.setenv("LOUPE_HOME", str(tmp_path))
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
    monkeypatch.setenv("LOUPE_AUTOPATCH", "1")
    from loupe import store as store_mod
    store_mod._default = None

    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()

    import httpx
    httpx._state["response"] = _make_response(
        200, {"content": [{"text": "hi from autopatch"}]},
    )

    # No @trace context — just call the patched httpx directly.
    httpx.Client().send(
        _make_request(
            "https://api.anthropic.com/v1/messages",
            body={"model": "claude-haiku", "messages": []},
        )
    )

    # A trace should now exist on disk.
    traces = list((tmp_path / "traces").glob("*.jsonl"))
    assert len(traces) == 1, "autopatch must have written a trace"
    import json as _json
    lines = traces[0].read_text().splitlines()
    header = _json.loads(lines[0])
    assert header["_type"] == "trace"
    # name = derived from sys.argv[0] stem (script filename) or "auto"
    # fallback. framework="autopatch" is the stable signal that this came
    # from the zero-code path.
    assert isinstance(header["name"], str) and header["name"]
    assert header["framework"] == "autopatch"
    # And the llm-call step is captured
    step_kinds = [
        _json.loads(line).get("kind") for line in lines[1:]
    ]
    assert "llm-call" in step_kinds


def test_no_autopatch_means_no_trace_without_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inverse: with LOUPE_AUTOPATCH unset and no setup config, the
    universal-httpx interceptor falls through silently when no @trace
    context exists. No phantom traces, no side effects (safety guard
    for transitive installs)."""
    monkeypatch.setenv("LOUPE_HOME", str(tmp_path))
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
    monkeypatch.delenv("LOUPE_AUTOPATCH", raising=False)
    # No config.toml exists → autopatch must stay OFF
    from loupe import store as store_mod
    store_mod._default = None

    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()

    import httpx
    httpx._state["response"] = _make_response(200, {"content": [{"text": "x"}]})

    httpx.Client().send(
        _make_request(
            "https://api.anthropic.com/v1/messages",
            body={"model": "claude", "messages": []},
        )
    )

    # No trace should have been written.
    assert not (tmp_path / "traces").exists() or not list(
        (tmp_path / "traces").glob("*.jsonl")
    )


def test_autopatch_defaults_on_when_setup_config_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v0.0.59 change: presence of ``~/.loupe/config.toml`` (i.e. the
    user has run ``loupe setup``) flips autopatch ON without needing
    the ``LOUPE_AUTOPATCH=1`` env var. This is the "install + setup =
    it just works" frictionless promise."""
    monkeypatch.setenv("LOUPE_HOME", str(tmp_path))
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
    monkeypatch.delenv("LOUPE_AUTOPATCH", raising=False)
    # Simulate having run `loupe setup`.
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.toml").write_text(
        '[default]\nprovider = "gemini"\nmodel = "gemini-2.5-flash"\n',
        encoding="utf-8",
    )
    from loupe import store as store_mod
    store_mod._default = None

    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()

    import httpx
    httpx._state["response"] = _make_response(
        200,
        {"content": [{"type": "text", "text": "hi"}]},
    )

    httpx.Client().send(
        _make_request(
            "https://api.anthropic.com/v1/messages",
            body={"model": "claude-haiku-4-5", "messages": []},
        )
    )

    # The trace should land because autopatch defaulted ON.
    traces = list((tmp_path / "traces").glob("*.jsonl"))
    assert len(traces) == 1


def test_autopatch_explicit_off_overrides_default_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LOUPE_AUTOPATCH=0`` is the explicit opt-out — it must win even
    when the user has run setup."""
    monkeypatch.setenv("LOUPE_HOME", str(tmp_path))
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
    monkeypatch.setenv("LOUPE_AUTOPATCH", "0")
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.toml").write_text(
        '[default]\nprovider = "gemini"\n', encoding="utf-8",
    )
    from loupe import store as store_mod
    store_mod._default = None

    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()

    import httpx
    httpx._state["response"] = _make_response(200, {"content": [{"text": "x"}]})

    httpx.Client().send(
        _make_request(
            "https://api.anthropic.com/v1/messages",
            body={"model": "x", "messages": []},
        )
    )

    assert not (tmp_path / "traces").exists() or not list(
        (tmp_path / "traces").glob("*.jsonl")
    )


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


def test_captures_error_body_on_4xx(store: JSONLStore) -> None:
    """A failed HTTP call (4xx/5xx) must capture the provider's error
    MESSAGE, not just the status code. Regression for the real
    Vyuha-Mind finding where a 400 from Gemini recorded only
    `{"status": 400}` and dropped 'API key not valid'."""
    _install_fake_httpx()
    sys.modules.pop("loupe.integrations.httpx", None)
    httpx_mod = importlib.import_module("loupe.integrations.httpx")
    httpx_mod.patch()

    import httpx
    # Gemini-style 400 error envelope.
    httpx._state["response"] = _make_response(  # type: ignore[attr-defined]
        400,
        {"error": {"code": 400,
                   "message": "API key not valid. Please pass a valid API key.",
                   "status": "INVALID_ARGUMENT"}},
    )

    @trace(framework="universal-test", store=store)
    def run() -> Any:
        return httpx.Client().send(_make_request(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash:generateContent",
            {"contents": [{"parts": [{"text": "hi"}], "role": "user"}]},
        ))

    run()
    steps = _read_steps(store)
    assert len(steps) == 1
    s = steps[0]
    assert s["outputs"]["status"] == 400
    # The message is captured in BOTH the outputs and the step error.
    assert "API key not valid" in s["outputs"].get("error", "")
    assert "API key not valid" in (s["error"] or "")
    assert s["error"].startswith("HTTP 400")


# ---------------------------------------------------------------------------
# Regression tests (v0.0.58)
# ---------------------------------------------------------------------------


def test_truncate_preserves_list_structure() -> None:
    """Lists shorter than the limit must stay as native lists, NOT be
    stringified through ``repr()``. The earlier behaviour produced
    invalid JSON (single quotes, ``True``/``False`` instead of
    ``true``/``false``) that downstream parsers choked on."""
    from loupe.integrations.httpx import _truncate

    messages = [{"role": "user", "content": "hi"}]
    out = _truncate(messages, limit=4000)
    assert out == messages
    assert isinstance(out, list)


def test_truncate_preserves_dict_structure() -> None:
    from loupe.integrations.httpx import _truncate

    body = {"model": "claude-haiku-4-5", "max_tokens": 32, "stream": True}
    out = _truncate(body, limit=4000)
    assert out == body
    assert isinstance(out, dict)


def test_truncate_stringifies_only_on_overflow() -> None:
    """Beyond the limit, a list collapses to a truncated JSON string."""
    from loupe.integrations.httpx import _truncate

    big = [{"content": "x" * 100} for _ in range(50)]
    out = _truncate(big, limit=200)
    assert isinstance(out, str)
    assert out.endswith("…[truncated]")
    # And it's JSON-shaped (double-quoted keys), not Python-repr.
    assert '"content"' in out
