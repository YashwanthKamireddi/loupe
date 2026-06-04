# Changelog

All notable changes to Loupe. Loupe follows [SemVer](https://semver.org/).

## [Unreleased]

### Planned for 0.1.0
- Cluster analysis across larger annotated corpora (hierarchical, not just frequency)
- Phase C: multi-trace bulk operations in the dashboard
- Phase D: time-series cost + activity view in the dashboard

## [0.0.78] — 2026-06-03  ·  **Dashboard tour returns (opt-in) + Gemma-2-2b local demo**

- **Opt-in dashboard tour.** A small `tour` button in the topbar
  opens a 5-step coachmark overlay that points at the sidebar →
  Cluster chip → evidence pane → live indicator → 14-day sparkline.
  Never auto-launches, never blocks the main surface, ESC / skip /
  click-outside dismisses. Replaces the old auto-launching first-run
  tour that was stripped as a gimmick in v0.0.68 — same value,
  controlled by the user.
- **`examples/gemma_local_demo.py`** — a real ReAct agent that runs
  against **Gemma-2-2b via local Ollama**. Because Gemma-2-2b is the
  exact model the GemmaScope SAE was trained on, running
  ``loupe attribute --sae gemma-2-2b <trace>`` on this trace gives
  features that ARE what fired in the model — not an approximation
  through a smaller surrogate. This is the canonical "real SAE
  attribution on a real agent" path.

## [0.0.77] — 2026-06-03  ·  **Research-grade pivot: cluster view in dashboard + LoupeBench v0.1 grows**

This is the start of Loupe's repositioning as **the open-source
forensic + interpretability tool for the LLM-agent research community**
(MATS scholars, Anthropic Fellows, Apollo Research, EleutherAI). The
direct framing — "Loupe is what you reach for when you want SAE-level
mechanistic answers about an agent failure" — supplants any attempt to
compete with LangSmith on production observability.

- **Cluster view in `loupe ui`** — a new `◇ Cluster` chip in the
  sidebar opens a dashboard pane that runs `compute_cluster()` against
  every tagged annotation: a *frequency* table (which SAE features fire
  across many traces of a category) and a *distinctiveness* table
  (features over-represented in a category vs every other category,
  scored by smoothed log-ratio). Apollo Research explicitly asked for
  this in their public [45-project-ideas
  list](https://www.lesswrong.com/posts/KfkpgXdgRheSRWDy8/a-list-of-45-mech-interp-project-ideas-from-apollo-research)
  ("Nice programming API to attribute an input to a collection of
  paths … Web user interface? Maybe in collaboration with neuronpedia.").
- **`compute_cluster()`** is now a public function in `loupe.attribution`
  — the dashboard `/api/cluster` endpoint and the existing
  `loupe cluster` CLI return the *exact same numbers*.
- **LoupeBench v0.1 grew from 5 to 8 hand-annotated entries** with
  three real-world failures captured + annotated this session:
  - `lb-tool-hallucination-006`: Gemini agent had `calc` + `count_letters`
    tools available, fabricated the entire ReAct loop including
    invented OBSERVATIONs (count_letters returned "26" — real answer
    is 27). Wrong final answer. Classic tool-hallucination, captured
    end-to-end.
  - `lb-rate-limit-007`: Real Gemini 2.5-flash 429 mid-agent run; full
    `RetryInfo` payload preserved (`retry-after: 45.097s`, exact
    `quotaMetric`) so the agent harness *could* recover. Real
    production agents typically log just `RateLimitError` and lose
    this signal.
  - `lb-deprecated-model-008`: browser-use's default LLM pointed at
    `gemini-2.0-flash-exp` which Google deprecated; 6 retries each
    hitting the same 404. Loupe captured all 6 with the full
    `models/<name> is not found for API version v1beta` body, so the
    retry-storm is debuggable.
- **`bench/CONTRIBUTING.md`** — entry schema + redaction guidance + a
  citation block. Sets the bar for what makes a strong LoupeBench
  contribution.

No behavior changes to the core capture path. CLI surface unchanged.

## [0.0.76] — 2026-06-03  ·  **Two real bugs fixed by running on real third-party agents**

Discovered by actually running Loupe end-to-end against the browser-use
OSS agent (96.9k *) and openai SDK pointed at Google's OpenAI-compatible
Gemini endpoint. Both were silently breaking "zero-code capture" — the
core pitch of the project — and got past every existing test.

**Fix 1 — localhost CDP traffic no longer captured as fake `llm-call`.**
The `local-ip` / `localhost` / `0.0.0.0` providers (registered for
Ollama / vLLM / LM Studio users) were too greedy: any HTTP traffic to
127.0.0.1 — Playwright Chrome DevTools Protocol, local dev servers,
mDNS, health checks — landed as bogus `llm-call` rows. Now the
classifier requires an OpenAI-shaped body (`messages` + `model` list)
before capturing localhost calls. Genuine Ollama traffic still flows.
Regression test: `test_localhost_only_captured_when_body_looks_llm_shaped`.

**Fix 2 — direct integrations now respect `LOUPE_AUTOPATCH=1`.**
When `openai` (or anthropic, langchain, ...) was installed, Loupe's
direct-SDK integration set `direct_capture_active=True` during calls —
correctly suppressing the universal httpx layer — but its own
`_emit_single` *silently returned* when no `@trace` decorator was on
the stack. So `pip install loupe-ai openai && LOUPE_AUTOPATCH=1 python
my_agent.py` captured nothing. Now every direct integration wraps the
call site with `ensure_implicit_trace_if_autopatch`, mirroring the
universal-httpx behavior — every path leads to a trace.

**New `examples/multistep_capture_demo.py`** — a real multi-step
research-style agent (planner → sub-questions → synthesis) hitting
Gemini via the openai SDK pointed at Google's OpenAI-compatible
endpoint. Demonstrates that Loupe captures cleanly when the SDK uses
httpx, and exercises the autopatch-implicit-trace path in production.

**New shared `loupe.integrations._autopatch`** — `autopatch_enabled()`
and `ensure_implicit_trace_if_autopatch()` extracted from the httpx
layer so every direct integration uses one consistent gate.

**Known limitation documented:** the native `google-genai` SDK uses
Google's own HTTP transport (not httpx), so `LOUPE_AUTOPATCH=1` does
NOT capture it. Workarounds: (a) use Gemini through the openai SDK
against Google's OpenAI-compatible endpoint
(`https://generativelanguage.googleapis.com/v1beta/openai/`), or (b)
use `loupe proxy` to MITM-capture at the HTTP layer. A native
`google-genai` integration is a candidate for v0.1.

## [0.0.75] — 2026-06-03  ·  **World-class CLI: `loupe watch`, animated banner, arrow-key setup, sparklines**

Four surgical upgrades that move Loupe's terminal experience from
polished to wow — without breaking scripting, JSON output, or any
existing command.

- **`loupe watch`** — new top-level command: a live forensic dashboard
  in your terminal. Built on Textual; refreshes every 500 ms; tails
  `~/.loupe/traces/*.jsonl` and renders each capture as a one-line
  card. `q` quits · `r` refresh · `f` failed-only filter. Pair with
  `loupe ui` (the FastAPI dashboard) — this is the in-shell counterpart.
- **First-run-only animated banner** — a ~240 ms gradient sweep on the
  wordmark the very first time you run `loupe` interactively. Persists
  a marker at `~/.loupe/.banner-seen` so every subsequent run is the
  static banner. Honors `NO_COLOR`, `CI`, and non-TTY pipes — silent
  by default in scripts.
- **`loupe setup` arrow-key picker** — provider selection now uses a
  proper interactive selector (questionary) with `↑`/`↓` + Enter on a
  TTY; CI / piped runs still get the numbered-input fallback.
- **Inline sparklines on `loupe cost` + `loupe stats`** — a 14-day
  capture-rate / spend sparkline rendered beside the totals. `--json`
  output stays clean (no chart in machine-readable mode).
- **Centralized terminal detection** — new `loupe._term` with
  `is_tty()`, `use_color()`, `use_animation()`. Single source of truth
  for `NO_COLOR` / `FORCE_COLOR` / `CI` respect — replaces inline
  `sys.stdout.isatty()` checks at three call sites.
- **`examples/` directory** — two ready-to-run demo scripts targeting
  notable public OSS agents: `browser_use_demo.py` (96.9k ★) and
  `gpt_researcher_demo.py` (27.5k ★). Run either with
  `LOUPE_AUTOPATCH=1` to see Loupe capture a real agent zero-code.

New required deps (~5 MB total): `textual`, `questionary`. All MIT,
all mature in 2026. Justified by genuine new function (live dashboard,
arrow-key picker) — not decoration.

## [0.0.74] — 2026-05-27  ·  **Correct package repository metadata**

Pointed both packages' `repository`/`Repository` URLs at the real repo
(`github.com/YashwanthKamireddi/loupe`) instead of a placeholder org.
The npm package ships with `--provenance`, which signs the build against
the GitHub source repo, so the metadata now matches the attestation and
the "Repository" links on npm + PyPI resolve correctly. No API changes.

## [0.0.73] — 2026-05-27  ·  **npm publishing: `npm install loupe-ai`**

Wired up npm publishing to match the live PyPI package. The TypeScript
SDK is renamed from the scoped `@loupe/sdk` to unscoped **`loupe-ai`**
— identical to the PyPI name, so it's `pip install loupe-ai` AND
`npm install loupe-ai`, no `@loupe` org to create. Import paths follow
suit: `loupe-ai`, `loupe-ai/universal`, `loupe-ai/autopatch`, etc.
(swept across README + every TS source docstring).

The release workflow's npm job now authenticates with an `NPM_TOKEN`
GitHub secret (the method that reliably publishes a brand-new package
on the first try) while still attaching `--provenance` via OIDC. The
publish is idempotent — re-running an already-published version is a
no-op, never a hard failure — and it's no longer best-effort
(`continue-on-error` removed) now that npm is a real target. PyPI
publishing already landed in v0.0.72 (live at pypi.org/project/loupe-ai).
All four version sources bumped to 0.0.73 in lockstep; 546 Python +
44 TS tests pass.

---

## [0.0.72] — 2026-05-21  ·  **Capture the error body, not just the code**

Found by testing Loupe on a real third-party project (a Gemini /
google-genai FastAPI agent) with no code changes — the universal
httpx interceptor captured the call correctly, proving Loupe works on
any project, any provider, zero instrumentation. But the capture had a
real gap: a failed LLM call (4xx/5xx) recorded only `{"status": 400}`
and **threw away the provider's error message** — exactly the "it just
shows the error code" problem. Now a failed call captures the error
body (`"API key not valid"`, `"rate limit exceeded"`, `"context length
exceeded"`, …) into `outputs.error` AND the step's `error` field, so
`loupe show` and the dashboard lead with the actual cause:

```
  2.   llm-call  gemini:gemini-2.5-flash
        HTTP 400: API key not valid. Please pass a valid API key.
```

New `_extract_error_message` helper handles the OpenAI / Gemini /
Anthropic error envelopes (all nest under `error.message`) with a
stringified-body fallback so nothing is ever lost. Regression test
mirrors the exact real-world Gemini 400.

Same root cause for successful calls: `loupe show` listed step NAMES
but hid the prompt + reply — the actual forensic payload. Now every
llm-call step prints the prompt the model saw and the reply it gave
(handles OpenAI/Anthropic `messages`, Gemini `contents`, and plain
`prompt`/`text` shapes), plus a compact `↳ in N · out M tokens` line.
The dashboard already showed conversation bubbles; the CLI now matches.
546 tests pass.

---

## [0.0.71] — 2026-05-21  ·  **`loupe onboard` — first run on your real project**

New `loupe onboard` command (and auto-trigger the first time you type
bare `loupe` inside a project folder). It's a real action flow, not a
text tour: (1) ensures a provider key is set, (2) scans the current
folder for your actual agent script — ranked by LLM-SDK imports,
entry-point name, and a `__main__` block — (3) shows what it found and,
with your confirmation, runs it under capture to produce a **real
trace from your own code**, (4) offers to open the dashboard on that
run. Empty folder falls back to scaffolding a sample. Never executes
anything without an explicit yes, and a non-interactive invocation
runs nothing — it just prints the outline. Detection logic lives in a
new testable `loupe._onboard` module (`detect_agent_scripts`,
`looks_like_project`) with 10 unit tests. 543 tests pass.

---

## [0.0.70] — 2026-05-21  ·  **Dashboard bug fixes**

Three real dashboard bugs from a user screenshot:

- **Dead "tour" button removed.** The tour JS/CSS was deleted in
  v0.0.68 but the trigger link survived in the sidebar header —
  clicking it did nothing. Gone now.
- **Cost sparkline ("14d spend") fixed.** It rendered as a broken
  near-white block because the bars used `fill="var(--ink)"` as an
  SVG *attribute*, and `var()` doesn't resolve in presentation
  attributes. Moved fills to CSS classes (`.spark-bar` amber,
  `.spark-bar-rl` red, `.spark-bar-empty` dim) where `var()` works.
- **Mobile overflow ("scrubbed and overflown").** Added a global
  `overflow-x: hidden` + `max-width: 100vw` guard, made the topbar
  stats/meta pills wrap, let the layout stack-and-scroll vertically
  on phones instead of trapping content in fixed-height panes, and
  made the timeline strip scroll horizontally inside itself. Also
  removed dead `.view-switcher` / `.theme-picker` media-query rules
  left over from the v0.0.64 gimmick strip.

533 tests pass.

---

## [0.0.69] — 2026-05-21  ·  **One reader, everywhere**

Architecture-only refactor, no behavior change. Finished the JSONL
reader consolidation started in v0.0.68: every trace-file read across
`report.py`, `report_html.py`, `attribution.py`, `bench.py`,
`otlp.py`, `_parquet.py`, `ui/server.py`, and ~8 call sites in
`cli.py` now routes through the canonical `iter_jsonl_records` /
`read_trace_header` / `load_trace_split` helpers in `loupe.store`.
Inline `json.loads(line)` loops dropped from ~22 to 6, and those 6
are intentional (the index's own builder, the LoupeBench corpus
loader, the proxy's SSE-stream parser, and `verify`'s raw schema
validator which must see unparsed bytes). cli.py module split still
deferred.

Also ran a full command-by-command functional sweep against a real
trace store — every `loupe` command works end-to-end. Fixed the one
break found: `loupe export --format jsonl` was rejected even though
`--help` advertises "JSONL (LoupeBench)"; `jsonl` is now an accepted
alias for `loupebench`. 533 tests pass.

---

## [0.0.68] — 2026-05-21  ·  **Kill the bugs, kill the bloat**

Bug + dead-code purge based on user-reported issues, no new commands
or docs. Removed ~250 lines of zombie tour overlay (JS + CSS + HTML)
that was still autolaunching on every first dashboard visit despite
being supposedly cut in v0.0.64. Removed the localhost-only "Share"
button on the step-detail panel — it generated `http://localhost:7860/#…`
URLs that were useless to share with anyone. Fixed two bugs in the
v0.0.67 first-trace inline hint: it re-appeared on every SSE refresh
(now latched once per session), and its 10-second auto-dismiss timer
leaked when `updateEmptyState()` fired repeatedly (now cleared before
re-arm). Started the architecture pass: added `iter_jsonl_records`,
`read_trace_header`, and `load_trace_split` to `loupe.store` as the
canonical readers; collapsed `_read_header` in both `cli.py` and
`ui/server.py` plus `_load_trace` / `_load_trace_with_warning` into
shims over the new store helpers. cli.py module split deferred to
v0.0.69. Same 532 tests pass.

---

## [0.0.67] — 2026-05-21  ·  **"Master Loupe in 60 seconds"**

The bar this release ships against: a naive vibe coder who has never
seen Loupe can `pip install loupe`, type `loupe`, and within 60
seconds say "oh — I get it. This is for debugging my agent."
Frictionless was necessary but not sufficient. The product itself
has to teach.

### The CLI welcome screen now PITCHES + TEACHES

`loupe` (no args) used to print a minimal banner + a few commands.
It now opens with a four-line pitch that defines what Loupe does and
what it'll capture, in plain English a vibe coder can quote back:

```
  A magnifying glass for your AI agent.

  Loupe captures every LLM call your code makes — model,
  prompt, response, latency, tokens, errors — so when your
  agent acts weird, you can replay the exact failure and
  find the cause.
```

If a provider env var is already set, a green
`✓ Detected your OPENAI_API_KEY — Loupe will capture every OpenAI
call.` line replaces the implicit "do I need to do something?"
question.

Below the pitch, exactly one CTA: `loupe init my-agent`. Then a
single help link: `loupe explain loupe`.

### `loupe explain loupe` — the "what IS this?" topic

New topic that defines every piece of Loupe vocabulary inline
(`trace`, `step`, `evidence`, `annotation`, `capture`, `autopatch`),
then walks through the three commands that cover 90% of usage. It's
the answer for a vibe coder who's confused — typing `loupe explain
loupe` is faster than reading a docs site.

### Dashboard empty-state now teaches the model

The dashboard's first-run empty-state used to be 3 quickstart
commands. Now it opens with a "What you'll see here" block that
defines `trace`, `step`, `evidence`, and `annotation` BEFORE the
quickstart, so a user arriving at the dashboard before they've
captured anything still walks away understanding the model.

### First-trace one-shot teaching hint

The user's very FIRST captured trace shows a small amber-bordered
inline label above the trace-detail pane:

> ◉ This is the timeline of one agent run. Each row below is a
> **step** — an LLM call, a tool call, or a checkpoint you wrote
> in your code. Click any step to see its evidence (inputs,
> outputs, HTTP call).

Self-dismisses after 10 s of dwell time or on `×` click. Persists
the dismissal in `localStorage["loupe.first_trace_seen"]` so a
returning user never sees it again. This is **not** a multi-step
tour (which the user rejected in v0.0.64); it's one inline label,
on the one moment it matters most.

### Trace-detail panel jargon defused

The `inputs (raw JSON)` / `outputs (raw JSON)` / `metadata` summary
labels now have `title=` hover-explainers:

- `inputs` — "The data your code passed to the model — prompts,
  messages, tool arguments, model name."
- `outputs` — "What the model returned, plus token counts and any
  tool-call requests."
- `metadata` — "Loupe-added context: latency, HTTP status,
  framework, rate-limit signals."

Screenreader-accessible out of the box (no JS, no new DOM, just
`title` attributes).

### Bulletproof JSONL reads

`loupe.store.safe_load_jsonl(path)` is the new tolerant reader.
Skips any line that doesn't parse and returns
`(records, skipped_line_count)`. Used by `_load_trace_with_warning`
and `loupe show`, so a SIGKILL'd writer, flaky disk, or
hand-edited JSONL no longer crashes the CLI — instead, callers
print a one-liner:

```
  ⚠ skipped 1 corrupt line(s) in abc12345... Run `loupe doctor --fix`
    to quarantine.
```

### `loupe doctor --fix` — safe + reversible self-heal

New `--fix` flag turns `doctor` into a one-command repair tool:

| Diagnostic                  | Repair                                  |
|-----------------------------|------------------------------------------|
| traces dir missing          | `mkdir -p ~/.loupe/traces`              |
| annotations dir missing     | `mkdir -p ~/.loupe/annotations`         |
| Corrupt JSONL               | `mv → ~/.loupe/quarantine/` (never rm)  |
| Orphan annotation sidecar   | `rm` (parent trace is already gone)     |
| DuckDB index drift          | rebuild via `JSONLIndex.rebuild()`      |

Quarantine name collisions get a `.1`, `.2`, … suffix — no file is
ever silently overwritten. After repair, doctor re-runs its full
diagnostic so the user sees a clean install in the same invocation.

### `loupe ui` auto-open is smarter

The existing `--no-browser` flag is unchanged; the auto-open path
now consults a new `_should_open_browser()` helper that blocks the
browser launch in:

- Non-TTY (CI, piped, captured by tests)
- Linux/BSD with no `DISPLAY` AND no `WAYLAND_DISPLAY` (headless SSH)
- `LOUPE_DISABLE_BROWSER=1` (escape hatch)

macOS and Windows are unconditionally allowed since `open` / `start`
handle the launch fine without an X display.

### New tests (4 files)

- `tests/test_welcome_naive_user.py` — pins the pitch contract
  (magnifying glass line, capture promise, CTA, env-var detection).
- `tests/test_corrupt_jsonl_tolerance.py` — `safe_load_jsonl` skips
  bad lines; `loupe show` / `loupe list` survive corrupt files
  with exit code 0 and a ⚠ warning.
- `tests/test_doctor_fix.py` — creates missing dirs, quarantines
  corrupt JSONL, removes orphan annotations, idempotent on clean
  install, suffix-collision safe.
- `tests/test_ui_browser_open.py` — TTY+DISPLAY opens; non-TTY
  blocks; headless Linux blocks; macOS/Windows always allowed;
  `LOUPE_DISABLE_BROWSER` overrides every signal.

---

## [0.0.66] — 2026-05-21  ·  **"Just works" — frictionless from `pip install` to first trace**

The shortest path from zero to a captured agent run drops to two lines:

```
pip install loupe
OPENAI_API_KEY=sk-… python my_agent.py
```

No `loupe[ui]` extra. No `loupe setup` ceremony. No `LOUPE_AUTOPATCH=1`.
The dashboard, the universal HTTP proxy, and zero-code capture all
work out of the box, and autopatch turns on the moment any recognized
provider env var is present.

### Frictionless install — three extras promoted to required

`pip install loupe` now bundles `httpx>=0.27`, `fastapi>=0.110`, and
`uvicorn>=0.30` directly. The `loupe[ui]` and `loupe[universal]`
extras are gone — they were friction with no benefit, since literally
every Loupe install was running them anyway. Total install size grows
by ~25 MB but every command works out of the box.

Heavy interpretability deps (`transformer-lens`, `sae-lens`, ~150 MB
combined) stay opt-in under `loupe[interp]`. Framework SDKs
(`anthropic`, `openai`, `langgraph`, `pydantic-ai`, `llama-index`,
`dspy`, `crewai`, `autogen`, `openhands`) also stay opt-in — most users
only need one, and each is 10–20 MB.

### Autopatch on env var

`loupe._autopatch_hook` previously activated only when
`~/.loupe/config.toml` existed or `LOUPE_AUTOPATCH=1` was explicit.
v0.0.66 adds a third trigger: **any recognized provider key in the
environment** (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `GOOGLE_API_KEY`, `MISTRAL_API_KEY`, `GROQ_API_KEY`,
`DEEPSEEK_API_KEY`).

Resolution order (unchanged):
1. `LOUPE_AUTOPATCH=0` → off, always (explicit opt-out is final)
2. `LOUPE_AUTOPATCH=1` → on, always
3. `~/.loupe/config.toml` exists → on (user ran setup)
4. **NEW**: any provider env var → on (user clearly intends to call an LLM)
5. otherwise → off (transitive install never surprises anyone)

`loupe._setup_providers.detect_from_env()` is the public helper for
this; `loupe status` already used it under the hood.

### Eight commands fixed for `loupe <cmd>` with no arguments

Before:
```
$ loupe annotations
Usage: loupe annotations [OPTIONS] TRACE_ID
Try 'loupe annotations --help' for help.
╭─ Error ─────────────────────────────────────────────────────
│ Missing argument 'TRACE_ID'.
╰─────────────────────────────────────────────────────────────
```

After:
```
$ loupe annotations
annotations · 7 across 4 trace(s)
  trace            step_id          category             severity  notes
  ─────────────────────────────────────────────────────────────────────
  …
```

**`loupe annotations`** (no arg) now lists EVERY annotation across
every trace — same way `loupe list` defaults to "all".

**`loupe show`, `report`, `tag`, `untag`, `diff`, `steer`, `causal`**
(no args) print a friendly error, suggest `loupe list`, and pre-fill
the example with a real trace id from the user's `~/.loupe/traces`
(the most-recent one for single-trace commands, the two most-recent
for `diff`). No more Typer "Missing argument 'TRACE_ID'" stubs.

New shared helper: `_missing_trace_id_hint(command, extra_examples=...)`
in `cli.py` — single source of truth for the friendly-error pattern.

### `loupe init --file FILENAME --provider PROVIDER`

```
loupe init my-agent --provider anthropic --file main.py
loupe init demo     --provider openai
loupe init scratch  --file run.py
```

`scaffold.py` now ships **three first-class templates** (Gemini,
Anthropic, OpenAI) keyed by provider. Each template:
- imports the right SDK (`from google import genai` /
  `import anthropic` / `import openai`)
- reads the right env var (`GEMINI_API_KEY` / `ANTHROPIC_API_KEY` /
  `OPENAI_API_KEY`)
- calls the provider's native API shape
- emits `@trace(framework="<provider>")` so the dashboard reports the
  real provider
- defaults to a sensible model (`gemini-2.5-flash`,
  `claude-haiku-4-5-20251001`, `gpt-4o-mini`)

The README inside the scaffolded project shows export syntax for
bash/zsh, fish, AND PowerShell — no more fish-only assumptions.

`validate_filename()` rejects paths, hidden files, and non-`.py`
endings with a friendly error.

### Dashboard onboarding adapts to your shell

The dashboard's first-run empty-state used to show a fish-only
`set -Ux GEMINI_API_KEY YOUR_KEY` — broken for the 95% of developers
on bash / zsh. v0.0.66 adds `GET /api/onboarding` which detects the
server-side `$SHELL` and returns the right snippet:

- bash/zsh/sh/ksh/etc → `export NAME=VALUE`
- fish              → `set -Ux NAME VALUE`
- PowerShell        → `$env:NAME='VALUE'`
- cmd.exe           → `set NAME=VALUE`

`app.js` fetches it once at boot and rewrites the empty-state code
block. Failure falls back silently to the bash default.

### New tests

- `tests/test_cli_no_args.py` — pins the friendly-error contract for
  every fixed command. Regression: nothing may emit Typer's "Missing
  argument" stub on a bare command call.
- `tests/test_autopatch_env_detect.py` — exhaustive matrix on the
  autopatch decision: 7 provider env vars × explicit-on × explicit-off
  × config-file × nothing.
- `tests/test_scaffold_variants.py` — every provider template renders
  the right SDK imports + framework label; invalid filenames /
  providers are rejected with usable errors.
- `tests/test_ui_onboarding_shell.py` — shell detection covers bash,
  zsh, fish, PowerShell, and the no-`SHELL` fallback.

### Cleanup

- README install section rewritten — single `pip install loupe` is now
  the headline; provider extras are mentioned only as optional flavor.
- All error messages that pointed at deleted extras (`pip install
  'loupe[ui]'`, `pip install 'loupe[universal]'`) now point at
  `pip install --upgrade loupe`. Catches: `ui/server.py`, `proxy.py`,
  `cli.py` (`loupe ui` + `loupe proxy` error paths), scaffold README.

---

## [0.0.65] — 2026-05-21  ·  **Lean core — every file earns its place**

Ruthless bloat pass with a world-class-MNC lens: strip anything that
isn't load-bearing OR covered by tests, then re-elevate the core surface.
The repo shrinks by ~700 lines of stale planning docs and ~5 duplicated
example files, while picking up the test coverage that was missing on
the one truly-untested integration. Versions across both SDKs are now
in lockstep with a guard test that fails CI if they drift again.

### Deleted (bloat that was no longer pulling its weight)

- `docs/COLD_EMAIL_NEEL_NANDA.md` — personal outreach draft, never
  belonged in the repo.
- `docs/UNIVERSAL_CAPTURE_PLAN.md`, `docs/UX_PLAN.md`,
  `docs/V0_2_ROADMAP.md` — stale planning artifacts; every feature they
  described has since shipped.
- `docs/loupe-trace.schema.json` — duplicate of the schema already
  vendored at `src/loupe/_data/loupe-trace.schema.json` and documented
  in `docs/SPEC.md`.
- `USE_LOUPE.md` (top-level) — duplicated the 60-second quickstart
  already in `README.md`.
- `packages/loupe-py/examples/` and `packages/loupe-ts/examples/` —
  superseded by `loupe init <name>`, which scaffolds a fresh, working
  Gemini agent on demand. Single source of truth, zero rot.

### Added — test the one path that was untested

- `tests/test_langchain.py` — 5 smoke tests for `LoupeCallbackHandler`
  covering llm-call pairs, tool-call pairs, chain errors, agent
  actions, and the no-active-trace safe-noop path. The LangChain
  integration had zero dedicated tests before; it does now.

### Added — cross-package version parity guard

- `tests/test_version_parity.py` — reads `loupe._version.__version__`,
  `packages/loupe-ts/package.json::version`, and the
  `VERSION` constant in `packages/loupe-ts/src/index.ts`. Fails CI if
  any of the three drift. (The Python SDK was on `0.0.64`, the TS
  `package.json` on `0.0.21`, and the TS source constant on `0.0.18`
  — three different "current" versions in one monorepo.)

### Synced — both SDKs now publish under `0.0.65`

- `packages/loupe-py/pyproject.toml`, `_version.py`,
  `packages/loupe-ts/package.json`, and `packages/loupe-ts/src/index.ts`
  all read `0.0.65`. From here on, the parity test makes drift
  impossible to ship.

### CI cleanup

- `.github/workflows/ci.yml` no longer lints a non-existent
  `examples/` dir, no longer shells out to deleted example scripts.
  The cross-language wire-format check now uses inline `trace()`
  snippets so it never depends on disk-resident demo files again.

---

## [0.0.64] — 2026-05-20  ·  **Surface what's built — discoverability + polish**

Audit pass with a world-class-product lens: every advanced feature we
shipped over the last 6 weeks (steering, attribution patching, SAE
registry, retention, encryption, redaction, Parquet) was reachable via
Python imports OR config-file edits, but **invisible from the CLI**.
This release surfaces every one of them.

### New top-level commands

#### `loupe status` — the at-a-glance install dashboard
One screen, four blocks: **capture** (autopatch state, encryption,
retention, redaction count) · **providers** (which keys are loaded
+ from where) · **activity** (traces on disk, last capture, last-24h
calls + cost + failures) · **Next** (context-sensitive hints).

Mirrors `vercel`, `stripe`, `gh status` in their respective ecosystems.
Answers "is Loupe live, what's it watching, and what did it cost
today?" without reading any docs.

#### `loupe config` — programmatic settings editor
```
loupe config list                       # every settable key + current value
loupe config get retention.max_age_days
loupe config set retention.max_age_days 30
loupe config set encryption.enabled true
loupe config add-redact 'EMP-\d{6}'    # custom regex
loupe config path                       # echo the file path
```
No more "edit `~/.loupe/config.toml` by hand" friction. Type coercion
catches bad values; the redactor's cache invalidates on `add-redact`
so subsequent captures pick the pattern up immediately.

#### `loupe steer` — the previously hidden Steerer
```
loupe steer abc12345 --feature 8842                # ablate
loupe steer abc12345 --feature 8842 --multiplier 2 # amplify
loupe steer abc12345 --feature 8842 --sae gemma-2-2b
```
The replay is captured as a new trace whose `metadata.steered_from`
links back; `loupe diff <orig> <steered>` works side-by-side.

#### `loupe causal` — clean-vs-corrupted attribution patching
```
loupe causal <trace> \
    --corrupted "Same prompt with the ambiguity removed." \
    --answer "No"
```
Implements the Anthropic 2024 paper recipe. Ranks features by signed
effect size at the SAE layer. Pair with `loupe steer` to test causal
hypotheses.

#### `loupe attribute --list-saes` — discoverable surrogate models
Prints every SAE entry the registry covers (`gpt2-small`, `gemma-2-2b`,
`pythia-70m`) so users know what `--sae <label>` accepts.

### `loupe setup` (already-configured branch) calls `status` first

Used to show just a list of provider names. Now renders the full
status dashboard (capture, providers, activity) and then lists the
three setup-specific actions (`--provider X`, `--remove X`, `--reset`).
One mental model, surfaced consistently.

### Eight new `loupe explain` topics

`status` · `config` · `retention` · `encryption` · `redact` · `steer` ·
`causal` · `sae-registry`. Every advanced feature now has a one-screen
explanation reachable from the CLI itself — no leaving the terminal.

### Why this matters

A world-class product surfaces every capability where users will look
for it. Before this release Loupe had genuinely novel features (causal
attribution, feature steering, encryption-at-rest, configurable
redaction) that you'd only find by reading the source. After this
release, every capability is one `loupe …` command away.

## [0.0.63] — 2026-05-20  ·  **Phase J — production hardening (J1·J2·J3·J6)**

Enterprise-ready production controls. Four features, all opt-in, all
backward-compatible with existing installs.

### J1 — Trace retention policy

New ``[retention]`` block in ``~/.loupe/config.toml``:

```toml
[retention]
max_age_days = 14         # 0 = unlimited (default)
keep_tagged  = true       # never delete annotated traces (default)
```

`loupe purge --auto` reads this and applies it — drop into a cron / systemd
timer for nightly cleanup. Without `--auto`, the existing
`--older-than 7d --yes` shape still works.

### J2 — Configurable redaction patterns

New ``[redact]`` block:

```toml
[redact]
patterns = ["INTERNAL-[A-Z]{4}-\\d{4}", "ssn:\\s*\\d{3}-\\d{2}-\\d{4}"]
```

User-supplied regexes are compiled once, cached, and applied after the
built-in credential patterns. Invalid regexes are silently skipped — the
redactor never raises into the capture path. The cache invalidates on
config file change.

### J3 — Encryption at rest (opt-in)

New ``[encryption]`` block:

```toml
[encryption]
enabled = true
```

When enabled:
- ``JSONLStore.save()`` wraps the document in a ``LOUPE-ENC-V1:<token>``
  envelope (Fernet / AES-128 + HMAC).
- The per-machine key lives at ``~/.loupe/.key`` (mode 0600).
- ``read_trace_text(path)`` decrypts transparently. All Loupe readers
  (dashboard, OTLP export, Parquet export, `loupe show`) route through
  this helper so encryption is invisible downstream.
- Existing plaintext JSONL files stay readable — backward compatible.
- Encryption failure during save falls back to plaintext rather than
  losing the trace silently.

Threat model: laptop / VM disk theft. Strict environments should still
layer dm-crypt / FileVault / BitLocker on top.

### J6 — Parquet export for analytics

```bash
loupe export --format parquet                   # → loupe-traces.parquet
loupe export --format parquet --trace-id abc1   # one trace subset
```

One row per Step, 23 typed columns (trace_id, step_kind, provider, model,
duration_ms, input_tokens, output_tokens, finish_reason, http_status,
rate_limited, error, inputs_json, outputs_json, …). ZSTD-compressed
Parquet, ready for `pandas.read_parquet`, DuckDB, Spark, Snowflake,
Databricks. Empty exports still emit a valid schema-only Parquet so
downstream pipelines don't break.

### Roll-up

- 16 new tests in ``test_production_hardening.py`` (retention loading +
  CLI, custom regex redaction, encryption round-trip + key file mode +
  graceful fallback, Parquet shape).
- **471 Python tests passing** (was 455), lint clean.
- Public API: ``loupe._crypto``, ``loupe._parquet`` exported.
- No new hard dependencies — `cryptography` is already transitive via
  FastAPI; `duckdb` is already pinned.

## [0.0.62] — 2026-05-20  ·  **UX strip — remove gimmicks, fix tour positioning**

Course-correction on the v0.0.61 push. The dashboard should feel like a
serious forensics tool, not a vim demo. Strip the noise; fix what was
broken.

### Removed

- **Keyboard shortcuts** (`j/k/h/l/t/e/v/Shift+T/?`) — all of them.
  Esc-closes-tour is the only key-handler the dashboard listens for now.
- **`?` Shortcuts modal** — gone.
- **Theme picker (light/dark/auto)** — single dark theme, no toggle.
  Stripped the light-theme CSS rules, `data-theme` attribute, inline
  pre-paint script, the `applyTheme/currentTheme` JS.
- **Detail ⇄ Timeline view switcher in the topbar** — gone. Removed the
  timeline view JS (`renderTimeline`), CSS, and the `v` shortcut.
- **Large "Tour" + "Shortcuts" buttons in the sidebar footer** — the
  whole `sidebar-foot` block is gone.

### Kept (but smaller / fixed)

- **Welcome tour stays.** Auto-plays on first visit; replayable from a
  small inline `tour` link tucked into the sidebar header (next to the
  trace count).
- **Tour positioning rewritten** so the card never overlaps the
  highlighted target. New algorithm:
    1. Measure the card's actual size after content lands.
    2. Try the requested placement, then `right` → `left` → `below` →
       `above`. Pick the first that fits the viewport AND doesn't
       overlap the highlighted rect.
    3. Last-resort clamp into viewport so the card is always reachable.
  Resizing the window mid-tour re-runs the layout pass.

### What feels different

- One less topbar element (no Detail/Timeline pip) — cleaner header.
- One less sidebar footer block — more vertical room for the trace list.
- Tour cards always sit BESIDE the thing they're describing, never on
  top of it.

No backend changes. UI tests still pass clean (25/25 in
`test_ui_server.py`). Pure cleanup release.

## [0.0.61] — 2026-05-20  ·  **Phase G — Dashboard UX overhaul (7 sub-phases)**

Every part of the dashboard a user touches got the world-class treatment.
All seven sub-phases (G1–G7) shipped in one release.

### G1 — Theme system (light / dark / auto)

- `<html data-theme="…">` attribute drives a complete light or dark palette
  via CSS custom properties.
- 3-pip segmented control in the sidebar footer; persists to localStorage.
- `auto` follows `prefers-color-scheme` and flips with the OS.
- Inline pre-paint script applies the saved theme BEFORE the stylesheet
  loads → zero flash-of-wrong-theme.
- `Shift+T` keyboard shortcut cycles through themes.
- All cross-theme transitions limited to color + border to avoid layout
  jank.

### G2 — Conversation bubble rendering

Captured `inputs.messages` (and Gemini-style `inputs.contents`) now render
as a chat thread instead of raw JSON:

- One bubble per role: `user`, `assistant`, `system`, `tool`.
- Anthropic content blocks (`text` + `tool_use` + `image`) collapsed into
  one readable bubble.
- The assistant's reply (`outputs.text`) appended as a final bubble with a
  tiny amber dot marking it as the actual reply (not history).
- Tool calls rendered as compact ``name(args)`` rows.
- Multimodal media (post-`scrub_media`) shown as
  `[image · image/png · 23.4 KB]` chips.
- Raw JSON view is still available — collapsed by default under a
  `<details>` summary.

### G3 — Multi-trace side-by-side diff view

- Select 2–4 traces in the sidebar → bulk action bar gains a **Diff**
  button.
- Side-by-side grid, one column per trace, one row per step index.
- Steps with matching `(kind, name)` are aligned (subtle background);
  diverging cells highlighted in amber for instant visual scan.
- Empty cells (one trace ran fewer steps) get a dashed placeholder.

### G4 — Timeline view

- Topbar pip toggle: **Detail ⇄ Timeline**, or press `v`.
- Horizontal time axis with 5 evenly-spaced tick labels (relative time).
- One bar per trace, color-coded:
  green = ok, amber = tagged, red = failed.
- Click or `Enter` opens that trace in detail view.
- Legend pinned to the timeline header.

### G5 — Keyboard navigation polish (vim-style)

- `j` / `k` (or arrow keys): next / previous **step**.
- `h` / `l`: previous / next **trace** in the sidebar.
- `t`: tag the selected step.
- `e`: export current trace as markdown.
- `v`: toggle Detail ⇄ Timeline.
- `Shift+T`: cycle theme.
- `/`: focus search; `Esc`: clear focus + close modals.
- `?`: show shortcuts modal (now grouped: Navigation / Actions / Views /
  Dialogs).

### G6 — Step detail polish

- `inputs` / `outputs` / `metadata` collapsed into `<details>` blocks
  (with hand-tuned triangle marker) so the conversation view is the
  primary thing the eye lands on.
- Copy button overlay on every `<pre>` — hidden by default, revealed on
  hover. Clipboard fallback for browsers without `navigator.clipboard`.
- **Deep links**: every open trace updates the URL to
  `#trace-<id>`; sharing a specific step works via
  `#trace-<id>/step-<step-id>`. Resolved on first paint.
- `🔗 Share` button on the detail header copies the deep link.

### G7 — Mobile responsive sweep

- Below 900px: sidebar stacks on top of viewer, view switcher wraps to
  its own row.
- Below 640px:
  - Brand subtitle + live indicator collapse to save vertical space.
  - Trace list rows lose the status column (carried by the colored
    border-left instead).
  - Touch targets bumped to 18px checkboxes + 36px+ buttons (WCAG 2.5.5).
  - Bulk action bar widens to full-width with comfortable button padding.
  - Help modal goes full-bleed.
- Diff view becomes horizontally scrollable so columns don't squish.

### Roll-up

Pure frontend release — no Python API changes, no new dependencies.
The dashboard now feels like a real product: themed, keyboard-driven,
mobile-friendly, with sharable deep links and a conversation-first detail
panel.

- 32 UI server tests still pass (no backend regressions).
- 455 Python + 44 TS tests still pass.
- All CSS uses custom properties → adding a new theme = adding one
  `:root[data-theme="…"]` block.

## [0.0.60] — 2026-05-20  ·  **Three-pillar completion — multimodal, LoupeBench, deep interpretability**

The deepest release so far. Closes the 5% gap on the capture pillar,
ships the LoupeBench v0.1 corpus, and adds the two interpretability
moats that no other observability tool has: a multi-model SAE registry
+ feature steering + attribution patching.

### Pillar 1 — Forensics → 100%

**Multimodal + tool-call hygiene.** `loupe._multimodal` strips inline
base64 images / PDFs / audio (Anthropic image blocks, OpenAI vision
`image_url` data URIs, Gemini `inlineData`) and replaces them with
`{sha256, size_bytes, media_type}` summaries before they hit disk.
Two captures of the same image share the same sha256 → the dashboard
can deduplicate.

**Tool-call extraction.** Three provider shapes (Anthropic `tool_use`
blocks, OpenAI `tool_calls` arrays, Gemini `functionCall` parts) get
normalized into one ``[{name, arguments, id?}, ...]`` list and surfaced
as `inputs.tool_calls` (history) + `outputs.tool_calls` (this turn).
Same code path lives in both `loupe.proxy` and
`loupe.integrations.httpx` so cross-language captures stay consistent.

**Test coverage:** 15 new tests in `test_multimodal.py`.

### Pillar 3 — LoupeBench v0.1 (curated, importable, gateable)

- **`bench/loupebench-v0.1.jsonl`** — 5 hand-curated realistic failures,
  one per category (hallucination, loop, tool-misuse, off-task,
  context-drop). Self-contained records that replay against any
  configured provider.
- **`bench/loupebench-v0.1.schema.json`** — formal JSON Schema for
  corpus records.
- **`bench/loupebench-leaderboard.schema.json`** — formal schema for
  result entries.
- **`loupe bench --corpus <source>`** — load from bundled name
  (`loupebench-v0.1`), local file, or HTTPS URL (capped at 10 MB).
  Replays each record, writes a leaderboard JSON.
- **`loupe bench --gate fail-rate=20%`** — CI gate. Exits 1 if more
  than the threshold of replays errored out. Drop into any GitHub
  Action.
- **`loupe bench --out lb-result.json`** — writes the leaderboard
  entry for sharing / archiving.

**Test coverage:** 10 new tests in `test_bench_corpus.py`.

### Pillar 2 — Interpretability (the moat)

- **`loupe._sae_registry`** — explicit table of supported (model,
  release, sae_id) tuples. Currently 3 entries:
  `gpt2-small` (default), `gemma-2-2b` (Gemini surrogate), `pythia-70m`
  (tiny). `recommended_sae_for("claude-haiku-4-5")` routes closed
  models to the right open-weight surrogate.
- **`loupe.steering.Steerer`** — feature steering primitive. Run a
  prompt through a surrogate model with one SAE feature
  dampened/amplified; the steered continuation lands as a NEW Loupe
  trace whose `metadata.steered_from` links to the original. Same
  Step shape, so `loupe diff` and `loupe attribute` Just Work on
  steered runs.
- **`loupe.attribution_patching.AttributionPatcher`** — *causal*
  interpretability via clean-vs-corrupted patching (Anthropic 2024
  paper). Given a `PatchPair(clean_prompt, corrupted_prompt, answer)`,
  ranks features by |Δactivation| at the SAE layer. Bigger signal =
  more causally responsible for the swing in the answer logit.
- **`SAEAttributor.from_registry(captured_model)`** — one-liner that
  picks the right surrogate for any captured model.

All three modules expose only data + orchestration without `[interp]`
deps — the heavy GPU pass is lazily loaded on first `.run()` call,
exactly like the existing `SAEAttributor`. Public API exported from
`loupe.__init__`.

**Test coverage:** 23 new tests across `test_sae_registry.py` and
`test_steering_and_patching.py`. The real forward-pass tests stay
behind the `[interp]` extra in `test_attribution.py` (untouched).

### Roll-up

- **455 tests passing** (was 432)
- **15 new files** total (multimodal, sae_registry, steering,
  attribution_patching, bench corpus + schema + leaderboard schema,
  4 new test modules)
- Lint clean, mypy clean on touched files
- All three pillars now at ~100% on their non-research deliverables

## [0.0.59] — 2026-05-20  ·  **Truly frictionless: setup = autopatch ON**

The "install + setup" promise is now an actual promise. No env var, no
shell rc edits, no NODE_OPTIONS to remember.

```bash
pip install loupe
loupe setup            # picks a provider + saves the key
python my_agent.py     # captured automatically — zero code, zero env vars
```

### The architectural change

Running `loupe setup` writes `~/.loupe/config.toml`. The `.pth` autopatch
hook now treats the presence of that file as "the user wants capture",
and flips ON without needing `LOUPE_AUTOPATCH=1` in the environment.

Resolution order (same in Python + TS):

```
LOUPE_AUTOPATCH=0          → always OFF (explicit opt-out)
LOUPE_AUTOPATCH=1          → always ON  (explicit opt-in, works before setup)
env var unset:
  ~/.loupe/config.toml exists  → ON   (you ran `loupe setup`)
  config file missing          → OFF  (probably transitive install)
```

The "missing-config → OFF" branch keeps libraries that depend on Loupe
safe — installing Loupe as a transitive dep no longer surprises anyone
with sudden capture activation.

Cost when off: one ``os.environ.get`` + one ``Path.exists`` ≈ 3 µs at
Python startup. No imports, no globals touched.

### `loupe run` — universal subprocess runner

`loupe run` now drives **any** command, not just Python:

```bash
loupe run my_agent.py "question"     # Python in-process
loupe run node my-agent.js            # Node, with NODE_OPTIONS wired
loupe run tsx scripts/eval.ts         # TS via tsx
loupe run go run main.go              # Go (Python.pth no-op, hint: use proxy)
loupe run -- sh -c 'exit 42'          # explicit `--` separator, exit propagates
```

For Node / TS commands the runner auto-locates `@loupe/sdk/autopatch`
in nearby `node_modules` and adds it to `NODE_OPTIONS=--require ...`,
so capture activates without any package.json change. Exit codes
propagate cleanly (sh exit 42 → loupe run exit 42).

### Dashboard — multi-trace bulk operations (Phase C)

Checkbox per trace, floating action bar when ≥1 selected, bulk delete
with confirm dialog. Annotation sidecars are cleaned up alongside their
trace so tag counts stay accurate. Backend cap: 500 ids per request.

### Dashboard — 14-day spend sparkline (Phase D)

New `/api/cost-timeseries?days=N` endpoint returns daily USD + call
counts + rate-limit incidents, zero-filled for empty days. Sidebar
renders a tiny SVG bar chart at the top, hidden until the first
priced call lands so empty installs don't show a placeholder. Bars
turn amber on days with rate-limit incidents.

### Tests

- **15 new Python tests** for: on-by-default autopatch (3), universal
  runner exit-code propagation (5), dashboard bulk-delete (5),
  cost-timeseries endpoint (3 — zero-fill, clamp, real attribution).
- **2 new TS tests** for on-by-default + explicit opt-out.
- **401 Python tests passing** (was 386) · **44 TS tests** (was 42)
- Lint clean, mypy clean on touched files.

### End-to-end verification (real subprocess)

Walked through the actual zero-friction install:

```
pip install loupe
loupe setup                        # writes ~/.loupe/config.toml
python /tmp/agent.py               # NO env var, NO Loupe imports
ls ~/.loupe/traces/                # → 1 trace captured
```

The captured trace has `framework: "autopatch"`, `name: "agent"` (from
the script filename), structured JSON `messages` array — exactly what
the v0.0.58 audit promised.

## [0.0.58] — 2026-05-20  ·  **Backend audit + four fixes**

Real end-to-end audit of the developer journey (install → autopatch →
capture → dashboard → export). Four bugs found, all fixed.

### Bug #1 — captured `messages` was a Python `repr` string

`_truncate` converted any list/dict to `repr(value)` unconditionally,
so a captured Anthropic request body would land in the trace JSONL as

```json
"messages": "[{'role': 'user', 'content': 'hi'}]"
```

— a Python-repr string with single quotes, not valid JSON. The dashboard
couldn't render structured messages and downstream JSON parsers
choked. Fixed in both `integrations/httpx.py` and `proxy.py`: lists +
dicts now stay as native JSON structures whenever their serialized
length fits the limit. Only stringified on actual overflow.

After fix:

```json
"messages": [{"role": "user", "content": "hi"}]
```

### Bug #2 — autopatch trace name was the same for every script

Every autopatched script produced a trace with `name: "autopatch"`,
`framework: "auto"`. Two issues:

- `loupe list` showed identical names — undistinguishable runs.
- Field convention was reversed vs the TS SDK (TS uses
  `name: "auto"`, `framework: "autopatch"`).

Fixed: name is now derived from `sys.argv[0]` stem (e.g. `my_agent` from
`my_agent.py`), framework is the stable `"autopatch"` signal. Falls
back to `"auto"` for REPL / `python -c` / embedded interpreters.

### Bug #3 — proxy trace was named `"proxy"` for every request

Every proxy-captured request landed with `name: "proxy"`,
`framework: "proxy"`. Now `name = provider` (anthropic / openai / gemini)
so `loupe list` distinguishes captures across providers in one glance.

### Bug #4 — broken `explain` hints

`loupe ask` (no args) printed `→ loupe explain ask` and `loupe run`
(no args) printed `→ loupe explain run` — but neither topic existed.
Added `ask`, `chat`, and `run` to `_EXPLAIN_TOPICS` so every hint
resolves.

### Verification

End-to-end audit confirmed:

- Fresh install → `loupe doctor` reports clean state ✓
- `LOUPE_AUTOPATCH=1 python my_agent.py` (no Loupe imports) captures ✓
- Captured `messages` is a JSON list, not a repr string ✓
- Trace named after the script ✓
- `loupe proxy` forwards real requests to a real upstream, captures
  the round-trip with provider-named traces ✓
- `loupe list` / `show` / `cost` / `export --format otlp` all read
  the captured traces cleanly ✓
- Cross-language Node autopatch (separate subprocess) still captures
  correctly ✓
- 383 Python tests · 42 TS tests · lint clean · typecheck clean

## [0.0.57] — 2026-05-20  ·  **CLI consolidation + six providers**

Polish pass focused on making the CLI feel curated rather than catalogued.

### `loupe setup` now supports six providers

The wizard's picker was hard-coded to Gemini / Anthropic / OpenAI. It now
ships a data-driven registry that adds **Mistral, Groq, DeepSeek**:

```
    1. gemini      free tier · fastest path to a first trace
    2. anthropic   best for production agent runs
    3. openai      GPT-4o, o-series · widest framework support
    4. mistral     European · open + frontier weights
    5. groq        LPU inference · ultra-low latency
    6. deepseek    open-weights · low cost · long context
```

Mistral, Groq, and DeepSeek all speak OpenAI-compatible HTTP so they
share one invocation path in the SDK — adding the three only needed
data-table entries, no new code paths in `chat` / `ask` / `replay`.

The registry (`loupe._setup_providers.SETUP_PROVIDERS`) is the single
source of truth used by:

- `loupe setup` (picker, key URL, persistence, default model)
- `loupe ask` / `loupe chat` (invocation)
- `loupe.config._env_keys_for()` (env-var override lookup)

Adding a provider = one row in the registry, no other code changes.

### Surface cleanup — 30 → 27 commands

Three duplicates were folded into the commands they were duplicating:

| Cut       | Use instead                          | Why |
|-----------|--------------------------------------|-----|
| `try`     | `loupe ask "hello"`                  | `try` was a canned-prompt subset of `ask` |
| `start`   | `loupe ui` (now auto-opens browser)  | `start` and `ui` both opened the dashboard |
| `otlp`    | `loupe export --format otlp`         | One export command, two formats |

`loupe ui` now ships the polish `start` had: trace-count line, auto-open
browser, friendly empty-state hint. Opt out with `--no-browser` for
headless / remote use.

`loupe export` now takes `--format` (default `loupebench`, also `otlp`)
plus the OTLP-specific `--trace-id` and `--service-name` flags.

### Other cleanups

- `_invoke_provider` (single-shot wrapper) removed — every call site
  now goes through `_invoke_with_history`, which routes to the registry.
- The 90-line per-provider switch in `_invoke_with_history` collapsed
  to a 2-line registry delegation.
- Tests cover: registry shape, all six provider scripted setup paths,
  cut-command tests (start/try/otlp removed from `_registered_command_names`),
  `export --format otlp` writes a valid OTLP doc, rejected formats.

### Test coverage

383 Python tests passing (was 375), 42 TS tests, lint clean, mypy clean
on every file we touched.

## [0.0.56] — 2026-05-20  ·  **Live tail + Node autopatch + OTLP export**

Three more pieces fall into place for the zero-friction promise.

### `loupe proxy --tail` — live one-line capture printout

Default-on. Every captured request renders a sparse, colour-coded line in
the proxy terminal as it lands:

```
14:22:09  ●  anthropic:claude-haiku-4-5            200   342ms   ↑12 ↓48   ab12cd34
14:22:11  ●  anthropic:claude-haiku-4-5            429    18ms   ↑·  ↓·    cd34ef56
14:22:14  ●  openai:gpt-4o-mini                    200   281ms   ↑8  ↓21   ef5678ab
```

You see capture working *immediately*, before opening the dashboard.
Status dot is green / amber / red. `--quiet` suppresses for CI / daemon use.
Callback exceptions never break capture — the JSONL is the source of truth.

### `@loupe/sdk/autopatch` — Node parity with `LOUPE_AUTOPATCH`

The TypeScript SDK now ships the same one-env-var pattern Python got in
v0.0.54. Combine with `NODE_OPTIONS="--require @loupe/sdk/autopatch"` and
zero source-code changes capture every fetch call to a known LLM provider:

```fish
set -x LOUPE_AUTOPATCH 1
set -x NODE_OPTIONS "--require @loupe/sdk/autopatch"

node my-agent.js          # captured, no source changes
tsx  my-agent.ts          # captured
```

When `LOUPE_AUTOPATCH` is unset the module is a near-zero-cost no-op.
When set, fetch calls made *outside* any user-defined `trace(...)` block
get wrapped in an implicit one-call trace (mirrors the Python
`_implicit_trace_context`).

### `loupe otlp` — OpenTelemetry OTLP/HTTP JSON export

Captured Loupe traces now convert to **OTLP JSON spans** with the
**GenAI Semantic Conventions** (`gen_ai.system`, `gen_ai.request.model`,
`gen_ai.usage.input_tokens`, `gen_ai.response.finish_reason`,
`gen_ai.response.rate_limited`, ...). POST the file to any OTLP/HTTP
collector — Datadog APM, Honeycomb, Jaeger, Tempo, Grafana Cloud, New
Relic, AWS X-Ray, anything OTel-compatible:

```bash
loupe otlp --out loupe.json
curl -X POST $COLLECTOR/v1/traces \
     -H 'content-type: application/json' \
     --data-binary @loupe.json
```

Flags:
- `--trace-id <prefix>` — export a subset.
- `--service-name <name>` — set the `service.name` resource attribute.
- `--out -` — stream to stdout (pipe to anything).

`loupe.kind`, `loupe.transport`, `loupe.tool.name`, and `http.*` attributes
land on every span so backends that aren't GenAI-aware still see useful
columns. Error/5xx steps carry `status.code = ERROR` with the upstream
status as the message.

### Test coverage

- 1 new proxy test for the tail callback (errors in callback must not
  break capture). Python: **375 tests**, up from 359.
- 5 new TS tests for Node autopatch (implicit trace, no-op when disabled,
  truthy values, no pollution on unknown providers, side-effect import
  safety). TS: **42 tests**, up from 37.
- 15 new OTLP tests covering identifier normalisation, GenAI semantic
  convention attributes, error status propagation, SpanKind selection,
  end-to-end JSONL → OTLP roundtrip, prefix filtering, malformed-file
  skip.

### Where the plan stands

- ✅ Phase A — universal HTTP proxy (v0.0.55)
- ✅ Phase B — live capture feedback (v0.0.56)
- ✅ Phase E — OTLP interop (v0.0.56)
- ✅ Phase F — Node autopatch parity (v0.0.56)
- ⏭ Phase C — multi-trace bulk ops in dashboard (0.1.0)
- ⏭ Phase D — time-series cost / activity charts (0.1.0)

## [0.0.55] — 2026-05-20  ·  **`loupe proxy` — universal HTTP capture for any language**

The second pillar of zero-friction capture. ``LOUPE_AUTOPATCH`` (v0.0.54)
made Python agents capture themselves. ``loupe proxy`` extends that
promise to **any language, any framework, any client** — Python, Node,
Go, Rust, Java, even raw ``curl``.

```fish
loupe proxy --provider anthropic --port 7878
set -x ANTHROPIC_BASE_URL http://127.0.0.1:7878

python my_agent.py        # captured
node my-agent.js          # captured
go run my-agent.go        # captured
curl http://127.0.0.1:7878/v1/messages -d '...'   # captured
```

### Highlights

- **Auto-detect** mode resolves the upstream from the request path:
  ``/v1/messages`` → Anthropic, ``/v1/chat/completions`` → OpenAI,
  ``/v1beta/models/...`` → Gemini, ``/openai/v1/chat`` → Groq.
- **Pinned** mode (``--provider anthropic``) routes every request to
  one upstream — useful for clients that hit nonstandard paths.
- **Override** mode (``--upstream URL``) sends to self-hosted gateways
  (LiteLLM, OpenRouter mirrors, internal proxies).
- **Streaming SSE pass-through** — chunks are forwarded byte-for-byte
  so first-token latency is unchanged. The streamed text is reassembled
  in-memory and stored exactly like a non-streamed response, so
  ``loupe cost`` and ``loupe attribute`` work identically on
  proxy-captured traces.
- **502 + failed trace on upstream errors** — the proxy never crashes
  silently. Network failures are recorded so you see them in
  ``loupe list``.
- ``loupe explain proxy`` — built-in topic page in the CLI.

### How proxy-captured Steps differ from autopatch Steps

They don't — the wire format is identical (``kind: "llm-call"``,
``inputs.provider``, ``outputs.text``, etc.), with one new metadata
field ``transport: "proxy"``. The dashboard, ``loupe cost``,
``loupe attribute``, ``loupe diff``, and ``loupe bench`` all treat
proxy-captured traces as native first-class citizens.

### Test coverage

21 new tests in ``tests/test_proxy.py``:

- Path → provider routing (Anthropic, OpenAI, Gemini, unknown).
- Upstream URL resolution (forced / Host / path).
- Step extraction for Anthropic + Gemini model + token shapes.
- Rate-limit (429) detection from both HTTP status and Gemini body.
- SSE assembly for Anthropic / OpenAI / Gemini formats + malformed-frame
  skip.
- End-to-end: forwarded request preserves method, URL, headers, body;
  captured trace has the right Step shape.
- 400 on unknown path (no upstream call, no trace written).
- 502 + failed-trace metadata on upstream connection errors.
- Streaming SSE round-trip: chunks forwarded, reassembled text captured.

359 tests passing.

## [0.0.54] — 2026-05-19  ·  **LOUPE_AUTOPATCH — zero-code agent capture**

The architectural promise that separates Loupe from a debug tool: a
developer's existing Python agent script captures every LLM call with
**zero code changes** — no ``@trace`` decorator, no ``patch_all()``
call, no ``loupe run`` prefix.

```fish
set -Ux LOUPE_AUTOPATCH 1
python my_agent.py    # captured automatically
```

That's the bar. Datadog APM, Sentry, New Relic all use this pattern
for traditional services. Loupe now matches them for AI agents.

### How it works

1. **The .pth file** — Loupe ships ``loupe-autopatch.pth`` at the
   wheel root. Python's ``site.py`` automatically scans every
   site-packages directory for ``.pth`` files at interpreter startup
   and executes any ``import`` lines they contain. Ours imports
   ``loupe._autopatch_hook``.
2. **The hook** — ``loupe/_autopatch_hook.py`` runs once per Python
   process. It checks ``LOUPE_AUTOPATCH`` env var; if unset, returns
   immediately (~1 µs cost). If set, calls ``patch_all()`` to install
   every framework integration AND signals the universal-httpx
   wrapper to enable implicit-trace mode.
3. **Implicit traces** — the universal-httpx wrapper, when called
   with no active ``@trace`` parent AND ``LOUPE_AUTOPATCH=1``,
   transparently wraps the call in a freshly-begun one-call trace
   named ``autopatch`` (framework ``auto``). The user sees their LLM
   call land in ``~/.loupe/traces/`` like any other capture.

### Performance properties

- **Off (default)**: 1 ``os.environ`` lookup at interpreter startup.
  No imports, no side effects. Effectively free.
- **On**: +20-40 ms at startup (imports ``loupe.integrations``).
  Per-call overhead is the same as ``@trace``: <100 µs/step, <5 ms/trace.
- **Opt out per-process**: ``LOUPE_AUTOPATCH= python my_agent.py``
  unsets the var for one run.

### Refactor

The universal-httpx wrapper was reorganized:

- ``_emit_around`` / ``_emit_around_async`` extract the
  invoke-and-emit-step body so the normal + implicit-trace paths
  share one capture path.
- ``_implicit_trace_context()`` is a contextmanager that creates an
  on-demand anonymous trace and finalizes it correctly on success
  AND failure (sets ``failed`` + ``error`` metadata on raises).
- ``_autopatch_enabled()`` is the single read of the env var.

### Setup integration

After ``loupe setup`` saves a provider key, it now offers
"Enable autopatch in this shell session?" (default yes). If accepted,
it sets ``LOUPE_AUTOPATCH=1`` for the current process and prints the
fish / bash / zsh commands to make it persistent.

### Tests

- ``test_autopatch_creates_implicit_trace_when_no_parent`` —
  REGRESSION FENCE. Without LOUPE_AUTOPATCH the wrapper falls
  through; with it set, a real trace lands on disk for an LLM
  call that has no ``@trace`` context.
- ``test_no_autopatch_means_no_trace_without_parent`` — proves
  the opposite direction. No env var → no phantom traces.
- **305 Python + 37 TypeScript = 342 tests.** Ruff + mypy + tsc clean.

### Documentation

``loupe explain autopatch`` covers the topic. Architecture details
in :mod:`loupe._autopatch_hook` and :mod:`loupe.integrations.httpx`.

## [0.0.53] — 2026-05-19  ·  Zero dead-end paths — flow into setup, never abort

Three real friction points surfaced in a hands-on shakedown:

1. ``loupe ask`` with no args → Typer's default ugly red panel:
   ``Missing argument 'QUESTION...'.``
2. ``loupe run`` with no args → same ugly default.
3. ``loupe ask`` / ``chat`` / ``try`` without a configured provider →
   dead-ended at ``✗ no provider configured yet. → loupe setup``.

A world-class CLI never tells the user "you should have done X first.
Now exit and re-run." It pivots. This release does that.

### Fixed — friendly missing-argument guidance

``loupe ask`` and ``loupe run`` now render their own help block when
called with no args, with concrete copy-paste-able examples and
adjacent commands:

```
$ loupe ask
  ◉ what do you want to ask?
    Pass your question as the argument:

  $ loupe ask "what is AI agent observability?"
  $ loupe ask "summarize this in one sentence: ..."

  → loupe chat            multi-turn REPL instead of one-shot
  → loupe explain ask     deeper explanation
```

Typer's default ``Missing argument`` panel is gone for these commands.
Implementation: arguments became optional (``typer.Argument(None)``)
and we check + render the guidance inline.

### Fixed — `try` / `ask` / `chat` flow seamlessly into setup

Instead of failing with ``✗ no provider configured yet``, these
commands now **launch ``loupe setup`` inline** and resume the
original intent once setup completes:

```
$ loupe ask "what is AI?"
  ◉ Loupe needs a provider before it can ask a question.
    Walking you through setup now — about 90 seconds.

  [setup wizard runs interactively]

  ↩ resuming ask a question…

  ◉ gemini:gemini-2.5-flash
  ✓ Speed of capture, no data loss…
```

The pivot only happens in interactive TTY contexts; CI / piped
contexts still see the explicit error + hint so scripts don't hang.

### How

- New ``_ensure_provider_or_setup(intent: str)`` helper. Used by
  ``try``, the ``_run_single_capture`` core (powers ``ask``), and
  ``chat``. Pure flow: if a provider is configured, return. Else
  if TTY, run the wizard inline + verify success. Else print the
  explicit error + hint (CI-safe).
- One ``intent`` string per call site (``"ask a question"``,
  ``"start a chat"``, ``"try the demo"``) feeds the resume line.

### Tests
- ``test_ask_empty_question_shows_helpful_guidance`` asserts the
  new copy + the explicit absence of Typer's "Missing argument"
  banner.
- 303 Python + 37 TypeScript = 340 tests. Ruff + mypy + tsc clean.

## [0.0.52] — 2026-05-19  ·  UX overhaul — Phases 3 + 4 (the final two)

Closes out the UX overhaul plan with the two remaining phases.

### Phase 3 — Dashboard first-visit guided tour + `?` tooltips

The dashboard now ships a Sentry-/Linear-style guided tour that runs
once on first visit (gated by ``localStorage["loupe.tour.seen"]``) and
can be re-triggered any time via the **Tour** button in the sidebar
footer. Five steps walk the user through:

1. Brand bar + live indicator
2. Case-files sidebar
3. Filter chips
4. Evidence pane (with mention of circuit attribution)
5. The Tour replay button + keyboard help

Implementation: a hollow ``.tour-spot`` box-shadow trick punches a
spotlight in a dimmed overlay (no SVG / canvas). The card placement
is recomputed on every step + on window resize. Honors
``prefers-reduced-motion`` — no slide animations if the user opted out.

Inline ``?`` icons next to two technical terms launch popovers with
plain-English explanations:

- **LoupeBench** on the annotation card
- **Circuit attribution** on the attribution card

The popover stays minimal (cool-blue accent, no animation, click-out
to dismiss, Escape closes). Extensible: ``TERM_EXPLANATIONS`` is a
flat dict in ``app.js`` — adding a new term is one entry + one
``<button class="term-help" data-term="…">?</button>`` site.

### Phase 4 — Organize `loupe --help` by purpose, not alphabetically

Instead of hiding 21 commands behind a ``loupe advanced`` subcommand
(which would feel like hiding power), the top-level help is now
**grouped** using Typer's ``rich_help_panel``:

```
╭─ Get started ────────────────────────╮
│ setup · try · init                   │
╭─ Use it (no code required) ──────────╮
│ ask · chat · run                     │
╭─ Inspect captured runs ──────────────╮
│ start · ui · list · show · diff ·    │
│ stats · annotations                  │
╭─ Analyze + benchmark ────────────────╮
│ tag · untag · bench · cost ·         │
│ attribute · cluster · replay ·       │
│ report · export                      │
╭─ Infrastructure ─────────────────────╮
│ doctor · verify · purge · providers ·│
│ explain · version · index            │
```

A first-time developer can now scan ``loupe --help`` in under five
seconds and know where to start. The ``index`` subgroup is correctly
grouped under Infrastructure too.

### Tests
- 2 new tests covering the tour markup and the term-help tooltip wiring.
- **305 Python + 37 TypeScript = 342 tests.** Ruff + mypy + tsc clean.

### What this closes

All seven phases of the UX overhaul plan (docs/UX_PLAN.md) are now
shipped:

- ✅ Phase 1 — smart router + setup wizard + try
- ✅ Phase 2 — ask / chat / run (zero-code paths)
- ✅ Phase 3 — Dashboard tour + ? tooltips  *(this release)*
- ✅ Phase 4 — `loupe --help` grouped by purpose  *(this release)*
- ✅ Phase 5 — Self-healing index + did-you-mean
- ✅ Phase 6 — `loupe explain <topic>`
- (Phase 7 — PyPI publish + 3-tier extras is release-engineering, not code.)

## [0.0.51] — 2026-05-19  ·  The wedge — `loupe bench` + `loupe cost` + rate-limit awareness

Research on the 2026 LLM-observability landscape (LangSmith, Langfuse,
Phoenix, Helicone, Braintrust, Promptfoo, DeepEval, Datadog LLM) gave
us a sharper picture of where Loupe is differentiated and where it
needed to catch up:

- **What we own that nobody else does**: SAE-based mechanistic
  circuit attribution + Neuronpedia explanations + local-first JSONL.
- **What the leaders own that we didn't**: CI-integrated regression
  testing (Braintrust / Promptfoo / DeepEval lead this category).
- **What every team needs that nobody surfaces well**: per-trace LLM
  cost.
- **What the 2026 pain numbers say**: rate-limit failures were 60 %
  of LLM call errors in Feb 2026 (Datadog State of AI 2026); we
  captured them but didn't surface them.

This release closes all three gaps.

### Added — `loupe bench`: agent regression testing

```
$ loupe bench
  ◉ benchmarking 5 tagged failure(s)

    original     category        replay
    abc12345     hallucination   → xyz78901
    def67890     loop            → mno45678
    ghi24680     tool-misuse     ✗ ANTHROPIC_API_KEY not set
    ...

  ✓ 4 replayed  ·  1 failed
  → loupe diff <original> <replay>    compare any pair
  → loupe ui                          inspect side-by-side
```

For every tagged failure in the annotation store, ``loupe bench``
re-invokes the original prompt against the current provider+model and
captures the result as a new trace (``bench:<original-name>``).
Exit 0 when every replay completes; exit 1 if any failed — drop-in
CI gate. Flags:

- ``--category hallucination``  restrict to one tag category
- ``--provider anthropic``      override the per-trace framework
- ``--model claude-sonnet-4-7`` test if a model upgrade fixes regressions
- ``--limit 10``                cap the replay count

This is **the wedge**: Loupe becomes the agent CI layer with
mechanistic insight no competitor has.

### Added — `loupe cost`: LLM spend tracking

```
$ loupe cost
  llm spend

         total   $12.47
  traces scanned 1,243
   priced steps  3,891
       unpriced  4

  by provider
    anthropic   $9.21
    gemini      $2.18
    openai      $1.08
```

Walks every captured trace, sums (input × in-price) + (output × out-
price) using the pricing table at :mod:`loupe.pricing`. Outputs:

- formatted CLI tables (default), or ``--json`` for jq pipelines
- ``--by-model`` breaks down by model id instead of provider
- unpriced step count surfaces the gap (don't silently $0)

Pricing table is hand-maintained, greppable on disk (USD per 1M
tokens for 13 models across Anthropic / Gemini / OpenAI), and never
calls a network pricing API on a hot path.

### Added — rate-limit awareness in `universal-httpx`

The universal-httpx interceptor now flags 429 responses with a
``rate_limited: true`` field on the step's outputs. Catches:
- HTTP 429 status codes (Anthropic, OpenAI standard form)
- Gemini's in-body ``error.code = 429`` (status 200 wrapped error)

The same step also gets ``status: 429`` set so existing dashboards
filter cleanly. Foundation for the next release's ``rate-limit``
filter chip in the UI.

### Added — Gemini token count extraction

Universal-httpx now reads Gemini's ``usageMetadata.promptTokenCount``
and ``candidatesTokenCount`` in addition to Anthropic's
``usage.input_tokens`` and OpenAI's ``usage.prompt_tokens``. Closes a
real bug where ``loupe cost`` reported 0 priced steps for Gemini
traces.

### Tests
- 10 new tests in :file:`tests/test_pricing.py` (exact match, prefix
  strip, provider fallback, unknown returns None, math correctness,
  missing-tokens, negative-tokens clamping, format tiers, known models).
- 6 new CLI tests for cost (empty home, sum, unpriced, pretty, by-model)
  and bench (no-tags, category filter).
- **334 Python + 37 TypeScript = 371 tests.** Ruff + mypy + tsc clean.

## [0.0.50] — 2026-05-19  ·  UX overhaul — Phases 5 + 6: self-heal, did-you-mean, explain

Three polish wins that match the friction bar set by gh / Stripe / Cargo CLIs.

### Did-you-mean typo suggestions

The CLI entry point now intercepts unknown top-level commands and
prints actionable suggestions instead of Typer's default usage block:

```
$ loupe sho
  ✗ unknown command 'sho'
    Did you mean show?
  → loupe --help    full list of commands

$ loupe lst
  ✗ unknown command 'lst'
    Did you mean: list, cluster?
  → loupe --help    full list of commands
```

Implementation: ``loupe.cli:main_entry`` wraps the Typer app. Unknown
first argument → ``difflib.get_close_matches`` against the registered
command list → print and exit 1. Everything else is delegated to the
regular app, so help / flags / known commands all behave identically.

### Self-healing index

The DuckDB index auto-rebuilds itself when it detects pollution:

- ``JSONLIndex.list_traces`` samples the first 20 indexed rows and
  checks each against the on-disk JSONL set.
- If >25 % of sampled rows point at files that no longer exist on
  disk → silent rebuild → re-run the query.
- This stops the dashboard / CLI from showing phantom rows after
  someone removed files outside ``loupe purge`` (a real bug we hit
  earlier this session).

### `loupe explain <topic>`

A built-in topic explainer so no one has to leave the terminal to
read docs:

```
$ loupe explain
  TOPICS
    attribution   cluster      config        index       providers
    replay        step         tag           trace       wire-format

$ loupe explain attribution
  ◉ attribution

  SAE-based circuit attribution per llm-call step.
  ...
```

10 topics covered: trace, step, tag, attribution, cluster, index,
replay, config, wire-format, providers. Unknown topic → "did you
mean?" suggestion, same pattern as the CLI.

### Tests

- 4 new tests: typo-suggestion subprocess check, explain lists topics,
  explain renders a topic body, explain unknown-topic suggestion.
- 2 new self-heal tests in ``test_index.py``: list_traces rebuilds when
  files are deleted; clean index does NOT trigger rebuild on every call.
- **317 Python + 37 TypeScript = 354 tests.** Ruff + mypy + tsc clean.

## [0.0.49] — 2026-05-19  ·  UX overhaul — Phase 2: ask / chat / run (zero code)

Three new commands so a developer never has to write a single line of
Python to use Loupe.

### Added — `loupe ask <question>`

```
$ loupe ask "Reply in one sentence: what is observability?"

  ◉ gemini:gemini-2.5-flash

  Observability is the ability to understand what a system is doing
  by examining its outputs and emitted signals.
```

One captured LLM call. Like ChatGPT in the terminal, with a trace
written to `~/.loupe/traces/`.

### Added — `loupe chat` (interactive REPL)

```
$ loupe chat

  ◉ chat (gemini:gemini-2.5-flash)  ·  /help for commands · blank line to quit

  you ▸ what is observability
  gemini ▸ ...

      ✓ trace 6d4ae83af377  ·  /tag <category> to mark this turn

  you ▸ /tag hallucination invented a fact about latency
  ✓ tagged 6d4ae83af377/d3a6a0967f78 as hallucination
```

Multi-turn conversation. History is held in memory and sent on each
turn so follow-ups work. Slash commands:

- `/tag <category> [notes]` — tag the last turn
- `/show` — print the last captured trace
- `/dashboard` — open the dashboard
- `/clear` — reset history
- `/help`, `/quit` — self-explanatory

### Added — `loupe run script.py [args]`

```
$ loupe run my_agent.py "what is the capital of France?"
  ◉ running my_agent.py  ·  every LLM call captured
  ...
  ✓ done — trace captured
```

Auto-instrument any Python script. Loupe calls `patch_all()` BEFORE
the script imports anything, wraps the whole execution in `@trace`,
and writes one JSONL per run. **No source edits needed**.

Use cases:
- Observe a teammate's script you don't want to modify
- Capture an open-source agent's LLM calls
- One-line addition to existing pipelines

`sys.argv` is rewritten so the script sees its own arg list as if
called directly with `python script.py args`.

### Refactored — single multi-turn invoker

The provider call code lived in three almost-duplicated functions
(setup's ping, try's call, ask's call). Now there's one
`_invoke_with_history` that all four entry points (setup ping, try,
ask, chat) route through. Adding a fifth provider is one branch.

### Tests
- 8 new tests covering: ask without config, ask empty question, ask
  full path with patched invoker (asserts trace landed on disk), chat
  without config, run requires args, run missing script, run executes
  + writes trace with the expected `run:<stem>` name.
- **311 Python + 37 TypeScript = 348 tests.** Ruff + mypy + tsc clean.

## [0.0.48] — 2026-05-19  ·  UX overhaul — Phase 1: smart router + setup + try

The friction-zero UX plan ships in five phases over the next releases.
This is Phase 1 — the highest-impact piece. A first-time developer can
now go from `loupe` (no args) to a captured real trace in **90 seconds**
without making a single configuration decision.

### Added — `~/.loupe/config.toml` (the durable config layer)

Replaces the historical scattered env-var-driven config:

```toml
[default]
provider = "gemini"
model    = "gemini-2.5-flash"

[providers.gemini]
api_key = "AIza..."

[providers.anthropic]
api_key = "sk-ant-..."

[attribution]
backend = "mock"
```

- New `loupe.config` module — `Config.load()` / `.save()` / immutable
  builder pattern (`set_provider_key`, `with_default`).
- **Env vars still win** as ephemeral overrides — backwards compat intact.
- Tolerant: corrupt config never crashes startup, falls back to defaults.
- Human-readable: hand-rendered TOML with comments + stable section order.

### Added — `loupe setup` (interactive wizard)

```
$ loupe setup
  Pick a provider
    1. gemini       free tier available · fastest path to a first trace
    2. anthropic    Claude · best for production-quality agent runs
    3. openai       GPT-4o, o-series · widest framework support
    your pick [1-3, default 1] › 1

  → opening https://aistudio.google.com/apikey in your browser…
  Paste your gemini key here (format: AIza…):
    key › •••••••

  ✓ saved to /home/you/.loupe/config.toml
  ✓ verified — model gemini-2.5-flash responded
```

Scripted mode for CI:

```
loupe setup --provider gemini --api-key "$GEMINI_KEY" --no-browser
```

### Added — `loupe try` (one-shot demo)

After setup, ``loupe try`` sends a canned prompt with your configured
provider, captures the trace, prints the answer, and suggests
``loupe ui``. The "it works on my machine" proof in 5 seconds.

### Added — smart router (`loupe` with no args)

`loupe` (no args) now picks the right action based on your state:

- First run, no config, no traces → auto-launches `loupe setup`
  (only when stdin is a real TTY; never in CI / piped contexts)
- Setup done, no traces → shows next steps including `loupe try`
- Has traces → shows the welcome with `loupe ui` / `list` / `stats`

### Tests
- 10 new tests for the config layer (load defaults, save+reload,
  env-var-wins, alphabetical providers, immutability, TOML format,
  corrupt-file tolerance, unknown-provider safety, ProviderConfig helper).
- 6 new tests for setup/try/router (scripted save path, empty-key
  rejection, unknown-provider rejection, already-configured
  short-circuit, try-without-config error, smart-router non-TTY fallback).
- **305 Python + 37 TypeScript = 342 tests.** Ruff + mypy + tsc clean.

## [0.0.47] — 2026-05-19  ·  Dashboard search + tighter error hints

### Dashboard — search now spans step content, not just headers

The sidebar search used to match only on trace name / framework /
trace_id. Now ``GET /api/traces?q=<query>`` filters server-side
across **every step's kind, name, and error text** too — so a query
like ``"429"`` surfaces every trace whose Gemini call rate-limited,
and ``"claude-haiku"`` finds runs you forgot the name of.

- Server-side filter at ``/api/traces?q=…``; case-insensitive
  substring match. Returns a ``match`` object per trace
  (``{"header": bool, "steps": bool}``) so the client can surface
  *why* a trace matched if we want a richer UI later.
- Client-side: debounced 200ms refetch on input. Local filter still
  runs against the cached list for instant feedback.

### CLI error paths now propose the next command

``_find_trace`` is the workhorse behind ``show / report / verify /
tag / annotations / diff / attribute / replay``. Its "no match" path
used to say `No trace matching <id>` and stop. Now it also prints
the next command, picked by whether the user has any traces yet:

```
loupe show abc123
  ✗ No trace matching 'abc123'
  → loupe list             see every captured trace
```

```
loupe show abc123                    # zero traces in home
  ✗ No trace matching 'abc123'
  → loupe init my-agent    scaffold a real starter project
  → python agent.py 'q'    capture your first trace
```

Same treatment for:
- ``loupe init <name>`` into a non-empty dir — now suggests a new
  name + the explicit ``rm -rf`` if they really meant it.
- ``loupe diff`` against a malformed trace — points at ``loupe verify``.

### Tests

- 4 new tests for ``/api/traces?q=…``: header match, step-content
  match, no-match → empty, empty-q returns all (no match metadata).
- **289 Python + 37 TypeScript = 326 tests.** Ruff + mypy + tsc clean.

## [0.0.46] — 2026-05-19  ·  `--json` output for list / stats / show

Three commands now have a ``--json`` flag so real users scripting
against Loupe — CI gates, jq pipelines, custom dashboards — can
consume the data programmatically without parsing Rich-formatted
tables.

### `loupe list --json`

```
[
  {
    "trace_id": "0e0a3b8548cf482190ecec70ab8a95ff",
    "name": "my-first-agent",
    "framework": "gemini",
    "duration_ms": 2016.39,
    "step_count": 2,
    "failed": true,
    "annotation_count": 1
  },
  …
]
```

Full ``trace_id`` (no truncation — that's a presentation concern, not
data). Empty home returns ``[]``.

### `loupe stats --json`

```
{
  "trace_count":         3,
  "failed_count":        1,
  "step_count":          8,
  "annotation_count":    2,
  "median_duration_ms":  2516.6,
  "by_framework":        {"gemini": 3},
  "by_failure_category": {"hallucination": 1, "other": 1}
}
```

Empty home returns the same shape with all counts at 0 and dicts
empty — never a banner, never a hint. Pipelines stay happy.

### `loupe show <id> --json`

Returns the full header + steps + annotations as one JSON object —
the same shape ``GET /api/traces/{id}`` returns from the dashboard
server. The single canonical way to extract one captured trace.

### Tests

- 6 new tests: empty home, populated home, JSON parse, full
  trace_id preserved, unknown trace exits 1, JSON shape correctness.
- **285 Python + 37 TypeScript = 322 tests.** Ruff + mypy + tsc clean.

## [0.0.45] — 2026-05-19  ·  Production hardening — every command, every state

End-to-end shakedown of every CLI command against fresh and populated
LOUPE_HOMEs. One critical bug surfaced, plus a round of polish.

### Fixed — background indexer was polluting users' real ``~/.loupe``

**Severity: high.** Shipped briefly; cleaned up in this release.

`JSONLStore.save()` dispatched a daemon thread that called
``default_index()``, which reads ``LOUPE_HOME`` at thread-run time. If
a test fixture's ``monkeypatch.setenv("LOUPE_HOME")`` got torn down
between scheduling and execution, the thread saw the live env and
wrote rows to the **user's real index file**. Symptom on the affected
user's machine: ``loupe list`` and ``loupe stats`` displayed rows
from long-deleted test traces, rendered as garbled unicode.

**Architectural fix:** the index path is now derived from the store's
own ``self.root`` and captured **eagerly** before the thread spawns.
``LOUPE_HOME`` is no longer read inside the background thread. Even a
swapped env mid-flight can't redirect the write anymore.

**Defensive fix:** every test fixture that sets ``LOUPE_HOME`` also
sets ``LOUPE_DISABLE_INDEX=1`` — so even if some future regression
re-opens the door, no test can pollute a user home.

**Recovery for anyone hit:** ``loupe index rebuild`` re-walks the
JSONL files on disk and replaces the polluted DB. The on-disk JSONL
files were never affected; they're the source of truth.

### Eliminated all "coming soon" / half-built language

The replay docstring used to say "Anthropic + OpenAI replay are coming
once we've validated edge cases." They're shipped now — see below.
Pre-alpha 🚧 badges and roadmap-style placeholders are gone from the
sub-package READMEs and the main README; what's in the box is what
works today.

### Added — Anthropic + OpenAI replay backends

``loupe replay`` now supports all three providers:

```
loupe replay <trace>                       # any framework auto-routed
loupe replay <trace> --model gpt-4o-mini   # cross-model replay
loupe replay <trace> --prompt "…"          # prompt variants
```

Each backend is a small subclass of ``_ReplayRunner`` exposing
``framework``, ``env_keys``, ``key_hint``, and ``invoke()``. Adding
a new provider is one class + one entry in ``_REPLAY_BACKENDS``.

### Polish

- ``loupe.neuronpedia.explain_many`` renamed an internal ``todo``
  variable to ``pending`` for readability.
- ``loupe.__init__`` now surfaces the attribution primitives
  (``MockAttributor``, ``SAEAttributor``, ``Attributor``,
  ``AttributionResult``, ``FeatureActivation``, ``make_attributor``,
  ``attribute_trace``) so user code can ``from loupe import …``.
- ``@loupe/sdk`` ``VERSION`` constant in ``src/index.ts`` synced to
  ``package.json`` (0.0.19) with a comment marking the sync point.
- ``packages/loupe-py/README.md`` and ``packages/loupe-ts/README.md``
  reworked to reflect today's install paths and the v0.2 commands.
- ``CONTRIBUTING.md`` written (the README linked it; no longer 404).
- ``docs/ARCHITECTURE.md`` extended with index, attribution,
  Neuronpedia, cluster, replay subsystems.

### Tests

- 1 new regression test
  (``test_background_indexer_targets_store_root_not_env``) that
  patches ``JSONLIndex.__init__`` to capture the db_path the
  background thread tries to use, then asserts it's derived from
  the **store's** root, not LOUPE_HOME.
- 2 new replay CLI tests for the new Anthropic + OpenAI backends:
  missing-API-key error path + backend resolver.
- 9 test fixtures hardened with ``LOUPE_DISABLE_INDEX=1`` so test
  runs can never leak into a user's real index.
- **278 Python + 37 TypeScript = 315 tests.** Ruff + mypy + tsc clean.

## [0.0.44] — 2026-05-19  ·  `loupe replay` — re-run any captured agent run

The agent-forensics use case asked: *"Did the bug get fixed?"*
``loupe replay <trace-id>`` answers it.

### What ships

```fish
loupe replay <trace-id>                       # same prompt, same model
loupe replay <trace-id> --model gemini-2.5-pro   # same prompt, newer model
loupe replay <trace-id> --prompt "different"   # different prompt, same model
```

For a captured agent run, replay:

1. Extracts the original prompt — from the ``plan`` step's
   ``outputs.q`` first (loupe-init scaffold pattern), falling back to
   the first ``llm-call`` step's ``inputs.contents`` /
   ``inputs.messages``.
2. Extracts the original model — from ``inputs.model``, or parsed out
   of the step ``name`` (``"gemini:gemini-2.5-flash"``) when
   universal-httpx captured a body that lacked the field (Gemini's
   case — its model lives in the URL).
3. Re-invokes the same provider+model.
4. Captures the new run as a separate trace with name
   ``replay-of-<original-name>``.
5. Prints both trace ids and suggests
   ``loupe diff <old> <new>`` for the comparison.

### Supported frameworks today

- ``gemini`` (the default for the ``loupe init`` scaffold).

Anthropic + OpenAI replay are coming once the per-provider
input-extraction edge cases are validated.

### Error paths (all return clean exit 1, no traceback)

- Unknown trace id
- Trace from an unsupported framework
- Missing ``GEMINI_API_KEY`` in the shell
- API failure (the failed call is still captured as a new trace)

### Tests

- 6 new tests covering the input-extraction helper (plan-step path,
  fallback to llm-call inputs, model parse-from-name, unrecognized
  trace) + CLI error paths (unknown trace, unknown framework).
- **275 Python + 37 TypeScript = 312 tests.** Ruff + mypy + tsc clean.

## [0.0.43] — 2026-05-19  ·  README refresh for v0.2

The README front door was six versions stale. New visitors saw a v0.0.32-era
quickstart and a "what's in the box" table missing every v0.2 feature.

### Updated

- Test count badge bumped from 218 → 339.
- Full command reference grouped by purpose:
  *Capture & inspect · Aggregate & tag · Mechanistic interpretability ·
  Indexing & lifecycle · Quality & integration*.
- New top-level section **"Circuit attribution — see *which* features
  fired"** with real example output (the legal-rulings / prison /
  percentages / alcohol / dates features from gpt2-small) and the
  honest cross-model caveat documented in plain English.
- `loupe cluster` walkthrough including the distinctiveness-table
  example (the research-paper artifact's output).
- "What's in the box" table updated:
  * DuckDB index (v0.0.38)
  * Real SAE attribution (v0.0.41)
  * Neuronpedia explanations (v0.0.42)
  * Cluster analysis (v0.0.40)
- "The magic moment" rewritten with the real workflow:
  failing-step → tag → attribute → cluster → reproducible
  feature characterization.

No code changes. Documentation pass only.

## [0.0.42] — 2026-05-19  ·  Neuronpedia explanations — features get readable names

v0.0.41 returned `#23123` and you had to know what that meant. Now
`loupe attribute --explain` looks each feature up on the public
Neuronpedia API and shows you the human-readable interpretation
alongside the activation:

```
  step d3a6a09 top features:
    # 23123  act=420.087  phrases related to legal documents and rulings
    #   979  act=401.952  phrases related to privatized prison industry…
    #   316  act=349.759  mentions of percentages or numerical values
    #  7496  act=329.776  phrases related to warning signs about alcohol
    # 23111  act=327.353  mentions of specific dates and events
```

Same data flows into the dashboard's **Circuit attribution** panel —
the feature row now shows the description in place of the hook layer.

### What ships

- **`loupe/neuronpedia.py`** — small, defensive client for
  ``https://www.neuronpedia.org/api/feature``:
  - ``explain(feature_id, hook_name, release)`` — single lookup.
  - ``explain_many([ids], ...)`` — batched lookup with a small
    thread pool. A 16-feature attribution finishes in ~1-2s.
  - Local cache at ``~/.loupe/neuronpedia-cache.json`` — second
    ``--explain`` run is instant + offline.
  - ``LOUPE_DISABLE_NEURONPEDIA=1`` opts out entirely.
- **`FeatureActivation.description`** — new optional field, written
  by ``--explain`` and rendered by the CLI + dashboard.
- **`--explain` flag on ``loupe attribute``** — single-trace and
  ``--all`` modes both support it. Lookups are batched per
  (hook_name, release) cluster so one big attribution doesn't fire
  hundreds of sequential requests.

### Honest properties
- Best-effort everywhere. Network down → ``description=None``,
  attribution result still saves with the raw feature ids.
- Recognized SAE releases today: ``gpt2-small-res-jb`` and
  ``gpt2-small-res-jb-feature-splitting``. Other releases return
  ``None`` from the Neuronpedia lookup and the CLI falls back to
  showing the hook layer.

### Tests
- 17 new tests covering: hook→layer mapping (canonical, feature-
  splitting, unknown release, malformed hook), model lookup, cache
  round-trip via disk, explain() with HTTP mock + cache hit + HTTP
  error + 404 + unknown-release short-circuit + disabled-env,
  explain_many() batch behavior + empty input, FeatureActivation
  description round-trips through JSON.
- **302 Python + 37 TypeScript = 339 tests.** Ruff + mypy + tsc clean.

## [0.0.41] — 2026-05-19  ·  **Real SAE attribution — the v0.2 research artifact**

The ``NotImplementedError`` is gone. ``loupe attribute --backend sae``
now runs a real forward pass through GPT-2 small + Joseph Bloom's
published layer-6 residual SAE (from the ``gpt2-small-res-jb`` release),
encodes the hidden states through a 24,576-feature dictionary, and
returns the top-K features by total post-ReLU activation across the
prompt+response — with each feature's peak token position.

### What ships

- **End-to-end real SAE pipeline** in ``loupe/attribution.py``:
  - ``transformer_lens.HookedTransformer`` for the forward pass
  - ``sae_lens.SAE.from_pretrained`` for the trained dictionary
  - ``model.run_with_cache(..., names_filter=hook_name)`` so we only
    materialize the layer the SAE consumes
  - ``sae.encode(hidden)`` projection, summed across token positions
  - per-feature peak token position recorded as diagnostic metadata
- **Lazy loading + caching**: weights download on first ``.attribute()``
  call, cached on the instance. First call ≈ 7 s on CPU; subsequent
  calls ≈ 200 ms per ~25-token turn.
- **Defaults that work out of the box**: ``gpt2-small`` + layer 6
  residual SAE. ``loupe attribute --backend sae`` requires zero
  extra flags after ``pip install 'loupe[interp]'``.
- **API-version tolerant**: handles sae-lens v6+ (hook_name under
  ``cfg.metadata``) and older versions (hook_name on ``cfg``).
- **Bounded compute**: ``max_tokens=256`` cap so a runaway agent
  trace doesn't OOM the user's laptop.
- **Empty-text safety**: empty prompt+response returns an empty
  ``AttributionResult`` with an explanatory summary, never throws.

### Honest caveat (documented in the class docstring)

Closed frontier models (Claude, GPT-4) have no public SAE. The
workflow Loupe is built for: capture an agent that calls a closed
model, then attribute the *same prompt* through GPT-2 small. The
features aren't literally what fired inside Claude — they're what
an open model would use to produce a similar continuation. That
correlation is what current mech-interp research relies on.

### Tests

- 4 new ``@needs_interp`` tests gated on the optional ``[interp]`` extra
  being installed:
  - construction is cheap (no weight load)
  - real forward pass produces ``sae-encode-topk`` results with
    activation-sorted features at the actual SAE hook layer
  - model + SAE are cached between calls (day-2 use stays fast)
  - empty text doesn't crash
- **285 Python + 37 TypeScript = 322 tests.** Lint + mypy + tsc clean.

### Try it

```fish
pip install 'loupe[interp]'                   # one-time, pulls torch + sae-lens
loupe attribute <trace-id> --backend sae       # ~7 s first call, ~200 ms after
```

The captured features are persisted into the same annotation store
as before — refresh the dashboard and the **Circuit attribution**
panel will show real SAE features with real activation bars.

## [0.0.40] — 2026-05-19  ·  Attribution: dashboard panel + bulk + cluster

Three additions that turn the v0.0.39 attribution foundation into a
genuinely useful research tool.

### Dashboard — circuit_attribution panel
When a step's annotation carries a ``circuit_attribution`` field, the
step detail pane now renders a dedicated **Circuit attribution** card
below the LoupeBench annotation: top-K features with horizontal bars
sized by relative activation, the model/SAE provenance line, and the
attributor's free-form summary. Cool-blue accent so it's visually
distinct from the amber tagging card.

### `loupe attribute --all` — bulk
Walk every captured trace and attribute every llm-call step in one go.
Default behavior **skips** steps that already have an attribution;
``--force`` re-runs them. Output is a single spinner + one summary
line: ``✓ attributed N step(s) ·  mock / mock-model · K skipped``.

### `loupe cluster` — failure-feature analysis
The analytical primitive of the LoupeBench research workflow:

```
loupe cluster                        # across every annotated step
loupe cluster --category hallucination
loupe cluster --category loop --top-k 25
```

Outputs:
- **Frequency table** — which feature ids fire across the filtered
  annotations, with hits + share %.
- **Distinctive features** — when ``--category`` is set, a second
  table shows features over-represented in this category vs every
  other, scored by a smoothed log-ratio (``+1`` smoothing so zero-out
  cases don't explode).

### Tests
- 11 new (5 for ``--all``/``--force``, 4 for ``cluster``, 2 for
  dashboard markup presence).
- ``Annotation.circuit_attribution`` re-typed as ``dict[str, Any]`` so
  the rich AttributionResult shape type-checks cleanly.
- **248 Python + 37 TypeScript = 285 tests.** Ruff + mypy + tsc clean.

## [0.0.39] — 2026-05-19  ·  Circuit attribution foundation

The v0.2 research-artifact foundation lands. Loupe can now attribute
each captured ``llm-call`` step to a set of top-K interpretable
features — the mechanistic foothold that distinguishes Loupe from
"just another agent observability tool."

### Architecture

This commit ships the *data model + pipeline + CLI + mock backend*.
The real SAE backend (forward pass through transformer-lens + sae-lens
projection) is staged behind ``NotImplementedError`` with a precise
contract in its docstring — coming in the next release once an open
model + public SAE pair is chosen. Everything around it is in place:

- **``loupe/attribution.py``**
  - :class:`FeatureActivation` (frozen) — one feature firing in one step.
  - :class:`AttributionResult` — full per-step result; JSON-stable so
    annotations can be replayed years later.
  - :class:`Attributor` protocol — narrow interface every backend implements.
  - :class:`MockAttributor` — deterministic SHA256-derived synthetic
    features. Reproducible across machines. Used for CI + plumbing
    validation when you don't want to pull GBs of weights.
  - :class:`SAEAttributor` — guarded by ``[interp]`` extra. Real
    implementation deferred to the next release; the contract is
    documented in the class docstring.
  - :func:`make_attributor` factory + :func:`attribute_trace` orchestrator.

### CLI

```
loupe attribute <trace> [--backend mock|sae] [--top-k 8]
                        [--only-failing] [--model M --sae S]
```

- Walks every ``llm-call`` step in the trace.
- Runs the chosen attributor on each.
- Persists results into the existing annotation store under the
  ``circuit_attribution`` field. Idempotent: re-running updates in
  place, doesn't duplicate. Existing human tags (category, notes,
  severity) are preserved.
- Prints a compact preview of the top features for the first step.

### Tests

- 16 new tests (1 skipped pending sae-lens install): deterministic
  output, activation ordering, divergent inputs, JSON round-trip,
  factory error paths, llm-call-only walking, ``--only-failing``,
  CLI persistence, tag-preservation, unknown-trace exit, unknown
  backend, idempotent re-run, immutability of FeatureActivation.
- **237 Python + 37 TypeScript = 274 tests.** Ruff + mypy + tsc clean.

## [0.0.38] — 2026-05-19  ·  DuckDB index for sub-millisecond queries

The v0.2 foundation: an embedded DuckDB index over the JSONL trace
store. `loupe list / stats` were O(N) disk scans; now they're indexed
SQL queries that stay fast at 10k+ traces.

### Added

- **`loupe/index.py`** — `JSONLIndex` class wrapping an embedded
  DuckDB database at `~/.loupe/index.duckdb`.
  - Schema-versioned with automatic rebuild on version drift.
  - In-process write lock; safe across threads.
  - Best-effort everywhere: any failure returns False/None, never
    raises. The JSONL files on disk remain the source of truth.

- **Background indexer in `JSONLStore.save()`** — every trace write
  now dispatches a daemon thread to upsert the index. The hot path
  stays under 100µs/step (perf budget unchanged). If indexing
  fails, the next `loupe index rebuild` reconciles.

- **CLI subcommands** —
  - `loupe index info` — path, size, row counts, schema version.
  - `loupe index rebuild` — drop the index and re-walk JSONL files.
    Crash-recovery path; safe to run any time.

- **Indexed `loupe list`** — uses the index when available, falls
  back to a disk walk if it's missing or broken. Prints a faint
  `· indexed` footer when the fast path served the result.

- **Indexed `loupe stats`** — aggregate counts, framework breakdown,
  and median duration now come from a single SQL query.

- **`loupe purge --yes` cleans the index** — when a trace JSONL is
  deleted, its corresponding rows are removed from the index too.

### Opt-out

Set `LOUPE_DISABLE_INDEX=1` to skip indexing entirely (useful on NFS
mounts without proper locking, or in restricted CI environments).

### Tests

- 12 new tests covering: upsert correctness, idempotence, failure
  marking, ordering, stats aggregates, rebuild from disk, removal,
  health info, missing-index fallback, background-thread upsert
  via JSONLStore, env-var disable, and schema-version migration.
- **225 Python + 37 TypeScript = 262 tests.** Ruff + mypy + tsc clean.
- Performance budgets (<100µs/step, <5ms/trace) unchanged.

## [0.0.37] — 2026-05-19

### CLI — Vercel/Stripe-grade visual polish

The CLI rendered correctly but didn't *feel* crafted. Pass-by-pass:

- **New banner.** Dropped the heavy `─────` box. Now: a single brand
  line (`◉  loupe  vX.X.X`), an optional italic subtitle, and a
  whisper-thin dotted rule. Matches the design language of the gh /
  vercel / stripe CLIs.
- **Real spinners.** `loupe doctor` now shows a Rich spinner labelled
  "Scanning installed integrations" while it walks the package list.
  Auto-disables on non-TTY (CI logs stay grep-friendly).
- **Adaptive width.** `loupe list` collapses to four columns
  (name · duration · steps · status) under 88 cols and expands to six
  (adds trace_id + framework) at wider terminals. Status column is
  never dropped — it's the most decision-relevant.
- **Refined copy.** Welcome screen reads "3 traces captured" instead
  of "YOU HAVE 3 TRACE(S) CAPTURED". Section heading style preserved
  for visual rhythm.

### Dashboard — no more Google Fonts CDN

Removed the `<link rel="stylesheet" href="https://fonts.googleapis...">`
preconnect chain. The dashboard now uses native system font stacks
exclusively:
- Body: `-apple-system / BlinkMacSystemFont / Inter / SF Pro Text / Segoe UI / system-ui`
- Wordmark serif: `Iowan Old Style / Georgia` (system on every OS)
- Mono: `SF Mono / Menlo / monospace`

Result: zero CDN dependency, instant load, works offline, no FOUT,
no privacy-leaking third-party request when you open the dashboard.
The serif system fallback (Iowan/Georgia) is still distinctively
non-AI and matches the forensic-dossier aesthetic.

### Tests
- 213 Python + 37 TypeScript = 250 tests. Ruff + mypy + tsc clean.

## [0.0.36] — 2026-05-19

### Removed — `loupe demo` and all fake content

World-class developer SDKs (Stripe, Sentry, Vercel) never seed fake
data into a user's account. Their onboarding instruments the user's
real code. Loupe now does the same.

**Removed:**
- `loupe demo` command + `loupe/demo.py` module + `tests/test_demo.py`
- The fake-claude / placeholder LLM step in the `loupe init` scaffold
- All "Pretend to call an LLM" placeholder comments
- Demo-seeding from `loupe start` (it now just opens the dashboard;
  empty home is fine — the in-browser onboarding card guides the user)
- README's "30-second quickstart" referencing pre-seeded fake traces

**Replaced with a real first-run flow:**
- `loupe init my-agent` scaffolds a working agent that calls **real
  Gemini** (free tier) — the user sets one env var and runs `python
  agent.py "their question"` to capture their first real trace.
- The welcome screen, the dashboard's empty-state onboarding card, and
  the empty trace-list hint all point at this real flow.

### Tests
- Scaffold test now asserts the generated agent uses a real LLM SDK
  (`google` import present) and contains no `fake` or `pretend` strings.
- **213 Python + 37 TypeScript = 250 tests.** Ruff + mypy + tsc clean.

## [0.0.35] — 2026-05-19

### Fixed — Gemini model extraction in universal-httpx

Bug surfaced by running a real loupe-chat session against a live Gemini
API key. Captured step said `gemini:unknown` because the universal-httpx
interceptor only looked at `body["model"]` — but Gemini (alone among
major providers) puts the model in the URL path:

    /v1beta/models/gemini-2.0-flash:generateContent

Now `_extract_model()` checks the body first, then falls back to a regex
on the URL path. Captured steps now correctly report `gemini:gemini-2.0-flash`
with `model` populated in inputs.

### Tests
- 1 new test (`test_extracts_model_from_gemini_url`) pins the URL-path
  extraction so this regression can't sneak back.
- **213 Python + 37 TypeScript = 250 tests.** Ruff + mypy + tsc clean.

## [0.0.34] — 2026-05-19  &nbsp; · &nbsp; `@loupe/sdk` 0.0.19

### TypeScript SDK — dedup parity with Python

v0.0.31 fixed the double-capture bug in the Python SDK
(direct-SDK integration + universal-httpx both emitting for the same
call). The TypeScript side had the exact same shape — `wrapModel`
captures at the Vercel AI SDK level and `patchFetch` captures at the
fetch level, so a user calling `patchAll()` would have gotten two
Steps per logical call.

- Added `withSuppressedHttpCapture(fn)` + `isDirectCaptureActive()` in
  `@loupe/sdk/integrations`. Async-safe via `AsyncLocalStorage` —
  parallel tasks each see their own state.
- `wrapModel`'s `doGenerate` and `doStream` now wrap the SDK call in
  `withSuppressedHttpCapture(...)`.
- `patchFetch` / `wrapFetch` short-circuit when the flag is set.

### Tests
- 2 new TypeScript tests pin the suppression contract: one with the
  guard on (no Step emitted), one without (Step emitted as before).
- **212 Python + 37 TypeScript = 249 tests.** All clean.

## [0.0.33] — 2026-05-19

### Backend hardening — production-grade UI server

Two real findings from a careful audit of `loupe.ui.server`.

**Fix — `_find_trace` no longer trusts the URL path**

The lookup ran `traces_dir.glob(f"{trace_id}*.jsonl")` directly on the
URL-supplied id. A request to `/api/traces/*` would have matched every
file in the directory (and returned the first); `[abc]` is a bracket
pattern; `..` could in pathological setups have escaped the directory.

Now:
- Reject ids containing `/ \ \0 \n \r \t * ? [ ] { }`.
- Reject `.`, `..`, or any id starting with `.`.
- Reject ids over 128 chars.
- Defense in depth: every matched file is resolved and re-rooted under
  `traces_dir` — symlink-out attempts can't escape the directory.

**Fix — `POST /api/traces` now caps body size at 8 MB**

Before: an attacker (or buggy client) could ship a multi-GB JSON body
that uvicorn would buffer in memory before FastAPI got a chance to
reject it.

Now: a declared Content-Length over the cap fails fast with `413
Payload Too Large` before any body bytes are read. Any body that grows
past the cap during read also 413s. 8 MB is generous — large traces
should be written directly to `~/.loupe/traces/`, not uploaded.

### Tests
- 5 parametrized "evil trace_id" cases (`*`, `[abc]`, `..`, `.hidden`,
  `../etc/passwd`).
- 1 direct unit test of `_find_trace` for control chars and `?` that
  the HTTP layer would otherwise reject before reaching the server.
- 2 oversized-body tests: 9 MB payload + 100 MB declared Content-Length.
- **212 Python + 35 TypeScript = 247 tests.** Ruff + mypy + tsc clean.

## [0.0.32] — 2026-05-19

### UI — first-run onboarding + live state visibility

Three production-polish additions to the dashboard so it stops feeling
like a debug page and starts feeling like a hand-crafted forensic tool.

- **Live connection indicator.** Small pulsing dot in the topbar next to
  the brand: `connecting` → `live` (green, gentle 2.4s pulse) →
  `reconnecting` (amber) when the SSE stream drops. Honors
  `prefers-reduced-motion`.
- **First-run onboarding card.** When the home is empty, the viewer
  shows a three-step numbered walkthrough (`loupe demo` → install →
  `@trace`) with click-to-copy command pills. Each pill flashes a
  green ✓ confirmation. Replaces the inert `<pre>` block.
- **Loading skeleton.** During the very first `/api/traces` fetch the
  sidebar shows shimmering placeholder rows instead of momentarily
  flashing "No traces yet" before real data arrives.

### UI — error path
- Network failure on the first fetch now surfaces a red toast
  ("Could not reach the Loupe server.") instead of silently leaving
  the page blank.

### Tests
- 203 Python + 35 TypeScript = **238 tests**. No regressions.

## [0.0.31] — 2026-05-19

Two real-world production bugs surfaced when running Loupe end-to-end
against the live Anthropic SDK against a local stub of api.anthropic.com.

### Fixed — `kind` is now a free-form string

The schema and ingest validator used to gate step `kind` against a closed
enum: `{llm-call, tool-call, io, thought, error, custom}`. Real user code
records domain-specific kinds — `plan`, `retrieve`, `final`, `step.42`,
etc. — and `loupe verify` would then reject the user's perfectly normal
trace at the very first run.

- `kind` is now any non-empty string up to 64 chars.
- Recommended kinds are still listed in the spec + dashboard color-codes,
  but they are guidance, not gates.
- Wire format unchanged for canonical kinds; existing traces validate.

### Fixed — direct SDK and universal-httpx no longer double-capture

When `patch_all()` activates both the `anthropic` SDK integration and the
universal-httpx interceptor, the same logical call was previously
recorded twice — once at the SDK layer (richer view) and once at the
HTTP layer (raw view). Polluted every real trace.

- Added `loupe.integrations.suppress_http_capture()` — a ContextVar-backed
  guard that direct integrations wrap their wrapped SDK call with.
- universal-httpx now skips emit when the guard is on.
- Async-safe (ContextVar, not threadlocal).
- Direct integrations updated: anthropic, openai.

### Tests
- 1 new universal-httpx test pins the suppression contract.
- 3 new ingest tests cover free-form kinds + 64-char cap + empty rejection.
- 5 new schema-parity VALID payloads with user-defined kinds.
- **203 Python + 35 TypeScript = 238 tests.** Ruff + mypy + tsc clean.

### Real-world validation
- Captured a live agent → real anthropic SDK → real httpx → real Loupe
  → JSONL on disk → `loupe verify` + `show` + `stats` + `report` +
  `report --html` + `loupe ui` + full REST endpoint walk + path-traversal
  attempt = all clean. No traceback in any code path.

## [0.0.30] — 2026-05-19

### Added — `loupe purge` for trace lifecycle

Real users accumulate traces; you need a safe way to free disk
without writing your own `find`/`rm` scripts.

```
loupe purge --older-than 7d                  # dry-run (default)
loupe purge --older-than 30d --yes           # actually delete
loupe purge --older-than 30d --yes --keep-tagged   # protect the bench set
```

Safety design:
- **Dry-run by default.** Without `--yes`, prints what would be deleted,
  then exits 0 without touching disk. The hint line includes the exact
  `--yes` command to re-run.
- **`--keep-tagged` protects annotated traces.** Anything you've tagged
  is part of your benchmark set; the flag opts it out of purging.
- **Cleans up sidecars too.** Deletes both `{trace}.jsonl` and the
  matching `annotations/{trace}.json` + `.lock`.
- **Friendly duration parser.** `30s` / `15m` / `24h` / `7d` / `3600`
  (bare seconds). Garbage gets a readable error, not a Python traceback.
- **Empty-home no-op.** Newly-installed users running `purge` first
  get a clean "no traces" message instead of a crash.

### Tests
- 8 new purge tests: dry-run, --yes, --keep-tagged, no-match, invalid
  duration, empty home, plus 2 direct duration-parser unit tests.
- **194 Python + 35 TypeScript = 229 tests.** Ruff + mypy + tsc clean.

## [0.0.29] — 2026-05-18

### Added — CLI test coverage for the latest behavior

- `test_doctor_smoke_runs_lifecycle` — asserts `loupe doctor --smoke`
  exits 0, prints every lifecycle step name, and emits the
  "smoke test passed" summary line.
- `test_ui_no_auto_port_exits_when_busy` — opens a real socket on an
  ephemeral port, invokes `loupe ui --port <busy> --no-auto-port`,
  asserts exit 1 with the "already in use" message and no Python
  traceback in output.
- `test_ui_auto_port_walks_forward` — calls `_resolve_port` directly
  with a busy start port and asserts it returns a free port in
  `(start, start+9]`.

### Tests

- 186 Python + 35 TypeScript = **221 tests total**.
- Lint, mypy strict, and tsc strict all clean.

## [0.0.23] — 2026-05-18

### Changed — UX polish pass

After a real walkthrough of every command, the visible CLI got tightened up.

**`loupe list` is fully readable in an 80-col terminal again.**
- Old design buried `name` and truncated everything to 6-character gibberish
  (`resear…`, `framewo…`). New design: name first, trace_id shortened to
  8 chars (still uniquely identifies hundreds of traces), framework full,
  rest compact. Total fits in 80 columns with no truncation.
- Tagged traces get a leading ◉ in the name column instead of a separate
  `tags` column — saves the horizontal space.

**`loupe doctor` stops wrapping `pip install` hints across two lines.**
- All three columns in `status_table` are `no_wrap=True`. Values like
  `pip install 'loupe[pydantic-ai]'` stay on one line.

**Welcome screen + `loupe stats` lose phantom blank-line spacing.**
- `stack()` no longer auto-inserts blank lines between items. Callers add
  `Text()` explicitly where they want breathing room. This stops the
  triple-blank-lines visual that happened when a stack contained another
  stack (welcome screen, stats overview).

### Tests
- 182 Python + 35 TypeScript = **217 tests**. Lint + mypy strict + tsc all clean.

## [0.0.22] — 2026-05-18

### Added
- **`loupe report --html`** — render a captured trace as a *standalone
  single-file HTML viewer*. No external CDN, no fonts loaded over the
  network, no JavaScript dependencies. Double-click the `.html` file to
  view a failure report offline; share it via email or Slack.
- The viewer ships the forensic-dossier palette (charcoal + amber), a
  steps table, annotation cards, top-level error banner, and collapsible
  inputs/outputs/metadata for each failing step.
- 3 new tests verify: complete HTML output, no external network deps,
  annotation card rendering, and the CLI `--html --out FILE` flag.

### Tests
- 182 Python + 35 TypeScript = **217 tests**. Lint + mypy strict + tsc all clean.

## [0.0.21] — 2026-05-18

### Changed
- **Mypy is now enforced on CI** — no more `continue-on-error: true`. The
  whole `src/loupe/` tree passes `mypy --ignore-missing-imports` with zero
  errors (29 source files). Type drift now fails the build.

### Fixed
- `cli.py` — `step_line` variable disambiguation in `loupe show` (was
  shadowing the for-loop's `line: str` and confusing mypy).
- `cli.py` — `annotations` command function renamed to `annotations_cmd`
  to avoid shadowing `from __future__ import annotations`.
- `_tui.py` — `Group(*parts)` typed-ignore added; Rich accepts any
  renderable so the runtime is fine.
- `integrations/openhands.py` — added `attr-defined` ignore for the
  `agent_cls.step` assignment (we resolve `agent_cls` dynamically across
  module paths so mypy can't see the attribute).

### Tests
- 179 Python + 35 TypeScript = **214 tests**, all green.
- Type-check matrix now blocks merge on py3.11 / 3.12 / 3.13.

## [0.0.20] — 2026-05-18

### Added — CLI test coverage for the new commands
- 8 new `typer.testing.CliRunner` tests pin the behavior of `loupe verify`
  (single + `--all` + missing-arg + unknown-trace paths), `loupe stats`
  (populated + empty home), and `loupe diff` (success + unknown trace).
- 179 Python tests in total now, up from 171.

### Tests
- 179 Python + 35 TypeScript = **214 tests**, all green.

## [0.0.19] — 2026-05-18

### Added
- **`loupe diff <a> <b>`** — side-by-side comparison of two traces. Header
  row shows trace_id, name, framework, step count, duration delta, status.
  Step alignment uses `difflib.SequenceMatcher` over step names: `=` for
  matching steps, `~` for replaced, `-` for removed, `+` for inserted. The
  workflow for "did my prompt change make things better or worse?".

### Fixed (caught by hypothesis property tests)
- **Trace IDs and step IDs are now path-safety-validated at ingest.** A
  trace_id containing `/`, `\`, null byte, control chars, or `..` is now a
  clean `IngestError` instead of an unhandled `ValueError` from pathlib.
  Same for step_id. Max 128 chars enforced. The fuzzer found this case in
  the wild — exactly what property tests are for.
- One redactor property test had an edge case where the input contained
  the literal `[redacted]` string; the property now explicitly excludes
  that degenerate case from its precondition.

### Tests
- 171 Python + 35 TypeScript = **206 tests**, all green.

## [0.0.18] — 2026-05-18

### Added — Mastra integration (final TS framework gap closed)
- **`@loupe/sdk/mastra` — `patchMastraAgent(Agent)`** captures every
  `agent.generate(...)` and `agent.stream(...)` call on every instance.
  Records agent name, model id, method, prompt (redacted), the standard
  option fields, response text, finish reason, token usage, tool step count.
  Errors get the agent identifier in the Step name. New subpath export.
- 4 vitest tests (sync generate, async stream, prompt redaction, error capture).

### Tests
- 171 Python + 35 TypeScript = **206 tests**, all green.

## [0.0.17] — 2026-05-18

### Added — property-based proof + stats overview
- **`loupe stats`** CLI: aggregate overview — trace count, failure rate,
  step count, tags, median duration, framework histogram, failure-category
  histogram.
- **`tests/test_redact_property.py`** — 5 properties × ~400 hypothesis-
  generated inputs verify redactor: never raises, idempotent, non-mutating,
  type-preserving, never invents `[redacted]` in clean inputs.
- **`tests/test_ingest_property.py`** — generated payloads verify ingest
  either succeeds or raises `IngestError` with a non-empty message. No
  other exception type may escape.
- Hypothesis added to the `dev` extra.

## [0.0.16] — 2026-05-18

### Added
- **`loupe._telemetry`** — `shielded()` ctx manager, `call_safe()` helper,
  `emit()` function. Caught exceptions in Loupe's own instrumentation
  surface as `LoupeTelemetryWarning` — filterable, observable, but never
  thrown into user code.
- `openhands` extra populated.

## [0.0.15] — 2026-05-18

### Added — installable-everywhere base + framework breadth

**JSON schema embedded as package data**
- `docs/loupe-trace.schema.json` is now also shipped at
  `loupe/_data/loupe-trace.schema.json`. `loupe verify` works after a plain
  `pip install loupe` — no source tree required.
- Schema lookup prefers the embedded copy and falls back to the dev tree
  for monorepo editable installs.

**`loupe verify --all`**
- One command validates every captured trace under `~/.loupe/traces/` against
  the schema. Prints a green ✓ per trace, a red ✗ with the exact failing
  path + message otherwise. Exits non-zero if any fail.

**OpenHands integration** (`loupe.integrations.openhands`)
- Patches `openhands.controller.agent.Agent.step` (sync + async,
  auto-detected). Captures agent name, iteration number, returned action
  class, the agent's `thought`, and common action fields (command, path,
  url, code, content). All free-text fields run through the redactor.
- Tries the current module path and falls back to the legacy
  `opendevin.controller.agent` location for older installs.
- 3 new tests including a credential-redaction case.

**TypeScript `patchAll()` mirror**
- `@loupe/sdk/integrations` now ships a `patchAll()` helper that mirrors
  Python's `loupe.integrations.patch_all`. Same dict-return contract;
  enables `universal-fetch` automatically wherever `globalThis.fetch`
  exists (Node 18+, modern browsers, Deno, Bun).
- 4 vitest tests verify shape, presence-on-fetch-availability, idempotence,
  and absence-of-missing-deps.

### Tests
- 158 Python + 31 TypeScript = **189 tests**. Lint + tsc strict clean.

### New extras
- `pip install 'loupe[openhands]'`

## [0.0.14] — 2026-05-18

### Added — base completeness pass

**One-liner instrumentation: `patch_all()`**
- `from loupe.integrations import patch_all; patch_all()` turns on every
  integration whose dependency is installed. Returns a dict reporting which
  ones flipped on this call. Missing packages are skipped silently.
- 4 new tests cover return shape, missing-framework safety, idempotence,
  and pick-up behavior (the last runs in a fresh subprocess to avoid
  sys.modules pollution from other tests).

**CrewAI integration** (`loupe.integrations.crewai`)
- Patches `Crew.kickoff` / `kickoff_async`. Captures agent count, task count,
  the first 8 task descriptions, kickoff inputs, output text, and (when the
  framework reports it) total token usage.

**AutoGen integration** (`loupe.integrations.autogen`)
- Patches `ConversableAgent.generate_reply` / `a_generate_reply`. Captures
  agent name, the message list, reply text. Messages run through the
  redactor so credentials embedded in turn-by-turn text get scrubbed.

**`loupe verify` CLI command**
- `loupe verify <trace-id>` validates a captured JSONL trace against the
  canonical `docs/loupe-trace.schema.json` (Draft-2020-12). Exits 0 + prints
  a green ✓ on success, exits 1 + prints the schema-path that failed on
  violation. Auto-locates the schema file by walking up from the package.

**Performance benchmark**
- `tests/test_performance.py` asserts three hard contracts:
  - `record_step` averages under 100µs per call inside an active trace
  - A 10-step trace plus disk write completes in under 5ms (median of 20)
  - `record_step` with no active trace averages under 5µs (single
    ContextVar lookup + None check)
- Performance regression in the hot path now fails the build.

### Tests
- 155 Python + 27 TypeScript = **182 tests**. Lint + tsc strict clean.

### New extras
- `pip install 'loupe[llama-index]'`
- `pip install 'loupe[dspy]'`
- `pip install 'loupe[crewai]'`
- `pip install 'loupe[autogen]'`

## [0.0.13] — 2026-05-15

### Added — bit-identical cross-language wire format
- **Python serializer now uses compact separators** (`json.dumps(..., separators=(",", ":"))`)
  so the output matches `JSON.stringify(...)` defaults in the TypeScript SDK.
- **Canonical fixture rewritten** with fractional timestamps to sidestep the
  `1.0`/`1` divergence between Python's `json.dumps` and JS's `JSON.stringify`.
- **`packages/loupe-ts/tests/wire-format-snapshot.test.ts`** — TypeScript
  snapshot test that builds the same Trace as the Python fixture and asserts
  bit-identical bytes. Cross-language drift is now a CI failure in either
  language. This makes `docs/SPEC.md` § 6 self-enforcing.

### Added — more agent frameworks
- **LlamaIndex integration** (`loupe.integrations.llama_index`) — patches
  `BaseQueryEngine.query` / `.aquery` so every RAG call lands as a Step.
  Captures query string, engine class, response text, and source-document
  count. Queries pass through the redactor.
- **DSPy integration** (`loupe.integrations.dspy`) — patches
  `dspy.Module.__call__` (with `Program` fallback for older versions).
  Captures module class, kwargs (redacted), positional args (redacted),
  and prediction fields. Works across all DSPy module subclasses (Predict,
  ChainOfThought, ReAct, custom).
- 5 new tests across the two integrations (sync, async, redaction).

### Tests
- 143 Python + 27 TypeScript = **170 tests**. Lint + tsc strict clean.

## [0.0.12] — 2026-05-15

### Added — base proof + 2026 framework coverage

**Pydantic AI integration** (`loupe.integrations.pydantic_ai`):
- Monkey-patches `Agent.run` / `Agent.run_sync` to capture every model
  invocation as a Loupe Step. Captures model id, system prompt, user prompt,
  result text, and token usage when the framework reports it.
- Prompts run through the same redactor as everything else — credentials
  pasted into a user prompt never hit disk.
- New extra: `pip install 'loupe[pydantic-ai]'`. Surfaced in `loupe doctor`.
- 4 new tests covering sync, async, error capture, and redaction parity.

**Schema-vs-validator parity** (`tests/test_schema_validator_parity.py`):
- 16 parametrised cases run every valid + invalid payload through *both*
  `loupe.ingest.ingest()` and `jsonschema` against
  `docs/loupe-trace.schema.json`. The two MUST agree on every case.
- Prevents schema drift between the public spec (the schema file) and the
  production validator (the ingest function). If they ever disagree, the
  test fails and the build breaks.

**Wire-format golden snapshot** (`tests/test_wire_format_snapshot.py`):
- A 3-line JSONL fixture in `tests/fixtures/canonical_trace.jsonl` is the
  *exact* expected output for a known Trace. Any change to field order,
  key naming, or type coercion in the Python serializer fails the test
  immediately. Future TypeScript snapshot tests will validate against the
  same fixture — bit-identical cross-language wire format becomes a CI gate.
- Bonus: the same fixture round-trips through `loupe.ingest.ingest()` so
  the canonical example is always ingest-valid.

### Tests
- 138 Python + 26 TypeScript = 164 tests. Lint + tsc strict clean.

## [0.0.11] — 2026-05-15

### Added — stability & correctness pass

**TypeScript redaction parity (`@loupe/sdk/_redact`):**
- Bit-for-bit behavior match of the Python `_redact` module — same field-name
  patterns, same in-string credential patterns, same idempotence + non-mutation
  guarantees, same depth cap. Class-instance pass-through is explicit (we only
  walk plain objects).
- Wired into `universal.ts` (messages + prompt) and `ai-sdk.ts` (params).
  Credentials in fetch-captured payloads are now scrubbed before the JSONL.
- 9 vitest tests covering primitive pass-through, every common key style,
  Bearer + provider token patterns, deep nesting, idempotence, non-mutation,
  recursion-cap safety, plain-vs-class object distinction.

**Concurrency-safe annotation store:**
- Read-modify-write paths (`AnnotationStore.add`, `.remove`) now acquire an
  OS-level advisory lock (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows)
  for the duration of the operation.
- Writes are **atomic via tmp + `os.replace`** — readers always see either
  the previous complete file or the new complete file, never a partial one.
- Loader is **corruption-tolerant**: a malformed sidecar file returns `[]`
  instead of crashing the dashboard.
- 4 new tests, including a true multi-process race (30 worker processes
  adding annotations to the same trace; all 30 land in the final file).

**Real CLI test suite:**
- 14 new tests using `typer.testing.CliRunner` cover every public command:
  welcome screen, `version`, `doctor`, `providers`, `list` (empty + with traces),
  `show` (known + unknown), `tag`/`untag`/`annotations`, `export` (with and
  without tagged failures), `report` (stdout + `--out`), `init` (success +
  refuse-non-empty-dir), `demo`. Forces wide-COLUMNS so Rich tables don't
  truncate.

### Verified
- **116 Python + 26 TS = 142 tests, all green.**
- Lint clean (ruff). Tsc strict clean.
- CI matrix: py 3.11 / 3.12 / 3.13 + node 20 / 22 / 24 + cross-language gate.

## [0.0.10] — 2026-05-15

### Added — security + future-proofing
- **Automatic secret redaction** (`loupe._redact`) — every captured payload now
  runs through a deep-walking redactor before it hits disk.
  - Field-name patterns: any key containing `authorization` / `api_key` /
    `apikey` / `token` / `secret` / `password` / `bearer` / `private_key` /
    `access_key` / `x-auth` is replaced with `[redacted]`.
  - Value patterns: `Bearer <jwt>`, `sk-…` (OpenAI), `sk-ant-…` (Anthropic),
    `sk-or-…` (OpenRouter), `gsk_…` (Groq), `gho_…` / `ghp_…` (GitHub),
    `AIza…` (Google), and JWT structures are scrubbed inside any string value.
  - Walks dicts/lists/tuples to arbitrary nesting (depth-capped at 8 for safety).
  - Idempotent, non-mutating, never raises.
  - Wired into `httpx`, `anthropic`, and `openai` integrations so messages
    + prompts + system text are all clean before serialization.
  - 10 unit tests pin the behavior (96 Python tests total now).

- **Canonical JSON schema** at `docs/loupe-trace.schema.json` — Draft-2020-12,
  validates the wire-format payload accepted by `POST /api/traces`. Any
  language can now validate Loupe traces programmatically.

### Verified
- 96 Python + 17 TypeScript = 113 tests, all green.
- Lint clean. Tsc strict clean.

## [0.0.9] — 2026-05-15

### Added — 100% coverage push
- **49 LLM providers** auto-detected by the universal capture (was 13).
  Full list: anthropic, openai, gemini, mistral (+ codestral), cohere, xai,
  deepseek, ai21, reka, aleph-alpha, zhipu, baidu, alibaba (frontier);
  groq, cerebras, sambanova, together, fireworks, deepinfra, hyperbolic,
  anyscale, nebius, lambda, lepton, siliconflow, featherless, inference-net,
  modal, replicate, perplexity (inference); openrouter, portkey, kong-ai,
  vellum (aggregators); azure-openai, aws-bedrock, vertex-ai, watsonx,
  databricks (cloud); voyage, jina, nomic, huggingface (+ endpoints)
  (embedding); local / 127.0.0.1 / 0.0.0.0 (local).
- **OpenAI-compatible fallback** — unknown hosts whose request body has
  `messages` + `model` are captured as `openai-compatible:<host>`. This
  picks up LiteLLM proxies, internal gateways, and OpenAI-spec forks.
- **`loupe providers` CLI command** — gorgeous categorized listing of every
  detectable provider.
- **`contains` match strategy** for cloud hosts where the identifier sits
  in the middle of the FQDN (Bedrock `bedrock-runtime.*`, Vertex
  `*-aiplatform.googleapis.com`) so we match precisely without overmatching.
- **Authoritative wire-format spec** at `docs/SPEC.md` — the contract any
  third-party integration (in any language) writes against. Forward-compat
  rules + a hand-written-with-shell example.

### Internal
- Provider list extracted into `loupe.integrations._providers` (Python) and
  `_providers.ts` (TS). Keep them in sync — one source of truth per language.
- httpx + universal-fetch integrations both call `detect_provider_from_host`
  + `looks_like_openai_compatible` instead of inline dictionaries.

### Tests
- Python: 86 tests pass (+ 13 since 0.0.8: provider matching, contains
  strategy, openai-compatible fallback).
- TypeScript: 17 tests pass.
- Total: 103 across both packages. Lint + typecheck clean.

## [0.0.8] — 2026-05-15

### Added — Loupe now works with ANY language
- **`POST /api/traces`** HTTP ingest endpoint — any HTTP client (Go, Rust,
  Ruby, Java, curl, browser fetch, anything) can submit a Loupe-shaped JSON
  payload and the dashboard picks it up immediately via SSE.
  - New `loupe.ingest` module with strict-but-lenient validation
  - Required fields: `name`, `steps` (list, may be empty). Each step needs
    `kind` (`llm-call`/`tool-call`/`thought`/`error`/`io`/`custom`) and `name`.
  - Everything else gets sensible defaults (auto-generated `trace_id`, `now()`
    timestamps, etc.) so a one-line curl works.
  - Returns 201 with `{trace_id, name, framework, step_count}`.
- **`@loupe/sdk/universal` — `patchFetch()`** — TypeScript counterpart of the
  Python httpx patch. One line patches `globalThis.fetch` and captures every
  call to a known LLM provider (anthropic, openai, mistral, groq, gemini,
  cohere, together, openrouter, fireworks, deepseek, xai, perplexity, local).
  - Also exports `wrapFetch(original)` for non-global use (custom fetch
    instances, dependency injection in tests).
  - Streaming responses (`text/event-stream`) get a `streamed: true` flag.

### Tests
- Python: 52 tests pass (44 → 52 with 8 new ingest tests).
- TypeScript: 17 tests pass (12 → 17 with 5 universal-fetch tests).
- Total: 69 across both packages. Lint + typecheck clean.

### Docs
- README "Any other language — Go, Rust, Ruby, Java, curl" section with a
  copy-paste curl example.
- The wire-format contract is now treated as part of the public surface and
  documented in docs/SPEC.md.

## [0.0.7] — 2026-05-15

### Added
- **Universal HTTP capture** (`loupe.integrations.httpx.patch()`) — one-line
  monkey-patch over `httpx.Client.send` / `AsyncClient.send` that detects calls
  to known LLM providers (Anthropic, OpenAI, Mistral, Groq, Gemini, Cohere,
  Together, OpenRouter, Fireworks, DeepSeek, xAI, Perplexity, Ollama/local) and
  records each as a `llm-call` Step with model + prompt + usage + status.
  Works with *any* Python client that uses httpx under the hood — instructor,
  dspy, llamaindex, custom proxies, etc.
- New optional extra: `pip install 'loupe[universal]'`
- 5 new tests pinning the universal-capture behavior (44 → 49 tests total).

### Changed (CLI redesign)
- **`loupe` with no args now shows a welcome screen** with adaptive next-step
  hints (different copy when you have 0 traces vs. when you have some).
- **New `loupe start` command** — interactive first-run: seeds samples if
  needed, opens the browser, starts the dashboard.
- All command outputs use a unified, calm visual language: amber-on-charcoal
  banner, `●/○` status dots, hairline tables, no heavy box-drawing.
- `loupe doctor` now reports the universal integration too; correctly escapes
  square brackets in `pip install 'loupe[xxx]'` hints (was broken in 0.0.6).
- `loupe show` got color-coded step kinds (llm-call=blue, tool-call=magenta,
  error=red, thought=dim).
- Shared `loupe._tui` module so every command renders from one palette.

### Docs
- README quickstart rewritten around the universal capture path so it's clear
  Loupe works with *any* Python LLM client, not just LangChain/Anthropic/OpenAI.

### Verified
- 49 Python tests pass · 12 TS tests pass · lint + typecheck clean.

## [0.0.6] — 2026-05-15

### Added
- **Streaming-response capture** for both Anthropic and OpenAI integrations
  - New `loupe.integrations._streaming` module with `TracedSyncStream` and
    `TracedAsyncStream` pass-through proxies
  - Captures `messages.create(stream=True)` (Anthropic) by accumulating
    `content_block_delta` text deltas and reading usage from `message_start` /
    `message_delta` events
  - Captures `chat.completions.create(stream=True)` (OpenAI) by accumulating
    `choices[0].delta.content` chunks and reading usage from the final chunk
  - Streams are pass-through: callers still get each event in real time;
    Loupe finalizes a single Step when iteration ends or the context exits
  - `streamed: true` is recorded in step outputs so streaming runs can be filtered
- `loupe demo` — seed three realistic sample traces (happy path, destructive
  failure, slow tool-call) plus a pre-baked annotation on the failure so a
  brand-new install isn't an empty dashboard.

### Changed
- CI uses `actions/checkout@v5`, `actions/setup-python@v6`, `actions/setup-node@v5`
  (removes the Node 20 deprecation warnings).

### Verified
- 36 Python tests pass · 12 TypeScript tests pass · 48 total
- GitHub Actions: 7/7 jobs green on first push to main

## [0.0.5] — 2026-05-15

### Added (Python)
- `loupe report <trace-id>` — render a shareable markdown case file with
  top-level error, annotations, step table, and per-failure detail. Designed
  to paste into a Twitter thread, an issue, or a blog post.
- `loupe init <name>` — scaffold a starter agent project (`agent.py`,
  `README.md`, `.gitignore`). Goes from zero to a captured trace in 4 commands.
- `loupe.report`, `loupe.scaffold` modules

### Added (TypeScript)
- **`@loupe/sdk`** package shipped (`packages/loupe-ts/`)
  - `trace()` higher-order function, function-wrapper style
  - AsyncLocalStorage-based context propagation (Python ContextVar equivalent)
  - `recordStep / openStep / closeStep` primitives
  - `JSONLStore` writes identical wire format to `~/.loupe/traces/`
- **`@loupe/sdk/ai-sdk`** subpath
  - `wrapModel(model)` — proxy any Vercel AI SDK LanguageModel
  - `loupeMiddleware()` — drop-in for `wrapLanguageModel({ middleware })`
  Captures llm-call steps with model, params, text, finish reason, token usage
- 12 vitest tests, strict tsconfig, tsup build → ESM + CJS + d.ts

### Added (UI)
- Proportional timeline: cells flex-grow by step duration (weight 1..5)
- Duration label on each timeline cell when ≥ 0.5 ms

### CI
- New `.github/workflows/ci.yml` with three jobs:
  - python: lint + pytest on 3.11 / 3.12 / 3.13
  - typescript: typecheck + vitest + tsup build on node 20 / 22 / 24
  - cross-language: runs both example demos and asserts the shared
    `~/.loupe/traces/` contains traces from both languages — the wire format
    contract is now enforced in CI.

### Verified
- 28 Python tests pass · 12 TypeScript tests pass · 40 total
- Lint clean (ruff) · Typecheck clean (tsc --strict)
- End-to-end: TS example trace appears alongside Python ones in `loupe ui`

## [0.0.4] — 2026-05-14

### Added
- `loupe.annotation` — JSON sidecar store at `~/.loupe/annotations/`
- `loupe.bench.export_jsonl` — bundle annotated failures into LoupeBench-format JSONL
- CLI: `loupe tag`, `loupe untag`, `loupe annotations`, `loupe export`
- UI: tag-this-failure inline form (category/severity/notes/mitigation)
- UI: stats banner + sidebar search + tagged-step ◉ marker
- API: `GET /api/stats`, `GET|POST|DELETE /api/traces/{id}/annotations`

### Verified
- 25 unit tests across core, store, integrations, UI, annotation, bench
- Lint clean (ruff)
- End-to-end: real LangGraph failure → captured → tagged via UI → exported

## [0.0.3] — 2026-05-14

### Added
- `loupe.ui` — FastAPI server + single-page forensic dashboard
- `loupe ui` CLI command
- Three-pane layout: timeline / step list / evidence
- Forensic dossier aesthetic: charcoal + amber, EB Garamond + JetBrains Mono
- Keyboard navigation (arrow keys between steps)

## [0.0.2] — 2026-05-14

### Added
- `loupe.integrations.langchain` — drop-in `LoupeCallbackHandler` for any
  LangChain runnable, including LangGraph graphs
- Captures LLM calls, tool calls, chain (graph node) starts/ends, agent
  actions, errors — with start/end pairing for duration tracking
- `examples/langgraph_demo.py` — verified end-to-end with FakeListChatModel

## [0.0.1] — 2026-05-14

### Added
- Core `@trace` decorator (sync + async, ContextVar-isolated)
- `Step` and `Trace` dataclasses
- `JSONLStore` writing to `~/.loupe/traces/{trace_id}.jsonl` — the canonical
  wire format (stable forever)
- CLI: `loupe list`, `loupe show <id>`
- `record_step()` helper for framework integrations
- `examples/hello_loupe.py` demonstrates a simulated failing agent
