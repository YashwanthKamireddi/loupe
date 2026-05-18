"""LlamaIndex integration tests using a planted fake module."""

from __future__ import annotations

import asyncio
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


def _install_fake_llama_index() -> None:
    """Plant llama_index.core.query_engine.BaseQueryEngine."""

    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self.response = text
            self.source_nodes = [SimpleNamespace(), SimpleNamespace()]

        def __str__(self) -> str:
            return self.response

    class BaseQueryEngine:
        def query(self, str_or_query_bundle: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse(f"rag-reply:{str_or_query_bundle}")

        async def aquery(self, str_or_query_bundle: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse(f"async-rag-reply:{str_or_query_bundle}")

    pkg = ModuleType("llama_index")
    core = ModuleType("llama_index.core")
    qe = ModuleType("llama_index.core.query_engine")
    qe.BaseQueryEngine = BaseQueryEngine
    core.query_engine = qe
    pkg.core = core
    sys.modules["llama_index"] = pkg
    sys.modules["llama_index.core"] = core
    sys.modules["llama_index.core.query_engine"] = qe


def _read_steps(store: JSONLStore) -> list[dict]:
    files = list(store.root.glob("*.jsonl"))
    assert len(files) == 1
    return [
        _json.loads(line)
        for line in files[0].read_text().splitlines()
        if _json.loads(line)["_type"] == "step"
    ]


def test_llama_index_sync_capture(store: JSONLStore) -> None:
    _install_fake_llama_index()
    sys.modules.pop("loupe.integrations.llama_index", None)
    mod = importlib.import_module("loupe.integrations.llama_index")
    assert mod.patch() is True
    assert mod.patch() is False

    from llama_index.core.query_engine import BaseQueryEngine  # type: ignore[import-not-found]

    @trace(framework="llama-index-test", store=store)
    def run() -> str:
        return str(BaseQueryEngine().query("what is loupe?"))

    out = run()
    assert out == "rag-reply:what is loupe?"

    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["kind"] == "tool-call"
    assert steps[0]["name"] == "llama-index:BaseQueryEngine"
    assert steps[0]["inputs"]["query"] == "what is loupe?"
    assert steps[0]["outputs"]["text"] == "rag-reply:what is loupe?"
    assert steps[0]["outputs"]["source_count"] == 2


def test_llama_index_async_capture(store: JSONLStore) -> None:
    _install_fake_llama_index()
    sys.modules.pop("loupe.integrations.llama_index", None)
    mod = importlib.import_module("loupe.integrations.llama_index")
    mod.patch()

    from llama_index.core.query_engine import BaseQueryEngine  # type: ignore[import-not-found]

    @trace(framework="llama-index-test", store=store)
    async def run() -> str:
        r = await BaseQueryEngine().aquery("how does it work?")
        return str(r)

    out = asyncio.run(run())
    assert out == "async-rag-reply:how does it work?"

    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["outputs"]["text"].startswith("async-rag-reply:")


def test_llama_index_redacts_secrets_in_query(store: JSONLStore) -> None:
    _install_fake_llama_index()
    sys.modules.pop("loupe.integrations.llama_index", None)
    mod = importlib.import_module("loupe.integrations.llama_index")
    mod.patch()

    from llama_index.core.query_engine import BaseQueryEngine  # type: ignore[import-not-found]

    @trace(framework="llama-index-test", store=store)
    def run() -> str:
        return str(BaseQueryEngine().query(
            "use sk-ant-abcdefghij1234567890abcdef to fetch data"
        ))

    run()
    steps = _read_steps(store)
    assert "[redacted]" in steps[0]["inputs"]["query"]
