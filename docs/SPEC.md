# Loupe Wire Format — v1.0

> **Status:** stable · **Format:** JSONL · **License:** CC-BY-4.0 (the spec; the implementations are MIT)

This document is the **authoritative contract** for Loupe traces. Any tool, in any language, that writes data conforming to this spec can be inspected by `loupe ui`, tagged for LoupeBench, exported as a markdown case file, etc.

The Python and TypeScript SDKs are reference implementations. There is no privileged language. **Anything that can write JSON files (or `POST` JSON over HTTP) is a first-class citizen.**

---

## 1. Storage layout

```
~/.loupe/
  traces/
    {trace_id}.jsonl       ← one file per trace, append-only, immutable once closed
  annotations/
    {trace_id}.json        ← optional sidecar; LoupeBench tags
```

The trace directory may be overridden via the `LOUPE_HOME` environment variable.

A trace file is a sequence of **newline-delimited JSON objects** (JSONL). The first line is the trace header. Every subsequent line is a step. There is no separator other than `\n`.

```jsonl
{"_type":"trace","trace_id":"…","name":"…", …}
{"_type":"step","step_id":"…","kind":"…", …}
{"_type":"step","step_id":"…","kind":"…", …}
```

---

## 2. The `trace` record (line 0)

| field | type | required | notes |
|---|---|---|---|
| `_type` | `"trace"` | yes | exact literal |
| `trace_id` | string | yes | 32-hex-char UUID4 recommended; any unique string is OK |
| `name` | string | yes | human-readable label of the agent run |
| `framework` | string \| null | no | "langgraph", "anthropic", "go-anthropic", "ai-sdk", anything |
| `started_at` | number | yes | unix seconds, float (sub-millisecond precision allowed) |
| `ended_at` | number \| null | no | unix seconds; if absent, the run is considered open |
| `metadata` | object | no | free-form. Recognized keys: `failed: bool`, `error: string` |

The dashboard surfaces `metadata.failed` as the trace's red/green badge and `metadata.error` as the top-of-page error banner.

---

## 3. The `step` record (lines 1..N)

| field | type | required | notes |
|---|---|---|---|
| `_type` | `"step"` | yes | exact literal |
| `step_id` | string | yes | unique within the trace; 12-hex-char UUID slice recommended |
| `parent_step_id` | string \| null | no | for nested trees; null = top-level |
| `kind` | enum | yes | see § 3.1 |
| `name` | string | yes | short human label (`anthropic:claude-haiku-4-5`, `read_file`, `plan`) |
| `started_at` | number | yes | unix seconds, float |
| `ended_at` | number \| null | no | unix seconds; if null, the step is still open |
| `inputs` | object | no | free-form; common keys below |
| `outputs` | object | no | free-form; common keys below |
| `metadata` | object | no | free-form telemetry |
| `error` | string \| null | no | if set, the step is rendered as red and is taggable |

### 3.1 `kind` enum

| value | meaning |
|---|---|
| `llm-call` | An LLM API call (chat completion, message, generate) |
| `tool-call` | An external tool/function call (`read_file`, `http_get`, `search`) |
| `thought` | A reasoning step, a graph-node entry/exit, a planning operation |
| `io` | File I/O, DB query, anything that touches the outside world but isn't a tool |
| `error` | An explicit error checkpoint (the agent caught and recorded an error) |
| `custom` | Anything else — kept for forward compatibility |

The dashboard color-codes these (`llm-call=blue`, `tool-call=purple`, `error=red`, `thought=dim`).

### 3.2 Recommended `inputs` / `outputs` keys

For `llm-call`:
- `inputs`: `provider`, `model`, `messages` (array of role+content), `system`, `max_tokens`, `temperature`, `stream`
- `outputs`: `text`, `input_tokens`, `output_tokens`, `stop_reason` / `finish_reason`, `streamed: true`

For `tool-call`:
- `inputs`: tool-specific arguments (any shape)
- `outputs`: tool return value, status, `elapsed_ms`

Loupe never *requires* these keys; they're conventions the dashboard knows how to surface nicely. Anything you put in `inputs`/`outputs` is pretty-printed as JSON.

---

## 4. HTTP ingest

`POST /api/traces` on a running `loupe ui` server accepts the **same record shape collapsed into a single object**:

```json
{
  "name": "my-go-agent",
  "framework": "go-anthropic",
  "started_at": 1778800000.0,
  "ended_at":   1778800001.2,
  "metadata":   {"failed": true},
  "steps": [
    {"kind": "thought",   "name": "plan"},
    {"kind": "llm-call",  "name": "anthropic:claude-haiku-4-5",
       "outputs": {"text": "hi", "input_tokens": 5, "output_tokens": 2}}
  ]
}
```

Responses:
- **201 Created** — `{"trace_id": "…", "name": "…", "framework": "…", "step_count": N}`
- **400 Bad Request** — invalid JSON
- **422 Unprocessable Entity** — schema violation; `detail` describes what's wrong

Defaults applied server-side: `trace_id` is auto-generated if missing; `started_at`/`ended_at` default to `now()`; each step's `step_id` is auto-generated; each step's `ended_at` defaults to its `started_at`.

---

## 5. Annotation sidecar (§ optional)

Stored at `~/.loupe/annotations/{trace_id}.json`. Schema:

```json
[
  {
    "trace_id":           "…",
    "step_id":            "…",
    "failure_category":   "unguarded-delete" | "hallucination" | "loop" | "tool-misuse" | …,
    "severity":           "low" | "medium" | "high" | "critical",
    "notes":              "free text root-cause analysis",
    "mitigation":         "free text fix",
    "annotator":          "username or 'anon'",
    "tags":               ["coding", "file-io", …],
    "circuit_attribution": {"sae_features": [8842, 12091]}
  }
]
```

Annotations are written by the dashboard (POST `/api/traces/{id}/annotations`) or the CLI (`loupe tag`). They never modify the trace file itself.

---

## 6. Forward compatibility

- **Unknown fields MUST be ignored** by readers. Always preserve unknown fields on writes if you mutate a trace.
- The `_type` discriminator (`"trace"` / `"step"`) is the only schema-versioning we need today. If a future change is breaking, a `_type: "trace-v2"` will appear and v1 readers SHOULD skip files whose header `_type` they don't recognize.
- The wire format is intentionally minimal. Custom data lives in `metadata`, `inputs`, `outputs`. Future built-in fields (e.g. SAE circuit attribution) will be added as optional keys, never as required ones.

---

## 7. Example: write a trace by hand

```bash
# Minimal "anything that can write a file" implementation:
mkdir -p ~/.loupe/traces
cat > ~/.loupe/traces/manual-test.jsonl <<'EOF'
{"_type":"trace","trace_id":"manual-test","name":"hand-written","framework":"shell","started_at":1778800000.0,"ended_at":1778800001.0,"metadata":{"failed":false}}
{"_type":"step","step_id":"s1","kind":"thought","name":"plan","started_at":1778800000.1,"ended_at":1778800000.2,"inputs":{},"outputs":{"plan":"do thing"},"metadata":{},"error":null}
{"_type":"step","step_id":"s2","kind":"llm-call","name":"local:llama3","started_at":1778800000.3,"ended_at":1778800000.9,"inputs":{"prompt":"hi"},"outputs":{"text":"hello"},"metadata":{},"error":null}
EOF

loupe ui   # → trace appears in the dashboard
```

That's the entire contract.
