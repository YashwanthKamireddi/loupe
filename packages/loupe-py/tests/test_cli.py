"""End-to-end CLI tests using Typer's CliRunner.

Every public `loupe …` command gets exercised against a clean LOUPE_HOME so
we catch regressions in the user-visible surface before they ship.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from loupe import record_step, trace
from loupe.cli import app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def loupe_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "loupe-home"
    home.mkdir()
    monkeypatch.setenv("LOUPE_HOME", str(home))
    # Force a wide terminal so Rich doesn't truncate trace names in tables.
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("FORCE_COLOR", "0")
    from loupe import store as store_mod

    store_mod._default = None
    return home


def _seed_one_trace(home: Path) -> str:
    """Seed exactly one trace and return THAT trace's id.

    `glob` order is not insertion-order on most filesystems, so we snapshot
    the directory before the run and diff to find the new file. Otherwise
    repeated calls would all return whichever file glob happens to surface
    first — silently breaking any test that wants distinct ids.
    """
    from loupe.store import JSONLStore

    traces_dir = home / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    before = {p.stem for p in traces_dir.glob("*.jsonl")}
    store = JSONLStore(root=traces_dir)

    @trace(name="cli-test-agent", framework="test", store=store)
    def agent() -> None:
        record_step("thought", "plan")
        record_step("llm-call", "fake", outputs={"text": "hi"})
        record_step("error", "boom", error="oh no")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        agent()

    after = {p.stem for p in traces_dir.glob("*.jsonl")}
    new = after - before
    assert len(new) == 1, f"expected exactly one new trace; got {len(new)}"
    return next(iter(new))


# ---------------------------------------------------------------------------
# Smoke: every command runs to completion
# ---------------------------------------------------------------------------


def test_welcome_screen_when_no_args(runner: CliRunner, loupe_home: Path) -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "L O U P E" in result.output
    # Empty-state copy
    assert "loupe start" in result.output or "loupe demo" in result.output


def test_version(runner: CliRunner, loupe_home: Path) -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "loupe" in result.output.lower()


def test_doctor(runner: CliRunner, loupe_home: Path) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "LOUPE_HOME" in result.output
    assert "python" in result.output


def test_providers(runner: CliRunner, loupe_home: Path) -> None:
    result = runner.invoke(app, ["providers"])
    assert result.exit_code == 0
    assert "Anthropic" in result.output
    assert "OpenAI" in result.output
    assert "Vertex AI" in result.output
    assert "Local server" in result.output


def test_list_empty(runner: CliRunner, loupe_home: Path) -> None:
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No traces yet" in result.output


def test_list_with_traces(runner: CliRunner, loupe_home: Path) -> None:
    _seed_one_trace(loupe_home)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "cli-test-agent" in result.output
    assert "failed" in result.output


# ---------------------------------------------------------------------------
# Trace inspection
# ---------------------------------------------------------------------------


def test_show_known_trace(runner: CliRunner, loupe_home: Path) -> None:
    trace_id = _seed_one_trace(loupe_home)
    result = runner.invoke(app, ["show", trace_id[:12]])
    assert result.exit_code == 0
    assert "cli-test-agent" in result.output
    assert "plan" in result.output
    assert "boom" in result.output


def test_show_unknown_trace_exits_nonzero(runner: CliRunner, loupe_home: Path) -> None:
    result = runner.invoke(app, ["show", "deadbeef"])
    assert result.exit_code == 1
    assert "No trace matching" in result.output


# ---------------------------------------------------------------------------
# Annotation workflow
# ---------------------------------------------------------------------------


def test_tag_and_annotations(runner: CliRunner, loupe_home: Path) -> None:
    trace_id = _seed_one_trace(loupe_home)
    # Find the failing step
    import json as _json
    path = (loupe_home / "traces" / f"{trace_id}.jsonl")
    failing_step = next(
        _json.loads(line)["step_id"]
        for line in path.read_text().splitlines()
        if _json.loads(line).get("error")
    )

    # Tag it
    result = runner.invoke(app, [
        "tag", trace_id[:12], failing_step[:8],
        "off-task",
        "--notes", "test note",
        "--severity", "high",
    ])
    assert result.exit_code == 0
    assert "tagged" in result.output

    # List shows it
    annot_result = runner.invoke(app, ["annotations", trace_id[:12]])
    assert annot_result.exit_code == 0
    assert "off-task" in annot_result.output
    assert "high" in annot_result.output

    # Untag
    untag_result = runner.invoke(app, ["untag", trace_id[:12], failing_step[:8]])
    assert untag_result.exit_code == 0
    assert "untagged" in untag_result.output

    # Now annotations is empty
    annot_result2 = runner.invoke(app, ["annotations", trace_id[:12]])
    assert "No annotations" in annot_result2.output


def test_export_with_tagged_failures(runner: CliRunner, loupe_home: Path, tmp_path: Path) -> None:
    trace_id = _seed_one_trace(loupe_home)
    # Tag the failing step
    import json as _json
    path = (loupe_home / "traces" / f"{trace_id}.jsonl")
    failing_step = next(
        _json.loads(line)["step_id"]
        for line in path.read_text().splitlines()
        if _json.loads(line).get("error")
    )
    runner.invoke(app, [
        "tag", trace_id[:12], failing_step[:8], "off-task",
        "--notes", "x", "--severity", "medium",
    ])
    out = tmp_path / "bench.jsonl"
    result = runner.invoke(app, ["export", "--out", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    assert out.read_text().strip(), "export file is empty"


def test_export_when_nothing_tagged(runner: CliRunner, loupe_home: Path, tmp_path: Path) -> None:
    _seed_one_trace(loupe_home)
    out = tmp_path / "bench.jsonl"
    result = runner.invoke(app, ["export", "--out", str(out)])
    assert result.exit_code == 0
    assert "Nothing to export" in result.output


# ---------------------------------------------------------------------------
# Reports + scaffolding
# ---------------------------------------------------------------------------


def test_report_to_stdout(runner: CliRunner, loupe_home: Path) -> None:
    trace_id = _seed_one_trace(loupe_home)
    result = runner.invoke(app, ["report", trace_id[:12]])
    assert result.exit_code == 0
    assert "# Case File · cli-test-agent" in result.output
    assert "## Steps" in result.output


def test_report_to_file(runner: CliRunner, loupe_home: Path, tmp_path: Path) -> None:
    trace_id = _seed_one_trace(loupe_home)
    out = tmp_path / "case.md"
    result = runner.invoke(app, ["report", trace_id[:12], "--out", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    assert "Case File" in out.read_text()


def test_init_scaffold(runner: CliRunner, loupe_home: Path, tmp_path: Path) -> None:
    target = tmp_path / "scaffold-target"
    result = runner.invoke(app, ["init", "demo-agent", "--dir", str(target)])
    assert result.exit_code == 0
    assert (target / "agent.py").exists()
    assert (target / "README.md").exists()
    src = (target / "agent.py").read_text()
    assert "from loupe import record_step, trace" in src


def test_init_refuses_non_empty_dir(runner: CliRunner, loupe_home: Path, tmp_path: Path) -> None:
    target = tmp_path / "non-empty"
    target.mkdir()
    (target / "preexisting.txt").write_text("hi")
    result = runner.invoke(app, ["init", "demo-agent", "--dir", str(target)])
    assert result.exit_code == 1
    assert "non-empty" in result.output.lower() or "refusing" in result.output.lower()


def test_demo_seeds_traces(runner: CliRunner, loupe_home: Path) -> None:
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0
    assert "seeded" in result.output

    # We should now see traces
    list_result = runner.invoke(app, ["list"])
    assert "happy-summary-agent" in list_result.output \
        or "auth-refactor-agent" in list_result.output \
        or "data-loader-agent" in list_result.output


# ---------------------------------------------------------------------------
# verify / stats / diff
# ---------------------------------------------------------------------------


def test_verify_single_trace_succeeds(
    runner: CliRunner, loupe_home: Path
) -> None:
    pytest.importorskip("jsonschema")
    trace_id = _seed_one_trace(loupe_home)
    result = runner.invoke(app, ["verify", trace_id[:12]])
    assert result.exit_code == 0
    assert "✓" in result.output
    assert "cli-test-agent" in result.output


def test_verify_with_all_flag_validates_every_trace(
    runner: CliRunner, loupe_home: Path
) -> None:
    pytest.importorskip("jsonschema")
    _seed_one_trace(loupe_home)
    # Make a second trace too
    _seed_one_trace(loupe_home)
    result = runner.invoke(app, ["verify", "--all"])
    assert result.exit_code == 0
    # Two ✓ lines for two traces
    assert result.output.count("✓") >= 2


def test_verify_requires_id_or_all_flag(
    runner: CliRunner, loupe_home: Path
) -> None:
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 1
    assert "pass a trace id" in result.output or "trace id" in result.output


def test_verify_unknown_trace_exits_nonzero(
    runner: CliRunner, loupe_home: Path
) -> None:
    pytest.importorskip("jsonschema")
    result = runner.invoke(app, ["verify", "doesnotexist"])
    assert result.exit_code == 1


def test_stats_with_traces(runner: CliRunner, loupe_home: Path) -> None:
    _seed_one_trace(loupe_home)
    _seed_one_trace(loupe_home)
    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    # Numbers + section headings
    assert "traces" in result.output
    assert "failed" in result.output
    assert "by framework" in result.output


def test_stats_empty_home(runner: CliRunner, loupe_home: Path) -> None:
    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    # Empty home should show the "no traces" hint
    assert "No traces yet" in result.output or "no traces" in result.output.lower()


def test_diff_two_traces(runner: CliRunner, loupe_home: Path) -> None:
    a = _seed_one_trace(loupe_home)
    b = _seed_one_trace(loupe_home)
    assert a != b
    result = runner.invoke(app, ["diff", a[:12], b[:12]])
    assert result.exit_code == 0
    assert "trace diff" in result.output
    assert "step alignment" in result.output
    # Two seeded traces have identical step names, so all rows should be equal.
    assert "cli-test-agent" in result.output


def test_diff_unknown_trace_exits_nonzero(
    runner: CliRunner, loupe_home: Path
) -> None:
    a = _seed_one_trace(loupe_home)
    result = runner.invoke(app, ["diff", a[:12], "deadbeefdead"])
    assert result.exit_code == 1
