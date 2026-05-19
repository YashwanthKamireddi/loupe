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


# ---------------------------------------------------------------------------
# Security / production-readiness — these are the audit findings turned tests
# so the hardening doesn't regress.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "evil_id",
    [
        "*",          # would match every trace via glob
        "[abc]",      # bracket pattern
        "..",         # traversal segment
        ".hidden",    # dotfile-style
        "../etc/passwd",  # blocked at the slash
    ],
)
def test_trace_id_validation_rejects_unsafe_ids(
    loupe_home: Path, evil_id: str
) -> None:
    """`_find_trace` must reject ids that would widen or escape the glob.

    Skips control characters and `?`: httpx's URL parser rejects those
    before the request leaves the client, so they're never reachable via
    the wire — defense-in-depth at the server is exercised by the unit
    test below that calls `_find_trace` directly.
    """
    _make_one_trace(loupe_home)
    client = TestClient(create_app())
    res = client.get(f"/api/traces/{evil_id}")
    # Either 404 (sanitized away) or 400 (rejected at the route level);
    # never 200 / a real trace.
    assert res.status_code in (400, 404), res.text


def test_find_trace_unit_rejects_control_chars(loupe_home: Path) -> None:
    """Direct unit test for the validator — covers control chars + ?
    that the HTTP layer would refuse to transmit."""
    from loupe.ui.server import _find_trace

    _make_one_trace(loupe_home)
    traces = loupe_home / "traces"
    for evil in ("foo\x00bar", "foo\nbar", "foo\tbar", "?", "a?b", "foo\\bar"):
        assert _find_trace(traces, evil) is None, f"should reject {evil!r}"


def test_glob_wildcard_does_not_leak_traces(loupe_home: Path) -> None:
    """A bare '*' must not return the first trace via glob expansion."""
    _make_one_trace(loupe_home)
    client = TestClient(create_app())
    res = client.get("/api/traces/*")
    assert res.status_code == 404
    assert res.json() == {"detail": "trace not found"}


def test_ingest_rejects_oversized_body(loupe_home: Path) -> None:
    """POST /api/traces must cap body size — protects local users from
    runaway clients shipping multi-GB JSON."""
    client = TestClient(create_app())
    # 9 MB of harmless ascii — over the 8 MB cap.
    huge = b"x" * (9 * 1024 * 1024)
    res = client.post(
        "/api/traces",
        content=huge,
        headers={"content-type": "application/json"},
    )
    assert res.status_code == 413
    assert "exceeds" in res.json()["detail"]


def test_ingest_rejects_oversized_content_length_pre_read(loupe_home: Path) -> None:
    """A declared Content-Length over the cap must 413 without buffering."""
    client = TestClient(create_app())
    res = client.post(
        "/api/traces",
        content=b"{}",  # tiny body, but declare a huge content-length
        headers={"content-length": str(100 * 1024 * 1024)},
    )
    assert res.status_code == 413
