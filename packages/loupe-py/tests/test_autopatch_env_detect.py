"""v0.0.66 autopatch trigger: a recognized provider env var alone is
enough to activate capture, even without a config file. Explicit
``LOUPE_AUTOPATCH=0`` always wins (opt-out is final).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loupe._autopatch_hook import _PROVIDER_ENV_VARS, _should_activate


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate every test from the developer's real environment + ~/.loupe."""
    # Wipe every known autopatch lever.
    monkeypatch.delenv("LOUPE_AUTOPATCH", raising=False)
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Point LOUPE_HOME at an empty tmpdir so the config-file branch
    # cannot accidentally pass.
    monkeypatch.setenv("LOUPE_HOME", str(tmp_path))


def test_no_signals_off() -> None:
    """Pristine env, no config, no provider key → autopatch stays off."""
    assert _should_activate() is False


@pytest.mark.parametrize("env_var", _PROVIDER_ENV_VARS)
def test_any_provider_key_turns_autopatch_on(
    monkeypatch: pytest.MonkeyPatch, env_var: str,
) -> None:
    """Setting any one of the known provider env vars activates capture."""
    monkeypatch.setenv(env_var, "fake-key")
    assert _should_activate() is True, f"{env_var} should have activated autopatch"


def test_config_file_alone_turns_autopatch_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A real config.toml under LOUPE_HOME also activates capture."""
    (tmp_path / "config.toml").write_text(
        '[default]\nprovider = "gemini"\nmodel = "gemini-2.5-flash"\n',
        encoding="utf-8",
    )
    assert _should_activate() is True


def test_explicit_off_beats_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """LOUPE_AUTOPATCH=0 must win over a provider env var (opt-out is final)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("LOUPE_AUTOPATCH", "0")
    assert _should_activate() is False


def test_explicit_on_works_with_no_other_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LOUPE_AUTOPATCH=1 alone is still a valid opt-in trigger."""
    monkeypatch.setenv("LOUPE_AUTOPATCH", "1")
    assert _should_activate() is True
