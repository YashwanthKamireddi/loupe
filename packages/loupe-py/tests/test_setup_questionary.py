"""Tests for the questionary-powered provider picker in ``loupe setup``.

The interactive path uses ``questionary.select(...).unsafe_ask()``;
testing it for real would require a pty harness. Instead we:

* assert the TTY branch routes to the interactive helper,
* assert the non-TTY branch never imports questionary,
* assert a missing-questionary install degrades gracefully to the
  numbered-input fallback (load-bearing on user systems that ran
  ``pip uninstall questionary`` without reading the warning),
* assert ``Esc`` (None return) and ``Ctrl-C`` (KeyboardInterrupt)
  both raise typer.Exit cleanly.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
import typer

from loupe import cli


def _force_tty(mp: pytest.MonkeyPatch, *, on: bool) -> None:
    mp.setattr(sys.stdin, "isatty", lambda: on)
    mp.setattr(sys.stdout, "isatty", lambda: on)


# --- Routing: TTY vs non-TTY -----------------------------------------------


def test_non_tty_skips_questionary(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_tty(monkeypatch, on=False)
    # If non-TTY mistakenly routed to questionary, the fallback patch
    # below would never get called and the test would block on stdin.
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")
    picked = cli._prompt_provider()
    # The 2nd entry in SETUP_PROVIDERS is Anthropic per the registry
    # order (gemini, anthropic, openai, mistral, groq, deepseek).
    assert isinstance(picked, str) and picked


def test_tty_routes_to_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_tty(monkeypatch, on=True)
    monkeypatch.setattr(cli, "_prompt_provider_interactive", lambda: "gemini")
    # If routing was wrong, the fallback's input() would block.
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail(
        "non-TTY fallback was reached on a TTY"
    ))
    assert cli._prompt_provider() == "gemini"


def test_missing_questionary_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user who pip-uninstalls questionary still gets a working setup."""
    _force_tty(monkeypatch, on=True)

    def _raise_import(*_a: Any, **_k: Any) -> None:
        raise ImportError("questionary not installed")

    monkeypatch.setattr(cli, "_prompt_provider_interactive", _raise_import)
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")
    picked = cli._prompt_provider()
    assert isinstance(picked, str) and picked


# --- Interactive helper behavior -------------------------------------------


def test_interactive_returns_picked_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """When questionary returns a value, the helper passes it through."""
    import questionary

    class _StubQuestion:
        def __init__(self, value: str) -> None:
            self._value = value

        def unsafe_ask(self) -> str:
            return self._value

    monkeypatch.setattr(questionary, "select", lambda *a, **kw: _StubQuestion("anthropic"))
    assert cli._prompt_provider_interactive() == "anthropic"


def test_interactive_esc_exits_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """``unsafe_ask`` returns None on Esc; we must raise typer.Exit, not return None."""
    import questionary

    class _StubEsc:
        def unsafe_ask(self) -> None:
            return None

    monkeypatch.setattr(questionary, "select", lambda *a, **kw: _StubEsc())
    with pytest.raises(typer.Exit):
        cli._prompt_provider_interactive()


def test_interactive_ctrl_c_exits_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-C raises KeyboardInterrupt inside unsafe_ask — we re-raise as Exit."""
    import questionary

    class _StubInterrupt:
        def unsafe_ask(self) -> str:
            raise KeyboardInterrupt

    monkeypatch.setattr(questionary, "select", lambda *a, **kw: _StubInterrupt())
    with pytest.raises(typer.Exit):
        cli._prompt_provider_interactive()


# --- Fallback exhausts the existing contract --------------------------------


def test_fallback_default_is_first_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from loupe._setup_providers import SETUP_PROVIDERS

    _force_tty(monkeypatch, on=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")  # empty → default
    assert cli._prompt_provider_fallback() == SETUP_PROVIDERS[0].label


def test_fallback_accepts_bare_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "OPENAI  ")
    assert cli._prompt_provider_fallback() == "openai"
