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
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
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
    # New minimal banner renders the brand as lowercase "loupe" with a ◉ mark.
    assert "loupe" in result.output.lower()
    # Empty-state copy points at the real first-run flow.
    assert "loupe init" in result.output


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


def test_show_displays_llm_reply_content(
    runner: CliRunner, loupe_home: Path,
) -> None:
    """`loupe show` must surface the actual model reply, not just step
    names — the captured content IS the forensic payload. The seed's
    llm-call has outputs {"text": "hi"}."""
    trace_id = _seed_one_trace(loupe_home)
    result = runner.invoke(app, ["show", trace_id[:12]])
    assert result.exit_code == 0
    assert "reply" in result.output
    assert "hi" in result.output


def test_show_displays_prompt_content(
    runner: CliRunner, loupe_home: Path,
) -> None:
    """A captured prompt (messages / contents / prompt) renders inline."""
    from loupe import record_step, trace
    from loupe.store import JSONLStore

    traces_dir = loupe_home / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    before = {p.stem for p in traces_dir.glob("*.jsonl")}
    store = JSONLStore(root=traces_dir)

    @trace(name="prompt-agent", framework="test", store=store)
    def agent() -> None:
        record_step(
            "llm-call", "gemini:gemini-2.5-flash",
            inputs={"messages": [{"role": "user", "content": "what is observability?"}]},
            outputs={"text": "Seeing what your system does.",
                     "input_tokens": 5, "output_tokens": 6},
        )

    agent()
    tid = next(iter({p.stem for p in traces_dir.glob("*.jsonl")} - before))
    result = runner.invoke(app, ["show", tid[:12]])
    assert result.exit_code == 0
    assert "prompt" in result.output
    assert "what is observability?" in result.output
    assert "Seeing what your system does." in result.output
    assert "tokens" in result.output  # token summary line


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


# ---------------------------------------------------------------------------
# doctor --smoke + ui port handling
# ---------------------------------------------------------------------------


def test_doctor_smoke_runs_lifecycle(
    runner: CliRunner, loupe_home: Path
) -> None:
    """--smoke runs a 4-step end-to-end check inside a tmp dir and exits 0."""
    pytest.importorskip("jsonschema")
    result = runner.invoke(app, ["doctor", "--smoke"])
    assert result.exit_code == 0, result.output
    assert "capture trace" in result.output
    assert "parse JSONL" in result.output
    assert "schema validate" in result.output
    assert "tag + untag" in result.output
    assert "smoke test passed" in result.output


def test_ui_no_auto_port_exits_when_busy(
    runner: CliRunner, loupe_home: Path
) -> None:
    """With --no-auto-port, a busy port produces a clean error + exit 1."""
    import socket as _socket

    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        result = runner.invoke(
            app, ["ui", "--port", str(port), "--no-auto-port"]
        )
        assert result.exit_code == 1
        assert "already in use" in result.output
        assert "Traceback" not in result.output
    finally:
        sock.close()


def test_ui_auto_port_walks_forward(
    loupe_home: Path,
) -> None:
    """The port resolver walks forward when the start port is busy."""
    import socket as _socket

    from loupe.cli import _resolve_port

    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        busy = sock.getsockname()[1]
        resolved = _resolve_port("127.0.0.1", busy, search=True)
        assert resolved is not None
        assert resolved != busy
        assert busy < resolved <= busy + 9
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# purge — trace lifecycle / disk-space management
# ---------------------------------------------------------------------------


def _age_file(path: Path, age_seconds: float) -> None:
    """Backdate a file's mtime so the purge command sees it as older than N."""
    import os
    import time as _time

    target_mtime = _time.time() - age_seconds
    os.utime(path, (target_mtime, target_mtime))


def test_purge_dry_run_does_not_delete(
    runner: CliRunner, loupe_home: Path
) -> None:
    """Without --yes, purge must only list candidates — never touch disk."""
    trace_id = _seed_one_trace(loupe_home)
    trace_path = loupe_home / "traces" / f"{trace_id}.jsonl"
    _age_file(trace_path, 86400 * 30)  # 30 days

    result = runner.invoke(app, ["purge", "--older-than", "7d"])
    assert result.exit_code == 0, result.output
    assert "would delete 1" in result.output
    assert trace_id[:12] in result.output
    assert "loupe purge --older-than 7d --yes" in result.output
    assert trace_path.exists(), "dry-run must not delete files"


def _first_step_id(jsonl_path: Path) -> str:
    """Return the first step_id from a trace JSONL — for use with `loupe tag`."""
    import json as _json

    for line in jsonl_path.read_text().splitlines():
        obj = _json.loads(line)
        if obj.get("_type") == "step":
            return str(obj["step_id"])
    raise AssertionError(f"no step found in {jsonl_path}")


def test_purge_with_yes_actually_deletes(
    runner: CliRunner, loupe_home: Path
) -> None:
    """--yes must remove the trace JSONL AND its annotation sidecar."""
    trace_id = _seed_one_trace(loupe_home)
    trace_path = loupe_home / "traces" / f"{trace_id}.jsonl"
    step_id = _first_step_id(trace_path)
    _age_file(trace_path, 86400 * 30)

    # Drop an annotation so we can confirm the sidecar gets cleaned up too.
    tag = runner.invoke(app, ["tag", trace_id[:12], step_id[:8], "regression"])
    assert tag.exit_code == 0, tag.output
    sidecar = loupe_home / "annotations" / f"{trace_id}.json"
    assert sidecar.exists()

    result = runner.invoke(app, ["purge", "--older-than", "7d", "--yes"])
    assert result.exit_code == 0, result.output
    assert "deleted 1" in result.output
    assert not trace_path.exists()
    assert not sidecar.exists(), "annotation sidecar must be cleaned up"


def test_purge_keep_tagged_skips_annotated(
    runner: CliRunner, loupe_home: Path
) -> None:
    """--keep-tagged is a safety: annotated traces are part of the bench set."""
    keep_id = _seed_one_trace(loupe_home)
    drop_id = _seed_one_trace(loupe_home)
    keep_step = _first_step_id(loupe_home / "traces" / f"{keep_id}.jsonl")
    for tid in (keep_id, drop_id):
        _age_file(loupe_home / "traces" / f"{tid}.jsonl", 86400 * 30)

    # Annotate only one.
    tag_result = runner.invoke(app, ["tag", keep_id[:12], keep_step[:8], "regression"])
    assert tag_result.exit_code == 0, tag_result.output

    result = runner.invoke(
        app, ["purge", "--older-than", "7d", "--yes", "--keep-tagged"]
    )
    assert result.exit_code == 0, result.output
    assert "deleted 1" in result.output
    assert (loupe_home / "traces" / f"{keep_id}.jsonl").exists()
    assert not (loupe_home / "traces" / f"{drop_id}.jsonl").exists()


def test_purge_no_match_is_a_clean_no_op(
    runner: CliRunner, loupe_home: Path
) -> None:
    """Recent traces below the threshold leave the disk untouched."""
    trace_id = _seed_one_trace(loupe_home)
    # Don't age the file — it's brand new.
    result = runner.invoke(app, ["purge", "--older-than", "7d", "--yes"])
    assert result.exit_code == 0, result.output
    assert "no traces older than 7d" in result.output
    assert (loupe_home / "traces" / f"{trace_id}.jsonl").exists()


def test_purge_rejects_invalid_duration(
    runner: CliRunner, loupe_home: Path
) -> None:
    """A malformed duration must exit 1 with a readable error, no traceback."""
    result = runner.invoke(app, ["purge", "--older-than", "banana"])
    assert result.exit_code == 1
    assert "invalid duration" in result.output
    assert "Traceback" not in result.output


def test_purge_empty_home_is_a_clean_no_op(
    runner: CliRunner, loupe_home: Path
) -> None:
    """An empty LOUPE_HOME must not crash — common on first install."""
    result = runner.invoke(app, ["purge", "--older-than", "1h", "--yes"])
    assert result.exit_code == 0
    assert "no traces" in result.output


def test_parse_duration_accepts_all_suffixes() -> None:
    """Unit-test the duration parser directly."""
    from loupe.cli import _parse_duration

    assert _parse_duration("30s") == 30.0
    assert _parse_duration("5m") == 300.0
    assert _parse_duration("2h") == 7200.0
    assert _parse_duration("7d") == 7 * 86400.0
    assert _parse_duration("3600") == 3600.0  # bare number = seconds
    assert _parse_duration("0d") == 0.0


# ---------------------------------------------------------------------------
# loupe replay — _extract_replay_inputs helper
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    import json as _json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(_json.dumps(line) for line in lines) + "\n",
        encoding="utf-8",
    )


def test_replay_extracts_prompt_from_plan_step(
    loupe_home: Path,
) -> None:
    """The loupe-init scaffold puts the question in plan.outputs.q —
    replay must pick it up there first."""
    from loupe.cli import _extract_replay_inputs

    p = loupe_home / "traces" / "alpha000000.jsonl"
    _write_jsonl(p, [
        {"_type": "trace", "trace_id": "alpha000000", "name": "x",
         "framework": "gemini", "started_at": 1.0, "ended_at": 2.0},
        {"_type": "step", "step_id": "s1", "kind": "plan",
         "name": "compose prompt", "started_at": 1.0, "ended_at": 1.1,
         "outputs": {"q": "what is the capital of France?"}},
        {"_type": "step", "step_id": "s2", "kind": "llm-call",
         "name": "gemini:gemini-2.5-flash", "started_at": 1.1, "ended_at": 1.9,
         "inputs": {"provider": "gemini", "model": "gemini-2.5-flash"},
         "outputs": {"text": "Paris"}},
    ])
    header, prompt, model, framework = _extract_replay_inputs(p)
    assert header is not None and header["trace_id"] == "alpha000000"
    assert prompt == "what is the capital of France?"
    assert model == "gemini-2.5-flash"
    assert framework == "gemini"


def test_replay_falls_back_to_llm_call_inputs_contents(
    loupe_home: Path,
) -> None:
    """If there's no plan step, replay must reach into the llm-call inputs."""
    from loupe.cli import _extract_replay_inputs

    p = loupe_home / "traces" / "beta00000000.jsonl"
    _write_jsonl(p, [
        {"_type": "trace", "trace_id": "beta00000000", "name": "y",
         "framework": "gemini", "started_at": 1.0, "ended_at": 2.0},
        {"_type": "step", "step_id": "s1", "kind": "llm-call",
         "name": "gemini:gemini-2.0-flash", "started_at": 1.0, "ended_at": 1.5,
         "inputs": {"contents": "hello world", "model": "gemini-2.0-flash"},
         "outputs": {"text": "hi"}},
    ])
    _h, prompt, model, _fw = _extract_replay_inputs(p)
    assert prompt == "hello world"
    assert model == "gemini-2.0-flash"


def test_replay_parses_model_from_step_name_when_inputs_lack_it(
    loupe_home: Path,
) -> None:
    """When universal-httpx captured a step but the body had no 'model'
    field (Gemini's case), the model lives in the step's name as
    'gemini:gemini-X.Y-flash'. Replay must parse it out."""
    from loupe.cli import _extract_replay_inputs

    p = loupe_home / "traces" / "gamma0000000.jsonl"
    _write_jsonl(p, [
        {"_type": "trace", "trace_id": "gamma0000000", "name": "z",
         "framework": "gemini", "started_at": 1.0, "ended_at": 2.0},
        {"_type": "step", "step_id": "s1", "kind": "plan",
         "name": "compose prompt", "started_at": 1.0, "ended_at": 1.1,
         "outputs": {"q": "test"}},
        {"_type": "step", "step_id": "s2", "kind": "llm-call",
         "name": "gemini:gemini-2.5-pro", "started_at": 1.1, "ended_at": 1.9,
         "inputs": {"provider": "gemini"},   # no model key
         "outputs": {"text": "ok"}},
    ])
    _h, _p, model, _fw = _extract_replay_inputs(p)
    assert model == "gemini-2.5-pro"


def test_replay_returns_empty_strings_when_trace_unrecognized(
    loupe_home: Path,
) -> None:
    """Defensive: a trace with no plan + no llm-call should not crash —
    returns empty prompt + model so the CLI can ask for --prompt overrides."""
    from loupe.cli import _extract_replay_inputs

    p = loupe_home / "traces" / "empty0000000.jsonl"
    _write_jsonl(p, [
        {"_type": "trace", "trace_id": "empty0000000", "name": "empty",
         "framework": "gemini", "started_at": 1.0, "ended_at": 2.0},
        {"_type": "step", "step_id": "s1", "kind": "io",
         "name": "weird", "started_at": 1.0, "ended_at": 1.1},
    ])
    header, prompt, model, framework = _extract_replay_inputs(p)
    assert header is not None
    assert prompt == ""
    assert model == ""
    assert framework == "gemini"


def test_replay_cli_unknown_trace_exits_one(
    runner: CliRunner, loupe_home: Path
) -> None:
    res = runner.invoke(app, ["replay", "deadbeefdead"])
    assert res.exit_code == 1


def test_replay_cli_unknown_framework_errors_clean(
    runner: CliRunner, loupe_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognized framework gets a clean error instead of a traceback."""
    p = loupe_home / "traces" / "alpha111111111.jsonl"
    _write_jsonl(p, [
        {"_type": "trace", "trace_id": "alpha111111111", "name": "x",
         "framework": "some-bespoke-framework", "started_at": 1.0, "ended_at": 2.0},
        {"_type": "step", "step_id": "s1", "kind": "plan",
         "name": "plan", "started_at": 1.0, "ended_at": 1.1,
         "outputs": {"q": "hello"}},
        {"_type": "step", "step_id": "s2", "kind": "llm-call",
         "name": "fake:fake-model", "started_at": 1.1, "ended_at": 1.9,
         "inputs": {"model": "fake-model"}, "outputs": {"text": "hi"}},
    ])
    res = runner.invoke(app, ["replay", "alpha1111"])
    assert res.exit_code == 1
    assert "does not recognize framework" in res.output
    assert "Traceback" not in res.output


def test_replay_cli_missing_api_key_errors_clean(
    runner: CliRunner, loupe_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic replay without ANTHROPIC_API_KEY errors cleanly."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = loupe_home / "traces" / "ant1111111111.jsonl"
    _write_jsonl(p, [
        {"_type": "trace", "trace_id": "ant1111111111", "name": "x",
         "framework": "anthropic", "started_at": 1.0, "ended_at": 2.0},
        {"_type": "step", "step_id": "s1", "kind": "plan",
         "name": "plan", "started_at": 1.0, "ended_at": 1.1,
         "outputs": {"q": "hello"}},
        {"_type": "step", "step_id": "s2", "kind": "llm-call",
         "name": "anthropic:claude-haiku-4-5", "started_at": 1.1,
         "ended_at": 1.9,
         "inputs": {"model": "claude-haiku-4-5"}, "outputs": {"text": "hi"}},
    ])
    res = runner.invoke(app, ["replay", "ant11111"])
    assert res.exit_code == 1
    assert "ANTHROPIC_API_KEY" in res.output
    assert "Traceback" not in res.output


def test_replay_resolves_anthropic_and_openai_backends(
    loupe_home: Path,
) -> None:
    """Backend resolver wires the three supported frameworks."""
    from loupe.cli import _resolve_replay_backend

    assert _resolve_replay_backend("gemini",    "p", "m", "src") is not None
    assert _resolve_replay_backend("google",    "p", "m", "src") is not None
    assert _resolve_replay_backend("anthropic", "p", "m", "src") is not None
    assert _resolve_replay_backend("openai",    "p", "m", "src") is not None
    # And aliases are case-insensitive.
    assert _resolve_replay_backend("ANTHROPIC", "p", "m", "src") is not None
    # Unknown framework returns None — caller decides what to do.
    assert _resolve_replay_backend("langgraph", "p", "m", "src") is None


# ---------------------------------------------------------------------------
# loupe setup — interactive wizard (scripted flag path)
# ---------------------------------------------------------------------------


def test_setup_scripted_path_saves_config(
    runner: CliRunner, loupe_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`loupe setup --provider X --api-key K --no-browser` is the
    non-interactive path used by CI. It MUST save to config.toml and
    print 'saved to' even if the ping call fails."""
    # Force ping to fail so we don't accidentally hit the network.
    from loupe import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_ping_provider",
                        lambda *a, **kw: (False, "test mode — no network"))

    res = runner.invoke(app, [
        "setup",
        "--provider", "gemini",
        "--api-key", "AIza-test-key-12345",
        "--no-browser",
    ])
    assert res.exit_code == 0, res.output
    assert "saved to" in res.output

    # Reload the config and assert the key landed.
    from loupe.config import Config
    cfg = Config.load()
    assert cfg.providers["gemini"].api_key == "AIza-test-key-12345"
    assert cfg.default_provider == "gemini"


def test_setup_rejects_empty_key(
    runner: CliRunner, loupe_home: Path,
) -> None:
    res = runner.invoke(app, [
        "setup", "--provider", "gemini", "--api-key", "", "--no-browser",
    ], input="\n")  # blank stdin too
    assert res.exit_code == 1
    assert "no key provided" in res.output


def test_setup_rejects_unknown_provider(
    runner: CliRunner, loupe_home: Path,
) -> None:
    res = runner.invoke(app, [
        "setup", "--provider", "gpt-12-mega", "--api-key", "x", "--no-browser",
    ])
    assert res.exit_code == 1
    assert "unknown provider" in res.output


def test_setup_short_circuits_when_already_configured(
    runner: CliRunner, loupe_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a provider is already in env vars, `loupe setup` (no args)
    should report it and exit cleanly, not re-prompt."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-already-set")
    res = runner.invoke(app, ["setup"])
    assert res.exit_code == 0
    assert "already set up" in res.output


# ---------------------------------------------------------------------------
# loupe cost — LLM spend tracking
# ---------------------------------------------------------------------------


def _seed_llm_call_trace(home: Path, *, model: str, in_tokens: int,
                         out_tokens: int) -> str:
    """Capture a trace whose llm-call step has known token counts."""
    from loupe.store import JSONLStore

    traces_dir = home / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    before = {p.stem for p in traces_dir.glob("*.jsonl")}
    store = JSONLStore(root=traces_dir)

    @trace(name="cost-test", framework="test", store=store)
    def agent() -> None:
        record_step(
            "llm-call",
            f"provider:{model}",
            inputs={"model": model, "provider": "openai"},
            outputs={
                "status": 200,
                "input_tokens": in_tokens,
                "output_tokens": out_tokens,
                "text": "ok",
            },
        )

    agent()
    after = {p.stem for p in traces_dir.glob("*.jsonl")}
    return next(iter(after - before))


def test_cost_empty_home_returns_zero(
    runner: CliRunner, loupe_home: Path,
) -> None:
    res = runner.invoke(app, ["cost", "--json"])
    assert res.exit_code == 0
    import json as _json
    data = _json.loads(res.output.strip())
    assert data["total_usd"] == 0.0
    assert data["trace_count"] == 0


def test_cost_sums_priced_steps(
    runner: CliRunner, loupe_home: Path,
) -> None:
    """One trace with 1M input + 1M output tokens of gpt-4o-mini = $0.75."""
    _seed_llm_call_trace(
        loupe_home, model="gpt-4o-mini",
        in_tokens=1_000_000, out_tokens=1_000_000,
    )
    res = runner.invoke(app, ["cost", "--json"])
    assert res.exit_code == 0
    import json as _json
    data = _json.loads(res.output.strip())
    assert data["total_usd"] == 0.75
    assert data["step_count"] == 1
    assert data["unpriced_step_count"] == 0
    assert data["by_model"]["gpt-4o-mini"] == 0.75


def test_cost_marks_unknown_model_as_unpriced(
    runner: CliRunner, loupe_home: Path,
) -> None:
    """A model not in our pricing table shouldn't get silently $0'd —
    it goes to unpriced so users see the gap."""
    _seed_llm_call_trace(
        loupe_home, model="some-private-fine-tune-v17",
        in_tokens=100, out_tokens=50,
    )
    res = runner.invoke(app, ["cost", "--json"])
    assert res.exit_code == 0
    import json as _json
    data = _json.loads(res.output.strip())
    # Provider hint ('openai' in the fixture) gives a fallback price → priced.
    # If we ever change behavior so unknown-model + known-provider → unpriced,
    # update this assertion.
    assert data["step_count"] + data["unpriced_step_count"] == 1


def test_cost_pretty_output_renders(
    runner: CliRunner, loupe_home: Path,
) -> None:
    _seed_llm_call_trace(
        loupe_home, model="gpt-4o-mini",
        in_tokens=2_000_000, out_tokens=1_000_000,
    )
    res = runner.invoke(app, ["cost"])
    assert res.exit_code == 0
    assert "llm spend" in res.output
    assert "$0.90" in res.output     # 2 * 0.15 + 1 * 0.60 = 0.90


def test_cost_by_model_breakdown(
    runner: CliRunner, loupe_home: Path,
) -> None:
    _seed_llm_call_trace(
        loupe_home, model="gpt-4o-mini",
        in_tokens=1_000_000, out_tokens=0,
    )
    _seed_llm_call_trace(
        loupe_home, model="gpt-4o",
        in_tokens=1_000_000, out_tokens=0,
    )
    res = runner.invoke(app, ["cost", "--by-model", "--json"])
    import json as _json
    data = _json.loads(res.output.strip())
    # Both traces should be priced; total = 0.15 + 2.50 = 2.65
    assert data["step_count"] == 2, f"got data: {data}"
    assert data["total_usd"] == 2.65
    assert "gpt-4o-mini" in data["by_model"]
    assert "gpt-4o" in data["by_model"]


# ---------------------------------------------------------------------------
# loupe bench — regression replay
# ---------------------------------------------------------------------------


def test_bench_with_no_tagged_failures_exits_clean(
    runner: CliRunner, loupe_home: Path,
) -> None:
    """No tags → bench is a no-op, but informative + exit 0."""
    _seed_one_trace(loupe_home)   # captured but untagged
    res = runner.invoke(app, ["bench"])
    assert res.exit_code == 0
    assert "No tagged failures yet" in res.output
    assert "loupe tag" in res.output


def test_bench_filters_by_category(
    runner: CliRunner, loupe_home: Path,
) -> None:
    """--category restricts the replay set."""
    res = runner.invoke(app, [
        "bench", "--category", "definitely-not-a-real-category",
    ])
    assert res.exit_code == 0
    assert "No tagged failures in category" in res.output


# ---------------------------------------------------------------------------
# Did-you-mean typo suggestions + loupe explain
# ---------------------------------------------------------------------------


def test_typo_suggestion_offers_close_match() -> None:
    """`loupe sho` should suggest `loupe show` and exit 1 cleanly.

    Invoked through the active interpreter (`python -m loupe.cli`) so the
    test runs anywhere — a local .venv, CI's system Python, a tox env —
    without depending on a `.venv/bin/loupe` console script existing.
    """
    import subprocess
    import sys
    res = subprocess.run(
        [sys.executable, "-m", "loupe.cli", "sho"],
        capture_output=True, text=True,
    )
    assert res.returncode == 1
    out = (res.stdout + res.stderr).lower()
    assert "unknown command" in out
    assert "did you mean" in out
    assert "show" in out


def test_explain_lists_topics_with_no_arg(
    runner: CliRunner, loupe_home: Path,
) -> None:
    res = runner.invoke(app, ["explain"])
    assert res.exit_code == 0
    assert "trace" in res.output
    assert "attribution" in res.output


def test_explain_renders_topic_body(
    runner: CliRunner, loupe_home: Path,
) -> None:
    res = runner.invoke(app, ["explain", "trace"])
    assert res.exit_code == 0
    assert "captured agent run" in res.output.lower()
    assert "jsonl" in res.output.lower()


def test_explain_unknown_topic_suggests_close_match(
    runner: CliRunner, loupe_home: Path,
) -> None:
    res = runner.invoke(app, ["explain", "atribuion"])  # intentional typo
    assert res.exit_code == 1
    assert "unknown topic" in res.output
    assert "attribution" in res.output


# ---------------------------------------------------------------------------
# loupe ask / chat / run — Phase 2 zero-code paths
# ---------------------------------------------------------------------------


def test_ask_without_config_exits_with_hint(
    runner: CliRunner, loupe_home: Path,
) -> None:
    res = runner.invoke(app, ["ask", "what", "is", "observability"])
    assert res.exit_code == 1
    assert "no provider configured" in res.output


def test_ask_empty_question_shows_helpful_guidance(
    runner: CliRunner, loupe_home: Path,
) -> None:
    """`loupe ask` (no args) should NOT show Typer's ugly default error.
    Instead: a clean inline guide showing exactly how to call it."""
    res = runner.invoke(app, ["ask"])
    assert res.exit_code == 1
    # The guidance copy + concrete example
    assert "what do you want to ask?" in res.output
    assert "loupe ask" in res.output
    # NEVER Typer's default missing-argument banner
    assert "Missing argument" not in res.output


def test_ask_uses_configured_provider(
    runner: CliRunner, loupe_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: with a fake provider invoker, ask captures a trace
    and prints the answer."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-fake-test-key")

    from loupe import cli as cli_mod
    calls: list[tuple] = []

    def fake_invoke(provider, api_key, model, history):  # type: ignore[no-untyped-def]
        prompt = history[-1]["content"] if history else ""
        calls.append((provider, model, prompt))
        return f"reply to: {prompt}"

    monkeypatch.setattr(cli_mod, "_invoke_with_history", fake_invoke)

    res = runner.invoke(app, ["ask", "what", "is", "loupe"])
    assert res.exit_code == 0, res.output
    assert calls, "the invoker should have been called"
    assert calls[0][2] == "what is loupe"
    assert "reply to: what is loupe" in res.output

    # And a trace landed on disk.
    traces = list((loupe_home / "traces").glob("*.jsonl"))
    assert len(traces) == 1


def test_chat_without_config_exits_with_hint(
    runner: CliRunner, loupe_home: Path,
) -> None:
    res = runner.invoke(app, ["chat"])
    assert res.exit_code == 1
    assert "no provider configured" in res.output


def test_run_requires_args(
    runner: CliRunner, loupe_home: Path,
) -> None:
    """`loupe run` with nothing fails cleanly — typer rejects it first."""
    res = runner.invoke(app, ["run"])
    # Typer raises a usage error (exit 2) before our code runs.
    assert res.exit_code != 0


def test_run_with_missing_script_exits_with_hint(
    runner: CliRunner, loupe_home: Path,
) -> None:
    res = runner.invoke(app, ["run", "/no/such/file.py"])
    assert res.exit_code == 1
    assert "no such file" in res.output


def test_run_executes_script_and_captures_trace(
    runner: CliRunner, loupe_home: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tiny script that uses @trace should produce a captured trace
    when run through `loupe run`. We don't need a real LLM here — the
    script just records a step itself, which is enough to prove the
    full capture pipeline (including patch_all + @trace wrapping) ran.
    """
    # Re-enable the indexer so this test exercises the real wrapping
    # path end-to-end.
    monkeypatch.delenv("LOUPE_DISABLE_INDEX", raising=False)

    script = tmp_path / "tiny_agent.py"
    script.write_text(
        "from loupe import record_step\n"
        "record_step('thought', 'inside tiny_agent', outputs={'a': 1})\n"
        "print('hello from tiny_agent')\n",
        encoding="utf-8",
    )

    res = runner.invoke(app, ["run", str(script)])
    assert res.exit_code == 0, res.output
    assert "running tiny_agent.py" in res.output
    assert "hello from tiny_agent" in res.output

    # The outer @trace wrap means we should have one captured trace.
    traces = list((loupe_home / "traces").glob("*.jsonl"))
    assert len(traces) == 1, f"expected 1 trace, got {len(traces)}: {traces}"
    # The captured trace's name follows our `run:{stem}` convention.
    import json as _json
    header = _json.loads(traces[0].read_text().splitlines()[0])
    assert header["name"] == "run:tiny_agent"
    assert header["framework"] == "loupe-run"


# ---------------------------------------------------------------------------
# Smart router — `loupe` with no args
# ---------------------------------------------------------------------------


def test_smart_router_falls_back_to_welcome_in_non_tty(
    runner: CliRunner, loupe_home: Path,
) -> None:
    """CliRunner is non-TTY, so the router MUST NOT auto-launch setup
    (which would hang on input()). Instead it shows the welcome screen."""
    res = runner.invoke(app, [])
    assert res.exit_code == 0
    # No input prompts hit; welcome screen shown.
    assert "loupe init" in res.output or "loupe setup" in res.output


# ---------------------------------------------------------------------------
# JSON output mode — pipeable + scriptable
# ---------------------------------------------------------------------------


def test_list_json_empty_home(runner: CliRunner, loupe_home: Path) -> None:
    """An empty home returns [] in JSON mode — never a Rich banner."""
    import json as _json
    res = runner.invoke(app, ["list", "--json"])
    assert res.exit_code == 0
    assert _json.loads(res.output.strip()) == []


def test_list_json_with_traces(runner: CliRunner, loupe_home: Path) -> None:
    """JSON list returns full trace_ids, all expected fields."""
    import json as _json
    trace_id = _seed_one_trace(loupe_home)
    res = runner.invoke(app, ["list", "--json"])
    assert res.exit_code == 0
    data = _json.loads(res.output.strip())
    assert isinstance(data, list)
    assert len(data) == 1
    row = data[0]
    assert row["trace_id"] == trace_id, "JSON must include FULL trace_id, not truncated"
    assert row["name"] == "cli-test-agent"
    assert row["framework"] == "test"
    assert row["failed"] is True
    assert row["step_count"] == 3
    assert "annotation_count" in row


def test_stats_json_empty_home(runner: CliRunner, loupe_home: Path) -> None:
    import json as _json
    res = runner.invoke(app, ["stats", "--json"])
    assert res.exit_code == 0
    data = _json.loads(res.output.strip())
    assert data == {
        "trace_count": 0, "failed_count": 0, "step_count": 0,
        "annotation_count": 0, "median_duration_ms": None,
        "by_framework": {}, "by_failure_category": {},
    }


def test_stats_json_with_traces(runner: CliRunner, loupe_home: Path) -> None:
    import json as _json
    _seed_one_trace(loupe_home)
    _seed_one_trace(loupe_home)
    res = runner.invoke(app, ["stats", "--json"])
    assert res.exit_code == 0
    data = _json.loads(res.output.strip())
    assert data["trace_count"] == 2
    assert data["failed_count"] == 2
    assert data["step_count"] == 6
    assert data["by_framework"] == {"test": 2}


def test_show_json_emits_full_trace_payload(
    runner: CliRunner, loupe_home: Path
) -> None:
    """show --json returns header + steps + annotations as a single object."""
    import json as _json
    trace_id = _seed_one_trace(loupe_home)
    res = runner.invoke(app, ["show", trace_id[:12], "--json"])
    assert res.exit_code == 0
    data = _json.loads(res.output.strip())
    assert data["trace_id"] == trace_id
    assert data["name"] == "cli-test-agent"
    assert isinstance(data["steps"], list) and len(data["steps"]) == 3
    assert "annotations" in data


def test_show_json_unknown_trace_exits_nonzero(
    runner: CliRunner, loupe_home: Path
) -> None:
    res = runner.invoke(app, ["show", "deadbeef", "--json"])
    assert res.exit_code == 1


def test_parse_duration_rejects_garbage() -> None:
    from loupe.cli import _parse_duration

    for bad in ("", "abc", "7x", "-1d", "1.2.3h"):
        try:
            _parse_duration(bad)
        except ValueError:
            continue
        raise AssertionError(f"_parse_duration({bad!r}) should have raised")


# ---------------------------------------------------------------------------
# Consolidation regression tests — v0.0.57 polish
# ---------------------------------------------------------------------------


def test_setup_picker_supports_all_six_providers() -> None:
    """The setup-provider registry must list the six providers the wizard
    advertises. Adding/removing one here is the canonical way to extend
    coverage — every other CLI surface flows from this list."""
    from loupe._setup_providers import SETUP_PROVIDERS, labels

    expected = {"gemini", "anthropic", "openai", "mistral", "groq", "deepseek"}
    assert {p.label for p in SETUP_PROVIDERS} == expected
    # Picker order — frontier first, then inference. Gemini stays at #1
    # because it has the free tier and is the fastest path to a trace.
    assert labels()[0] == "gemini"


def test_setup_registry_has_required_fields_for_every_provider() -> None:
    """Each entry must carry the fields the wizard needs end-to-end."""
    from loupe._setup_providers import SETUP_PROVIDERS

    for p in SETUP_PROVIDERS:
        assert p.label
        assert p.display
        assert p.tagline
        assert p.env_keys
        assert p.key_url.startswith("https://")
        assert p.key_prefix
        assert p.default_model


def test_setup_scripted_supports_mistral_groq_deepseek(
    runner: CliRunner, loupe_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding 3 new providers must work end-to-end via the scripted path."""
    from loupe import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_ping_provider",
                        lambda *a, **kw: (False, "test mode"))

    for provider, fake_key in [
        ("mistral",  "sk-test-mistral"),
        ("groq",     "gsk_test-groq"),
        ("deepseek", "sk-test-deepseek"),
    ]:
        # Reset config between providers so each save is observed in isolation.
        cfg_path = loupe_home / "config.toml"
        if cfg_path.exists():
            cfg_path.unlink()
        res = runner.invoke(app, [
            "setup",
            "--provider", provider,
            "--api-key", fake_key,
            "--no-browser",
        ])
        assert res.exit_code == 0, f"{provider}: {res.output}"

        from loupe.config import Config
        cfg = Config.load()
        assert cfg.providers[provider].api_key == fake_key
        assert cfg.default_provider == provider


def test_export_default_format_is_loupebench(
    runner: CliRunner, loupe_home: Path, tmp_path: Path,
) -> None:
    """`loupe export` (no --format) must keep producing LoupeBench JSONL —
    we didn't change the historical default."""
    _seed_one_trace(loupe_home)
    res = runner.invoke(app, ["export", "--out", str(tmp_path / "x.jsonl")])
    # No tagged failures, but the command must succeed cleanly + hint at tagging.
    assert res.exit_code == 0
    assert "Nothing to export" in res.output


def test_export_otlp_writes_otel_document(
    runner: CliRunner, loupe_home: Path, tmp_path: Path,
) -> None:
    """`loupe export --format otlp` must write a valid OTLP document."""
    import json

    _seed_one_trace(loupe_home)
    out = tmp_path / "otel.json"
    res = runner.invoke(app, [
        "export", "--format", "otlp", "--out", str(out),
    ])
    assert res.exit_code == 0, res.output
    assert out.exists()

    doc = json.loads(out.read_text())
    assert "resourceSpans" in doc
    assert len(doc["resourceSpans"]) >= 1


def test_export_rejects_unknown_format(
    runner: CliRunner, loupe_home: Path,
) -> None:
    res = runner.invoke(app, ["export", "--format", "csv"])
    assert res.exit_code == 1
    assert "unknown --format" in res.output


def test_export_accepts_jsonl_alias(
    runner: CliRunner, loupe_home: Path, tmp_path: Path,
) -> None:
    """`--format jsonl` is an alias for `loupebench` — the help text
    calls it "JSONL (LoupeBench)", so a user typing jsonl must not hit
    the unknown-format error."""
    out = tmp_path / "bench.jsonl"
    res = runner.invoke(app, ["export", "--format", "jsonl", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert "unknown --format" not in res.output


def test_start_command_is_gone(runner: CliRunner, loupe_home: Path) -> None:
    """`loupe start` was merged into `loupe ui` — typing it must surface
    the did-you-mean shim instead of silently working."""
    from loupe.cli import _registered_command_names
    assert "start" not in _registered_command_names()
    assert "ui" in _registered_command_names()


def test_try_command_is_gone(runner: CliRunner, loupe_home: Path) -> None:
    """`loupe try` was cut — `loupe ask 'hi'` covers it. Confirm it no
    longer appears in the registered command list."""
    from loupe.cli import _registered_command_names
    assert "try" not in _registered_command_names()


def test_otlp_command_is_gone(runner: CliRunner, loupe_home: Path) -> None:
    """`loupe otlp` was merged into `loupe export --format otlp`."""
    from loupe.cli import _registered_command_names
    assert "otlp" not in _registered_command_names()
    assert "export" in _registered_command_names()


# ---------------------------------------------------------------------------
# v0.0.59 — universal `loupe run` + autopatch on-by-default
# ---------------------------------------------------------------------------


def test_run_python_script_still_works(
    runner: CliRunner, loupe_home: Path, tmp_path: Path,
) -> None:
    """`loupe run my_agent.py` continues to execute in-process for the
    deepest capture (parent @trace + nested steps)."""
    script = tmp_path / "ok.py"
    script.write_text("print('hello from run')\n", encoding="utf-8")
    res = runner.invoke(app, ["run", str(script)])
    assert res.exit_code == 0, res.output
    assert "trace captured" in res.output


def test_run_unknown_command_exits_127(
    runner: CliRunner, loupe_home: Path,
) -> None:
    """If the binary isn't on PATH, exit 127 (standard shell convention)
    with a friendly hint about `loupe proxy`."""
    res = runner.invoke(app, ["run", "nonexistent-command-xyz"])
    assert res.exit_code == 127
    assert "not found on PATH" in res.output
    assert "loupe proxy" in res.output


def test_run_subprocess_propagates_exit_code(
    runner: CliRunner, loupe_home: Path,
) -> None:
    """A subprocess that exits 42 must surface as exit 42 from `loupe run`."""
    res = runner.invoke(app, ["run", "sh", "-c", "exit 42"])
    assert res.exit_code == 42


def test_run_double_dash_separator_is_stripped(
    runner: CliRunner, loupe_home: Path, tmp_path: Path,
) -> None:
    """`loupe run -- python --version` should run `python --version`, not
    look for a file named `--`."""
    res = runner.invoke(app, ["run", "--", "sh", "-c", "echo ok"])
    assert res.exit_code == 0
    assert "ok" in res.output


def test_setup_reset_deletes_config_file(
    runner: CliRunner, loupe_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`loupe setup --reset --provider X --api-key K --no-browser` should:
       1. Delete the existing config.toml first
       2. Then run the wizard to write a new one
    """
    # Suppress the network ping so the test is deterministic.
    from loupe import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_ping_provider",
                        lambda *a, **kw: (False, "test mode"))

    # Seed an existing config with one provider.
    r1 = runner.invoke(app, [
        "setup", "--provider", "openai", "--api-key", "sk-old", "--no-browser",
    ])
    assert r1.exit_code == 0
    cfg_path = loupe_home / "config.toml"
    assert cfg_path.exists()
    assert "sk-old" in cfg_path.read_text()

    # Reset + reconfigure to a different provider in one command.
    r2 = runner.invoke(app, [
        "setup", "--reset",
        "--provider", "gemini", "--api-key", "AIza-new", "--no-browser",
    ])
    assert r2.exit_code == 0, r2.output
    assert "deleted" in r2.output
    text = cfg_path.read_text()
    assert "sk-old" not in text       # old provider gone
    assert "AIza-new" in text         # new provider saved


def test_setup_reset_with_no_existing_config_is_noop_then_runs_wizard(
    runner: CliRunner, loupe_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`loupe setup --reset` on a fresh install must not error — it just
    falls through to the normal wizard run."""
    from loupe import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_ping_provider",
                        lambda *a, **kw: (False, "test mode"))

    res = runner.invoke(app, [
        "setup", "--reset",
        "--provider", "gemini", "--api-key", "AIza-fresh", "--no-browser",
    ])
    assert res.exit_code == 0, res.output
    assert "no config to reset" in res.output
    assert (loupe_home / "config.toml").exists()


def test_setup_remove_drops_single_provider(
    runner: CliRunner, loupe_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`loupe setup --remove gemini` should keep openai + delete gemini."""
    from loupe import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_ping_provider",
                        lambda *a, **kw: (False, "test mode"))

    # Configure two providers.
    for prov, key in [("gemini", "AIza-key"), ("openai", "sk-key")]:
        res = runner.invoke(app, [
            "setup", "--provider", prov, "--api-key", key, "--no-browser",
        ])
        assert res.exit_code == 0

    # Remove just gemini.
    res = runner.invoke(app, ["setup", "--remove", "gemini"])
    assert res.exit_code == 0, res.output
    assert "removed gemini" in res.output

    from loupe.config import Config
    cfg = Config.load()
    assert "gemini" not in cfg.providers or cfg.providers["gemini"].api_key is None
    assert cfg.providers["openai"].api_key == "sk-key"


def test_setup_remove_default_provider_falls_back_to_remaining(
    runner: CliRunner, loupe_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing the *default* provider must promote one of the remaining
    providers — otherwise the user's next `loupe ask` would break."""
    from loupe import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_ping_provider",
                        lambda *a, **kw: (False, "test mode"))

    runner.invoke(app, ["setup", "--provider", "gemini",
                        "--api-key", "AIza", "--no-browser"])
    runner.invoke(app, ["setup", "--provider", "openai",
                        "--api-key", "sk", "--no-browser"])
    from loupe.config import Config
    assert Config.load().default_provider == "openai"  # set most recently

    res = runner.invoke(app, ["setup", "--remove", "openai"])
    assert res.exit_code == 0
    cfg = Config.load()
    assert "openai" not in cfg.providers or cfg.providers["openai"].api_key is None
    # default must have moved off the now-deleted provider
    assert cfg.default_provider != "openai"
    assert cfg.api_key_for(cfg.default_provider) is not None


def test_setup_remove_unknown_provider_exits_1(
    runner: CliRunner, loupe_home: Path,
) -> None:
    res = runner.invoke(app, ["setup", "--remove", "made-up-llm"])
    assert res.exit_code == 1
    assert "unknown provider" in res.output


def test_setup_remove_noop_when_provider_isnt_configured(
    runner: CliRunner, loupe_home: Path,
) -> None:
    res = runner.invoke(app, ["setup", "--remove", "gemini"])
    assert res.exit_code == 0
    assert "isn't configured" in res.output


def test_autopatch_resolver_documents_three_modes() -> None:
    """The _autopatch_enabled() function must enforce the documented
    resolution: env-var > config-toml existence > off."""
    import os as _os

    from loupe.integrations.httpx import _autopatch_enabled

    saved = _os.environ.pop("LOUPE_AUTOPATCH", None)
    try:
        # Explicit on
        _os.environ["LOUPE_AUTOPATCH"] = "1"
        assert _autopatch_enabled() is True
        # Explicit off
        _os.environ["LOUPE_AUTOPATCH"] = "0"
        assert _autopatch_enabled() is False
        # Unknown value → off (safer default)
        _os.environ["LOUPE_AUTOPATCH"] = "maybe"
        assert _autopatch_enabled() is False
    finally:
        if saved is None:
            _os.environ.pop("LOUPE_AUTOPATCH", None)
        else:
            _os.environ["LOUPE_AUTOPATCH"] = saved
