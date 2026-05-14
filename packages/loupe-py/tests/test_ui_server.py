"""UI server smoke tests — verify endpoints respond and serve real trace data."""

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
    # ensure the default store picks up the new env
    from loupe import store as store_mod

    store_mod._default = None
    return home


def _make_one_trace(home: Path) -> None:
    store = JSONLStore(root=home / "traces")

    @trace(name="ui-test-agent", framework="test", store=store)
    def agent() -> None:
        record_step("thought", "plan")
        record_step("llm-call", "fake-model", outputs={"text": "hi"})

    agent()


def test_traces_endpoint_lists(loupe_home: Path) -> None:
    _make_one_trace(loupe_home)
    client = TestClient(create_app())
    res = client.get("/api/traces")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["name"] == "ui-test-agent"
    assert data[0]["step_count"] == 2


def test_trace_detail(loupe_home: Path) -> None:
    _make_one_trace(loupe_home)
    client = TestClient(create_app())
    list_res = client.get("/api/traces").json()
    trace_id = list_res[0]["trace_id"]

    res = client.get(f"/api/traces/{trace_id[:12]}")
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "ui-test-agent"
    assert len(data["steps"]) == 2
    assert data["steps"][0]["name"] == "plan"


def test_unknown_trace_404(loupe_home: Path) -> None:
    client = TestClient(create_app())
    res = client.get("/api/traces/doesnotexist")
    assert res.status_code == 404


def test_root_serves_html(loupe_home: Path) -> None:
    client = TestClient(create_app())
    res = client.get("/")
    assert res.status_code == 200
    assert "loupe" in res.text.lower()
    assert res.headers["content-type"].startswith("text/html")
