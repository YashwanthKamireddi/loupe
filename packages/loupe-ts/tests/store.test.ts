import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { JSONLStore } from "../src/index.js";
import type { Trace } from "../src/index.js";

let tempDir: string;

beforeEach(async () => {
  tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "loupe-store-"));
});

afterEach(async () => {
  await fs.rm(tempDir, { recursive: true, force: true });
});

describe("JSONLStore", () => {
  it("writes the canonical wire format", async () => {
    const store = new JSONLStore(tempDir);
    const trace: Trace = {
      trace_id: "abc123",
      name: "t1",
      framework: "test",
      started_at: 1.0,
      ended_at: 2.0,
      steps: [
        {
          step_id: "s1",
          parent_step_id: null,
          kind: "thought",
          name: "plan",
          started_at: 1.1,
          ended_at: 1.2,
          inputs: { q: "x" },
          outputs: { plan: "do thing" },
          metadata: {},
          error: null,
        },
      ],
      metadata: {},
    };
    await store.save(trace);

    const lines = (await fs.readFile(path.join(tempDir, "abc123.jsonl"), "utf-8"))
      .trim()
      .split("\n")
      .map((l) => JSON.parse(l));
    expect(lines).toHaveLength(2);
    expect(lines[0]).toMatchObject({
      _type: "trace",
      trace_id: "abc123",
      name: "t1",
      framework: "test",
    });
    expect("steps" in lines[0]).toBe(false);
    expect(lines[1]).toMatchObject({
      _type: "step",
      name: "plan",
      kind: "thought",
    });
  });

  it("creates the directory if missing", async () => {
    const target = path.join(tempDir, "nested", "deeper");
    const store = new JSONLStore(target);
    await store.save({
      trace_id: "x",
      name: "n",
      framework: null,
      started_at: 0,
      ended_at: 0,
      steps: [],
      metadata: {},
    });
    const exists = await fs
      .stat(path.join(target, "x.jsonl"))
      .then(() => true)
      .catch(() => false);
    expect(exists).toBe(true);
  });
});
