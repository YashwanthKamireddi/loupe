# Loupe — Architecture

> One pager for "how does this work?" Read this before opening a PR.

## The promise (the one-line spec)

**A captured agent run = a single JSONL file at `~/.loupe/traces/{trace_id}.jsonl`.**
Line 0 is the trace header. Lines 1..N are steps. Schema in [`SPEC.md`](SPEC.md).

Every other piece of Loupe is in service of that file: write it, read it, validate it, annotate it, render it.

---

## The layers

```
┌─────────────────────────────────────────────────────────────────────────┐
│  USER CODE: @trace, record_step, framework integrations, HTTP ingest    │
└──────────────┬──────────────────────────────────────────────────────────┘
               │ writes
               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  WIRE FORMAT (the contract)                                              │
│   ~/.loupe/traces/{trace_id}.jsonl     — immutable once written          │
│   ~/.loupe/annotations/{trace_id}.json — sidecar, atomic + locked         │
│   docs/loupe-trace.schema.json         — Draft-2020-12, public contract  │
└──────────────┬──────────────────────────────────────────────────────────┘
               │ read
               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PRESENTATION                                                            │
│   loupe ui   — local FastAPI dashboard, SSE-live, forensic palette       │
│   loupe report (markdown + standalone HTML) — shareable case files       │
│   loupe show / list / stats / diff / verify — terminal-side inspection   │
└─────────────────────────────────────────────────────────────────────────┘
```

The seam between layers is *the file*. Anything that can write the JSONL — a Python agent, a TypeScript agent, a Go program POSTing to `/api/traces`, a shell script doing `echo … > file.jsonl` — is a first-class Loupe citizen.

---

## Capture path (Python)

`@trace(...)` wraps a function. On enter:

1. Creates a `Trace` dataclass with a fresh UUID
2. Pushes it onto the `_current_trace` `ContextVar` (asyncio-safe; one trace per task)
3. Calls the wrapped function
4. Any nested `record_step(...)` reads `_current_trace`, builds a `Step`, appends
5. On exit: marks failure metadata if there was an exception, calls `store.save(trace)`

`store.save` writes the JSONL **compactly** (`separators=(",", ":")`) so every byte matches `JSON.stringify` in the TypeScript SDK. The snapshot test at `tests/test_wire_format_snapshot.py` enforces this.

Framework integrations (`loupe.integrations.*`) monkey-patch popular SDKs (LangChain callback, Anthropic Messages.create, OpenAI Completions.create, etc.) and call `record_step` underneath. Users never see the integration internals.

The universal `httpx` integration is a fallback: patch `httpx.Client.send` once, every LLM call routed through httpx becomes a `llm-call` Step. Combined with `looks_like_openai_compatible()` it also captures unknown hosts whose request body has `messages` + `model` — that's how LiteLLM proxies and internal gateways get covered without per-host plumbing.

---

## Capture path (TypeScript)

`@loupe/sdk` mirrors the Python primitives. `trace()` is a higher-order function. `AsyncLocalStorage` is the equivalent of Python's `ContextVar`. `JSONLStore` writes the same wire format.

Provider catalog and redaction module are duplicated between languages on purpose — one is the source of truth for each runtime. The cross-language snapshot test in `loupe-ts/tests/wire-format-snapshot.test.ts` validates against the same fixture as the Python snapshot test. Drift fails CI.

The Vercel AI SDK integration (`@loupe/sdk/ai-sdk`) and Mastra integration (`@loupe/sdk/mastra`) handle framework-specific quirks; the universal fetch patch (`@loupe/sdk/universal`) covers everything else.

---

## HTTP ingest (any language)

`POST /api/traces` on the running `loupe ui` server accepts a single-object payload (header fields + a `steps` array). `loupe.ingest.ingest()`:

1. Validates field types and the `kind` enum
2. Path-injection-checks `trace_id` and `step_id` (`/`, `\\`, null bytes, control chars all rejected)
3. Builds the same `Trace` / `Step` dataclasses an in-process capture would
4. Writes via the standard `JSONLStore.save`

A separate test (`tests/test_schema_validator_parity.py`) runs every valid + invalid payload through both `jsonschema` (against `loupe-trace.schema.json`) and the in-house validator and asserts they agree. The public spec and the production code stay in lockstep.

---

## Redaction

Every captured payload runs through `loupe._redact.redact()` before serialization:

- **Field-name patterns**: case-insensitive substring match on `authorization`, `api_key`, `apikey`, `secret`, `token`, `password`, `bearer`, `private_key`, `access_key`, `x-auth`. Any key containing those gets its value replaced with `[redacted]`.
- **Value patterns**: regex over the string for `Bearer …`, `sk-…`, `sk-ant-…`, `sk-or-…`, `gsk_…`, `gho_…`, `ghp_…`, `AIza…`, JWT structures.
- **Walks** dicts/lists/tuples to depth 8, never mutating.

Property tests fuzz the contract (`test_redact_property.py`): never raises, idempotent, type-preserving, never invents `[redacted]` in clean input. TS parity in `_redact.ts`.

---

## Annotation store (concurrency)

Annotations live at `~/.loupe/annotations/{trace_id}.json`. The store implements:

- **Read** path: lock-free; either the previous complete file or the new complete file (atomic via `os.replace`).
- **Write** path (`.add`, `.remove`): grabs a per-trace advisory lock (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows) for the duration of the read-modify-write cycle, then renames a tmp file into place.

Tested under real multi-process contention: 30 processes adding annotations to the same trace; all 30 land. Corrupt files are tolerated (loader returns `[]`).

---

## Dashboard (loupe ui)

FastAPI + a single-page static SPA in `loupe/ui/static/`. The interesting bits:

- **`GET /api/events`** — Server-Sent Events. A background task polls `~/.loupe/traces` and `~/.loupe/annotations` every 1.5s. New files → `new_trace` / `annotation_changed` events. The client subscribes via `EventSource`, refreshes when notified.
- **`POST /api/traces`** — the HTTP ingest endpoint described above.
- **`GET /api/traces/{id}/report`** — server-side render of the markdown case file.

The frontend has no framework — vanilla JS + CSS. Three reasons: instant load, zero dependency upgrade chores, every line is auditable in one read. The forensic dossier palette (charcoal + amber) is the design language; see `style.css` for the variables.

---

## Test architecture

| Test file | What it locks down |
|---|---|
| `test_trace.py` | core @trace contract |
| `test_store.py` | JSONL serialization |
| `test_redact.py` + `test_redact_property.py` | redactor contract + fuzz |
| `test_annotation_concurrent.py` | concurrent-write safety (multi-process) |
| `test_schema_validator_parity.py` | schema ↔ ingest validator agreement |
| `test_wire_format_snapshot.py` (PY + TS) | byte-identical cross-language output |
| `test_lifecycle.py` | end-to-end user flow (13 CLI commands in one test) |
| `test_performance.py` | hot path budget (< 100µs/step, < 5ms/trace) |
| `test_cli.py` | every public CLI command via CliRunner |
| `test_<integration>.py` | each framework integration |

CI runs all of them on Python 3.11 / 3.12 / 3.13 and Node 20 / 22 / 24. Mypy strict. Ruff strict.

---

## Adding a new framework integration

Two file edits + two tests is the whole drill:

1. Create `loupe/integrations/{framework}.py`. Use the existing modules as templates — they follow the same pattern: try to import the framework; monkey-patch the user-facing entry method; pass inputs through `redact()`; emit `Step` objects via `current_trace().add_step()`.
2. Add the framework to `loupe.integrations._providers.ALL_PROVIDERS` if it has a known LLM endpoint.
3. Add a new optional dep to `pyproject.toml` under `[project.optional-dependencies]`.
4. Add the integration to the `integrations` list in `loupe.cli.doctor` and `loupe.integrations.patch_all`.
5. Write a test that plants a fake module into `sys.modules` and verifies capture (see `test_pydantic_ai.py` for the cleanest example).

That's it. No core changes required.

---

## Adding a new CLI command

`@app.command("name")` decorator on a function in `loupe/cli.py`. Add the command's name to the module docstring at the top of the file (it's a CLI help reference, not a docstring of the module).

Use the helpers in `loupe._tui` for visual consistency:
- `banner(subtitle, version=...)` for the top of any multi-line output
- `kv_table(rows)` for key-value sections
- `status_table(rows)` for the doctor-style three-column status grids
- `cmd`, `hint`, `section` for inline elements
- `render_padded(...)` to print a Group with the standard left/right padding

Add a CLI test in `tests/test_cli.py` using `typer.testing.CliRunner`.
