# Changelog

All notable changes to Loupe. Loupe follows [SemVer](https://semver.org/).

## [Unreleased]

### Planned for 0.1.0
- Cluster analysis across larger annotated corpora (hierarchical, not just frequency)

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
