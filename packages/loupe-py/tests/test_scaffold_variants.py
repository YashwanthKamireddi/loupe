"""v0.0.66 scaffold takes `--file FILENAME` and `--provider PROVIDER`,
so users aren't locked into `agent.py` + Gemini.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loupe.scaffold import PROVIDERS, scaffold, validate_filename


def _files_in(target: Path) -> set[str]:
    return {p.name for p in target.iterdir()}


def test_default_scaffold_unchanged(tmp_path: Path) -> None:
    """Bare call still emits agent.py + Gemini template (default contract)."""
    out = tmp_path / "demo"
    scaffold(out, "demo")
    assert _files_in(out) == {"agent.py", "README.md", ".gitignore"}
    agent_src = (out / "agent.py").read_text()
    assert "google import genai" in agent_src
    assert 'framework="gemini"' in agent_src
    assert 'MODEL = "gemini-2.5-flash"' in agent_src


def test_custom_filename(tmp_path: Path) -> None:
    out = tmp_path / "demo"
    scaffold(out, "demo", filename="main.py")
    assert "main.py" in _files_in(out)
    assert "agent.py" not in _files_in(out)
    # README points at the chosen filename.
    assert "python main.py" in (out / "README.md").read_text()


def test_anthropic_provider(tmp_path: Path) -> None:
    out = tmp_path / "demo"
    scaffold(out, "demo", provider="anthropic")
    agent_src = (out / "agent.py").read_text()
    assert "import anthropic" in agent_src
    assert "ANTHROPIC_API_KEY" in agent_src
    assert 'framework="anthropic"' in agent_src
    # No Gemini leftovers.
    assert "google import genai" not in agent_src


def test_openai_provider(tmp_path: Path) -> None:
    out = tmp_path / "demo"
    scaffold(out, "demo", provider="openai")
    agent_src = (out / "agent.py").read_text()
    assert "import openai" in agent_src
    assert "OPENAI_API_KEY" in agent_src
    assert 'framework="openai"' in agent_src
    assert "chat.completions.create" in agent_src


def test_unknown_provider_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        scaffold(tmp_path / "demo", "demo", provider="bogus-provider")


@pytest.mark.parametrize(
    "bad_name",
    ["foo", "agent.txt", "sub/agent.py", "../escape.py", ".hidden.py"],
)
def test_invalid_filename_is_rejected(bad_name: str) -> None:
    with pytest.raises(ValueError):
        validate_filename(bad_name)


def test_provider_registry_covers_three_first_class_providers() -> None:
    """Anyone bumping the registry has to remember to keep all three."""
    assert {"gemini", "anthropic", "openai"} <= set(PROVIDERS.keys())
