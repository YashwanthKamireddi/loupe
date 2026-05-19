"""End-to-end lifecycle smoke test.

ONE test that exercises every public CLI command in the order a real user
would hit them. If any single step regresses — capture, list, show,
schema-validate, tag, annotations, export, report markdown, report html,
diff, stats, untag — this test fails and the entire user-visible loop is
caught.

This is the "is the product still useable end-to-end" gate. It's slower
than a unit test (one trace captured for real, one HTML rendered, one
schema validated) but cheap enough to keep in the main suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from loupe import record_step, trace
from loupe.cli import app
from loupe.store import JSONLStore


@pytest.fixture()
def loupe_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "loupe-home"
    home.mkdir()
    monkeypatch.setenv("LOUPE_HOME", str(home))
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("FORCE_COLOR", "0")
    from loupe import store as store_mod

    store_mod._default = None
    return home


def _capture_failing_trace(home: Path, name: str = "lifecycle-agent") -> tuple[str, str]:
    """Real @trace + record_step path — same as a user would write."""
    store = JSONLStore(root=home / "traces")
    captured: dict[str, str] = {}

    @trace(name=name, framework="lifecycle", store=store)
    def agent() -> None:
        record_step("thought", "plan")
        record_step("tool-call", "fetch", inputs={"url": "https://example.com"})
        s = record_step(
            "error",
            "unguarded-delete",
            error="rm -rf src/ instead of src/old.py",
        )
        assert s is not None
        captured["step"] = s.step_id
        raise RuntimeError("agent went off the rails")

    traces_dir = home / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    before = {p.stem for p in traces_dir.glob("*.jsonl")}
    with pytest.raises(RuntimeError):
        agent()
    after = {p.stem for p in traces_dir.glob("*.jsonl")}
    new_ids = after - before
    assert len(new_ids) == 1
    return next(iter(new_ids)), captured["step"]


def test_full_user_lifecycle(loupe_home: Path, tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    runner = CliRunner()

    # 1. CAPTURE — real agent run that fails
    trace_id, fail_step = _capture_failing_trace(loupe_home)
    assert (loupe_home / "traces" / f"{trace_id}.jsonl").exists()

    # 2. LIST shows the trace
    r = runner.invoke(app, ["list"])
    assert r.exit_code == 0
    assert "lifecycle-agent" in r.output
    assert "failed" in r.output

    # 3. SHOW prints the steps
    r = runner.invoke(app, ["show", trace_id[:12]])
    assert r.exit_code == 0
    assert "plan" in r.output
    assert "fetch" in r.output
    assert "unguarded-delete" in r.output

    # 4. VERIFY validates the captured JSONL against the canonical schema
    r = runner.invoke(app, ["verify", trace_id[:12]])
    assert r.exit_code == 0, f"verify failed: {r.output}"
    assert "✓" in r.output

    # 5. TAG the failure step for LoupeBench
    r = runner.invoke(app, [
        "tag", trace_id[:12], fail_step[:8],
        "unguarded-delete",
        "--notes", "agent issued rm -rf instead of rm",
        "--mitigation", "wrap rm in a path-prefix guard",
        "--severity", "critical",
    ])
    assert r.exit_code == 0
    assert "tagged" in r.output

    # 6. ANNOTATIONS lists what we just added
    r = runner.invoke(app, ["annotations", trace_id[:12]])
    assert r.exit_code == 0
    assert "unguarded-delete" in r.output
    assert "critical" in r.output

    # 7. EXPORT bundles the tagged failure into LoupeBench JSONL
    out_jsonl = tmp_path / "bench.jsonl"
    r = runner.invoke(app, ["export", "--out", str(out_jsonl)])
    assert r.exit_code == 0
    assert out_jsonl.exists()
    record = json.loads(out_jsonl.read_text().strip())
    assert record["annotation"]["failure_category"] == "unguarded-delete"
    assert record["annotation"]["severity"] == "critical"

    # 8. REPORT renders shareable markdown
    out_md = tmp_path / "case.md"
    r = runner.invoke(app, ["report", trace_id[:12], "--out", str(out_md)])
    assert r.exit_code == 0
    md = out_md.read_text()
    assert "# Case File · lifecycle-agent" in md
    assert "unguarded-delete" in md
    assert "wrap rm in a path-prefix guard" in md

    # 9. REPORT --html renders the standalone single-file viewer
    out_html = tmp_path / "case.html"
    r = runner.invoke(app, ["report", trace_id[:12], "--html", "--out", str(out_html)])
    assert r.exit_code == 0
    html = out_html.read_text()
    assert html.startswith("<!DOCTYPE html>")
    assert "lifecycle-agent" in html
    # Standalone: no external HTTP includes
    assert "src=\"http" not in html
    assert "link rel=\"stylesheet\" href=\"http" not in html

    # 10. STATS shows the new trace + the annotation in the overview
    r = runner.invoke(app, ["stats"])
    assert r.exit_code == 0
    assert "traces" in r.output
    assert "failed" in r.output
    assert "unguarded-delete" in r.output  # category histogram

    # 11. SECOND CAPTURE + DIFF — two-trace comparison
    trace_id_2, _ = _capture_failing_trace(loupe_home, name="lifecycle-agent-v2")
    assert trace_id != trace_id_2
    r = runner.invoke(app, ["diff", trace_id[:12], trace_id_2[:12]])
    assert r.exit_code == 0
    assert "trace diff" in r.output
    assert "step alignment" in r.output

    # 12. VERIFY --all walks every captured trace
    r = runner.invoke(app, ["verify", "--all"])
    assert r.exit_code == 0
    assert r.output.count("✓") >= 2  # two traces, both valid

    # 13. UNTAG removes the annotation
    r = runner.invoke(app, ["untag", trace_id[:12], fail_step[:8]])
    assert r.exit_code == 0
    assert "untagged" in r.output

    r = runner.invoke(app, ["annotations", trace_id[:12]])
    assert "No annotations" in r.output
