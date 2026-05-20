# Loupe — Friction-zero UX overhaul (architecture, CLI, dashboard)

> The goal: a first-time developer can install Loupe, run a real
> captured agent trace, and understand the dashboard in **under two
> minutes** — without writing a line of code, debugging anything,
> or making a single configuration decision.

## What changed since the previous UX plan

The earlier draft of this document scoped 8 incremental phases that
left the existing architecture in place. The user feedback after a
real walkthrough was sharper: **"restart the architecture from
scratch if needed; remove friction permanently."** This rewrite goes
further. It now includes:

- An **architectural restructuring** (single config layer, unified
  provider interface, command consolidation 21 → 7).
- A **complete frontend redesign** for world-class dashboard UI/UX.
- A **backend logic refinement** pass (typed errors with hints, auto-
  discovery service, consistent return shapes).

## Research — world-class tools (extended)

### CLI design language references

| Tool | Friction-removal move |
|------|------------------------|
| **Stripe CLI** | `stripe login` opens a device-flow OAuth in the browser. No key paste. |
| **gh (GitHub CLI)** | `gh auth login` interactive; `gh repo create` walks through choices. |
| **Vercel CLI** | `vercel` from any folder auto-detects + deploys. Zero config. |
| **Cargo** | `cargo new` + `cargo run` covers 80% of the lifecycle. |
| **flyctl** | `fly launch` does config + secrets + deploy in one prompt flow. |
| **Astro** | `npm create astro@latest` interactive template picker. |
| **Bun** | Single binary, zero config, instant start. |
| **Modal CLI** | `modal token new` opens browser; subsequent runs auth-free. |
| **Supabase CLI** | `supabase init` then `supabase start`; everything project-local. |

### Dashboard / web-UI references

| Product | What we steal |
|---------|----------------|
| **Vercel dashboard** | Empty-state hero with one-click "deploy from template" actions. |
| **Linear** | Keyboard-driven everything; no mouse needed for any common path. |
| **Stripe dashboard** | Inline help drawer that explains every technical term. |
| **Sentry** | First-visit guided tour with skip + checkpoint progress. |
| **Figma** | Tooltips on every glyph; long-press for full explanation. |
| **Raycast** | Command palette (Cmd-K) as the primary navigation surface. |
| **GitHub web** | Inline `?` icons next to every advanced setting. |

### LLM observability competitors

| Tool | Where they hurt | What we beat them at |
|------|------------------|----------------------|
| LangSmith | Hosted only; paid tier; LangChain-coupled. | Local-first, free, framework-agnostic. |
| Langfuse | Self-hosting is fiddly; setup is 3+ env vars. | Local-only by default; one `pipx install`. |
| Phoenix (Arize) | OpenInference-only adapter. | Universal-httpx + 11 direct SDK integrations. |
| Helicone | HTTP-proxy only; misses decision steps. | Captures plan/thought/tool-call/error steps. |
| OpenLLMetry / Traceloop | OTLP backend required; complex. | Single JSONL file per run; no infrastructure. |

### Pattern distillation — what every great tool has

1. **One install command** — `pipx install`, `brew install`, `cargo install`.
2. **Interactive setup wizard on first run** — detects state, asks ≤3 questions.
3. **Browser-based auth** — no manual key handling.
4. **A "just try it" path** — proof of value in <60 seconds.
5. **Smart defaults everywhere** — user only specifies the non-obvious.
6. **Progressive disclosure** — advanced features hidden until needed.
7. **Self-healing** — auto-rebuild caches, never tell user to "fix" infrastructure.
8. **"Did you mean?" on typos.**
9. **Inline help + examples** in every `--help`.
10. **Command palette / fuzzy-find** for navigation.

## Friction audit — every confusing moment in Loupe today

A first-time developer's journey, with every "wait, what?" marked:

| Step | Today | Friction | Severity |
|------|-------|----------|----------|
| 1. Install | `git clone` + `pip install -e '.[ui]'` + symlink to PATH | Clone repo; manual venv; PATH hack | HIGH |
| 2. Pick provider + get key | README → aistudio → copy key → `set -Ux GEMINI_API_KEY` | 4 steps across browser + shell; fish vs bash differ | HIGH |
| 3. First trace | `loupe init my-agent` + install `google-genai` + write a question | Need to install a sub-package; write Python args | HIGH |
| 4. View trace | `loupe ui` + open browser manually | Two terminals + URL copy | MEDIUM |
| 5. Understand dashboard | No tour, no inline help | "What is a step?" left as homework | MEDIUM |
| 6. Pick which extras | 11 optional dependencies | Decision fatigue; wrong subset installed | MEDIUM |
| 7. Run advanced (`loupe attribute --backend sae`) | Requires ~500 MB `[interp]` extra | Heavy deps surprise the user | LOW |
| 8. Recover from typos (`loupe sho`) | Full error, no suggestion | "Did you mean `loupe show`?" not offered | LOW |
| 9. 21 top-level commands | Flat namespace | Cognitive load; can't tell user-level vs admin-level | MEDIUM |
| 10. Multi-provider config | Each integration uses its own env var pattern | No unified config; can't enumerate state | LOW |
| 11. Update Loupe | No version check / update path | User pinned to whatever they cloned | LOW |
| 12. Discover features | `loupe --help` is alphabetical alphabet soup | No "here's what you can do today" framing | MEDIUM |

## The plan — 7 phases

### Phase 1 — The "one command" entry point ⬆️ highest impact

**Replace the welcome screen + multi-command discovery with a single command that does everything.**

- **A.** `loupe` (no args) becomes a **smart router**:
  - First run (no config + no traces) → auto-launch `loupe setup`
  - Setup done, no traces → auto-launch `loupe try` (one-shot demo)
  - Has traces → opens the dashboard
- **B.** `loupe setup` — interactive wizard:
  1. Detect existing keys (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`)
  2. If none, ask which provider with one-line descriptions
  3. Open browser to the right key-creation page (`webbrowser.open`)
  4. Prompt for the key in the terminal (no shell-quoting risk)
  5. **Persist to `~/.loupe/config.toml`** (also writes the shell-rc export for backwards compat)
  6. Test the key with a tiny ping
  7. Pick a default model per provider
- **C.** `loupe try` — one-shot demo trace using the configured key + default model. Auto-opens the dashboard at the end.

### Phase 2 — Zero-code usage paths

**Make Loupe usable end-to-end WITHOUT writing Python.**

- **D.** `loupe ask "<question>"` — single LLM call, captured. Like `gh copilot` but with a captured trace.
- **E.** `loupe chat` — interactive REPL. Built-in slash commands: `/tag`, `/show`, `/diff`, `/dashboard`, `/help`.
- **F.** `loupe run -- python script.py` — auto-instrument any Python invocation by injecting `patch_all()` via a `sitecustomize` shim. Captures every LLM call the script makes, no source edits.

### Phase 3 — Command consolidation (21 → 7)

**Cognitive-load reduction at the CLI level.**

User-facing top-level (always visible in `loupe --help`):

1. `loupe setup` — configure your providers
2. `loupe ask <question>` — one-shot LLM call with capture
3. `loupe chat` — interactive REPL
4. `loupe ui` (alias `start`) — open the dashboard
5. `loupe list` — your captured runs
6. `loupe show <id>` — inspect one run
7. `loupe explain <topic>` — built-in topic explainer

Everything else moves under `loupe advanced`:

```
loupe advanced attribute  cluster  replay  index  verify
                diff      report   export  purge  providers
                doctor    init     tag     untag  annotations
```

This is the **most important architectural change** — top-level `--help` becomes legible.

### Phase 4 — Architecture refactor: single config layer

**Replace 5+ env vars with `~/.loupe/config.toml`.**

```toml
# ~/.loupe/config.toml

[default]
model = "gemini-2.5-flash"

[providers.gemini]
api_key = "AIza..."

[providers.anthropic]
api_key = "sk-ant-..."

[providers.openai]
api_key = "sk-..."

[attribution]
backend = "sae"
explain = true

[index]
disabled = false
```

- **G.** New `loupe.config` module — read/write `~/.loupe/config.toml`.
- **H.** Provider abstraction — single `loupe.providers.Provider` interface; each provider (gemini / anthropic / openai) implements `chat()` + `model_id()` + `usage()`. Used by `setup`, `ask`, `chat`, `try`, `replay`.
- **I.** Env vars still work as overrides (`GEMINI_API_KEY` wins over `config.toml`). Backwards compatibility intact.

### Phase 5 — Dashboard as a world-class first-time experience

**Frontend overhaul — match Vercel / Linear / Sentry quality.**

- **J.** First-visit guided tour (5 steps, skippable, progress saved to localStorage):
  1. Sidebar overview ("these are your case files")
  2. Filter chips ("All / Failed / Tagged")
  3. Timeline + step list
  4. Evidence pane + the tag-this-failure flow
  5. Export → LoupeBench
- **K.** Inline `?` tooltips on every technical term (SAE, circuit attribution, LoupeBench category, hook layer, etc.) — click for a plain-English one-paragraph explanation.
- **L.** Command palette (Cmd-K / Ctrl-K) — fuzzy-find any action: "tag", "export", "attribute", "open trace abc123".
- **M.** Settings panel — manage `~/.loupe/config.toml` from the dashboard (add/remove keys, pick default model, toggle attribution backend).
- **N.** Empty-state hero — when no traces exist, big "Try the demo" + "Capture from your code" + "Set up another provider" cards. Replaces the current ASCII walkthrough.
- **O.** Live agent panel in the sidebar (toggleable) — paste a prompt, see the captured trace stream in. Eliminates the need to leave the dashboard for `loupe ask`.

### Phase 6 — Friction-zero install + update

- **P.** Publish to PyPI: `pip install loupe` and `pipx install loupe` both work.
- **Q.** Collapse 11 extras → 3 tiers:
  - `loupe` — core SDK + CLI + sensible defaults
  - `loupe[ui]` — dashboard + universal-httpx + all framework integrations
  - `loupe[research]` — heavy ML deps (torch / sae-lens / transformer-lens)
- **R.** One-line installer script for non-Python users (`curl loupe.dev/install | sh`).
- **S.** Auto-update notice — `loupe` on startup checks PyPI once a day; prints a single line if a new version is out. Opt-out via config.

### Phase 7 — Self-healing + smart errors

- **T.** "Did you mean?" — `difflib.get_close_matches` on the command list when an unknown command is invoked.
- **U.** Auto-rebuild stale index when `loupe list` detects pollution (header-count vs disk-file-count mismatch above a threshold).
- **V.** Auto-recover from corrupt files (skip + log, never crash the CLI).
- **W.** Every error path proposes the next command (already partially done — finish the remaining 5 paths).
- **X.** `loupe doctor --fix` — non-destructively repair anything fixable (rebuild index, recreate dirs, regenerate config).

## What needs architectural restructuring (and what doesn't)

### Restructured

| Layer | Old | New |
|-------|-----|-----|
| **Config** | 5+ env vars across modules | Single `~/.loupe/config.toml`, env-var override still works |
| **Command surface** | Flat 21 commands | 7 top-level + `loupe advanced …` |
| **Provider abstraction** | Each integration has its own pattern | Unified `Provider` interface (chat/model/usage) used by setup, ask, chat, try, replay |
| **Entry point** | Welcome screen + manual command picking | Smart router: `loupe` figures out what you want |
| **CLI-dashboard duality** | Disjoint surfaces | Dashboard becomes primary UI; CLI is the power-user path |

### Untouched (public contract — stays stable forever)

- Wire format (`~/.loupe/traces/{id}.jsonl`) — same Draft-2020-12 schema
- Storage layout (`~/.loupe/`) — same directory structure
- `@trace` + `record_step` Python API — for users who DO want to write code
- HTTP ingest endpoint (`POST /api/traces`) — language-agnostic capture path
- TypeScript SDK wire-format parity — byte-identical JSONL output

## Sequencing + estimated effort

| Order | Phase | Effort | Why this order |
|-------|-------|--------|----------------|
| 1 | Phase 1 (smart router + setup + try) | ~5 h | Biggest perceived improvement — collapses the first 30 minutes of friction to 90 seconds |
| 2 | Phase 2 (ask / chat / run) | ~5 h | Lets non-Python developers use Loupe |
| 3 | Phase 3 (command consolidation) | ~3 h | `--help` becomes legible; sets the stage for "explain" + docs |
| 4 | Phase 7 (self-healing + did-you-mean) | ~2 h | Cheap, high-confidence polish; should land alongside command consolidation |
| 5 | Phase 4 (config.toml) | ~4 h | Architecture — unblocks future config-driven features |
| 6 | Phase 6 (install + extras + update) | ~3 h | Real impact requires PyPI publishing (out of code scope here; produces install artifacts) |
| 7 | Phase 5 (dashboard overhaul) | ~8 h | Largest visual change; do last to focus on it without other-phase churn |

**Total: ~30 hours of focused work.** Phases 1+2+3 alone deliver ~70% of the perceived improvement.

## Acceptance criteria

After this plan ships, a brand-new developer can:

1. ✅ Install Loupe in one command (`pipx install loupe`).
2. ✅ Set up their first provider in 90 seconds (`loupe setup` → browser → paste → ✓).
3. ✅ See a real captured trace in **under 2 minutes total** (`loupe try`).
4. ✅ Use every common feature **without writing a line of code** (`loupe ask`, `loupe chat`, `loupe run`).
5. ✅ See only the 7 commands they need in `--help`; advanced ones gated.
6. ✅ Understand the dashboard the first time they open it (guided tour).
7. ✅ Hit zero "what is this?" moments thanks to inline tooltips + `loupe explain`.
8. ✅ Recover from typos with "did you mean?" suggestions.
9. ✅ Self-heal from any index/cache corruption without manual intervention.
10. ✅ Open the command palette (Cmd-K) in the dashboard and do any action by name.

**That's the bar. Anything less and we're not done.**

## What this plan does NOT do

- Does **not** change the wire format (`~/.loupe/traces/*.jsonl` contract stays stable).
- Does **not** introduce a hosted service (keeps the local-first positioning).
- Does **not** require any user to learn a new concept — every new surface uses existing vocabulary.
- Does **not** add dependencies to the core install (heavy stuff still lives behind `loupe[research]`).
- Does **not** break the `@trace` + `record_step` API for users who do want to write code.

## After this plan ships

The next document up is `docs/V0_2_ROADMAP.md` — the research work continues (SAE backend already shipped; clustering analysis already shipped; what remains is scale: distributed indexing, multi-trace correlation analysis, more SAE families).

But that's after. First we land friction-zero.
