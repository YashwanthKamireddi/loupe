"""HTTP ingest endpoint tests — any language can POST a trace."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from loupe.ingest import IngestError, ingest  # noqa: E402
from loupe.store import JSONLStore  # noqa: E402
from loupe.ui.server import create_app  # noqa: E402


@pytest.fixture()
def loupe_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "loupe-home"
    home.mkdir()
    monkeypatch.setenv("LOUPE_HOME", str(home))
    from loupe import store as store_mod

    store_mod._default = None
    return home


def _payload(**overrides) -> dict:
    base = {
        "name": "go-agent-test",
        "framework": "go-anthropic",
        "started_at": 1778800000.0,
        "ended_at": 1778800001.2,
        "metadata": {"failed": False},
        "steps": [
            {
                "step_id": "s1",
                "kind": "llm-call",
                "name": "anthropic:claude-haiku-4-5",
                "started_at": 1778800000.1,
                "ended_at": 1778800000.9,
                "inputs": {"prompt": "hi"},
                "outputs": {"text": "hello", "input_tokens": 5, "output_tokens": 2},
            },
        ],
    }
    base.update(overrides)
    return base


def test_ingest_writes_jsonl(loupe_home: Path) -> None:
    store = JSONLStore(root=loupe_home / "traces")
    t = ingest(_payload(), store=store)
    assert t.name == "go-agent-test"
    files = list((loupe_home / "traces").glob("*.jsonl"))
    assert len(files) == 1
    assert files[0].stem == t.trace_id


def test_ingest_accepts_minimal_payload(loupe_home: Path) -> None:
    store = JSONLStore(root=loupe_home / "traces")
    t = ingest({"name": "tiny", "steps": []}, store=store)
    assert t.framework is None
    assert t.steps == []
    assert (loupe_home / "traces" / f"{t.trace_id}.jsonl").exists()


def test_ingest_accepts_user_defined_kind(loupe_home: Path) -> None:
    """Free-form kinds: user code uses domain-specific names like 'plan'."""
    store = JSONLStore(root=loupe_home / "traces")
    payload = _payload()
    payload["steps"][0]["kind"] = "user-defined-step"
    trace = ingest(payload, store=store)
    assert trace.steps[0].kind == "user-defined-step"


def test_ingest_rejects_empty_kind(loupe_home: Path) -> None:
    store = JSONLStore(root=loupe_home / "traces")
    bad = _payload()
    bad["steps"][0]["kind"] = ""
    with pytest.raises(IngestError, match=r"steps\[0\].kind"):
        ingest(bad, store=store)


def test_ingest_rejects_oversized_kind(loupe_home: Path) -> None:
    store = JSONLStore(root=loupe_home / "traces")
    bad = _payload()
    bad["steps"][0]["kind"] = "k" * 65
    with pytest.raises(IngestError, match=r"steps\[0\].kind"):
        ingest(bad, store=store)


def test_ingest_rejects_missing_name(loupe_home: Path) -> None:
    store = JSONLStore(root=loupe_home / "traces")
    with pytest.raises(IngestError, match="trace.name"):
        ingest({"steps": []}, store=store)


def test_ingest_rejects_non_list_steps(loupe_home: Path) -> None:
    store = JSONLStore(root=loupe_home / "traces")
    with pytest.raises(IngestError, match="trace.steps"):
        ingest({"name": "x", "steps": "not a list"}, store=store)


def test_http_ingest_endpoint(loupe_home: Path) -> None:
    client = TestClient(create_app())
    res = client.post("/api/traces", json=_payload())
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "go-agent-test"
    assert body["framework"] == "go-anthropic"
    assert body["step_count"] == 1

    # Round-trip: the ingested trace should now be in the GET list
    listed = client.get("/api/traces").json()
    assert any(t["trace_id"] == body["trace_id"] for t in listed)


def test_http_ingest_validation_errors(loupe_home: Path) -> None:
    client = TestClient(create_app())

    # 422 for malformed but parseable — empty kind is invalid (must be a non-empty string)
    bad = _payload()
    bad["steps"][0]["kind"] = ""
    res = client.post("/api/traces", json=bad)
    assert res.status_code == 422
    assert "kind" in res.json()["detail"]

    # 422 for kind exceeding the 64-char cap
    too_long = _payload()
    too_long["steps"][0]["kind"] = "k" * 65
    res = client.post("/api/traces", json=too_long)
    assert res.status_code == 422

    # 422 for missing required field
    res = client.post("/api/traces", json={"steps": []})
    assert res.status_code == 422


def test_http_ingest_curl_style_minimal(loupe_home: Path) -> None:
    """A bare minimum payload — what a curl one-liner would send."""
    client = TestClient(create_app())
    res = client.post(
        "/api/traces",
        json={
            "name": "curl-hello",
            "steps": [{"kind": "thought", "name": "from curl"}],
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["step_count"] == 1
    assert body["name"] == "curl-hello"
