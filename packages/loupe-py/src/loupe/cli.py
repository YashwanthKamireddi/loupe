"""`loupe` CLI entry point.

Commands:
    loupe                             Welcome screen + quickstart
    loupe start                       Interactive first run: seed + open UI
    loupe demo                        Seed three sample traces
    loupe init <name>                 Scaffold a starter agent project
    loupe providers                   List every LLM provider auto-detected
    loupe ui [--port 7860]            Launch the local forensic dashboard
    loupe list                        List all captured traces
    loupe show <trace-id>             Print one trace step-by-step
    loupe tag <trace> <step> <cat>    Mark a step as a benchmark failure
    loupe untag <trace> <step>        Remove a tag
    loupe annotations <trace>         List tags on one trace
    loupe export [--out FILE]         Bundle annotated failures
    loupe report <trace-id> [--out]   Render a shareable markdown case file
    loupe doctor                      Diagnose install + integrations
    loupe version                     Print version
"""

from __future__ import annotations

import importlib
import importlib.metadata as md
import json
import os
import sys
import webbrowser
from pathlib import Path
from typing import Any

import typer
from rich.text import Text

from loupe._tui import (
    AMBER,
    DIM,
    GREEN,
    INK,
    RED,
    banner,
    cmd,
    console,
    hint,
    render_padded,
    section,
    stack,
    status_table,
)
from loupe._version import __version__
from loupe.annotation import Annotation, AnnotationStore
from loupe.bench import export_jsonl
from loupe.demo import seed as demo_seed
from loupe.report import render_trace_markdown
from loupe.scaffold import scaffold
from loupe.store import _default_dir

# Custom click context: no implicit error formatting, we'll do our own.
app = typer.Typer(
    name="loupe",
    help="A magnifying glass for your AI agent.",
    no_args_is_help=False,  # we provide our own welcome screen
    add_completion=False,
    rich_markup_mode=None,
)


# ----------------------------------------------------------------------------
# Welcome / start
# ----------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Show a welcome screen when no command is given."""
    if ctx.invoked_subcommand is not None:
        return
    _show_welcome()


def _show_welcome() -> None:
    home = _default_dir()
    trace_count = len(list((home / "traces").glob("*.jsonl"))) if (home / "traces").exists() else 0

    sub = "Forensic observability for AI agents — open-source, local-first."

    if trace_count == 0:
        next_steps = stack(
            section("Get started in 30 seconds"),
            Text(),
            cmd("loupe start         # seed sample traces + open the dashboard"),
            Text(),
            section("Or, one at a time"),
            cmd("loupe demo          # create three sample traces"),
            cmd("loupe ui            # open the forensic dashboard"),
            cmd("loupe init my-agent # scaffold an instrumented starter project"),
        )
    else:
        next_steps = stack(
            section(f"You have {trace_count} trace(s) captured"),
            Text(),
            cmd("loupe ui            # open the forensic dashboard"),
            cmd("loupe list          # see them in the terminal"),
            cmd("loupe demo          # add more samples"),
        )

    render_padded(
        banner(sub, version=__version__),
        next_steps,
        Text(),
        section("Help"),
        hint("loupe doctor        — diagnose your install + integrations"),
        hint("loupe --help        — full command reference"),
    )


@app.command("start")
def start(
    port: int = typer.Option(7860, "--port", "-p", help="Port for the dashboard"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't auto-open the browser"),
    skip_demo: bool = typer.Option(False, "--no-demo", help="Don't seed demo traces"),
) -> None:
    """Interactive first run: seed samples, open the dashboard."""
    render_padded(banner("first run", version=__version__))

    home = _default_dir()
    existing = len(list((home / "traces").glob("*.jsonl"))) if (home / "traces").exists() else 0
    needs_seed = existing == 0 and not skip_demo

    if needs_seed:
        console.print(Text("  Seeding sample traces…", style=DIM))
        ids = demo_seed()
        console.print(
            Text("  ✓ ", style=GREEN) +
            Text(f"{len(ids)} sample trace(s) created.", style=INK)
        )
    elif existing > 0:
        console.print(Text("  ✓ ", style=GREEN) + Text(f"{existing} existing trace(s).", style=INK))

    url = f"http://127.0.0.1:{port}"
    console.print(Text("  Dashboard:  ", style=DIM) + Text(url, style=AMBER))
    console.print()

    if not no_browser:
        try:
            webbrowser.open(url, new=1)
            console.print(Text(f"  Opening {url} in your browser…", style=DIM))
        except Exception:  # pragma: no cover
            pass

    console.print(Text("  Press Ctrl-C to stop.", style=DIM))
    console.print()
    _run_ui(host="127.0.0.1", port=port)


# ----------------------------------------------------------------------------
# Trace listing & inspection
# ----------------------------------------------------------------------------


@app.command("list")
def list_traces() -> None:
    """List all traces stored locally."""
    traces_dir = _default_dir() / "traces"
    if not traces_dir.exists():
        render_padded(banner(version=__version__), _no_traces_hint())
        return

    files = sorted(traces_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        render_padded(banner(version=__version__), _no_traces_hint())
        return

    ann_store = AnnotationStore()
    from rich.box import SIMPLE
    from rich.table import Table
    table = Table(
        show_header=True,
        header_style=f"dim {DIM}",
        box=SIMPLE,
        padding=(0, 2),
        title=Text("traces", style=f"italic {AMBER}"),
        title_justify="left",
    )
    table.add_column("trace_id", style=AMBER)
    table.add_column("name", style=INK)
    table.add_column("framework", style=DIM)
    table.add_column("duration", justify="right", style=DIM)
    table.add_column("steps", justify="right")
    table.add_column("tags", justify="right")
    table.add_column("status")

    for file in files[:100]:
        header = _read_header(file)
        if header is None:
            continue
        steps = sum(1 for _ in file.open()) - 1
        duration = (
            f"{(header['ended_at'] - header['started_at']) * 1000:.0f} ms"
            if header.get("ended_at") else "—"
        )
        failed = header.get("metadata", {}).get("failed", False)
        status = Text("failed", style=RED) if failed else Text("ok", style=GREEN)
        ann_count = len(ann_store.load(header["trace_id"]))
        table.add_row(
            header["trace_id"][:12],
            header["name"],
            header.get("framework") or "—",
            duration,
            str(steps),
            Text(str(ann_count), style=AMBER) if ann_count else Text("—", style=DIM),
            status,
        )

    console.print()
    console.print(table)
    console.print()


@app.command("show")
def show_trace(trace_id: str) -> None:
    """Print the full step-by-step content of one trace."""
    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)

    header: dict | None = None
    steps: list[dict] = []
    for line in path.open():
        obj = json.loads(line)
        kind = obj.pop("_type")
        if kind == "trace":
            header = obj
        elif kind == "step":
            steps.append(obj)

    if header is None:
        console.print(Text("malformed trace", style=RED))
        raise typer.Exit(code=1)

    console.print()
    title = Text()
    title.append("◉ ", style=AMBER)
    title.append(header["name"], style=f"bold {INK}")
    title.append("  ·  ", style=DIM)
    title.append(header.get("framework") or "—", style=DIM)
    title.append("  ·  ", style=DIM)
    title.append(header["trace_id"][:12], style=AMBER)
    console.print(title)

    for i, step in enumerate(steps, 1):
        kind_style = {
            "llm-call": "blue",
            "tool-call": "magenta",
            "thought": DIM,
            "error": RED,
            "io": DIM,
            "custom": DIM,
        }.get(step["kind"], DIM)
        line = Text()
        line.append(f"  {i:>2}. ", style=DIM)
        line.append(f"{step['kind']:>10}", style=kind_style)
        line.append(f"  {step['name']}", style=INK)
        if step.get("error"):
            line.append(f"\n        {step['error']}", style=RED)
        console.print(line)
    console.print()


# ----------------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------------


@app.command("ui")
def ui(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(7860, help="Bind port"),
) -> None:
    """Launch the local forensic dashboard."""
    _run_ui(host=host, port=port)


def _run_ui(*, host: str, port: int) -> None:
    try:
        import uvicorn

        from loupe.ui.server import create_app
    except ImportError:
        console.print(
            Text("  loupe ui needs fastapi + uvicorn.", style=RED) +
            Text("  Install with:  pip install 'loupe[ui]'", style=DIM)
        )
        raise typer.Exit(code=1) from None

    url_text = Text()
    url_text.append("  ◉ Loupe ", style=AMBER)
    url_text.append(f"http://{host}:{port}", style=f"bold {INK}")
    console.print(url_text)
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


# ----------------------------------------------------------------------------
# Annotation workflow
# ----------------------------------------------------------------------------


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
    tags: list[str] = typer.Option(None, "--tag", "-t", help="Extra free-form tags"),
) -> None:
    """Mark a step as a benchmark-worthy failure."""
    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)
    full_trace_id = path.stem

    step_match = None
    for line in path.open():
        obj = json.loads(line)
        if obj.get("_type") == "step" and obj.get("step_id", "").startswith(step_id):
            step_match = obj["step_id"]
            break
    if step_match is None:
        console.print(Text(f"  No step matching {step_id}", style=RED))
        raise typer.Exit(code=1)

    annotator = os.environ.get("USER", "anon")
    AnnotationStore().add(Annotation(
        trace_id=full_trace_id,
        step_id=step_match,
        failure_category=category,  # type: ignore[arg-type]
        notes=notes,
        mitigation=mitigation,
        severity=severity,  # type: ignore[arg-type]
        annotator=annotator,
        tags=list(tags or []),
    ))
    msg = Text()
    msg.append("  ✓ tagged ", style=GREEN)
    msg.append(f"{full_trace_id[:12]}/{step_match} ", style=INK)
    msg.append("as ", style=DIM)
    msg.append(category, style=AMBER)
    console.print(msg)


@app.command("untag")
def untag(trace_id: str, step_id: str) -> None:
    """Remove a tag on a step."""
    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)
    full_trace_id = path.stem
    full_step = step_id
    for line in path.open():
        obj = json.loads(line)
        if obj.get("_type") == "step" and obj.get("step_id", "").startswith(step_id):
            full_step = obj["step_id"]
            break
    removed = AnnotationStore().remove(full_trace_id, full_step)
    if removed:
        console.print(Text(f"  ✓ untagged {full_trace_id[:12]}/{full_step}", style=GREEN))
    else:
        console.print(Text(f"  no tag found for {full_trace_id[:12]}/{full_step}", style=DIM))


@app.command("annotations")
def annotations(trace_id: str) -> None:
    """List annotations on one trace."""
    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)
    items = AnnotationStore().load(path.stem)
    if not items:
        console.print(Text("  No annotations on this trace.", style=DIM))
        return
    from rich.box import SIMPLE
    from rich.table import Table
    table = Table(
        show_header=True,
        header_style=f"dim {DIM}",
        box=SIMPLE,
        padding=(0, 2),
        title=Text(f"annotations · {path.stem[:12]}", style=f"italic {AMBER}"),
        title_justify="left",
    )
    table.add_column("step_id", style=AMBER)
    table.add_column("category", style=INK)
    table.add_column("severity", style=DIM)
    table.add_column("notes", style=DIM)
    for a in items:
        table.add_row(a.step_id[:12], a.failure_category, a.severity, a.notes or "—")
    console.print()
    console.print(table)
    console.print()


# ----------------------------------------------------------------------------
# Export + report + scaffolding + demo
# ----------------------------------------------------------------------------


@app.command("export")
def export(
    out: Path = typer.Option(Path("loupe-bench.jsonl"), "--out", "-o"),
    license_: str = typer.Option("CC-BY-4.0", "--license"),
) -> None:
    """Bundle annotated failures into LoupeBench JSONL."""
    count = export_jsonl(out, license=license_)
    if count == 0:
        console.print(Text("  Nothing to export yet — tag some failures first.", style=DIM))
        return
    console.print(
        Text(f"  ✓ exported {count} record(s) → ", style=GREEN) +
        Text(str(out), style=AMBER)
    )


@app.command("report")
def report(
    trace_id: str,
    out: Path | None = typer.Option(None, "--out", "-o"),
) -> None:
    """Render a shareable markdown case file."""
    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)
    text = render_trace_markdown(path)
    if out:
        out.write_text(text, encoding="utf-8")
        console.print(Text(f"  ✓ wrote {out}", style=GREEN))
    else:
        typer.echo(text)


@app.command("init")
def init(
    name: str = typer.Argument(..., help="Project / agent name"),
    target: Path = typer.Option(Path("."), "--dir", "-d"),
) -> None:
    """Scaffold a Loupe-instrumented starter project."""
    project_dir = target / name if target == Path(".") else target
    if project_dir.exists() and any(project_dir.iterdir()):
        console.print(Text(f"  Refusing to write into non-empty {project_dir}", style=RED))
        raise typer.Exit(code=1)
    files = scaffold(project_dir, name)
    console.print()
    console.print(Text("  ◉ scaffolded ", style=AMBER) + Text(str(project_dir), style=INK))
    for f in files:
        console.print(Text(f"     → {f.relative_to(project_dir.parent)}", style=DIM))
    try:
        display_path = project_dir.relative_to(Path.cwd())
    except ValueError:
        display_path = project_dir
    console.print()
    console.print(cmd(f"cd {display_path}"))
    console.print(cmd("python agent.py"))
    console.print(cmd("loupe ui"))
    console.print()


@app.command("demo")
def demo(
    no_tag: bool = typer.Option(False, "--no-tag", help="Skip pre-baked annotation"),
) -> None:
    """Seed three sample traces so the dashboard isn't empty."""
    ids = demo_seed(tag_failure=not no_tag)
    console.print()
    console.print(Text(f"  ◉ seeded {len(ids)} trace(s)", style=AMBER))
    for trace_id in ids:
        console.print(Text(f"     → {trace_id[:12]}", style=DIM))
    console.print()
    console.print(Text("  Now run ", style=DIM) + Text("loupe ui", style=AMBER) +
                  Text(" or refresh http://localhost:7860", style=DIM))
    console.print()


# ----------------------------------------------------------------------------
# Diagnostics
# ----------------------------------------------------------------------------


@app.command("doctor")
def doctor() -> None:
    """Diagnose your install + show wired-up integrations."""
    home = _default_dir()
    traces = list((home / "traces").glob("*.jsonl")) if (home / "traces").exists() else []
    annots = list((home / "annotations").glob("*.json")) if (home / "annotations").exists() else []

    rows: list[tuple[str, str, str]] = []
    rows.append(("LOUPE_HOME", _badge_ok(), str(home)))
    rows.append((
        "traces dir",
        _badge_ok() if traces else _badge_empty(),
        f"{len(traces)} trace(s)",
    ))
    rows.append((
        "annotations dir",
        _badge_ok() if annots else _badge_empty(),
        f"{len(annots)} file(s)",
    ))

    integrations = [
        ("langchain_core", "langchain", "langgraph"),
        ("anthropic", "anthropic", "anthropic"),
        ("openai", "openai", "openai"),
        ("pydantic_ai", "pydantic-ai", "pydantic-ai"),
        ("llama_index", "llama-index", "llama-index"),
        ("dspy", "dspy", "dspy"),
        ("crewai", "crewai", "crewai"),
        ("autogen", "autogen", "autogen"),
        ("openhands", "openhands", "openhands"),
        ("httpx", "universal", "universal"),
        ("fastapi", "ui", "ui"),
    ]
    for pkg, integration, extra in integrations:
        try:
            importlib.import_module(pkg)
            ver = md.version(pkg)
            rows.append((f"integration:{integration}", _badge_ready(), f"{pkg} {ver}"))
        except (ImportError, md.PackageNotFoundError):
            # Escape the square brackets so Rich doesn't interpret them as markup.
            extras_hint = f"pip install 'loupe\\[{extra}]'"
            rows.append((
                f"integration:{integration}",
                _badge_missing(),
                extras_hint,
            ))
    rows.append(("python", _badge_ok(), sys.version.split()[0]))

    render_padded(
        banner("install diagnostic", version=__version__),
        status_table(rows),
    )


@app.command("verify")
def verify(
    trace_id: str = typer.Argument(
        "",
        help="Trace id (or prefix). Omit and use --all to check every trace.",
    ),
    check_all: bool = typer.Option(False, "--all", help="Validate every captured trace"),
) -> None:
    """Validate one or all captured traces against the canonical JSON schema."""
    schema_path = _find_schema_file()
    if schema_path is None:
        console.print(
            Text("  schema file not found.  ", style=RED)
            + Text("Reinstall loupe so the bundled schema is restored.", style=DIM)
        )
        raise typer.Exit(code=1)
    try:
        import jsonschema  # type: ignore[import-not-found]
    except ImportError:
        console.print(
            Text("  jsonschema not installed.  ", style=RED)
            + Text("Run: pip install 'loupe\\[dev]'", style=DIM)
        )
        raise typer.Exit(code=1) from None

    import json as _json

    schema = _json.loads(schema_path.read_text(encoding="utf-8"))

    targets: list[Path]
    if check_all:
        traces_dir = _default_dir() / "traces"
        targets = sorted(traces_dir.glob("*.jsonl")) if traces_dir.exists() else []
        if not targets:
            console.print(Text("  no traces to verify.", style=DIM))
            return
    else:
        if not trace_id:
            console.print(Text("  pass a trace id, or use --all.", style=RED))
            raise typer.Exit(code=1)
        path = _find_trace(trace_id)
        if path is None:
            raise typer.Exit(code=1)
        targets = [path]

    failures = 0
    for target in targets:
        ok, payload, err = _validate_trace_file(target, schema, jsonschema)
        label = target.stem[:12]
        name = (payload or {}).get("name", "?")
        step_count = len((payload or {}).get("steps", []))
        if ok:
            console.print(
                Text("  ✓ ", style=GREEN)
                + Text(f"{label} · {step_count} step(s) · {name}", style=INK)
            )
        else:
            failures += 1
            console.print(
                Text("  ✗ ", style=RED)
                + Text(f"{label} · {name}", style=INK)
                + Text(f"  — {err}", style=DIM)
            )

    if failures:
        console.print()
        console.print(
            Text(
                f"  {failures} of {len(targets)} trace(s) failed validation.",
                style=RED,
            )
        )
        raise typer.Exit(code=1)


def _validate_trace_file(
    path: Path,
    schema: dict,
    jsonschema_mod: Any,
) -> tuple[bool, dict | None, str | None]:
    """Read a JSONL trace, convert to ingest payload shape, validate.

    Returns (ok, payload-or-None, error-message-or-None).
    """
    import json as _json

    header: dict | None = None
    steps: list[dict] = []
    try:
        for line in path.open():
            obj = _json.loads(line)
            kind = obj.pop("_type", None)
            if kind == "trace":
                header = obj
            elif kind == "step":
                steps.append(obj)
    except (OSError, _json.JSONDecodeError) as exc:
        return False, None, f"unreadable: {exc}"

    if header is None:
        return False, None, "no trace header"
    payload = {**header, "steps": steps}
    try:
        jsonschema_mod.validate(instance=payload, schema=schema)
    except jsonschema_mod.ValidationError as exc:
        loc = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        return False, payload, f"{exc.message} at {loc}"
    return True, payload, None


@app.command("providers")
def providers() -> None:
    """List every LLM provider the universal capture auto-detects."""
    from rich.box import SIMPLE
    from rich.table import Table

    from loupe.integrations._providers import ALL_PROVIDERS

    by_category: dict[str, list] = {}
    for p in ALL_PROVIDERS:
        by_category.setdefault(p.category, []).append(p)

    categories_order = [
        ("frontier", "Frontier labs"),
        ("inference", "Inference providers"),
        ("aggregator", "Aggregators / gateways"),
        ("cloud", "Enterprise cloud"),
        ("embedding", "Embedding & retrieval"),
        ("local", "Local servers"),
    ]

    render_padded(banner(f"{len(ALL_PROVIDERS)} providers auto-detected", version=__version__))

    for key, label in categories_order:
        items = by_category.get(key, [])
        if not items:
            continue
        table = Table(
            show_header=False,
            show_edge=False,
            box=SIMPLE,
            padding=(0, 2),
            title=Text(label, style=f"italic {AMBER}"),
            title_justify="left",
        )
        table.add_column("name", style=INK)
        table.add_column("host", style=DIM)
        for p in items:
            table.add_row(p.name, p.host_suffix)
        console.print()
        console.print(table)
    console.print()
    console.print(Text(
        "  Plus: unknown hosts whose request body looks like OpenAI spec "
        "are captured as openai-compatible:<host>.",
        style=DIM,
    ))
    console.print()


@app.command("version")
def version() -> None:
    """Print Loupe version."""
    console.print(Text("loupe ", style=DIM) + Text(__version__, style=AMBER))


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _badge_ok() -> str:
    return f"[{GREEN}]●[/{GREEN}] ok"


def _badge_ready() -> str:
    return f"[{GREEN}]●[/{GREEN}] ready"


def _badge_empty() -> str:
    return f"[{AMBER}]○[/{AMBER}] empty"


def _badge_missing() -> str:
    return f"[{DIM}]○[/{DIM}] missing"


def _no_traces_hint() -> object:
    return stack(
        Text("  No traces yet.", style=INK),
        Text(),
        hint("loupe demo    seed three sample traces"),
        hint("loupe init    scaffold an instrumented project"),
    )


def _find_schema_file() -> Path | None:
    """Walk up from cli.py looking for docs/loupe-trace.schema.json.

    Works in any repo layout — dev source tree, installed sdist, monorepo.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "docs" / "loupe-trace.schema.json"
        if candidate.exists():
            return candidate
    return None


def _find_trace(trace_id: str) -> Path | None:
    traces_dir = _default_dir() / "traces"
    matches = list(traces_dir.glob(f"{trace_id}*.jsonl"))
    if not matches:
        console.print(Text(f"  No trace matching {trace_id}", style=RED))
        return None
    if len(matches) > 1:
        console.print(Text(f"  Multiple matches; picking {matches[0].stem}", style=AMBER))
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
