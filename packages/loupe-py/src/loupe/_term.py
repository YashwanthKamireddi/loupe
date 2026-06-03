"""Centralized terminal capability detection.

Every TTY / color / animation gate in Loupe routes through these
helpers so that a single env-var (NO_COLOR, FORCE_COLOR, CI) is
honored everywhere — call sites stay terse and consistent.

The semantics:

* :func:`is_tty` — both stdin AND stdout connected to a real terminal.
  Interactive flows (``loupe setup``, ``loupe onboard``, ``loupe
  watch``, the animated first-run banner) all require keyboard input
  *and* visible output, so the AND is the right join.
* :func:`use_color` — whether to emit ANSI color sequences. Respects
  the ``NO_COLOR`` / ``FORCE_COLOR`` conventions (see no-color.org).
* :func:`use_animation` — whether to play motion transitions (banner
  fade, fancy spinners). Stricter than ``use_color``: also off in CI
  and when stdin is not a TTY.
"""

from __future__ import annotations

import os
import sys

_TRUTHY = {"1", "true", "yes", "on"}


def is_tty() -> bool:
    """Return True when both stdin and stdout are real terminals."""
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def use_color() -> bool:
    """Return True when ANSI color should be emitted to stdout.

    Honors the de-facto standard:

    * ``NO_COLOR`` set to any value → never color
      (https://no-color.org).
    * ``FORCE_COLOR`` set to a truthy value → always color, even
      when piped.
    * Otherwise → color only when stdout is a TTY.
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    if (os.environ.get("FORCE_COLOR") or "").lower() in _TRUTHY:
        return True
    return sys.stdout.isatty()


def use_animation() -> bool:
    """Return True when motion (banner fade, fancy spinners) should play.

    Stricter than :func:`use_color`. Animation is suppressed when:

    * color is off (``NO_COLOR`` set, or no TTY without ``FORCE_COLOR``)
    * stdin is not a TTY (interactive context disappeared)
    * ``CI`` is truthy — CI logs are static and motion just adds noise.
    """
    if not use_color():
        return False
    if not sys.stdin.isatty():
        return False
    return (os.environ.get("CI") or "").lower() not in _TRUTHY
