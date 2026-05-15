"""Tests for the GET /api/traces/{id}/report endpoint (markdown export)."""

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
    from loupe import store as store_mod

    store_mod._default = None
    return home


def _make_failing_trace(home: Path) -> str:
    store = JSONLStore(root=home / "traces")

    @trace(name="report-test-agent", framework="test", store=store)
    def agent() -> None:
        record_step("thought", "plan")
        record_step("error", "boom", error="kaboom")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        agent()
    return list((home / "traces").glob("*.jsonl"))[0].stem


def test_report_endpoint_returns_markdown(loupe_home: Path) -> None:
    trace_id = _make_failing_trace(loupe_home)
    client = TestClient(create_app())
    res = client.get(f"/api/traces/{trace_id[:12]}/report")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    body = res.text
    assert "# Case File · report-test-agent" in body
    assert "## Steps" in body
    assert "## Failure detail" in body


def test_report_endpoint_404_on_unknown(loupe_home: Path) -> None:
    client = TestClient(create_app())
    res = client.get("/api/traces/nonexistentid/report")
    assert res.status_code == 404
