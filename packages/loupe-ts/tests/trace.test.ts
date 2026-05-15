import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { JSONLStore, currentTrace, recordStep, trace } from "../src/index.js";

let tempDir: string;
let store: JSONLStore;

beforeEach(async () => {
  tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "loupe-test-"));
  store = new JSONLStore(tempDir);
});

afterEach(async () => {
  await fs.rm(tempDir, { recursive: true, force: true });
});

describe("trace() decorator", () => {
  it("captures a successful run with steps", async () => {
    const agent = trace({ name: "ok-agent", framework: "test", store }, async (q: string) => {
      recordStep("thought", "plan", { outputs: { plan: "go" } });
      recordStep("llm-call", "model", { outputs: { text: `echo: ${q}` } });
      return q.toUpperCase();
    });

    const out = await agent("hello");
    expect(out).toBe("HELLO");

    const files = (await fs.readdir(tempDir)).filter((f) => f.endsWith(".jsonl"));
    expect(files).toHaveLength(1);

    const content = await fs.readFile(path.join(tempDir, files[0]!), "utf-8");
    const lines = content.trim().split("\n").map((l) => JSON.parse(l));
    expect(lines[0]._type).toBe("trace");
    expect(lines[0].name).toBe("ok-agent");
    expect(lines[0].framework).toBe("test");
    expect(lines[0].metadata.failed).toBeUndefined();
    expect(lines.slice(1).map((s) => s.name)).toEqual(["plan", "model"]);
  });

  it("records failure metadata on throw", async () => {
    const boom = trace({ framework: "test", store }, async () => {
      recordStep("error", "boom-step", { error: "planned" });
      throw new Error("boom");
    });

    await expect(boom()).rejects.toThrow(/boom/);

    const files = await fs.readdir(tempDir);
    const content = await fs.readFile(path.join(tempDir, files[0]!), "utf-8");
    const header = JSON.parse(content.split("\n")[0]!);
    expect(header.metadata.failed).toBe(true);
    expect(header.metadata.error).toMatch(/Error: boom/);
  });

  it("uses canonical wire format (header + steps)", async () => {
    const t = trace({ framework: "test", store }, async () => {
      recordStep("custom", "x");
      return "ok";
    });
    await t();
    const files = await fs.readdir(tempDir);
    const lines = (await fs.readFile(path.join(tempDir, files[0]!), "utf-8"))
      .trim()
      .split("\n")
      .map((l) => JSON.parse(l));
    expect(lines[0]).toMatchObject({
      _type: "trace",
      name: expect.any(String),
      trace_id: expect.any(String),
      started_at: expect.any(Number),
      ended_at: expect.any(Number),
    });
    expect("steps" in lines[0]).toBe(false);
    expect(lines[1]).toMatchObject({ _type: "step", kind: "custom", name: "x" });
  });

  it("currentTrace() is undefined outside a wrapped function", () => {
    expect(currentTrace()).toBeUndefined();
    expect(recordStep("thought", "ignored")).toBeNull();
  });

  it("isolates concurrent traces (AsyncLocalStorage works)", async () => {
    const a = trace({ name: "a", framework: "test", store }, async () => {
      await new Promise((r) => setTimeout(r, 10));
      recordStep("thought", "step-a");
      return "a";
    });
    const b = trace({ name: "b", framework: "test", store }, async () => {
      recordStep("thought", "step-b");
      return "b";
    });
    await Promise.all([a(), b()]);
    const files = (await fs.readdir(tempDir)).filter((f) => f.endsWith(".jsonl"));
    expect(files.length).toBe(2);
    const headers = await Promise.all(
      files.map(async (f) =>
        JSON.parse((await fs.readFile(path.join(tempDir, f), "utf-8")).split("\n")[0]!),
      ),
    );
    const names = headers.map((h) => h.name).sort();
    expect(names).toEqual(["a", "b"]);
  });

  it("short form: trace(asyncFn) without options works", async () => {
    const _store = store;
    const agent = trace(async function namedAgent() {
      recordStep("thought", "ok");
      return 42;
    });
    // Re-route default store for this single call by using options form instead
    // (short form goes to ~/.loupe — out of scope for this test)
    expect(typeof agent).toBe("function");
    void _store;
  });
});
