"""Tests for the centralized terminal capability detector.

Uses pytest's ``monkeypatch`` so env mutations and isatty stubs are
auto-restored between tests — no leakage into the rest of the suite.
"""

from __future__ import annotations

import sys

import pytest

from loupe._term import is_tty, use_animation, use_color


def _wipe_env(mp: pytest.MonkeyPatch) -> None:
    """Drop the env vars this module cares about so tests start clean."""
    for k in ("NO_COLOR", "FORCE_COLOR", "CI"):
        mp.delenv(k, raising=False)


def _set_tty(mp: pytest.MonkeyPatch, *, stdin: bool, stdout: bool) -> None:
    mp.setattr(sys.stdin, "isatty", lambda: stdin)
    mp.setattr(sys.stdout, "isatty", lambda: stdout)


# ---- is_tty ----------------------------------------------------------------


def test_is_tty_true_when_both_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tty(monkeypatch, stdin=True, stdout=True)
    assert is_tty() is True


def test_is_tty_false_when_stdin_piped(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tty(monkeypatch, stdin=False, stdout=True)
    assert is_tty() is False


def test_is_tty_false_when_stdout_piped(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tty(monkeypatch, stdin=True, stdout=False)
    assert is_tty() is False


# ---- use_color -------------------------------------------------------------


def test_no_color_always_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    _wipe_env(monkeypatch)
    monkeypatch.setenv("NO_COLOR", "1")
    _set_tty(monkeypatch, stdin=True, stdout=True)
    assert use_color() is False


def test_no_color_wins_even_with_force_color(monkeypatch: pytest.MonkeyPatch) -> None:
    # Per no-color.org, NO_COLOR takes precedence over FORCE_COLOR.
    _wipe_env(monkeypatch)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    _set_tty(monkeypatch, stdin=True, stdout=True)
    assert use_color() is False


def test_force_color_enables_even_when_piped(monkeypatch: pytest.MonkeyPatch) -> None:
    _wipe_env(monkeypatch)
    monkeypatch.setenv("FORCE_COLOR", "1")
    _set_tty(monkeypatch, stdin=False, stdout=False)
    assert use_color() is True


def test_default_color_follows_stdout_isatty(monkeypatch: pytest.MonkeyPatch) -> None:
    _wipe_env(monkeypatch)
    _set_tty(monkeypatch, stdin=True, stdout=True)
    assert use_color() is True
    _set_tty(monkeypatch, stdin=True, stdout=False)
    assert use_color() is False


# ---- use_animation ---------------------------------------------------------


def test_animation_off_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    _wipe_env(monkeypatch)
    monkeypatch.setenv("CI", "true")
    _set_tty(monkeypatch, stdin=True, stdout=True)
    assert use_animation() is False


def test_animation_off_when_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    _wipe_env(monkeypatch)
    monkeypatch.setenv("NO_COLOR", "1")
    _set_tty(monkeypatch, stdin=True, stdout=True)
    assert use_animation() is False


def test_animation_off_when_stdin_piped(monkeypatch: pytest.MonkeyPatch) -> None:
    _wipe_env(monkeypatch)
    _set_tty(monkeypatch, stdin=False, stdout=True)
    assert use_animation() is False


def test_animation_off_when_stdout_piped(monkeypatch: pytest.MonkeyPatch) -> None:
    _wipe_env(monkeypatch)
    _set_tty(monkeypatch, stdin=True, stdout=False)
    assert use_animation() is False


def test_animation_on_when_all_conditions_met(monkeypatch: pytest.MonkeyPatch) -> None:
    _wipe_env(monkeypatch)
    _set_tty(monkeypatch, stdin=True, stdout=True)
    assert use_animation() is True
