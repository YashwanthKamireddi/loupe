"""Tests for the first-run animated banner gating + marker-file mechanics.

The actual ~240ms Rich Live animation is not asserted frame-by-frame
(that's a visual contract, not a logical one). What we DO assert:

* The boolean gate flips off after the marker file is written, so the
  animation never plays twice on the same machine.
* All the soft-failure paths (NO_COLOR, CI, no TTY, read-only home)
  short-circuit to False so scripting + CI never see motion.
* :func:`mark_first_run_seen` is idempotent and forgiving of write
  failures — failing here must never crash a successful first run.
* :func:`play_first_run_intro` actually runs to completion when
  invoked with ``frame_seconds=0`` (smoke test only).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from loupe._tui import (
    _banner_seen_path,
    mark_first_run_seen,
    play_first_run_intro,
    should_play_first_run_intro,
)


@pytest.fixture
def fake_loupe_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect ~/.loupe to a tmp dir so the marker can be probed safely."""
    monkeypatch.setenv("LOUPE_HOME", str(tmp_path))
    return tmp_path


def _set_animation_friendly(mp: pytest.MonkeyPatch) -> None:
    """All env knobs that would suppress animation, cleared."""
    for k in ("NO_COLOR", "FORCE_COLOR", "CI"):
        mp.delenv(k, raising=False)
    mp.setattr(sys.stdin, "isatty", lambda: True)
    mp.setattr(sys.stdout, "isatty", lambda: True)


def test_first_call_returns_true(
    monkeypatch: pytest.MonkeyPatch, fake_loupe_home: Path
) -> None:
    _set_animation_friendly(monkeypatch)
    assert should_play_first_run_intro() is True


def test_marker_makes_subsequent_calls_false(
    monkeypatch: pytest.MonkeyPatch, fake_loupe_home: Path
) -> None:
    _set_animation_friendly(monkeypatch)
    assert should_play_first_run_intro() is True
    mark_first_run_seen()
    assert should_play_first_run_intro() is False


def test_marker_file_lives_under_loupe_home(
    monkeypatch: pytest.MonkeyPatch, fake_loupe_home: Path
) -> None:
    _set_animation_friendly(monkeypatch)
    mark_first_run_seen()
    assert _banner_seen_path() == fake_loupe_home / ".banner-seen"
    assert _banner_seen_path().exists()


def test_no_color_blocks_intro(
    monkeypatch: pytest.MonkeyPatch, fake_loupe_home: Path
) -> None:
    _set_animation_friendly(monkeypatch)
    monkeypatch.setenv("NO_COLOR", "1")
    assert should_play_first_run_intro() is False


def test_ci_blocks_intro(
    monkeypatch: pytest.MonkeyPatch, fake_loupe_home: Path
) -> None:
    _set_animation_friendly(monkeypatch)
    monkeypatch.setenv("CI", "true")
    assert should_play_first_run_intro() is False


def test_piped_stdout_blocks_intro(
    monkeypatch: pytest.MonkeyPatch, fake_loupe_home: Path
) -> None:
    _set_animation_friendly(monkeypatch)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert should_play_first_run_intro() is False


def test_piped_stdin_blocks_intro(
    monkeypatch: pytest.MonkeyPatch, fake_loupe_home: Path
) -> None:
    _set_animation_friendly(monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert should_play_first_run_intro() is False


def test_mark_seen_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, fake_loupe_home: Path
) -> None:
    _set_animation_friendly(monkeypatch)
    mark_first_run_seen()
    # Calling again must not raise even though the marker already exists.
    mark_first_run_seen()
    assert _banner_seen_path().exists()


def test_mark_seen_swallows_oserror(
    monkeypatch: pytest.MonkeyPatch, fake_loupe_home: Path
) -> None:
    # Simulate a write failure (read-only home, full disk). The user's
    # first run must complete successfully even if we can't persist
    # the marker.
    def _explode(*_args: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "touch", _explode)
    mark_first_run_seen()  # must not raise


def test_intro_smoke_runs_to_completion(
    monkeypatch: pytest.MonkeyPatch, fake_loupe_home: Path
) -> None:
    # Zero-sleep run — verify the animation function returns cleanly and
    # leaves the screen in a usable state. We can't easily verify the
    # frames themselves without a snapshot harness; the value here is
    # catching a regression that would raise / hang.
    _set_animation_friendly(monkeypatch)
    play_first_run_intro(
        subtitle="test",
        version="0.0.0",
        frames=2,
        frame_seconds=0.0,
    )
