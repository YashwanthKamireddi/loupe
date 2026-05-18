"""DSPy integration tests using a planted fake module."""

from __future__ import annotations

import importlib
import json as _json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from loupe import trace
from loupe.store import JSONLStore


@pytest.fixture()
def store(tmp_path: Path) -> JSONLStore:
    return JSONLStore(root=tmp_path)


def _install_fake_dspy() -> None:
    """Plant a minimal dspy.Module subclass."""

    class _FakePrediction:
        def __init__(self, answer: str) -> None:
            self.answer = answer

    class Module:
        def __call__(self, **kwargs: Any) -> _FakePrediction:
            q = kwargs.get("question", "?")
            return _FakePrediction(f"dspy:{q}")

    class Predict(Module):
        def __init__(self, signature: str) -> None:
            self.signature = signature

    pkg = ModuleType("dspy")
    pkg.Module = Module
    pkg.Predict = Predict
    sys.modules["dspy"] = pkg


def _read_steps(store: JSONLStore) -> list[dict]:
    files = list(store.root.glob("*.jsonl"))
    assert len(files) == 1
    return [
        _json.loads(line)
        for line in files[0].read_text().splitlines()
        if _json.loads(line)["_type"] == "step"
    ]


def test_dspy_capture(store: JSONLStore) -> None:
    _install_fake_dspy()
    sys.modules.pop("loupe.integrations.dspy", None)
    mod = importlib.import_module("loupe.integrations.dspy")
    assert mod.patch() is True
    assert mod.patch() is False

    import dspy  # type: ignore[import-not-found]

    @trace(framework="dspy-test", store=store)
    def run() -> str:
        qa = dspy.Predict("question -> answer")
        return qa(question="what is loupe?").answer

    out = run()
    assert out == "dspy:what is loupe?"

    steps = _read_steps(store)
    assert len(steps) == 1
    assert steps[0]["kind"] == "llm-call"
    assert steps[0]["name"] == "dspy:Predict"
    assert steps[0]["inputs"]["module"] == "Predict"
    assert steps[0]["inputs"]["kwargs"] == {"question": "what is loupe?"}
    assert steps[0]["outputs"]["fields"]["answer"] == "dspy:what is loupe?"


def test_dspy_redacts_secrets_in_kwargs(store: JSONLStore) -> None:
    _install_fake_dspy()
    sys.modules.pop("loupe.integrations.dspy", None)
    mod = importlib.import_module("loupe.integrations.dspy")
    mod.patch()

    import dspy  # type: ignore[import-not-found]

    @trace(framework="dspy-test", store=store)
    def run() -> str:
        qa = dspy.Predict("question, api_key -> answer")
        return qa(question="hi", api_key="sk-secret-shouldnotleak").answer

    run()
    steps = _read_steps(store)
    # api_key arg should be redacted by key-name
    assert steps[0]["inputs"]["kwargs"]["api_key"] == "[redacted]"
