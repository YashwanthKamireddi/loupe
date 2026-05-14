"""`loupe` CLI entry point.

Commands:
    loupe list                       List all traces stored locally
    loupe show <trace-id>            Print step-by-step content of one trace
    loupe ui [--port 7860]           Launch the local web dashboard
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from loupe.store import _default_dir

app = typer.Typer(
    name="loupe",
    help="A magnifying glass for your AI agent.",
    no_args_is_help=True,
)
console = Console()


@app.command("list")
def list_traces() -> None:
    """List all traces stored locally."""
    traces_dir = _default_dir() / "traces"
    if not traces_dir.exists():
        console.print("[dim]No traces yet. Wrap a function with @trace.[/dim]")
        return

    files = sorted(traces_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        console.print("[dim]No traces yet. Wrap a function with @trace.[/dim]")
        return

    table = Table(title="Loupe traces", show_lines=False)
    table.add_column("trace_id", style="cyan")
    table.add_column("name", style="bold")
    table.add_column("framework", style="dim")
    table.add_column("duration", justify="right")
    table.add_column("steps", justify="right")
    table.add_column("status")

    for file in files[:50]:
        header = _read_header(file)
        if header is None:
            continue
        steps = sum(1 for _ in file.open()) - 1
        duration = (
            f"{(header['ended_at'] - header['started_at']) * 1000:.0f} ms"
            if header.get("ended_at")
            else "—"
        )
        failed = header.get("metadata", {}).get("failed", False)
        status = "[red]failed[/red]" if failed else "[green]ok[/green]"
        table.add_row(
            header["trace_id"][:12],
            header["name"],
            header.get("framework") or "—",
            duration,
            str(steps),
            status,
        )

    console.print(table)


@app.command("show")
def show_trace(trace_id: str) -> None:
    """Show the full step-by-step content of one trace."""
    traces_dir = _default_dir() / "traces"
    matches = list(traces_dir.glob(f"{trace_id}*.jsonl"))
    if not matches:
        console.print(f"[red]No trace matching {trace_id}[/red]")
        raise typer.Exit(code=1)
    if len(matches) > 1:
        console.print(f"[yellow]Multiple matches; picking {matches[0].stem}[/yellow]")

    for line in matches[0].open():
        obj = json.loads(line)
        kind = obj.pop("_type")
        if kind == "trace":
            console.print(f"[bold cyan]Trace {obj['trace_id'][:12]}[/bold cyan] — {obj['name']}")
        else:
            console.print(
                f"  [dim]{obj['kind']:>10}[/dim]  [bold]{obj['name']}[/bold]"
                + (f"  [red]{obj['error']}[/red]" if obj.get("error") else "")
            )


@app.command("ui")
def ui(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(7860, help="Bind port"),
) -> None:
    """Launch the local web dashboard (FastAPI + uvicorn)."""
    try:
        import uvicorn

        from loupe.ui.server import create_app
    except ImportError:
        console.print(
            "[red]loupe ui needs fastapi + uvicorn.[/red] "
            "Install with: pip install 'loupe[ui]'"
        )
        raise typer.Exit(code=1) from None

    console.print(f"[cyan]loupe[/cyan] [bold]ui[/bold]  ·  http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


def _read_header(path: Path) -> dict | None:
    try:
        with path.open() as f:
            first = json.loads(next(f))
            assert first["_type"] == "trace"
            return first
    except (StopIteration, json.JSONDecodeError, KeyError, AssertionError):
        return None


if __name__ == "__main__":
    app()
