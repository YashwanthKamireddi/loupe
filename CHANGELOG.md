# Changelog

All notable changes to Loupe. Loupe follows [SemVer](https://semver.org/).

## [Unreleased]

### Planned for 0.1.0
- DuckDB indexer for fast search across many traces
- Mastra agent framework integration (TS)
- SAE-based circuit attribution (the research artifact)

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
