"""Tests for the ~/.loupe/config.toml layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from loupe.config import Config, ProviderConfig, config_path


@pytest.fixture()
def loupe_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "loupe-home"
    home.mkdir()
    monkeypatch.setenv("LOUPE_HOME", str(home))
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
    # Clear any provider env vars that might leak from the parent process.
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY",
              "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    from loupe import store as store_mod
    store_mod._default = None
    return home


def test_load_returns_defaults_when_no_file(loupe_home: Path) -> None:
    cfg = Config.load()
    assert cfg.default_provider == "gemini"
    assert cfg.default_model == "gemini-2.5-flash"
    assert cfg.providers == {}
    assert cfg.attribution_backend == "mock"
    assert cfg.index_disabled is False


def test_save_and_reload_roundtrip(loupe_home: Path) -> None:
    cfg = Config.load().set_provider_key("gemini", "AIza-test-key")
    cfg.save()

    reloaded = Config.load()
    assert reloaded.providers["gemini"].api_key == "AIza-test-key"
    assert reloaded.api_key_for("gemini") == "AIza-test-key"


def test_env_var_overrides_config_file(
    loupe_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env vars MUST win — ephemeral overrides like CI dependence on this."""
    Config.load().set_provider_key("gemini", "from-config").save()

    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    cfg = Config.load()
    assert cfg.api_key_for("gemini") == "from-env"


def test_configured_providers_is_alphabetical(
    loupe_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-y")
    cfg = Config.load()
    assert cfg.configured_providers() == ["gemini", "openai"]


def test_set_provider_key_returns_new_instance(loupe_home: Path) -> None:
    """Config is immutable from the caller's perspective."""
    a = Config.load()
    b = a.set_provider_key("anthropic", "sk-ant-...")
    assert a.providers == {}            # original untouched
    assert b.providers["anthropic"].api_key == "sk-ant-..."


def test_with_default_changes_only_what_you_pass(loupe_home: Path) -> None:
    a = Config.load()
    b = a.with_default(provider="openai")
    assert a.default_provider == "gemini"   # original untouched
    assert b.default_provider == "openai"
    assert b.default_model == a.default_model   # model preserved


def test_saved_file_is_human_readable_toml(loupe_home: Path) -> None:
    """The TOML we write must be loadable by stdlib tomllib AND
    contain helpful comments so a curious user can read it."""
    import tomllib
    Config.load().set_provider_key("gemini", "AIza-test").save()
    text = config_path().read_text(encoding="utf-8")
    assert "[default]" in text
    assert "[providers.gemini]" in text
    assert "# Loupe config" in text            # helpful comment present
    parsed = tomllib.loads(text)
    assert parsed["default"]["provider"] == "gemini"
    assert parsed["providers"]["gemini"]["api_key"] == "AIza-test"


def test_load_tolerates_corrupt_file(loupe_home: Path) -> None:
    """A malformed config must NEVER crash `loupe` startup."""
    config_path().write_text("this is not valid toml [[[", encoding="utf-8")
    cfg = Config.load()                       # must not raise
    # Falls back to defaults
    assert cfg.default_provider == "gemini"
    assert cfg.providers == {}


def test_api_key_for_unknown_provider_returns_none(loupe_home: Path) -> None:
    cfg = Config.load()
    assert cfg.api_key_for("nonexistent-provider") is None


def test_provider_config_is_configured_helper() -> None:
    assert ProviderConfig(name="x", api_key="key").is_configured()
    assert not ProviderConfig(name="x").is_configured()
    assert not ProviderConfig(name="x", api_key="").is_configured()
