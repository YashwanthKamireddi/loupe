"""Integration wrapper tests — Anthropic + OpenAI without real network calls.

We exercise the patch() function against synthetic objects that mimic the SDK
surface area, then verify that record_step was called with the right metadata.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from loupe import trace
from loupe.store import JSONLStore


@pytest.fixture()
def store(tmp_path: Path) -> JSONLStore:
    return JSONLStore(root=tmp_path)


# ---------------------------------------------------------------------------
# Fake Anthropic SDK
# ---------------------------------------------------------------------------


def _install_fake_anthropic() -> None:
    """Plant a fake `anthropic` module that the integration can patch."""

    class _FakeMessage:
        def __init__(self, text: str) -> None:
            self.content = [SimpleNamespace(text=text)]
            self.stop_reason = "end_turn"
            self.usage = SimpleNamespace(input_tokens=12, output_tokens=34)

    class Messages:
        def create(self, **kwargs: object) -> _FakeMessage:
            return _FakeMessage(f"echo:{kwargs.get('messages')}")

    class AsyncMessages:
        async def create(self, **kwargs: object) -> _FakeMessage:
            return _FakeMessage(f"echo:{kwargs.get('messages')}")

    pkg = ModuleType("anthropic")
    resources = ModuleType("anthropic.resources")
    messages_mod = ModuleType("anthropic.resources.messages")
    messages_mod.Messages = Messages
    messages_mod.AsyncMessages = AsyncMessages
    resources.messages = messages_mod
    pkg.resources = resources

    sys.modules["anthropic"] = pkg
    sys.modules["anthropic.resources"] = resources
    sys.modules["anthropic.resources.messages"] = messages_mod


def _install_fake_openai() -> None:
    class _FakeChoice:
        def __init__(self, text: str) -> None:
            self.message = SimpleNamespace(content=text)

    class _FakeChat:
        def __init__(self, text: str) -> None:
            self.choices = [_FakeChoice(text)]
            self.usage = SimpleNamespace(prompt_tokens=7, completion_tokens=11)

    class Completions:
        def create(self, **kwargs: object) -> _FakeChat:
            return _FakeChat("hello back")

    class AsyncCompletions:
        async def create(self, **kwargs: object) -> _FakeChat:
            return _FakeChat("hello back")

    pkg = ModuleType("openai")
    resources = ModuleType("openai.resources")
    chat = ModuleType("openai.resources.chat")
    completions_mod = ModuleType("openai.resources.chat.completions")
    completions_mod.Completions = Completions
    completions_mod.AsyncCompletions = AsyncCompletions
    chat.completions = completions_mod
    resources.chat = chat
    pkg.resources = resources

    sys.modules["openai"] = pkg
    sys.modules["openai.resources"] = resources
    sys.modules["openai.resources.chat"] = chat
    sys.modules["openai.resources.chat.completions"] = completions_mod


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


def test_anthropic_sync_capture(store: JSONLStore) -> None:
    _install_fake_anthropic()
    # Re-import the integration so it picks up the fresh fake module.
    sys.modules.pop("loupe.integrations.anthropic", None)
    from loupe.integrations.anthropic import patch

    assert patch() is True
    # Calling again must be a no-op.
    assert patch() is False

    from anthropic.resources.messages import Messages  # type: ignore[import-not-found]

    @trace(framework="anthropic-test", store=store)
    def run() -> str:
        msg = Messages().create(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hi"}],
        )
        return msg.content[0].text

    out = run()
    assert out.startswith("echo:")

    lines = list(store.root.glob("*.jsonl"))[0].read_text().splitlines()
    steps = [json.loads(line) for line in lines[1:]]
    assert len(steps) == 1
    assert steps[0]["kind"] == "llm-call"
    assert steps[0]["name"].startswith("anthropic:")
    assert steps[0]["outputs"]["input_tokens"] == 12
    assert steps[0]["outputs"]["output_tokens"] == 34
    assert steps[0]["outputs"]["stop_reason"] == "end_turn"


def test_anthropic_async_capture(store: JSONLStore) -> None:
    _install_fake_anthropic()
    sys.modules.pop("loupe.integrations.anthropic", None)
    from loupe.integrations.anthropic import patch

    patch()
    import asyncio

    from anthropic.resources.messages import AsyncMessages  # type: ignore[import-not-found]

    @trace(framework="anthropic-test", store=store)
    async def run() -> str:
        msg = await AsyncMessages().create(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hi"}],
        )
        return msg.content[0].text

    asyncio.run(run())

    steps = [
        json.loads(line)
        for line in list(store.root.glob("*.jsonl"))[0].read_text().splitlines()[1:]
    ]
    assert len(steps) == 1
    assert steps[0]["kind"] == "llm-call"


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


def test_openai_sync_capture(store: JSONLStore) -> None:
    _install_fake_openai()
    sys.modules.pop("loupe.integrations.openai", None)
    from loupe.integrations.openai import patch

    assert patch() is True
    assert patch() is False

    from openai.resources.chat.completions import Completions  # type: ignore[import-not-found]

    @trace(framework="openai-test", store=store)
    def run() -> str:
        resp = Completions().create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
        )
        return resp.choices[0].message.content

    out = run()
    assert out == "hello back"

    steps = [
        json.loads(line)
        for line in list(store.root.glob("*.jsonl"))[0].read_text().splitlines()[1:]
    ]
    assert len(steps) == 1
    assert steps[0]["kind"] == "llm-call"
    assert steps[0]["name"].startswith("openai-chat:")
    assert steps[0]["outputs"]["prompt_tokens"] == 7
    assert steps[0]["outputs"]["completion_tokens"] == 11


def test_openai_sync_capture_on_error(store: JSONLStore) -> None:
    _install_fake_openai()
    sys.modules.pop("loupe.integrations.openai", None)

    # Make the create method raise to verify error capture
    import openai.resources.chat.completions as comp  # type: ignore[import-not-found]

    def boom(self: object, **kwargs: object) -> None:
        raise RuntimeError("rate limited")

    comp.Completions.create = boom  # type: ignore[method-assign]

    from loupe.integrations.openai import patch

    patch()

    @trace(framework="openai-test", store=store)
    def run() -> None:
        comp.Completions().create(model="gpt-4o-mini", messages=[])

    with pytest.raises(RuntimeError):
        run()

    lines = list(store.root.glob("*.jsonl"))[0].read_text().splitlines()
    header = json.loads(lines[0])
    steps = [json.loads(line) for line in lines[1:]]
    assert header["metadata"]["failed"] is True
    assert len(steps) == 1
    assert "rate limited" in (steps[0]["error"] or "")
