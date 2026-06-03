"""Shared TUI primitives — the Loupe CLI's look-and-feel lives here.

Design language:
- Minimal. One accent color (amber). No heavy borders, no double-rules,
  no ASCII art beyond a single ◉ mark.
- Adaptive. Every renderable respects the terminal width without overflow
  or excess whitespace.
- Honest. Spinners run only when there's real work in flight.
- One palette, one logo, one type rhythm across every command.

The aesthetic target is the gh / vercel / stripe CLIs: dense at the top,
quiet in the middle, action-oriented at the bottom.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from rich.box import SIMPLE
from rich.console import Console, Group
from rich.padding import Padding
from rich.rule import Rule
from rich.spinner import Spinner
from rich.status import Status
from rich.table import Table
from rich.text import Text

# Shared single console — so we don't make a new one in every command.
console = Console(highlight=False, soft_wrap=False)

# --- Palette --------------------------------------------------------------
# Forensic-dossier charcoal + amber, with a couple of state colors.
# Same hex values as the dashboard for cross-surface consistency.

AMBER = "#e0a458"
AMBER_SOFT = "#f0c080"
DIM = "grey50"
DIM_2 = "grey39"
INK = "#f5f0e3"
INK_2 = "#cabfa9"
RED = "#e25a47"
GREEN = "#7ea96b"

LOGO_MARK = "◉"


# --- Terminal width helpers -----------------------------------------------


def term_width() -> int:
    """The current terminal width — adapts when the user resizes."""
    return console.size.width


def is_narrow() -> bool:
    """True if we're under 88 cols — collapse multi-column layouts."""
    return term_width() < 88


# --- Banner ---------------------------------------------------------------


def banner(subtitle: str | None = None, version: str | None = None) -> Group:
    """A two-line header: brand line + optional subtitle, then a thin rule.

    Canonical screen header for every long command output. Stays
    single-line in the brand row so the version sits flush right of the
    name, and any subtitle gets its own line at slightly smaller
    visual weight.
    """
    brand = Text()
    brand.append(f"{LOGO_MARK}  ", style=f"bold {AMBER}")
    brand.append("loupe", style=f"bold {INK}")
    if version:
        brand.append(f"  v{version}", style=DIM_2)

    pieces: list[object] = [brand]
    if subtitle:
        pieces.append(Text(subtitle, style=f"italic {INK_2}"))
    pieces.append(Rule(style=DIM_2, characters="·"))
    return Group(*pieces)  # type: ignore[arg-type]


# --- First-run animated banner -------------------------------------------
#
# Plays a tiny ~240ms gradient sweep across the wordmark the FIRST time a
# user invokes ``loupe`` interactively. Every subsequent invocation falls
# back to the static :func:`banner` above. Gated by:
#   * a marker file (~/.loupe/.banner-seen)
#   * :func:`loupe._term.use_animation` (off in CI, NO_COLOR, or when
#     stdout/stdin aren't TTYs)
# so scripting and piping are never slowed down.


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp_color(a: str, b: str, t: float) -> str:
    """Linearly interpolate between two ``#rrggbb`` hex colors at ``t∈[0,1]``."""
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    return (
        f"#{round(ar + (br - ar) * t):02x}"
        f"{round(ag + (bg - ag) * t):02x}"
        f"{round(ab + (bb - ab) * t):02x}"
    )


def _ease(t: float) -> float:
    """Symmetric ease — 0 at the edges, 1 in the middle of the word."""
    return 4 * t * (1 - t)


def _gradient_brand(version: str | None, phase: float) -> Text:
    """Render the ◉ loupe wordmark with a phase-shifted gradient sweep.

    ``phase`` in [0, 1] shifts the gradient highlight horizontally —
    chaining several phases produces the one-shot sweep animation.
    """
    word = "loupe"
    n = len(word)
    brand = Text()
    brand.append(f"{LOGO_MARK}  ", style=f"bold {AMBER}")
    for i, ch in enumerate(word):
        t = ((i / max(1, n - 1)) + phase) % 1.0
        color = _lerp_color(AMBER, AMBER_SOFT, _ease(t))
        brand.append(ch, style=f"bold {color}")
    if version:
        brand.append(f"  v{version}", style=DIM_2)
    return brand


def play_first_run_intro(
    subtitle: str | None = None,
    version: str | None = None,
    *,
    frames: int = 4,
    frame_seconds: float = 0.06,
) -> None:
    """Play the first-run gradient sweep, then leave the brand on screen.

    Honors the caller's already-gated decision — does NOT re-check
    :func:`use_animation` here. Use :func:`should_play_first_run_intro`
    before calling. ``frames`` / ``frame_seconds`` are exposed so tests
    can run with zero sleep.
    """
    import time

    from rich.live import Live

    initial = _gradient_brand(version, 0.0)
    with Live(initial, console=console, refresh_per_second=30, transient=True) as live:
        for f in range(frames):
            phase = (f + 1) / max(1, frames)
            live.update(_gradient_brand(version, phase))
            if frame_seconds > 0:
                time.sleep(frame_seconds)

    # Final non-transient print so the brand stays in scrollback as the
    # full banner shape (brand + subtitle + rule) — matches static banner.
    console.print(banner(subtitle, version=version))


def _banner_seen_path() -> Path:
    """Where we persist the 'first-run banner played' marker."""
    from loupe.store import _default_dir
    return _default_dir() / ".banner-seen"


def should_play_first_run_intro() -> bool:
    """True iff the animated banner should play right now.

    Three gates AND'd together: animation allowed by the terminal,
    a marker file does not yet exist, and we can write to it (so a
    read-only home dir does not stall startup on every invocation).
    """
    from loupe._term import use_animation

    if not use_animation():
        return False
    marker = _banner_seen_path()
    if marker.exists():
        return False
    # Make sure we'll be able to mark it afterwards — otherwise the
    # animation plays every invocation, which is exactly what we don't
    # want. A read-only home short-circuits to False.
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    else:
        return True


def mark_first_run_seen() -> None:
    """Touch the marker so :func:`should_play_first_run_intro` is False next time.

    Best-effort: a write failure (read-only home, full disk, racing
    parallel processes) is silently swallowed — failing here would
    crash a successful first run, which is the worst possible UX.
    """
    with suppress(OSError):
        _banner_seen_path().touch(exist_ok=True)


# --- Section heading -------------------------------------------------------


def section(title: str) -> Text:
    """Caps-locked, letterspaced label used to introduce CLI sections."""
    return Text(title.upper(), style=f"bold {DIM}")


# --- Key/value layout ------------------------------------------------------


def kv_table(rows: list[tuple[str, str]]) -> Table:
    """A two-column key/value layout with right-aligned keys."""
    table = Table(
        show_header=False,
        show_edge=False,
        box=None,
        padding=(0, 2),
    )
    table.add_column("k", style=DIM, justify="right", min_width=14, no_wrap=True)
    table.add_column("v", style=INK, overflow="fold")
    for k, v in rows:
        table.add_row(k, v)
    return table


# --- Status / check table --------------------------------------------------


def status_table(rows: list[tuple[str, str, str]]) -> Table:
    """Three-column status grid (check / status / detail).

    Detail column folds at narrow widths so install hints
    ('pip install loupe[pydantic-ai]') don't blow up the layout.
    """
    table = Table(
        show_header=True,
        show_edge=False,
        header_style=f"dim {DIM}",
        box=SIMPLE,
        padding=(0, 2),
    )
    table.add_column("check", style=INK, no_wrap=True, ratio=2)
    table.add_column("status", no_wrap=True, ratio=1)
    table.add_column("detail", style=DIM, ratio=3, overflow="fold")
    for check, status, detail in rows:
        table.add_row(check, status, detail)
    return table


# --- Micro-typography ------------------------------------------------------


def hint(text: str) -> Text:
    return Text("  → " + text, style=DIM)


def cmd(text: str) -> Text:
    return Text("  $ " + text, style=AMBER)


def crumb(*parts: str) -> Text:
    """A breadcrumb-style line: 'foo · bar · baz' in dim ink."""
    line = Text()
    for i, p in enumerate(parts):
        if i:
            line.append("  ·  ", style=DIM_2)
        line.append(p, style=DIM)
    return line


# --- Stacking & rendering --------------------------------------------------


def stack(*items: object) -> Group:
    """Stack items vertically without inserting blank lines between them."""
    return Group(*items)  # type: ignore[arg-type]


def render_padded(*items: object) -> None:
    """Print items with consistent left/right padding."""
    console.print(Padding(stack(*items), (1, 2)))


# --- Spinner / status ------------------------------------------------------


@contextmanager
def spinner(message: str) -> Iterator[Status]:
    """Show a Rich spinner while a slow operation runs.

    Auto-disables when stdout isn't a TTY (CI logs, piped output) so the
    output stays grep-friendly.
    """
    if not console.is_terminal or not sys.stdout.isatty():
        # No spinner; print a static '…' line so the operation is still
        # visible in the log.
        console.print(Text(f"  · {message}…", style=DIM))
        yield Status(message, console=console)
        return
    with console.status(
        Text(message, style=INK_2),
        spinner="dots",
        spinner_style=AMBER,
    ) as status:
        yield status


__all__ = [
    "AMBER",
    "AMBER_SOFT",
    "DIM",
    "DIM_2",
    "GREEN",
    "INK",
    "INK_2",
    "LOGO_MARK",
    "RED",
    "Spinner",
    "banner",
    "cmd",
    "console",
    "crumb",
    "hint",
    "is_narrow",
    "kv_table",
    "render_padded",
    "section",
    "spinner",
    "stack",
    "status_table",
    "term_width",
]
