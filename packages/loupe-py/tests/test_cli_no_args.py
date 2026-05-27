"""Every command that takes a trace_id MUST be friendly when invoked
with no arguments.

Before v0.0.66 these dropped to Typer's generic "Missing argument 'TRACE_ID'"
output. v0.0.66 makes each one either:

  - run a sensible default (annotations → list all), or
  - print a friendly error pointing at `loupe list` + a usage example.

This test pins the contract: no command may emit Typer's default
"Missing argument" string, and each one must mention `loupe list` so a
new user always has a recoverable next step.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from loupe.cli import app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


COMMANDS_THAT_NEED_TARGETS = [
    # (command, [args], expected_exit_code, must_mention)
    ("show",        [],           1, "loupe list"),
    ("report",      [],           1, "loupe list"),
    ("tag",         [],           1, "loupe list"),
    ("untag",       [],           1, "loupe list"),
    ("diff",        [],           1, "loupe list"),
    ("steer",       [],           1, "loupe list"),
    ("causal",      [],           1, "loupe list"),
]


@pytest.mark.parametrize(
    "command,args,exit_code,must_mention", COMMANDS_THAT_NEED_TARGETS,
)
def test_command_no_args_is_friendly(
    runner: CliRunner, command: str, args: list[str],
    exit_code: int, must_mention: str,
) -> None:
    """Each command without its required args prints a friendly hint, not
    Typer's "Missing argument" stub."""
    res = runner.invoke(app, [command, *args])
    assert res.exit_code == exit_code, res.output
    combined = res.output or ""
    assert "Missing argument" not in combined, (
        f"loupe {command} fell through to Typer's default error:\n{combined}"
    )
    assert must_mention in combined, (
        f"loupe {command} did not suggest `{must_mention}`:\n{combined}"
    )


def test_annotations_no_args_lists_all_not_errors(runner: CliRunner) -> None:
    """`loupe annotations` (no arg) should not error — it lists across
    every trace. With no annotations on disk it prints a friendly empty
    state, NOT Typer's missing-argument stub."""
    res = runner.invoke(app, ["annotations"])
    combined = (res.output or "") + (res.stdout or "")
    assert "Missing argument" not in combined, combined
    # Exit code 0 in either case (annotations exist OR friendly empty state).
    assert res.exit_code == 0, combined
