"""The first thing a naive vibe coder sees must teach what Loupe is.

v0.0.67 rewrote `_show_welcome()` to be a one-screen pitch that
defines what Loupe captures and gives one concrete next action.
This test pins the teaching contract: every line the rewrite
promised must actually render.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from loupe.cli import app


@pytest.fixture()
def fresh_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    h = tmp_path / "loupe-home"
    monkeypatch.setenv("LOUPE_HOME", str(h))
    # Make sure no provider env vars leak from the dev's real environment.
    for var in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LOUPE_DISABLE_AUTOSETUP", "1")  # don't try interactive wizard
    return h


def test_welcome_pitches_the_product(fresh_home: Path) -> None:
    """No provider, no traces → welcome explains what Loupe DOES."""
    res = CliRunner().invoke(app, [])
    assert res.exit_code == 0, res.output
    # The one-line pitch.
    assert "magnifying glass" in res.output
    # The capture promise — defines vocabulary inline.
    assert "captures every LLM call" in res.output
    # The one CTA the user is supposed to type next.
    assert "loupe init my-agent" in res.output
    # The explain-loupe pointer (vocabulary ladder).
    assert "loupe explain loupe" in res.output


def test_welcome_detects_provider_env_var(
    fresh_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With OPENAI_API_KEY set, welcome flags that Loupe will capture it."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    res = CliRunner().invoke(app, [])
    assert res.exit_code == 0, res.output
    # Green detection line names the env var the user already set.
    assert "Detected your OPENAI_API_KEY" in res.output
    assert "capture every OpenAI call" in res.output


def test_explain_loupe_topic_exists(fresh_home: Path) -> None:
    """`loupe explain loupe` is the answer to 'what IS this thing?'."""
    res = CliRunner().invoke(app, ["explain", "loupe"])
    assert res.exit_code == 0, res.output
    # Concepts every newcomer needs.
    assert "trace" in res.output
    assert "step" in res.output
    assert "annotation" in res.output
    assert "autopatch" in res.output
    # Doesn't fall through to a typo-suggestion path.
    assert "did you mean" not in res.output.lower()
