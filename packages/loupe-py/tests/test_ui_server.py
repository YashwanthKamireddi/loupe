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
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
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


# ---------------------------------------------------------------------------
# /api/traces?q=… — server-side search across trace header + step content
# ---------------------------------------------------------------------------


def _make_named_trace(home: Path, name: str, step_kind: str = "llm-call",
                      step_name: str = "fake-model") -> None:
    store = JSONLStore(root=home / "traces")
    from loupe import record_step as _rs
    from loupe import trace as _t

    @_t(name=name, framework="test", store=store)
    def agent() -> None:
        _rs(step_kind, step_name)

    agent()


def test_list_traces_search_matches_header(loupe_home: Path) -> None:
    _make_named_trace(loupe_home, "alpha-agent")
    _make_named_trace(loupe_home, "beta-bot")
    client = TestClient(create_app())
    data = client.get("/api/traces?q=alpha").json()
    assert len(data) == 1
    assert data[0]["name"] == "alpha-agent"
    assert data[0]["match"]["header"] is True


def test_list_traces_search_matches_step_content(loupe_home: Path) -> None:
    """A query that doesn't match the header SHOULD match step names."""
    _make_named_trace(loupe_home, "x-agent", step_kind="llm-call",
                      step_name="anthropic:claude-haiku-4-5")
    _make_named_trace(loupe_home, "y-agent", step_kind="llm-call",
                      step_name="openai:gpt-4o-mini")
    client = TestClient(create_app())
    data = client.get("/api/traces?q=claude").json()
    assert len(data) == 1
    assert data[0]["name"] == "x-agent"
    assert data[0]["match"]["steps"] is True
    assert data[0]["match"]["header"] is False


def test_list_traces_search_no_match_returns_empty(loupe_home: Path) -> None:
    _make_named_trace(loupe_home, "the-only-trace")
    client = TestClient(create_app())
    data = client.get("/api/traces?q=nonsense").json()
    assert data == []


def test_list_traces_search_empty_query_returns_all(loupe_home: Path) -> None:
    """An empty q means "no filter" — return every trace, no match field."""
    _make_named_trace(loupe_home, "x")
    _make_named_trace(loupe_home, "y")
    client = TestClient(create_app())
    data = client.get("/api/traces?q=").json()
    assert len(data) == 2
    assert "match" not in data[0]   # no match metadata when not filtering


# ---------------------------------------------------------------------------
# v0.0.59 — bulk-delete endpoint (dashboard multi-trace ops)
# ---------------------------------------------------------------------------


def test_bulk_delete_removes_traces(loupe_home: Path) -> None:
    """POST /api/traces/bulk-delete with a list of ids removes those JSONL
    files and returns the deleted set."""
    _make_named_trace(loupe_home, "agent-1")
    _make_named_trace(loupe_home, "agent-2")
    _make_named_trace(loupe_home, "agent-3")
    client = TestClient(create_app())

    data = client.get("/api/traces").json()
    assert len(data) == 3
    ids = [t["trace_id"] for t in data]

    res = client.post(
        "/api/traces/bulk-delete",
        json={"trace_ids": [ids[0], ids[1]]},
    )
    assert res.status_code == 200
    body = res.json()
    assert set(body["deleted"]) == {ids[0], ids[1]}
    assert body["not_found"] == []

    remaining = client.get("/api/traces").json()
    assert len(remaining) == 1
    assert remaining[0]["trace_id"] == ids[2]


def test_bulk_delete_records_not_found_without_failing(loupe_home: Path) -> None:
    """Unknown ids return in `not_found`, not as an error."""
    _make_named_trace(loupe_home, "agent-real")
    client = TestClient(create_app())
    trace_id = client.get("/api/traces").json()[0]["trace_id"]

    res = client.post(
        "/api/traces/bulk-delete",
        json={"trace_ids": [trace_id, "deadbeef"]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["deleted"] == [trace_id]
    assert body["not_found"] == ["deadbeef"]


def test_bulk_delete_rejects_non_array(loupe_home: Path) -> None:
    client = TestClient(create_app())
    res = client.post("/api/traces/bulk-delete", json={"trace_ids": "not-a-list"})
    assert res.status_code == 422


def test_bulk_delete_caps_batch_size_at_500(loupe_home: Path) -> None:
    """A misbehaving client must not be able to ask us to walk the whole
    disk in one shot."""
    client = TestClient(create_app())
    res = client.post(
        "/api/traces/bulk-delete",
        json={"trace_ids": [f"id-{i}" for i in range(501)]},
    )
    assert res.status_code == 413


def test_cost_timeseries_returns_zero_filled_days(loupe_home: Path) -> None:
    """With no traces, the endpoint still returns the requested window of
    days, each with usd=0, calls=0 — the chart needs a stable shape."""
    client = TestClient(create_app())
    res = client.get("/api/cost-timeseries?days=7")
    assert res.status_code == 200
    body = res.json()
    assert len(body["days"]) == 7
    for d in body["days"]:
        assert d["usd"] == 0
        assert d["calls"] == 0
        assert d["rate_limited"] == 0
        # Must be ISO date
        assert len(d["date"]) == 10 and d["date"][4] == "-" and d["date"][7] == "-"


def test_cost_timeseries_clamps_huge_window(loupe_home: Path) -> None:
    """A malicious client asking for days=10000 must not bust memory —
    the endpoint clamps to 90."""
    client = TestClient(create_app())
    res = client.get("/api/cost-timeseries?days=10000")
    assert res.status_code == 200
    assert len(res.json()["days"]) == 90


def test_cost_timeseries_attributes_call_to_correct_day(loupe_home: Path) -> None:
    """A captured trace with a real model + token counts must contribute
    to the right day's bucket."""
    import json as _json
    from datetime import date as _date

    # Manually write a trace timestamped at today's midnight UTC.
    trace_id = "0" * 32
    started = (
        _date.today().toordinal() - _date(1970, 1, 1).toordinal()
    ) * 86400  # midnight UTC of today
    header = {
        "_type": "trace",
        "trace_id": trace_id,
        "name": "billing-demo",
        "framework": "test",
        "started_at": float(started),
        "ended_at": float(started + 1),
        "metadata": {},
    }
    step = {
        "_type": "step",
        "step_id": "stp1",
        "parent_step_id": None,
        "kind": "llm-call",
        "name": "anthropic:claude-haiku-4-5",
        "started_at": float(started),
        "ended_at": float(started + 1),
        "inputs": {"provider": "anthropic", "model": "claude-haiku-4-5"},
        "outputs": {"status": 200, "input_tokens": 1000, "output_tokens": 200},
        "metadata": {},
        "error": None,
    }
    traces_dir = loupe_home / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{trace_id}.jsonl").write_text(
        _json.dumps(header) + "\n" + _json.dumps(step) + "\n",
        encoding="utf-8",
    )

    client = TestClient(create_app())
    res = client.get("/api/cost-timeseries?days=2")
    assert res.status_code == 200
    today = res.json()["days"][-1]
    assert today["calls"] == 1
    # claude-haiku-4-5 is priced, so usd must be > 0
    assert today["usd"] > 0


def test_bulk_delete_clears_annotations_alongside_trace(loupe_home: Path) -> None:
    """Deleting a trace also removes its annotation file, so the dashboard
    doesn't show stale tag counts after a refresh."""
    _make_named_trace(loupe_home, "tagged-agent")
    client = TestClient(create_app())
    trace_id = client.get("/api/traces").json()[0]["trace_id"]

    # Add an annotation directly via the API so the file is created.
    add = client.post(
        f"/api/traces/{trace_id}/annotations",
        json={"step_id": "abc1", "failure_category": "off-task", "severity": "low"},
    )
    assert add.status_code == 200

    ann_path = loupe_home / "annotations" / f"{trace_id}.json"
    assert ann_path.exists()

    res = client.post("/api/traces/bulk-delete", json={"trace_ids": [trace_id]})
    assert res.status_code == 200
    assert not ann_path.exists()
