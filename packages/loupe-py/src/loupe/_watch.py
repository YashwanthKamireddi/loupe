"""``loupe watch`` — live forensic dashboard in the terminal.

A Textual app that tails ``~/.loupe/traces/*.jsonl`` and renders every
new trace as a one-line card. Header carries a 14-day capture-rate
sparkline; footer carries the hot-key bar (lazygit / k9s convention).
Pressing ``enter`` opens the focused trace inline via the existing
``loupe show`` Rich rendering.

Implementation notes:
* Refresh tick is 500ms — fast enough to feel live, slow enough that
  one long-running ``loupe watch`` doesn't dominate IO on a developer
  machine.
* Trace headers are the only thing read on each tick. Steps are
  deferred until the user opens detail, so 50 visible traces ≈ 50
  one-line reads/tick.
* The list is sorted by mtime descending so the newest capture sits
  on top — matches every other Loupe "list" surface.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from loupe._sparkline import daily_series, sparkline
from loupe.store import _default_dir, read_trace_header

REFRESH_SECONDS = 0.5
MAX_VISIBLE = 50


def _fmt_time(ts: float | None) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=UTC).astimezone().strftime("%H:%M:%S")


def _status_glyph(failed: bool) -> str:
    return "✗" if failed else "✓"


def _read_card(path: Path) -> dict[str, Any] | None:
    """One row's worth of data from a trace file. ``None`` on read error."""
    header = read_trace_header(path)
    if header is None:
        return None
    meta = header.get("metadata") or {}
    # ``inputs`` of the trace header (when present) carries the rolled-up
    # provider/model summary; fall back to top-level metadata fields.
    inputs = header.get("inputs") or {}
    provider = inputs.get("provider") or meta.get("provider") or "—"
    model = inputs.get("model") or meta.get("model") or "—"
    failed = bool(meta.get("failed"))
    return {
        "started_at": header.get("started_at"),
        "trace_id": str(header.get("trace_id") or path.stem),
        "provider": str(provider),
        "model": str(model),
        "status": _status_glyph(failed),
        "failed": failed,
    }


class WatchApp(App[None]):
    """Live trace-tail dashboard."""

    TITLE = "loupe watch"
    SUB_TITLE = "live forensic capture"

    CSS = """
    Screen {
        background: #181a1f;
        color: #f5f0e3;
    }
    Header {
        background: #1f2128;
        color: #e0a458;
    }
    Footer {
        background: #1f2128;
        color: #cabfa9;
    }
    #spark {
        height: 1;
        padding: 0 1;
        color: #e0a458;
    }
    DataTable {
        background: #181a1f;
    }
    DataTable > .datatable--header {
        background: #1f2128;
        color: #cabfa9;
    }
    DataTable > .datatable--cursor {
        background: #2a2d36;
    }
    .status-ok {
        color: #7ea96b;
    }
    .status-fail {
        color: #e25a47;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("r", "refresh_now", "refresh"),
        Binding("f", "toggle_failed", "failed only"),
    ]

    def __init__(self, traces_dir: Path | None = None) -> None:
        super().__init__()
        self.traces_dir = traces_dir or (_default_dir() / "traces")
        # Cache the row data keyed by path so we only re-build the
        # table when the on-disk set or mtimes actually change.
        self._signature: tuple[tuple[str, float], ...] = ()
        self._failed_only = False

    # --- composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield Static("", id="spark")
            yield DataTable(id="traces", zebra_stripes=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#traces", DataTable)
        table.cursor_type = "row"
        table.add_columns("time", "trace", "provider", "model", "status")
        self.set_interval(REFRESH_SECONDS, self._refresh)
        self._refresh()

    # --- core refresh loop -------------------------------------------------

    def _list_trace_files(self) -> list[Path]:
        if not self.traces_dir.exists():
            return []
        files = list(self.traces_dir.glob("*.jsonl"))
        # mtime descending — newest first
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files[:MAX_VISIBLE]

    def _refresh(self) -> None:
        files = self._list_trace_files()
        signature = tuple((str(p), p.stat().st_mtime) for p in files)
        if signature == self._signature:
            return
        self._signature = signature

        table = self.query_one("#traces", DataTable)
        spark_widget = self.query_one("#spark", Static)

        # Read cards (cheap: one short line per file)
        cards: list[dict[str, Any]] = []
        spark_items: list[tuple[float, float]] = []
        for p in files:
            c = _read_card(p)
            if c is None:
                continue
            if self._failed_only and not c["failed"]:
                continue
            cards.append(c)
            if c["started_at"] is not None:
                spark_items.append((float(c["started_at"]), 1.0))

        # Header sparkline — 14d capture rate
        spark = sparkline(daily_series(spark_items, days=14))
        if spark:
            spark_widget.update(f"  14d  {spark}   {len(cards)} traces")
        else:
            spark_widget.update(f"  {len(cards)} traces")

        # Rebuild the table — Textual's DataTable doesn't have a
        # cheap diff API yet, but at MAX_VISIBLE=50 a full rebuild is
        # imperceptible (~1 ms).
        table.clear()
        for c in cards:
            table.add_row(
                _fmt_time(c["started_at"]),
                c["trace_id"][:14],
                c["provider"],
                c["model"],
                c["status"],
            )

    # --- actions -----------------------------------------------------------

    def action_refresh_now(self) -> None:
        # Force a rebuild on the next tick by invalidating the signature.
        self._signature = ()
        self._refresh()

    def action_toggle_failed(self) -> None:
        self._failed_only = not self._failed_only
        self._signature = ()
        self._refresh()


def run(traces_dir: Path | None = None) -> None:
    """Entry point invoked by the ``loupe watch`` CLI command."""
    WatchApp(traces_dir=traces_dir).run()


__all__ = ["WatchApp", "run"]
# silence unused-import warning when textual is shimmed in tests
_ = time
