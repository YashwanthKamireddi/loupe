"""Streaming-mode capture tests for Anthropic + OpenAI integrations.

We don't make real network calls — we plant tiny synthetic stream objects into
sys.modules and verify the wrappers (a) pass each event through to the caller
unchanged and (b) emit one Step with the aggregated text + usage.
"""

from __future__ import annotations

import asyncio
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
# Synthetic SDK builders
# ---------------------------------------------------------------------------


def _install_fake_anthropic_with_streams() -> None:
    """Plant anthropic.resources.messages.{Messages,AsyncMessages} that can stream."""

    def make_events(text: str):
        # Mimic the Anthropic SSE event shape
        return [
            SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(usage=SimpleNamespace(input_tokens=5, output_tokens=0)),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text=text[:3]),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text=text[3:]),
            ),
            SimpleNamespace(
                type="message_delta",
                usage=SimpleNamespace(output_tokens=11),
                delta=SimpleNamespace(stop_reason="end_turn"),
            ),
        ]

    class _SyncStream:
        def __init__(self, events):
            self._events = iter(events)
        def __iter__(self):
            return self
        def __next__(self):
            return next(self._events)

    class _AsyncStream:
        def __init__(self, events):
            self._events = list(events)
            self._i = 0
        def __aiter__(self):
            return self
        async def __anext__(self):
            if self._i >= len(self._events):
                raise StopAsyncIteration
            ev = self._events[self._i]
            self._i += 1
            return ev

    class Messages:
        def create(self, **kwargs):
            text = "hi there"
            if kwargs.get("stream"):
                return _SyncStream(make_events(text))
            return SimpleNamespace(
                content=[SimpleNamespace(text=text)],
                stop_reason="end_turn",
                usage=SimpleNamespace(input_tokens=5, output_tokens=11),
            )

    class AsyncMessages:
        async def create(self, **kwargs):
            text = "hi there"
            if kwargs.get("stream"):
                return _AsyncStream(make_events(text))
            return SimpleNamespace(
                content=[SimpleNamespace(text=text)],
                stop_reason="end_turn",
                usage=SimpleNamespace(input_tokens=5, output_tokens=11),
            )

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


def _install_fake_openai_with_streams() -> None:
    def make_chunks(text: str):
        # Yield three chunks splitting `text`, then a final chunk with finish_reason + usage
        pieces = [text[:3], text[3:6], text[6:]]
        chunks = []
        for p in pieces:
            chunks.append(
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(delta=SimpleNamespace(content=p), finish_reason=None)
                    ],
                    usage=None,
                )
            )
        chunks.append(
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=None),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=4, completion_tokens=9),
            )
        )
        return chunks

    class _SyncStream:
        def __init__(self, chunks):
            self._it = iter(chunks)
        def __iter__(self):
            return self
        def __next__(self):
            return next(self._it)

    class _AsyncStream:
        def __init__(self, chunks):
            self._chunks = list(chunks)
            self._i = 0
        def __aiter__(self):
            return self
        async def __anext__(self):
            if self._i >= len(self._chunks):
                raise StopAsyncIteration
            ev = self._chunks[self._i]
            self._i += 1
            return ev

    class Completions:
        def create(self, **kwargs):
            text = "hello back"
            if kwargs.get("stream"):
                return _SyncStream(make_chunks(text))
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                usage=SimpleNamespace(prompt_tokens=4, completion_tokens=9),
            )

    class AsyncCompletions:
        async def create(self, **kwargs):
            text = "hello back"
            if kwargs.get("stream"):
                return _AsyncStream(make_chunks(text))
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                usage=SimpleNamespace(prompt_tokens=4, completion_tokens=9),
            )

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


def _read_steps(store: JSONLStore) -> list[dict]:
    files = list(store.root.glob("*.jsonl"))
    assert len(files) == 1
    return [
        json.loads(line)
        for line in files[0].read_text().splitlines()
        if json.loads(line)["_type"] == "step"
    ]


# ---------------------------------------------------------------------------
# Anthropic streaming
# ---------------------------------------------------------------------------


def test_anthropic_sync_stream_aggregates(store: JSONLStore) -> None:
    _install_fake_anthropic_with_streams()
    sys.modules.pop("loupe.integrations.anthropic", None)
    from loupe.integrations.anthropic import patch

    patch()
    from anthropic.resources.messages import Messages  # type: ignore[import-not-found]

    @trace(framework="anthropic-test", store=store)
    def run() -> str:
        stream = Messages().create(
            model="claude-sonnet-4-6",
            stream=True,
            messages=[{"role": "user", "content": "hi"}],
        )
        # Consume the stream like a real caller would
        events = list(stream)
        return f"saw {len(events)} events"

    out = run()
    assert out == "saw 4 events"

    steps = _read_steps(store)
    assert len(steps) == 1
    step = steps[0]
    assert step["name"] == "anthropic:claude-sonnet-4-6"
    assert step["outputs"]["streamed"] is True
    assert step["outputs"]["text"] == "hi there"
    assert step["outputs"]["stop_reason"] == "end_turn"
    assert step["outputs"]["input_tokens"] == 5
    assert step["outputs"]["output_tokens"] == 11


def test_anthropic_async_stream_aggregates(store: JSONLStore) -> None:
    _install_fake_anthropic_with_streams()
    sys.modules.pop("loupe.integrations.anthropic", None)
    from loupe.integrations.anthropic import patch

    patch()
    from anthropic.resources.messages import AsyncMessages  # type: ignore[import-not-found]

    @trace(framework="anthropic-test", store=store)
    async def run() -> str:
        stream = await AsyncMessages().create(
            model="claude-haiku-4-5",
            stream=True,
            messages=[{"role": "user", "content": "hi"}],
        )
        events = []
        async for ev in stream:
            events.append(ev)
        return str(len(events))

    out = asyncio.run(run())
    assert out == "4"

    step = _read_steps(store)[0]
    assert step["outputs"]["text"] == "hi there"
    assert step["outputs"]["streamed"] is True
    assert step["outputs"]["output_tokens"] == 11


def test_anthropic_non_streaming_still_works(store: JSONLStore) -> None:
    _install_fake_anthropic_with_streams()
    sys.modules.pop("loupe.integrations.anthropic", None)
    from loupe.integrations.anthropic import patch

    patch()
    from anthropic.resources.messages import Messages  # type: ignore[import-not-found]

    @trace(framework="anthropic-test", store=store)
    def run() -> str:
        msg = Messages().create(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hi"}],
        )
        return msg.content[0].text

    assert run() == "hi there"
    step = _read_steps(store)[0]
    assert step["outputs"]["text"] == "hi there"
    assert "streamed" not in step["outputs"]


# ---------------------------------------------------------------------------
# OpenAI streaming
# ---------------------------------------------------------------------------


def test_openai_sync_stream_aggregates(store: JSONLStore) -> None:
    _install_fake_openai_with_streams()
    sys.modules.pop("loupe.integrations.openai", None)
    from loupe.integrations.openai import patch

    patch()
    from openai.resources.chat.completions import Completions  # type: ignore[import-not-found]

    @trace(framework="openai-test", store=store)
    def run() -> str:
        stream = Completions().create(
            model="gpt-4o-mini",
            stream=True,
            messages=[{"role": "user", "content": "hi"}],
        )
        text_parts = []
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                text_parts.append(delta)
        return "".join(text_parts)

    assert run() == "hello back"

    step = _read_steps(store)[0]
    assert step["name"] == "openai-chat:gpt-4o-mini"
    assert step["outputs"]["text"] == "hello back"
    assert step["outputs"]["streamed"] is True
    assert step["outputs"]["finish_reason"] == "stop"
    assert step["outputs"]["prompt_tokens"] == 4
    assert step["outputs"]["completion_tokens"] == 9


def test_openai_async_stream_aggregates(store: JSONLStore) -> None:
    _install_fake_openai_with_streams()
    sys.modules.pop("loupe.integrations.openai", None)
    from loupe.integrations.openai import patch

    patch()
    from openai.resources.chat.completions import AsyncCompletions  # type: ignore[import-not-found]

    @trace(framework="openai-test", store=store)
    async def run() -> str:
        stream = await AsyncCompletions().create(
            model="gpt-4o-mini",
            stream=True,
            messages=[{"role": "user", "content": "hi"}],
        )
        text_parts = []
        async for chunk in stream:
            d = chunk.choices[0].delta.content
            if d:
                text_parts.append(d)
        return "".join(text_parts)

    assert asyncio.run(run()) == "hello back"
    step = _read_steps(store)[0]
    assert step["outputs"]["text"] == "hello back"
    assert step["outputs"]["streamed"] is True
