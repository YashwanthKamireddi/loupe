# Changelog

All notable changes to Loupe. Loupe follows [SemVer](https://semver.org/).

## [Unreleased]

### Planned for 0.1.0
- DuckDB indexer for fast search across many traces
- Streaming-response support for Anthropic + OpenAI integrations
- Mastra agent framework integration (TS)
- SAE-based circuit attribution (the research artifact)

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
