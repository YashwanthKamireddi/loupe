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
    Configured but no traces           → suggest `loupe try`
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
        console.print()
        console.print(Text("  Welcome to Loupe.", style=f"bold {AMBER}"))
        console.print(
            Text(
                "  Let's get you set up — takes about 90 seconds.",
                style=DIM,
            )
        )
        console.print()
        _run_setup()
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


@app.command("setup", rich_help_panel=_GROUP_GET_STARTED)
def setup(
    provider: str = typer.Option(
        "", "--provider", "-p",
        help="Provider to configure (gemini, anthropic, openai). "
             "Omit to be asked interactively.",
    ),
    api_key: str = typer.Option(
        "", "--api-key", "-k",
        help="Pre-supplied API key (skips the interactive paste step).",
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser",
        help="Don't auto-open the browser to the provider's key-creation page.",
    ),
) -> None:
    """Configure your first LLM provider — interactive or scripted.

    The wizard:
      1. Detects existing keys in env vars (you may already be set up).
      2. Asks which provider you want to configure (gemini, anthropic, openai).
      3. Opens the browser to the right key-creation page.
      4. Prompts for the key (no shell quoting).
      5. Persists to ``~/.loupe/config.toml`` (file is +0600 / your-only-read).
      6. Tests the key with a tiny ping call.
      7. Sets your default provider + model.

    All steps are skippable via flags for scripted use, e.g. CI:

        loupe setup --provider gemini --api-key "$GEMINI_KEY" --no-browser
    """
    _run_setup(
        forced_provider=provider or None,
        forced_key=api_key or None,
        open_browser=not no_browser,
    )


def _run_setup(
    *,
    forced_provider: str | None = None,
    forced_key: str | None = None,
    open_browser: bool = True,
) -> None:
    """Implementation of the setup wizard. Pure function-style for testing."""
    from loupe.config import Config

    cfg = Config.load()
    already = cfg.configured_providers()

    if already and forced_provider is None:
        console.print(
            Text("  ✓ ", style=GREEN)
            + Text("you're already set up:", style=INK)
        )
        for name in already:
            source = "env var" if any(
                os.environ.get(k) for k in (
                    "GEMINI_API_KEY", "GOOGLE_API_KEY",
                    "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                ) if name in k.lower()
            ) else "config file"
            console.print(
                Text(f"    • {name}", style=AMBER)
                + Text(f"  ({source})", style=DIM)
            )
        console.print()
        console.print(hint("loupe try                   one-shot demo trace"))
        console.print(hint("loupe ui                    open the dashboard"))
        return

    # Pick a provider.
    provider = forced_provider or _prompt_provider()
    if provider not in {"gemini", "anthropic", "openai"}:
        console.print(
            Text(f"  ✗ unknown provider {provider!r}; pick gemini, anthropic, or openai.",
                 style=RED)
        )
        raise typer.Exit(code=1)

    key_pages = {
        "gemini":    "https://aistudio.google.com/apikey",
        "anthropic": "https://console.anthropic.com/settings/keys",
        "openai":    "https://platform.openai.com/api-keys",
    }
    default_models = {
        "gemini":    "gemini-2.5-flash",
        "anthropic": "claude-haiku-4-5-20251001",
        "openai":    "gpt-4o-mini",
    }
    key_format_hints = {
        "gemini":    "AIza…",
        "anthropic": "sk-ant-…",
        "openai":    "sk-…",
    }
    url = key_pages[provider]

    # Open the browser (best-effort, never blocks).
    if open_browser:
        import contextlib
        import webbrowser
        console.print(
            Text("  → opening ", style=DIM)
            + Text(url, style=AMBER)
            + Text(" in your browser…", style=DIM)
        )
        with contextlib.suppress(Exception):
            webbrowser.open(url, new=1)

    # Prompt for the key.
    if forced_key is not None:
        api_key = forced_key
    else:
        console.print()
        console.print(
            Text("  Paste your ", style=DIM)
            + Text(provider, style=AMBER)
            + Text(f" key here  (format: {key_format_hints[provider]}):", style=DIM)
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
    new_cfg = cfg.set_provider_key(provider, api_key).with_default(
        provider=provider,
        model=default_models[provider],
    )
    saved_at = new_cfg.save()

    console.print()
    console.print(
        Text("  ✓ ", style=GREEN)
        + Text("saved to ", style=INK)
        + Text(str(saved_at), style=AMBER)
    )

    # Ping the API to confirm the key works.
    ok, detail = _ping_provider(provider, api_key, default_models[provider])
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

    # Optional: offer zero-code auto-capture as the last step of setup.
    # Only when running interactively — CI / piped contexts skip.
    if (
        sys.stdin.isatty() and sys.stdout.isatty()
        and not os.environ.get("LOUPE_AUTOPATCH")
    ):
        console.print()
        console.print(section("Zero-code auto-capture (recommended)"))
        console.print(
            Text(
                "  When LOUPE_AUTOPATCH=1 is set, every Python script you\n"
                "  run captures its LLM calls automatically — no @trace,\n"
                "  no patch_all(), no `loupe run` prefix. Run `loupe explain\n"
                "  autopatch` later for the details.",
                style=DIM,
            )
        )
        try:
            answer = input("    Enable autopatch in this shell session? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer in ("", "y", "yes"):
            os.environ["LOUPE_AUTOPATCH"] = "1"
            console.print(
                Text("    ✓ enabled for this session.", style=GREEN)
            )
            console.print(
                Text(
                    "    To make it permanent across shells, add to your shell rc:",
                    style=DIM,
                )
            )
            console.print(cmd("set -Ux LOUPE_AUTOPATCH 1        # fish"))
            console.print(cmd("export LOUPE_AUTOPATCH=1         # bash / zsh"))

    console.print()
    console.print(section("Next"))
    console.print()
    console.print(hint("loupe try                   one-shot demo trace"))
    console.print(hint("loupe ask 'your question'   one captured call"))
    console.print(hint("loupe chat                  interactive REPL"))
    console.print(hint("loupe ui                    open the dashboard"))
    console.print(hint("loupe explain autopatch     zero-code capture details"))
    console.print()


def _prompt_provider() -> str:
    """Ask the user which provider to configure. Defaults to gemini."""
    console.print(section("Pick a provider"))
    console.print()
    options = [
        ("1. gemini      ", "free tier available · fastest path to a first trace"),
        ("2. anthropic   ", "Claude · best for production-quality agent runs"),
        ("3. openai      ", "GPT-4o, o-series · widest framework support"),
    ]
    for label, desc in options:
        console.print(Text(f"    {label}", style=AMBER) + Text(desc, style=DIM))
    console.print()
    try:
        choice = input("    your pick [1-3, default 1] › ").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        console.print(Text("\n  ✗ setup cancelled.", style=RED))
        raise typer.Exit(code=1) from None
    return {"1": "gemini", "2": "anthropic", "3": "openai"}.get(choice, choice.lower())


def _ping_provider(provider: str, api_key: str, model: str) -> tuple[bool, str]:
    """Send a tiny request to confirm the key works. Returns (ok, message).

    Best-effort: any exception during the ping is reported as a soft fail,
    not a hard error — the key is still saved.
    """
    try:
        if provider == "gemini":
            from google import genai
            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model=model, contents="ping",
            )
            return True, f"model {model} responded ({len(resp.text or '')} chars)"
        if provider == "anthropic":
            import anthropic
            anthropic_client = anthropic.Anthropic(api_key=api_key)
            ant_resp = anthropic_client.messages.create(
                model=model, max_tokens=8,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True, f"model {model} responded ({ant_resp.usage.output_tokens} tokens)"
        if provider == "openai":
            from openai import OpenAI
            openai_client = OpenAI(api_key=api_key)
            oai_resp = openai_client.chat.completions.create(
                model=model, max_tokens=8,
                messages=[{"role": "user", "content": "ping"}],
            )
            tok = oai_resp.usage.completion_tokens if oai_resp.usage else 0
            return True, f"model {model} responded ({tok} tokens)"
    except ImportError as exc:
        return False, f"SDK not installed ({exc.name}). pip install '{exc.name}'."
    except Exception as exc:  # noqa: BLE001 — show provider error verbatim
        return False, str(exc)[:160]
    return False, f"no ping implementation for {provider}"


@app.command("try", rich_help_panel=_GROUP_GET_STARTED)
def try_cmd(
    model: str = typer.Option(
        "", "--model",
        help="Override the model. Default: your configured default.",
    ),
) -> None:
    """One-shot demo — sends a canned prompt with your configured provider,
    captures the trace, prints the answer, suggests `loupe ui`.

    The "it works on my machine" proof, after `loupe setup`. Costs less
    than a fraction of a cent.
    """
    from loupe.config import Config

    _ensure_provider_or_setup("try the demo")
    cfg = Config.load()
    providers = cfg.configured_providers()

    provider = cfg.default_provider if cfg.default_provider in providers else providers[0]
    chosen_model = model or _default_model_for(provider)
    api_key = cfg.api_key_for(provider)
    assert api_key, "configured_providers said yes but api_key_for says no — bug"

    question = (
        "Reply in one sentence: what's one thing AI agent observability "
        "should never compromise on?"
    )

    from loupe import record_step
    from loupe import trace as trace_decorator
    from loupe.integrations import patch_all

    patch_all()

    @trace_decorator(name="loupe-try", framework=provider)
    def _run() -> str:
        record_step(
            "plan", "loupe try demo",
            outputs={"q": question, "provider": provider, "model": chosen_model},
        )
        text = _invoke_provider(provider, api_key, chosen_model, question)
        record_step("final", "got reply", outputs={"text": text[:300]})
        return text

    console.print()
    console.print(
        Text("  ◉ ", style=AMBER)
        + Text("calling ", style=DIM)
        + Text(f"{provider}:{chosen_model}", style=INK)
    )
    console.print(
        Text("    prompt: ", style=DIM)
        + Text(question, style=INK)
    )
    console.print()

    text: str = ""
    try:
        with spinner("Capturing"):
            text = _run()
    except Exception as exc:  # noqa: BLE001
        console.print(Text(f"  ✗ {exc}", style=RED))
        console.print(hint("loupe setup    re-check your provider key"))
        raise typer.Exit(code=1) from None

    console.print(
        Text("  ✓ ", style=GREEN)
        + Text(text.strip()[:280], style=INK)
    )
    console.print()
    console.print(section("Next"))
    console.print()
    console.print(hint("loupe ui                   open the dashboard"))
    console.print(hint("loupe list                 see every captured trace"))
    console.print(hint("loupe ask '<question>'     one more captured call"))
    console.print()


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
    return {
        "gemini":    "gemini-2.5-flash",
        "anthropic": "claude-haiku-4-5-20251001",
        "openai":    "gpt-4o-mini",
    }.get(provider, "gemini-2.5-flash")


def _invoke_provider(provider: str, api_key: str, model: str, prompt: str) -> str:
    """Synchronous single-shot call to any supported provider."""
    return _invoke_with_history(
        provider, api_key, model,
        [{"role": "user", "content": prompt}],
    )


def _invoke_with_history(
    provider: str, api_key: str, model: str,
    history: list[dict[str, str]],
) -> str:
    """Multi-turn invocation. ``history`` is a list of ``{role, content}``
    messages with roles ``user`` / ``assistant`` — provider-translated
    internally so callers don't see per-SDK shape differences.

    The single-shot ``_invoke_provider`` above delegates to this so
    every provider has exactly one call site for both modes.
    """
    if provider in ("gemini", "google"):
        from google import genai
        client = genai.Client(api_key=api_key)
        contents = [
            {
                "role": "user" if m["role"] == "user" else "model",
                "parts": [{"text": m["content"]}],
            }
            for m in history
        ]
        return client.models.generate_content(model=model, contents=contents).text or ""
    if provider == "anthropic":
        import anthropic
        anthropic_client = anthropic.Anthropic(api_key=api_key)
        ant_resp = anthropic_client.messages.create(
            model=model, max_tokens=1024,
            messages=[
                # The Anthropic SDK's TypedDict has role: Literal["user",
                # "assistant"] — we trust the caller to feed valid roles.
                {"role": m["role"], "content": m["content"]}  # type: ignore[typeddict-item, misc]
                for m in history
            ],
        )
        return "".join(
            getattr(b, "text", "") for b in (ant_resp.content or [])
            if getattr(b, "type", None) == "text"
        )
    if provider == "openai":
        from openai import OpenAI
        openai_client = OpenAI(api_key=api_key)
        oai_resp = openai_client.chat.completions.create(
            model=model, max_tokens=1024,
            messages=[
                {"role": m["role"], "content": m["content"]}  # type: ignore[misc]
                for m in history
            ],
        )
        return oai_resp.choices[0].message.content or ""
    raise RuntimeError(f"no invoker for provider {provider!r}")


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
        text = _invoke_provider(provider, api_key, chosen_model, prompt)
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
                    import json as _json
                    for line in reversed(files[0].read_text().splitlines()):
                        obj = _json.loads(line)
                        if obj.get("_type") == "step" and obj.get("kind") == "llm-call":
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
        import json as _json
        for line in path.open():
            obj = _json.loads(line)
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


@app.command("run", rich_help_panel=_GROUP_USE)
def run_script(
    args: list[str] = typer.Argument(
        None,
        help="Python script + its arguments. Example: loupe run my_agent.py 'hello'",
    ),
) -> None:
    """Run a Python script with every LLM call auto-captured.

    Loupe activates ``patch_all()`` before your script imports anything,
    wraps the whole execution in a Loupe trace, and writes one JSONL
    file per run to ``~/.loupe/traces/``. **No source edits needed in
    the script** — bring your own agent code as-is.

    ::

        loupe run my_agent.py "What is observability?"
        loupe run scripts/eval.py --eval-set my_set

    The script's own ``sys.argv`` is preserved as if you had called
    ``python my_agent.py …`` directly.
    """
    import runpy

    from loupe import record_step
    from loupe import trace as trace_decorator
    from loupe.integrations import patch_all

    if not args:
        console.print()
        console.print(
            Text("  ◉ ", style=AMBER)
            + Text("which Python script should Loupe run?", style=INK)
        )
        console.print(
            Text("    Loupe activates patch_all() before your script imports", style=DIM)
        )
        console.print(
            Text("    anything, so every LLM call is captured automatically.", style=DIM)
        )
        console.print()
        console.print(cmd("loupe run my_agent.py 'your question'"))
        console.print(cmd("loupe run scripts/eval.py --dataset my_set"))
        console.print()
        console.print(hint("loupe init my-agent    scaffold a starter project first"))
        console.print(hint("loupe explain run      deeper explanation"))
        console.print()
        raise typer.Exit(code=1)

    script = args[0]
    script_path = Path(script)
    if not script_path.exists():
        console.print(Text(f"  ✗ no such file: {script}", style=RED))
        console.print(hint("loupe init <name>     scaffold a starter project"))
        raise typer.Exit(code=1)

    name = script_path.stem

    # Replace sys.argv with the script's view of the world — exactly what
    # `python script.py args...` would see.
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
        # Honor the script's exit code; the trace is still captured.
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


@app.command("start", rich_help_panel=_GROUP_INSPECT)
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
    trace_id: str,
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
        if as_json:
            typer.echo(json.dumps({"error": "malformed trace"}))
        else:
            console.print(Text("malformed trace", style=RED))
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
    console.print()


# ----------------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------------


@app.command("ui", rich_help_panel=_GROUP_INSPECT)
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


@app.command("tag", rich_help_panel=_GROUP_ANALYZE)
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


@app.command("untag", rich_help_panel=_GROUP_ANALYZE)
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


@app.command("annotations", rich_help_panel=_GROUP_INSPECT)
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


@app.command("export", rich_help_panel=_GROUP_ANALYZE)
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


@app.command("report", rich_help_panel=_GROUP_ANALYZE)
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


@app.command("init", rich_help_panel=_GROUP_GET_STARTED)
def init(
    name: str = typer.Argument(..., help="Project / agent name"),
    target: Path = typer.Option(Path("."), "--dir", "-d"),
) -> None:
    """Scaffold a Loupe-instrumented starter project."""
    project_dir = target / name if target == Path(".") else target
    if project_dir.exists() and any(project_dir.iterdir()):
        console.print(Text(f"  ✗ Refusing to write into non-empty {project_dir}", style=RED))
        console.print(hint(f"loupe init {name}-new        scaffold under a different name"))
        console.print(hint(f"rm -rf {project_dir}    if you really want to overwrite"))
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


@app.command("doctor", rich_help_panel=_GROUP_INFRA)
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


@app.command("diff", rich_help_panel=_GROUP_INSPECT)
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

    for path in sorted(traces_dir.glob("*.jsonl")):
        trace_count += 1
        for line in path.open(encoding="utf-8"):
            try:
                obj = _json.loads(line)
            except _json.JSONDecodeError:
                continue
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
) -> None:
    """Replay every tagged failure as a regression test.

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
    """
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
    import json as _json

    header: dict | None = None
    prompt = ""
    model = ""
    framework = ""

    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = _json.loads(line)
            kind = obj.pop("_type", None)
            if kind == "trace":
                header = obj
                framework = header.get("framework") or ""
                continue
            if kind != "step":
                continue

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
        "Loupe stores user-level settings (API keys, default model,\n"
        "attribution backend) in ~/.loupe/config.toml.\n\n"
        "Env vars still work as ephemeral overrides:\n"
        "  GEMINI_API_KEY=... loupe ask 'hi'\n\n"
        "Edit by hand or run `loupe setup` for the guided wizard."
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
    "autopatch": (
        "Zero-code auto-capture. Set one env var and every Python script\n"
        "you run captures its LLM calls automatically — no @trace, no\n"
        "patch_all(), no `loupe run` prefix.\n\n"
        "    set -Ux LOUPE_AUTOPATCH 1     # fish, persistent\n"
        "    export LOUPE_AUTOPATCH=1      # bash / zsh\n\n"
        "How it works:\n"
        "  Loupe ships a .pth file at install time that runs on every\n"
        "  Python startup. The hook checks LOUPE_AUTOPATCH; if set, it\n"
        "  calls patch_all() and enables implicit-trace mode in the\n"
        "  universal-httpx interceptor. Every LLM call to a recognized\n"
        "  provider lands as its own one-call trace.\n\n"
        "Cost when off:\n"
        "  ~1 µs at Python startup. One os.environ lookup, then return.\n\n"
        "Cost when on:\n"
        "  +20-40 ms at startup (imports loupe.integrations). Per-call\n"
        "  overhead is the same as @trace: <100 µs/step, <5 ms/trace.\n\n"
        "Opt out per-process by unsetting the var:\n"
        "    LOUPE_AUTOPATCH= python my_agent.py"
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
    try:
        with path.open() as f:
            first = json.loads(next(f))
        if first.get("_type") != "trace":
            return None
        return first
    except (StopIteration, json.JSONDecodeError, KeyError):
        return None


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
