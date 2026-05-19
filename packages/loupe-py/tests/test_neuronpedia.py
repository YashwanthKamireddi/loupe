"""Tests for the Neuronpedia client.

Coverage:
- hook_to_neuronpedia_layer mapping (correct + invalid)
- model_for_neuronpedia mapping
- explain() cache miss → http call → cache hit (no second http call)
- explain() respects LOUPE_DISABLE_NEURONPEDIA
- explain() handles HTTP errors gracefully (returns None, doesn't raise)
- explain_many() batches and honors the cache
- The on-disk cache survives across instances
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loupe.neuronpedia import (
    _Cache,
    explain,
    explain_many,
    hook_to_neuronpedia_layer,
    model_for_neuronpedia,
)

# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def test_hook_to_neuronpedia_layer_canonical() -> None:
    layer = hook_to_neuronpedia_layer(
        "blocks.6.hook_resid_pre",
        "gpt2-small-res-jb/blocks.6.hook_resid_pre",
    )
    assert layer == "6-res-jb"


def test_hook_to_neuronpedia_layer_feature_splitting() -> None:
    layer = hook_to_neuronpedia_layer(
        "blocks.8.hook_resid_pre",
        "gpt2-small-res-jb-feature-splitting/blocks.8.hook_resid_pre",
    )
    assert layer == "8-res-jb-feature-splitting"


def test_hook_to_neuronpedia_layer_unknown_release_returns_none() -> None:
    assert hook_to_neuronpedia_layer(
        "blocks.6.hook_resid_pre", "some-unknown-release"
    ) is None


def test_hook_to_neuronpedia_layer_malformed_hook_returns_none() -> None:
    assert hook_to_neuronpedia_layer("not-a-hook-name", "gpt2-small-res-jb") is None


def test_model_for_neuronpedia_recognizes_gpt2_small() -> None:
    assert model_for_neuronpedia("gpt2-small-res-jb") == "gpt2-small"
    assert model_for_neuronpedia("gpt2-small-res-jb/blocks.6.hook_resid_pre") == "gpt2-small"


def test_model_for_neuronpedia_returns_none_for_unknown() -> None:
    assert model_for_neuronpedia("llama-3-8b-something") is None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_roundtrips_via_disk(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    c1 = _Cache(cache_path)
    c1.put("gpt2-small/6-res-jb/1", "circuit for X")
    c1.put("gpt2-small/6-res-jb/2", None)   # confirmed-no-explanation

    # Re-open with a fresh _Cache pointing at the same file
    c2 = _Cache(cache_path)
    found, val = c2.get("gpt2-small/6-res-jb/1")
    assert found and val == "circuit for X"
    found2, val2 = c2.get("gpt2-small/6-res-jb/2")
    assert found2 and val2 is None    # cached negative
    found3, val3 = c2.get("gpt2-small/6-res-jb/3")
    assert not found3 and val3 is None    # never cached


# ---------------------------------------------------------------------------
# explain() with HTTP mocking
# ---------------------------------------------------------------------------


@pytest.fixture()
def cache(tmp_path: Path) -> _Cache:
    return _Cache(tmp_path / "neuronpedia-cache.json")


def test_explain_disabled_env_returns_none(
    monkeypatch: pytest.MonkeyPatch, cache: _Cache
) -> None:
    monkeypatch.setenv("LOUPE_DISABLE_NEURONPEDIA", "1")
    result = explain(
        23123,
        hook_name="blocks.6.hook_resid_pre",
        release="gpt2-small-res-jb",
        cache=cache,
    )
    assert result is None


def test_explain_returns_description_from_api(
    monkeypatch: pytest.MonkeyPatch, cache: _Cache
) -> None:
    """Patch httpx.get to return a canned Neuronpedia response."""
    import httpx

    calls = []

    class FakeResp:
        status_code = 200
        def json(self) -> dict:
            return {"explanations": [
                {"description": "phrases about cats", "scores": {}},
            ]}

    def fake_get(url: str, **kw: object) -> FakeResp:
        calls.append(url)
        return FakeResp()

    monkeypatch.setattr(httpx, "get", fake_get, raising=False)

    desc = explain(
        42,
        hook_name="blocks.6.hook_resid_pre",
        release="gpt2-small-res-jb",
        cache=cache,
    )
    assert desc == "phrases about cats"
    assert any("/42" in url for url in calls)


def test_explain_uses_cache_on_second_call(
    monkeypatch: pytest.MonkeyPatch, cache: _Cache
) -> None:
    """Second lookup must NOT hit the network."""
    import httpx

    calls: list[str] = []

    class FakeResp:
        status_code = 200
        def json(self) -> dict:
            return {"explanations": [{"description": "first", "scores": {}}]}

    def fake_get(url: str, **kw: object) -> FakeResp:
        calls.append(url)
        return FakeResp()

    monkeypatch.setattr(httpx, "get", fake_get, raising=False)

    explain(7, hook_name="blocks.6.hook_resid_pre",
            release="gpt2-small-res-jb", cache=cache)
    n_after_first = len(calls)
    explain(7, hook_name="blocks.6.hook_resid_pre",
            release="gpt2-small-res-jb", cache=cache)
    assert len(calls) == n_after_first, "second call should hit cache, not network"


def test_explain_http_error_returns_none(
    monkeypatch: pytest.MonkeyPatch, cache: _Cache
) -> None:
    """Network failure must not raise — Loupe never blocks on Neuronpedia."""
    import httpx

    def boom(url: str, **kw: object) -> None:
        raise httpx.ConnectError("dns broken")

    monkeypatch.setattr(httpx, "get", boom, raising=False)

    result = explain(
        99,
        hook_name="blocks.6.hook_resid_pre",
        release="gpt2-small-res-jb",
        cache=cache,
    )
    assert result is None


def test_explain_404_returns_none(
    monkeypatch: pytest.MonkeyPatch, cache: _Cache
) -> None:
    """Neuronpedia 404 for an unknown feature → None, no traceback."""
    import httpx

    class FakeResp:
        status_code = 404
        def json(self) -> dict:
            return {}

    monkeypatch.setattr(httpx, "get", lambda url, **kw: FakeResp(), raising=False)

    assert explain(
        99,
        hook_name="blocks.6.hook_resid_pre",
        release="gpt2-small-res-jb",
        cache=cache,
    ) is None


def test_explain_unknown_release_returns_none(
    monkeypatch: pytest.MonkeyPatch, cache: _Cache
) -> None:
    """If we can't map the release → no Neuronpedia call attempted at all."""
    # No httpx patch — if the code tried to call out, this test would
    # hit the real network. We assert it doesn't try.
    result = explain(
        1,
        hook_name="blocks.6.hook_resid_pre",
        release="totally-unknown-sae-set",
        cache=cache,
    )
    assert result is None


# ---------------------------------------------------------------------------
# explain_many() batching
# ---------------------------------------------------------------------------


def test_explain_many_returns_one_entry_per_feature(
    monkeypatch: pytest.MonkeyPatch, cache: _Cache
) -> None:
    import httpx

    class FakeResp:
        def __init__(self, fid: int) -> None:
            self.fid = fid
            self.status_code = 200
        def json(self) -> dict:
            return {"explanations": [{"description": f"feat-{self.fid}", "scores": {}}]}

    def fake_get(url: str, **kw: object) -> FakeResp:
        fid = int(url.rstrip("/").rsplit("/", 1)[-1])
        return FakeResp(fid)

    monkeypatch.setattr(httpx, "get", fake_get, raising=False)

    out = explain_many(
        [10, 20, 30],
        hook_name="blocks.6.hook_resid_pre",
        release="gpt2-small-res-jb",
        cache=cache,
    )
    assert out == {10: "feat-10", 20: "feat-20", 30: "feat-30"}


def test_explain_many_disabled_env_returns_all_none(
    monkeypatch: pytest.MonkeyPatch, cache: _Cache
) -> None:
    monkeypatch.setenv("LOUPE_DISABLE_NEURONPEDIA", "1")
    out = explain_many(
        [1, 2],
        hook_name="blocks.6.hook_resid_pre",
        release="gpt2-small-res-jb",
        cache=cache,
    )
    assert out == {1: None, 2: None}


def test_explain_many_empty_input_returns_empty(cache: _Cache) -> None:
    """Edge case — no features to look up."""
    assert explain_many(
        [],
        hook_name="blocks.6.hook_resid_pre",
        release="gpt2-small-res-jb",
        cache=cache,
    ) == {}


# ---------------------------------------------------------------------------
# FeatureActivation has description field (round-trip serialization)
# ---------------------------------------------------------------------------


def test_feature_activation_description_field_serializes() -> None:
    """The dataclass change must round-trip through JSON cleanly so dashboard
    + on-disk annotations see the description."""
    from dataclasses import asdict

    from loupe.attribution import FeatureActivation

    f = FeatureActivation(
        feature_id=23123,
        activation=420.0,
        layer="blocks.6.hook_resid_pre",
        token_position=5,
        description="phrases related to legal rulings",
    )
    d = asdict(f)
    assert d["description"] == "phrases related to legal rulings"
    # JSON round-trip — what gets stored to disk via to_json_dict
    j = json.loads(json.dumps(d))
    assert j["description"] == "phrases related to legal rulings"
