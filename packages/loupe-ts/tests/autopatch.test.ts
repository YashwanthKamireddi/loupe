/**
 * `loupe-ai/autopatch` — zero-code capture via env var + NODE_OPTIONS.
 *
 * The module ships as a side-effect import: `--require loupe-ai/autopatch`
 * at Node startup activates `patchFetch()` iff `LOUPE_AUTOPATCH=1` is set.
 * This test exercises the *implicit-trace* behaviour added to `universal.ts`
 * — fetch calls made OUTSIDE any user-defined `trace(...)` block still
 * produce a one-call trace on disk when autopatch is enabled.
 */

import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { _resetDefaultStore } from "../src/store.js";
import { wrapFetch } from "../src/integrations/universal.js";

let tempDir: string;
const savedHome = process.env.LOUPE_HOME;
const savedFlag = process.env.LOUPE_AUTOPATCH;

beforeEach(async () => {
  tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "loupe-autopatch-"));
  process.env.LOUPE_HOME = tempDir;
  _resetDefaultStore();
});

afterEach(async () => {
  process.env.LOUPE_HOME = savedHome;
  if (savedFlag === undefined) delete process.env.LOUPE_AUTOPATCH;
  else process.env.LOUPE_AUTOPATCH = savedFlag;
  _resetDefaultStore();
  await fs.rm(tempDir, { recursive: true, force: true });
});

function fakeAnthropicFetch(): typeof fetch {
  return async function (_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> {
    return new Response(
      JSON.stringify({
        content: [{ type: "text", text: "hello back" }],
        usage: { input_tokens: 4, output_tokens: 2 },
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  };
}

async function readAllTraces(): Promise<Array<{ header: any; steps: any[] }>> {
  const tracesDir = path.join(tempDir, "traces");
  let files: string[] = [];
  try {
    files = (await fs.readdir(tracesDir)).filter((f) => f.endsWith(".jsonl"));
  } catch {
    return [];
  }
  const out: Array<{ header: any; steps: any[] }> = [];
  for (const f of files) {
    const lines = (await fs.readFile(path.join(tracesDir, f), "utf-8")).trim().split("\n");
    out.push({
      header: JSON.parse(lines[0]!),
      steps: lines.slice(1).map((l) => JSON.parse(l)),
    });
  }
  return out;
}

describe("LOUPE_AUTOPATCH — implicit-trace fetch capture", () => {
  it("captures a one-call trace when autopatch is enabled and no parent @trace exists", async () => {
    process.env.LOUPE_AUTOPATCH = "1";
    const wrapped = wrapFetch(fakeAnthropicFetch());

    const response = await wrapped("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: "claude-haiku-4-5",
        messages: [{ role: "user", content: "hi" }],
      }),
    });
    expect(response.status).toBe(200);

    // Give async save() a tick — defaultStore().save() is async.
    await new Promise((r) => setTimeout(r, 50));

    const traces = await readAllTraces();
    expect(traces).toHaveLength(1);
    const t = traces[0]!;
    expect(t.header.framework).toBe("autopatch");
    expect(t.header.name).toBe("auto");
    expect(t.steps).toHaveLength(1);
    expect(t.steps[0].kind).toBe("llm-call");
    expect(t.steps[0].name).toBe("anthropic:claude-haiku-4-5");
    expect(t.steps[0].outputs.text).toBe("hello back");
    expect(t.steps[0].outputs.input_tokens).toBe(4);
    expect(t.steps[0].outputs.output_tokens).toBe(2);
  });

  it("is a no-op when autopatch is disabled and no parent trace exists", async () => {
    delete process.env.LOUPE_AUTOPATCH;
    const wrapped = wrapFetch(fakeAnthropicFetch());

    const response = await wrapped("https://api.anthropic.com/v1/messages", {
      method: "POST",
      body: JSON.stringify({ model: "claude-haiku-4-5", messages: [] }),
    });
    expect(response.status).toBe(200);

    await new Promise((r) => setTimeout(r, 50));
    const traces = await readAllTraces();
    expect(traces).toHaveLength(0);
  });

  it("accepts truthy values (true / yes / on, case-insensitive)", async () => {
    for (const flag of ["true", "YES", "On"]) {
      process.env.LOUPE_AUTOPATCH = flag;
      const wrapped = wrapFetch(fakeAnthropicFetch());
      await wrapped("https://api.anthropic.com/v1/messages", {
        method: "POST",
        body: JSON.stringify({ model: "claude-haiku-4-5", messages: [] }),
      });
    }
    await new Promise((r) => setTimeout(r, 50));
    const traces = await readAllTraces();
    expect(traces.length).toBe(3);
  });

  it("skips unknown providers even with autopatch on (no trace pollution)", async () => {
    process.env.LOUPE_AUTOPATCH = "1";
    const wrapped = wrapFetch(fakeAnthropicFetch());

    await wrapped("https://example.com/some/random/path", { method: "GET" });
    await new Promise((r) => setTimeout(r, 50));
    const traces = await readAllTraces();
    expect(traces).toHaveLength(0);
  });

  it("defaults ON when ~/.loupe/config.toml exists (v0.0.59)", async () => {
    // Simulate the post-`loupe setup` state: env var unset, but
    // the config file is present under LOUPE_HOME.
    delete process.env.LOUPE_AUTOPATCH;
    await fs.writeFile(
      path.join(tempDir, "config.toml"),
      '[default]\nprovider = "gemini"\n',
      "utf-8",
    );

    const wrapped = wrapFetch(fakeAnthropicFetch());
    const response = await wrapped("https://api.anthropic.com/v1/messages", {
      method: "POST",
      body: JSON.stringify({
        model: "claude-haiku-4-5",
        messages: [{ role: "user", content: "hi" }],
      }),
    });
    expect(response.status).toBe(200);
    await new Promise((r) => setTimeout(r, 50));

    const traces = await readAllTraces();
    expect(traces).toHaveLength(1);
    expect(traces[0]!.header.framework).toBe("autopatch");
  });

  it("LOUPE_AUTOPATCH=0 overrides the on-by-default after setup", async () => {
    process.env.LOUPE_AUTOPATCH = "0";
    await fs.writeFile(
      path.join(tempDir, "config.toml"),
      '[default]\nprovider = "gemini"\n',
      "utf-8",
    );

    const wrapped = wrapFetch(fakeAnthropicFetch());
    await wrapped("https://api.anthropic.com/v1/messages", {
      method: "POST",
      body: JSON.stringify({ model: "claude-haiku-4-5", messages: [] }),
    });
    await new Promise((r) => setTimeout(r, 50));
    const traces = await readAllTraces();
    expect(traces).toHaveLength(0);
  });

  it("autopatch entry module is importable without LOUPE_AUTOPATCH set", async () => {
    delete process.env.LOUPE_AUTOPATCH;
    // Imports the module by URL — checks the side-effect path is safe to load
    // even when autopatch is disabled.
    const mod = await import("../src/autopatch.js");
    expect(typeof mod.patchFetch).toBe("function");
  });
});
