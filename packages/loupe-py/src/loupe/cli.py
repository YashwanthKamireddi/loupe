"""`loupe` CLI entry point.

Commands:
    loupe                             Welcome screen + quickstart
    loupe init <name>                 Scaffold a starter agent project
    loupe providers                   List every LLM provider auto-detected
    loupe stats                       Aggregate overview of captured state
    loupe diff <a> <b>                Side-by-side diff of two traces
    loupe verify [--all]              Validate trace(s) against the canonical schema
    loupe ui [--port 7860]            Launch the local forensic dashboard
    loupe list                        List all captured traces
    loupe show <trace-id>             Print one trace step-by-step
    loupe tag <trace> <step> <cat>    Mark a step as a benchmark failure
    loupe untag <trace> <step>        Remove a tag
    loupe annotations <trace>         List tags on one trace
    loupe export [--out FILE]         Bundle annotated failures
    loupe report <trace-id> [--out]   Render a shareable markdown case file
    loupe doctor                      Diagnose install + integrations
    loupe purge [--older-than 7d]     Delete old captured traces (dry-run by default)
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
    kv_table,
    render_padded,
    section,
    spinner,
    stack,
    status_table,
)
from loupe._version import __version__
from loupe.annotation import Annotation, AnnotationStore
from loupe.bench import export_jsonl
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
            section("Quickstart"),
            Text(),
            cmd("loupe init my-agent          # scaffold a real working starter"),
            cmd("cd my-agent && python agent.py 'your first question'"),
            cmd("loupe ui                     # open the forensic dashboard"),
        )
    else:
        plural = "trace" if trace_count == 1 else "traces"
        next_steps = stack(
            section(f"{trace_count} {plural} captured"),
            Text(),
            cmd("loupe ui            # open the forensic dashboard"),
            cmd("loupe list          # compact table of every run"),
            cmd("loupe stats         # aggregate breakdown by framework + failure"),
        )

    render_padded(
        banner(sub, version=__version__),
        Text(),
        next_steps,
        Text(),
        section("Help"),
        Text(),
        hint("loupe doctor          diagnose your install + integrations"),
        hint("loupe --help          full command reference"),
    )


@app.command("start")
def start(
    port: int = typer.Option(7860, "--port", "-p", help="Port for the dashboard"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't auto-open the browser"),
) -> None:
    """Open the dashboard. If you have no traces yet, you'll see the onboarding."""
    render_padded(banner("dashboard", version=__version__))

    home = _default_dir()
    existing = len(list((home / "traces").glob("*.jsonl"))) if (home / "traces").exists() else 0
    if existing > 0:
        console.print(Text("  ✓ ", style=GREEN) + Text(f"{existing} trace(s) captured.", style=INK))
    else:
        console.print(Text("  No traces yet — run an instrumented agent and refresh.", style=DIM))

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
    """List all traces stored locally.

    Uses the DuckDB index when available (millisecond-level for thousands of
    traces). Falls back to a disk walk if the index is missing or broken.
    """
    traces_dir = _default_dir() / "traces"
    if not traces_dir.exists():
        render_padded(banner(version=__version__), _no_traces_hint())
        return

    from rich.box import SIMPLE
    from rich.table import Table

    from loupe._tui import is_narrow
    from loupe.index import default_index

    ann_store = AnnotationStore()

    # Try the index first.
    indexed = default_index().list_traces(limit=100)
    used_index = bool(indexed)

    if used_index:
        rows = [
            {
                "trace_id": r.trace_id,
                "name": r.name,
                "framework": r.framework,
                "duration_ms": (
                    (r.ended_at - r.started_at) * 1000
                    if (r.ended_at is not None and r.started_at is not None)
                    else None
                ),
                "step_count": r.step_count,
                "failed": r.failed,
            }
            for r in indexed
        ]
    else:
        files = sorted(
            traces_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            render_padded(banner(version=__version__), _no_traces_hint())
            return
        rows = []
        for file in files[:100]:
            header = _read_header(file)
            if header is None:
                continue
            steps = sum(1 for _ in file.open()) - 1
            rows.append({
                "trace_id": header["trace_id"],
                "name": header["name"],
                "framework": header.get("framework"),
                "duration_ms": (
                    (header["ended_at"] - header["started_at"]) * 1000
                    if header.get("ended_at") else None
                ),
                "step_count": steps,
                "failed": header.get("metadata", {}).get("failed", False),
            })

    if not rows:
        render_padded(banner(version=__version__), _no_traces_hint())
        return

    narrow = is_narrow()
    table = Table(
        show_header=True,
        header_style=f"dim {DIM}",
        box=SIMPLE,
        padding=(0, 1),
        title=Text("traces", style=f"italic {AMBER}"),
        title_justify="left",
    )
    table.add_column("name", style=INK, no_wrap=False, min_width=18, ratio=3)
    if not narrow:
        table.add_column("trace_id", style=AMBER, no_wrap=True, width=8)
        table.add_column("framework", style=DIM, no_wrap=True, min_width=8, ratio=2)
    table.add_column("duration", justify="right", style=DIM, no_wrap=True, width=8)
    table.add_column("steps", justify="right", no_wrap=True, width=5)
    table.add_column("status", no_wrap=True, width=6)

    for row in rows:
        dur_ms = row["duration_ms"]
        duration = f"{dur_ms:.0f} ms" if isinstance(dur_ms, (int, float)) else "—"
        status = Text("failed", style=RED) if row["failed"] else Text("ok", style=GREEN)
        trace_id_str = str(row["trace_id"])
        name_str = str(row["name"])
        framework_str = str(row["framework"]) if row.get("framework") else "—"
        step_count_str = str(row["step_count"])
        ann_count = len(ann_store.load(trace_id_str))
        name_cell = Text()
        if ann_count > 0:
            name_cell.append("◉ ", style=AMBER)
        name_cell.append(name_str, style=INK)
        if narrow:
            table.add_row(name_cell, duration, step_count_str, status)
        else:
            table.add_row(
                name_cell,
                trace_id_str[:8],
                framework_str,
                duration,
                step_count_str,
                status,
            )

    console.print()
    console.print(table)
    # Subtle index-mode hint at the bottom, dim so it doesn't compete.
    if used_index:
        console.print(Text("  · indexed", style=DIM))
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
        step_line = Text()
        step_line.append(f"  {i:>2}. ", style=DIM)
        step_line.append(f"{step['kind']:>10}", style=kind_style)
        step_line.append(f"  {step['name']}", style=INK)
        if step.get("error"):
            step_line.append(f"\n        {step['error']}", style=RED)
        console.print(step_line)
    console.print()


# ----------------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------------


@app.command("ui")
def ui(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(7860, help="Bind port"),
    auto_port: bool = typer.Option(
        True, "--auto-port/--no-auto-port",
        help="If the chosen port is busy, try the next 9 ports before giving up.",
    ),
) -> None:
    """Launch the local forensic dashboard."""
    _run_ui(host=host, port=port, auto_port=auto_port)


def _run_ui(*, host: str, port: int, auto_port: bool = True) -> None:
    try:
        import uvicorn

        from loupe.ui.server import create_app
    except ImportError:
        console.print(
            Text("  loupe ui needs fastapi + uvicorn.", style=RED) +
            Text("  Install with:  pip install 'loupe[ui]'", style=DIM)
        )
        raise typer.Exit(code=1) from None

    bind_port = _resolve_port(host, port, search=auto_port)
    if bind_port is None:
        return  # _resolve_port printed the error and we want a clean exit

    url_text = Text()
    url_text.append("  ◉ Loupe ", style=AMBER)
    url_text.append(f"http://{host}:{bind_port}", style=f"bold {INK}")
    if bind_port != port:
        url_text.append(f"  (port {port} was busy)", style=DIM)
    console.print(url_text)
    try:
        uvicorn.run(create_app(), host=host, port=bind_port, log_level="warning")
    except KeyboardInterrupt:
        console.print()
        console.print(Text("  Stopped.", style=DIM))


def _resolve_port(host: str, start: int, *, search: bool) -> int | None:
    """Return the first free port in [start, start+9] (or just `start` if not searching).

    Prints a clean error and returns None if nothing's available — caller exits cleanly.
    """
    import socket

    candidates = range(start, start + 10) if search else (start,)
    for candidate in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, candidate))
            except OSError:
                continue
            return candidate

    if search:
        console.print(
            Text(f"  All ports {start}..{start + 9} on {host} are busy.", style=RED)
        )
    else:
        console.print(
            Text(f"  Port {start} on {host} is already in use.", style=RED)
            + Text("  Try a different --port.", style=DIM)
        )
    raise typer.Exit(code=1)


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
def annotations_cmd(trace_id: str) -> None:
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
# Circuit attribution
# ----------------------------------------------------------------------------


@app.command("attribute")
def attribute(
    trace_id: str = typer.Argument(
        "",
        help="Trace id (or prefix). Omit and use --all to attribute every trace.",
    ),
    all_traces: bool = typer.Option(
        False, "--all",
        help="Walk every captured trace; skip steps already attributed unless --force.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Re-run attribution on steps that already have it. Default skips them.",
    ),
    backend: str = typer.Option(
        "mock",
        "--backend",
        help="Attributor backend: 'mock' (default, no deps) or 'sae' "
             "(requires loupe[interp] extra).",
    ),
    model: str | None = typer.Option(
        None, "--model",
        help="Model name for the SAE backend (e.g. gpt2-small).",
    ),
    sae: str | None = typer.Option(
        None, "--sae",
        help="SAE identifier for the SAE backend.",
    ),
    top_k: int = typer.Option(
        8, "--top-k",
        help="Number of top features to keep per step.",
    ),
    only_failing: bool = typer.Option(
        False, "--only-failing",
        help="Skip steps that completed without an error.",
    ),
    annotator: str = typer.Option(
        "loupe-attribute",
        "--annotator",
        help="Annotator id stored alongside the attribution.",
    ),
    explain: bool = typer.Option(
        False, "--explain",
        help="Look up each top feature's interpretation on Neuronpedia. "
             "Adds a description field to every FeatureActivation. Cached "
             "locally so a second --explain run is instant + offline.",
    ),
) -> None:
    """Compute circuit attribution for llm-call steps.

    Two modes:

    * ``loupe attribute <trace>``  — attribute one trace.
    * ``loupe attribute --all``    — attribute every captured trace, skipping
                                     steps that already have an attribution
                                     (use ``--force`` to re-run them).

    Results are stored in each step's annotation under
    ``circuit_attribution`` — alongside any human tags. Existing tags +
    notes are preserved on re-run.

    Backends:
      * ``mock``: deterministic synthetic features, no deps.
      * ``sae``:  real SAE attribution. Requires ``pip install 'loupe[interp]'``
                  and a model/SAE pair.
    """
    from loupe.attribution import attribute_trace, make_attributor

    if all_traces and trace_id:
        console.print(Text("  ✗ pass either <trace> OR --all, not both.", style=RED))
        raise typer.Exit(code=1)

    if not all_traces and not trace_id:
        console.print(Text("  ✗ pass a trace id, or use --all.", style=RED))
        raise typer.Exit(code=1)

    # Resolve the target list of paths exactly once so error paths
    # don't accidentally make N partial passes over the disk.
    if all_traces:
        traces_dir = _default_dir() / "traces"
        target_paths = sorted(traces_dir.glob("*.jsonl")) if traces_dir.exists() else []
        if not target_paths:
            console.print(Text("  No captured traces yet.", style=DIM))
            return
    else:
        single = _find_trace(trace_id)
        if single is None:
            raise typer.Exit(code=1)
        target_paths = [single]

    try:
        attributor = make_attributor(
            backend, model=model, sae=sae, top_k=top_k,
        )
    except (ValueError, ImportError) as exc:
        console.print(Text(f"  ✗ {exc}", style=RED))
        raise typer.Exit(code=1) from None

    store = AnnotationStore()
    total_attributed = 0
    total_skipped = 0
    first_result: tuple[str, Any] | None = None

    label = f"Attributing {len(target_paths)} trace(s) via {attributor.name}"
    with spinner(label):
        for path in target_paths:
            try:
                results = attribute_trace(
                    path, attributor, only_failing=only_failing,
                )
            except NotImplementedError as exc:
                console.print(Text(f"  ✗ {exc}", style=RED))
                raise typer.Exit(code=1) from None
            except Exception as exc:  # noqa: BLE001
                console.print(
                    Text(f"  ✗ {path.stem[:12]} failed: {exc}", style=RED)
                )
                continue

            if explain:
                results = _attach_explanations(results)

            existing = {a.step_id: a for a in store.load(path.stem)}
            for step_id, result in results:
                if step_id in existing:
                    ann = existing[step_id]
                    has_attr = bool(ann.circuit_attribution)
                    if has_attr and not force:
                        total_skipped += 1
                        continue
                    ann.circuit_attribution = result.to_json_dict()  # type: ignore[assignment]
                else:
                    ann = Annotation(
                        trace_id=path.stem,
                        step_id=step_id,
                        failure_category="other",  # type: ignore[arg-type]
                        annotator=annotator,
                        circuit_attribution=result.to_json_dict(),  # type: ignore[arg-type]
                    )
                store.add(ann)
                total_attributed += 1
                if first_result is None:
                    first_result = (step_id, result)

    if total_attributed == 0 and total_skipped == 0:
        console.print(
            Text("  No llm-call steps eligible for attribution.", style=DIM)
        )
        if only_failing:
            console.print(Text("  (try without --only-failing)", style=DIM))
        return

    console.print()
    summary_line = (
        Text("  ✓ ", style=GREEN)
        + Text(f"attributed {total_attributed} step(s)", style=INK)
        + Text(f"  ·  {attributor.name} / {attributor.model}", style=DIM)
    )
    if total_skipped:
        summary_line += Text(f"  ·  {total_skipped} skipped (already attributed)", style=DIM)
    console.print(summary_line)
    # Tiny preview so the user can see something concrete.
    if first_result is not None:
        first_id, first_payload = first_result
        feats = first_payload.top_features[:3]
        if feats:
            console.print()
            console.print(Text(f"  step {first_id[:12]} top features:", style=DIM))
            for f in feats:
                line = (
                    Text("    ", style=DIM)
                    + Text(f"#{f.feature_id:>6}", style=AMBER)
                    + Text(f"  act={f.activation:.3f}", style=INK)
                )
                if f.description:
                    line += Text(f"  {f.description}", style=INK)
                else:
                    line += Text(f"  {f.layer}", style=DIM)
                console.print(line)
    console.print()


def _attach_explanations(
    results: list[tuple[str, Any]],
) -> list[tuple[str, Any]]:
    """Bulk-fetch Neuronpedia explanations for every (feature_id, layer,
    release) tuple across all results, then attach to FeatureActivations.

    Uses one parallel lookup per (hook_name, release) cluster so a 20-step
    attribution doesn't fire 20 sequential round-trips. Best-effort:
    every failure path produces ``description=None`` and the original
    activation magnitude survives intact.
    """
    from dataclasses import replace

    from loupe.attribution import AttributionResult
    from loupe.neuronpedia import explain_many

    # Cluster features by (hook_name, release) so a batched lookup makes
    # sense — most attributions all share the same SAE.
    clusters: dict[tuple[str, str], set[int]] = {}
    for _step_id, r in results:
        if not isinstance(r, AttributionResult):
            continue
        for f in r.top_features:
            clusters.setdefault((f.layer, r.sae), set()).add(f.feature_id)

    explanations: dict[tuple[str, str, int], str | None] = {}
    for (layer, release), feature_ids in clusters.items():
        lookup = explain_many(
            list(feature_ids), hook_name=layer, release=release,
        )
        for fid, desc in lookup.items():
            explanations[(layer, release, fid)] = desc

    new_results: list[tuple[str, Any]] = []
    for step_id, r in results:
        if not isinstance(r, AttributionResult):
            new_results.append((step_id, r))
            continue
        annotated = [
            replace(
                f,
                description=explanations.get((f.layer, r.sae, f.feature_id)),
            )
            for f in r.top_features
        ]
        new_results.append((step_id, replace(r, top_features=annotated)))
    return new_results


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
    html_out: bool = typer.Option(
        False, "--html", help="Render as a standalone single-file HTML viewer"
    ),
) -> None:
    """Render a shareable case file (markdown by default, --html for a viewer)."""
    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)
    if html_out:
        from loupe.report_html import render_trace_html
        text = render_trace_html(path)
    else:
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


# ----------------------------------------------------------------------------
# Diagnostics
# ----------------------------------------------------------------------------


@app.command("doctor")
def doctor(
    smoke: bool = typer.Option(
        False, "--smoke", help="Also run a full end-to-end smoke test in a tmp dir"
    ),
) -> None:
    """Diagnose your install + show wired-up integrations.

    Pass --smoke to additionally execute a tiny lifecycle (capture → save →
    load → validate → tag → untag) so you know everything actually works,
    not just that the packages are importable.
    """
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
    with spinner("Scanning installed integrations"):
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

    if smoke:
        _run_smoke_test()


def _run_smoke_test() -> None:
    """Tiny end-to-end lifecycle in a tmp dir. Prints ✓/✗ per step."""
    import contextlib
    import json as _json
    import shutil
    import tempfile
    import time as _time

    from loupe import record_step
    from loupe import trace as trace_fn
    from loupe.annotation import Annotation, AnnotationStore
    from loupe.store import JSONLStore

    console.print()
    console.print(Text("  Running end-to-end smoke test in a tmp dir…", style=DIM))
    console.print()

    tmp_root = Path(tempfile.mkdtemp(prefix="loupe-smoke-"))
    try:
        traces_root = tmp_root / "traces"
        annotations_root = tmp_root / "annotations"
        store = JSONLStore(root=traces_root)
        ann_store = AnnotationStore(root=annotations_root)
        results: list[tuple[str, bool, str]] = []
        captured: dict[str, str] = {}
        t0 = _time.perf_counter()

        # 1. Capture
        try:
            @trace_fn(name="smoke", framework="smoke", store=store)
            def agent() -> None:
                record_step("thought", "plan")
                s = record_step("error", "fail", error="planned")
                if s:
                    captured["step"] = s.step_id
                raise RuntimeError("planned smoke failure")

            with contextlib.suppress(RuntimeError):
                agent()
            jsonl_files = list(traces_root.glob("*.jsonl"))
            ok = len(jsonl_files) == 1
            results.append(("capture trace", ok, f"{len(jsonl_files)} file(s) written"))
            if not ok:
                raise AssertionError("capture produced no file")
            trace_id = jsonl_files[0].stem
        except Exception as exc:
            results.append(("capture trace", False, str(exc)))
            _print_smoke_results(results, _time.perf_counter() - t0)
            return

        # 2. JSONL parses cleanly
        try:
            lines = [_json.loads(line) for line in jsonl_files[0].read_text().splitlines()]
            parsed_ok = bool(lines) and lines[0].get("_type") == "trace"
            results.append(("parse JSONL", parsed_ok, f"{len(lines)} line(s)"))
        except Exception as exc:
            results.append(("parse JSONL", False, str(exc)))

        # 3. Validate against schema
        try:
            schema_path = _find_schema_file()
            if schema_path is None:
                results.append(("schema validate", False, "schema file not found"))
            else:
                try:
                    import jsonschema  # type: ignore[import-not-found]
                except ImportError:
                    results.append((
                        "schema validate",
                        False,
                        "jsonschema not installed (pip install 'loupe\\[dev]')",
                    ))
                else:
                    schema = _json.loads(schema_path.read_text(encoding="utf-8"))
                    header: dict = {}
                    steps: list[dict] = []
                    for line in jsonl_files[0].open():
                        obj = _json.loads(line)
                        kind = obj.pop("_type", None)
                        if kind == "trace":
                            header = obj
                        elif kind == "step":
                            steps.append(obj)
                    payload = {**header, "steps": steps}
                    jsonschema.validate(instance=payload, schema=schema)
                    results.append(("schema validate", True, "matches v1 contract"))
        except Exception as exc:
            results.append(("schema validate", False, str(exc)))

        # 4. Tag + Untag
        try:
            step_id = captured.get("step") or ""
            ann_store.add(Annotation(
                trace_id=trace_id,
                step_id=step_id,
                failure_category="other",
                notes="smoke",
            ))
            loaded = ann_store.load(trace_id)
            ok = len(loaded) == 1 and ann_store.remove(trace_id, step_id)
            results.append(("tag + untag", ok, "round-trip clean"))
        except Exception as exc:
            results.append(("tag + untag", False, str(exc)))

        elapsed_ms = (_time.perf_counter() - t0) * 1000
        _print_smoke_results(results, elapsed_ms / 1000)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _print_smoke_results(
    rows: list[tuple[str, bool, str]],
    elapsed_s: float,
) -> None:
    """Render smoke-test results table + the final overall status line."""
    rendered = [
        (label, _badge_ready() if ok else _badge_failed(), detail)
        for label, ok, detail in rows
    ]
    console.print(status_table(rendered))
    console.print()
    all_ok = all(ok for _, ok, _ in rows)
    if all_ok:
        console.print(
            Text("  ✓ smoke test passed", style=GREEN)
            + Text(f"  ({elapsed_s * 1000:.0f} ms, {len(rows)} step(s))", style=DIM)
        )
    else:
        failures = sum(1 for _, ok, _ in rows if not ok)
        console.print(Text(f"  ✗ {failures} smoke step(s) failed.", style=RED))
        raise typer.Exit(code=1)
    console.print()


def _badge_failed() -> str:
    return f"[{RED}]✗[/{RED}] fail"


@app.command("diff")
def diff_cmd(
    a: str = typer.Argument(..., help="First trace id (or prefix)"),
    b: str = typer.Argument(..., help="Second trace id (or prefix)"),
) -> None:
    """Side-by-side diff of two captured traces — useful for A/B comparisons."""
    from difflib import SequenceMatcher

    path_a = _find_trace(a)
    if path_a is None:
        raise typer.Exit(code=1)
    path_b = _find_trace(b)
    if path_b is None:
        raise typer.Exit(code=1)

    header_a, steps_a = _load_trace(path_a)
    header_b, steps_b = _load_trace(path_b)
    if header_a is None or header_b is None:
        console.print(Text("  malformed trace", style=RED))
        raise typer.Exit(code=1)

    rows: list[tuple[str, str]] = []
    rows.append(("trace_id", f"{path_a.stem[:12]}   vs   {path_b.stem[:12]}"))
    rows.append((
        "name",
        f"{header_a.get('name', '?')}   vs   {header_b.get('name', '?')}",
    ))
    rows.append((
        "framework",
        f"{header_a.get('framework', '—')}   vs   {header_b.get('framework', '—')}",
    ))
    rows.append(("steps", f"{len(steps_a)}   vs   {len(steps_b)}"))
    dur_a = _duration_ms(header_a)
    dur_b = _duration_ms(header_b)
    if dur_a is not None and dur_b is not None:
        delta = dur_b - dur_a
        sign = "+" if delta >= 0 else ""
        rows.append((
            "duration",
            f"{dur_a:.0f} ms   vs   {dur_b:.0f} ms   ({sign}{delta:.0f} ms)",
        ))
    failed_a = bool(header_a.get("metadata", {}).get("failed"))
    failed_b = bool(header_b.get("metadata", {}).get("failed"))
    rows.append((
        "status",
        ("FAILED" if failed_a else "ok") + "   vs   " + ("FAILED" if failed_b else "ok"),
    ))

    render_padded(banner("trace diff", version=__version__), kv_table(rows))

    names_a = [s["name"] for s in steps_a]
    names_b = [s["name"] for s in steps_b]
    matcher = SequenceMatcher(a=names_a, b=names_b)

    from rich.box import SIMPLE
    from rich.table import Table

    table = Table(
        show_header=True,
        header_style=f"dim {DIM}",
        box=SIMPLE,
        padding=(0, 2),
        title=Text("step alignment", style=f"italic {AMBER}"),
        title_justify="left",
    )
    table.add_column("op", width=8)
    table.add_column(f"{path_a.stem[:8]}", style=INK)
    table.add_column(f"{path_b.stem[:8]}", style=INK)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                table.add_row(
                    Text("=", style=DIM),
                    Text(names_a[i1 + k], style=DIM),
                    Text(names_b[j1 + k], style=DIM),
                )
        elif tag == "replace":
            len_a, len_b = i2 - i1, j2 - j1
            for k in range(max(len_a, len_b)):
                table.add_row(
                    Text("~", style=AMBER),
                    Text(names_a[i1 + k] if k < len_a else "—",
                         style=AMBER if k < len_a else DIM),
                    Text(names_b[j1 + k] if k < len_b else "—",
                         style=AMBER if k < len_b else DIM),
                )
        elif tag == "delete":
            for k in range(i2 - i1):
                table.add_row(
                    Text("-", style=RED),
                    Text(names_a[i1 + k], style=RED),
                    Text("—", style=DIM),
                )
        elif tag == "insert":
            for k in range(j2 - j1):
                table.add_row(
                    Text("+", style=GREEN),
                    Text("—", style=DIM),
                    Text(names_b[j1 + k], style=GREEN),
                )

    console.print(table)
    console.print()


def _load_trace(path: Path) -> tuple[dict | None, list[dict]]:
    import json as _json

    header: dict | None = None
    steps: list[dict] = []
    for line in path.open():
        obj = _json.loads(line)
        kind = obj.pop("_type", None)
        if kind == "trace":
            header = obj
        elif kind == "step":
            steps.append(obj)
    return header, steps


def _duration_ms(header: dict) -> float | None:
    started = header.get("started_at")
    ended = header.get("ended_at")
    if started is None or ended is None:
        return None
    return max(0.0, (ended - started) * 1000)


@app.command("stats")
def stats() -> None:
    """Aggregate counts + breakdowns across every captured trace.

    Uses the DuckDB index when available for O(1)-ish aggregates; falls
    back to walking JSONL files on disk if the index isn't healthy.
    """
    import json as _json
    from collections import Counter

    from loupe.index import default_index

    traces_dir = _default_dir() / "traces"
    ann_store = AnnotationStore()
    indexed_stats = default_index().stats()

    framework_counter: Counter[str] = Counter()
    failure_count = 0
    step_count = 0
    total_traces = 0
    median_dur: float | None = None

    if indexed_stats is not None and indexed_stats["trace_count"] > 0:
        total_traces = indexed_stats["trace_count"]
        failure_count = indexed_stats["failed_count"]
        step_count = indexed_stats["step_count"]
        median_dur = indexed_stats["median_duration_ms"]
        framework_counter.update(indexed_stats["by_framework"])
    else:
        files = sorted(traces_dir.glob("*.jsonl")) if traces_dir.exists() else []
        if not files:
            render_padded(banner(version=__version__), _no_traces_hint())
            return
        durations_ms: list[float] = []
        for path in files:
            try:
                with path.open() as f:
                    first = _json.loads(next(f))
            except (StopIteration, _json.JSONDecodeError):
                continue
            if first.get("_type") != "trace":
                continue
            framework_counter[first.get("framework") or "(none)"] += 1
            if first.get("metadata", {}).get("failed"):
                failure_count += 1
            step_count += max(0, sum(1 for _ in path.open()) - 1)
            if first.get("ended_at") and first.get("started_at"):
                durations_ms.append(
                    (first["ended_at"] - first["started_at"]) * 1000
                )
        total_traces = len(files)
        if durations_ms:
            median_dur = sorted(durations_ms)[len(durations_ms) // 2]

    # Annotation category histogram
    cat_counter: Counter[str] = Counter()
    annotation_total = 0
    for trace_id, items in ann_store.all().items():
        del trace_id  # not needed
        annotation_total += len(items)
        for ann in items:
            cat_counter[ann.failure_category] += 1

    from rich.box import SIMPLE
    from rich.table import Table

    summary = kv_table([
        ("traces", str(total_traces)),
        ("failed", f"{failure_count}  ({failure_count / total_traces:.0%})"
         if total_traces else "0"),
        ("steps", str(step_count)),
        ("tagged", str(annotation_total)),
        ("median dur", f"{median_dur:.0f} ms" if median_dur is not None else "—"),
    ])

    framework_table = Table(
        show_header=False, show_edge=False, box=SIMPLE, padding=(0, 2),
        title=Text("by framework", style=f"italic {AMBER}"),
        title_justify="left",
    )
    framework_table.add_column("name", style=INK)
    framework_table.add_column("count", justify="right", style=DIM)
    for fw, n in framework_counter.most_common():
        framework_table.add_row(fw, str(n))

    category_table = Table(
        show_header=False, show_edge=False, box=SIMPLE, padding=(0, 2),
        title=Text("failure categories", style=f"italic {AMBER}"),
        title_justify="left",
    )
    category_table.add_column("category", style=INK)
    category_table.add_column("count", justify="right", style=DIM)
    if cat_counter:
        for cat, n in cat_counter.most_common():
            category_table.add_row(cat, str(n))
    else:
        category_table.add_row("(none yet)", "—")

    render_padded(
        banner("captured-state overview", version=__version__),
        summary,
        Text(),
        framework_table,
        Text(),
        category_table,
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


@app.command("purge")
def purge(
    older_than: str = typer.Option(
        ...,
        "--older-than",
        help="Age threshold. Use suffixes: '7d', '24h', '30m', '3600s'.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Actually delete. Without this flag, purge is a dry-run.",
    ),
    keep_tagged: bool = typer.Option(
        False,
        "--keep-tagged",
        help="Skip traces that have any annotations — protect your benchmark set.",
    ),
) -> None:
    """Delete captured traces older than a given age. Dry-run by default."""
    try:
        max_age = _parse_duration(older_than)
    except ValueError as exc:
        console.print(Text(f"  {exc}", style=RED))
        raise typer.Exit(code=1) from None

    import time as _time

    traces_dir = _default_dir() / "traces"
    annotations_dir = _default_dir() / "annotations"
    if not traces_dir.exists():
        console.print(Text("  no traces to purge.", style=DIM))
        return

    now = _time.time()
    all_traces = sorted(traces_dir.glob("*.jsonl"))
    candidates: list[tuple[Path, float]] = []
    for path in all_traces:
        age = now - path.stat().st_mtime
        if age >= max_age:
            candidates.append((path, age))

    if keep_tagged:
        store = AnnotationStore(annotations_dir)
        candidates = [
            (p, age) for (p, age) in candidates
            if not store.load(p.stem)
        ]

    if not candidates:
        console.print(Text(f"  no traces older than {older_than}.", style=DIM))
        return

    verb = "would delete" if not yes else "deleting"
    console.print()
    console.print(
        Text(f"  {verb} {len(candidates)} trace(s) older than ", style=INK)
        + Text(older_than, style=AMBER)
        + (Text("  (--keep-tagged: annotated traces skipped)", style=DIM)
           if keep_tagged else Text())
    )
    console.print()
    for path, age in candidates:
        console.print(
            Text(f"    {path.stem[:12]}", style=INK)
            + Text(f"   {_fmt_age(age)} old", style=DIM)
        )

    if not yes:
        console.print()
        console.print(hint("loupe purge --older-than " + older_than + " --yes    actually delete"))
        if keep_tagged:
            console.print(hint("(re-run without --keep-tagged to include annotated traces)"))
        console.print()
        return

    import contextlib

    from loupe.index import default_index
    idx = default_index()

    deleted = 0
    for path, _age in candidates:
        try:
            path.unlink()
        except OSError as exc:
            console.print(Text(f"  ✗ could not delete {path.stem[:12]}: {exc}", style=RED))
            continue
        deleted += 1
        # Best-effort: also remove the sidecar annotation + lock files,
        # and drop the corresponding row from the DuckDB index.
        for suffix in (".json", ".lock"):
            sidecar = annotations_dir / f"{path.stem}{suffix}"
            with contextlib.suppress(OSError):
                sidecar.unlink(missing_ok=True)
        with contextlib.suppress(Exception):
            idx.remove_trace(path.stem)

    console.print()
    console.print(Text(f"  ✓ deleted {deleted} trace(s).", style=GREEN))
    console.print()


def _parse_duration(text: str) -> float:
    """Parse '7d' / '24h' / '30m' / '3600s' / '90' → seconds. Raises ValueError."""
    if not text:
        raise ValueError("empty duration")
    text = text.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text[-1] in units:
        try:
            value = float(text[:-1])
        except ValueError as e:
            raise ValueError(f"invalid duration {text!r}") from e
        seconds = value * units[text[-1]]
    else:
        try:
            seconds = float(text)
        except ValueError as e:
            raise ValueError(
                f"invalid duration {text!r} — use '7d', '24h', '30m', '3600s'"
            ) from e
    if seconds < 0:
        raise ValueError("duration must be non-negative")
    return seconds


def _fmt_age(seconds: float) -> str:
    """Render a coarse human-readable age. Picks the largest unit that yields ≥1."""
    if seconds >= 86400:
        return f"{seconds / 86400:.1f}d"
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    if seconds >= 60:
        return f"{seconds / 60:.1f}m"
    return f"{seconds:.0f}s"


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


@app.command("cluster")
def cluster(
    category: str = typer.Option(
        "", "--category", "-c",
        help="Restrict to one failure category (hallucination, loop, …). "
             "Omit to cluster across every annotated step.",
    ),
    top_k: int = typer.Option(
        15, "--top-k",
        help="How many top features to show (sorted by frequency).",
    ),
) -> None:
    """Find the features that fire across many tagged failures.

    This is the analytical primitive of the LoupeBench research workflow:
    once you have N hand-tagged failures with circuit attribution, this
    command shows which SAE features recur across them — and which are
    *distinctive* to one category versus all others.

    Output:
      - frequency table: how often each feature fires in the filtered set
      - distinctiveness: features over-represented in the chosen
        category relative to every other category, with a simple
        log-ratio score. Computed only when --category is set.
    """
    import math
    from collections import Counter

    store = AnnotationStore()
    all_annotations = store.all()

    # Flatten into a list of (category, top_feature_ids) per annotation
    # that actually has attribution.
    rows: list[tuple[str, list[int]]] = []
    for _trace_id, items in all_annotations.items():
        for ann in items:
            attr = ann.circuit_attribution or {}
            feats = attr.get("top_features", []) if isinstance(attr, dict) else []
            if not feats:
                continue
            ids = [
                int(f["feature_id"]) for f in feats
                if isinstance(f, dict) and "feature_id" in f
            ]
            if not ids:
                continue
            rows.append((ann.failure_category, ids))

    if not rows:
        console.print(
            Text("  No annotated steps with circuit attribution yet.", style=DIM)
        )
        console.print(
            hint("loupe attribute --all    attribute every captured trace first")
        )
        return

    # Filter
    if category:
        in_cat = [r for r in rows if r[0] == category]
        out_cat = [r for r in rows if r[0] != category]
    else:
        in_cat = rows
        out_cat = []

    if not in_cat:
        console.print(
            Text(f"  No annotated steps in category {category!r}.", style=RED)
        )
        return

    # Count features inside the filtered set.
    in_counter: Counter[int] = Counter()
    for _cat, ids in in_cat:
        for fid in ids:
            in_counter[fid] += 1

    # Frequency table
    from rich.box import SIMPLE
    from rich.table import Table

    title = (
        f"cluster · {category}  ({len(in_cat)} annotation(s))"
        if category else f"cluster · all categories  ({len(in_cat)} annotation(s))"
    )
    freq_table = Table(
        show_header=True,
        header_style=f"dim {DIM}",
        box=SIMPLE,
        padding=(0, 2),
        title=Text(title, style=f"italic {AMBER}"),
        title_justify="left",
    )
    freq_table.add_column("feature_id", style=AMBER, no_wrap=True, justify="right")
    freq_table.add_column("hits", style=INK, no_wrap=True, justify="right")
    freq_table.add_column("share", style=DIM, no_wrap=True, justify="right")

    for fid, hits in in_counter.most_common(top_k):
        share = hits / len(in_cat)
        freq_table.add_row(f"#{fid}", str(hits), f"{share:.0%}")

    # Distinctiveness: features over-represented in this category vs others.
    distinct_table: Table | None = None
    if category and out_cat:
        out_counter: Counter[int] = Counter()
        for _cat, ids in out_cat:
            for fid in ids:
                out_counter[fid] += 1

        # Smoothed log-ratio. +1 smoothing avoids log(0) and handles the
        # "feature appears in zero out-of-category annotations" case.
        scores: list[tuple[int, float, int, int]] = []
        for fid, hits in in_counter.items():
            in_rate = (hits + 1) / (len(in_cat) + 1)
            out_rate = (out_counter.get(fid, 0) + 1) / (len(out_cat) + 1)
            score = math.log(in_rate / out_rate)
            scores.append((fid, score, hits, out_counter.get(fid, 0)))
        scores.sort(key=lambda x: x[1], reverse=True)

        distinct_table = Table(
            show_header=True,
            header_style=f"dim {DIM}",
            box=SIMPLE,
            padding=(0, 2),
            title=Text(
                f"distinctive features  (vs {len(out_cat)} other-category annotation(s))",
                style=f"italic {AMBER}",
            ),
            title_justify="left",
        )
        distinct_table.add_column("feature_id", style=AMBER, no_wrap=True, justify="right")
        distinct_table.add_column("in", style=INK, no_wrap=True, justify="right")
        distinct_table.add_column("out", style=DIM, no_wrap=True, justify="right")
        distinct_table.add_column("score", style=GREEN, no_wrap=True, justify="right")
        for fid, score, hits_in, hits_out in scores[:top_k]:
            if score <= 0:
                # Stop printing once we cross over into "not distinctive"
                break
            distinct_table.add_row(
                f"#{fid}", str(hits_in), str(hits_out), f"{score:+.2f}",
            )

    blocks: list[object] = [
        banner("failure-feature cluster", version=__version__),
        Text(),
        freq_table,
    ]
    if distinct_table is not None:
        blocks.extend([Text(), distinct_table])

    render_padded(*blocks)


@app.command("version")
def version() -> None:
    """Print Loupe version."""
    console.print(Text("loupe ", style=DIM) + Text(__version__, style=AMBER))


# ----------------------------------------------------------------------------
# `loupe index` subcommands — manage the DuckDB query index
# ----------------------------------------------------------------------------

index_app = typer.Typer(
    name="index",
    help="Manage the DuckDB query index over your captured traces.",
    no_args_is_help=True,
)
app.add_typer(index_app, name="index")


@index_app.command("info")
def index_info() -> None:
    """Show the index file path, size, and row counts."""
    from loupe.index import default_index

    info = default_index().info()
    render_padded(
        banner("index health", version=__version__),
        kv_table([
            ("path",           info["db_path"]),
            ("exists",         "yes" if info["exists"] else "no"),
            ("size",           f"{info['size_bytes']:,} bytes"),
            ("schema version", str(info["schema_version"])),
            ("traces indexed", str(info.get("trace_count", 0))),
            ("steps indexed",  str(info.get("step_count", 0))),
        ] + ([("error", info["error"])] if info.get("error") else [])),
    )


@index_app.command("rebuild")
def index_rebuild() -> None:
    """Drop the index and re-walk every JSONL file on disk.

    Safe to run at any time — the JSONL files are the source of truth.
    Use this if `loupe doctor` reports an index inconsistency, or after
    moving traces between machines.
    """
    from loupe.index import default_index

    with spinner("Rebuilding index from JSONL files"):
        indexed, skipped = default_index().rebuild()

    console.print()
    console.print(
        Text("  ✓ ", style=GREEN)
        + Text(f"{indexed} trace(s) indexed", style=INK)
        + (Text(f"  ·  {skipped} skipped", style=DIM) if skipped else Text())
    )
    console.print()


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
        hint("loupe init my-agent    scaffold an instrumented starter project"),
        hint("loupe ui               open the dashboard (will show onboarding)"),
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
