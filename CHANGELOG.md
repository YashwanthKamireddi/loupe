# Changelog

All notable changes to Loupe. Loupe follows [SemVer](https://semver.org/).

## [Unreleased]

### Planned for 0.1.0
- DuckDB indexer for fast search across many traces
- SAE-based circuit attribution (the research artifact)

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
