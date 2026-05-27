"""Tests for `loupe onboard` agent detection.

The interactive flow runs real user code (gated behind a TTY +
confirmation), so it isn't unit-tested end-to-end. What IS tested is
the pure detection logic + the safety contract that a non-interactive
`loupe onboard` never executes anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from loupe._onboard import detect_agent_scripts, looks_like_project
from loupe.cli import app


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_sdk_import_outranks_plain_file(tmp_path: Path) -> None:
    _write(tmp_path / "utils.py", "def add(a, b):\n    return a + b\n")
    _write(
        tmp_path / "bot.py",
        "import openai\nclient = openai.OpenAI()\n",
    )
    ranked = detect_agent_scripts(tmp_path)
    assert ranked, "should find at least the openai file"
    assert ranked[0].path.name == "bot.py"
    assert "openai" in ranked[0].why
    # The plain utils.py scores nothing and is excluded entirely.
    assert all(c.path.name != "utils.py" for c in ranked)


def test_agent_name_bonus_without_imports(tmp_path: Path) -> None:
    _write(tmp_path / "agent.py", "print('hi')\n")
    ranked = detect_agent_scripts(tmp_path)
    assert len(ranked) == 1
    assert ranked[0].path.name == "agent.py"
    assert "named agent.py" in ranked[0].why


def test_main_block_adds_score(tmp_path: Path) -> None:
    _write(
        tmp_path / "thing.py",
        "import anthropic\nif __name__ == '__main__':\n    pass\n",
    )
    ranked = detect_agent_scripts(tmp_path)
    assert ranked[0].score >= 12  # 10 (sdk) + 2 (__main__)
    assert "runnable" in ranked[0].why


def test_skips_noise_dirs(tmp_path: Path) -> None:
    _write(tmp_path / ".venv" / "lib" / "openai_client.py", "import openai\n")
    _write(tmp_path / "tests" / "test_agent.py", "import openai\n")
    _write(tmp_path / "__pycache__" / "cached.py", "import openai\n")
    _write(tmp_path / "real.py", "import openai\n")
    ranked = detect_agent_scripts(tmp_path)
    names = {c.path.name for c in ranked}
    assert "real.py" in names
    assert "openai_client.py" not in names
    assert "test_agent.py" not in names
    assert "cached.py" not in names


def test_depth_one_found_depth_two_ignored(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "agent.py", "import openai\n")          # depth 1 dir
    _write(tmp_path / "src" / "deep" / "buried.py", "import openai\n")  # depth 2
    ranked = detect_agent_scripts(tmp_path)
    names = {c.path.name for c in ranked}
    assert "agent.py" in names
    assert "buried.py" not in names


def test_empty_folder_returns_nothing(tmp_path: Path) -> None:
    assert detect_agent_scripts(tmp_path) == []


def test_top_level_beats_nested_on_tie(tmp_path: Path) -> None:
    _write(tmp_path / "agent.py", "import openai\n")
    _write(tmp_path / "pkg" / "agent.py", "import openai\n")
    ranked = detect_agent_scripts(tmp_path)
    # Same score; the shallower path wins.
    assert ranked[0].path == tmp_path / "agent.py"


def test_looks_like_project(tmp_path: Path) -> None:
    assert looks_like_project(tmp_path) is False
    _write(tmp_path / "agent.py", "x = 1\n")
    assert looks_like_project(tmp_path) is True


def test_looks_like_project_detects_node(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert looks_like_project(tmp_path) is True


def test_onboard_non_tty_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """In a non-interactive context, `loupe onboard` must NOT execute
    any user code — it prints the outline and exits cleanly."""
    home = tmp_path / "loupe-home"
    monkeypatch.setenv("LOUPE_HOME", str(home))
    # A booby-trapped "agent" that would corrupt the test run if executed.
    (tmp_path / "agent.py").write_text(
        "import sys; sys.exit('onboard must not run me')\n", encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    res = CliRunner().invoke(app, ["onboard"])
    # Clean exit, and our trap never fired.
    assert res.exit_code == 0, res.output
    assert "onboard must not run me" not in res.output
