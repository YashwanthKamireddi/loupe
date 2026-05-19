import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { JSONLStore, trace } from "../src/index.js";
import { patchFetch, wrapFetch } from "../src/integrations/universal.js";

let tempDir: string;
let store: JSONLStore;

beforeEach(async () => {
  tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "loupe-universal-"));
  store = new JSONLStore(tempDir);
});

afterEach(async () => {
  await fs.rm(tempDir, { recursive: true, force: true });
});

function makeFakeFetch(responseBody: unknown, contentType = "application/json") {
  return async function fakeFetch(_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> {
    return new Response(JSON.stringify(responseBody), {
      status: 200,
      headers: { "content-type": contentType },
    });
  };
}

async function readSteps(): Promise<Record<string, unknown>[]> {
  const files = (await fs.readdir(tempDir)).filter((f) => f.endsWith(".jsonl"));
  if (files.length === 0) return [];
  const content = await fs.readFile(path.join(tempDir, files[0]!), "utf-8");
  return content
    .trim()
    .split("\n")
    .map((l) => JSON.parse(l))
    .filter((o) => o._type === "step");
}

describe("wrapFetch — universal capture", () => {
  it("captures an anthropic call", async () => {
    const responseBody = {
      content: [{ type: "text", text: "Hello there." }],
      stop_reason: "end_turn",
      usage: { input_tokens: 6, output_tokens: 4 },
    };
    const wrapped = wrapFetch(makeFakeFetch(responseBody));

    const run = trace({ framework: "universal", store }, async () => {
      return await wrapped("https://api.anthropic.com/v1/messages", {
        method: "POST",
        body: JSON.stringify({
          model: "claude-haiku-4-5",
          messages: [{ role: "user", content: "hi" }],
          max_tokens: 50,
        }),
      });
    });

    const r = await run();
    expect(r.status).toBe(200);

    const steps = await readSteps();
    expect(steps).toHaveLength(1);
    expect(steps[0]!.kind).toBe("llm-call");
    expect(steps[0]!.name).toBe("anthropic:claude-haiku-4-5");
    expect((steps[0]!.outputs as Record<string, unknown>).text).toBe("Hello there.");
    expect((steps[0]!.outputs as Record<string, unknown>).stop_reason).toBe("end_turn");
    expect((steps[0]!.outputs as Record<string, unknown>).input_tokens).toBe(6);
    expect((steps[0]!.outputs as Record<string, unknown>).output_tokens).toBe(4);
    expect((steps[0]!.outputs as Record<string, unknown>).status).toBe(200);
    expect((steps[0]!.metadata as Record<string, unknown>).transport).toBe("fetch");
  });

  it("captures an openai-style call", async () => {
    const responseBody = {
      choices: [{ message: { content: "ok" }, finish_reason: "stop" }],
      usage: { prompt_tokens: 9, completion_tokens: 1 },
    };
    const wrapped = wrapFetch(makeFakeFetch(responseBody));

    const run = trace({ framework: "universal", store }, async () => {
      return await wrapped("https://api.openai.com/v1/chat/completions", {
        method: "POST",
        body: JSON.stringify({
          model: "gpt-4o-mini",
          messages: [{ role: "user", content: "hi" }],
        }),
      });
    });
    await run();

    const steps = await readSteps();
    expect(steps).toHaveLength(1);
    expect(steps[0]!.name).toBe("openai:gpt-4o-mini");
    expect((steps[0]!.outputs as Record<string, unknown>).text).toBe("ok");
    expect((steps[0]!.outputs as Record<string, unknown>).finish_reason).toBe("stop");
  });

  it("ignores calls to non-provider hosts", async () => {
    const wrapped = wrapFetch(makeFakeFetch({ unrelated: true }));
    const run = trace({ framework: "universal", store }, async () => {
      await wrapped("https://example.com/api/whatever");
    });
    await run();
    const steps = await readSteps();
    expect(steps).toHaveLength(0);
  });

  it("captures streaming responses with a streamed flag", async () => {
    const wrapped = wrapFetch(makeFakeFetch({ choices: [] }, "text/event-stream"));
    const run = trace({ framework: "universal", store }, async () => {
      await wrapped("https://api.openai.com/v1/chat/completions", {
        method: "POST",
        body: JSON.stringify({ model: "gpt-4o-mini", stream: true, messages: [] }),
      });
    });
    await run();
    const steps = await readSteps();
    expect(steps).toHaveLength(1);
    expect((steps[0]!.outputs as Record<string, unknown>).streamed).toBe(true);
  });
});

describe("patchFetch — global fetch", () => {
  it("patches globalThis.fetch idempotently", () => {
    const fake = { fetch: makeFakeFetch({ ok: true }) };
    expect(patchFetch(fake)).toBe(true);
    expect(patchFetch(fake)).toBe(false);
  });
});

describe("withSuppressedHttpCapture — dedup with direct SDK integrations", () => {
  it("skips Step emission while the flag is active", async () => {
    const { withSuppressedHttpCapture } = await import("../src/integrations/index.js");
    const wrapped = wrapFetch(makeFakeFetch({ content: [{ text: "hi" }] }));
    const run = trace({ framework: "universal", store }, async () => {
      // Mimic what wrapModel does: claim the http layer while the wrapped
      // SDK runs so universal-fetch doesn't double-record.
      await withSuppressedHttpCapture(() =>
        wrapped("https://api.anthropic.com/v1/messages", {
          method: "POST",
          body: JSON.stringify({ model: "claude", messages: [] }),
        }),
      );
    });
    await run();
    const steps = await readSteps();
    expect(steps).toHaveLength(0);
  });

  it("captures normally when the flag is NOT active", async () => {
    const wrapped = wrapFetch(makeFakeFetch({ content: [{ text: "hi" }] }));
    const run = trace({ framework: "universal", store }, async () => {
      await wrapped("https://api.anthropic.com/v1/messages", {
        method: "POST",
        body: JSON.stringify({ model: "claude", messages: [] }),
      });
    });
    await run();
    const steps = await readSteps();
    expect(steps).toHaveLength(1);
  });
});
