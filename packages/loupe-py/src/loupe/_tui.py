"""Shared TUI primitives — the Loupe CLI's look-and-feel lives here.

Goals:
- Futuristic, smooth, content-first.
- One canonical palette + one canonical logo across every command.
- Cheap to render — no animations, no heavy boxes, nothing that breaks `--no-color`.
"""

from __future__ import annotations

from rich.box import HORIZONTALS, SIMPLE
from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Shared single console — so we don't make a new one in every command.
console = Console(highlight=False, soft_wrap=False)

AMBER = "#e0a458"
AMBER_SOFT = "#f0c080"
DIM = "grey53"
INK = "#f5f0e3"
RED = "#e25a47"
GREEN = "#7ea96b"

LOGO = "◉  L O U P E"


def banner(subtitle: str | None = None, version: str | None = None) -> Panel:
    """The Loupe banner — a single, calm, branded element."""
    title = Text()
    title.append("◉", style=f"bold {AMBER}")
    title.append("  L O U P E", style=f"bold {INK}")
    if version:
        title.append(f"   v{version}", style=DIM)

    bits = [title]
    if subtitle:
        sub = Text(subtitle, style=f"italic {AMBER_SOFT}")
        bits.append(sub)

    return Panel(
        Group(*bits),
        box=HORIZONTALS,
        border_style=AMBER,
        padding=(0, 2),
    )


def section(title: str) -> Text:
    """Caps-locked, letterspaced label used to introduce CLI sections."""
    return Text(
        title.upper(),
        style=f"bold {DIM}",
    )


def kv_table(rows: list[tuple[str, str]]) -> Table:
    """A two-column key/value layout with our standard styling."""
    table = Table(
        show_header=False,
        show_edge=False,
        box=None,
        padding=(0, 2),
    )
    table.add_column("k", style=DIM, justify="right", min_width=14)
    table.add_column("v", style=INK)
    for k, v in rows:
        table.add_row(k, v)
    return table


def status_table(rows: list[tuple[str, str, str]]) -> Table:
    """Three-column status grid (check / status / detail)."""
    table = Table(
        show_header=True,
        show_edge=False,
        header_style=f"dim {DIM}",
        box=SIMPLE,
        padding=(0, 2),
    )
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail", style=DIM)
    for check, status, detail in rows:
        table.add_row(check, status, detail)
    return table


def hint(text: str) -> Text:
    return Text("  → " + text, style=DIM)


def cmd(text: str) -> Text:
    return Text("$ " + text, style=AMBER)


def stack(*items: object) -> Group:
    """Stack things with a blank line between."""
    parts: list[object] = []
    for i, x in enumerate(items):
        if i > 0:
            parts.append(Text())
        parts.append(x)
    return Group(*parts)  # type: ignore[arg-type]  # Rich accepts any renderable


def render_padded(*items: object) -> None:
    """Print items with consistent left/right padding."""
    console.print(Padding(stack(*items), (1, 2)))


__all__ = [
    "AMBER",
    "AMBER_SOFT",
    "DIM",
    "GREEN",
    "INK",
    "LOGO",
    "RED",
    "banner",
    "cmd",
    "console",
    "hint",
    "kv_table",
    "render_padded",
    "section",
    "stack",
    "status_table",
]
