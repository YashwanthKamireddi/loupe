"""UI server annotation endpoint tests."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from loupe import record_step, trace  # noqa: E402
from loupe.store import JSONLStore  # noqa: E402
from loupe.ui.server import create_app  # noqa: E402


@pytest.fixture()
def loupe_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "loupe-home"
    home.mkdir()
    monkeypatch.setenv("LOUPE_HOME", str(home))
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
    from loupe import store as store_mod

    store_mod._default = None
    return home


def _make_one_trace(home: Path) -> tuple[str, str]:
    store = JSONLStore(root=home / "traces")
    captured: dict[str, str] = {}

    @trace(name="t", framework="test", store=store)
    def agent() -> None:
        s = record_step("error", "boom", error="kaboom")
        assert s is not None
        captured["step_id"] = s.step_id
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        agent()

    trace_id = list((home / "traces").glob("*.jsonl"))[0].stem
    return trace_id, captured["step_id"]


def test_stats_endpoint(loupe_home: Path) -> None:
    _make_one_trace(loupe_home)
    client = TestClient(create_app())
    res = client.get("/api/stats").json()
    assert res["trace_count"] == 1
    assert res["failed_count"] == 1
    assert res["step_count"] == 1
    assert res["annotation_count"] == 0


def test_create_and_get_annotation(loupe_home: Path) -> None:
    trace_id, step_id = _make_one_trace(loupe_home)
    client = TestClient(create_app())

    res = client.post(
        f"/api/traces/{trace_id[:12]}/annotations",
        json={
            "step_id": step_id,
            "failure_category": "unguarded-delete",
            "notes": "deleted src",
            "severity": "critical",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["failure_category"] == "unguarded-delete"

    res2 = client.get(f"/api/traces/{trace_id[:12]}/annotations").json()
    assert len(res2) == 1
    assert res2[0]["step_id"] == step_id

    trace_res = client.get(f"/api/traces/{trace_id[:12]}").json()
    assert len(trace_res["annotations"]) == 1


def test_delete_annotation(loupe_home: Path) -> None:
    trace_id, step_id = _make_one_trace(loupe_home)
    client = TestClient(create_app())
    client.post(
        f"/api/traces/{trace_id[:12]}/annotations",
        json={
            "step_id": step_id,
            "failure_category": "loop",
        },
    )
    res = client.delete(f"/api/traces/{trace_id[:12]}/annotations/{step_id}")
    assert res.status_code == 200
    assert res.json()["removed"] is True
    items = client.get(f"/api/traces/{trace_id[:12]}/annotations").json()
    assert items == []


def test_trace_list_includes_annotation_count(loupe_home: Path) -> None:
    trace_id, step_id = _make_one_trace(loupe_home)
    client = TestClient(create_app())
    client.post(
        f"/api/traces/{trace_id[:12]}/annotations",
        json={"step_id": step_id, "failure_category": "other"},
    )
    items = client.get("/api/traces").json()
    assert len(items) == 1
    assert items[0]["annotation_count"] == 1
