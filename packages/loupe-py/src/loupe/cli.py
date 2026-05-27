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
    rich_markup_mode="rich",
)

# Help-panel groups — used by Typer's `rich_help_panel` to group commands
# in ``loupe --help`` by purpose instead of alphabetically. Keeps the
# 21-command surface scannable.
_GROUP_GET_STARTED = "Get started"
_GROUP_USE         = "Use it (no code required)"
_GROUP_INSPECT     = "Inspect captured runs"
_GROUP_ANALYZE     = "Analyze + benchmark"
_GROUP_INFRA       = "Infrastructure"


# ----------------------------------------------------------------------------
# Welcome / start
# ----------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Smart router: pick the right action based on the user's state.

    First run (no config + no traces)  → run `loupe setup` automatically
    Configured but no traces           → show welcome with `loupe ask` hint
    Has traces                          → show the welcome with next-steps
    """
    if ctx.invoked_subcommand is not None:
        return
    _smart_route()


def _smart_route() -> None:
    """Decide what `loupe` (no args) should do for this user, this moment.

    Goal: collapse the first 30 minutes of friction to ~90 seconds.

    Interactivity rule: only auto-launch the setup wizard when we KNOW we
    have a real terminal. In CI / piped / CliRunner contexts, fall back
    to the static welcome screen so we don't hang on an unread stdin.
    """
    from loupe.config import Config

    home = _default_dir()
    has_traces = (home / "traces").exists() and any(
        (home / "traces").glob("*.jsonl")
    )
    cfg = Config.load()
    has_provider = bool(cfg.configured_providers())

    # First-run case: no config + no traces → if interactive, guide them
    # through setup. Otherwise show the static welcome with `loupe setup`
    # at the top so it's still discoverable.
    is_interactive = (
        sys.stdin.isatty() and sys.stdout.isatty()
        and not os.environ.get("LOUPE_DISABLE_AUTOSETUP")
    )
    if not has_provider and not has_traces and is_interactive:
        from loupe._onboard import looks_like_project
        # In a real project folder, run the on-your-code onboarding: it
        # configures a provider, captures a trace from THEIR agent, and
        # opens the dashboard. Outside a project (e.g. the home dir),
        # there's nothing to instrument — fall back to the plain wizard.
        if looks_like_project(Path.cwd()):
            _run_onboard()
        else:
            console.print()
            console.print(Text("  Welcome to Loupe.", style=f"bold {AMBER}"))
            console.print(
                Text("  Let's get you set up — takes about 90 seconds.", style=DIM)
            )
            console.print()
            _run_setup()
        return

    _show_welcome()


def _show_welcome() -> None:
    """One-screen pitch + next action.

    The job of this screen, for a first-time vibe coder, is:

      1. Tell them what Loupe IS, in one line they can quote back.
      2. Tell them what Loupe will CAPTURE for them, with the
         vocabulary they'll see later (trace, step) defined inline.
      3. Give them exactly ONE thing to type next.

    For a returning user with traces, the pitch shrinks and the
    next-steps grow.
    """
    from loupe._setup_providers import detect_from_env

    home = _default_dir()
    trace_count = (
        len(list((home / "traces").glob("*.jsonl")))
        if (home / "traces").exists() else 0
    )

    sub = "Forensic observability for AI agents — open-source, local-first."

    # --- the pitch (only when they have nothing yet) -----------------
    pitch_block: object | None = None
    detected = detect_from_env()
    if trace_count == 0:
        pitch_lines: list[object] = [
            Text("  A magnifying glass for your AI agent.", style=INK),
            Text(),
            Text(
                "  Loupe captures every LLM call your code makes — model,",
                style=DIM,
            ),
            Text(
                "  prompt, response, latency, tokens, errors — so when your",
                style=DIM,
            ),
            Text(
                "  agent acts weird, you can replay the exact failure and",
                style=DIM,
            ),
            Text("  find the cause.", style=DIM),
        ]
        if detected:
            pitch_lines.append(Text())
            pitch_lines.append(
                Text("  ✓ ", style=GREEN)
                + Text(f"Detected your {detected.env_keys[0]}", style=INK)
                + Text(
                    f" — Loupe will capture every {detected.display} call.",
                    style=DIM,
                )
            )
        pitch_block = stack(*pitch_lines)

    # --- next steps -------------------------------------------------
    if trace_count == 0:
        next_steps = stack(
            section("Capture your first trace"),
            Text(),
            cmd("loupe init my-agent       # scaffold a working starter (any provider)"),
            cmd("loupe ask 'hello'         # ask any provider, capture the call"),
            Text(),
            Text(
                "  Then open ", style=DIM,
            )
            + Text("loupe ui", style=AMBER)
            + Text(" to see what just happened.", style=DIM),
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

    blocks: list[object] = [banner(sub, version=__version__), Text()]
    if pitch_block is not None:
        blocks.extend([pitch_block, Text()])
    blocks.extend([
        next_steps, Text(),
        section("Help"), Text(),
        hint("loupe explain loupe   what is Loupe? (trace, step, annotation, …)"),
        hint("loupe doctor          diagnose your install + integrations"),
        hint("loupe --help          full command reference"),
    ])
    render_padded(*blocks)


@app.command("setup", rich_help_panel=_GROUP_GET_STARTED)
def setup(
    provider: str = typer.Option(
        "", "--provider", "-p",
        help="Provider to configure. Omit to be asked interactively.",
    ),
    api_key: str = typer.Option(
        "", "--api-key", "-k",
        help="Pre-supplied API key (skips the interactive paste step).",
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser",
        help="Don't auto-open the browser to the provider's key-creation page.",
    ),
    reset: bool = typer.Option(
        False, "--reset",
        help="Wipe ~/.loupe/config.toml and run the wizard from scratch. "
             "Autopatch turns OFF until you re-configure.",
    ),
    remove: str = typer.Option(
        "", "--remove",
        help="Drop one provider's saved key (e.g. --remove openai). "
             "Other providers and the default selection are preserved.",
    ),
) -> None:
    """Configure / reconfigure / remove an LLM provider.

    Six providers are supported (run with no flags to see the picker):
    Gemini, Anthropic, OpenAI, Mistral, Groq, DeepSeek.

    Common flows:

      # Fresh setup or add a new provider
      loupe setup
      loupe setup --provider anthropic

      # Replace a saved key (interactive)
      loupe setup --provider gemini

      # Remove one provider's key, keep the rest
      loupe setup --remove openai

      # Wipe everything and start over
      loupe setup --reset

      # Scripted (CI / IaC)
      loupe setup --provider gemini --api-key "$GEMINI_KEY" --no-browser
    """
    # --remove short-circuits before anything else: a destructive op that
    # doesn't need the wizard.
    if remove:
        _run_setup_remove(remove)
        return

    # --reset wipes the config file before running the wizard. The new
    # wizard run treats the user as a first-timer (no `already configured`
    # short-circuit).
    if reset:
        _run_setup_reset()
        # If the user passed --reset alone, fall through and run the
        # wizard so they end in a working state. If they combined
        # --reset with --provider, that provider gets configured next.

    _run_setup(
        forced_provider=provider or None,
        forced_key=api_key or None,
        open_browser=not no_browser,
    )


def _run_setup_reset() -> None:
    """Delete ``~/.loupe/config.toml`` so the next ``loupe setup`` run
    starts fresh and autopatch turns OFF until reconfigured.

    Traces and annotations are *never* touched — only the config file.
    """
    from loupe.config import config_path

    path = config_path()
    if path.exists():
        try:
            path.unlink()
        except OSError as exc:
            console.print(
                Text(f"  ✗ could not delete {path}: {exc}", style=RED)
            )
            raise typer.Exit(code=1) from None
        console.print(
            Text("  ✓ ", style=GREEN)
            + Text("deleted ", style=INK)
            + Text(str(path), style=AMBER)
        )
        console.print(
            Text("    Autopatch is OFF until you finish setup.", style=DIM)
        )
    else:
        console.print(
            Text("  ◉ ", style=AMBER)
            + Text("no config to reset — running fresh setup.", style=INK)
        )
    console.print()


def _run_setup_remove(provider: str) -> None:
    """Remove a single provider's saved API key from the config file."""
    from loupe._setup_providers import SETUP_PROVIDERS
    from loupe.config import Config

    label = provider.strip().lower()
    known = {p.label for p in SETUP_PROVIDERS}
    if label not in known:
        joined = ", ".join(sorted(known))
        console.print(
            Text(f"  ✗ unknown provider {provider!r}; pick one of: {joined}.",
                 style=RED)
        )
        raise typer.Exit(code=1)

    cfg = Config.load()
    if label not in cfg.providers or not cfg.providers[label].api_key:
        console.print(
            Text(f"  ◉ {label} ", style=AMBER)
            + Text("isn't configured — nothing to remove.", style=INK)
        )
        return

    new_providers = {k: v for k, v in cfg.providers.items() if k != label}
    new_default = cfg.default_provider
    # If they just dropped their default provider, point default at the
    # next remaining one (alphabetically) so future `loupe ask` calls
    # don't break.
    if new_default == label:
        new_default = (
            sorted(new_providers)[0] if new_providers
            else SETUP_PROVIDERS[0].label    # back to gemini
        )

    next_cfg = Config(
        default_provider=new_default,
        default_model=cfg.default_model,
        providers=new_providers,
        attribution_backend=cfg.attribution_backend,
        index_disabled=cfg.index_disabled,
        check_for_updates=cfg.check_for_updates,
        _path=cfg._path,
    )
    saved_at = next_cfg.save()
    console.print(
        Text("  ✓ ", style=GREEN)
        + Text(f"removed {label} ", style=INK)
        + Text(f"({saved_at})", style=DIM)
    )
    if not new_providers:
        console.print(
            Text("    No providers left — autopatch will still be ON "
                 "(config.toml exists), but ", style=DIM)
            + Text("loupe ask", style=AMBER)
            + Text(" won't work until you configure one.", style=DIM)
        )
        console.print(hint("loupe setup    add a provider back"))
    else:
        console.print(
            Text("    Remaining: ", style=DIM)
            + Text(", ".join(sorted(new_providers)), style=AMBER)
        )
    console.print()


def _run_setup(
    *,
    forced_provider: str | None = None,
    forced_key: str | None = None,
    open_browser: bool = True,
) -> None:
    """Implementation of the setup wizard. Pure function-style for testing."""
    from loupe._setup_providers import SETUP_PROVIDERS
    from loupe._setup_providers import get as _get_provider
    from loupe.config import Config

    cfg = Config.load()
    already = cfg.configured_providers()

    if already and forced_provider is None:
        # Already configured → render the full system overview via the
        # `loupe status` formatter so users see EVERY setting (retention,
        # encryption, redaction, activity) instead of just provider keys.
        # Then list the setup-specific actions at the bottom.
        console.print()
        console.print(
            Text("  ✓ ", style=GREEN)
            + Text("Loupe is already set up — ", style=INK)
            + Text(", ".join(already), style=AMBER)
            + Text(" configured.", style=INK)
        )
        console.print()
        import contextlib as _ctx
        with _ctx.suppress(typer.Exit):
            status()
        console.print(section("setup actions"))
        console.print()
        console.print(hint("loupe setup --provider X    add or replace a provider"))
        console.print(hint("loupe setup --remove X      drop one provider's key"))
        console.print(hint("loupe setup --reset         wipe everything + start over"))
        console.print()
        return

    # Pick a provider.
    provider = forced_provider or _prompt_provider()
    info = _get_provider(provider)
    if info is None:
        known = ", ".join(p.label for p in SETUP_PROVIDERS)
        console.print(
            Text(f"  ✗ unknown provider {provider!r}; pick one of: {known}.",
                 style=RED)
        )
        raise typer.Exit(code=1)

    # Open the browser (best-effort, never blocks).
    if open_browser:
        import contextlib
        import webbrowser
        console.print(
            Text("  → opening ", style=DIM)
            + Text(info.key_url, style=AMBER)
            + Text(" in your browser…", style=DIM)
        )
        with contextlib.suppress(Exception):
            webbrowser.open(info.key_url, new=1)

    # Prompt for the key.
    if forced_key is not None:
        api_key = forced_key
    else:
        console.print()
        console.print(
            Text("  Paste your ", style=DIM)
            + Text(info.display, style=AMBER)
            + Text(f" key here  (format: {info.key_prefix}):", style=DIM)
        )
        # Use getpass so the key doesn't echo if the user has a savvy shell.
        # Fall back to plain input if getpass complains (no tty).
        try:
            import getpass
            api_key = getpass.getpass("    key › ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print(Text("\n  ✗ setup cancelled.", style=RED))
            raise typer.Exit(code=1) from None
        except Exception:  # noqa: BLE001 — fallback when no tty
            api_key = input("    key › ").strip()

    if not api_key:
        console.print(Text("  ✗ no key provided.", style=RED))
        raise typer.Exit(code=1)

    # Persist + test.
    new_cfg = cfg.set_provider_key(info.label, api_key).with_default(
        provider=info.label,
        model=info.default_model,
    )
    saved_at = new_cfg.save()

    console.print()
    console.print(
        Text("  ✓ ", style=GREEN)
        + Text("saved to ", style=INK)
        + Text(str(saved_at), style=AMBER)
    )

    # Ping the API to confirm the key works.
    ok, detail = _ping_provider(info.label, api_key, info.default_model)
    if ok:
        console.print(
            Text("  ✓ ", style=GREEN)
            + Text(f"verified — {detail}", style=INK)
        )
    else:
        console.print(
            Text("  ⚠ ", style=AMBER)
            + Text(f"saved, but the test call failed: {detail}", style=INK)
        )
        console.print(
            Text("    The key is still saved — re-run with a corrected one if needed.",
                 style=DIM)
        )

    # Inform the user that auto-capture is now active. As of v0.0.59
    # writing the config.toml flips the .pth-loaded autopatch ON for
    # every future Python script on this machine — no extra env var,
    # no shell rc edit.
    console.print()
    console.print(
        Text("  ✓ ", style=GREEN)
        + Text("zero-code auto-capture is now ", style=INK)
        + Text("ON", style=f"bold {GREEN}")
        + Text(" for every Python script you run.", style=INK)
    )
    console.print(
        Text("    No imports needed — `python my_agent.py` captures automatically.",
             style=DIM)
    )
    console.print(
        Text("    Opt out anytime with ", style=DIM)
        + Text("LOUPE_AUTOPATCH=0", style=AMBER)
        + Text(".", style=DIM)
    )

    console.print()
    console.print(section("Next"))
    console.print()
    console.print(hint("python my_agent.py          captures automatically"))
    console.print(hint("loupe ask 'hello'           one captured call"))
    console.print(hint("loupe ui                    open the dashboard"))
    console.print(hint("loupe explain autopatch     how zero-code capture works"))
    console.print()


def _prompt_provider() -> str:
    """Ask the user which provider to configure.

    Defaults to the first entry in the registry (Gemini — free tier, the
    fastest path to a first trace).
    """
    from loupe._setup_providers import SETUP_PROVIDERS

    console.print(section("Pick a provider"))
    console.print()
    width = max(len(p.label) for p in SETUP_PROVIDERS) + 2
    for i, p in enumerate(SETUP_PROVIDERS, start=1):
        console.print(
            Text(f"    {i}. {p.label:<{width}}", style=AMBER)
            + Text(p.tagline, style=DIM)
        )
    console.print()
    try:
        prompt = f"    your pick [1-{len(SETUP_PROVIDERS)}, default 1] › "
        choice = input(prompt).strip() or "1"
    except (EOFError, KeyboardInterrupt):
        console.print(Text("\n  ✗ setup cancelled.", style=RED))
        raise typer.Exit(code=1) from None
    # Accept either a number or the bare label so muscle memory works.
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(SETUP_PROVIDERS):
            return SETUP_PROVIDERS[idx].label
    return choice.lower()


def _ping_provider(provider: str, api_key: str, model: str) -> tuple[bool, str]:
    """Send a tiny request to confirm the key works. Returns (ok, message)."""
    from loupe._setup_providers import ping
    return ping(provider=provider, api_key=api_key, model=model)


def _ensure_provider_or_setup(intent: str) -> None:
    """If no provider is configured, flow seamlessly into ``loupe setup``.

    Used by ``try`` / ``ask`` / ``chat`` so a first-time user never hits a
    dead-end "no provider configured" error. They typed ``loupe ask "…"``
    — we'd rather guide them through setup and then continue, not exit.

    ``intent`` is the human-readable name of what they were trying to do
    (``"ask a question"``, ``"start a chat"``, ``"try the demo"``), used
    only for the friendly nudge line.

    In non-TTY contexts (CI, piped) we keep the actionable error path so
    scripts don't hang on unread stdin.
    """
    from loupe.config import Config

    if Config.load().configured_providers():
        return

    is_tty = (
        sys.stdin.isatty() and sys.stdout.isatty()
        and not os.environ.get("LOUPE_DISABLE_AUTOSETUP")
    )

    if not is_tty:
        console.print(Text("  ✗ no provider configured yet.", style=RED))
        console.print(hint("loupe setup    configure your first provider"))
        raise typer.Exit(code=1)

    # Interactive: run setup inline, then return so the calling command
    # can carry on with its original intent.
    console.print()
    console.print(
        Text("  ◉ ", style=AMBER)
        + Text(f"Loupe needs a provider before it can {intent}.", style=INK)
    )
    console.print(
        Text("    Walking you through setup now — about 90 seconds.", style=DIM)
    )
    console.print()
    _run_setup()

    # After setup, verify we actually got a configured provider. If the
    # user bailed mid-flow, exit cleanly without trying to continue.
    if not Config.load().configured_providers():
        raise typer.Exit(code=1)
    console.print()
    console.print(
        Text("  ↩ resuming ", style=DIM)
        + Text(intent, style=INK)
        + Text("…", style=DIM)
    )
    console.print()


def _default_model_for(provider: str) -> str:
    """Default model name for a provider. Falls back to Gemini Flash."""
    from loupe._setup_providers import SETUP_PROVIDERS, get
    info = get(provider)
    return info.default_model if info else SETUP_PROVIDERS[0].default_model


def _invoke_with_history(
    provider: str, api_key: str, model: str,
    history: list[dict[str, str]],
) -> str:
    """Multi-turn invocation routed through the setup-provider registry."""
    from loupe._setup_providers import invoke
    return invoke(provider=provider, api_key=api_key, model=model, history=history)


# ----------------------------------------------------------------------------
# Phase 2: zero-code usage paths — ask / chat / run
# ----------------------------------------------------------------------------


@app.command("ask", rich_help_panel=_GROUP_USE)
def ask(
    question: list[str] = typer.Argument(
        None,
        help="Your question. Quote it if it contains shell metacharacters.",
    ),
    provider: str = typer.Option(
        "", "--provider",
        help="Provider to use. Default: your configured default.",
    ),
    model: str = typer.Option(
        "", "--model",
        help="Model id. Default: the provider's default.",
    ),
) -> None:
    """One captured LLM call — like ChatGPT in the terminal, with a trace.

    ::

        loupe ask "Reply in one sentence: what is observability?"

    The trace lands in ``~/.loupe/traces/`` automatically — inspect it
    with ``loupe ui`` or ``loupe show <id>``.
    """
    prompt = " ".join(question or []).strip()
    if not prompt:
        console.print()
        console.print(
            Text("  ◉ ", style=AMBER)
            + Text("what do you want to ask?", style=INK)
        )
        console.print(
            Text("    Pass your question as the argument:", style=DIM)
        )
        console.print()
        console.print(cmd('loupe ask "what is AI agent observability?"'))
        console.print(cmd('loupe ask "summarize this in one sentence: ..."'))
        console.print()
        console.print(hint("loupe chat            multi-turn REPL instead of one-shot"))
        console.print(hint("loupe explain ask     deeper explanation"))
        console.print()
        raise typer.Exit(code=1)
    _run_single_capture(prompt, provider_override=provider or None,
                        model_override=model or None, name="loupe-ask")


def _run_single_capture(
    prompt: str,
    *,
    provider_override: str | None = None,
    model_override: str | None = None,
    name: str = "loupe-ask",
) -> None:
    """Shared implementation behind ``loupe ask`` + ``loupe try`` ergonomics.

    Looks up the provider + key from Config, prints the prompt + model,
    runs the call inside an @trace block, and prints the answer + the
    one-line trace summary.
    """
    from loupe import record_step
    from loupe import trace as trace_decorator
    from loupe.config import Config
    from loupe.integrations import patch_all

    _ensure_provider_or_setup("ask a question")
    cfg = Config.load()
    providers = cfg.configured_providers()

    provider = provider_override or (
        cfg.default_provider if cfg.default_provider in providers else providers[0]
    )
    if provider not in providers:
        console.print(
            Text(
                f"  ✗ provider {provider!r} not configured. "
                f"Have: {', '.join(providers)}.",
                style=RED,
            )
        )
        console.print(hint(f"loupe setup --provider {provider}    add this provider"))
        raise typer.Exit(code=1)

    chosen_model = model_override or _default_model_for(provider)
    api_key = cfg.api_key_for(provider)
    assert api_key   # configured_providers() guaranteed this

    patch_all()

    @trace_decorator(name=name, framework=provider)
    def _run() -> str:
        record_step(
            "plan", "compose prompt",
            outputs={"q": prompt[:200], "provider": provider, "model": chosen_model},
        )
        text = _invoke_with_history(
            provider, api_key, chosen_model,
            [{"role": "user", "content": prompt}],
        )
        record_step("final", "got reply", outputs={"text": text[:300]})
        return text

    console.print()
    console.print(
        Text("  ◉ ", style=AMBER)
        + Text(f"{provider}:{chosen_model}", style=INK)
    )
    console.print()

    answer: str = ""
    try:
        with spinner("thinking"):
            answer = _run()
    except Exception as exc:  # noqa: BLE001 — surface the API error verbatim
        console.print(Text(f"  ✗ {exc}", style=RED))
        console.print(hint("loupe setup    re-check your provider key"))
        raise typer.Exit(code=1) from None

    assert isinstance(answer, str)
    console.print(Text("  " + answer.strip(), style=INK))
    console.print()


@app.command("chat", rich_help_panel=_GROUP_USE)
def chat(
    provider: str = typer.Option(
        "", "--provider",
        help="Provider to use. Default: your configured default.",
    ),
    model: str = typer.Option(
        "", "--model",
        help="Model id. Default: the provider's default.",
    ),
) -> None:
    """Interactive REPL — multi-turn conversation, every turn captured.

    Slash commands inside the REPL:

      /tag <category> [notes]   tag the last turn as a benchmark failure
      /show                     print the last captured trace
      /dashboard                open the dashboard in your browser
      /clear                    reset conversation history
      /help                     show this list
      /quit                     exit (or just press Enter on an empty line)

    The conversation history is held in memory and sent on each turn so
    follow-up questions work as expected. Each turn lands as its own
    Loupe trace in ``~/.loupe/traces/``.
    """
    from loupe import record_step
    from loupe import trace as trace_decorator
    from loupe.config import Config
    from loupe.integrations import patch_all

    _ensure_provider_or_setup("start a chat")
    cfg = Config.load()
    providers = cfg.configured_providers()

    chosen_provider = provider or (
        cfg.default_provider if cfg.default_provider in providers else providers[0]
    )
    if chosen_provider not in providers:
        console.print(
            Text(
                f"  ✗ provider {chosen_provider!r} not configured. "
                f"Have: {', '.join(providers)}.",
                style=RED,
            )
        )
        raise typer.Exit(code=1)

    chosen_model = model or _default_model_for(chosen_provider)
    api_key = cfg.api_key_for(chosen_provider)
    assert api_key

    patch_all()

    console.print()
    console.print(
        Text("  ◉ ", style=AMBER)
        + Text(f"chat ({chosen_provider}:{chosen_model})", style=INK)
        + Text("  ·  ", style=DIM)
        + Text("/help for commands · blank line to quit", style=DIM)
    )
    console.print()

    # Each turn is traced via a single decorator-bound function defined
    # ONCE (outside the loop) — keeps the closure clean and lets ruff
    # see it's not a loop-variable capture.
    @trace_decorator(name="loupe-chat-turn", framework=chosen_provider)
    def _capture_turn(
        history: list[dict[str, str]], user_text: str,
    ) -> str:
        history.append({"role": "user", "content": user_text})
        record_step(
            "plan", "user turn",
            outputs={"q": user_text[:200], "history_len": len(history)},
        )
        assert api_key
        text = _invoke_with_history(
            chosen_provider, api_key, chosen_model, history,
        )
        history.append({"role": "assistant", "content": text})
        record_step("final", "got reply", outputs={"text": text[:300]})
        return text

    history: list[dict[str, str]] = []
    last_trace_id: str | None = None
    last_step_id: str | None = None

    while True:
        try:
            user_text = input("  you ▸ ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if not user_text:
            break

        if user_text.startswith("/"):
            cont = _handle_chat_slash(
                user_text, history, last_trace_id, last_step_id,
            )
            if cont == "quit":
                break
            if cont == "cleared":
                history = []
                last_trace_id = None
                last_step_id = None
            continue

        answer: str
        try:
            answer = _capture_turn(history, user_text)
        except Exception as exc:  # noqa: BLE001
            console.print(Text(f"  ✗ {exc}", style=RED))
            # Remove the user turn we appended inside _capture_turn so
            # re-asking doesn't double-include it.
            if history and history[-1]["role"] == "user":
                history.pop()
            continue

        # The most recent trace file is ours. Cache its id for slash cmds.
        traces_dir = _default_dir() / "traces"
        if traces_dir.exists():
            files = sorted(
                traces_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if files:
                last_trace_id = files[0].stem
                # Find the step_id of the assistant's reply (last llm-call).
                try:
                    from loupe.store import load_trace_split
                    _h, _steps, _ = load_trace_split(files[0])
                    for obj in reversed(_steps):
                        if obj.get("kind") == "llm-call":
                            last_step_id = obj["step_id"]
                            break
                except Exception:  # noqa: BLE001 — best-effort
                    last_step_id = None

        # Render the reply.
        console.print()
        console.print(Text(f"  {chosen_provider} ▸ ", style=AMBER))
        # Wrap long answers naturally; Rich handles this.
        console.print(Text("    " + answer.strip().replace("\n", "\n    "), style=INK))
        if last_trace_id:
            console.print()
            console.print(
                Text(f"      ✓ trace {last_trace_id[:12]}", style=DIM)
                + Text("  ·  /tag <category> to mark this turn", style=DIM)
            )
        console.print()

    console.print(Text("  bye. all traces saved to ~/.loupe/traces/", style=DIM))


def _handle_chat_slash(
    raw: str,
    history: list[dict[str, str]],
    last_trace_id: str | None,
    last_step_id: str | None,
) -> str:
    """Process one ``/foo bar`` slash command. Returns a sentinel:

    - ``"continue"`` — keep the REPL going (the default).
    - ``"quit"`` — break out of the REPL.
    - ``"cleared"`` — caller should reset its local history + id state.
    """
    from loupe.annotation import Annotation, AnnotationStore

    parts = raw[1:].split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    if cmd in ("quit", "exit", "q"):
        return "quit"

    if cmd in ("help", "?", "h"):
        console.print()
        help_lines = [
            "/tag <category> [notes]   tag the last turn",
            "/show                     print the last captured trace",
            "/dashboard                open the dashboard",
            "/clear                    reset conversation history",
            "/quit                     exit (or blank line)",
        ]
        for line in help_lines:
            console.print(Text(f"    {line}", style=DIM))
        console.print()
        return "continue"

    if cmd == "clear":
        console.print(Text("  ✓ conversation cleared", style=GREEN))
        return "cleared"

    if cmd == "dashboard":
        import contextlib
        import webbrowser
        url = "http://127.0.0.1:7860"
        with contextlib.suppress(Exception):
            webbrowser.open(url, new=1)
        console.print(
            Text("  → opened ", style=DIM)
            + Text(url, style=AMBER)
            + Text("  (start it with `loupe ui` if not running)", style=DIM)
        )
        return "continue"

    if cmd == "show":
        if not last_trace_id:
            console.print(Text("  no turns yet — say something first.", style=DIM))
            return "continue"
        path = _find_trace(last_trace_id[:12])
        if path is None:
            return "continue"
        from loupe.store import iter_jsonl_records
        for obj in iter_jsonl_records(path):
            if obj.get("_type") == "step":
                console.print(
                    Text("    ", style=DIM)
                    + Text(f"{obj['kind']:>10}", style=DIM)
                    + Text(f"  {obj['name']}", style=INK)
                )
        return "continue"

    if cmd == "tag":
        if not last_trace_id or not last_step_id:
            console.print(Text("  no captured turn yet to tag.", style=DIM))
            return "continue"
        if not rest:
            console.print(
                Text("  usage: /tag <category> [notes]", style=DIM)
                + Text("  e.g. /tag hallucination invented a fake fact", style=DIM)
            )
            return "continue"
        cat_parts = rest.split(maxsplit=1)
        category = cat_parts[0]
        notes = cat_parts[1] if len(cat_parts) > 1 else ""
        try:
            AnnotationStore().add(Annotation(
                trace_id=last_trace_id,
                step_id=last_step_id,
                failure_category=category,  # type: ignore[arg-type]
                notes=notes,
                annotator="loupe-chat",
            ))
        except Exception as exc:  # noqa: BLE001
            console.print(Text(f"  ✗ tag failed: {exc}", style=RED))
            return "continue"
        console.print(
            Text("  ✓ tagged ", style=GREEN)
            + Text(f"{last_trace_id[:12]}/{last_step_id[:8]}", style=AMBER)
            + Text(f" as {category}", style=INK)
        )
        return "continue"

    console.print(
        Text(f"  unknown /{cmd}.  ", style=RED)
        + Text("/help for the list.", style=DIM)
    )
    return "continue"


@app.command(
    "run",
    rich_help_panel=_GROUP_USE,
    # Forward every flag past the subcommand to the user's command — Loupe
    # consumes nothing. So `loupe run sh -c "exit 42"` runs sh -c, not
    # `loupe run -c=...`.
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
    },
)
def run_script(
    args: list[str] = typer.Argument(
        None,
        help="Command + its arguments. Example: loupe run my_agent.py 'hello' "
             "or loupe run node my-agent.js or loupe run sh -c 'exit 0'",
    ),
) -> None:
    """Run ANY command with every LLM call auto-captured.

    Detects the runtime from the command and activates capture the right
    way for each:

      • Python script (``*.py``)  → in-process: patch_all() + @trace
      • Anything else             → subprocess + LOUPE_AUTOPATCH=1
                                    + NODE_OPTIONS=--require if it's Node

    Examples::

        loupe run my_agent.py "What is observability?"   # Python in-process
        loupe run node my-agent.js                        # Node, autopatched
        loupe run tsx scripts/eval.ts                     # TS via tsx
        loupe run go run main.go                          # Go, captured via proxy hint
        loupe run -- python -m my.package --flag value    # explicit `--` separator

    The script's own argv is preserved as if you had run the command
    directly. Exit code propagates.
    """
    if not args:
        _run_show_usage()
        raise typer.Exit(code=1)

    # Strip a leading `--` if the user passed one for shell-quoting clarity.
    if args[0] == "--":
        args = args[1:]
        if not args:
            _run_show_usage()
            raise typer.Exit(code=1)

    # Decide whether this is a direct Python script or a generic command.
    # Heuristic: first arg ends in `.py` → user clearly meant a Python
    # script; if the file is missing, error out with a friendly hint rather
    # than fall through to subprocess mode (which would 127 with a binary
    # lookup miss).
    first = args[0]
    if first.endswith(".py"):
        if not Path(first).exists():
            console.print(Text(f"  ✗ no such file: {first}", style=RED))
            console.print(hint("loupe init <name>     scaffold a starter project"))
            raise typer.Exit(code=1)
        _run_python_script_inproc(args)
        return
    _run_subprocess_command(args)


def _run_show_usage() -> None:
    console.print()
    console.print(
        Text("  ◉ ", style=AMBER)
        + Text("what should Loupe run?", style=INK)
    )
    console.print(
        Text("    Loupe activates capture before your command starts, so", style=DIM)
    )
    console.print(
        Text("    every LLM call gets recorded — no source-code changes.", style=DIM)
    )
    console.print()
    console.print(cmd("loupe run my_agent.py 'your question'    # Python"))
    console.print(cmd("loupe run node my-agent.js               # Node / TypeScript"))
    console.print(cmd("loupe run go run main.go                 # Go (proxy mode)"))
    console.print()
    console.print(hint("loupe init my-agent    scaffold a starter project first"))
    console.print(hint("loupe explain run      deeper explanation"))
    console.print()


def _run_python_script_inproc(args: list[str]) -> None:
    """Execute a Python script in-process so we get the richest possible
    capture (full @trace context, every step nested under one parent trace).
    """
    import runpy

    from loupe import record_step
    from loupe import trace as trace_decorator
    from loupe.integrations import patch_all

    script_path = Path(args[0])
    name = script_path.stem

    original_argv = sys.argv[:]
    sys.argv = list(args)

    patch_all()

    console.print()
    console.print(
        Text("  ◉ ", style=AMBER)
        + Text(f"running {script_path.name}", style=INK)
        + Text("  ·  every LLM call captured", style=DIM)
    )
    console.print()

    @trace_decorator(name=f"run:{name}", framework="loupe-run")
    def _execute() -> None:
        record_step(
            "plan", "loupe run",
            outputs={"script": str(script_path), "argv": list(args)},
        )
        runpy.run_path(str(script_path), run_name="__main__")

    try:
        _execute()
    except SystemExit as exc:
        sys.argv = original_argv
        raise typer.Exit(code=exc.code if isinstance(exc.code, int) else 0) from None
    except Exception as exc:  # noqa: BLE001
        console.print(Text(f"  ✗ script raised: {exc}", style=RED))
        console.print(
            Text("    (the failure was still captured — open  loupe ui  to inspect)",
                 style=DIM)
        )
        sys.argv = original_argv
        raise typer.Exit(code=1) from None
    finally:
        sys.argv = original_argv

    console.print()
    console.print(Text("  ✓ done — trace captured", style=GREEN))
    console.print(hint("loupe ui                   open the dashboard"))
    console.print(hint("loupe list                 see this and every other run"))
    console.print()


def _run_subprocess_command(args: list[str]) -> None:
    """Execute ANY command in a subprocess with the right capture env.

    Strategy by command:

      • node / npm / npx / pnpm / yarn / bun / tsx / ts-node
            → set NODE_OPTIONS to require @loupe/sdk/autopatch (if installed)
            → set LOUPE_AUTOPATCH=1
      • python / python3 / pytest / uvicorn / fastapi
            → set LOUPE_AUTOPATCH=1 so the .pth hook in the subprocess
              auto-activates
      • anything else
            → set LOUPE_AUTOPATCH=1 (cheap no-op for non-Python),
              plus suggest using `loupe proxy` for true cross-runtime
              capture (printed once before exec)

    Exit code mirrors the child.
    """
    import shutil
    import subprocess

    cmd_name = Path(args[0]).name.lower()
    env = os.environ.copy()
    # Tell every Python/Node child that capture is intended. The .pth /
    # NODE_OPTIONS hooks check this and activate accordingly.
    env["LOUPE_AUTOPATCH"] = "1"

    node_runtimes = {"node", "npm", "npx", "pnpm", "yarn", "bun", "tsx", "ts-node"}
    if cmd_name in node_runtimes:
        # Find the Node autopatch entry. If @loupe/sdk isn't installed in
        # node_modules we can't pre-require it; print a one-liner and
        # continue (LLM calls will only be captured if the user uses
        # `loupe proxy` separately).
        require_arg = _resolve_node_autopatch(Path.cwd())
        if require_arg:
            existing = env.get("NODE_OPTIONS", "")
            env["NODE_OPTIONS"] = (
                f"{existing} --require {require_arg}".strip()
            )
        else:
            console.print(
                Text("  ◉ tip: ", style=AMBER)
                + Text("`@loupe/sdk` isn't in node_modules; ", style=DIM)
                + Text("install it or use ", style=DIM)
                + Text("loupe proxy", style=AMBER)
                + Text(" for cross-language capture.", style=DIM)
            )

    # Locate the binary on PATH for nicer errors than execvp's default.
    binary = shutil.which(args[0])
    if binary is None:
        console.print(Text(f"  ✗ command not found on PATH: {args[0]}", style=RED))
        console.print(hint("loupe proxy --provider <name>    for non-Python agents"))
        raise typer.Exit(code=127)

    console.print()
    console.print(
        Text("  ◉ ", style=AMBER)
        + Text(f"running {' '.join(args)}", style=INK)
        + Text("  ·  capture armed (LOUPE_AUTOPATCH=1)", style=DIM)
    )
    console.print()

    proc = subprocess.run([binary, *args[1:]], env=env, check=False)

    console.print()
    if proc.returncode == 0:
        console.print(Text("  ✓ done — child exited cleanly", style=GREEN))
    else:
        console.print(
            Text(f"  ✗ child exited {proc.returncode} — trace still recorded",
                 style=AMBER)
        )
    console.print(hint("loupe ui            open the dashboard"))
    console.print(hint("loupe list          see this and every other run"))
    console.print()
    raise typer.Exit(code=proc.returncode)


def _resolve_node_autopatch(cwd: Path) -> str | None:
    """Find the on-disk path of ``@loupe/sdk/autopatch`` for use with
    ``NODE_OPTIONS=--require``. Walks up from ``cwd`` looking for a
    ``node_modules/@loupe/sdk`` install.
    """
    cur: Path | None = cwd.resolve()
    while cur is not None:
        candidate = cur / "node_modules" / "@loupe" / "sdk" / "dist" / "autopatch.cjs"
        if candidate.exists():
            return str(candidate)
        cur = cur.parent if cur != cur.parent else None
    return None


_PROXY_PROVIDER_HINTS: dict[str, tuple[str, str]] = {
    # provider → (env var the client uses to find a base URL, example value)
    "anthropic": ("ANTHROPIC_BASE_URL", "http://127.0.0.1:{port}"),
    "openai":    ("OPENAI_BASE_URL",    "http://127.0.0.1:{port}/v1"),
    "gemini":    ("GEMINI_BASE_URL",    "http://127.0.0.1:{port}"),
    "groq":      ("GROQ_BASE_URL",      "http://127.0.0.1:{port}/openai/v1"),
    "mistral":   ("MISTRAL_BASE_URL",   "http://127.0.0.1:{port}"),
    "openrouter":("OPENROUTER_BASE_URL","http://127.0.0.1:{port}/api/v1"),
}


@app.command("proxy", rich_help_panel=_GROUP_USE)
def proxy(
    port: int = typer.Option(7878, "--port", "-p", help="Port to bind on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    provider: str = typer.Option(
        "", "--provider",
        help="Pin every request to one provider. If unset, the proxy "
             "infers from the inbound Host header + path.",
    ),
    upstream: str = typer.Option(
        "", "--upstream",
        help="Override the upstream URL. Useful for self-hosted gateways "
             "or LiteLLM. Example: --upstream https://my-litellm.example.com",
    ),
    auto_port: bool = typer.Option(
        True, "--auto-port/--no-auto-port",
        help="If the chosen port is busy, try the next 9 ports.",
    ),
    tail: bool = typer.Option(
        True, "--tail/--quiet",
        help="Print every captured call as a one-line summary. --quiet suppresses.",
    ),
) -> None:
    """Universal HTTP capture — any agent, any language, zero code.

    Examples:

      # Capture every Anthropic call from any client (Python, Node, Go, curl):
      loupe proxy --provider anthropic
      set -x ANTHROPIC_BASE_URL http://127.0.0.1:7878
      python my_agent.py

      # Auto-detect: send to /v1/messages → Anthropic, /v1/chat → OpenAI, etc.
      loupe proxy
      set -x ANTHROPIC_BASE_URL http://127.0.0.1:7878
      set -x OPENAI_BASE_URL    http://127.0.0.1:7878/v1
    """
    try:
        from loupe.proxy import run as _run_proxy
    except ImportError:
        console.print(
            Text("  loupe proxy needs fastapi + uvicorn + httpx.\n", style=RED)
            + Text("  Reinstall with:  pip install --upgrade loupe", style=DIM)
        )
        raise typer.Exit(code=1) from None

    bind_port = _resolve_port(host, port, search=auto_port)
    if bind_port is None:
        return

    provider_label = provider.strip().lower() or None
    upstream_label = upstream.strip() or None

    # Header — short, scannable, every hint actionable.
    render_padded(banner("proxy", version=__version__))

    rows: list[tuple[str, str]] = [
        ("listening on", f"http://{host}:{bind_port}"),
        ("provider",     provider_label or "auto-detect from path/host"),
        ("tail",         "on" if tail else "off"),
    ]
    if upstream_label:
        rows.append(("upstream", upstream_label))
    console.print(kv_table(rows))
    console.print()

    # Per-provider client setup hint, derived from the pinned provider if any.
    if provider_label and provider_label in _PROXY_PROVIDER_HINTS:
        env, template = _PROXY_PROVIDER_HINTS[provider_label]
        console.print(section("point your client at the proxy"))
        console.print()
        console.print(hint(f"set -x {env} {template.format(port=bind_port)}    # fish"))
        console.print(hint(f"export {env}={template.format(port=bind_port)}   # bash / zsh"))
        console.print()
    else:
        console.print(section("auto-detect mode — set whichever you use"))
        console.print()
        for label, (env, template) in _PROXY_PROVIDER_HINTS.items():
            console.print(hint(f"{env:24s} {template.format(port=bind_port)}    # {label}"))
        console.print()

    if tail:
        console.print(section("live capture · one line per request"))
        console.print()

    console.print(Text("  Press Ctrl-C to stop.", style=DIM))
    console.print()

    try:
        _run_proxy(
            host=host,
            port=bind_port,
            forced_provider=provider_label,
            upstream_override=upstream_label,
            tail=_proxy_tail_printer if tail else None,
        )
    except KeyboardInterrupt:
        console.print()
        console.print(Text("  Stopped.", style=DIM))


def _proxy_tail_printer(step: object, trace_id: str) -> None:
    """One-line live printout of a captured proxy request.

    Renders something like::

        14:22:09  ●  anthropic:claude-haiku-4-5     200  342ms   ↑12 ↓48   ab12cd34

    Status dot is colored (green / amber / red). Token + latency columns
    are right-aligned to a fixed width so the eye locks onto provider/model
    + status across many lines.
    """
    import time as _time

    # `step` is a `loupe.trace.Step` but we keep the annotation loose so
    # the proxy module doesn't pull a hard cli-side import.
    name      = getattr(step, "name", "?")
    outputs   = getattr(step, "outputs", {}) or {}
    status    = outputs.get("status")
    in_tok    = outputs.get("input_tokens")
    out_tok   = outputs.get("output_tokens")
    duration  = getattr(step, "duration_ms", None) or 0.0
    error     = getattr(step, "error", None)
    rate_lim  = outputs.get("rate_limited")

    if error or (isinstance(status, int) and status >= 500):
        dot_style = RED
    elif rate_lim or (isinstance(status, int) and 400 <= status < 500):
        dot_style = AMBER
    else:
        dot_style = GREEN

    ts = _time.strftime("%H:%M:%S")
    tokens = ""
    if in_tok is not None or out_tok is not None:
        in_str = str(in_tok) if in_tok is not None else "·"
        out_str = str(out_tok) if out_tok is not None else "·"
        tokens = f"↑{in_str} ↓{out_str}"
    latency = f"{int(duration):>4d}ms"
    status_txt = f"{status}" if status is not None else "ERR"

    line = Text()
    line.append("  ")
    line.append(ts, style=DIM)
    line.append("  ")
    line.append("●", style=dot_style)
    line.append("  ")
    line.append(f"{name:<38s}", style=INK)
    line.append(f"  {status_txt:>4s}", style=DIM if dot_style == GREEN else dot_style)
    line.append(f"  {latency}", style=DIM)
    if tokens:
        line.append(f"   {tokens:>12s}", style=DIM)
    line.append(f"   {trace_id[:8]}", style=DIM)
    console.print(line)


# ----------------------------------------------------------------------------
# Trace listing & inspection
# ----------------------------------------------------------------------------


@app.command("list", rich_help_panel=_GROUP_INSPECT)
def list_traces(
    as_json: bool = typer.Option(
        False, "--json",
        help="Output the trace list as a JSON array. Pipeable + scriptable.",
    ),
) -> None:
    """List all traces stored locally.

    Uses the DuckDB index when available (millisecond-level for thousands of
    traces). Falls back to a disk walk if the index is missing or broken.

    With ``--json`` the output is a single JSON array of trace summaries,
    one object per trace. Designed to be piped into ``jq``, used by CI
    gates, or processed by other tools — no Rich formatting, no banner.
    """
    traces_dir = _default_dir() / "traces"
    if not traces_dir.exists():
        if as_json:
            typer.echo("[]")
            return
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
        if as_json:
            typer.echo("[]")
            return
        render_padded(banner(version=__version__), _no_traces_hint())
        return

    if as_json:
        # Augment each row with annotation_count so CI/jq pipelines can
        # filter by "has tags" without a second API call. id stays full-length
        # in JSON output — truncation is a presentation concern, not data.
        out_rows = []
        for row in rows:
            ann_count = len(ann_store.load(str(row["trace_id"])))
            out_rows.append({
                "trace_id":         row["trace_id"],
                "name":             row["name"],
                "framework":        row["framework"],
                "duration_ms":      row["duration_ms"],
                "step_count":       row["step_count"],
                "failed":           bool(row["failed"]),
                "annotation_count": ann_count,
            })
        typer.echo(json.dumps(out_rows, indent=2))
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


@app.command("show", rich_help_panel=_GROUP_INSPECT)
def show_trace(
    trace_id: str = typer.Argument(
        "",
        help="Trace id (or prefix). Omit to see usage + a list of recent traces.",
    ),
    as_json: bool = typer.Option(
        False, "--json",
        help="Output the full trace (header + every step + annotations) "
             "as a JSON object. Pipeable into jq.",
    ),
) -> None:
    """Print the full step-by-step content of one trace.

    With ``--json`` the output is the raw header + steps + annotations
    structure — the same shape the dashboard's
    ``GET /api/traces/{id}`` returns. Designed for scripting + CI.
    """
    if not trace_id:
        _missing_trace_id_hint("show")
        raise typer.Exit(code=1)

    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)

    header, steps, skipped = _load_trace_with_warning(path)
    if skipped and not as_json:
        _warn_skipped_lines(path, skipped)

    if header is None:
        if as_json:
            typer.echo(json.dumps({"error": "malformed trace"}))
        else:
            console.print(Text("  ✗ malformed trace (no header line)", style=RED))
            console.print(hint("loupe doctor --fix      quarantine this file"))
            console.print(hint("loupe list              every captured trace"))
        raise typer.Exit(code=1)

    if as_json:
        from dataclasses import asdict as _asdict
        anns = AnnotationStore().load(header["trace_id"])
        payload = {
            **header,
            "steps": steps,
            "annotations": [_asdict(a) for a in anns],
        }
        typer.echo(json.dumps(payload, indent=2))
        return

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

        # Surface the actual content — the prompt the model saw and the
        # reply it gave. This IS the forensic payload; without it `show`
        # is just a list of step names. Indented + dimmed so the step
        # structure still reads at a glance.
        prompt = _extract_prompt_preview(step.get("inputs") or {})
        if prompt:
            _print_show_block("prompt", prompt)
        reply = _extract_reply_preview(step.get("outputs") or {})
        if reply:
            _print_show_block("reply", reply)
        toks = _extract_token_summary(step.get("outputs") or {})
        if toks:
            console.print(Text(f"        ↳ {toks}", style=DIM))
    console.print()


def _print_show_block(label: str, body: str, *, limit: int = 600) -> None:
    """Print one labelled, indented content block under a step."""
    body = body.strip()
    if not body:
        return
    if len(body) > limit:
        body = body[:limit].rstrip() + " …"
    console.print(Text(f"        {label}", style=f"dim {AMBER}"))
    for line in body.splitlines() or [body]:
        console.print(Text("          " + line, style=DIM))


def _extract_prompt_preview(inputs: dict) -> str:
    """Best-effort single-string prompt from an llm-call step's inputs.

    Handles OpenAI/Anthropic ``messages`` and Gemini ``contents``, plus
    the plain ``prompt`` / ``contents`` string shapes. Returns "" when
    there's nothing prompt-like (e.g. a tool-call or thought step)."""
    msgs = inputs.get("messages")
    if isinstance(msgs, list) and msgs:
        bits: list[str] = []
        for m in msgs:
            if isinstance(m, dict):
                role = m.get("role", "")
                content = m.get("content")
                text = content if isinstance(content, str) else _flatten_content(content)
                if text:
                    bits.append(f"{role}: {text}" if role else text)
        if bits:
            return "\n".join(bits)
    contents = inputs.get("contents")
    if isinstance(contents, str):
        return contents
    if isinstance(contents, list):
        flat = _flatten_content(contents)
        if flat:
            return flat
    for key in ("prompt", "input", "question", "q"):
        v = inputs.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _flatten_content(content: object) -> str:
    """Pull text out of Gemini parts / Anthropic content-block lists."""
    if isinstance(content, str):
        return content
    out: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    out.append(item["text"])
                elif isinstance(item.get("parts"), list):
                    out.append(_flatten_content(item["parts"]))
    return " ".join(p for p in out if p)


def _extract_reply_preview(outputs: dict) -> str:
    """The model's reply text from an llm-call step's outputs."""
    for key in ("text", "output", "reply", "completion"):
        v = outputs.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _extract_token_summary(outputs: dict) -> str:
    """Compact "in 12 · out 8 tokens" line, or "" if no token counts."""
    in_tok = outputs.get("input_tokens")
    out_tok = outputs.get("output_tokens")
    parts = []
    if isinstance(in_tok, int):
        parts.append(f"in {in_tok}")
    if isinstance(out_tok, int):
        parts.append(f"out {out_tok}")
    return (" · ".join(parts) + " tokens") if parts else ""


# ----------------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------------


@app.command("ui", rich_help_panel=_GROUP_INSPECT)
def ui(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(7860, "--port", "-p", help="Bind port"),
    auto_port: bool = typer.Option(
        True, "--auto-port/--no-auto-port",
        help="If the chosen port is busy, try the next 9 ports.",
    ),
    open_browser: bool = typer.Option(
        True, "--open-browser/--no-browser",
        help="Auto-open the dashboard in your browser. Default: on.",
    ),
) -> None:
    """Open the local forensic dashboard.

    Auto-opens your browser. With no traces yet, the dashboard renders the
    onboarding card. Pass ``--no-browser`` for headless / remote use.
    """
    _run_ui(
        host=host, port=port,
        auto_port=auto_port, open_browser=open_browser,
    )


def _run_ui(
    *,
    host: str,
    port: int,
    auto_port: bool = True,
    open_browser: bool = False,
) -> None:
    try:
        import uvicorn

        from loupe.ui.server import create_app
    except ImportError:
        console.print(
            Text("  loupe ui needs fastapi + uvicorn.\n", style=RED) +
            Text("  Reinstall with:  pip install --upgrade loupe", style=DIM)
        )
        raise typer.Exit(code=1) from None

    bind_port = _resolve_port(host, port, search=auto_port)
    if bind_port is None:
        return  # _resolve_port printed the error already

    home = _default_dir()
    traces_dir = home / "traces"
    n_traces = (
        sum(1 for _ in traces_dir.glob("*.jsonl"))
        if traces_dir.exists() else 0
    )

    render_padded(banner("dashboard", version=__version__))
    if n_traces:
        console.print(
            Text("  ✓ ", style=GREEN)
            + Text(f"{n_traces} trace(s) captured.", style=INK)
        )
    else:
        console.print(
            Text("  No traces yet — capture one and the dashboard auto-refreshes.",
                 style=DIM)
        )

    url = f"http://{host}:{bind_port}"
    console.print(Text("  Dashboard:  ", style=DIM) + Text(url, style=AMBER))
    if bind_port != port:
        console.print(Text(f"              (port {port} was busy)", style=DIM))
    console.print()

    if open_browser and _should_open_browser():
        import contextlib
        import webbrowser
        with contextlib.suppress(Exception):
            webbrowser.open(url, new=1)
        console.print(Text(f"  Opening {url} in your browser…", style=DIM))

    console.print(Text("  Press Ctrl-C to stop.", style=DIM))
    console.print()
    try:
        uvicorn.run(create_app(), host=host, port=bind_port, log_level="warning")
    except KeyboardInterrupt:
        console.print()
        console.print(Text("  Stopped.", style=DIM))


def _should_open_browser() -> bool:
    """Auto-open guards.

    Don't try to open a browser when:
      - stdout isn't a TTY (CI, piped, captured by tests)
      - we're on Linux/BSD with no DISPLAY (headless SSH session)
      - LOUPE_DISABLE_BROWSER is set (escape hatch for power users)

    On macOS / Windows, the absence of DISPLAY is normal — the
    `open` / `start` shells handle browser launch fine, so we allow it.
    """
    if os.environ.get("LOUPE_DISABLE_BROWSER"):
        return False
    if not sys.stdout.isatty():
        return False
    if sys.platform.startswith(("darwin", "win")):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


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


@app.command("tag", rich_help_panel=_GROUP_ANALYZE)
def tag(
    trace_id: str = typer.Argument(
        "",
        help="Trace id (or prefix) the step lives on.",
    ),
    step_id: str = typer.Argument(
        "",
        help="Step id (or prefix) within that trace.",
    ),
    category: str = typer.Argument(
        "",
        help="Failure category, e.g. unguarded-delete, loop, hallucination",
    ),
    notes: str = typer.Option("", "--notes", "-n", help="Free-text root-cause notes"),
    mitigation: str = typer.Option("", "--mitigation", "-m", help="What fixed it"),
    severity: str = typer.Option("medium", "--severity", "-s", help="low|medium|high|critical"),
    tags: list[str] = typer.Option(None, "--tag", "-t", help="Extra free-form tags"),
) -> None:
    """Mark a step as a benchmark-worthy failure."""
    if not trace_id or not step_id or not category:
        _missing_trace_id_hint(
            "tag",
            extra_examples=[
                "loupe tag <trace> <step> hallucination --notes 'invented citation'",
                "loupe explain tag                       category list + workflow",
            ],
        )
        raise typer.Exit(code=1)

    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)
    full_trace_id = path.stem

    from loupe.store import iter_jsonl_records
    step_match = None
    for obj in iter_jsonl_records(path):
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


@app.command("untag", rich_help_panel=_GROUP_ANALYZE)
def untag(
    trace_id: str = typer.Argument("", help="Trace id (or prefix)."),
    step_id: str = typer.Argument("", help="Step id (or prefix)."),
) -> None:
    """Remove a tag on a step."""
    if not trace_id or not step_id:
        _missing_trace_id_hint(
            "untag",
            extra_examples=["loupe untag <trace> <step>     drop the failure tag"],
        )
        raise typer.Exit(code=1)
    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)
    full_trace_id = path.stem
    full_step = step_id
    from loupe.store import iter_jsonl_records
    for obj in iter_jsonl_records(path):
        if obj.get("_type") == "step" and obj.get("step_id", "").startswith(step_id):
            full_step = obj["step_id"]
            break
    removed = AnnotationStore().remove(full_trace_id, full_step)
    if removed:
        console.print(Text(f"  ✓ untagged {full_trace_id[:12]}/{full_step}", style=GREEN))
    else:
        console.print(Text(f"  no tag found for {full_trace_id[:12]}/{full_step}", style=DIM))


@app.command("annotations", rich_help_panel=_GROUP_INSPECT)
def annotations_cmd(
    trace_id: str = typer.Argument(
        "",
        help="Trace id (or prefix). Omit to list annotations across EVERY trace.",
    ),
) -> None:
    """List annotations — on one trace, or across every trace by default.

    Examples::

        loupe annotations              # every annotation, every trace
        loupe annotations abc123       # just the trace starting with "abc123"
    """
    from rich.box import SIMPLE
    from rich.table import Table

    store = AnnotationStore()

    if trace_id:
        # Single-trace mode (the legacy behaviour).
        path = _find_trace(trace_id)
        if path is None:
            raise typer.Exit(code=1)
        items = store.load(path.stem)
        if not items:
            console.print(Text("  No annotations on this trace.", style=DIM))
            console.print(hint(f"loupe tag {path.stem[:12]} <step-id> <category>"))
            return
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
            table.add_row(
                a.step_id[:12], a.failure_category, a.severity, a.notes or "—",
            )
        console.print()
        console.print(table)
        console.print()
        return

    # All-traces mode: walk ~/.loupe/annotations/*.json directly.
    all_rows: list[tuple[str, str, str, str, str]] = []
    ann_dir = _default_dir() / "annotations"
    if ann_dir.exists():
        traces_dir = _default_dir() / "traces"
        # AnnotationStore.load(trace_id) lookups need trace stems; collect
        # every captured trace_id from the traces dir so we can iterate
        # without depending on filename encoding here.
        if traces_dir.exists():
            for trace_path in sorted(traces_dir.glob("*.jsonl")):
                stem = trace_path.stem
                for a in store.load(stem):
                    all_rows.append((
                        stem[:12],
                        a.step_id[:12],
                        a.failure_category,
                        a.severity,
                        a.notes or "—",
                    ))

    if not all_rows:
        console.print()
        console.print(
            Text("  ◉ ", style=AMBER)
            + Text("No annotations yet.", style=INK)
        )
        console.print(
            Text("    Tag a step on any captured trace to start "
                 "your failure dossier.", style=DIM)
        )
        console.print()
        console.print(hint("loupe list                              "
                           "every captured trace"))
        console.print(hint("loupe tag <trace> <step> <category>     "
                           "annotate a failure"))
        console.print(hint("loupe explain annotations               "
                           "what tags unlock"))
        console.print()
        return

    table = Table(
        show_header=True,
        header_style=f"dim {DIM}",
        box=SIMPLE,
        padding=(0, 2),
        title=Text(
            f"annotations · {len(all_rows):,} across {len({r[0] for r in all_rows}):,} "
            f"trace(s)", style=f"italic {AMBER}",
        ),
        title_justify="left",
    )
    table.add_column("trace", style=AMBER)
    table.add_column("step_id", style=AMBER)
    table.add_column("category", style=INK)
    table.add_column("severity", style=DIM)
    table.add_column("notes", style=DIM)
    for row in all_rows:
        table.add_row(*row)
    console.print()
    console.print(table)
    console.print()


# ----------------------------------------------------------------------------
# Circuit attribution
# ----------------------------------------------------------------------------


@app.command("attribute", rich_help_panel=_GROUP_ANALYZE)
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
    list_saes: bool = typer.Option(
        False, "--list-saes",
        help="Print every SAE Loupe can attribute through, then exit. "
             "Use the labels with --sae to pin a specific surrogate.",
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

    if list_saes:
        from loupe._sae_registry import SAE_ENTRIES
        render_padded(banner("attribution · supported SAEs", version=__version__))
        rows = [(e.label, f"{e.display} · {e.tagline}") for e in SAE_ENTRIES]
        console.print(kv_table(rows))
        console.print()
        console.print(
            hint("loupe attribute <trace> --backend sae --sae <label>    pin a SAE")
        )
        console.print()
        return

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


@app.command("export", rich_help_panel=_GROUP_ANALYZE)
def export(
    out: Path = typer.Option(
        Path(""), "--out", "-o",
        help="Output file. Default depends on --format. Use `-` for stdout.",
    ),
    format: str = typer.Option(
        "loupebench", "--format", "-f",
        help="`loupebench` (annotated failures, JSONL), `otlp` (OpenTelemetry "
             "OTLP/HTTP JSON), or `parquet` (one row per step, for analytics).",
    ),
    license_: str = typer.Option(
        "CC-BY-4.0", "--license",
        help="License field on each LoupeBench record. Ignored for --format otlp.",
    ),
    trace_id: str = typer.Option(
        "", "--trace-id",
        help="OTLP only — export only traces whose id starts with this prefix.",
    ),
    service_name: str = typer.Option(
        "loupe", "--service-name",
        help="OTLP only — `service.name` resource attribute on every span.",
    ),
) -> None:
    """Export captured traces to JSONL (LoupeBench) or OpenTelemetry OTLP JSON.

    Examples:

      loupe export                              # LoupeBench → loupe-bench.jsonl
      loupe export --format otlp                # OTLP JSON  → loupe-otlp.json
      loupe export --format otlp --out -        # OTLP to stdout (pipe friendly)
      loupe export --format otlp --trace-id ab1 # one specific trace

    OTLP output drops cleanly into any OTel/HTTP collector (Datadog APM,
    Honeycomb, Jaeger, Tempo, Grafana, New Relic, AWS X-Ray):

      curl -X POST <collector>/v1/traces \\
           -H 'content-type: application/json' \\
           --data-binary @loupe-otlp.json
    """
    fmt = (format or "loupebench").strip().lower()
    # `jsonl` is an accepted alias for `loupebench` — the LoupeBench
    # export IS a JSONL file, and `loupe export` help calls it
    # "JSONL (LoupeBench)", so a user typing --format jsonl should work.
    if fmt == "jsonl":
        fmt = "loupebench"
    if fmt not in ("loupebench", "otlp", "parquet"):
        console.print(
            Text(f"  ✗ unknown --format {format!r}; "
                 "use 'loupebench' (alias: jsonl), 'otlp', or 'parquet'.",
                 style=RED)
        )
        raise typer.Exit(code=1)

    if fmt == "loupebench":
        target = out if str(out) else Path("loupe-bench.jsonl")
        count = export_jsonl(target, license=license_)
        if count == 0:
            console.print(
                Text("  Nothing to export yet — tag some failures first.", style=DIM)
            )
            console.print(hint("loupe tag <trace-id> <step-id> <category>"))
            return
        console.print(
            Text(f"  ✓ exported {count} record(s) → ", style=GREEN)
            + Text(str(target), style=AMBER)
        )
        return

    if fmt == "parquet":
        _run_parquet_export(
            out=out if str(out) else Path("loupe-traces.parquet"),
            trace_id_prefix=trace_id.strip() or None,
        )
        return

    # --- OTLP path -----------------------------------------------------
    from loupe.otlp import build_otlp_document, export_traces

    home = _default_dir()
    traces_dir = home / "traces"
    if not traces_dir.exists() or not any(traces_dir.glob("*.jsonl")):
        console.print(_no_traces_hint())
        raise typer.Exit(code=1)

    raw_out = str(out)
    if raw_out == "-":
        pattern = f"{trace_id.strip()}*.jsonl" if trace_id.strip() else "*.jsonl"
        doc = build_otlp_document(
            sorted(traces_dir.glob(pattern)), service_name=service_name,
        )
        console.print_json(data=doc)
        return

    target = out if raw_out else Path("loupe-otlp.json")
    count, written = export_traces(
        traces_dir=traces_dir,
        out=target,
        service_name=service_name,
        trace_id_prefix=trace_id.strip() or None,
    )
    if count == 0:
        console.print(Text("  No matching traces found.", style=DIM))
        raise typer.Exit(code=1)

    render_padded(
        banner("otlp export", version=__version__),
        kv_table([
            ("traces exported", str(count)),
            ("service.name",    service_name),
            ("written to",      str(written)),
        ]),
    )
    console.print()
    console.print(hint("curl -X POST <collector>/v1/traces \\"))
    console.print(hint(f"     -H 'content-type: application/json' --data-binary @{written}"))
    console.print()


def _run_parquet_export(*, out: Path, trace_id_prefix: str | None) -> None:
    """Implementation of ``loupe export --format parquet``.

    Walks the user's traces directory, flattens every step into a
    Parquet row, writes a single columnar file. See
    :mod:`loupe._parquet` for the column schema.
    """
    from loupe._parquet import export_traces_to_parquet

    home = _default_dir()
    traces_dir = home / "traces"
    if not traces_dir.exists() or not any(traces_dir.glob("*.jsonl")):
        console.print(_no_traces_hint())
        raise typer.Exit(code=1)

    try:
        trace_count, step_count, written = export_traces_to_parquet(
            traces_dir=traces_dir,
            out=out,
            trace_id_prefix=trace_id_prefix,
        )
    except RuntimeError as exc:
        console.print(Text(f"  ✗ {exc}", style=RED))
        raise typer.Exit(code=1) from None

    if trace_count == 0:
        console.print(Text("  No matching traces found.", style=DIM))
        raise typer.Exit(code=1)

    render_padded(
        banner("parquet export", version=__version__),
        kv_table([
            ("traces exported", str(trace_count)),
            ("rows (steps)",    str(step_count)),
            ("compression",     "ZSTD"),
            ("written to",      str(written)),
        ]),
    )
    console.print()
    console.print(
        hint("duckdb -c \"SELECT step_kind, count(*) FROM '"
             + str(written) + "' GROUP BY 1;\"")
    )
    console.print()


@app.command("report", rich_help_panel=_GROUP_ANALYZE)
def report(
    trace_id: str = typer.Argument(
        "",
        help="Trace id (or prefix) to render.",
    ),
    out: Path | None = typer.Option(None, "--out", "-o"),
    html_out: bool = typer.Option(
        False, "--html", help="Render as a standalone single-file HTML viewer"
    ),
) -> None:
    """Render a shareable case file (markdown by default, --html for a viewer)."""
    if not trace_id:
        _missing_trace_id_hint(
            "report",
            extra_examples=[
                "loupe report <trace> --html --out r.html    standalone viewer",
            ],
        )
        raise typer.Exit(code=1)
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


@app.command("onboard", rich_help_panel=_GROUP_GET_STARTED)
def onboard() -> None:
    """Get Loupe running on YOUR project in ~60 seconds.

    Walks you through it for real — no demo data:

      1. Make sure a provider key is set.
      2. Find the agent script in this folder.
      3. Run it under capture (with your OK) → a real trace.
      4. Open the dashboard on your own captured run.

    Also runs automatically the first time you type `loupe` inside a
    project folder. Re-run it anytime.
    """
    _run_onboard()


def _run_onboard() -> None:
    """Interactive, on-your-real-project first-run flow.

    Safety: never executes user code without an explicit yes, and in a
    non-TTY context runs nothing at all — it just prints the outline.
    """
    from loupe._onboard import detect_agent_scripts

    is_tty = (
        sys.stdin.isatty() and sys.stdout.isatty()
        and not os.environ.get("LOUPE_DISABLE_AUTOSETUP")
    )

    render_padded(banner("onboard", version=__version__))
    console.print(
        Text("  Let's get Loupe capturing your agent — on your own code.",
             style=INK)
    )
    console.print()

    cwd = Path.cwd()
    candidates = detect_agent_scripts(cwd)

    # --- non-interactive: print the outline, run NOTHING ----------------
    if not is_tty:
        console.print(section("what onboarding does"))
        console.print()
        console.print(hint("1. configure a provider key"))
        if candidates:
            top = candidates[0]
            console.print(hint(
                f"2. run {top.path.name} under capture  ({top.why})"
            ))
        else:
            console.print(hint("2. scaffold a sample agent + run it"))
        console.print(hint("3. open the dashboard on the captured trace"))
        console.print()
        console.print(
            Text("  Run ", style=DIM)
            + Text("loupe onboard", style=AMBER)
            + Text(" in an interactive terminal to do it for real.", style=DIM)
        )
        console.print()
        return

    # --- step 1: provider ----------------------------------------------
    console.print(section("step 1 · provider"))
    console.print()
    _ensure_provider_or_setup("capture your agent")
    console.print()

    # --- step 2 + 3: find + run their agent ----------------------------
    console.print(section("step 2 · your agent"))
    console.print()
    target_script: Path | None = None
    for cand in candidates[:3]:
        rel = cand.path.relative_to(cwd) if cand.path.is_relative_to(cwd) else cand.path
        console.print(
            Text("  ◉ ", style=AMBER)
            + Text(f"found {rel}", style=INK)
            + Text(f"  ({cand.why})", style=DIM)
        )
        ans = _prompt_yes_no(f"    Run {rel} under capture?", default=True)
        if ans:
            target_script = cand.path
            break
        console.print()

    if target_script is None and not candidates:
        console.print(
            Text("  No agent script detected in this folder.", style=DIM)
        )
        if _prompt_yes_no("    Scaffold a sample agent + run it?", default=True):
            from loupe.scaffold import scaffold
            demo_dir = cwd / "loupe-demo"
            if not demo_dir.exists():
                scaffold(demo_dir, "loupe-demo")
            target_script = demo_dir / "agent.py"
            console.print(
                Text(f"  ◉ scaffolded {demo_dir.relative_to(cwd)}/agent.py",
                     style=AMBER)
            )
        else:
            console.print()
            console.print(hint("loupe run <your command>   capture any agent, any language"))
            console.print(hint("loupe init my-agent         scaffold a starter"))
            console.print()
            return

    if target_script is None:
        # Candidates existed but the user declined all of them.
        console.print()
        console.print(hint("loupe run <file.py>   capture a specific script anytime"))
        console.print()
        return

    console.print()
    console.print(section("step 3 · capture"))
    import contextlib as _ctx
    # The script may exit non-zero; the trace was still captured, so we
    # carry on to the dashboard step regardless.
    with _ctx.suppress(typer.Exit):
        _run_python_script_inproc([str(target_script)])

    # --- step 4: open the dashboard ------------------------------------
    console.print()
    console.print(section("step 4 · inspect"))
    console.print()
    if _prompt_yes_no("  Open the dashboard on your captured run?", default=True):
        _run_ui(host="127.0.0.1", port=7860, auto_port=True, open_browser=True)
    else:
        console.print(hint("loupe ui      open the forensic dashboard anytime"))
        console.print()


def _prompt_yes_no(question: str, *, default: bool = True) -> bool:
    """Tiny Y/n prompt. Returns ``default`` on empty input or non-tty."""
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        raw = input(question + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not raw:
        return default
    return raw in ("y", "yes")


@app.command("init", rich_help_panel=_GROUP_GET_STARTED)
def init(
    name: str = typer.Argument(..., help="Project / agent name"),
    target: Path = typer.Option(Path("."), "--dir", "-d"),
    filename: str = typer.Option(
        "agent.py", "--file", "-f",
        help="Name of the entry script (must end in .py). "
             "Use this if `agent.py` collides with your existing layout.",
    ),
    provider: str = typer.Option(
        "gemini", "--provider", "-p",
        help="LLM provider for the starter: gemini (default, free tier), "
             "anthropic, or openai. The generated agent uses that "
             "provider's native SDK.",
    ),
) -> None:
    """Scaffold a Loupe-instrumented starter project.

    Examples::

        loupe init my-agent                                 # default: Gemini + agent.py
        loupe init my-agent --provider anthropic            # Anthropic Claude
        loupe init my-agent --provider openai --file main.py
    """
    project_dir = target / name if target == Path(".") else target
    if project_dir.exists() and any(project_dir.iterdir()):
        console.print(Text(f"  ✗ Refusing to write into non-empty {project_dir}", style=RED))
        console.print(hint(f"loupe init {name}-new        scaffold under a different name"))
        console.print(hint(f"rm -rf {project_dir}    if you really want to overwrite"))
        raise typer.Exit(code=1)
    try:
        files = scaffold(project_dir, name, filename=filename, provider=provider)
    except ValueError as exc:
        console.print(Text(f"  ✗ {exc}", style=RED))
        console.print(hint("loupe init <name> --provider gemini|anthropic|openai"))
        console.print(hint("loupe init <name> --file main.py     "
                           "any bare *.py filename"))
        raise typer.Exit(code=1) from None
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
    console.print(cmd(f"python {filename}"))
    console.print(cmd("loupe ui"))
    console.print()


# ----------------------------------------------------------------------------
# Diagnostics
# ----------------------------------------------------------------------------


@app.command("doctor", rich_help_panel=_GROUP_INFRA)
def doctor(
    smoke: bool = typer.Option(
        False, "--smoke", help="Also run a full end-to-end smoke test in a tmp dir"
    ),
    fix: bool = typer.Option(
        False, "--fix",
        help="SAFE + REVERSIBLE self-heal: create missing dirs, "
             "quarantine corrupt JSONL (mv → ~/.loupe/quarantine/), "
             "remove orphan annotation sidecars, rebuild the DuckDB "
             "index if it has drifted from the on-disk traces.",
    ),
) -> None:
    """Diagnose your install + show wired-up integrations.

    Pass ``--smoke`` to additionally execute a tiny lifecycle (capture
    → save → load → validate → tag → untag) so you know everything
    actually works, not just that the packages are importable.

    Pass ``--fix`` to repair common issues in place. Every fix is safe
    and reversible: corrupt JSONL is *moved* to ``~/.loupe/quarantine/``
    (never deleted), and orphan annotation sidecars are removed only
    when their parent trace is already gone (so there's nothing to
    lose). Run again without ``--fix`` to confirm the install reports
    clean afterward.
    """
    home = _default_dir()
    if fix:
        _run_doctor_fix(home)
        # Fall through and re-diagnose so the user sees the post-repair state.

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


def _run_doctor_fix(home: Path) -> None:
    """Self-heal: safe + reversible repairs to a Loupe install.

    Runs BEFORE the diagnostic table so subsequent rows reflect the
    post-repair state.

    Repairs (all idempotent):
      1. ``~/.loupe/traces/``       — create if missing
      2. ``~/.loupe/annotations/``  — create if missing
      3. Corrupt JSONL              — mv to ``~/.loupe/quarantine/``
      4. Orphan annotation sidecar  — rm (parent trace already gone)
      5. DuckDB index drift         — rebuild via ``rebuild_index()``

    Every repair prints a ``✓`` one-liner so the user knows what changed.
    """
    from loupe.store import safe_load_jsonl

    console.print()
    console.print(section("self-heal"))
    console.print()
    repairs = 0

    # 1 + 2. Missing directories.
    for sub in ("traces", "annotations"):
        d = home / sub
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            console.print(Text(f"  ✓ created {d}", style=GREEN))
            repairs += 1

    # 3. Corrupt JSONL → quarantine.
    quarantine = home / "quarantine"
    traces_dir = home / "traces"
    if traces_dir.exists():
        for path in sorted(traces_dir.glob("*.jsonl")):
            try:
                records, skipped = safe_load_jsonl(path)
            except Exception:
                # Even safe_load_jsonl couldn't open it — treat as fully corrupt.
                records, skipped = [], 1
            header_present = any(r.get("_type") == "trace" for r in records)
            if skipped > 0 or not header_present:
                quarantine.mkdir(parents=True, exist_ok=True)
                dest = quarantine / path.name
                # If a same-named quarantine file already exists, append a suffix.
                if dest.exists():
                    suffix = 1
                    while (quarantine / f"{path.stem}.{suffix}.jsonl").exists():
                        suffix += 1
                    dest = quarantine / f"{path.stem}.{suffix}.jsonl"
                path.rename(dest)
                reason = (
                    f"{skipped} corrupt line(s)" if skipped > 0
                    else "no trace header"
                )
                console.print(
                    Text(f"  ✓ quarantined {path.stem[:12]} ", style=GREEN)
                    + Text(f"({reason}) → {dest}", style=DIM)
                )
                repairs += 1

    # 4. Orphan annotation sidecars.
    annotations_dir = home / "annotations"
    if annotations_dir.exists() and traces_dir.exists():
        live_trace_stems = {p.stem for p in traces_dir.glob("*.jsonl")}
        for ann in sorted(annotations_dir.glob("*.json")):
            # Annotation files are named by trace_id (with optional suffix).
            stem = ann.stem.split(".")[0]
            if stem and stem not in live_trace_stems:
                ann.unlink()
                console.print(
                    Text(f"  ✓ removed orphan annotation {ann.stem[:18]} ",
                         style=GREEN)
                    + Text("(parent trace already gone)", style=DIM)
                )
                repairs += 1

    # 5. Index drift.
    try:
        from loupe.index import JSONLIndex
        idx = JSONLIndex(
            db_path=home / "index.duckdb",
            traces_dir=home / "traces",
        )
        info_before = idx.info()
        indexed = int(info_before.get("trace_count", 0))
        traces_on_disk = (
            len(list(traces_dir.glob("*.jsonl")))
            if traces_dir.exists() else 0
        )
        if indexed != traces_on_disk:
            indexed_after, _ = idx.rebuild()
            console.print(
                Text(
                    f"  ✓ index rebuilt (was {indexed} rows for "
                    f"{traces_on_disk} file(s) → now {indexed_after}/"
                    f"{traces_on_disk})",
                    style=GREEN,
                )
            )
            repairs += 1
    except Exception:  # noqa: BLE001 — index is best-effort, never block
        pass

    if repairs == 0:
        console.print(Text("  ◉ already clean — nothing to repair.", style=DIM))
    else:
        console.print()
        console.print(
            Text(f"  {repairs} repair(s) applied.", style=INK)
        )
        quarantine_dir = home / "quarantine"
        if quarantine_dir.exists() and any(quarantine_dir.iterdir()):
            console.print(
                Text(
                    f"  Quarantined files live at {quarantine_dir} — "
                    "inspect or delete by hand.",
                    style=DIM,
                )
            )
    console.print()


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


@app.command("diff", rich_help_panel=_GROUP_INSPECT)
def diff_cmd(
    a: str = typer.Argument("", help="First trace id (or prefix)"),
    b: str = typer.Argument("", help="Second trace id (or prefix)"),
) -> None:
    """Side-by-side diff of two captured traces — useful for A/B comparisons."""
    from difflib import SequenceMatcher

    if not a or not b:
        # Pull the two most-recent traces so the suggestion is paste-ready.
        traces_dir = _default_dir() / "traces"
        recent_ids: list[str] = []
        if traces_dir.exists():
            recent = sorted(
                traces_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:2]
            recent_ids = [p.stem[:12] for p in recent]

        console.print()
        console.print(
            Text("  ◉ ", style=AMBER)
            + Text("loupe diff needs TWO trace ids.", style=INK)
        )
        console.print()
        if len(recent_ids) >= 2:
            console.print(hint(f"loupe diff {recent_ids[0]} {recent_ids[1]}    "
                               f"your two most recent traces"))
        console.print(hint("loupe diff <trace-a> <trace-b>       "
                           "any two traces, prefix is fine"))
        console.print(hint("loupe list                           "
                           "every captured trace"))
        console.print()
        raise typer.Exit(code=1)

    path_a = _find_trace(a)
    if path_a is None:
        raise typer.Exit(code=1)
    path_b = _find_trace(b)
    if path_b is None:
        raise typer.Exit(code=1)

    header_a, steps_a = _load_trace(path_a)
    header_b, steps_b = _load_trace(path_b)
    if header_a is None or header_b is None:
        console.print(Text("  ✗ malformed trace", style=RED))
        console.print(hint(f"loupe verify {path_a.stem[:12]}   check the schema"))
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
    """Thin shim over ``loupe.store.load_trace_split``."""
    from loupe.store import load_trace_split
    header, steps, _ = load_trace_split(path)
    return header, steps


def _load_trace_with_warning(
    path: Path,
) -> tuple[dict | None, list[dict], int]:
    """Thin shim over ``loupe.store.load_trace_split`` — kept so existing
    callers using the warning-aware variant compile unchanged."""
    from loupe.store import load_trace_split
    return load_trace_split(path)


def _warn_skipped_lines(path: Path, skipped: int) -> None:
    """Print a ⚠ AMBER one-liner if a trace had unparseable lines."""
    if skipped <= 0:
        return
    console.print(
        Text(f"  ⚠ skipped {skipped} corrupt line(s) in ", style=AMBER)
        + Text(path.stem[:12], style=f"italic {AMBER}")
        + Text(
            ". Run `loupe doctor --fix` to quarantine.",
            style=DIM,
        )
    )


def _duration_ms(header: dict) -> float | None:
    started = header.get("started_at")
    ended = header.get("ended_at")
    if started is None or ended is None:
        return None
    return max(0.0, (ended - started) * 1000)


@app.command("stats", rich_help_panel=_GROUP_INSPECT)
def stats(
    as_json: bool = typer.Option(
        False, "--json",
        help="Output stats as a JSON object instead of the formatted tables. "
             "Pipeable into jq, ingestible by CI gates.",
    ),
) -> None:
    """Aggregate counts + breakdowns across every captured trace.

    Uses the DuckDB index when available for O(1)-ish aggregates; falls
    back to walking JSONL files on disk if the index isn't healthy.

    With ``--json``:

        {
          "trace_count":         N,
          "failed_count":        N,
          "step_count":          N,
          "annotation_count":    N,
          "median_duration_ms":  N | null,
          "by_framework":        {framework: count, ...},
          "by_failure_category": {category: count, ...}
        }
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
        # Fall through to the unified empty-home handler below when files
        # is empty — that path returns the structured JSON {} response.
        from loupe.store import read_trace_header
        durations_ms: list[float] = []
        for path in files:
            first = read_trace_header(path)
            if first is None:
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

    # Empty-home guard. Falling through to the formatted/JSON paths below
    # would print zeroed-out tables; the explicit hint is friendlier.
    if total_traces == 0 and annotation_total == 0:
        if as_json:
            typer.echo(_json.dumps({
                "trace_count": 0, "failed_count": 0, "step_count": 0,
                "annotation_count": 0, "median_duration_ms": None,
                "by_framework": {}, "by_failure_category": {},
            }, indent=2))
            return
        render_padded(banner(version=__version__), _no_traces_hint())
        return

    if as_json:
        typer.echo(_json.dumps({
            "trace_count":         total_traces,
            "failed_count":        failure_count,
            "step_count":          step_count,
            "annotation_count":    annotation_total,
            "median_duration_ms":  median_dur,
            "by_framework":        dict(framework_counter),
            "by_failure_category": dict(cat_counter),
        }, indent=2))
        return

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


@app.command("verify", rich_help_panel=_GROUP_INFRA)
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


@app.command("purge", rich_help_panel=_GROUP_INFRA)
def purge(
    older_than: str = typer.Option(
        "",
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
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Use the [retention] block from ~/.loupe/config.toml "
             "(max_age_days + keep_tagged). Designed for cron / nightly jobs.",
    ),
) -> None:
    """Delete captured traces older than a given age. Dry-run by default."""
    # J1 — `--auto` reads the retention block from config.toml and runs
    # the same purge logic. Lets you schedule a nightly cron job without
    # hardcoding the threshold in shell.
    if auto:
        from loupe.config import Config
        cfg = Config.load()
        if cfg.retention_max_age_days <= 0:
            console.print(
                Text("  ◉ ", style=AMBER)
                + Text("retention is OFF in ~/.loupe/config.toml "
                       "([retention] max_age_days = 0).", style=INK)
            )
            console.print(
                hint("loupe explain retention    how to enable + tune")
            )
            return
        older_than = f"{cfg.retention_max_age_days}d"
        # config.keep_tagged defaults to True; the explicit CLI flag wins.
        if not keep_tagged:
            keep_tagged = cfg.retention_keep_tagged

    if not older_than:
        console.print(
            Text("  ✗ ", style=RED)
            + Text("pass --older-than <duration> or --auto", style=INK)
        )
        console.print(hint("loupe purge --older-than 7d --keep-tagged"))
        console.print(hint("loupe purge --auto    use [retention] from config.toml"))
        raise typer.Exit(code=1)

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
    from loupe.store import load_trace_split

    try:
        header, steps, _ = load_trace_split(path)
    except OSError as exc:
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


@app.command("providers", rich_help_panel=_GROUP_INFRA)
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


@app.command("cluster", rich_help_panel=_GROUP_ANALYZE)
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


# ----------------------------------------------------------------------------
# `loupe steer` — feature ablation / amplification on a captured trace
# ----------------------------------------------------------------------------


@app.command("steer", rich_help_panel=_GROUP_ANALYZE)
def steer(
    trace_id: str = typer.Argument(
        "", help="Trace id (or prefix) whose prompt will be replayed.",
    ),
    feature_id: int = typer.Option(
        -1, "--feature", "-f",
        help="SAE feature index to edit (matches an id from "
             "`loupe attribute --explain`).",
    ),
    multiplier: float = typer.Option(
        0.0, "--multiplier", "-m",
        help="0 = ablate · 0.5 = halve · 1 = identity (sanity check) · "
             "2 = amplify. Default 0 (full ablation).",
    ),
    sae_label: str = typer.Option(
        "", "--sae",
        help="Which SAE entry to use. Omit to auto-pick by captured model. "
             "Run `loupe attribute --list-saes` for options.",
    ),
    max_new_tokens: int = typer.Option(
        200, "--max-tokens",
        help="Cap on the steered continuation length.",
    ),
) -> None:
    """Replay a captured prompt with one SAE feature dampened / amplified.

    Answers the mech-interp 101 question: "what if feature 12345 had
    fired half as strongly on this turn — would the agent still have
    looped?" The steered run is captured as a new trace whose
    ``metadata.steered_from`` links back to the original — so
    ``loupe diff`` works between the two side-by-side.

    Examples::

        # Ablate feature 8842 in the open surrogate model
        loupe steer abc12345 --feature 8842

        # Amplify feature 8842 by 2x
        loupe steer abc12345 --feature 8842 --multiplier 2.0

        # Pin a specific SAE (default auto-picks by captured model)
        loupe steer abc12345 --feature 8842 --sae gemma-2-2b

    Limitations:
      - Only OPEN-WEIGHT surrogate models can be steered. Closed-model
        captures (Claude, GPT-4) use an open surrogate; results are
        correlational, not causal proof for the closed model itself.
      - Requires the ``loupe[interp]`` extra. First run downloads the
        SAE weights (~1 GB depending on the entry).
    """
    if not trace_id or feature_id < 0:
        _missing_trace_id_hint(
            "steer",
            extra_examples=[
                "loupe steer <trace> --feature 8842                "
                "ablate feature 8842",
                "loupe steer <trace> --feature 8842 --multiplier 2  "
                "amplify by 2x",
                "loupe attribute --list-saes                       "
                "every SAE Loupe can steer through",
            ],
        )
        raise typer.Exit(code=1)

    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)

    header, prompt, _model, _framework = _extract_replay_inputs(path)
    if not prompt:
        console.print(Text("  ✗ couldn't extract prompt from trace.", style=RED))
        raise typer.Exit(code=1)

    try:
        from loupe.steering import Steerer, SteerSpec
    except ImportError as exc:
        console.print(
            Text(f"  ✗ steering needs loupe[interp]: {exc}", style=RED)
        )
        console.print(hint("pip install 'loupe[interp]'"))
        raise typer.Exit(code=1) from None

    try:
        steerer = Steerer(
            sae_label=sae_label or None,
            max_new_tokens=max_new_tokens,
        )
    except ValueError as exc:
        console.print(Text(f"  ✗ {exc}", style=RED))
        console.print(hint("loupe attribute --list-saes    available SAE labels"))
        raise typer.Exit(code=1) from None

    spec = SteerSpec(feature_id=feature_id, multiplier=multiplier)

    render_padded(
        banner("feature steering", version=__version__),
        kv_table([
            ("source trace",  f"{(header or {}).get('name', '?')}  ({path.stem[:12]})"),
            ("surrogate SAE", f"{steerer.entry.label} · {steerer.entry.model}"),
            ("feature",       str(feature_id)),
            ("multiplier",    f"{multiplier:g}"),
        ]),
    )
    console.print()
    try:
        with spinner("loading SAE + generating steered continuation"):
            result = steerer.run(
                prompt=prompt,
                spec=spec,
                original_trace_id=path.stem,
            )
    except ImportError as exc:
        console.print(Text(f"  ✗ {exc}", style=RED))
        raise typer.Exit(code=1) from None
    except Exception as exc:  # noqa: BLE001 — show provider error verbatim
        console.print(Text(f"  ✗ steering failed: {exc}", style=RED))
        raise typer.Exit(code=1) from None

    console.print(
        Text("  ✓ ", style=GREEN)
        + Text("steered trace captured: ", style=INK)
        + Text(result.trace_id[:12], style=AMBER)
    )
    console.print()
    console.print(section("steered continuation"))
    console.print()
    console.print(Text(f"    {result.steered_text[:600]}", style=INK))
    if len(result.steered_text) > 600:
        console.print(Text("    …(truncated)", style=DIM))
    console.print()
    console.print(
        hint(f"loupe diff {path.stem[:12]} {result.trace_id[:12]}    "
             "compare original vs steered")
    )
    console.print()


# ----------------------------------------------------------------------------
# `loupe attribute --causal` — clean-vs-corrupted attribution patching
# ----------------------------------------------------------------------------
# Implemented as a sibling top-level command since `attribute`'s arg
# surface is already dense. Mirrors the AttributionPatcher API.


@app.command("causal", rich_help_panel=_GROUP_ANALYZE)
def causal(
    trace_id: str = typer.Argument(
        "", help="Trace id whose prompt is the CLEAN run.",
    ),
    corrupted_prompt: str = typer.Option(
        "", "--corrupted", "-c",
        help="A minimally-edited version of the prompt where the failure "
             "should NOT occur (the counterfactual).",
    ),
    answer: str = typer.Option(
        "", "--answer", "-a",
        help="The target answer token both runs should produce when "
             "the failure is fixed. Single-token recommended.",
    ),
    sae_label: str = typer.Option(
        "", "--sae",
        help="SAE entry. Omit to auto-pick by captured model.",
    ),
    top_k: int = typer.Option(
        12, "--top-k",
        help="Top-K features to surface by |Δ activation|.",
    ),
) -> None:
    """Causal interpretability via attribution patching (clean vs corrupted).

    Standard ``loupe attribute`` is correlational: it lists features that
    fired strongly. Attribution patching ranks features by how much
    they CAUSE the gap between a clean (failing) run and a corrupted
    (non-failing) variant — Anthropic 2024 paper-style.

    Workflow::

        # Original failing prompt (loaded from the trace)
        loupe causal abc12345 \\
            --corrupted "Same prompt but with the ambiguous referent fixed." \\
            --answer "No"

    Top features ranked by signed effect size (positive = patching
    them moves clean toward corrupted output, i.e. these features
    were causally responsible for the original failure).
    """
    if not trace_id or not corrupted_prompt or not answer:
        _missing_trace_id_hint(
            "causal",
            extra_examples=[
                "loupe causal <trace> \\\n"
                "      --corrupted 'fixed prompt' \\\n"
                "      --answer 'No'",
                "loupe explain causal                       "
                "attribution patching, in plain English",
            ],
        )
        raise typer.Exit(code=1)

    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)

    header, clean_prompt, model, _framework = _extract_replay_inputs(path)
    if not clean_prompt:
        console.print(Text("  ✗ couldn't extract prompt from trace.", style=RED))
        raise typer.Exit(code=1)

    try:
        from loupe.attribution_patching import AttributionPatcher, PatchPair
    except ImportError as exc:
        console.print(
            Text(f"  ✗ causal attribution needs loupe[interp]: {exc}", style=RED)
        )
        console.print(hint("pip install 'loupe[interp]'"))
        raise typer.Exit(code=1) from None

    try:
        patcher = (
            AttributionPatcher(sae_label=sae_label, top_k=top_k)
            if sae_label else
            AttributionPatcher.from_registry(model, top_k=top_k)
        )
    except ValueError as exc:
        console.print(Text(f"  ✗ {exc}", style=RED))
        raise typer.Exit(code=1) from None

    pair = PatchPair(
        clean_prompt=clean_prompt,
        clean_answer_token=answer,
        corrupted_prompt=corrupted_prompt,
        corrupted_answer_token=answer,
    )

    render_padded(
        banner("causal attribution · patching", version=__version__),
        kv_table([
            ("source trace",   f"{(header or {}).get('name', '?')}  ({path.stem[:12]})"),
            ("surrogate SAE",  f"{patcher.entry.label} · {patcher.entry.model}"),
            ("answer token",   answer),
            ("top-k",          str(top_k)),
        ]),
    )
    console.print()
    try:
        with spinner("loading SAE + running clean/corrupted patches"):
            result = patcher.run(pair)
    except Exception as exc:  # noqa: BLE001
        console.print(Text(f"  ✗ patching failed: {exc}", style=RED))
        raise typer.Exit(code=1) from None

    console.print(
        Text("  ", style=DIM)
        + Text(f"baseline Δlogit = {result.baseline_delta:+.3f}", style=INK)
        + Text("  (negative = clean was less likely to produce the answer)",
               style=DIM)
    )
    console.print()
    console.print(section(f"top-{len(result.top_features)} causal features"))
    console.print()
    if not result.top_features:
        console.print(Text("    (no features above threshold)", style=DIM))
    else:
        from rich.box import SIMPLE
        from rich.table import Table
        table = Table(
            show_header=True, show_edge=False, box=SIMPLE, padding=(0, 2),
            header_style=f"dim {DIM}",
        )
        table.add_column("feature", style=AMBER, no_wrap=True)
        table.add_column("score",   style=INK, no_wrap=True)
        table.add_column("token",   style=DIM, no_wrap=True)
        for f in result.top_features:
            table.add_row(
                str(f.feature_id),
                f"{f.score:+.3f}",
                str(f.token_position) if f.token_position is not None else "?",
            )
        console.print(table)
    console.print()
    console.print(
        hint("loupe steer <trace> --feature <id>    test a causal hypothesis")
    )
    console.print()


# ----------------------------------------------------------------------------
# `loupe cost` — LLM spend tracking from captured traces
# ----------------------------------------------------------------------------


@app.command("cost", rich_help_panel=_GROUP_ANALYZE)
def cost(
    by_model: bool = typer.Option(
        False, "--by-model",
        help="Break down by model id instead of by provider.",
    ),
    as_json: bool = typer.Option(
        False, "--json",
        help="Output as JSON (pipeable into jq, gates, dashboards).",
    ),
) -> None:
    """LLM spend across every captured trace.

    Sums the (input × in-price) + (output × out-price) for every
    ``llm-call`` step whose ``inputs.model`` or step name resolves to
    a known pricing tier. Steps with missing token counts are
    counted as ``—`` (we never make up numbers).

    Pricing table lives in :mod:`loupe.pricing` — published USD list
    prices, hand-maintained, greppable on disk. Run ``loupe explain
    config`` for the override path.
    """
    import json as _json
    from collections import defaultdict

    from loupe.pricing import estimate_cost_usd, format_usd, price_for

    traces_dir = _default_dir() / "traces"
    if not traces_dir.exists() or not any(traces_dir.glob("*.jsonl")):
        if as_json:
            typer.echo(_json.dumps({
                "total_usd": 0.0,
                "trace_count": 0,
                "step_count": 0,
                "unpriced_step_count": 0,
                "by_provider": {},
                "by_model": {},
            }, indent=2))
            return
        render_padded(banner(version=__version__), _no_traces_hint())
        return

    total: float = 0.0
    by_provider: dict[str, float] = defaultdict(float)
    by_model_costs: dict[str, float] = defaultdict(float)
    trace_count = 0
    priced_steps = 0
    unpriced_steps = 0

    from loupe.store import iter_jsonl_records
    for path in sorted(traces_dir.glob("*.jsonl")):
        trace_count += 1
        for obj in iter_jsonl_records(path):
            if obj.get("_type") != "step" or obj.get("kind") != "llm-call":
                continue

            inputs = obj.get("inputs") or {}
            outputs = obj.get("outputs") or {}

            # Token counts can live in either step inputs/outputs (depending
            # on the integration). Try each location explicitly — must not
            # use ``or`` because a legitimate token count of 0 (rare but
            # real for empty responses) would be silently dropped.
            in_tok: Any = None
            for src, key in (
                (outputs, "input_tokens"),
                (outputs, "input"),
                (inputs, "input_tokens"),
            ):
                if src.get(key) is not None:
                    in_tok = src[key]
                    break
            out_tok: Any = None
            for src, key in (
                (outputs, "output_tokens"),
                (outputs, "output"),
                (inputs, "output_tokens"),
            ):
                if src.get(key) is not None:
                    out_tok = src[key]
                    break
            model_id = (
                inputs.get("model")
                or (obj.get("name") or "").split(":", 1)[-1]
            )
            provider_hint = inputs.get("provider")

            cost_usd = estimate_cost_usd(
                in_tok, out_tok, model=model_id, provider=provider_hint,
            )
            if cost_usd is None:
                unpriced_steps += 1
                continue
            priced_steps += 1
            total += cost_usd
            p = price_for(model_id, provider_hint)
            if p is not None:
                by_provider[p.provider] += cost_usd
                by_model_costs[p.model] += cost_usd

    if as_json:
        typer.echo(_json.dumps({
            "total_usd":           round(total, 6),
            "trace_count":         trace_count,
            "step_count":          priced_steps,
            "unpriced_step_count": unpriced_steps,
            "by_provider":         {k: round(v, 6) for k, v in by_provider.items()},
            "by_model":            {k: round(v, 6) for k, v in by_model_costs.items()},
        }, indent=2))
        return

    from rich.box import SIMPLE
    from rich.table import Table

    summary = kv_table([
        ("total", format_usd(total)),
        ("traces scanned", str(trace_count)),
        ("priced steps", str(priced_steps)),
        ("unpriced", str(unpriced_steps)),
    ])

    breakdown = Table(
        show_header=False, show_edge=False, box=SIMPLE, padding=(0, 2),
        title=Text(
            "by model" if by_model else "by provider",
            style=f"italic {AMBER}",
        ),
        title_justify="left",
    )
    breakdown.add_column("name", style=INK, no_wrap=True)
    breakdown.add_column("cost", style=DIM, no_wrap=True, justify="right")
    rows = (by_model_costs if by_model else by_provider).items()
    for name, c in sorted(rows, key=lambda r: -r[1]):
        breakdown.add_row(name, format_usd(c))

    render_padded(
        banner("llm spend", version=__version__),
        summary,
        Text(),
        breakdown,
    )


# ----------------------------------------------------------------------------
# `loupe bench` — regression testing for captured tagged failures
# ----------------------------------------------------------------------------


@app.command("bench", rich_help_panel=_GROUP_ANALYZE)
def bench(
    only_category: str = typer.Option(
        "", "--category", "-c",
        help="Restrict to one failure category (hallucination, loop, …).",
    ),
    provider_override: str = typer.Option(
        "", "--provider",
        help="Override the provider for every replay. Default: each "
             "trace's original framework.",
    ),
    model_override: str = typer.Option(
        "", "--model",
        help="Override the model for every replay (e.g. test a model upgrade).",
    ),
    limit: int = typer.Option(
        0, "--limit",
        help="Replay at most N tagged failures. 0 = unlimited.",
    ),
    corpus: str = typer.Option(
        "", "--corpus",
        help="Replay records from an external LoupeBench corpus instead of "
             "your locally tagged failures. Accepts a bundled name "
             "(`loupebench-v0.1`), a local path, or an HTTP(S) URL.",
    ),
    gate: str = typer.Option(
        "", "--gate",
        help="CI gate. Exits 1 if the chosen threshold is exceeded. "
             "Format: `fail-rate=20%%` (replays that errored / total).",
    ),
    out: Path = typer.Option(
        Path(""), "--out",
        help="Write the leaderboard JSON entry to this path. Default: stdout-skip.",
    ),
) -> None:
    """Replay tagged failures (yours, or from a published corpus).

    The agent CI loop:

    1. You capture an agent run and tag a step (``loupe tag …``).
    2. You change something — a prompt, a model, a tool.
    3. ``loupe bench`` re-runs every tagged failure against the current
       provider+model and reports which still produce the broken output.

    Exit code:
      - **0** when every tagged failure was successfully replayed (the
        re-run completed without an SDK error). Inspect the new traces
        with ``loupe diff <old> <new>`` to judge fix quality.
      - **1** when one or more replays failed to execute (API error,
        missing key, unsupported framework, etc.). Useful for CI gates.

    Each replay is captured as a new trace named ``bench:<original-name>``
    so you can compare side-by-side with ``loupe diff``.

    Examples::

        loupe bench                                   # your tagged failures
        loupe bench --corpus loupebench-v0.1          # bundled public corpus
        loupe bench --corpus https://.../corpus.jsonl # any URL
        loupe bench --corpus ./my.jsonl --out lb.json # write leaderboard
        loupe bench --gate fail-rate=20%              # CI gate (exits 1 if >20%)
    """
    if corpus:
        _run_corpus_bench(
            corpus=corpus,
            provider_override=provider_override or None,
            model_override=model_override or None,
            limit=limit,
            gate=gate,
            out=out if str(out) else None,
            only_category=only_category or None,
        )
        return

    from loupe.annotation import AnnotationStore

    store = AnnotationStore()
    targets: list[tuple[str, str, str]] = []  # (trace_id, step_id, category)
    for trace_id, items in store.all().items():
        for ann in items:
            if only_category and ann.failure_category != only_category:
                continue
            targets.append((trace_id, ann.step_id, ann.failure_category))

    if not targets:
        msg = (
            f"No tagged failures in category {only_category!r}."
            if only_category else "No tagged failures yet."
        )
        console.print(Text(f"  {msg}", style=DIM))
        console.print(hint("loupe tag <trace> <step> <category>    mark a failure"))
        return

    if limit > 0:
        targets = targets[:limit]

    console.print()
    console.print(
        Text("  ◉ ", style=AMBER)
        + Text(f"benchmarking {len(targets)} tagged failure(s)", style=INK)
    )
    if provider_override or model_override:
        console.print(
            Text("    override → ", style=DIM)
            + Text(
                f"{provider_override or 'original'}:{model_override or 'original'}",
                style=AMBER,
            )
        )
    console.print()

    from rich.box import SIMPLE
    from rich.table import Table

    table = Table(
        show_header=True, show_edge=False, box=SIMPLE, padding=(0, 2),
        header_style=f"dim {DIM}",
    )
    table.add_column("original", style=AMBER, no_wrap=True, width=12)
    table.add_column("category", style=INK, no_wrap=True, min_width=14)
    table.add_column("replay", style=DIM, no_wrap=True)

    successes = 0
    failures = 0

    with spinner("replaying tagged failures"):
        for trace_id, step_id, category in targets:
            del step_id    # reserved for future fine-grained re-run
            ok, new_id, detail = _replay_one_for_bench(
                trace_id, provider_override or None, model_override or None,
            )
            if ok:
                successes += 1
                table.add_row(trace_id[:12], category, f"→ {new_id[:12]}")
            else:
                failures += 1
                table.add_row(
                    trace_id[:12], category,
                    Text(f"✗ {detail}", style=RED),
                )

    console.print(table)
    console.print()
    summary = (
        Text("  ✓ ", style=GREEN)
        + Text(f"{successes} replayed", style=INK)
    )
    if failures:
        summary += Text(f"  ·  {failures} failed", style=RED)
    console.print(summary)
    console.print()
    if successes > 0:
        console.print(
            hint("loupe diff <original> <replay>    compare any pair")
        )
        console.print(
            hint("loupe ui                         inspect side-by-side")
        )
        console.print()

    if failures > 0:
        raise typer.Exit(code=1)


def _replay_one_for_bench(
    trace_id: str,
    provider_override: str | None,
    model_override: str | None,
) -> tuple[bool, str, str]:
    """Re-invoke one captured trace using the same machinery as
    ``loupe replay`` — but quietly, returning (ok, new_trace_id, detail).

    Reuses ``_extract_replay_inputs`` + ``_resolve_replay_backend`` so the
    pricing/provider logic stays consistent across the two commands.
    """
    from loupe import record_step
    from loupe import trace as trace_decorator
    from loupe.integrations import patch_all

    path = _find_trace(trace_id)
    if path is None:
        return False, "", "trace file gone"

    header, prompt, model, framework = _extract_replay_inputs(path)
    if not prompt:
        return False, "", "couldn't extract prompt"

    used_provider = provider_override or framework
    used_model = model_override or model
    if not used_model:
        return False, "", "couldn't determine model"

    runner = _resolve_replay_backend(used_provider, prompt, used_model, path.stem)
    if runner is None:
        return False, "", f"no backend for {used_provider!r}"
    if runner.missing_env():
        return False, "", f"{runner.missing_env()} not set"

    patch_all()
    original_name = (header or {}).get("name", "agent")

    @trace_decorator(name=f"bench:{original_name}", framework=runner.framework)
    def _execute() -> str:
        record_step(
            "plan", "bench replay",
            outputs={
                "q": prompt[:200],
                "source_trace": path.stem,
                "model": used_model,
                "provider": runner.framework,
            },
        )
        text, tokens = runner.invoke()
        record_step("final", "got reply", outputs={"text": text[:300], **tokens})
        return text

    try:
        _execute()
    except Exception as exc:  # noqa: BLE001
        return False, "", str(exc)[:80]

    # Find the latest trace — that's the one we just wrote.
    traces_dir = _default_dir() / "traces"
    files = sorted(
        traces_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    new_id = files[0].stem if files else "?"
    return True, new_id, ""


# ---------------------------------------------------------------------------
# `loupe bench --corpus` — replay an external LoupeBench corpus.
# ---------------------------------------------------------------------------


def _run_corpus_bench(
    *,
    corpus: str,
    provider_override: str | None,
    model_override: str | None,
    limit: int,
    gate: str,
    out: Path | None,
    only_category: str | None,
) -> None:
    """Load a corpus, replay each record, write a leaderboard entry.

    See ``loupe.bench.load_corpus`` for accepted ``corpus`` sources.

    Records that can't be replayed (missing model, no provider backend,
    SDK error) count as errors. The gate threshold uses ``errors/total``.
    """
    import json as _json

    from loupe.bench import (
        CorpusError,
        corpus_to_leaderboard_entry,
        load_corpus,
    )

    try:
        records = load_corpus(corpus)
    except CorpusError as exc:
        console.print(Text(f"  ✗ {exc}", style=RED))
        raise typer.Exit(code=1) from None

    if only_category:
        records = [
            r for r in records
            if (r.get("annotation") or {}).get("failure_category") == only_category
        ]
    if not records:
        console.print(
            Text(f"  No records in {corpus} match the category filter.", style=DIM)
        )
        raise typer.Exit(code=1)

    if limit > 0:
        records = records[:limit]

    console.print()
    console.print(
        Text("  ◉ ", style=AMBER)
        + Text(f"benchmarking {len(records)} corpus record(s)", style=INK)
    )
    console.print(Text(f"    corpus: {corpus}", style=DIM))
    if provider_override or model_override:
        console.print(
            Text("    override → ", style=DIM)
            + Text(
                f"{provider_override or 'original'}:{model_override or 'original'}",
                style=AMBER,
            )
        )
    console.print()

    from rich.box import SIMPLE
    from rich.table import Table

    table = Table(
        show_header=True, show_edge=False, box=SIMPLE, padding=(0, 2),
        header_style=f"dim {DIM}",
    )
    table.add_column("id", style=AMBER, no_wrap=True, min_width=22)
    table.add_column("category", style=INK, no_wrap=True, min_width=14)
    table.add_column("status", style=DIM, no_wrap=True)

    results: list[dict[str, str | bool]] = []
    record_categories: dict[str, str] = {}

    used_provider = provider_override
    used_model = model_override
    with spinner("replaying corpus records"):
        for rec in records:
            rec_id = str(rec.get("id", "?"))
            ann = rec.get("annotation") or {}
            category = str(ann.get("failure_category", "unknown"))
            record_categories[rec_id] = category

            ok, new_id, detail = _replay_corpus_record(
                rec,
                provider_override=provider_override,
                model_override=model_override,
            )
            entry: dict[str, str | bool] = {"id": rec_id, "ok": ok}
            if ok:
                entry["trace_id"] = new_id
                table.add_row(rec_id, category, f"→ {new_id[:12]}")
                # Capture the provider/model actually used by the first
                # successful replay so the leaderboard entry is informative.
                if not used_provider:
                    used_provider = (
                        (rec.get("step") or {}).get("inputs", {}).get("provider")
                        or rec.get("framework") or "unknown"
                    )
                if not used_model:
                    used_model = (
                        (rec.get("step") or {}).get("inputs", {}).get("model")
                        or "unknown"
                    )
            else:
                entry["error"] = detail
                table.add_row(rec_id, category, Text(f"✗ {detail}", style=RED))
            results.append(entry)

    console.print(table)
    console.print()

    leaderboard = corpus_to_leaderboard_entry(
        corpus_source=corpus,
        corpus_size=len(records),
        provider=used_provider or "unknown",
        model=used_model or "unknown",
        results=results,
        record_categories=record_categories,
    )

    summary = (
        Text("  ✓ ", style=GREEN)
        + Text(f"{leaderboard['replayed']}/{leaderboard['total']} replayed", style=INK)
        + Text(f"  ·  fail-rate {leaderboard['fail_rate']:.1%}", style=DIM)
    )
    console.print(summary)
    console.print()

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(leaderboard, indent=2) + "\n", encoding="utf-8")
        console.print(
            Text("  ✓ ", style=GREEN)
            + Text("leaderboard → ", style=INK)
            + Text(str(out), style=AMBER)
        )
        console.print()

    if gate:
        _enforce_bench_gate(gate, leaderboard)


def _replay_corpus_record(
    rec: dict[str, Any],
    *,
    provider_override: str | None,
    model_override: str | None,
) -> tuple[bool, str, str]:
    """Replay one corpus record against the configured provider+model.

    Extracts the prompt from ``rec.step.inputs`` (messages, prompt, or
    Gemini-style contents). Then routes through the same replay backend
    machinery used by ``loupe replay`` so the trace + Step shape stays
    consistent.

    Returns ``(ok, new_trace_id, detail)`` mirroring ``_replay_one_for_bench``.
    """
    from loupe import record_step
    from loupe import trace as trace_decorator
    from loupe.integrations import patch_all

    step = rec.get("step") or {}
    inputs = step.get("inputs") or {}

    prompt = _extract_corpus_prompt(inputs)
    if not prompt:
        return False, "", "no prompt in record.step.inputs"

    framework = (
        rec.get("framework")
        or inputs.get("provider")
        or "anthropic"
    )
    original_model = inputs.get("model")

    used_provider = provider_override or framework
    used_model = model_override or original_model
    if not used_model:
        return False, "", "no model in record + no --model override"

    runner = _resolve_replay_backend(used_provider, prompt, used_model, rec.get("id", "lb"))
    if runner is None:
        return False, "", f"no backend for {used_provider!r}"
    if runner.missing_env():
        return False, "", f"{runner.missing_env()} not set"

    patch_all()

    @trace_decorator(
        name=f"bench:{rec.get('id', 'lb')}",
        framework=runner.framework,
    )
    def _execute() -> str:
        record_step(
            "plan", "bench replay",
            outputs={
                "q": prompt[:200],
                "corpus_record": rec.get("id"),
                "model": used_model,
                "provider": runner.framework,
            },
        )
        text, tokens = runner.invoke()
        record_step("final", "got reply", outputs={"text": text[:300], **tokens})
        return text

    try:
        _execute()
    except Exception as exc:  # noqa: BLE001
        return False, "", str(exc)[:80]

    traces_dir = _default_dir() / "traces"
    files = sorted(
        traces_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    new_id = files[0].stem if files else "?"
    return True, new_id, ""


def _extract_corpus_prompt(inputs: dict[str, Any]) -> str:
    """Pull a single prompt string out of a captured step's inputs.

    Tries the common shapes in order:
      1. ``messages`` (Anthropic / OpenAI / etc.) — last user turn
      2. ``contents`` (Gemini) — last user-role parts.text
      3. ``prompt`` (legacy completion API)
    """
    messages = inputs.get("messages")
    if isinstance(messages, list):
        for m in reversed(messages):
            if not isinstance(m, dict) or m.get("role") != "user":
                continue
            content = m.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # Concatenate text blocks; ignore media + tool_use blocks
                # which can't be replayed by the chat-completion runners.
                pieces: list[str] = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text")
                        if isinstance(text, str):
                            pieces.append(text)
                if pieces:
                    return "\n".join(pieces)

    contents = inputs.get("contents")
    if isinstance(contents, list):
        for c in reversed(contents):
            if not isinstance(c, dict):
                continue
            if c.get("role") not in (None, "user"):
                continue
            parts = c.get("parts")
            if isinstance(parts, list):
                pieces = [
                    p["text"] for p in parts
                    if isinstance(p, dict) and isinstance(p.get("text"), str)
                ]
                if pieces:
                    return "\n".join(pieces)

    prompt = inputs.get("prompt")
    if isinstance(prompt, str):
        return prompt
    return ""


def _enforce_bench_gate(gate: str, leaderboard: dict[str, Any]) -> None:
    """Apply a CI gate to a leaderboard entry. Exits 1 if breached.

    Supported syntax: ``fail-rate=20%`` or ``fail-rate=0.2``.
    """
    spec = gate.strip()
    if "=" not in spec:
        console.print(
            Text(f"  ✗ invalid --gate {gate!r}; expected `metric=value`", style=RED)
        )
        raise typer.Exit(code=1)
    metric, _, value = spec.partition("=")
    metric = metric.strip().lower()
    value = value.strip()
    if metric != "fail-rate":
        console.print(
            Text(f"  ✗ unsupported gate metric {metric!r}; only "
                 "`fail-rate` is implemented today.", style=RED)
        )
        raise typer.Exit(code=1)
    threshold = value.rstrip("%")
    try:
        thr = float(threshold)
    except ValueError:
        console.print(
            Text(f"  ✗ gate threshold {value!r} isn't a number", style=RED)
        )
        raise typer.Exit(code=1) from None
    if value.endswith("%"):
        thr /= 100.0
    actual = float(leaderboard["fail_rate"])
    if actual > thr:
        console.print(
            Text("  ✗ ", style=RED)
            + Text(f"gate breached: fail-rate {actual:.1%} > threshold {thr:.1%}",
                   style=INK)
        )
        raise typer.Exit(code=1)
    console.print(
        Text("  ✓ ", style=GREEN)
        + Text(f"gate ok: fail-rate {actual:.1%} ≤ threshold {thr:.1%}",
               style=INK)
    )
    console.print()


@app.command("replay", rich_help_panel=_GROUP_ANALYZE)
def replay(
    trace_id: str,
    prompt: str = typer.Option(
        "", "--prompt", "-p",
        help="Override the prompt. Default: extract from the original trace.",
    ),
    model: str = typer.Option(
        "", "--model",
        help="Override the model. Default: use the one from the original trace.",
    ),
) -> None:
    """Re-run an agent run captured earlier.

    Useful for:
      * **Reproducibility**: same prompt, same model — did the bug repeat?
      * **Model upgrades**: same prompt, newer model — did the bug get fixed?
      * **Prompt variants**: edit ``--prompt``, hold model constant.

    The result is captured as a NEW Loupe trace. The CLI prints both
    trace ids so you can run ``loupe diff <old> <new>`` immediately.

    Supported providers: ``gemini`` / ``google``, ``anthropic``,
    ``openai``. The framework field on the original trace selects which
    SDK to invoke; the required API key for that provider must be in
    the current shell.
    """
    from loupe import record_step
    from loupe import trace as trace_decorator
    from loupe.integrations import patch_all

    path = _find_trace(trace_id)
    if path is None:
        raise typer.Exit(code=1)

    header, prompt_from_trace, model_from_trace, framework = _extract_replay_inputs(path)
    if header is None:
        console.print(Text("  ✗ trace has no header.", style=RED))
        raise typer.Exit(code=1)

    final_prompt = prompt or prompt_from_trace
    final_model = model or model_from_trace

    if not final_prompt:
        console.print(
            Text("  ✗ could not extract a prompt from the trace.", style=RED)
        )
        console.print(
            Text("  Pass --prompt to override.", style=DIM)
        )
        raise typer.Exit(code=1)
    if not final_model:
        console.print(
            Text("  ✗ could not extract a model from the trace.", style=RED)
        )
        console.print(Text("  Pass --model to override.", style=DIM))
        raise typer.Exit(code=1)

    # Select the replay backend by framework name. Each backend is a
    # closure: () -> str. Centralizing keeps the trace+spinner code
    # below uniform across providers.
    runner = _resolve_replay_backend(framework, final_prompt, final_model, path.stem)
    if runner is None:
        console.print(
            Text(
                f"  ✗ replay does not recognize framework {framework!r}. "
                "Supported: gemini, google, anthropic, openai.",
                style=RED,
            )
        )
        console.print(
            Text("  Pass --model + ensure the trace's framework field is set.",
                 style=DIM)
        )
        raise typer.Exit(code=1)

    missing_env = runner.missing_env()
    if missing_env:
        console.print(
            Text(f"  ✗ {missing_env} is not set in this shell.", style=RED)
        )
        console.print(Text(f"  {runner.key_hint}", style=DIM))
        raise typer.Exit(code=1)

    patch_all()
    original_name = header.get("name", "agent")

    @trace_decorator(name=f"replay-of-{original_name}", framework=runner.framework)
    def _replay_turn() -> str:
        record_step(
            "plan", "replay prompt",
            outputs={
                "q": final_prompt[:200],
                "source_trace": path.stem,
                "model": final_model,
                "provider": runner.framework,
            },
        )
        text, tokens = runner.invoke()
        record_step(
            "final", "got reply",
            outputs={"text": text[:300], **tokens},
        )
        return text

    console.print()
    console.print(
        Text("  ◉ replaying ", style=AMBER)
        + Text(f"{path.stem[:12]}", style=INK)
        + Text(f"  ·  model={final_model}", style=DIM)
    )
    console.print(
        Text("    prompt: ", style=DIM)
        + Text(final_prompt[:120], style=INK)
        + (Text(" …", style=DIM) if len(final_prompt) > 120 else Text())
    )
    console.print()

    try:
        with spinner(f"Calling {final_model}"):
            answer: str = _replay_turn()
    except Exception as exc:  # noqa: BLE001 — surface the API error verbatim
        console.print(Text(f"  ✗ replay failed: {exc}", style=RED))
        console.print(
            Text("  (the failure was still captured — open loupe ui to see it)",
                 style=DIM)
        )
        raise typer.Exit(code=1) from None

    # Find the new trace id by diffing the traces dir snapshot.
    traces_dir = _default_dir() / "traces"
    candidates = sorted(
        traces_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    new_id = candidates[0].stem if candidates else "?"

    console.print(
        Text("  ✓ replay captured ", style=GREEN)
        + Text(new_id[:12], style=AMBER)
    )
    console.print(
        Text("    answer: ", style=DIM)
        + Text(answer[:160], style=INK)
        + (Text(" …", style=DIM) if len(answer) > 160 else Text())
    )
    console.print()
    console.print(
        hint(f"loupe diff {path.stem[:12]} {new_id[:12]}    # compare originals")
    )
    console.print(
        hint(f"loupe show {new_id[:12]}                    # inspect the replay")
    )
    console.print()


# ----------------------------------------------------------------------------
# Replay backends — one per provider.
#
# Each backend is constructed with (prompt, model, source_trace_id) and
# exposes:
#   .framework  — canonical string written to the new trace
#   .key_hint   — user-facing one-line hint for setting the env var
#   .missing_env() — returns "GEMINI_API_KEY" / "ANTHROPIC_API_KEY" / ""
#                   so the CLI can fail before touching the SDK
#   .invoke()    — performs the actual API call. Returns (text, tokens-dict).
#                  May raise any provider SDK exception; the caller catches
#                  and prints a clean error.
#
# Adding a new provider = one class + one entry in _REPLAY_BACKENDS below.
# ----------------------------------------------------------------------------


class _ReplayRunner:
    """Base class for per-provider replay backends.

    Each subclass implements ``invoke()`` to call the real SDK and return
    ``(text, tokens-dict)``. The base class handles the env-key check
    uniformly so the CLI can fail before any SDK touches the network.
    """

    framework: str
    key_hint: str
    env_keys: tuple[str, ...]

    def __init__(self, prompt: str, model: str) -> None:
        self.prompt = prompt
        self.model = model

    def missing_env(self) -> str:
        """Return the canonical env var name when NONE of the accepted
        vars are set; otherwise empty string."""
        if any(os.environ.get(k) for k in self.env_keys):
            return ""
        return self.env_keys[0]

    def invoke(self) -> tuple[str, dict[str, Any]]:  # pragma: no cover — abstract
        raise NotImplementedError


class _GeminiReplay(_ReplayRunner):
    framework = "gemini"
    key_hint = "Get a free key at https://aistudio.google.com/apikey"
    env_keys = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

    def invoke(self) -> tuple[str, dict[str, Any]]:
        from google import genai
        client = genai.Client()
        response = client.models.generate_content(
            model=self.model, contents=self.prompt,
        )
        text = response.text or "(no text)"
        usage = getattr(response, "usage_metadata", None)
        tokens: dict[str, Any] = {}
        if usage is not None:
            tokens["input"] = getattr(usage, "prompt_token_count", None)
            tokens["output"] = getattr(usage, "candidates_token_count", None)
        return text, tokens


class _AnthropicReplay(_ReplayRunner):
    framework = "anthropic"
    key_hint = "Set ANTHROPIC_API_KEY in your shell (sk-ant-…)."
    env_keys = ("ANTHROPIC_API_KEY",)

    def invoke(self) -> tuple[str, dict[str, Any]]:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": self.prompt}],
        )
        # Concatenate every text block — multimodal responses may include
        # non-text content (tool_use, etc.); we extract just the prose.
        text = "".join(
            getattr(b, "text", "") for b in (response.content or [])
            if getattr(b, "type", None) == "text"
        ) or "(no text)"
        tokens: dict[str, Any] = {
            "input": getattr(response.usage, "input_tokens", None),
            "output": getattr(response.usage, "output_tokens", None),
        }
        return text, tokens


class _OpenAIReplay(_ReplayRunner):
    framework = "openai"
    key_hint = "Set OPENAI_API_KEY in your shell (sk-…)."
    env_keys = ("OPENAI_API_KEY",)

    def invoke(self) -> tuple[str, dict[str, Any]]:
        from openai import OpenAI
        client = OpenAI()
        response = client.chat.completions.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": self.prompt}],
        )
        text = response.choices[0].message.content or "(no text)"
        usage = response.usage
        tokens: dict[str, Any] = {
            "input": getattr(usage, "prompt_tokens", None),
            "output": getattr(usage, "completion_tokens", None),
        } if usage else {}
        return text, tokens


# Framework string (as captured in the trace) → backend class. Aliases
# accepted (e.g. both "gemini" and "google" map to the Gemini backend
# because universal-httpx labels Google APIs differently in different
# capture paths).
_REPLAY_BACKENDS: dict[str, type[_ReplayRunner]] = {
    "gemini":    _GeminiReplay,
    "google":    _GeminiReplay,
    "anthropic": _AnthropicReplay,
    "openai":    _OpenAIReplay,
}


def _resolve_replay_backend(
    framework: str, prompt: str, model: str, source: str,
) -> _ReplayRunner | None:
    """Pick a replay backend by framework name.

    ``source`` is the original trace_id; currently unused by the backends
    but reserved for future telemetry (e.g. recording which trace seeded
    a replay attempt so cluster analysis can track lineage).
    """
    del source   # reserved
    cls = _REPLAY_BACKENDS.get(framework.lower())
    if cls is None:
        return None
    return cls(prompt, model)


def _extract_replay_inputs(
    path: Path,
) -> tuple[dict | None, str, str, str]:
    """Read a trace and return (header, prompt, model, framework).

    Best-effort extraction. The prompt is sourced from, in order:
      1. The first ``plan``-kind step's ``outputs.q`` (loupe init scaffold).
      2. The first ``llm-call``-kind step's ``inputs.contents`` (Gemini).
      3. The first ``llm-call`` step's ``inputs.messages`` joined as text.

    The model is sourced from the first ``llm-call`` step's ``inputs.model``
    or, failing that, parsed out of its ``name`` (``"gemini:gemini-2.5-flash"``).
    """
    from loupe.store import load_trace_split

    prompt = ""
    model = ""
    framework = ""

    header, steps, _ = load_trace_split(path)
    if header is not None:
        framework = header.get("framework") or ""

    for obj in steps:
        if obj.get("kind") == "plan" and not prompt:
            q = (obj.get("outputs") or {}).get("q")
            if isinstance(q, str) and q.strip():
                prompt = q

        if obj.get("kind") == "llm-call":
            ins = obj.get("inputs") or {}
            if not prompt:
                if isinstance(ins.get("contents"), str):
                    prompt = ins["contents"]
                elif isinstance(ins.get("messages"), list):
                    bits: list[str] = []
                    for m in ins["messages"]:
                        if isinstance(m, dict):
                            c = m.get("content")
                            if isinstance(c, str):
                                bits.append(c)
                    prompt = "\n".join(bits)
            if not model:
                if isinstance(ins.get("model"), str):
                    model = ins["model"]
                elif isinstance(obj.get("name"), str) and ":" in obj["name"]:
                    model = obj["name"].split(":", 1)[1]

    return header, prompt, model, framework


_EXPLAIN_TOPICS: dict[str, str] = {
    "loupe": (
        "Loupe is a magnifying glass for your AI agent.\n\n"
        "It captures every LLM call your code makes — model, prompt,\n"
        "response, latency, tokens, errors — so when your agent\n"
        "misbehaves you can replay the exact failure and find the cause.\n\n"
        "Vocabulary you'll see everywhere:\n\n"
        "  trace        one captured agent run — top-to-bottom story of\n"
        "               what happened during one invocation of your code.\n"
        "  step         one event inside a trace: an LLM call, a tool\n"
        "               call, or a checkpoint you wrote with record_step.\n"
        "  evidence     the inputs + outputs + metadata Loupe attached\n"
        "               to a step — what the model saw and returned.\n"
        "  annotation   your tag on a failing step. Feeds LoupeBench.\n"
        "  capture      Loupe writing one of those events to disk.\n"
        "  autopatch    zero-code instrumentation — Loupe wraps any\n"
        "               LLM SDK you import, no changes to your script.\n\n"
        "Three commands cover 90% of usage:\n\n"
        "  loupe init <name>         scaffold a working starter project\n"
        "  python <script>           run anything — Loupe captures it\n"
        "  loupe ui                  open the dashboard to inspect runs\n\n"
        "Then, when one fails:\n\n"
        "  loupe tag <trace> <step> <category>   mark the root cause\n"
        "  loupe diff <a> <b>                    side-by-side comparison\n"
        "  loupe attribute <trace>               which features fired\n\n"
        "Loupe is local-first. Every trace lives at\n"
        "  ~/.loupe/traces/<trace_id>.jsonl\n"
        "as a plain append-only JSONL. No daemon, no cloud, no lock-in."
    ),
    "trace": (
        "A captured agent run — one logical execution of a function "
        "wrapped with @trace.\n\n"
        "Lives on disk as a single JSONL file at\n"
        "  ~/.loupe/traces/{trace_id}.jsonl\n\n"
        "Line 0 is the header (id, name, framework, started_at,\n"
        "ended_at, failure metadata). Lines 1..N are individual steps.\n\n"
        "Once written, traces are immutable. Annotations and circuit\n"
        "attribution are stored in sidecar files so the trace JSONL\n"
        "stays a stable public contract — see docs/SPEC.md."
    ),
    "step": (
        "A single event inside a trace. Each step has:\n"
        "  kind         e.g. 'llm-call', 'tool-call', 'thought', 'error'\n"
        "  name         short human label\n"
        "  inputs       free-form dict (prompts, messages, etc.)\n"
        "  outputs      free-form dict (responses, tokens, etc.)\n"
        "  error        non-null if this step failed\n\n"
        "Steps are emitted by:\n"
        "  • record_step(...)            in your own code\n"
        "  • framework integrations      automatic when you call patch_all()\n"
        "  • universal-httpx capture     any HTTP call to a known provider"
    ),
    "tag": (
        "A LoupeBench annotation on a failing step. Choose a category\n"
        "(hallucination, loop, tool-misuse, etc.), add notes + mitigation,\n"
        "and the tag persists alongside the trace.\n\n"
        "Tagged failures can be bundled into a public benchmark via:\n"
        "  loupe export --out my-bench.jsonl\n\n"
        "Use the dashboard for the easiest tagging UI, or the CLI:\n"
        "  loupe tag <trace-id> <step-id> <category>"
    ),
    "attribution": (
        "SAE-based circuit attribution per llm-call step.\n\n"
        "When you tag a step, you label WHAT went wrong. Circuit\n"
        "attribution shows WHY at the level of mechanism — which\n"
        "interpretable features in the model fired during that turn.\n\n"
        "Backends:\n"
        "  mock  deterministic synthetic features, no deps. CI / demos.\n"
        "  sae   real GPT-2 small + sae-lens forward pass.\n"
        "        Requires `pip install loupe[research]`.\n\n"
        "Run with:\n"
        "  loupe attribute <trace-id> --backend sae --explain"
    ),
    "cluster": (
        "Cluster analysis across tagged failures — find which SAE\n"
        "features recur across many failures of the same category,\n"
        "and which are distinctive to one category vs others.\n\n"
        "  loupe cluster --category hallucination\n\n"
        "Output: a frequency table + (when --category is set) a\n"
        "distinctiveness table scored by smoothed log-ratio."
    ),
    "index": (
        "Embedded DuckDB index at ~/.loupe/index.duckdb that makes\n"
        "loupe list / stats / verify --all O(1)-ish regardless of\n"
        "how many traces you have on disk.\n\n"
        "Source of truth stays the JSONL files. The index is a\n"
        "derived view — rebuild any time with:\n"
        "  loupe index rebuild\n\n"
        "Background-thread upsert on every trace.save() so the hot\n"
        "path stays under 100 µs / step. Set LOUPE_DISABLE_INDEX=1 to\n"
        "opt out entirely (e.g. NFS mounts)."
    ),
    "replay": (
        "Re-invoke a captured agent run with the same prompt + model.\n\n"
        "Used to answer:\n"
        "  • Reproducibility — does the bug repeat?\n"
        "  • Model upgrades — did the bug get fixed by a newer model?\n"
        "  • Prompt variants — edit --prompt, hold model constant.\n\n"
        "The new run is captured as a separate trace; compare with:\n"
        "  loupe diff <old> <new>"
    ),
    "config": (
        "Loupe stores user-level settings in ~/.loupe/config.toml. Manage\n"
        "them with the `loupe config` subcommand — no manual TOML edits.\n\n"
        "    loupe config list                       # every settable key\n"
        "    loupe config get retention.max_age_days\n"
        "    loupe config set retention.max_age_days 30\n"
        "    loupe config set encryption.enabled true\n"
        "    loupe config add-redact 'EMP-\\\\d{6}'    # custom redaction regex\n"
        "    loupe config path                       # echo file path\n\n"
        "Env vars still win as ephemeral overrides:\n"
        "    GEMINI_API_KEY=... loupe ask 'hi'\n\n"
        "For provider keys specifically, use `loupe setup --provider X`."
    ),
    "retention": (
        "Auto-purge captured traces older than a threshold. Off by\n"
        "default — every install keeps all traces forever.\n\n"
        "Enable + tune:\n"
        "    loupe config set retention.max_age_days 30\n"
        "    loupe config set retention.keep_tagged true\n\n"
        "Then drop in cron / systemd timer:\n"
        "    0 3 * * *  loupe purge --auto --yes\n\n"
        "Or run on demand:\n"
        "    loupe purge --auto            # uses config\n"
        "    loupe purge --older-than 7d   # one-off override\n\n"
        "Annotated (tagged) traces are protected when `keep_tagged = true`."
    ),
    "encryption": (
        "Opt-in encryption-at-rest for captured JSONL traces. Uses Fernet\n"
        "(AES-128 + HMAC) with a per-machine key at ~/.loupe/.key (mode 0600).\n\n"
        "Enable:\n"
        "    loupe config set encryption.enabled true\n\n"
        "From that point on, new traces land as a single\n"
        "    LOUPE-ENC-V1:<token>\n"
        "envelope line. All Loupe readers (dashboard, OTLP/Parquet export,\n"
        "loupe show, loupe attribute) decrypt transparently.\n\n"
        "Existing plaintext traces stay readable. Encryption failure\n"
        "during save falls back to plaintext rather than losing the trace.\n\n"
        "Threat model: laptop / VM disk theft. For stricter requirements,\n"
        "layer dm-crypt / FileVault / BitLocker underneath."
    ),
    "redact": (
        "Loupe scrubs credentials from captured payloads before they hit\n"
        "disk. The built-in scanners catch Bearer tokens, sk-… / sk-ant-…\n"
        "/ gho_… / AIza… / JWTs, plus any field whose name contains\n"
        "'authorization', 'api_key', 'secret', etc.\n\n"
        "Add custom regexes for your org's PII / IDs / ticket numbers:\n"
        "    loupe config add-redact 'EMP-\\\\d{6}'\n"
        "    loupe config add-redact 'SSN:\\\\s*\\\\d{3}-\\\\d{2}-\\\\d{4}'\n\n"
        "Patterns compile once + cache; bad regexes are skipped silently.\n"
        "Redact never raises into the capture path."
    ),
    "steer": (
        "Feature steering — replay a captured prompt with one SAE feature\n"
        "dampened (0.0 = ablate) or amplified (>1.0). The steered run\n"
        "is captured as a new trace, so `loupe diff` works side-by-side.\n\n"
        "    loupe steer abc12345 --feature 8842                # ablate\n"
        "    loupe steer abc12345 --feature 8842 --multiplier 2 # amplify\n"
        "    loupe steer abc12345 --feature 8842 --sae gemma-2-2b\n\n"
        "Use it to test causal hypotheses raised by `loupe causal` or\n"
        "the cohort signatures from `loupe attribute cluster`.\n\n"
        "Needs the loupe[interp] extra. Open-weight surrogate models\n"
        "only — closed-model captures (Claude, GPT-4) steer via the open\n"
        "model the SAE registry maps them to."
    ),
    "causal": (
        "Attribution patching — causal interpretability via clean /\n"
        "corrupted prompt pairs (Anthropic 2024 paper recipe).\n\n"
        "Standard `loupe attribute` is correlational. Patching ranks\n"
        "features by how much they CAUSE the gap between a failing\n"
        "(clean) run and a counterfactual (corrupted) run where the\n"
        "failure shouldn't occur.\n\n"
        "    loupe causal <trace> \\\n"
        "        --corrupted 'Same prompt with the ambiguity removed.' \\\n"
        "        --answer 'No'\n\n"
        "Output ranks the top-K features by |Δactivation| at the SAE\n"
        "layer. Positive scores = patching that feature moves the clean\n"
        "run toward the corrupted output, i.e. feature was responsible.\n\n"
        "Test causal hypotheses with `loupe steer`."
    ),
    "sae-registry": (
        "Loupe ships an SAE registry covering three open-weight models:\n\n"
        "    gpt2-small     · default, fastest, well-studied\n"
        "    gemma-2-2b     · used as the Gemini surrogate\n"
        "    pythia-70m     · tiny / smoke-test\n\n"
        "Closed-model captures route to a surrogate by family:\n"
        "    claude-…  → gpt2-small\n"
        "    gpt-…     → gpt2-small\n"
        "    gemini-…  → gemma-2-2b\n\n"
        "Inspect the full list:\n"
        "    loupe attribute --list-saes\n\n"
        "Pin a specific SAE with `--sae <label>` on `attribute`,\n"
        "`causal`, or `steer`."
    ),
    "status": (
        "`loupe status` is the at-a-glance overview of your install.\n"
        "Shows everything Loupe is doing right now in one screen:\n\n"
        "    capture  · autopatch on/off, default provider/model,\n"
        "               encryption, retention, redaction count\n"
        "    providers · which API keys are loaded (env vs config)\n"
        "    activity  · traces on disk, last capture, last-24h cost,\n"
        "                calls, and failures\n"
        "    Next      · context-sensitive next-step hints\n\n"
        "Run it after `loupe setup` to confirm everything is wired up,\n"
        "or whenever you want to know 'is Loupe live, and what is it\n"
        "watching?' without reading any config files."
    ),
    "wire-format": (
        "The Loupe JSONL wire format is the public contract — language-\n"
        "neutral, append-only, byte-stable across SDKs.\n\n"
        "  Line 0: {\"_type\":\"trace\", trace_id, name, framework, ...}\n"
        "  Line N: {\"_type\":\"step\",  step_id, kind, name, inputs, outputs, ...}\n\n"
        "Schema: docs/loupe-trace.schema.json (Draft-2020-12).\n"
        "POST any compliant payload to http://127.0.0.1:7860/api/traces\n"
        "and it lands like a native capture — Go, Rust, curl, anything works."
    ),
    "providers": (
        "Loupe captures LLM calls from 49 providers automatically.\n\n"
        "Three layers:\n"
        "  • Direct SDK integrations (Anthropic, OpenAI) — richest.\n"
        "  • Universal-httpx — catches ANY known provider via host suffix.\n"
        "  • Openai-compatible fallback — unknown hosts whose body\n"
        "    looks like {messages,model} are captured as\n"
        "    openai-compatible:<host>.\n\n"
        "See the full list: loupe providers"
    ),
    "ask": (
        "One captured LLM call from your terminal — like ChatGPT-CLI, but\n"
        "every call is recorded as a Loupe trace.\n\n"
        "    loupe ask \"what is observability?\"\n"
        "    loupe ask --provider anthropic \"summarize this in one line: ...\"\n"
        "    loupe ask --model gpt-4o-mini \"why does my agent loop?\"\n\n"
        "Defaults to the provider + model set by `loupe setup`. Pass\n"
        "--provider / --model to override.\n\n"
        "When to use:\n"
        "  • You want a captured trace without writing any Python.\n"
        "  • Quick model comparisons via `--provider` + `--model`.\n\n"
        "Related:\n"
        "  loupe chat        multi-turn REPL\n"
        "  loupe proxy       capture from ANY language\n"
        "  loupe run         capture your existing Python script"
    ),
    "chat": (
        "Interactive REPL — multi-turn conversation, every turn captured\n"
        "as one step in a single trace.\n\n"
        "    loupe chat                          # default provider\n"
        "    loupe chat --provider anthropic     # pick a provider per session\n\n"
        "Slash commands inside the REPL:\n"
        "    /tag         tag the most recent turn as a failure\n"
        "    /show        print the running trace JSONL\n"
        "    /dashboard   open the local dashboard\n"
        "    /clear       drop conversation history (starts a new trace)\n"
        "    /help, /quit"
    ),
    "run": (
        "Run an existing Python script with auto-capture enabled — no\n"
        "@trace decorator, no patch_all(), no imports added to the script.\n\n"
        "    loupe run my_agent.py \"your question\"\n"
        "    loupe run scripts/eval.py --eval-set my_set\n\n"
        "What it does:\n"
        "  • Calls patch_all() before your script imports anything\n"
        "  • Wraps execution in a Loupe trace named after the script\n"
        "  • Preserves sys.argv so the script sees what `python ... ...`\n"
        "    would have seen\n\n"
        "If you set LOUPE_AUTOPATCH=1 globally, plain `python script.py`\n"
        "works the same way (see `loupe explain autopatch`)."
    ),
    "otlp": (
        "OpenTelemetry OTLP/HTTP JSON export — ship Loupe traces into\n"
        "your existing observability stack (Datadog APM, Honeycomb,\n"
        "Jaeger, Tempo, Grafana, New Relic, AWS X-Ray, anything OTel).\n\n"
        "    loupe export --format otlp                          # → loupe-otlp.json\n"
        "    loupe export --format otlp --out -                  # → stdout\n"
        "    loupe export --format otlp --trace-id abc1234567    # one trace\n\n"
        "Each Loupe step becomes an OTLP span. LLM-call steps carry the\n"
        "GenAI Semantic Convention attributes (`gen_ai.system`,\n"
        "`gen_ai.request.model`, `gen_ai.usage.input_tokens`, etc.) so\n"
        "OTel-aware backends render them as first-class AI spans.\n\n"
        "POST the JSON file to any OTLP/HTTP collector:\n"
        "    curl -X POST <collector>/v1/traces \\\n"
        "         -H 'content-type: application/json' \\\n"
        "         --data-binary @loupe-otlp.json"
    ),
    "proxy": (
        "Universal HTTP capture — works with ANY language, framework, or\n"
        "agent stack. The proxy listens on localhost, forwards every\n"
        "request to the real provider, and captures the full round-trip\n"
        "as a Loupe trace.\n\n"
        "    loupe proxy --provider anthropic --port 7878\n"
        "    set -x ANTHROPIC_BASE_URL http://127.0.0.1:7878\n\n"
        "Now any of these capture without changes:\n"
        "    python my_agent.py        # Python\n"
        "    node my-agent.js          # TypeScript / JavaScript\n"
        "    go run my-agent.go        # Go\n"
        "    curl http://127.0.0.1:7878/v1/messages -d '...'\n\n"
        "Streaming responses pass through chunk-by-chunk — first-token\n"
        "latency is the same as a direct call. Captured streamed text is\n"
        "reassembled and stored exactly like a non-streamed response, so\n"
        "loupe cost + loupe attribute work identically.\n\n"
        "Auto-detect mode (no --provider):\n"
        "  /v1/messages         → Anthropic\n"
        "  /v1/chat/completions → OpenAI\n"
        "  /v1beta/models/...   → Gemini\n"
        "  /openai/v1/chat      → Groq"
    ),
    "autopatch": (
        "Zero-code auto-capture for Python — on automatically after\n"
        "`loupe setup`, no env var needed.\n\n"
        "    pip install loupe\n"
        "    loupe setup           # picks a provider + saves the key\n"
        "    python my_agent.py    # captured automatically\n\n"
        "How it works:\n"
        "  Loupe ships a .pth file that runs on every Python interpreter\n"
        "  startup. It checks:\n"
        "    1. LOUPE_AUTOPATCH=0 → never activate (explicit opt-out)\n"
        "    2. LOUPE_AUTOPATCH=1 → always activate\n"
        "    3. env var unset → activate iff ~/.loupe/config.toml exists\n"
        "       (i.e. you ran `loupe setup`)\n\n"
        "Cost when off: ~3 µs at startup. No imports, no side effects.\n"
        "Cost when on:  +20-40 ms at startup (imports loupe.integrations).\n"
        "               Per-call overhead: <100 µs/step, <5 ms/trace.\n\n"
        "Opt out per-process:\n"
        "    LOUPE_AUTOPATCH=0 python my_agent.py\n\n"
        "Opt out permanently:\n"
        "    rm ~/.loupe/config.toml    # or pin LOUPE_AUTOPATCH=0 in rc\n\n"
        "Original opt-in pattern (before v0.0.59):\n"
        "    set -Ux LOUPE_AUTOPATCH 1    # still works"
    ),
}


@app.command("explain", rich_help_panel=_GROUP_INFRA)
def explain(
    topic: str = typer.Argument(
        "", help="Topic to explain. Run with no arg for the list.",
    ),
) -> None:
    """Built-in topic explainer — no need to leave the terminal for docs.

    Topics: trace, step, tag, attribution, cluster, index, replay,
    config, wire-format, providers.
    """
    if not topic:
        console.print(section("Topics"))
        console.print()
        for name in sorted(_EXPLAIN_TOPICS):
            console.print(Text(f"    {name}", style=AMBER))
        console.print()
        console.print(hint("loupe explain <topic>    plain-English explanation"))
        console.print()
        return

    topic_lower = topic.lower().strip()
    if topic_lower not in _EXPLAIN_TOPICS:
        import difflib
        suggestions = difflib.get_close_matches(
            topic_lower, list(_EXPLAIN_TOPICS.keys()), n=3, cutoff=0.55,
        )
        console.print()
        console.print(Text(f"  ✗ unknown topic '{topic}'", style=RED))
        if suggestions:
            console.print(
                Text("    Did you mean: ", style=DIM)
                + Text(", ".join(suggestions), style=AMBER)
                + Text("?", style=DIM)
            )
        console.print(hint("loupe explain    list every topic"))
        console.print()
        raise typer.Exit(code=1)

    body = _EXPLAIN_TOPICS[topic_lower]
    console.print()
    console.print(
        Text("  ◉ ", style=AMBER)
        + Text(topic_lower, style=f"bold {INK}")
    )
    console.print()
    for line in body.split("\n"):
        console.print(Text(f"  {line}", style=INK if line.strip() else DIM))
    console.print()


@app.command("version", rich_help_panel=_GROUP_INFRA)
def version() -> None:
    """Print Loupe version."""
    console.print(Text("loupe ", style=DIM) + Text(__version__, style=AMBER))


@app.command("status", rich_help_panel=_GROUP_GET_STARTED)
def status() -> None:
    """One-screen overview of your Loupe install.

    Shows what `loupe` is doing right now, what it would capture, and
    what your last 24 hours of activity looked like. Designed so you
    can answer "is Loupe live?", "what's it watching?", "how much have
    I spent today?" without reading any docs or config files.

    Mirrors how `vercel`, `stripe status`, and `gh status` work in
    their respective ecosystems.
    """
    import json as _json
    import os
    import time as _time

    from loupe._setup_providers import get as _get_provider
    from loupe.config import Config, config_path

    home = _default_dir()
    traces_dir = home / "traces"
    cfg = Config.load()
    cfg_exists = config_path().exists()

    # ----- header banner -----
    render_padded(banner("status", version=__version__))

    # ----- block 1: capture --------------------------------------------------
    # Resolution mirrors `_autopatch_enabled()` so what we print matches
    # what the .pth hook actually does.
    raw = os.environ.get("LOUPE_AUTOPATCH")
    if raw is not None:
        norm = raw.strip().lower()
        if norm in ("1", "true", "yes", "on"):
            autopatch_state = "ON (env override)"
        else:
            autopatch_state = "OFF (env override)"
    elif cfg_exists:
        autopatch_state = "ON (config.toml present)"
    else:
        autopatch_state = "OFF (run `loupe setup`)"

    encryption_state = (
        "ON · ~/.loupe/.key (0600)" if cfg.encryption_enabled else "off"
    )
    retention_state = (
        f"every {cfg.retention_max_age_days}d"
        + ("  · keep tagged" if cfg.retention_keep_tagged else "")
        if cfg.retention_max_age_days > 0 else "off"
    )

    rows = [
        ("autopatch",        autopatch_state),
        ("default provider", f"{cfg.default_provider} · {cfg.default_model}"),
        ("encryption",       encryption_state),
        ("retention",        retention_state),
        ("redact patterns",
         f"{len(cfg.redact_patterns)} custom · "
         f"{8 + len(cfg.redact_patterns)} total"),
    ]
    console.print(section("capture"))
    console.print(kv_table(rows))
    console.print()

    # ----- block 2: providers configured -------------------------------------
    configured = cfg.configured_providers()
    if configured:
        cards = []
        for name in configured:
            info = _get_provider(name)
            env_active = any(
                os.environ.get(k) for k in (info.env_keys if info else ())
            )
            src = "env var" if env_active else "config file"
            cards.append((name, f"({src})"))
        console.print(section("providers ready"))
        console.print(kv_table(cards))
        console.print()
    else:
        console.print(section("providers"))
        console.print(
            Text("  No provider configured yet.", style=DIM)
        )
        console.print(hint("loupe setup    pick a provider + paste a key"))
        console.print()

    # ----- block 3: traces snapshot ------------------------------------------
    trace_count = 0
    cost_24h = 0.0
    calls_24h = 0
    failures_24h = 0
    now = _time.time()
    last_24h = now - 24 * 3600
    last_capture_at: float | None = None

    if traces_dir.exists():
        from loupe.pricing import estimate_cost_usd
        for p in traces_dir.glob("*.jsonl"):
            trace_count += 1
            stat = p.stat()
            if last_capture_at is None or stat.st_mtime > last_capture_at:
                last_capture_at = stat.st_mtime
            if stat.st_mtime < last_24h:
                continue
            # Walk the file for cost + failure (cheap: usually a few KB)
            try:
                from loupe._crypto import read_trace_text
                text = read_trace_text(p)
            except Exception:
                continue
            for line in text.splitlines():
                if not line:
                    continue
                try:
                    obj = _json.loads(line)
                except _json.JSONDecodeError:
                    break
                if obj.get("_type") == "trace" and (obj.get("metadata") or {}).get("failed"):
                    failures_24h += 1
                if obj.get("_type") == "step" and obj.get("kind") == "llm-call":
                    calls_24h += 1
                    ins = obj.get("inputs") or {}
                    outs = obj.get("outputs") or {}
                    model = ins.get("model")
                    in_tok = outs.get("input_tokens")
                    out_tok = outs.get("output_tokens")
                    priced = (
                        isinstance(model, str)
                        and isinstance(in_tok, int)
                        and isinstance(out_tok, int)
                    )
                    if priced:
                        usd = estimate_cost_usd(in_tok, out_tok, model=model)
                        if usd is not None:
                            cost_24h += usd

    last_capture_human = (
        _human_relative(now - last_capture_at)
        if last_capture_at is not None
        else "never"
    )
    rows = [
        ("traces on disk",    f"{trace_count:,}"),
        ("last capture",      last_capture_human),
        ("last 24h · calls",  f"{calls_24h:,}"),
        ("last 24h · cost",   f"${cost_24h:.4f}" if cost_24h > 0 else "$0.00"),
        ("last 24h · failed", f"{failures_24h:,}"),
    ]
    console.print(section("activity"))
    console.print(kv_table(rows))
    console.print()

    # ----- block 4: next-step hints ------------------------------------------
    console.print(section("Next"))
    console.print()
    if not configured:
        console.print(hint("loupe setup                 configure your first provider"))
    elif trace_count == 0:
        console.print(hint("python my_agent.py          autopatched — captures automatically"))
        console.print(hint("loupe ask 'hello'           one captured call"))
    else:
        console.print(hint("loupe ui                    open the dashboard"))
        console.print(hint("loupe list                  every captured trace"))
    if cfg.retention_max_age_days == 0:
        console.print(hint("loupe config set retention.max_age_days 30    auto-purge after 30d"))
    if not cfg.encryption_enabled:
        console.print(hint("loupe config set encryption.enabled true      encrypt at rest"))
    console.print(hint("loupe config list           every setting + its value"))
    console.print(hint("loupe doctor                deep health check"))
    console.print()


def _human_relative(secs: float) -> str:
    """Format a seconds-ago duration like ``2m ago`` / ``3h ago`` / ``5d ago``."""
    s = max(0, int(secs))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


# ---------------------------------------------------------------------------
# `loupe config` — read + write ~/.loupe/config.toml programmatically.
# Designed so users never have to "edit the TOML by hand" for retention,
# redaction, encryption, sampling, or attribution defaults.
# ---------------------------------------------------------------------------


config_app = typer.Typer(
    name="config",
    help="View and edit ~/.loupe/config.toml without opening an editor.",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config", rich_help_panel=_GROUP_INFRA)


# All settable keys live in this table — keeps `set` honest + lets
# `list` enumerate the full surface. Each entry: (dotted-key, type, doc).
_CONFIG_KEYS: list[tuple[str, str, str]] = [
    ("default.provider",          "str",  "Default LLM provider"),
    ("default.model",             "str",  "Default model"),
    ("attribution.backend",       "str",  "Attribution backend (mock | sae)"),
    ("index.disabled",            "bool", "Disable the DuckDB index"),
    ("updates.check_on_startup",  "bool", "Check for updates on `loupe` startup"),
    ("retention.max_age_days",    "int",  "Auto-purge traces older than N days (0 = off)"),
    ("retention.keep_tagged",     "bool", "Skip annotated traces when auto-purging"),
    ("encryption.enabled",        "bool", "Encrypt new JSONL traces at rest"),
]


def _split_config_key(key: str) -> tuple[str, str]:
    if "." not in key:
        raise typer.BadParameter(
            f"config key must be of form 'section.key', got {key!r}"
        )
    section, _, sub = key.partition(".")
    return section.strip(), sub.strip()


def _coerce_config_value(raw: str, declared_type: str) -> Any:
    """Coerce a CLI-typed string into the type the config key expects."""
    v = raw.strip()
    if declared_type == "bool":
        if v.lower() in ("true", "1", "yes", "on"):
            return True
        if v.lower() in ("false", "0", "no", "off"):
            return False
        raise typer.BadParameter(f"expected bool, got {raw!r}")
    if declared_type == "int":
        try:
            return int(v)
        except ValueError as exc:
            raise typer.BadParameter(f"expected int, got {raw!r}") from exc
    return v


@config_app.command("path")
def config_path_cmd() -> None:
    """Print the absolute path of the config file."""
    from loupe.config import config_path
    console.print(str(config_path()))


@config_app.command("list")
def config_list() -> None:
    """List every settable key with its current value + description."""
    from loupe.config import Config
    cfg = Config.load()

    # Pull live values via direct attribute mapping. The Config dataclass
    # uses snake-case attribute names; the dotted config-keys map cleanly.
    def _get(dotted: str) -> Any:
        section, sub = _split_config_key(dotted)
        # Map "retention.max_age_days" → cfg.retention_max_age_days etc.
        attr = f"{section}_{sub}" if section in (
            "retention", "encryption", "updates", "index", "attribution",
        ) else None
        if section == "default" and sub == "provider":
            return cfg.default_provider
        if section == "default" and sub == "model":
            return cfg.default_model
        if section == "attribution" and sub == "backend":
            return cfg.attribution_backend
        if section == "index" and sub == "disabled":
            return cfg.index_disabled
        if section == "updates" and sub == "check_on_startup":
            return cfg.check_for_updates
        if attr and hasattr(cfg, attr):
            return getattr(cfg, attr)
        return "?"

    render_padded(
        banner("config", version=__version__),
        kv_table([
            (key, f"{_get(key)}  · {doc}")
            for key, _t, doc in _CONFIG_KEYS
        ]),
    )
    console.print()
    # Lists (redact.patterns, providers) get their own readable table since
    # KV-table truncates long lists.
    if cfg.redact_patterns:
        console.print(section("redact.patterns"))
        console.print()
        for pat in cfg.redact_patterns:
            console.print(Text(f"    • {pat}", style=AMBER))
        console.print()
    else:
        console.print(
            Text("  redact.patterns: ", style=DIM)
            + Text("(none — using built-in credential scanners only)", style=DIM)
        )
        console.print()
    if cfg.providers:
        console.print(section("providers (api keys set)"))
        console.print()
        for name in sorted(cfg.providers):
            p = cfg.providers[name]
            mark = "●" if p.api_key else "○"
            console.print(
                Text(f"    {mark} {name}", style=AMBER if p.api_key else DIM)
            )
        console.print()


@config_app.command("get")
def config_get(key: str) -> None:
    """Print one config value. Example: `loupe config get retention.max_age_days`."""
    from loupe.config import Config
    cfg = Config.load()
    # Reuse the same mapping as `list`.
    section, sub = _split_config_key(key)
    attr_map = {
        ("default", "provider"):    cfg.default_provider,
        ("default", "model"):       cfg.default_model,
        ("attribution", "backend"): cfg.attribution_backend,
        ("index", "disabled"):      cfg.index_disabled,
        ("updates", "check_on_startup"): cfg.check_for_updates,
        ("retention", "max_age_days"):   cfg.retention_max_age_days,
        ("retention", "keep_tagged"):    cfg.retention_keep_tagged,
        ("encryption", "enabled"):       cfg.encryption_enabled,
    }
    if (section, sub) not in attr_map:
        console.print(Text(f"  ✗ unknown key {key!r}", style=RED))
        console.print(hint("loupe config list    every settable key"))
        raise typer.Exit(code=1)
    console.print(str(attr_map[(section, sub)]))


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Dotted key — e.g. retention.max_age_days"),
    value: str = typer.Argument(..., help="New value. Booleans accept true/false/on/off."),
) -> None:
    """Update one config value. Writes ~/.loupe/config.toml atomically.

    Examples::

        loupe config set retention.max_age_days 30
        loupe config set retention.keep_tagged true
        loupe config set encryption.enabled true
        loupe config set default.model claude-sonnet-4-5
    """
    section, sub = _split_config_key(key)
    # Look up the declared type so we coerce correctly.
    declared = next(
        (t for (k, t, _) in _CONFIG_KEYS if k == key), None,
    )
    if declared is None:
        console.print(Text(f"  ✗ {key!r} is not a settable key", style=RED))
        console.print(hint("loupe config list    every settable key"))
        raise typer.Exit(code=1)
    coerced = _coerce_config_value(value, declared)

    from loupe.config import Config
    cfg = Config.load()
    # Mutate via builder pattern: build a kwargs dict that overrides only
    # the field we changed.
    field_map = {
        ("default", "provider"):    "default_provider",
        ("default", "model"):       "default_model",
        ("attribution", "backend"): "attribution_backend",
        ("index", "disabled"):      "index_disabled",
        ("updates", "check_on_startup"): "check_for_updates",
        ("retention", "max_age_days"):   "retention_max_age_days",
        ("retention", "keep_tagged"):    "retention_keep_tagged",
        ("encryption", "enabled"):       "encryption_enabled",
    }
    field = field_map[(section, sub)]
    # Construct a new Config with the overridden field.
    new_kwargs: dict[str, Any] = {
        "default_provider":    cfg.default_provider,
        "default_model":       cfg.default_model,
        "providers":           dict(cfg.providers),
        "attribution_backend": cfg.attribution_backend,
        "index_disabled":      cfg.index_disabled,
        "check_for_updates":   cfg.check_for_updates,
        "retention_max_age_days": cfg.retention_max_age_days,
        "retention_keep_tagged":  cfg.retention_keep_tagged,
        "redact_patterns":     list(cfg.redact_patterns),
        "encryption_enabled":  cfg.encryption_enabled,
        "_path":               cfg._path,
    }
    new_kwargs[field] = coerced
    new_cfg = Config(**new_kwargs)
    saved_at = new_cfg.save()

    console.print(
        Text("  ✓ ", style=GREEN)
        + Text(f"{key} = ", style=INK)
        + Text(repr(coerced), style=AMBER)
        + Text(f"  → {saved_at}", style=DIM)
    )


@config_app.command("add-redact")
def config_add_redact(
    pattern: str = typer.Argument(..., help="Regex to redact in captured values."),
) -> None:
    """Append a custom redaction pattern to ``[redact.patterns]``."""
    import re
    try:
        re.compile(pattern)
    except re.error as exc:
        console.print(Text(f"  ✗ invalid regex: {exc}", style=RED))
        raise typer.Exit(code=1) from None

    from loupe.config import Config
    cfg = Config.load()
    if pattern in cfg.redact_patterns:
        console.print(Text("  ◉ pattern already present.", style=DIM))
        return
    new_patterns = [*cfg.redact_patterns, pattern]
    next_cfg = Config(
        default_provider=cfg.default_provider,
        default_model=cfg.default_model,
        providers=dict(cfg.providers),
        attribution_backend=cfg.attribution_backend,
        index_disabled=cfg.index_disabled,
        check_for_updates=cfg.check_for_updates,
        retention_max_age_days=cfg.retention_max_age_days,
        retention_keep_tagged=cfg.retention_keep_tagged,
        redact_patterns=new_patterns,
        encryption_enabled=cfg.encryption_enabled,
        _path=cfg._path,
    )
    saved = next_cfg.save()
    console.print(
        Text("  ✓ ", style=GREEN)
        + Text("added redaction pattern  ", style=INK)
        + Text(f"({len(new_patterns)} total)", style=DIM)
        + Text(f"\n  → {saved}", style=DIM)
    )
    # Invalidate the redactor cache so subsequent captures pick up the change.
    from loupe import _redact
    _redact._reset_custom_pattern_cache()


# ----------------------------------------------------------------------------
# `loupe index` subcommands — manage the DuckDB query index
# ----------------------------------------------------------------------------

index_app = typer.Typer(
    name="index",
    help="Manage the DuckDB query index over your captured traces.",
    no_args_is_help=True,
)
app.add_typer(index_app, name="index", rich_help_panel=_GROUP_INFRA)


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
    """Return the canonical JSON Schema path.

    The schema ships inside the wheel at ``loupe/_data/loupe-trace.schema.json``,
    so this is always the same file the dashboard, validator, and SDK use.
    """
    here = Path(__file__).resolve()
    candidate = here.parent / "_data" / "loupe-trace.schema.json"
    return candidate if candidate.exists() else None


def _missing_trace_id_hint(command: str, *, extra_examples: list[str] | None = None) -> None:
    """Print a friendly "which trace?" error + actionable hints.

    Shared by every command that needs a trace id (``show``, ``report``,
    ``tag``, ``annotations``, ``diff``, ``steer``, ``causal``). Always
    suggests ``loupe list`` so the user can copy a real id and re-run.
    Also peeks at the most recent trace and uses its id in the example,
    so the suggestion is paste-ready.
    """
    traces_dir = _default_dir() / "traces"
    sample_id = ""
    if traces_dir.exists():
        traces = sorted(
            traces_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if traces:
            sample_id = traces[0].stem[:12]

    console.print()
    console.print(
        Text("  ◉ ", style=AMBER)
        + Text(f"loupe {command} needs a trace id.", style=INK)
    )
    console.print()
    if sample_id:
        console.print(hint(f"loupe {command} {sample_id}    "
                           f"your most recent trace"))
    else:
        console.print(hint(f"loupe {command} <trace-id>     "
                           f"requires at least 4 chars; prefix is fine"))
    for ex in extra_examples or []:
        console.print(hint(ex))
    console.print(hint("loupe list                      every captured trace"))
    console.print()


def _find_trace(trace_id: str) -> Path | None:
    """Resolve a trace prefix to a JSONL file, with actionable error hints.

    Reasons this can return None:
      - The id doesn't match any file → we suggest `loupe list`.
      - The traces directory doesn't exist → we point at `loupe init`.
    """
    traces_dir = _default_dir() / "traces"
    matches = list(traces_dir.glob(f"{trace_id}*.jsonl")) if traces_dir.exists() else []
    if not matches:
        console.print(Text(f"  ✗ No trace matching '{trace_id}'", style=RED))
        any_traces = traces_dir.exists() and any(traces_dir.glob("*.jsonl"))
        if any_traces:
            console.print(hint("loupe list             see every captured trace"))
        else:
            console.print(hint("loupe init my-agent    scaffold a real starter project"))
            console.print(hint("python agent.py 'q'    capture your first trace"))
        return None
    if len(matches) > 1:
        console.print(Text(f"  Multiple matches; picking {matches[0].stem}", style=AMBER))
        console.print(Text("  pass a longer prefix to disambiguate.", style=DIM))
    return matches[0]


def _read_header(path: Path) -> dict | None:
    """Thin shim over ``loupe.store.read_trace_header``."""
    from loupe.store import read_trace_header
    return read_trace_header(path)


def _registered_command_names() -> list[str]:
    """Best-effort introspection of every command name registered on the
    top-level Typer app. Used by the typo-suggestion shim."""
    names: list[str] = []
    for entry in getattr(app, "registered_commands", []):
        if entry.name:
            names.append(entry.name)
        elif entry.callback is not None:
            names.append(entry.callback.__name__.replace("_", "-"))
    for group in getattr(app, "registered_groups", []):
        if group.name:
            names.append(group.name)
    return sorted(set(names))


def main_entry() -> None:
    """``loupe`` CLI entry point with friendly typo suggestions.

    Behaviour:

    - Unknown top-level subcommand → print
      ``✗ unknown command 'sho'.  Did you mean: show?`` and exit 1
      WITHOUT invoking Typer (which would otherwise emit a noisy
      "Usage: …" block).
    - Everything else is delegated to the regular Typer app.
    """
    if len(sys.argv) >= 2:
        first = sys.argv[1]
        if first and not first.startswith("-"):
            known = _registered_command_names()
            if first not in known:
                import difflib
                matches = difflib.get_close_matches(first, known, n=3, cutoff=0.55)
                console.print()
                console.print(Text(f"  ✗ unknown command '{first}'", style=RED))
                if matches:
                    if len(matches) == 1:
                        console.print(
                            Text("    Did you mean ", style=DIM)
                            + Text(matches[0], style=AMBER)
                            + Text("?", style=DIM)
                        )
                    else:
                        console.print(
                            Text("    Did you mean: ", style=DIM)
                            + Text(", ".join(matches), style=AMBER)
                            + Text("?", style=DIM)
                        )
                console.print(hint("loupe --help    full list of commands"))
                console.print()
                sys.exit(1)
    app()


if __name__ == "__main__":
    main_entry()
