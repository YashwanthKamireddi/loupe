"""`loupe` CLI entry point.

Commands:
    loupe list                        List all traces stored locally
    loupe show <trace-id>             Print step-by-step content of one trace
    loupe ui [--port 7860]            Launch the local web dashboard
    loupe tag <trace> <step> <cat>    Mark a step as a benchmark failure
    loupe untag <trace> <step>        Remove a tag
    loupe annotations <trace>         List tags on one trace
    loupe export [--out FILE]         Bundle annotated failures into LoupeBench JSONL
    loupe report <trace-id> [--out]   Render a shareable markdown case file
    loupe init <name> [--dir PATH]    Scaffold a starter agent project
    loupe demo                        Seed three sample traces (great for first run)
    loupe doctor                      Diagnose Loupe install + show what's wired up
    loupe version                     Print Loupe version
"""

from __future__ import annotations

import importlib
import importlib.metadata as md
import json
import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from loupe._version import __version__
from loupe.annotation import Annotation, AnnotationStore
from loupe.bench import export_jsonl
from loupe.demo import seed as demo_seed
from loupe.report import render_trace_markdown
from loupe.scaffold import scaffold
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

    ann_store = AnnotationStore()
    table = Table(title="Loupe traces", show_lines=False)
    table.add_column("trace_id", style="cyan")
    table.add_column("name", style="bold")
    table.add_column("framework", style="dim")
    table.add_column("duration", justify="right")
    table.add_column("steps", justify="right")
    table.add_column("tags", justify="right")
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
        ann_count = len(ann_store.load(header["trace_id"]))
        table.add_row(
            header["trace_id"][:12],
            header["name"],
            header.get("framework") or "—",
            duration,
            str(steps),
            str(ann_count) if ann_count else "[dim]—[/dim]",
            status,
        )

    console.print(table)


@app.command("show")
def show_trace(trace_id: str) -> None:
    """Show the full step-by-step content of one trace."""
    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)
    for line in path.open():
        obj = json.loads(line)
        kind = obj.pop("_type")
        if kind == "trace":
            console.print(f"[bold cyan]Trace {obj['trace_id'][:12]}[/bold cyan] — {obj['name']}")
        else:
            err = f"  [red]{obj['error']}[/red]" if obj.get("error") else ""
            console.print(f"  [dim]{obj['kind']:>10}[/dim]  [bold]{obj['name']}[/bold]{err}")


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


@app.command("tag")
def tag(
    trace_id: str,
    step_id: str,
    category: str = typer.Argument(
        ..., help="Failure category, e.g. unguarded-delete, loop, hallucination"
    ),
    notes: str = typer.Option("", "--notes", "-n", help="Free-text root-cause notes"),
    mitigation: str = typer.Option("", "--mitigation", "-m", help="What fixed it"),
    severity: str = typer.Option("medium", "--severity", "-s", help="low|medium|high|critical"),
    tags: list[str] = typer.Option(None, "--tag", "-t", help="Extra free-form tags (repeat)"),
) -> None:
    """Mark a step as a benchmark-worthy failure."""
    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)
    full_trace_id = path.stem

    # Validate step exists
    step_match = None
    for line in path.open():
        obj = json.loads(line)
        if obj.get("_type") == "step" and obj.get("step_id", "").startswith(step_id):
            step_match = obj["step_id"]
            break
    if step_match is None:
        console.print(f"[red]No step matching {step_id} in trace {trace_id}[/red]")
        raise typer.Exit(code=1)

    annotator = os.environ.get("USER", "anon")
    ann = Annotation(
        trace_id=full_trace_id,
        step_id=step_match,
        failure_category=category,  # type: ignore[arg-type]
        notes=notes,
        mitigation=mitigation,
        severity=severity,  # type: ignore[arg-type]
        annotator=annotator,
        tags=list(tags or []),
    )
    AnnotationStore().add(ann)
    console.print(
        f"[green]tagged[/green] {full_trace_id[:12]}/{step_match} "
        f"as [bold]{category}[/bold]"
    )


@app.command("untag")
def untag(trace_id: str, step_id: str) -> None:
    """Remove the tag on a step."""
    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)
    full_trace_id = path.stem
    # Resolve full step_id
    full_step = step_id
    for line in path.open():
        obj = json.loads(line)
        if obj.get("_type") == "step" and obj.get("step_id", "").startswith(step_id):
            full_step = obj["step_id"]
            break
    removed = AnnotationStore().remove(full_trace_id, full_step)
    if removed:
        console.print(f"[green]untagged[/green] {full_trace_id[:12]}/{full_step}")
    else:
        console.print(f"[yellow]no tag found for[/yellow] {full_trace_id[:12]}/{full_step}")


@app.command("annotations")
def annotations(trace_id: str) -> None:
    """List annotations on one trace."""
    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)
    full_trace_id = path.stem
    items = AnnotationStore().load(full_trace_id)
    if not items:
        console.print("[dim]No annotations on this trace.[/dim]")
        return
    table = Table(title=f"Annotations · {full_trace_id[:12]}")
    table.add_column("step_id", style="cyan")
    table.add_column("category", style="bold")
    table.add_column("severity")
    table.add_column("notes", style="dim")
    for a in items:
        table.add_row(a.step_id[:12], a.failure_category, a.severity, a.notes or "—")
    console.print(table)


@app.command("export")
def export(
    out: Path = typer.Option(Path("loupe-bench.jsonl"), "--out", "-o", help="Output JSONL path"),
    license_: str = typer.Option("CC-BY-4.0", "--license", help="License field on each record"),
) -> None:
    """Bundle all annotated failures into LoupeBench-compatible JSONL."""
    count = export_jsonl(out, license=license_)
    if count == 0:
        console.print("[yellow]Nothing to export yet — tag some failures first.[/yellow]")
        return
    console.print(f"[green]exported[/green] {count} record(s) → [bold]{out}[/bold]")


@app.command("report")
def report(
    trace_id: str,
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Write markdown to this path (otherwise stdout)"
    ),
) -> None:
    """Render a shareable markdown case file for one trace."""
    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)
    md = render_trace_markdown(path)
    if out:
        out.write_text(md, encoding="utf-8")
        console.print(f"[green]wrote[/green] {out}")
    else:
        # Use plain print to keep the markdown clean for piping
        typer.echo(md)


@app.command("init")
def init(
    name: str = typer.Argument(..., help="Project / agent name"),
    target: Path = typer.Option(
        Path("."), "--dir", "-d", help="Target directory (default: ./<name>)"
    ),
) -> None:
    """Scaffold a Loupe-instrumented agent starter."""
    project_dir = target / name if target == Path(".") else target
    if project_dir.exists() and any(project_dir.iterdir()):
        console.print(
            f"[red]Refusing to write into non-empty directory {project_dir}[/red]"
        )
        raise typer.Exit(code=1)
    files = scaffold(project_dir, name)
    console.print(f"[green]scaffolded[/green] {project_dir}")
    for f in files:
        console.print(f"  [dim]→[/dim] {f.relative_to(project_dir.parent)}")
    try:
        display_path = project_dir.relative_to(Path.cwd())
    except ValueError:
        display_path = project_dir
    console.print()
    console.print(f"  cd {display_path}")
    console.print("  python agent.py")
    console.print("  loupe ui")


@app.command("demo")
def demo(
    no_tag: bool = typer.Option(
        False, "--no-tag", help="Skip pre-baking an annotation on the failure trace"
    ),
) -> None:
    """Seed three sample traces so the dashboard isn't empty on first run."""
    ids = demo_seed(tag_failure=not no_tag)
    console.print(f"[green]seeded[/green] {len(ids)} trace(s):")
    for trace_id in ids:
        console.print(f"  [dim]→[/dim] {trace_id[:12]}")
    console.print()
    console.print("Now run: [bold]loupe ui[/bold]   (or refresh http://localhost:7860)")


@app.command("version")
def version() -> None:
    """Print Loupe version."""
    console.print(f"loupe [cyan]{__version__}[/cyan]")


@app.command("doctor")
def doctor() -> None:
    """Diagnose Loupe install + report what integrations are reachable."""
    table = Table(title=f"loupe doctor · v{__version__}", show_lines=False)
    table.add_column("check", style="bold")
    table.add_column("status")
    table.add_column("detail", style="dim")

    home = _default_dir()
    table.add_row("LOUPE_HOME", "[green]ok[/green]", str(home))

    traces = list((home / "traces").glob("*.jsonl")) if (home / "traces").exists() else []
    table.add_row(
        "traces dir",
        "[green]ok[/green]" if traces else "[yellow]empty[/yellow]",
        f"{len(traces)} trace(s)",
    )

    annots = list((home / "annotations").glob("*.json")) if (home / "annotations").exists() else []
    table.add_row(
        "annotations dir",
        "[green]ok[/green]" if annots else "[yellow]empty[/yellow]",
        f"{len(annots)} file(s)",
    )

    for pkg, integration in [
        ("langchain_core", "langchain"),
        ("anthropic", "anthropic"),
        ("openai", "openai"),
        ("fastapi", "ui"),
    ]:
        try:
            importlib.import_module(pkg)
            ver = md.version(pkg) if pkg != "fastapi" else md.version("fastapi")
            table.add_row(f"integration:{integration}", "[green]ready[/green]", f"{pkg} {ver}")
        except (ImportError, md.PackageNotFoundError):
            table.add_row(
                f"integration:{integration}",
                "[dim]missing[/dim]",
                f"pip install '{integration}'",
            )

    table.add_row("python", "[green]ok[/green]", sys.version.split()[0])
    console.print(table)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _find_trace(trace_id: str) -> Path | None:
    traces_dir = _default_dir() / "traces"
    matches = list(traces_dir.glob(f"{trace_id}*.jsonl"))
    if not matches:
        console.print(f"[red]No trace matching {trace_id}[/red]")
        return None
    if len(matches) > 1:
        console.print(f"[yellow]Multiple matches; picking {matches[0].stem}[/yellow]")
    return matches[0]


def _read_header(path: Path) -> dict | None:
    try:
        with path.open() as f:
            first = json.loads(next(f))
        if first.get("_type") != "trace":
            return None
        return first
    except (StopIteration, json.JSONDecodeError, KeyError):
        return None


if __name__ == "__main__":
    app()
