/**
 * Cross-language wire-format gate.
 *
 * The Python `tests/fixtures/canonical_trace.jsonl` is the *exact* expected
 * output for a known Trace. This test builds the SAME Trace in TypeScript
 * and asserts the JSONLStore writes bit-identical bytes.
 *
 * If this ever fails, either:
 *   - Python serialization drifted (unlikely; covered by Python snapshot test), or
 *   - TypeScript serialization drifted (this is what we're guarding).
 *
 * Either way the wire-format contract is violated. Don't update the fixture
 * without updating both languages and SPEC.md.
 */

import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { JSONLStore } from "../src/index.js";
import type { Trace } from "../src/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// Python's fixture, three directories up from tests/ in loupe-ts
const PY_FIXTURE = path.resolve(
  __dirname,
  "..",
  "..",
  "loupe-py",
  "tests",
  "fixtures",
  "canonical_trace.jsonl",
);

function buildFixtureTrace(): Trace {
  // Use fractional timestamps so JSON.stringify(1.0) === "1" doesn't disagree
  // with json.dumps(1.0) === "1.0". See test_wire_format_snapshot.py.
  return {
    trace_id: "abc123def456abc123def456abc12345",
    name: "snapshot-fixture",
    framework: "test",
    started_at: 1.001,
    ended_at: 2.001,
    steps: [
      {
        step_id: "s00000000001",
        parent_step_id: null,
        kind: "thought",
        name: "plan",
        started_at: 1.101,
        ended_at: 1.201,
        inputs: {},
        outputs: { plan: "do thing" },
        metadata: {},
        error: null,
      },
      {
        step_id: "s00000000002",
        parent_step_id: null,
        kind: "llm-call",
        name: "anthropic:claude-haiku-4-5",
        started_at: 1.301,
        ended_at: 1.901,
        inputs: { prompt: "hi", model: "claude-haiku-4-5" },
        outputs: { text: "hello", input_tokens: 5, output_tokens: 2 },
        metadata: {},
        error: null,
      },
    ],
    metadata: {},
  };
}

describe("wire-format snapshot (TS ↔ Python)", () => {
  it("TypeScript serialization matches the canonical Python fixture", async () => {
    const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "loupe-wire-"));
    try {
      const store = new JSONLStore(tempDir);
      await store.save(buildFixtureTrace());

      const tsBytes = await fs.readFile(
        path.join(tempDir, "abc123def456abc123def456abc12345.jsonl"),
        "utf-8",
      );
      const pyBytes = await fs.readFile(PY_FIXTURE, "utf-8");

      expect(tsBytes).toBe(pyBytes);
    } finally {
      await fs.rm(tempDir, { recursive: true, force: true });
    }
  });
});
