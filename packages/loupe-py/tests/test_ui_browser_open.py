"""`loupe ui` should auto-open the browser when the user is sitting
at a real terminal with a display, AND stay quiet when they're not."""

from __future__ import annotations

import pytest

from loupe.cli import _should_open_browser


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every env signal so each test sets exactly what it needs."""
    for var in ("DISPLAY", "WAYLAND_DISPLAY", "LOUPE_DISABLE_BROWSER"):
        monkeypatch.delenv(var, raising=False)


def test_tty_plus_display_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real desktop: TTY + DISPLAY → open the browser."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    assert _should_open_browser() is True


def test_wayland_also_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wayland session: WAYLAND_DISPLAY set → open the browser."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert _should_open_browser() is True


def test_non_tty_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI / piped stdout → don't try to open."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    assert _should_open_browser() is False


def test_ssh_no_display_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux SSH session with no DISPLAY → don't try to open."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.platform", "linux")
    # No DISPLAY, no WAYLAND_DISPLAY (autouse fixture cleared both).
    assert _should_open_browser() is False


def test_macos_always_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS: DISPLAY is irrelevant — `open` handles it."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.platform", "darwin")
    assert _should_open_browser() is True


def test_windows_always_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.platform", "win32")
    assert _should_open_browser() is True


def test_loupe_disable_browser_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env-var escape hatch overrides every other signal."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setenv("LOUPE_DISABLE_BROWSER", "1")
    assert _should_open_browser() is False
