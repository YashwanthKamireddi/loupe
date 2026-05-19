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
from contextlib import contextmanager

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
